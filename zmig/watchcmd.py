"""The `zmig watch` command: resolve refs, run the scan(s), compare with the baseline and
write the outputs. The comparison itself is in zmig.watch."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import getpass
import json
import logging
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from zmig import __version__
from zmig.cache import Cache
from zmig.errors import UsageError
from zmig.refs import UPSTREAM_ZEPHYR, resolve_ref
from zmig.tools import Runner
from zmig.verdict import invariant_violations
from zmig.watch import (
    EXIT_ERROR,
    EXIT_OK,
    Acceptance,
    Baseline,
    Policy,
    baseline_root,
    compare,
    config_mismatch,
    decide,
    inconclusive_reasons,
    make_baseline,
    portable,
    render_comment,
    render_watch_sarif,
    watch_json,
)

log = logging.getLogger("zmig")

JSON = dict[str, Any]


def run_config(args: argparse.Namespace, root: Path) -> JSON:
    """The options that must match the baseline's for a comparison to mean anything."""
    app = Path(args.app).resolve()
    try:
        app_s = app.relative_to(root.resolve()).as_posix()
    except ValueError:
        app_s = str(app)
    return {
        "app": app_s,
        "board": args.board,
        "to_board": args.to_board,
        "sysbuild": bool(args.sysbuild),
        "fast": bool(args.fast),
        "modules": sorted(set(args.modules)),
        "cmake_args": [portable(a, root) for a in args.cmake_args],
        "analyzers": ",".join(args.analyzers) if args.analyzers else "all",
    }


def manifest_zephyr(manifest: Path) -> tuple[str, str]:
    """(revision, url) of the zephyr project in a west.yml."""
    data = yaml.safe_load(manifest.read_text()) or {}
    m = data.get("manifest") or {}
    remotes = {r["name"]: r.get("url-base", "") for r in m.get("remotes") or []}
    defaults = m.get("defaults") or {}
    for p in m.get("projects") or []:
        if p.get("name") == "zephyr" or str(p.get("repo-path", "")).endswith("zephyr"):
            rev = str(p.get("revision") or defaults.get("revision") or "master")
            url = p.get("url")
            if not url:
                base = remotes.get(p.get("remote") or defaults.get("remote") or "", "")
                url = f"{base}/{p.get('repo-path') or p['name']}" if base else UPSTREAM_ZEPHYR
            return rev, str(url).removesuffix(".git")
    raise UsageError(f"{manifest} has no project named zephyr; pass --from <ref>")


def find_manifest(app: Path) -> Path:
    for d in [app.resolve(), *app.resolve().parents]:
        if (d / "west.yml").is_file():
            return d / "west.yml"
    raise UsageError(f"no west.yml above {app}; pass --manifest or --from <ref>")


_RELEASE = re.compile(r"v(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?")


def resolve_target(
    spec: str, from_sha: str, url: str, cache: Cache, runner: Runner, include_rc: bool
) -> str:
    """A ref spec for --to: track:main -> main after a fetch; track:next-release -> the
    newest release tag above --from (final releases only, unless include_rc)."""
    if not spec.startswith("track:"):
        return spec
    from zmig.analyzers.hops import ref_version

    mirror = resolve_ref(from_sha, cache, runner, url).mirror
    mirror.fetch()  # a moving target is only as fresh as the mirror
    if spec == "track:main":
        return "main"
    if spec != "track:next-release":
        raise UsageError(f"--to {spec}: expected track:main or track:next-release")
    (major, minor), patch = ref_version(mirror, from_sha)
    tags = runner.run([mirror.git, "--git-dir", mirror.path, "tag", "-l", "v*"]).stdout.split()
    best: tuple[tuple[int, int, int, float], str] | None = None
    for t in tags:
        m = _RELEASE.fullmatch(t)
        if not m or (m[4] and not include_rc):
            continue
        key = (int(m[1]), int(m[2]), int(m[3]), float(m[4]) if m[4] else float("inf"))
        if key[:3] > (major, minor, patch) and (best is None or key > best[0]):
            best = (key, t)
    if best is None:
        raise UsageError(f"no release tag above {major}.{minor}.{patch} in {mirror.url}")
    return best[1]


def _acceptance(args: argparse.Namespace, root: Path) -> Acceptance:
    def git(*a: str) -> str | None:
        p = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), *a],
            capture_output=True,
            text=True,
            check=False,
        )
        return p.stdout.strip() or None if p.returncode == 0 else None

    who = os.environ.get("GITHUB_ACTOR") or git("config", "user.name") or getpass.getuser()
    if args.until:
        dt.date.fromisoformat(args.until)  # validate
    return Acceptance(
        reason=args.reason,
        by=who,
        at=dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        commit=git("rev-parse", "HEAD"),
        until=args.until,
    )


def _scan(
    args: argparse.Namespace,
    cache: Cache,
    runner: Runner,
    from_ref: str,
    to_ref: str,
    md: str = "MIGRATION.md",
) -> JSON:
    from zmig.cli import scan_report
    from zmig.report import render

    a = copy.copy(args)
    a.from_ref, a.to_ref = from_ref, to_ref
    report = scan_report(a, cache, runner)
    if args.out:
        _write(args.out / md, render(report, "markdown"))
    return report.to_json()


def _write(path: Path | None, text: str) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _command_line(args: argparse.Namespace) -> str:
    """The current command line without baseline-update options, for instructions."""
    skip = {"--init", "--update-baseline"}
    return shlex.join(["zmig", *[a for a in sys.argv[1:] if a not in skip]])


def cmd_watch(args: argparse.Namespace, cache: Cache, runner: Runner) -> int:
    if (args.init or args.update_baseline) and not args.reason:
        raise UsageError('--init and --update-baseline need --reason "why these are accepted"')
    if args.pinned_report and not args.report:
        raise UsageError("--pinned-report only applies with --report")
    root = baseline_root(args.baseline)
    config = run_config(args, root)

    baseline: Baseline | None = None
    if args.baseline.is_file():
        baseline = Baseline.load(args.baseline)
        if args.init and not args.update_baseline:
            raise UsageError(f"{args.baseline} already exists; use --update-baseline to rewrite it")
        if diff := config_mismatch(baseline.data, config):
            raise UsageError(
                f"this run's options don't match {args.baseline}'s config:\n  "
                + "\n  ".join(diff)
                + "\nRun with the options the baseline was made with, or re-create it."
            )
    elif not args.init:
        raise UsageError(
            f"no baseline at {args.baseline}. Create one from a run you have reviewed, and "
            f'commit it:\n  {_command_line(args)} --to <target> --init --reason "..."\n'
            "(--to v4.4.0 or --to track:next-release; PR runs then pin to its target SHA)"
        )
    if args.init and not args.to_ref and not args.report:
        raise UsageError("--init needs --to (the upgrade target to record)")

    # The run's refs.
    if args.report:
        report = json.loads(args.report.read_text())
        to_spec = args.to_ref or report["inputs"]["to"]["spec"]
        resolved = report["inputs"]["to"]["spec"]
    else:
        if args.from_ref == "manifest":
            manifest = args.manifest or find_manifest(args.app)
            rev, url = manifest_zephyr(manifest)
            log.warning("--from manifest: %s pins zephyr at %s (%s)", manifest, rev, url)
            from_ref = rev
        else:
            from_ref, url = args.from_ref, UPSTREAM_ZEPHYR
        from_sha = resolve_ref(from_ref, cache, runner, url).ref.sha
        to_spec = args.to_ref or (baseline.to_sha if baseline else "")
        resolved = resolve_target(to_spec, from_sha, url, cache, runner, args.include_rc)
        if resolved != to_spec:
            log.warning("--to %s resolved to %s", to_spec, resolved)
        report = _scan(args, cache, runner, from_ref, resolved)

    to = {"spec": to_spec, "resolved": resolved, "sha": report["inputs"]["to"]["sha"]}
    if args.out:
        out: Path = args.out
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if violations := invariant_violations(report):
        print("zmig: internal error: the report violates verdict invariants:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return EXIT_ERROR

    if args.init or args.update_baseline:
        data = make_baseline(report, config, to, _acceptance(args, root), __version__, baseline)
        _write(args.baseline, json.dumps(data, indent=2) + "\n")
        n = len(data["accepted"])
        print(
            f"zmig watch: wrote {args.baseline}: {n} accepted finding(s), target "
            f"{resolved} ({to['sha'][:12]}). Review and commit it."
        )
        return EXIT_OK
    assert baseline is not None

    pinned: JSON | None = None
    moved = report["inputs"]["to"]["sha"] != baseline.to_sha
    if moved and args.report:
        if args.pinned_report:
            pinned = json.loads(args.pinned_report.read_text())
    elif moved and not args.no_attribute:
        log.warning(
            "the target moved since the baseline (%s -> %s); scanning against the baseline's "
            "target too, to label new findings by cause",
            baseline.to_sha[:12],
            report["inputs"]["to"]["sha"][:12],
        )
        pinned = _scan(
            args,
            cache,
            runner,
            report["inputs"]["from"]["spec"],
            baseline.to_sha,
            "pinned/MIGRATION.md",
        )

    today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(dt.UTC).date()
    cmp = compare(report, baseline, today, pinned)
    policy = Policy(
        fail_on=("breaking", "risky") if args.fail_on == "risky" else ("breaking",),
        allow_inconclusive=args.allow_inconclusive,
        on_target_movement=args.on_target_movement,
    )
    outcome = decide(cmp, inconclusive_reasons(report, baseline.data), policy)
    comment = render_comment(report, baseline, cmp, outcome, args.board)
    base = str(config["app"])
    sarif = render_watch_sarif(report, cmp, __version__, base)
    result = json.dumps(watch_json(report, baseline, cmp, outcome), indent=2) + "\n"
    if args.out:
        _write(args.out / "watch.json", result)
        _write(args.out / "watch-comment.md", comment)
        _write(args.out / "watch.sarif", sarif)
        if pinned is not None:
            _write(args.out / "pinned" / "report.json", json.dumps(pinned, indent=2) + "\n")
    _write(args.sarif, sarif)
    _write(args.pr_comment, comment)
    # Fork PRs get a read-only token and can't post comments; the job summary always works.
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as f:
            f.write(comment)
    if args.format == "json":
        sys.stdout.write(result)
    else:
        sys.stdout.write(comment)
    return outcome.code
