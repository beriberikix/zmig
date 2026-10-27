"""Isolated Zephyr builds and the artifacts they produce.

A build is keyed by everything that can change its output: the ref's tree, the
board, the app's file contents, the mode, sysbuild and extra CMake arguments.
Its directory lives under <cache>/builds/<key>/. A build that fails is a valid,
cached outcome: a configure error at the target ref (e.g. an overlay that no
longer applies) is itself something to report.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from zmig.cache import Cache
from zmig.errors import ArtifactMissing
from zmig.tools import Runner, find_tool
from zmig.workspace import Module, RefWorkspace, west_argv

# Bump when the build recipe changes in a way that invalidates cached builds.
RECIPE_VERSION = 10

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
    SPDX = "spdx/zephyr.spdx"  # plus app.spdx and build.spdx in the same directory


# Artifacts that exist only after the build step, not after configure.
BUILD_ONLY = frozenset({Artifact.ELF, Artifact.MAP, Artifact.SPDX})


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


ImageRole = Literal["app", "bootloader", "other"]


@dataclass(frozen=True)
class ImageContext:
    """One Zephyr image of a build. A plain build has exactly one (the app); a sysbuild
    build has one per entry in domains.yaml (app, MCUboot, ...)."""

    name: str
    role: ImageRole
    role_source: str  # how the role was determined
    dir: Path  # this image's CMake build directory
    log: Path  # this image's configure and build output only
    status: BuildStatus
    mode: BuildMode
    board: str
    ref: str  # spec of the ref it was built from, for messages

    def has(self, a: Artifact) -> bool:
        return (self.dir / a.value).is_file()

    def missing_reason(self, a: Artifact) -> str:
        """Why an artifact is missing, quoting the producing tool when it said."""
        if a is Artifact.SPDX:
            spdx_log = self.dir / "zmig-spdx.log"
            text = spdx_log.read_text() if spdx_log.is_file() else ""
            errors = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("ERROR:")]
            if errors:
                return f"west spdx: {errors[0].removeprefix('ERROR:').strip()}"
            if self.status != "ok":
                return f"the build {self.status.replace('-', ' ')}; west spdx needs a full build"
        if a in BUILD_ONLY and self.mode == "fast":
            return f"{a.value} needs a full build (rerun without --fast)"
        return f"{a.value} missing"

    def artifact(self, a: Artifact) -> Path:
        p = self.dir / a.value
        if p.is_file():
            return p
        raise ArtifactMissing(
            f"{a.value} missing for image {self.name} at {self.ref}: {self.missing_reason(a)}"
        )

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "role": self.role,
            "role_source": self.role_source,
            "status": self.status,
            "board": self.board,
        }


@dataclass(frozen=True)
class BuildContext:
    workspace: RefWorkspace
    spec: BuildSpec
    key: str
    dir: Path
    status: BuildStatus  # worst over the images and the sysbuild steps
    log: Path  # everything, in order (configure, each image, sysbuild)
    # The copy of the app that was built (inside the workspace; see app_source()).
    src: Path
    images: tuple[ImageContext, ...] = ()

    @property
    def app(self) -> ImageContext:
        return next(i for i in self.images if i.role == "app")

    def image(self, name: str) -> ImageContext | None:
        return next((i for i in self.images if i.name == name), None)

    def to_json(self) -> dict[str, object]:
        return {
            "ref": self.workspace.resolved.ref.spec,
            "board": self.spec.board,
            "mode": self.spec.mode,
            "sysbuild": self.spec.sysbuild,
            "status": self.status,
            "key": self.key,
            "images": [i.to_json() for i in self.images],
        }


@functools.cache
def python_environment() -> dict[str, str]:
    """Identity of the Python environment that runs Zephyr's build scripts.

    Part of the build key: a build that failed because a package was missing (MCUboot's
    imgtool needing `cryptography`, say) must not be served from cache after the
    package is installed, and two venvs must not share builds.
    """
    from importlib.metadata import distributions

    dists = sorted(
        {
            f"{d.metadata['Name']}=={d.version}".lower()
            for d in distributions()
            if d.metadata["Name"]
        }
    )
    return {
        "executable": sys.executable,
        "prefix": sys.prefix,  # the venv; its python often symlinks to a shared binary
        "version": sys.version.split()[0],
        "packages": hashlib.sha256("\n".join(dists).encode()).hexdigest()[:16],
    }


def build_key(ws: RefWorkspace, spec: BuildSpec, modules: list[Module]) -> str:
    from zmig import __version__

    material = {
        "recipe": RECIPE_VERSION,
        "zmig": __version__,
        "python": python_environment(),
        "ref": ws.resolved.ref.key,
        "modules": [[m.name, m.sha] for m in modules],
        "board": spec.board,
        "app": hash_tree(spec.app),
        "mode": spec.mode,
        "sysbuild": spec.sysbuild,
        "cmake_args": list(spec.cmake_args),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:16]


def app_source(ws: RefWorkspace, spec: BuildSpec) -> Path:
    """Where the app is copied before building: inside the ref's workspace.

    Zephyr's scripts locate the west workspace by searching upward from the app's own
    directory (west spdx does, for one). Building a copy placed inside zmig's workspace
    keeps those lookups isolated from whatever surrounds the user's app.
    """
    return ws.root / "apps" / hash_tree(spec.app)[:16] / spec.app.resolve().name


def _copy_app(src: Path, dest: Path) -> None:
    if dest.is_dir():
        return
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp, ignore=shutil.ignore_patterns(*APP_IGNORE_DIRS))
    tmp.rename(dest)


_BANNER = re.compile(r"^\s*\*\s+Running CMake for (?P<image>\S+)\s+\*\s*$", re.M)
_WRITTEN = re.compile(r"^-- Build files have been written to: (?P<dir>.+)$", re.M)
SYSBUILD_SEGMENT = "sysbuild"


def discover_images(bdir: Path, spec: BuildSpec) -> list[tuple[str, Path]]:
    """(name, build dir) of every image, in flash order, from sysbuild's domains.yaml (the
    file `west flash` reads). A plain build has no domains.yaml: one image, built in
    bdir itself. A sysbuild configure that failed before writing domains.yaml falls back
    to the image directories CMake created."""
    import yaml

    domains = bdir / "domains.yaml"
    name = spec.app.resolve().name
    if domains.is_file():
        d = yaml.safe_load(domains.read_text())
        dirs = {x["name"]: Path(x["build_dir"]) for x in d.get("domains", [])}
        order = d.get("flash_order") or list(dirs)
        return [(n, dirs[n]) for n in order if n in dirs] + [
            (n, p) for n, p in dirs.items() if n not in order
        ]
    if not spec.sysbuild:
        return [(name, bdir)]
    found = sorted(
        (p.name, p) for p in bdir.iterdir() if p.is_dir() and (p / "CMakeCache.txt").is_file()
    )
    # Configure stopped before the app image existed: keep it, as configure-failed.
    return found if any(n == name for n, _ in found) else [*found, (name, bdir / name)]


def split_configure_log(text: str, images: list[tuple[str, Path]]) -> dict[str, str]:
    """Attribute merged configure output to images. Sysbuild announces each image with a
    "Running CMake for <image>" banner; output before the first one is sysbuild's own.
    Without banners (a plain build, or a version that doesn't print them), a segment
    belongs to the image whose build directory it mentions most."""
    names = {n for n, _ in images}
    if len(images) == 1 and not _BANNER.search(text):
        return {images[0][0]: text}
    out: dict[str, str] = {}
    starts = [(m.start(), m["image"]) for m in _BANNER.finditer(text) if m["image"] in names]
    if starts:
        out[SYSBUILD_SEGMENT] = text[: starts[0][0]]
        for (pos, name), nxt in zip(starts, [*starts[1:], (len(text), "")], strict=True):
            out[name] = out.get(name, "") + text[pos : nxt[0]]
        return out
    for seg in re.split(r"(?<=\n)(?=-- Application: )", text):
        counts = {n: seg.count(str(d) + "/") + seg.count(str(d) + "\n") for n, d in images}
        best = max(counts, key=lambda n: counts[n]) if counts else ""
        key = best if counts.get(best) else SYSBUILD_SEGMENT
        out[key] = out.get(key, "") + seg
    return out


def _role(name: str, d: Path, default: str) -> tuple[ImageRole, str]:
    if name == default:
        return "app", "the default image (domains.yaml), or the only image"
    cfg = d / Artifact.CONFIG.value
    if cfg.is_file() and re.search(r"^CONFIG_MCUBOOT=y$", cfg.read_text(), re.M):
        return "bootloader", "CONFIG_MCUBOOT=y in the image's .config"
    return "other", "neither the default image nor MCUboot"


def build(ws: RefWorkspace, spec: BuildSpec, cache: Cache, runner: Runner) -> BuildContext:
    """Configure (fast) or build (full) `spec` in an isolated, cached directory.

    Steps, each logged on its own so output is attributed to the image that printed it:
      1. configure everything (`west build --cmake-only`), stdout+stderr merged in order;
      2. full mode: build each image separately (`cmake --build <image dir>`, in flash
         order);
      3. sysbuild: build the top level, which signs and merges what step 2 produced;
      4. generate SPDX per image with this ref's `west spdx`.
    A plain build is the one-image case of the same steps.
    """
    modules = ws.modules(runner)
    key = build_key(ws, spec, modules)
    src = app_source(ws, spec)
    bdir = cache.builds / key
    marker = cache.builds / f"{key}.json"
    log = cache.builds / f"{key}.log"
    if marker.is_file() and bdir.is_dir():
        meta = json.loads(marker.read_text())
        runner.redactions.setdefault(str(bdir), f"$BUILD[{key}]")
        images = tuple(_image_from_json(x, bdir, spec, ws) for x in meta["images"])
        return BuildContext(ws, spec, key, bdir, meta["status"], log, src, images)

    shutil.rmtree(bdir, ignore_errors=True)
    src.parent.mkdir(parents=True, exist_ok=True)
    _copy_app(spec.app.resolve(), src)
    logs = bdir / "zmig-logs"
    logs.mkdir(parents=True)
    # Ask CMake for its file-based API reply (codemodel), which `west spdx` reads. This
    # empty query file is what `west spdx --init` creates on every version from v3.6
    # to v4.4. Under sysbuild every image is its own CMake build, but image directories
    # are only known after configure, so the app's is pre-created here; the others get
    # one after configure, followed by a CMake re-run on that image (step 2).
    for d in [bdir] + ([bdir / spec.app.resolve().name] if spec.sysbuild else []):
        _cmake_query(d)
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
        str(src),
        "--sysbuild" if spec.sysbuild else "--no-sysbuild",
        "--cmake-only",
        "--",
        f"-DPython3_EXECUTABLE={sys.executable}",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        # Required by `west spdx` on every version; set on both sides of a comparison,
        # so it never shows up as a difference.
        "-DCONFIG_BUILD_OUTPUT_META=y",
        *spec.cmake_args,
    ]
    if modules:
        # Explicit, so the build sees exactly the modules that are in the cache key.
        argv.append("-DZEPHYR_MODULES=" + ";".join(str(m.path) for m in modules))
    # PYTHONPATH from the user's shell (e.g. ROS) can shadow the packages Zephyr's
    # scripts import; the build must see only zmig's interpreter environment.
    run_env = {"env": env, "unset_env": ["PYTHONPATH"], "check": False, "merge_output": True}
    conf = runner.run(argv, cwd=ws.root, **run_env)  # type: ignore[arg-type]
    configure_text = f"$ {' '.join(argv)}\n{conf.stdout}"
    found = discover_images(bdir, spec)
    segments = split_configure_log(configure_text, found)
    default = _default_image(bdir, spec)
    parts = [configure_text]
    statuses: dict[str, BuildStatus] = {}
    build_text: dict[str, str] = {}
    cmake = str(find_tool("cmake"))
    for name, d in found:
        configured = (d / "build.ninja").is_file() and (d / Artifact.CONFIG.value).is_file()
        statuses[name] = "ok" if configured else "configure-failed"
        if conf.returncode != 0 and not configured:
            continue
        if spec.mode == "full" and configured:
            # Images whose directories didn't exist before configure have no file-API
            # reply yet; re-running CMake on the existing cache generates it.
            # Sysbuild forwards -DCONFIG_BUILD_OUTPUT_META only to the default image, but
            # every image needs it for `west spdx` (set on both refs, so it never shows up
            # as a difference).
            no_meta = not re.search(
                r"^CONFIG_BUILD_OUTPUT_META=y$", (d / Artifact.CONFIG.value).read_text(), re.M
            )
            if no_meta or not (d / ".cmake" / "api" / "v1" / "reply").is_dir():
                _cmake_query(d)
                proc = runner.run([cmake, "-DCONFIG_BUILD_OUTPUT_META=y", str(d)], cwd=d, **run_env)  # type: ignore[arg-type]
                build_text[name] = f"$ cmake -DCONFIG_BUILD_OUTPUT_META=y {d}\n{proc.stdout}"
            proc = runner.run([cmake, "--build", str(d)], **run_env)  # type: ignore[arg-type]
            build_text[name] = build_text.get(name, "") + f"$ cmake --build {d}\n{proc.stdout}"
            parts.append(build_text[name])
            if proc.returncode != 0:
                statuses[name] = "build-failed"
    overall: BuildStatus = "ok"
    if conf.returncode != 0:
        overall = "configure-failed"
    elif any(s == "build-failed" for s in statuses.values()):
        overall = "build-failed"
    if spec.sysbuild and spec.mode == "full" and overall == "ok":
        proc = runner.run([cmake, "--build", str(bdir)], **run_env)  # type: ignore[arg-type]
        top = f"$ cmake --build {bdir}\n{proc.stdout}"
        parts.append(top)
        segments[SYSBUILD_SEGMENT] = segments.get(SYSBUILD_SEGMENT, "") + top
        if proc.returncode != 0:
            overall = "build-failed"
    log.write_text("\n".join(parts))
    if segments.get(SYSBUILD_SEGMENT):
        (logs / f"{SYSBUILD_SEGMENT}.log").write_text(segments[SYSBUILD_SEGMENT])

    built: list[ImageContext] = []
    for name, d in found:
        ilog = logs / f"{name}.log"
        ilog.write_text(segments.get(name, "") + build_text.get(name, ""))
        if spec.mode == "full" and statuses[name] == "ok":
            _spdx(ws, d, env, runner)
        role, why = _role(name, d, default)
        # The image's own board target: in multi-core sysbuild an image can be built for
        # another core's board target than the one passed with -b.
        board = spec.board
        cfg = d / Artifact.CONFIG.value
        if cfg.is_file() and (
            bt := re.search(r'^CONFIG_BOARD_TARGET="([^"]+)"$', cfg.read_text(), re.M)
        ):
            board = bt[1]
        built.append(
            ImageContext(
                name,
                role,
                why,
                d,
                ilog,
                statuses[name],
                spec.mode,
                board,
                ws.resolved.ref.spec,
            )
        )
    marker.write_text(
        json.dumps(
            {
                "status": overall,
                "spec": {
                    "app": str(spec.app),
                    "board": spec.board,
                    "mode": spec.mode,
                    "sysbuild": spec.sysbuild,
                    "cmake_args": list(spec.cmake_args),
                },
                "ref": ws.resolved.ref.to_json(),
                "images": [{**i.to_json(), "dir": str(i.dir), "log": str(i.log)} for i in built],
            },
            indent=2,
        )
    )
    runner.redactions.setdefault(str(bdir), f"$BUILD[{key}]")
    return BuildContext(ws, spec, key, bdir, overall, log, src, tuple(built))


def _default_image(bdir: Path, spec: BuildSpec) -> str:
    import yaml

    domains = bdir / "domains.yaml"
    if domains.is_file():
        return str(yaml.safe_load(domains.read_text()).get("default", ""))
    return spec.app.resolve().name


def _image_from_json(
    x: dict[str, Any], bdir: Path, spec: BuildSpec, ws: RefWorkspace
) -> ImageContext:
    return ImageContext(
        x["name"],
        x["role"],
        x["role_source"],
        Path(x["dir"]),
        Path(x["log"]),
        x["status"],
        spec.mode,
        x.get("board", spec.board),
        ws.resolved.ref.spec,
    )


def _cmake_query(d: Path) -> None:
    query = d / ".cmake" / "api" / "v1" / "query" / "codemodel-v2"
    query.parent.mkdir(parents=True, exist_ok=True)
    query.touch()


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
        # Each image's own log: under sysbuild, errors in one image's configure name
        # files of the app copy that build used (prj.conf, sysbuild/<image>.conf, ...).
        edits = []
        for img in current.images:
            edits += locate_salvage_edits(img.log.read_text(), current.src)
        if not current.images:
            edits = locate_salvage_edits(current.log.read_text(), current.src)
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
    return SalvageResult(
        original, current, tuple(sorted(all_edits, key=lambda e: (e.file, e.line)))
    )


def _spdx(ws: RefWorkspace, image_dir: Path, env: dict[str, str], runner: Runner) -> None:
    """SPDX documents for one image with this ref's own `west spdx` (best effort: a
    failure leaves the artifact missing, and missing_reason() quotes spdx's error). The
    exit status isn't trusted: v3.6's spdx exits 0 after "bailing"."""
    argv = [*west_argv(), "spdx", "-d", str(image_dir), "-s", str(image_dir / "spdx")]
    proc = runner.run(
        argv, cwd=ws.root, env=env, unset_env=["PYTHONPATH"], check=False, merge_output=True
    )
    (image_dir / "zmig-spdx.log").write_text(
        f"$ {' '.join(argv)}\nexit {proc.returncode}\n{proc.stdout}"
    )
