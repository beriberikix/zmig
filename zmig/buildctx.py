"""Isolated Zephyr builds and the artifacts they produce.

A build is keyed by everything that can change its output: the ref's tree, the
board, the app's file contents, the mode, sysbuild and extra CMake arguments.
Its directory lives under <cache>/builds/<key>/. A build that fails is a valid,
cached outcome: a configure error at the target ref (e.g. an overlay that no
longer applies) is itself something to report.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Literal

from zmig.cache import Cache
from zmig.errors import ArtifactMissing
from zmig.tools import Runner
from zmig.workspace import Module, RefWorkspace, west_argv

# Bump when the build recipe changes in a way that invalidates cached builds.
RECIPE_VERSION = 3

BuildMode = Literal["full", "fast"]
BuildStatus = Literal["ok", "configure-failed", "build-failed"]

# Boards built with the host compiler rather than a cross toolchain.
HOST_BOARD_PREFIXES = ("native_sim", "native_posix", "nrf52_bsim", "nrf5340bsim", "nrf54l15bsim")

APP_IGNORE_DIRS = {".git", "build", "__pycache__", ".venv"}


class Artifact(Enum):
    CONFIG = "zephyr/.config"
    DTS = "zephyr/zephyr.dts"
    EDT = "zephyr/edt.pickle"
    COMPILE_COMMANDS = "compile_commands.json"
    MODULES = "zephyr_modules.txt"
    CMAKE_CACHE = "CMakeCache.txt"
    ELF = "zephyr/zephyr.elf"
    MAP = "zephyr/zephyr.map"


# Artifacts that exist only after the build step, not after configure.
BUILD_ONLY = frozenset({Artifact.ELF, Artifact.MAP})


def hash_tree(root: Path) -> str:
    """Content hash of an app directory (relative paths + bytes), ignoring build dirs."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in APP_IGNORE_DIRS for part in rel.parts) or not p.is_file():
            continue
        h.update(rel.as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def is_host_board(board: str) -> bool:
    return board.split("/")[0].startswith(HOST_BOARD_PREFIXES)


@dataclass(frozen=True)
class BuildSpec:
    app: Path
    board: str
    mode: BuildMode = "full"
    sysbuild: bool = False
    cmake_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class BuildContext:
    workspace: RefWorkspace
    spec: BuildSpec
    key: str
    dir: Path
    status: BuildStatus
    log: Path

    @property
    def app_dir(self) -> Path:
        """Directory of the application image's build (differs under sysbuild)."""
        return self.dir / self.spec.app.resolve().name if self.spec.sysbuild else self.dir

    def has(self, a: Artifact) -> bool:
        return (self.app_dir / a.value).is_file()

    def artifact(self, a: Artifact) -> Path:
        p = self.app_dir / a.value
        if p.is_file():
            return p
        if a in BUILD_ONLY and self.spec.mode == "fast":
            why = "it is only produced by a full build; rerun without --fast"
        elif self.status != "ok":
            why = f"the build {self.status.replace('-', ' ')} (see {self.log})"
        else:
            why = "the build did not produce it"
        raise ArtifactMissing(
            f"{a.value} missing for {self.spec.board} at {self.workspace.resolved.ref.spec}: {why}"
        )

    def to_json(self) -> dict[str, object]:
        return {
            "ref": self.workspace.resolved.ref.spec,
            "board": self.spec.board,
            "mode": self.spec.mode,
            "sysbuild": self.spec.sysbuild,
            "status": self.status,
            "key": self.key,
        }


def build_key(ws: RefWorkspace, spec: BuildSpec, modules: list[Module]) -> str:
    material = {
        "recipe": RECIPE_VERSION,
        "ref": ws.resolved.ref.key,
        "modules": [[m.name, m.sha] for m in modules],
        "board": spec.board,
        # Content and location: artifacts (compile_commands, logs) embed the app path.
        "app": hash_tree(spec.app),
        "app_path": str(spec.app.resolve()),
        "mode": spec.mode,
        "sysbuild": spec.sysbuild,
        "cmake_args": list(spec.cmake_args),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:16]


def build(ws: RefWorkspace, spec: BuildSpec, cache: Cache, runner: Runner) -> BuildContext:
    """Configure (fast) or build (full) `spec` in an isolated, cached directory."""
    modules = ws.modules(runner)
    key = build_key(ws, spec, modules)
    bdir = cache.builds / key
    marker = cache.builds / f"{key}.json"
    log = cache.builds / f"{key}.log"
    if marker.is_file() and bdir.is_dir():
        status: BuildStatus = json.loads(marker.read_text())["status"]
        runner.redactions.setdefault(str(bdir), f"$BUILD[{key}]")
        return BuildContext(ws, spec, key, bdir, status, log)

    shutil.rmtree(bdir, ignore_errors=True)
    env = {"ZEPHYR_BASE": str(ws.zephyr)}
    if is_host_board(spec.board):
        env["ZEPHYR_TOOLCHAIN_VARIANT"] = "host"
    argv: list[str] = [
        *west_argv(),
        "build",
        "--pristine",
        "always",
        "-b",
        spec.board,
        "-d",
        str(bdir),
        "-s",
        str(spec.app.resolve()),
        "--sysbuild" if spec.sysbuild else "--no-sysbuild",
    ]
    if spec.mode == "fast":
        argv.append("--cmake-only")
    argv += [
        "--",
        f"-DPython3_EXECUTABLE={sys.executable}",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        *spec.cmake_args,
    ]
    if modules:
        # Explicit, so the build sees exactly the modules that are in the cache key.
        argv.append("-DZEPHYR_MODULES=" + ";".join(str(m.path) for m in modules))
    # PYTHONPATH from the user's shell (e.g. ROS) can shadow the packages Zephyr's
    # scripts import; the build must see only zmig's interpreter environment.
    proc = runner.run(argv, cwd=ws.root, env=env, unset_env=["PYTHONPATH"], check=False)
    log.write_text(f"$ {' '.join(argv)}\n{proc.stdout}\n{proc.stderr}")

    if proc.returncode == 0:
        status = "ok"
    else:
        configured = (
            (bdir / Artifact.CONFIG.value).is_file()
            if not spec.sysbuild
            else (bdir / spec.app.resolve().name / Artifact.CONFIG.value).is_file()
        )
        status = (
            "build-failed"
            if configured and (bdir / "build.ninja").is_file()
            else "configure-failed"
        )
    marker.write_text(
        json.dumps(
            {
                "status": status,
                "spec": {
                    "app": str(spec.app),
                    "board": spec.board,
                    "mode": spec.mode,
                    "sysbuild": spec.sysbuild,
                    "cmake_args": list(spec.cmake_args),
                },
                "ref": ws.resolved.ref.to_json(),
            },
            indent=2,
        )
    )
    runner.redactions.setdefault(str(bdir), f"$BUILD[{key}]")
    return BuildContext(ws, spec, key, bdir, status, log)


# --- Salvage -------------------------------------------------------------------------
#
# Zephyr turns every Kconfig warning and devicetree binding violation into a configure
# error. At the target ref that is the normal case for a real migration, and it leaves
# nothing to compare. Salvage does what a developer would do by hand: disable exactly
# the assignments Zephyr rejected, in a copy of the app under the cache, and configure
# again. The edits are recorded and reported; the user's app is never modified.

MAX_SALVAGE_ROUNDS = 4
SALVAGE_TAG = "zmig-salvage"


@dataclass(frozen=True)
class SalvageEdit:
    file: str  # relative to the app
    line: int
    text: str  # the original line
    reason: str  # the diagnostic message that caused it

    def to_json(self) -> dict[str, object]:
        return {"file": self.file, "line": self.line, "text": self.text, "reason": self.reason}


@dataclass(frozen=True)
class SalvageResult:
    original: BuildContext
    salvaged: BuildContext | None
    edits: tuple[SalvageEdit, ...]

    @property
    def effective(self) -> BuildContext:
        """The build analyzers should compare: salvaged if salvage was needed."""
        return self.salvaged or self.original


def find_last_assignment(app: Path, files: list[str], sym: str) -> tuple[str, int] | None:
    """Last `CONFIG_<sym>=` line across conf fragments (later fragments win)."""
    hit = None
    pat = re.compile(rf"^\s*CONFIG_{re.escape(sym)}=")
    for rel in files:
        for i, line in enumerate((app / rel).read_text().splitlines(), 1):
            if pat.match(line):
                hit = (rel, i)
    return hit


def find_dt_property(app: Path, files: list[str], prop: str) -> tuple[str, int] | None:
    """First single-line `prop = ...;` or `prop;` statement in the app's overlays."""
    pat = re.compile(rf"^\s*{re.escape(prop)}\s*(=.*)?;\s*(//.*)?$")
    for rel in files:
        for i, line in enumerate((app / rel).read_text().splitlines(), 1):
            if pat.match(line):
                return rel, i
    return None


def locate_salvage_edits(log_text: str, app: Path) -> list[SalvageEdit]:
    """Map the configure errors in a log to the app lines that caused them."""
    from zmig.diagnostics import app_relative, parse_log

    facts = parse_log(log_text)
    confs = list(app_relative(facts.configs, app)) or sorted(
        p.relative_to(app).as_posix() for p in app.rglob("*.conf")
    )
    overlays = list(app_relative(facts.overlays, app))
    edits: dict[tuple[str, int], SalvageEdit] = {}
    for d in facts.diagnostics:
        loc: tuple[str, int] | None = None
        if d.kind == "kconfig-undefined":
            rel = app_relative((d.file,), app)
            loc = (rel[0], d.line) if rel else None
        elif d.kind in ("kconfig-not-taken", "kconfig-promptless"):
            loc = find_last_assignment(app, confs, d.symbol)
        elif d.kind == "dt-undeclared-property":
            loc = find_dt_property(app, overlays, d.symbol)
        if loc is not None and loc not in edits:
            text = (app / loc[0]).read_text().splitlines()[loc[1] - 1]
            edits[loc] = SalvageEdit(loc[0], loc[1], text, d.message)
    return sorted(edits.values(), key=lambda e: (e.file, e.line))


def _apply_edits(app: Path, edits: list[SalvageEdit]) -> None:
    by_file: dict[str, list[SalvageEdit]] = {}
    for e in edits:
        by_file.setdefault(e.file, []).append(e)
    for rel, es in by_file.items():
        path = app / rel
        lines = path.read_text().splitlines(keepends=True)
        for e in es:
            orig = lines[e.line - 1].rstrip("\n")
            # Comment out in place, so every other line keeps its number.
            lines[e.line - 1] = (
                f"/* {SALVAGE_TAG}: {orig.strip()} */\n"
                if rel.endswith((".overlay", ".dts", ".dtsi"))
                else f"# {SALVAGE_TAG}: {orig}\n"
            )
        path.write_text("".join(lines))


def build_with_salvage(
    ws: RefWorkspace, spec: BuildSpec, cache: Cache, runner: Runner
) -> SalvageResult:
    """Build; if configure fails on assignments Zephyr rejected, salvage (see above)."""
    original = build(ws, spec, cache, runner)
    if original.status != "configure-failed":
        return SalvageResult(original, None, ())
    app_src = spec.app.resolve()
    copy = cache.root / "salvage" / original.key / app_src.name
    shutil.rmtree(copy.parent, ignore_errors=True)
    shutil.copytree(app_src, copy, ignore=shutil.ignore_patterns(*APP_IGNORE_DIRS))
    all_edits: list[SalvageEdit] = []
    current = original
    for _ in range(MAX_SALVAGE_ROUNDS):
        # Paths in the log point at whichever app copy that build used.
        edits = locate_salvage_edits(current.log.read_text(), current.spec.app.resolve())
        new_edits = [
            e for e in edits if (e.file, e.line) not in {(a.file, a.line) for a in all_edits}
        ]
        if not new_edits:
            break
        _apply_edits(copy, new_edits)
        all_edits += new_edits
        current = build(ws, replace(spec, app=copy), cache, runner)
        if current.status != "configure-failed":
            break
    if not all_edits:
        return SalvageResult(original, None, ())
    return SalvageResult(original, current, tuple(all_edits))
