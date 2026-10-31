"""Per-ref west workspaces in the cache.

Each ref gets <cache>/workspaces/<ref key>/ laid out like a `west init -l`
workspace: the zephyr worktree at `zephyr/` and a `.west/config` pointing at its
west.yml. Modules are cloned on demand with `west update --narrow <names>`, so
only what the project needs is fetched, and nothing else is visible to the build.

A process holds an exclusive lock on each workspace it materializes until it exits:
the active module set is workspace state, so two runs of the same ref with different
modules would otherwise build (or analyze) against each other's set.
"""

from __future__ import annotations

import fcntl
import logging
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from zmig.cache import Cache
from zmig.errors import ZmigError
from zmig.refs import ResolvedRef
from zmig.tools import Runner, find_tool

log = logging.getLogger("zmig")

WEST_CONFIG = """\
[manifest]
path = zephyr
file = west.yml

[zephyr]
base = zephyr
"""


_held: dict[Path, IO[str]] = {}


def lock_workspace(lock: Path) -> None:
    """Hold an exclusive lock on a workspace until this process exits (idempotent)."""
    if lock in _held:
        return
    lock.parent.mkdir(parents=True, exist_ok=True)
    f = lock.open("a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.warning("waiting for another zmig run using %s", lock.with_suffix(""))
        fcntl.flock(f, fcntl.LOCK_EX)
    _held[lock] = f


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
        into this shared per-ref workspace. (Concurrent runs are serialized by
        lock_workspace().)
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

    def shallow_modules(self, runner: Runner) -> list[str]:
        """Active modules cloned without history (`--shallow`, or an earlier shallow run)."""
        out = []
        for m in self.modules(runner):
            proc = runner.run(
                [find_tool("git"), "-C", m.path, "rev-parse", "--is-shallow-repository"],
                check=False,
            )
            if proc.stdout.strip() == "true":
                out.append(m.name)
        return out


def materialize(
    resolved: ResolvedRef,
    cache: Cache,
    runner: Runner,
    modules: Sequence[str] = (),
    shallow: bool = False,
) -> RefWorkspace:
    """Create or reuse the workspace for a ref, cloning `modules` (west project names).

    shallow: fetch only each module's pinned commit (`git fetch --depth=1`), about 5x
    less to download than its history (DESIGN.md, "CI mode (zmig watch)"). Nothing in `scan`
    reads module history; fork triage would (see shallow_modules()).
    """
    root = cache.workspaces / resolved.ref.key
    lock_workspace(root.with_name(root.name + ".lock"))
    resolved.mirror.checkout(resolved.ref, root / "zephyr", resolved.dirty)
    if not (root / "zephyr" / "west.yml").is_file():
        raise ZmigError(f"{resolved.ref.label} has no west.yml; is it a Zephyr tree?")
    cfg = root / ".west" / "config"
    if not cfg.is_file():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(WEST_CONFIG)
    ws = RefWorkspace(resolved, root)
    # A module requested for both refs may not exist in this ref's manifest (e.g. cmsis_6
    # before v4.1, tinycrypt from v4.3); west update would fail on it, so skip it here.
    from zmig.manifest import load_manifest

    known = set(load_manifest(root))
    missing = sorted(set(modules) - known)
    if missing:
        log.warning("not in %s's manifest, skipped: %s", resolved.ref.label, ", ".join(missing))
    modules = [m for m in modules if m in known]
    ws.activate(modules, runner)
    if modules:
        runner.run(
            [
                *west_argv(),
                "update",
                "--narrow",
                *(["-o=--depth=1"] if shallow else []),
                *sorted(modules),
            ],
            cwd=root,
            unset_env=["ZEPHYR_BASE"],
            progress=f"updating modules {', '.join(sorted(modules))} for {resolved.ref.label}",
        )
    return ws
