"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys
from collections.abc import Iterator
from pathlib import Path

from zmig import __version__, analyzers
from zmig.boards import alias_tables, map_board
from zmig.buildctx import (
    BuildMode,
    BuildSpec,
    SalvageResult,
    build,
    build_with_salvage,
    hash_tree,
    sdk_identity,
)
from zmig.cache import Cache, default_cache_root
from zmig.errors import UsageError, ZmigError
from zmig.findings import Category, Confidence, Finding, NotChecked, Report, Severity, plural
from zmig.forks import DEFAULT_METHODS, compare_tree, guess_version_tag
from zmig.manifest import load_manifest, scan_user_workspace
from zmig.modulecheck import ModuleNeed, diagnose
from zmig.plan import PlanContext
from zmig.refs import ResolvedRef, resolve_ref
from zmig.report import render
from zmig.scan import ScanContext
from zmig.tools import (
    Runner,
    check_mcuboot_python,
    check_zephyr_python,
    find_tool,
    tool_version,
)
from zmig.verdict import upgrade_path
from zmig.workspace import RefWorkspace, materialize

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
        "--release-data",
        type=Path,
        default=None,
        help="release data JSON (boundaries, step size, fallback LTS list); default: "
        "$ZMIG_RELEASE_DATA or the bundled file",
    )
    plan.add_argument(
        "--analyzers",
        type=_csv,
        default=None,
        help="comma-separated analyzers to run (default: all)",
    )
    plan.add_argument(
        "--patch-method",
        choices=["git-apply", "git-am", "3way"],
        default=None,
        help="how the project applies its local patches: git-apply (patch files, e.g. "
        "zenv), git-am, or 3way (git rebase / cherry-pick). Default: both git-apply and "
        "3way, reporting disagreements; the stricter result is the verdict",
    )
    plan.add_argument(
        "--patch-dir",
        type=Path,
        default=None,
        help="the project's patch-file directory, to name the patch file in findings "
        "(matched to commits by file name)",
    )

    scan = sub.add_parser(
        "scan",
        parents=[common],
        help="build the app at both versions and report what changed for it",
    )
    scan.add_argument("--from", dest="from_ref", required=True)
    scan.add_argument("--to", dest="to_ref", required=True)
    _scan_args(scan)

    fix = sub.add_parser(
        "fix",
        parents=[common],
        help="rename Kconfig assignments the target removed, where Kconfig says what "
        "replaced them (dry run unless --apply)",
        description="Scans like `zmig scan`, then classifies each assignment in the "
        "project's own Kconfig fragments. Only renames with Kconfig-level evidence, a "
        "matching type and dependencies that resolve at the target are applied. --apply "
        "refuses a dirty tree, commits each evidence kind on a new branch, rebuilds the "
        "target and reruns the analyzers. Exit 3 if the fix introduced a finding.",
    )
    fix.add_argument("what", choices=["kconfig"])
    fix.add_argument("--from", dest="from_ref", required=True)
    fix.add_argument("--to", dest="to_ref", required=True)
    _scan_args(fix)
    fix.add_argument("--apply", action="store_true", help="commit the fixes on a new branch")
    fix.add_argument("--branch", help="branch for --apply (default: zmig/fix-kconfig-<to>)")

    doctor = sub.add_parser(
        "doctor",
        parents=[common],
        help="check the host tools and the toolchain a board needs",
        description="Checks git, CMake, Ninja, dtc, the host GCC (32-bit for native_sim), "
        "Python and Zephyr's build packages, and a Zephyr SDK with the board's toolchain. "
        "Exit 0 if no check failed, else 1.",
    )
    doctor.add_argument("--board", help="the board you will scan (e.g. nucleo_h563zi)")
    doctor.add_argument("--sdk", type=Path, help="the Zephyr SDK you will pass to zmig scan")
    doctor.add_argument(
        "--ref",
        default="HEAD",
        help="Zephyr ref to read the board's yaml and SDK_VERSION from (default: the "
        "cached mirror's HEAD)",
    )

    watch = sub.add_parser(
        "watch",
        parents=[common],
        help="CI mode: scan, compare with a committed baseline, exit nonzero on new findings",
        description="Exit codes: 0 ok; 1 internal error; 2 usage, config mismatch or no "
        "baseline; 3 new finding outside the baseline; 4 inconclusive (unless "
        "--allow-inconclusive). See docs/DESIGN-WATCH.md.",
    )
    watch.add_argument(
        "--from",
        dest="from_ref",
        default="manifest",
        help="'manifest' (the Zephyr revision the project's west.yml pins; the default) or a ref",
    )
    watch.add_argument(
        "--to",
        dest="to_ref",
        default=None,
        help="target: a ref, track:next-release or track:main. Default: the baseline's "
        "recorded target SHA (pinned; what pull-request runs use)",
    )
    _scan_args(watch)
    watch.add_argument(
        "--baseline", type=Path, default=Path(".zmig/baseline.json"), help="baseline file"
    )
    watch.add_argument(
        "--init", action="store_true", help="create the baseline from this run (needs --to)"
    )
    watch.add_argument(
        "--update-baseline",
        action="store_true",
        help="rewrite the baseline's accepted set to this run's findings (needs --reason)",
    )
    watch.add_argument(
        "--rebase-baseline",
        action="store_true",
        help="re-key a baseline made by another zmig build onto this build's findings "
        "(needs --reason)",
    )
    watch.add_argument("--reason", help="why (--init, --update-baseline, --rebase-baseline)")
    watch.add_argument(
        "--until", help="expiry date (YYYY-MM-DD) for entries this --init/--update-baseline adds"
    )
    watch.add_argument(
        "--fail-on",
        choices=["breaking", "risky"],
        default="breaking",
        help="lowest severity of a new finding that fails the run (default: breaking)",
    )
    watch.add_argument(
        "--allow-inconclusive",
        action="store_true",
        help="exit 0 with a warning instead of 4 when the run couldn't check everything",
    )
    watch.add_argument(
        "--on-target-movement",
        choices=["fail", "warn"],
        default="fail",
        help="new findings due to the target moving since the baseline: fail (exit 3, "
        "the default) or warn",
    )
    watch.add_argument(
        "--no-attribute",
        action="store_true",
        help="when the target moved, don't re-scan against the baseline's target to label "
        "new findings by cause",
    )
    watch.add_argument(
        "--include-rc", action="store_true", help="track:next-release also considers -rc tags"
    )
    watch.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="west.yml for --from manifest (default: the nearest one above --app)",
    )
    watch.add_argument("--sarif", type=Path, help="write SARIF with baselineState here")
    watch.add_argument("--pr-comment", type=Path, help="write the PR comment (Markdown) here")
    watch.add_argument(
        "--report",
        type=Path,
        default=None,
        help="compare an existing report.json instead of scanning",
    )
    watch.add_argument(
        "--pinned-report",
        type=Path,
        default=None,
        help="with --report: the same project's report against the baseline's target",
    )
    watch.add_argument("--today", default=None, help=argparse.SUPPRESS)

    for name, what in (("verify", "post-migration verification"),):
        s = sub.add_parser(name, parents=[common], help=f"{what} (not in the MVP)")
        s.add_argument("rest", nargs=argparse.REMAINDER)
    return p


def _scan_args(scan: argparse.ArgumentParser) -> None:
    scan.add_argument("--app", type=Path, required=True)
    scan.add_argument("--board", required=True, help="board[@rev][/qualifiers] at --from")
    scan.add_argument("--to-board", help="board name at --to (default: mapped automatically)")
    scan.add_argument("--sysbuild", action="store_true")
    scan.add_argument(
        "--shallow",
        action="store_true",
        help="fetch modules at depth 1 (no history; much less to download)",
    )
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
        "--wire-allowlist",
        type=Path,
        default=None,
        help="YAML file of extra wire-format entries (same format as zmig/data/wire_format.yaml)",
    )
    scan.add_argument(
        "--deployed-image",
        type=Path,
        default=None,
        help="signed app image (.bin) read from a field device: the MCUboot checks compare "
        "the target build's image with it instead of the source build's",
    )
    scan.add_argument(
        "--sdk",
        type=Path,
        default=None,
        help="Zephyr SDK to build with (sets ZEPHYR_SDK_INSTALL_DIR; the build fails if any "
        "image uses another SDK)",
    )
    scan.add_argument(
        "--all-guide-entries",
        action="store_true",
        help="also list migration-guide entries that don't match the app",
    )
    scan.add_argument(
        "--cmake-arg",
        dest="cmake_args",
        action="append",
        default=[],
        help="extra argument passed to CMake at both versions (repeatable)",
    )


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
        (out / "report.sarif").write_text(render(report, "sarif"))
        (out / "logs" / "invocations.json").write_text(
            json.dumps([i.to_json() for i in runner.invocations], indent=2) + "\n"
        )
    sys.stdout.write(render(report, args.format))


def _historic_alias_tables(old: ResolvedRef, new: ResolvedRef) -> Iterator[tuple[str, str]]:
    """boards/deprecated.cmake at the latest release of each minor between the refs,
    newest first."""
    from zmig.analyzers.hops import ref_version, release_tags

    mirror = old.mirror
    old_v, _ = ref_version(mirror, old.ref.sha)
    new_v, _ = ref_version(mirror, new.ref.sha)
    for minor, tags in sorted(release_tags(mirror).items(), reverse=True):
        if old_v < minor <= new_v:
            tag = tags[-1][1]
            proc = mirror.runner.run(
                [mirror.git, "--git-dir", mirror.path, "show", f"{tag}:boards/deprecated.cmake"],
                check=False,
            )
            if proc.returncode == 0:
                yield tag, proc.stdout


def cmd_scan(args: argparse.Namespace, cache: Cache, runner: Runner) -> int:
    _emit(scan_report(args, cache, runner), runner, args)
    return EXIT_OK


def scan_report(
    args: argparse.Namespace,
    cache: Cache,
    runner: Runner,
    keep: list[ScanContext] | None = None,
) -> Report:
    """The scan's report; `keep` receives its ScanContext (for `zmig fix`)."""
    for tool in ("git", "cmake", "ninja", "dtc"):
        find_tool(tool)
    check_zephyr_python()
    if "mcuboot" in args.modules:
        check_mcuboot_python()
    app: Path = args.app
    if not (app / "CMakeLists.txt").is_file():
        raise ZmigError(f"{app} is not a Zephyr application (no CMakeLists.txt)")
    selected = analyzers.select(
        "build",
        None if args.analyzers is None else [] if args.analyzers == ["none"] else args.analyzers,
    )

    mode: BuildMode = "fast" if args.fast else "full"
    old_r = resolve_ref(args.from_ref, cache, runner)
    new_r = resolve_ref(args.to_ref, cache, runner)
    old_ws = materialize(old_r, cache, runner, args.modules, shallow=args.shallow)
    new_ws = materialize(new_r, cache, runner, args.modules, shallow=args.shallow)
    table = new_ws.zephyr / "boards" / "deprecated.cmake"
    mapping = map_board(
        args.board,
        args.to_board,
        alias_tables(
            args.to_ref,
            table.read_text() if table.is_file() else "",
            lambda: _historic_alias_tables(old_r, new_r),
        ),
    )
    to_board = mapping.target
    if mapping.how == "deprecated.cmake":
        log.warning(
            "board %s was renamed (boards/deprecated.cmake at %s); building it as %s at %s",
            args.board,
            mapping.tag,
            to_board,
            args.to_ref,
        )

    def spec(board: str) -> BuildSpec:
        return BuildSpec(
            app=app,
            board=board,
            mode=mode,
            sysbuild=args.sysbuild,
            cmake_args=tuple(args.cmake_args),
            sdk=args.sdk.expanduser().resolve() if args.sdk else None,
        )

    log.warning("building %s for %s at %s (%s)...", app, args.board, args.from_ref, mode)
    old = build(old_ws, spec(args.board), cache, runner)
    log.warning("  %s: %s", args.from_ref, old.status)
    log.warning("building %s for %s at %s (%s)...", app, to_board, args.to_ref, mode)
    salvage = build_with_salvage(new_ws, spec(to_board), cache, runner)
    needs, added = _missing_modules(salvage, new_ws, runner)
    if added:
        # A module the target needs but the scan's module list lacks would stop every
        # build-based analyzer (regression: bms-firmware at v4.2 lost its whole target
        # build to cmsis_6). Report it, and analyze the target built with it.
        log.warning(
            "the target build needs module(s) %s (%s); rebuilding it with them added",
            ", ".join(added),
            "; ".join(n.why for n in needs),
        )
        new_ws = materialize(new_r, cache, runner, [*args.modules, *added], shallow=args.shallow)
        first_contexts = [salvage.original.to_json()]
        salvage = build_with_salvage(new_ws, spec(to_board), cache, runner)
        log.warning("  %s with %s: %s", args.to_ref, ", ".join(added), salvage.effective.status)
    else:
        first_contexts = []
    # Tool messages quote absolute paths of the built app copies; report them as <app>.
    runner.redactions[str(app.resolve())] = "<app>"
    for built in (old, salvage.original, salvage.salvaged):
        if built is not None:
            runner.redactions[str(built.src)] = "<app>"

    contexts = [
        old.to_json(),
        *({**c, "superseded_by_module_recovery": list(added)} for c in first_contexts),
        {**salvage.original.to_json(), **({"modules_added": list(added)} if added else {})},
    ]
    if salvage.salvaged is not None:
        contexts.append(
            {
                **salvage.salvaged.to_json(),
                "salvage_of": salvage.original.key,
                "salvage_edits": [e.to_json() for e in salvage.edits],
            }
        )
    report = Report(
        command="scan",
        zmig_version=__version__,
        inputs={
            "from": old_r.ref.to_json(),
            "to": new_r.ref.to_json(),
            "app": str(app),
            "app_tree_hash": hash_tree(app),
            "board": mapping.to_json(),
            "build_mode": mode,
            "sysbuild": args.sysbuild,
        },
        environment={
            **_environment(runner),
            **({"sdk": sdk_identity(args.sdk.expanduser())} if args.sdk else {}),
        },
        contexts=contexts,
    )
    ctx = ScanContext(
        runner=runner,
        old=old,
        new=salvage.effective,
        new_raw=salvage.original,
        old_img=old.app,
        new_img=salvage.effective.app,
        new_raw_img=salvage.original.app,
        salvage=salvage.edits,
        board_mapping=mapping,
        all_guide_entries=args.all_guide_entries,
        deployed_image=args.deployed_image.expanduser().resolve() if args.deployed_image else None,
        wire_allowlist=args.wire_allowlist.expanduser().resolve() if args.wire_allowlist else None,
        module_needs=tuple(needs),
        modules_added=tuple(added),
    )
    report.findings, report.analyzers, report.not_checked = analyzers.run_build_analyzers(
        selected, ctx
    )
    # Tool versions analyzers recorded (the libclang the API diff used).
    report.environment.update(ctx.shared.get("environment", {}))
    report.checked = list(ctx.shared.get("checked", []))
    shallow = sorted(set(old_ws.shallow_modules(runner)) | set(new_ws.shallow_modules(runner)))
    if shallow:
        report.not_checked.append(
            NotChecked(
                "fork triage of modules",
                f"module history is unavailable (shallow clones: {', '.join(shallow)}), so "
                "commits a project carries on top of upstream modules can't be classified; "
                "run `zmig plan` on a full workspace for that",
                "scan",
            )
        )
    report.not_checked.append(
        NotChecked(
            "salvage of files the app doesn't own",
            "salvage only disables lines in the app's own config and overlay files; a target "
            "configure failure caused by board, module or generated files can't be salvaged"
            + (
                f" (this run salvaged {len(salvage.edits)} line(s))"
                if salvage.edits
                else " (no salvage was needed in this run)"
            ),
            "scan",
        )
    )
    report.upgrade_path = upgrade_path(
        ctx.shared, report.findings, report.analyzers, salvage.effective.status
    )
    if keep is not None:
        keep.append(ctx)
    return report


def _missing_modules(
    salvage: SalvageResult, ws: RefWorkspace, runner: Runner
) -> tuple[list[ModuleNeed], list[str]]:
    """Modules a failed target build shows it needs, and which of them can be added
    (declared in the target manifest, so `west update` can fetch them)."""
    if salvage.effective.status == "ok":
        return [], []
    text = "\n".join(
        b.log.read_text()
        for b in (salvage.original, salvage.salvaged)
        if b is not None and b.log.is_file()
    )
    active = {m.name for m in ws.modules(runner)}
    needs = diagnose(text, ws.zephyr, load_manifest(ws.root), active)
    return needs, [n.module for n in needs]


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
        release_data_path=args.release_data,
        patch_methods=(args.patch_method,) if args.patch_method else DEFAULT_METHODS,
        patch_dir=args.patch_dir,
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
    if os.environ.get("PYTHONPATH"):
        print(
            "zmig: warning: PYTHONPATH is set; zmig removes it from the builds and tools it "
            "runs. If zmig itself fails to import, run `env -u PYTHONPATH zmig ...`.",
            file=sys.stderr,
        )
    try:
        if args.command == "fix":
            from zmig.fixcmd import cmd_fix

            return cmd_fix(args, cache, runner)
        if args.command == "doctor":
            from zmig.doctor import format_checks, run_checks

            checks = run_checks(args.board, args.sdk, args.ref, cache)
            print(format_checks(checks))
            return EXIT_ERROR if any(c.status == "FAIL" for c in checks) else EXIT_OK
        if args.command == "scan":
            return cmd_scan(args, cache, runner)
        if args.command == "plan":
            return cmd_plan(args, cache, runner)
        if args.command == "watch":
            from zmig.watchcmd import cmd_watch

            return cmd_watch(args, cache, runner)
        raise ZmigError(f"`zmig {args.command}` is outside the MVP scope (see docs/DESIGN.md)")
    except UsageError as e:
        print(f"zmig: error: {e}", file=sys.stderr)
        return EXIT_USAGE
    except ZmigError as e:
        print(f"zmig: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.exit(main())
