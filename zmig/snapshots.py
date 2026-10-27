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
from pathlib import Path
from typing import Any

from zmig.buildctx import Artifact, BuildContext
from zmig.errors import ArtifactMissing, ZmigError
from zmig.tools import Runner

EXTRACT = Path(__file__).parent / "extract"
SNAPSHOT_VERSION = 2

ConfigValues = dict[str, str]


def parse_dotconfig(text: str) -> ConfigValues:
    """CONFIG_X=v lines -> {X: v}; '# CONFIG_X is not set' -> {X: 'n'}."""
    out: ConfigValues = {}
    for line in text.splitlines():
        if m := re.match(r"^CONFIG_(\w+)=(.*)$", line):
            out[m[1]] = m[2]
        elif m := re.match(r"^# CONFIG_(\w+) is not set$", line):
            out[m[1]] = "n"
    return out


def config_values(ctx: BuildContext) -> ConfigValues:
    return parse_dotconfig(ctx.artifact(Artifact.CONFIG).read_text())


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


def _cached(ctx: BuildContext, name: str, produce: Any) -> dict[str, Any]:
    path = ctx.app_dir / f"zmig-{name}.json"
    if path.is_file():
        data: dict[str, Any] = json.loads(path.read_text())
        if data.get("_v") == SNAPSHOT_VERSION:
            return data
    produce(path)
    data = json.loads(path.read_text())
    data["_v"] = SNAPSHOT_VERSION
    path.write_text(json.dumps(data, sort_keys=True))
    return data


def kconfig_symbols(ctx: BuildContext, runner: Runner) -> dict[str, dict[str, Any]]:
    ninja = ctx.app_dir / "build.ninja"
    if not ninja.is_file():
        raise ArtifactMissing(
            f"build.ninja missing for {ctx.spec.board} at "
            f"{ctx.workspace.resolved.ref.spec} (configure failed)"
        )

    def produce(out: Path) -> None:
        env, root = menuconfig_env(ninja)
        runner.run(
            [
                sys.executable,
                EXTRACT / "kconfig_dump.py",
                ctx.workspace.zephyr / "scripts" / "kconfig",
                root,
                out,
            ],
            env=env,
            unset_env=["PYTHONPATH"],
        )

    syms: dict[str, dict[str, Any]] = _cached(ctx, "kconfig", produce)["symbols"]
    return syms


def edt(ctx: BuildContext, runner: Runner) -> dict[str, Any]:
    pickle_path = ctx.artifact(Artifact.EDT)

    def produce(out: Path) -> None:
        runner.run(
            [
                sys.executable,
                EXTRACT / "edt_dump.py",
                ctx.workspace.zephyr / "scripts" / "dts" / "python-devicetree" / "src",
                pickle_path,
                ctx.workspace.root,
                out,
            ],
            unset_env=["PYTHONPATH"],
        )

    return _cached(ctx, "edt", produce)
