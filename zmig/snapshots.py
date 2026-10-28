"""Normalized snapshots of a build's resolved configuration and devicetree.

  config_values()   final .config, parsed directly
  kconfig_symbols() symbol metadata, via that tree's kconfiglib (extract/kconfig_dump.py)
  edt()             final devicetree, via that tree's edtlib (extract/edt_dump.py)

Kconfig needs the environment the build gave it. Zephyr's build.ninja carries that
exact environment on the `menuconfig` target's command line (`cmake -E env K=V ...
menuconfig.py <Kconfig root>`), on every version checked (v3.6.0 to v4.4.2), so it is
read from there rather than reconstructed.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
import tempfile
from pathlib import Path
from typing import Any

from zmig.buildctx import Artifact, ImageContext
from zmig.errors import ArtifactMissing, ZmigError
from zmig.tools import Runner
from zmig.workspace import RefWorkspace

EXTRACT = Path(__file__).parent / "extract"
SNAPSHOT_VERSION = 6

ConfigValues = dict[str, str]


def parse_dotconfig(text: str, prefix: str = "CONFIG_") -> ConfigValues:
    """<prefix>X=v lines -> {X: v}; '# <prefix>X is not set' -> {X: 'n'}. Sysbuild's own
    config uses the prefix SB_CONFIG_."""
    out: ConfigValues = {}
    p = re.escape(prefix)
    for line in text.splitlines():
        if m := re.match(rf"^{p}(\w+)=(.*)$", line):
            out[m[1]] = m[2]
        elif m := re.match(rf"^# {p}(\w+) is not set$", line):
            out[m[1]] = "n"
    return out


def config_values(img: ImageContext) -> ConfigValues:
    return parse_dotconfig(img.artifact(Artifact.CONFIG).read_text())


def menuconfig_env(build_ninja: Path) -> tuple[dict[str, str], Path]:
    """(environment, Kconfig root) from the menuconfig rule in build.ninja."""
    text = build_ninja.read_text()
    for m in re.finditer(r"^\s*COMMAND = (.*)$", text, re.M):
        cmd = m[1]
        if "menuconfig.py" not in cmd:
            continue
        # The command is `cd <dir> && <cmake> -E env K=V ... <python> menuconfig.py <root>`.
        part = next(p for p in cmd.split(" && ") if "menuconfig.py" in p)
        argv = shlex.split(part)
        i = argv.index("env") + 1
        env: dict[str, str] = {}
        while i < len(argv) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[i]):
            k, v = argv[i].split("=", 1)
            env[k] = v
            i += 1
        return env, Path(argv[-1])
    raise ZmigError(
        f"no menuconfig rule in {build_ninja}; cannot reproduce the Kconfig "
        f"environment of this build"
    )


def _cached(where: ImageContext | Path, name: str, produce: Any) -> dict[str, Any]:
    path = (where.dir if isinstance(where, ImageContext) else where) / f"zmig-{name}.json"
    if path.is_file():
        data: dict[str, Any] = json.loads(path.read_text())
        if data.get("_v") == SNAPSHOT_VERSION:
            return data
    produce(path)
    data = json.loads(path.read_text())
    data["_v"] = SNAPSHOT_VERSION
    path.write_text(json.dumps(data, sort_keys=True))
    return data


def kconfig_symbols(
    img: ImageContext, ws: RefWorkspace, runner: Runner
) -> dict[str, dict[str, Any]]:
    ninja = img.dir / "build.ninja"
    if not ninja.is_file():
        raise ArtifactMissing(
            f"build.ninja missing for image {img.name} at {img.ref} (configure failed)"
        )

    def produce(out: Path) -> None:
        env, root = menuconfig_env(ninja)
        runner.run(
            [
                sys.executable,
                EXTRACT / "kconfig_dump.py",
                ws.zephyr / "scripts" / "kconfig",
                root,
                out,
            ],
            env=env,
            unset_env=["PYTHONPATH"],
        )

    syms: dict[str, dict[str, Any]] = _cached(img, "kconfig", produce)["symbols"]
    return syms


_FRAGMENT = re.compile(r"^(?:Loaded|Merged) configuration '(.+)'$")


def kconfig_try(
    img: ImageContext,
    ws: RefWorkspace,
    runner: Runner,
    assign: dict[str, str],
    report: list[str],
) -> dict[str, str | None] | None:
    """The image's Kconfig re-resolved with `assign` merged after the fragments its
    configure log names (zmig/extract/kconfig_try.py). None if the fragments aren't in the
    log. Not cached: each call is a different question."""
    ninja = img.dir / "build.ninja"
    if not ninja.is_file() or not img.log.is_file():
        return None
    frags = [m[1] for ln in img.log.read_text().splitlines() if (m := _FRAGMENT.match(ln))]
    if not frags or not all(Path(f).is_file() for f in frags):
        return None
    env, root = menuconfig_env(ninja)
    with tempfile.TemporaryDirectory() as d:
        req, out = Path(d) / "req.json", Path(d) / "out.json"
        req.write_text(json.dumps({"fragments": frags, "assign": assign, "report": report}))
        runner.run(
            [
                sys.executable,
                EXTRACT / "kconfig_try.py",
                ws.zephyr / "scripts" / "kconfig",
                root,
                req,
                out,
            ],
            env=env,
            unset_env=["PYTHONPATH"],
        )
        values: dict[str, str | None] = json.loads(out.read_text())["values"]
    return values


def edt(img: ImageContext, ws: RefWorkspace, runner: Runner) -> dict[str, Any]:
    pickle_path = img.artifact(Artifact.EDT)

    def produce(out: Path) -> None:
        runner.run(
            [
                sys.executable,
                EXTRACT / "edt_dump.py",
                ws.zephyr / "scripts" / "dts" / "python-devicetree" / "src",
                pickle_path,
                ws.root,
                out,
            ],
            unset_env=["PYTHONPATH"],
        )

    return _cached(img, "edt", produce)


def init_levels(img: ImageContext, ws: RefWorkspace, runner: Runner) -> dict[str, Any]:
    """The init calls of an image in run order, read by this ref's own
    check_init_priorities.py (extract/init_dump.py). Raises ArtifactMissing when the ref
    has no such reader (before v3.5) or the image has no ELF."""
    elf = img.artifact(Artifact.ELF)
    # What Zephyr's own check reads: the native runner executable when there is one
    # (native_sim's zephyr.elf is an intermediate library), else the ELF (CMakeLists.txt,
    # check_init_priorities_input).
    exe = elf.with_suffix(".exe")
    if exe.is_file():
        elf = exe
    pickle_path = img.artifact(Artifact.EDT)
    script = ws.zephyr / "scripts" / "build" / "check_init_priorities.py"
    if not script.is_file() or "class ZephyrInitLevels" not in script.read_text():
        raise ArtifactMissing(
            f"{ws.resolved.ref.label} has no scripts/build/check_init_priorities.py that can "
            "list init levels (ZephyrInitLevels, added in Zephyr v3.5)"
        )

    def produce(out: Path) -> None:
        proc = runner.run(
            [sys.executable, EXTRACT / "init_dump.py", script.parent, elf, pickle_path, out],
            unset_env=["PYTHONPATH"],
            check=False,
        )
        if proc.returncode != 0:
            last = (proc.stderr.strip().splitlines() or ["no output"])[-1]
            raise ArtifactMissing(
                f"{ws.resolved.ref.label}'s check_init_priorities.py can't read the init "
                f"levels of image {img.name}: {last}"
            )

    return _cached(img, "initlevels", produce)


def app_undefined_symbols(img: ImageContext, app: Path, runner: Runner) -> set[str] | None:
    """Symbols the app's own object files reference but don't define (`nm -u`): the
    Zephyr APIs it actually calls. None when there are no objects (a --fast build)."""
    cc = img.dir / "compile_commands.json"
    if not cc.is_file():
        return None
    root = str(app.resolve()) + "/"
    objects = []
    for entry in json.loads(cc.read_text()):
        if not str(entry.get("file", "")).startswith(root) or "output" not in entry:
            continue
        obj = Path(entry["directory"]) / entry["output"]
        if obj.is_file():
            objects.append(obj)
    if not objects:
        return None
    nm = "nm"
    cache_txt = img.dir / "CMakeCache.txt"
    if cache_txt.is_file() and (
        m := re.search(r"^CMAKE_NM:\w+=(.+)$", cache_txt.read_text(), re.M)
    ):
        nm = m[1].strip() or nm
    out = runner.run([nm, "-u", *sorted(objects)]).stdout
    return {line.split()[-1] for line in out.splitlines() if line.strip().startswith("U ")}


# --- sysbuild level ---------------------------------------------------------------------

SYSBUILD_PREFIX = "SB_CONFIG_"


def sysbuild_config(bdir: Path) -> ConfigValues | None:
    """Sysbuild's resolved config (<build>/zephyr/.config, prefix SB_CONFIG_), or None for
    a plain build."""
    cfg = bdir / "zephyr" / ".config"
    if not (bdir / "domains.yaml").is_file() or not cfg.is_file():
        return None
    return parse_dotconfig(cfg.read_text(), SYSBUILD_PREFIX)


def sysbuild_kconfig(bdir: Path, ws: RefWorkspace, runner: Runner) -> dict[str, dict[str, Any]]:
    """Sysbuild's Kconfig metadata, from the menuconfig rule of the top-level build.ninja
    (its root is share/sysbuild/Kconfig), with that ref's kconfiglib."""
    ninja = bdir / "build.ninja"
    if not ninja.is_file():
        raise ArtifactMissing(f"sysbuild build.ninja missing in {bdir}")

    def produce(out: Path) -> None:
        env, root = menuconfig_env(ninja)
        runner.run(
            [
                sys.executable,
                EXTRACT / "kconfig_dump.py",
                ws.zephyr / "scripts" / "kconfig",
                root,
                out,
            ],
            env=env,
            unset_env=["PYTHONPATH"],
        )

    syms: dict[str, dict[str, Any]] = _cached(bdir, "sysbuild-kconfig", produce)["symbols"]
    return syms
