"""The `zmig fix kconfig` command: collect the project's assignments from both builds,
classify them (zmig.fixkconfig), print the plan, and with --apply commit, rebuild and
compare."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zmig.buildctx import BuildContext, ImageContext, pair_images
from zmig.cache import Cache
from zmig.errors import UsageError
from zmig.findings import Report, Severity
from zmig.fixkconfig import (
    Assignment,
    Proposal,
    classify,
    drop_conflicts,
    parse_conf,
    plan_edits,
    rewrite,
    unified_diff,
)
from zmig.report import render
from zmig.report.human import target_build
from zmig.scan import ScanContext
from zmig.snapshots import kconfig_symbols, kconfig_try
from zmig.tools import Runner

EXIT_INTRODUCED = 3
KINDS = ("deprecation select", "alias")


def _git(runner: Runner, root: Path, *args: str, check: bool = True) -> str:
    return runner.run(["git", "-C", str(root), *args], check=check).stdout


def project_root(app: Path, runner: Runner) -> Path | None:
    proc = runner.run(["git", "-C", str(app), "rev-parse", "--show-toplevel"], check=False)
    return Path(proc.stdout.strip()).resolve() if proc.returncode == 0 else None


@dataclass
class Collected:
    assignments: list[tuple[Assignment, ScanContext | None]]
    outside: list[str]  # user fragments outside the project: never edited


def _split_project(files: list[Path], root: Path) -> tuple[list[Path], list[str]]:
    """(files inside the project, paths outside it: never edited)."""
    inside = [f for f in files if f.is_relative_to(root)]
    return inside, [str(f) for f in files if not f.is_relative_to(root)]


def collect(ctx: ScanContext, app: Path, root: Path, sysbuild: bool) -> Collected:
    """Assignments in the fragments each image's build read (ScanContext.config_files;
    sysbuild.conf and SB_CONF_FILE from the sysbuild log)."""
    out: list[tuple[Assignment, ScanContext | None]] = []
    outside: list[str] = []

    def add(f: Path, prefix: str, image: str, pc: ScanContext | None) -> None:
        lines = f.read_text().splitlines()
        rel = f.relative_to(root).as_posix()
        for line, pre, sym, value in parse_conf("\n".join(lines)):
            if pre == prefix:
                a = Assignment(rel, f, line, lines[line - 1].strip(), pre, sym, value, image)
                out.append((a, pc))

    for o, n in pair_images(ctx.old, ctx.new):
        pc = ScanContext.for_pair(ctx, o, n)
        files, out_files = _split_project(pc.config_files or [], root)
        outside += [f for f in out_files if f not in outside]
        for f in files:
            add(f, "CONFIG_", pc.image, pc)
    if sysbuild:
        files, out_files = _split_project(ctx.sysbuild_config_files or [], root)
        outside += [f for f in out_files if f not in outside]
        for f in files:
            add(f, "SB_CONFIG_", "sysbuild", None)
    return Collected(out, outside)


def _sysbuild_image(b: BuildContext) -> ImageContext:
    """The sysbuild level as an image: its Kconfig is the top-level build's menuconfig."""
    return ImageContext(
        "sysbuild",
        "other",
        "sysbuild",
        b.dir,
        b.log,
        b.status,
        b.spec.mode,
        b.spec.board,
        b.workspace.resolved.ref.label,
    )


def propose(ctx: ScanContext, col: Collected) -> list[Proposal]:
    props: list[Proposal] = []
    entries = [e for _, e in ctx.guide_entries]
    sb_syms: dict[str, Any] = {}
    for a, pc in col.assignments:
        if pc is None:  # SB_CONFIG_: the sysbuild-level Kconfig tree
            if not sb_syms:
                sb_syms["old"] = kconfig_symbols(
                    _sysbuild_image(ctx.old), ctx.old.workspace, ctx.runner
                )
                sb_syms["new"] = kconfig_symbols(
                    _sysbuild_image(ctx.new), ctx.new.workspace, ctx.runner
                )
            cfg = ctx.new.dir / "zephyr" / ".config"
            m = (
                re.search(rf"^SB_CONFIG_{a.symbol}=(.*)$", cfg.read_text(), re.M)
                if cfg.is_file()
                else None
            )
            p = classify(
                a,
                sb_syms["old"],
                sb_syms["new"],
                m[1] if m else None,
                entries,
                lambda s, v: None,
                ctx.old_spec,
                ctx.new_spec,
            )
            if p is not None and p.new_symbol:
                p = Proposal(
                    p.assignment,
                    "needs-human",
                    p.new_symbol,
                    p.evidence_kind,
                    (
                        *p.reasons,
                        "not applied: zmig can't re-resolve the sysbuild-level "
                        "Kconfig to check the replacement's dependencies",
                    ),
                )
        else:

            def try_value(sym: str, value: str, pc: ScanContext = pc) -> str | None:
                got = kconfig_try(pc.new_img, pc.new.workspace, pc.runner, {sym: value}, [sym])
                return None if got is None else got.get(sym)

            p = classify(
                a,
                pc.old_kconfig,
                pc.new_kconfig,
                pc.new_config.get(a.symbol),
                entries,
                try_value,
                pc.old_spec,
                pc.new_spec,
            )
        if p is not None:
            props.append(p)
    return drop_conflicts(props)


def format_plan(props: list[Proposal], col: Collected, header: str) -> str:
    files = sorted({a.path for a, _ in col.assignments})
    lines = [
        header,
        f"Checked {len(col.assignments)} assignment(s) in {len(files)} file(s): "
        + (", ".join(files) or "none"),
        "",
    ]
    groups = [
        *(
            (
                f"Fix: rename ({k}), Kconfig evidence",
                [p for p in props if p.category == "rename" and p.evidence_kind == k],
            )
            for k in KINDS
        ),
        ("Needs a human (not edited)", [p for p in props if p.category == "needs-human"]),
        (
            "Not auto-fixed: still defined, dependencies unmet",
            [p for p in props if p.category == "deps-unmet"],
        ),
    ]
    for title, ps in groups:
        if not ps:
            continue
        lines.append(f"{title} [{len(ps)}]")
        for p in ps:
            a = p.assignment
            arrow = f" -> {a.prefix}{p.new_symbol}" if p.category == "rename" else ""
            img = f" ({a.image})" if a.image not in ("app", "sysbuild") else ""
            lines.append(f"  {a.path}:{a.line}{img}  {a.text}{arrow}")
            lines.extend(f"      {r}" for r in p.reasons)
        lines.append("")
    if col.outside:
        lines.append(f"Outside the project, never edited [{len(col.outside)}]")
        lines.extend(f"  {f}" for f in col.outside)
        lines.append("")
    if not props:
        lines.append("Nothing to fix: every assignment is defined at the target.")
    return "\n".join(lines)


Edit = tuple[str, int, str]  # (app-relative path, line, new symbol)


def compare(
    before: Report, after: Report, edits: list[Edit]
) -> tuple[list[Any], list[Any], list[Any]]:
    """(fixed, revealed, introduced): breaking/risky findings gone, and new ones split by
    whether the fix caused them."""
    sev = {Severity.BREAKING, Severity.RISKY}
    b = {f.id: f for f in before.findings if f.severity in sev}
    a = {f.id: f for f in after.findings if f.severity in sev}
    fixed = [b[i] for i in b if i not in a]
    new = [a[i] for i in a if i not in b]
    return fixed, *_split(new, before, edits)


def _split(new: list[Any], before: Report, edits: list[Edit]) -> tuple[list[Any], list[Any]]:
    """A new finding is introduced by the fix if it is about a symbol the fix wrote or
    cites an edited line, or if nothing was hidden before (the target configured). The
    rest were revealed: hidden before because the target configure failed."""
    hidden = any(
        c.get("salvage_edits") or c.get("status") == "configure-failed" for c in before.contexts[1:]
    )
    syms = {f"CONFIG_{s}" for _, _, s in edits}
    lines = {(p, n) for p, n, _ in edits}
    revealed: list[Any] = []
    introduced: list[Any] = []
    for f in new:
        cites = any(
            e.get("type") == "file_line" and (e.get("path"), e.get("line")) in lines
            for e in f.evidence
        )
        (introduced if (not hidden or cites or f.subject in syms) else revealed).append(f)
    return revealed, introduced


def cmd_fix(args: argparse.Namespace, cache: Cache, runner: Runner) -> int:
    from zmig.cli import scan_report

    if args.what != "kconfig":
        raise UsageError("only `zmig fix kconfig` exists")
    app: Path = args.app.expanduser().resolve()
    root = project_root(app, runner)
    if args.apply:
        if root is None:
            raise UsageError(f"{app} is not in a git repository; --apply commits on a new branch")
        if _git(runner, root, "status", "--porcelain").strip():
            raise UsageError(
                f"the working tree of {root} is dirty; commit or stash first (--apply works "
                "on a new branch, one commit per fix category)"
            )
    root = root or app
    keep: list[ScanContext] = []
    before = scan_report(args, cache, runner, keep)
    ctx = keep[0]
    col = collect(ctx, app, root, args.sysbuild)
    props = propose(ctx, col)
    mode = "apply" if args.apply else "dry run; --apply commits these on a new branch"
    print(format_plan(props, col, f"zmig fix kconfig: {ctx.old_spec} -> {ctx.new_spec} ({mode})"))
    plans = {k: plan_edits(props, k) for k in KINDS}
    diff = "".join(unified_diff(p.values()) for p in plans.values() if p)
    if diff:
        print(diff)
    result: dict[str, Any] = {"proposals": [p.to_json() for p in props], "outside": col.outside}
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "before").mkdir(exist_ok=True)
        (args.out / "before" / "report.json").write_text(render(before, "json"))
    if not args.apply or not any(plans.values()):
        _write(args, result)
        return 0
    branch = args.branch or "zmig/fix-kconfig-" + re.sub(r"[^\w.-]+", "-", ctx.new_spec)
    if _git(runner, root, "branch", "--list", branch).strip():
        raise UsageError(f"branch {branch} already exists; pass --branch")
    _git(runner, root, "switch", "-q", "-c", branch)
    commits = []
    edits: list[Edit] = []
    for kind, files in plans.items():
        if not files:
            continue
        body = []
        for fp in files.values():
            fp.file.write_text(rewrite(fp.file.read_text(), fp.edits))
            _git(runner, root, "add", "--", fp.path)
            for line, (old, new) in sorted(fp.edits.items()):
                body.append(f"- {fp.path}:{line}: CONFIG_{old} -> CONFIG_{new}")
                if fp.file.is_relative_to(app):
                    edits.append((fp.file.relative_to(app).as_posix(), line, new))
        msg = (
            f"zmig fix kconfig: rename ({kind})\n\n"
            + "\n".join(body)
            + (
                f"\n\nKconfig evidence from {ctx.old_spec} and {ctx.new_spec}; see `zmig fix "
                "kconfig` output.\n"
            )
        )
        _git(runner, root, "commit", "-q", "-m", msg)
        commits.append(_git(runner, root, "rev-parse", "--short", "HEAD").strip())
    print(f"Committed on branch {branch}: {', '.join(commits)}")
    print("Rebuilding the target and rerunning the analyzers...")
    after = scan_report(args, cache, runner)
    if args.out:
        (args.out / "after").mkdir(exist_ok=True)
        (args.out / "after" / "report.json").write_text(render(after, "json"))
    fixed, revealed, introduced = compare(before, after, edits)
    print(f"before: {target_build(before.contexts)}")
    print(f"after:  {target_build(after.contexts)}")
    for title, fs in (
        ("gone", fixed),
        ("revealed (hidden before: the target configure failed)", revealed),
        ("introduced by the fix", introduced),
    ):
        print(f"{title}: {len(fs)}")
        for f in fs:
            print(f"  {f.severity}  {f.title}")
    result.update(
        branch=branch,
        commits=commits,
        fixed=[f.id for f in fixed],
        revealed=[f.id for f in revealed],
        introduced=[f.id for f in introduced],
    )
    _write(args, result)
    if introduced:
        print(
            f"Stopped: the fix introduced {len(introduced)} finding(s). The commits are on "
            f"{branch}; inspect them, or drop the branch.",
            file=sys.stderr,
        )
        return EXIT_INTRODUCED
    return 0


def _write(args: argparse.Namespace, result: dict[str, Any]) -> None:
    if args.out:
        (args.out / "fix.json").write_text(json.dumps(result, indent=2) + "\n")
