"""Inputs shared by the `plan` analyzers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from zmig.cache import Cache
from zmig.manifest import ProjectInfo, UserWorkspace
from zmig.refs import Mirror
from zmig.tools import Runner
from zmig.workspace import RefWorkspace


def same_repo(a: str, b: str) -> bool:
    """URL equality ignoring scheme, trailing slash and .git suffix."""
    from zmig.refs import url_slug

    return url_slug(a).lower() == url_slug(b).lower()


@dataclass
class PlanContext:
    cache: Cache
    runner: Runner
    old: RefWorkspace
    new: RefWorkspace
    old_manifest: dict[str, ProjectInfo]
    new_manifest: dict[str, ProjectInfo]
    user: UserWorkspace | None
    upstream_overrides: dict[str, str] = field(default_factory=dict)
    _mirrors: dict[str, Mirror] = field(default_factory=dict)

    @property
    def scope(self) -> tuple[str, str]:
        return (self.old.resolved.ref.sha, self.new.resolved.ref.sha)

    def mirror(self, url: str) -> Mirror:
        """The cache mirror for `url`, reusing the zephyr mirror the refs came from."""
        zephyr = self.old.resolved.mirror
        if same_repo(url, zephyr.url):
            return zephyr
        if url not in self._mirrors:
            self._mirrors[url] = Mirror(url, self.cache, self.runner)
        return self._mirrors[url]

    def rel(self, path: Path) -> str:
        """A path relative to the user's workspace, for evidence."""
        if self.user is not None:
            try:
                return path.resolve().relative_to(self.user.topdir).as_posix()
            except ValueError:
                pass
        return str(path)
