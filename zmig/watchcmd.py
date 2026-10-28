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
from zmig.findings import NotChecked
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
    classify_gaps,
    compare,
    config_mismatch,
    decide,
    gaps,
    make_baseline,
    portable,
    rebase_baseline,
    render_comment,
    render_watch_sarif,
    watch_json,
    zmig_build,
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
    from_manifest: JSON | None = None,
) -> JSON:
    from zmig.cli import scan_report
    from zmig.report import render

    a = copy.copy(args)
    a.from_ref, a.to_ref = from_ref, to_ref
    report = scan_report(a, cache, runner)
    if from_manifest:
        report.inputs["from_manifest"] = from_manifest
        if from_manifest.get("warning"):
            report.not_checked.append(
                NotChecked("the Zephyr commit under test", from_manifest["warning"], "watch")
            )
    if args.out:
        _write(args.out / md, render(report, "markdown"))
    return report.to_json()


def _write(path: Path | None, text: str) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _command_line() -> str:
    """The current command line without baseline-update options, for instructions."""
    skip = {"--init", "--update-baseline", "--rebase-baseline"}
    return shlex.join(["zmig", *[a for a in sys.argv[1:] if a not in skip]])


def _skew(baseline: Baseline) -> str | None:
    build = zmig_build()
    if baseline.data.get("zmig_build") == build:
        return None
    return (
        f"{baseline.path} was made by zmig {baseline.data.get('zmig_version')} (build "
        f"{baseline.data.get('zmig_build') or 'unrecorded'}); this is zmig {__version__} "
        f"(build {build}). Findings and their fingerprints can change between zmig builds, "
        "so comparing would report findings as new that aren't. Either run the zmig the "
        "baseline was made with (pin it by commit), or upgrade zmig in a pull request that "
        f'also re-keys the baseline:\n  {_command_line()} --rebase-baseline --reason "..."'
    )


def _resolve_from(
    args: argparse.Namespace, cache: Cache, runner: Runner
) -> tuple[str, str, str, JSON | None]:
    """(from ref, url, sha, from_manifest record) for --from."""
    if args.from_ref != "manifest":
        ref = resolve_ref(args.from_ref, cache, runner).ref
        return args.from_ref, UPSTREAM_ZEPHYR, ref.sha, None
    manifest = args.manifest or find_manifest(args.app)
    rev, url = manifest_zephyr(manifest)
    ref = resolve_ref(rev, cache, runner, url).ref
    record: JSON = {"manifest": str(manifest), "revision": rev, "kind": ref.kind, "sha": ref.sha}
    if ref.kind == "branch":
        record["warning"] = (
            f"--from manifest: {manifest.name} pins zephyr at branch {rev!r}, resolved to "
            f"its tip today ({ref.sha[:12]}). That may differ from the Zephyr commit the "
            "project was built and tested with; pin a tag or SHA in the manifest, or pass "
            "--from <sha>."
        )
        log.warning("%s", record["warning"])
    else:
        log.warning("--from manifest: %s pins zephyr at %s (%s)", manifest, rev, url)
    return rev, url, ref.sha, record


def cmd_watch(args: argparse.Namespace, cache: Cache, runner: Runner) -> int:
    updating = args.init or args.update_baseline or args.rebase_baseline
    if updating and not args.reason:
        raise UsageError('--init, --update-baseline and --rebase-baseline need --reason "why"')
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
        if not args.rebase_baseline and (skew := _skew(baseline)):
            raise UsageError(skew)
        if args.rebase_baseline and not _skew(baseline):
            raise UsageError(
                f"{args.baseline} was made by this zmig build; there is nothing to rebase"
            )
    elif not args.init:
        raise UsageError(
            f"no baseline at {args.baseline}. Create one from a run you have reviewed, and "
            f'commit it:\n  {_command_line()} --to <target> --init --reason "..."\n'
            "(--to v4.4.0 or --to track:next-release; PR runs then pin to its target SHA)"
        )
    if args.init and not args.to_ref and not args.report:
        raise UsageError("--init needs --to (the upgrade target to record)")
    if args.rebase_baseline and args.to_ref:
        raise UsageError("--rebase-baseline runs against the baseline's own target; omit --to")

    # The run's refs.
    if args.report:
        report = json.loads(args.report.read_text())
        to_spec = args.to_ref or report["inputs"]["to"]["spec"]
        resolved = report["inputs"]["to"]["spec"]
    else:
        from_ref, url, from_sha, from_manifest = _resolve_from(args, cache, runner)
        to_spec = args.to_ref or (baseline.to_sha if baseline else "")
        resolved = resolve_target(to_spec, from_sha, url, cache, runner, args.include_rc)
        if resolved != to_spec:
            log.warning("--to %s resolved to %s", to_spec, resolved)
        report = _scan(args, cache, runner, from_ref, resolved, from_manifest=from_manifest)
    warnings = tuple(w for w in [(report["inputs"].get("from_manifest") or {}).get("warning")] if w)

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

    if args.rebase_baseline:
        assert baseline is not None
        if report["inputs"]["to"]["sha"] != baseline.to_sha:
            raise UsageError("--rebase-baseline needs a run against the baseline's target")
        rb = rebase_baseline(baseline, report, _acceptance(args, root), __version__, zmig_build())
        _write(args.baseline, json.dumps(rb.data, indent=2) + "\n")
        print(
            f"zmig watch: rebased {args.baseline} from build "
            f"{baseline.data.get('zmig_build') or 'unrecorded'} to {zmig_build()}"
        )
        print(f"Re-mapped ({len(rb.remapped)}):")
        for old, new in rb.remapped:
            moved = "" if old["fingerprint"] == new["fingerprint"] else " (fingerprint changed)"
            print(
                f"  {old['fingerprint']} -> {new['fingerprint']}{moved}  {new['severity']} "
                f"{new['category']}/{new['kind']} {new['subject'][:60]}"
            )
        print(f"Could not re-map, dropped from the baseline ({len(rb.unmapped)}):")
        for old, why in rb.unmapped:
            print(
                f"  {old['fingerprint']}  {old['severity']} {old['category']}/{old['kind']} "
                f"{old['subject'][:60]}: {why}"
            )
        print(
            f"Evidence files added by this build, accepted with the rebase ({len(rb.new_files)}):"
        )
        for new, files in rb.new_files:
            print(f"  {new['fingerprint']}  {new['category']}/{new['kind']}: {', '.join(files)}")
        print(f"Found by this build, not accepted (will be new) ({len(rb.not_accepted)}):")
        for f in rb.not_accepted:
            print(f"  {f['severity']} {f['category']}/{f['kind']}: {f['title'][:90]}")
        print("Review the diff and commit it with the zmig upgrade.")
        return EXIT_OK

    if args.init or args.update_baseline:
        data = make_baseline(report, config, to, _acceptance(args, root), __version__, baseline)
        _write(args.baseline, json.dumps(data, indent=2) + "\n")
        print(
            f"zmig watch: wrote {args.baseline}: {len(data['accepted'])} accepted finding(s), "
            f"{len(data['accepted_gaps'])} accepted gap(s), target {resolved} "
            f"({to['sha'][:12]}). Review and commit it."
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
    gap_states = classify_gaps(gaps(report, baseline.data), baseline, today)
    cmp = compare(report, baseline, today, pinned, blind=any(g.blocking for g in gap_states))
    policy = Policy(
        fail_on=("breaking", "risky") if args.fail_on == "risky" else ("breaking",),
        allow_inconclusive=args.allow_inconclusive,
        on_target_movement=args.on_target_movement,
    )
    outcome = decide(cmp, gap_states, policy)
    comment = render_comment(report, baseline, cmp, outcome, args.board, today, warnings)
    base = str(config["app"])
    sarif = render_watch_sarif(report, cmp, __version__, base)
    result = watch_json(report, baseline, cmp, outcome)
    result["warnings"] = list(warnings)
    result_text = json.dumps(result, indent=2) + "\n"
    if args.out:
        _write(args.out / "watch.json", result_text)
        _write(args.out / "watch-comment.md", comment)
        _write(args.out / "watch.sarif", sarif)
        if pinned is not None:
            _write(args.out / "pinned" / "report.json", json.dumps(pinned, indent=2) + "\n")
    _write(args.sarif, sarif)
    _write(args.pr_comment, comment)
    # Fork PRs get a read-only token and can't post comments; the job summary always works.
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as fh:
            fh.write(comment)
    if args.format == "json":
        sys.stdout.write(result_text)
    else:
        sys.stdout.write(comment)
    return outcome.code
