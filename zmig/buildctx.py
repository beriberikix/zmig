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
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from zmig.cache import Cache
from zmig.errors import ArtifactMissing
from zmig.tools import Runner
from zmig.workspace import Module, RefWorkspace, west_argv

# Bump when the build recipe changes in a way that invalidates cached builds.
RECIPE_VERSION = 2

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
        "app": hash_tree(spec.app),
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
