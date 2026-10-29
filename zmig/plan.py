"""Inputs shared by the `plan` analyzers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from zmig.cache import Cache
from zmig.forks import DEFAULT_METHODS, PatchMethod
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
    release_data_path: Path | None = None
    # How the project applies its local patches (zmig.forks); and where its patch files
    # live, to name them in findings (matched to commits by file name).
    patch_methods: tuple[PatchMethod, ...] = DEFAULT_METHODS
    patch_dir: Path | None = None
    _mirrors: dict[str, Mirror] = field(default_factory=dict)

    def patch_file(self, subject: str, message: str = "") -> str | None:
        """The project patch file a local commit came from: a file under patch_dir whose
        name is the commit's subject or appears in its message."""
        if self.patch_dir is None:
            return None
        names = {f.name: f for f in sorted(self.patch_dir.rglob("*.patch"))}  # as given
        hit = names.get(subject.strip()) or next(
            (f for n, f in names.items() if n in message), None
        )
        if hit is None:
            return None
        return (self.patch_dir / hit.relative_to(self.patch_dir)).as_posix()

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
