"""Per-ref west workspaces in the cache.

Each ref gets <cache>/workspaces/<ref key>/ laid out like a `west init -l`
workspace: the zephyr worktree at `zephyr/` and a `.west/config` pointing at its
west.yml. Modules are cloned on demand with `west update --narrow <names>`, so
only what the project needs is fetched, and nothing else is visible to the build.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from zmig.cache import Cache
from zmig.errors import ZmigError
from zmig.refs import ResolvedRef
from zmig.tools import Runner, find_tool

WEST_CONFIG = """\
[manifest]
path = zephyr
file = west.yml

[zephyr]
base = zephyr
"""


def west_argv() -> list[str]:
    """west, run with zmig's interpreter so Zephyr's extension commands see the same
    Python packages that check_zephyr_python() verified."""
    return [sys.executable, "-m", "west"]


@dataclass(frozen=True)
class Module:
    name: str
    path: Path
    sha: str


@dataclass(frozen=True)
class RefWorkspace:
    resolved: ResolvedRef
    root: Path

    @property
    def zephyr(self) -> Path:
        return self.root / "zephyr"

    def activate(self, names: Sequence[str], runner: Runner) -> None:
        """Make exactly `names` active via west's manifest.project-filter.

        Zephyr's module discovery only considers active projects, so this pins what
        a build sees to the requested set, regardless of what an earlier run cloned
        into this shared per-ref workspace. (Builds of the same ref with different
        module sets must therefore not run concurrently.)
        """
        rules = ["-.*", *(f"+{re.escape(n)}" for n in sorted(names))]
        runner.run(
            [*west_argv(), "config", "manifest.project-filter", "--", ",".join(rules)],
            cwd=self.root,
            unset_env=["ZEPHYR_BASE"],
        )

    def modules(self, runner: Runner) -> list[Module]:
        """Active, cloned projects other than the manifest repo, with their commit."""
        out = runner.run(
            [*west_argv(), "list", "--format", "{name}|{cloned}|{abspath}"],
            cwd=self.root,
            unset_env=["ZEPHYR_BASE"],
        ).stdout
        found = []
        for line in out.splitlines():
            name, cloned, path = line.split("|", 2)
            if name == "manifest" or cloned != "cloned":
                continue
            sha = runner.run([find_tool("git"), "-C", path, "rev-parse", "HEAD"]).stdout.strip()
            found.append(Module(name, Path(path), sha))
        return sorted(found, key=lambda m: m.name)


def materialize(
    resolved: ResolvedRef, cache: Cache, runner: Runner, modules: Sequence[str] = ()
) -> RefWorkspace:
    """Create or reuse the workspace for a ref, cloning `modules` (west project names)."""
    root = cache.workspaces / resolved.ref.key
    resolved.mirror.checkout(resolved.ref, root / "zephyr", resolved.dirty)
    if not (root / "zephyr" / "west.yml").is_file():
        raise ZmigError(f"{resolved.ref.spec} has no west.yml; is it a Zephyr tree?")
    cfg = root / ".west" / "config"
    if not cfg.is_file():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(WEST_CONFIG)
    ws = RefWorkspace(resolved, root)
    ws.activate(modules, runner)
    if modules:
        runner.run(
            [*west_argv(), "update", "--narrow", *sorted(modules)],
            cwd=root,
            unset_env=["ZEPHYR_BASE"],
        )
    return ws
