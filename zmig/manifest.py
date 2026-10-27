"""West manifests: loading them through west's own API, and inspecting a user's workspace.

Everything here is read-only with respect to the user's workspace. Git queries use
`--no-optional-locks` so not even the index stat cache is refreshed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from zmig.errors import ZmigError
from zmig.tools import Runner, find_tool

log = logging.getLogger("zmig")


@dataclass(frozen=True)
class ProjectInfo:
    """One project as a manifest declares it."""

    name: str
    url: str
    revision: str
    path: str
    groups: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "url": self.url,
            "revision": self.revision,
            "path": self.path,
            "groups": list(self.groups),
        }


def load_manifest(topdir: Path) -> dict[str, ProjectInfo]:
    """All projects of the manifest in a west workspace, keyed by name.

    Uses west.manifest so imports, allowlists and group filters resolve exactly as
    west resolves them. Projects whose own manifests can't be imported (because
    they aren't cloned) are kept, but their imports are ignored.
    """
    from west.manifest import ImportFlag, Manifest, ManifestImportFailed

    try:
        m = Manifest.from_topdir(topdir=topdir)
    except ManifestImportFailed:
        log.info("manifest imports in %s need uncloned projects; ignoring those imports", topdir)
        m = Manifest.from_topdir(topdir=topdir, import_flags=ImportFlag.IGNORE_PROJECTS)
    except Exception as e:  # west raises several unrelated types for bad manifests
        raise ZmigError(f"could not load the west manifest in {topdir}: {e}") from e
    out: dict[str, ProjectInfo] = {}
    for p in m.projects[1:]:  # projects[0] is the manifest repository itself
        out[p.name] = ProjectInfo(p.name, p.url, p.revision, p.path, tuple(p.groups))
    return out


def manifest_repo_path(topdir: Path) -> str:
    from west.manifest import Manifest

    return str(Manifest.from_topdir(topdir=topdir).projects[0].path)


PresenceKind = Literal["git", "tree", "missing"]


@dataclass(frozen=True)
class UserProject:
    """A project in the user's workspace and what is actually on disk for it."""

    name: str
    path: Path
    presence: PresenceKind
    declared: ProjectInfo | None  # None for the manifest repository itself
    is_manifest_repo: bool
    head: str | None = None
    # Uncommitted changes (git checkouts only): porcelain status lines.
    dirty: tuple[str, ...] = ()
    # Commits reachable from HEAD but from no remote-tracking ref (local, unpushed).
    unpushed: frozenset[str] = frozenset()


def _git_ro(runner: Runner, path: Path, *args: str, check: bool = True) -> str:
    proc = runner.run([find_tool("git"), "--no-optional-locks", "-C", path, *args], check=check)
    return proc.stdout if proc.returncode == 0 else ""


def inspect_project(
    runner: Runner, name: str, path: Path, declared: ProjectInfo | None, is_manifest_repo: bool
) -> UserProject:
    if not path.exists():
        return UserProject(name, path, "missing", declared, is_manifest_repo)
    if not (path / ".git").exists():
        return UserProject(name, path, "tree", declared, is_manifest_repo)
    head = _git_ro(runner, path, "rev-parse", "HEAD").strip()
    dirty = tuple(
        sorted(line for line in _git_ro(runner, path, "status", "--porcelain").splitlines() if line)
    )
    unpushed = frozenset(
        _git_ro(runner, path, "rev-list", "HEAD", "--not", "--remotes", check=False).split()
    )
    return UserProject(name, path, "git", declared, is_manifest_repo, head, dirty, unpushed)


@dataclass(frozen=True)
class UserWorkspace:
    topdir: Path
    projects: dict[str, UserProject]

    @property
    def manifest_repo(self) -> UserProject:
        return next(p for p in self.projects.values() if p.is_manifest_repo)


def scan_user_workspace(topdir: Path, runner: Runner) -> UserWorkspace:
    """Read the user's west workspace: manifest, then what is on disk for each project."""
    topdir = topdir.resolve()
    if not (topdir / ".west" / "config").is_file():
        raise ZmigError(
            f"{topdir} is not a west workspace (no .west/config); pass --workspace <topdir>"
        )
    declared = load_manifest(topdir)
    mpath = manifest_repo_path(topdir)
    projects = {"manifest": inspect_project(runner, "manifest", topdir / mpath, None, True)}
    for name, info in declared.items():
        projects[name] = inspect_project(runner, name, topdir / info.path, info, False)
    return UserWorkspace(topdir, projects)
