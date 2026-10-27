"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys
from pathlib import Path

from zmig import __version__, analyzers
from zmig.buildctx import BuildContext, BuildMode, BuildSpec, build, hash_tree
from zmig.cache import Cache, default_cache_root
from zmig.errors import ZmigError
from zmig.findings import Category, Confidence, Finding, Report, Severity, plural
from zmig.forks import compare_tree, guess_version_tag
from zmig.manifest import load_manifest, scan_user_workspace
from zmig.plan import PlanContext
from zmig.refs import ResolvedRef, resolve_ref
from zmig.report import render
from zmig.tools import Runner, check_zephyr_python, find_tool, tool_version
from zmig.workspace import materialize

log = logging.getLogger("zmig")

EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--format",
        choices=["human", "json", "markdown", "sarif"],
        default="human",
        help="stdout format (default: human)",
    )
    common.add_argument(
        "--out", type=Path, help="write report.json, MIGRATION.md and logs/ to this directory"
    )
    common.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="cache root (default: $ZMIG_CACHE or ~/.cache/zmig)",
    )
    common.add_argument("-v", "--verbose", action="count", default=0)

    p = argparse.ArgumentParser(prog="zmig", description=__doc__)
    p.add_argument("--version", action="version", version=f"zmig {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    plan = sub.add_parser(
        "plan", parents=[common], help="manifest and fork triage between two Zephyr versions"
    )
    plan.add_argument("--from", dest="from_ref", required=True)
    plan.add_argument("--to", dest="to_ref", required=True)
    plan.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="your west workspace topdir (default: the current directory, if it is one)",
    )
    plan.add_argument(
        "--upstream",
        action="append",
        default=[],
        metavar="NAME=URL",
        help="upstream URL for a project, overriding upstream Zephyr's manifest",
    )
    plan.add_argument(
        "--analyzers",
        type=_csv,
        default=None,
        help="comma-separated analyzers to run (default: all)",
    )

    scan = sub.add_parser(
        "scan",
        parents=[common],
        help="build the app at both versions and report what changed for it",
    )
    scan.add_argument("--app", type=Path, required=True)
    scan.add_argument("--board", required=True, help="board[@rev][/qualifiers] at --from")
    scan.add_argument("--to-board", help="board name at --to (default: mapped automatically)")
    scan.add_argument("--from", dest="from_ref", required=True)
    scan.add_argument("--to", dest="to_ref", required=True)
    scan.add_argument("--sysbuild", action="store_true")
    scan.add_argument(
        "--fast",
        action="store_true",
        help="configure only; skips analyzers that need build outputs",
    )
    scan.add_argument(
        "--module",
        dest="modules",
        action="append",
        default=[],
        help="west project to clone into the per-version workspaces (repeatable)",
    )
    scan.add_argument(
        "--analyzers",
        type=_csv,
        default=None,
        help="comma-separated analyzers to run, or 'none' (default: all)",
    )
    scan.add_argument(
        "--cmake-arg",
        dest="cmake_args",
        action="append",
        default=[],
        help="extra argument passed to CMake at both versions (repeatable)",
    )

    for name, what in (
        ("fix", "codemods"),
        ("verify", "post-migration verification"),
        ("watch", "CI mode"),
    ):
        s = sub.add_parser(name, parents=[common], help=f"{what} (not in the MVP)")
        s.add_argument("rest", nargs=argparse.REMAINDER)
    return p


def _environment(runner: Runner) -> dict[str, str | None]:
    return {
        "python": platform.python_version(),
        "west": tool_version(runner, "west", ["--version"]),
        "cmake": tool_version(runner, "cmake", ["--version"]),
        "ninja": tool_version(runner, "ninja", ["--version"]),
        "dtc": tool_version(runner, "dtc", ["--version"]),
        "git": tool_version(runner, "git", ["--version"]),
    }


def _emit(report: Report, runner: Runner, args: argparse.Namespace) -> None:
    if args.out:
        out: Path = args.out
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(render(report, "json"))
        (out / "MIGRATION.md").write_text(render(report, "markdown"))
        (out / "logs" / "invocations.json").write_text(
            json.dumps([i.to_json() for i in runner.invocations], indent=2) + "\n"
        )
    sys.stdout.write(render(report, args.format))


def cmd_scan(args: argparse.Namespace, cache: Cache, runner: Runner) -> int:
    for tool in ("git", "cmake", "ninja", "dtc"):
        find_tool(tool)
    check_zephyr_python()
    app: Path = args.app
    if not (app / "CMakeLists.txt").is_file():
        raise ZmigError(f"{app} is not a Zephyr application (no CMakeLists.txt)")
    selected = analyzers.select(
        "build",
        None if args.analyzers is None else [] if args.analyzers == ["none"] else args.analyzers,
    )

    to_board = args.to_board or args.board  # automatic HWMv2 mapping lands in M3
    mode: BuildMode = "fast" if args.fast else "full"
    contexts: list[BuildContext] = []
    refs = []
    for spec, board in ((args.from_ref, args.board), (args.to_ref, to_board)):
        resolved = resolve_ref(spec, cache, runner)
        refs.append(resolved.ref)
        ws = materialize(resolved, cache, runner, args.modules)
        log.warning("building %s for %s at %s (%s)...", app, board, spec, mode)
        ctx = build(
            ws,
            BuildSpec(
                app=app,
                board=board,
                mode=mode,
                sysbuild=args.sysbuild,
                cmake_args=tuple(args.cmake_args),
            ),
            cache,
            runner,
        )
        log.warning("  %s: %s", spec, ctx.status)
        contexts.append(ctx)
    old, new = contexts

    report = Report(
        command="scan",
        zmig_version=__version__,
        inputs={
            "from": refs[0].to_json(),
            "to": refs[1].to_json(),
            "app": str(app),
            "app_tree_hash": hash_tree(app),
            "board": {"from": args.board, "to": to_board},
            "build_mode": mode,
            "sysbuild": args.sysbuild,
        },
        environment=_environment(runner),
        contexts=[c.to_json() for c in contexts],
    )
    report.findings, report.analyzers = analyzers.run_build_analyzers(
        selected, old, new, usage=None
    )
    _emit(report, runner, args)
    return EXIT_OK


def _resolve_plan_from(
    spec: str, cache: Cache, runner: Runner
) -> tuple[ResolvedRef, list[Finding]]:
    """--from may be an untracked Zephyr tree (no .git): infer its release from VERSION,
    and report how its content differs from that release."""
    p = Path(spec).expanduser()
    if not (p.is_dir() and not (p / ".git").exists()):
        return resolve_ref(spec, cache, runner), []
    guess = guess_version_tag(p)
    if guess.tag is None:
        raise ZmigError(
            f"{spec} is not a git checkout and its VERSION ({guess.version}) "
            f"names no release ({guess.reason}); pass a git ref as --from"
        )
    resolved = resolve_ref(guess.tag, cache, runner)
    diff = compare_tree(resolved.mirror, resolved.ref.sha, p)
    if diff.clean:
        return resolved, []
    changed = diff.modified + diff.missing + diff.added
    return resolved, [
        Finding(
            category=Category.FORK,
            kind="content-mismatch",
            subject="zephyr",
            title=f"{spec} is not a git checkout; it claims {guess.tag} (from VERSION) but "
            f"differs from it in {plural(len(changed), 'file')}",
            severity=Severity.RISKY,
            confidence=Confidence.MEDIUM,
            action="Record these changes as commits on top of the release in a git checkout "
            "so they can be classified and carried forward.",
            analyzer="forks",
            scope=(resolved.ref.sha,),
            new={
                "modified": list(diff.modified),
                "missing": list(diff.missing),
                "added": list(diff.added),
                "compared_files": diff.compared,
            },
            heuristics=("fork.version-from-VERSION-file", "fork.blob-hash-no-filters"),
        )
    ]


def cmd_plan(args: argparse.Namespace, cache: Cache, runner: Runner) -> int:
    find_tool("git")
    selected = analyzers.select("workspace", args.analyzers)
    overrides = dict(_kv(s) for s in args.upstream)
    old_r, pre = _resolve_plan_from(args.from_ref, cache, runner)
    new_r = resolve_ref(args.to_ref, cache, runner)
    old_ws, new_ws = materialize(old_r, cache, runner), materialize(new_r, cache, runner)

    user = None
    topdir = args.workspace
    if topdir is None and (Path.cwd() / ".west" / "config").is_file():
        topdir = Path.cwd()
    if topdir is not None:
        user = scan_user_workspace(topdir, runner)
    ctx = PlanContext(
        cache=cache,
        runner=runner,
        old=old_ws,
        new=new_ws,
        old_manifest=load_manifest(old_ws.root),
        new_manifest=load_manifest(new_ws.root),
        user=user,
        upstream_overrides=overrides,
    )
    report = Report(
        command="plan",
        zmig_version=__version__,
        inputs={
            "from": old_r.ref.to_json(),
            "to": new_r.ref.to_json(),
            "from_spec": args.from_ref,
            "workspace": runner.redact(str(user.topdir)) if user else None,
        },
        environment=_environment(runner),
    )
    report.findings, report.analyzers = analyzers.run_workspace_analyzers(selected, ctx)
    report.findings = pre + report.findings
    _emit(report, runner, args)
    return EXIT_OK


def _kv(s: str) -> tuple[str, str]:
    if "=" not in s:
        raise ZmigError(f"--upstream expects NAME=URL, got {s!r}")
    k, v = s.split("=", 1)
    return k, v


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    level = [logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)]
    logging.basicConfig(level=level, format="zmig: %(message)s", stream=sys.stderr)

    cache = Cache(args.cache_dir or default_cache_root())
    cache.ensure()
    log_dir = (args.out / "logs") if args.out else None
    runner = Runner(
        log_dir=log_dir, redactions={str(cache.root): "$ZMIG_CACHE", str(Path.home()): "~"}
    )
    try:
        if args.command == "scan":
            return cmd_scan(args, cache, runner)
        if args.command == "plan":
            return cmd_plan(args, cache, runner)
        raise ZmigError(f"`zmig {args.command}` is outside the MVP scope (see docs/DESIGN.md)")
    except ZmigError as e:
        print(f"zmig: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.exit(main())
