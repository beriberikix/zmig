"""Resolve --from/--to specs to commits and check them out in isolation.

zmig never writes to a user's repository. It keeps its own bare mirror per
upstream URL in the cache, cloned fresh from that URL, and creates worktrees
off the mirror. A local-path ref is read with `git fetch <path>` (read-only on
the source) and `git --no-optional-locks` (so `git status` doesn't refresh the
user's index).
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from zmig.cache import Cache
from zmig.errors import RefNotFound, ZmigError
from zmig.tools import Runner, find_tool

UPSTREAM_ZEPHYR = "https://github.com/zephyrproject-rtos/zephyr"

RefKind = Literal["tag", "branch", "sha", "path"]


def url_slug(url: str) -> str:
    """github.com/zephyrproject-rtos/zephyr -> github.com_zephyrproject-rtos_zephyr"""
    s = re.sub(r"^[a-z+]+://", "", url.rstrip("/"))
    s = re.sub(r"^[^@]+@", "", s).replace(":", "/")
    s = s.removesuffix(".git")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)


@dataclass(frozen=True)
class Ref:
    spec: str
    sha: str
    kind: RefKind
    url: str
    # Hash of the uncommitted changes snapshotted from a path ref; None if clean.
    dirty_hash: str | None = None

    @property
    def dirty(self) -> bool:
        return self.dirty_hash is not None

    @property
    def label(self) -> str:
        """How the ref is shown in findings: the spec, except that a local checkout is
        shown by its directory name and commit (regression: absolute scratch paths in
        every title of the ZMK dogfood report)."""
        if self.kind == "path":
            return f"{Path(self.spec).name}@{self.sha[:12]}"
        return self.spec

    @property
    def key(self) -> str:
        """Identity of the tree contents, for cache keys."""
        return self.sha if self.dirty_hash is None else f"{self.sha}-dirty-{self.dirty_hash}"

    def to_json(self) -> dict[str, object]:
        return {
            "spec": self.spec,
            "sha": self.sha,
            "kind": self.kind,
            "url": self.url,
            "dirty": self.dirty,
        }


class Mirror:
    """A cache-owned bare clone of one upstream repository."""

    def __init__(self, url: str, cache: Cache, runner: Runner) -> None:
        self.url = url
        self.runner = runner
        self.path = cache.mirrors / f"{url_slug(url)}.git"
        self.git = find_tool("git")
        self._fetched = False

    def _git(self, *args: str, check: bool = True) -> str:
        proc = self.runner.run([self.git, "--git-dir", self.path, *args], check=check)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def ensure(self) -> None:
        if self.path.is_dir():
            return
        tmp = self.path.with_name(self.path.name + ".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.runner.run([self.git, "clone", "--bare", self.url, tmp])
        tmp.rename(self.path)
        # A plain --bare clone sets no fetch refspec; track branches and tags.
        self._git("config", "remote.origin.fetch", "+refs/heads/*:refs/heads/*")
        self._fetched = True

    def fetch(self) -> None:
        if self._fetched:
            return
        self._git("fetch", "--prune", "--tags", "origin")
        self._fetched = True

    def _lookup(self, spec: str) -> tuple[str, RefKind] | None:
        for prefix, kind in (("refs/tags/", "tag"), ("refs/heads/", "branch")):
            sha = self._git(
                "rev-parse", "--verify", "--quiet", f"{prefix}{spec}^{{commit}}", check=False
            )
            if sha:
                return sha, kind  # type: ignore[return-value]
        if re.fullmatch(r"[0-9a-fA-F]{4,40}", spec):
            sha = self._git("rev-parse", "--verify", "--quiet", f"{spec}^{{commit}}", check=False)
            if sha:
                return sha, "sha"
        return None

    def resolve(self, spec: str) -> tuple[str, RefKind]:
        """Resolve a tag, branch or SHA, fetching once if it isn't known yet."""
        self.ensure()
        found = self._lookup(spec)
        if found is None:
            self.fetch()
            found = self._lookup(spec)
        if found is None:
            raise RefNotFound(f"'{spec}' is not a tag, branch or commit in {self.url}")
        return found

    def fetch_path(self, path: Path) -> str:
        """Copy the HEAD commit of a local checkout into the mirror (read-only on `path`)."""
        self.ensure()
        sha = self.runner.run([self.git, "-C", path, "rev-parse", "HEAD"]).stdout.strip()
        self._git("fetch", "--no-tags", str(path), f"+{sha}:refs/zmig/local/{sha}")
        return sha

    def checkout(self, ref: Ref, dest: Path, dirty: DirtySnapshot | None = None) -> Path:
        """Create (or reuse) a detached worktree of `ref` at `dest`.

        A marker next to `dest` records the ref key once checkout (and any dirty
        snapshot) completed, so an interrupted checkout is redone, not reused.
        """
        marker = dest.with_name(dest.name + ".zmig-ready")
        if dest.is_dir() and marker.is_file() and marker.read_text() == ref.key:
            return dest
        marker.unlink(missing_ok=True)
        if dest.exists():
            self._git("worktree", "remove", "--force", str(dest), check=False)
            shutil.rmtree(dest, ignore_errors=True)
        self._git("worktree", "prune")
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._git("worktree", "add", "--detach", str(dest), ref.sha)
        if dirty is not None:
            dirty.apply_to(dest, self.runner)
        marker.write_text(ref.key)
        return dest


@dataclass(frozen=True)
class DirtySnapshot:
    """Uncommitted state of a local checkout: a diff of tracked files plus copies of
    untracked (non-ignored) files, stored under the cache."""

    dir: Path
    digest: str

    @property
    def patch(self) -> Path:
        return self.dir / "tracked.patch"

    @property
    def untracked(self) -> Path:
        return self.dir / "untracked"

    def apply_to(self, worktree: Path, runner: Runner) -> None:
        if self.patch.stat().st_size:
            runner.run([find_tool("git"), "-C", worktree, "apply", "--binary", self.patch])
        if self.untracked.is_dir():
            shutil.copytree(self.untracked, worktree, dirs_exist_ok=True)


def snapshot_dirty(path: Path, cache: Cache, runner: Runner) -> DirtySnapshot | None:
    """Capture uncommitted changes of a local checkout, strictly read-only.

    Only read commands run against the user's repo, with optional locks disabled
    so git doesn't refresh their index. Nothing is staged or hashed into their
    object store.
    """
    git = find_tool("git")
    ro: list[str | Path] = [git, "--no-optional-locks", "-C", path]
    diff = runner.run([*ro, "diff", "--binary", "HEAD"]).stdout
    others = runner.run([*ro, "ls-files", "-z", "--others", "--exclude-standard"]).stdout
    untracked = sorted(f for f in others.split("\0") if f)
    if not diff and not untracked:
        return None
    h = hashlib.sha256(diff.encode())
    for rel in untracked:
        h.update(b"\0" + rel.encode() + b"\0" + (path / rel).read_bytes())
    digest = h.hexdigest()[:12]
    snap = DirtySnapshot(dir=cache.snapshots / digest, digest=digest)
    if not snap.dir.is_dir():
        tmp = snap.dir.with_name(digest + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        (tmp / "tracked.patch").write_text(diff)
        for rel in untracked:
            dst = tmp / "untracked" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path / rel, dst)
        tmp.rename(snap.dir)
    return snap


@dataclass(frozen=True)
class ResolvedRef:
    ref: Ref
    mirror: Mirror
    dirty: DirtySnapshot | None = None


def resolve_ref(spec: str, cache: Cache, runner: Runner, url: str = UPSTREAM_ZEPHYR) -> ResolvedRef:
    """Turn a --from/--to spec into a Ref backed by a cache mirror."""
    mirror = Mirror(url, cache, runner)
    p = Path(spec).expanduser()
    if p.exists():
        if not p.is_dir():
            raise ZmigError(f"{spec} is a file; expected a Zephyr checkout directory")
        if not (p / ".git").exists():
            raise ZmigError(
                f"{spec} is not a git checkout. Untracked trees are only supported by "
                f"`zmig plan` (unrecorded-fork detection); `scan` needs a commit to build."
            )
        sha = mirror.fetch_path(p)
        snap = snapshot_dirty(p, cache, runner)
        ref = Ref(
            spec=spec, sha=sha, kind="path", url=url, dirty_hash=snap.digest if snap else None
        )
        return ResolvedRef(ref, mirror, snap)
    sha, kind = mirror.resolve(spec)
    return ResolvedRef(Ref(spec=spec, sha=sha, kind=kind, url=url), mirror)
