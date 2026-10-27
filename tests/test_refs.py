from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import git, tree_digest
from zmig.cache import Cache
from zmig.errors import RefNotFound, ZmigError
from zmig.refs import Mirror, resolve_ref, url_slug
from zmig.tools import Runner


def test_url_slug() -> None:
    assert url_slug("https://github.com/zephyrproject-rtos/zephyr") == (
        "github.com_zephyrproject-rtos_zephyr"
    )
    assert url_slug("git@github.com:nrfconnect/sdk-zephyr.git") == (
        "github.com_nrfconnect_sdk-zephyr"
    )


def test_resolve_tag_branch_sha(upstream: Path, cache: Cache, runner: Runner) -> None:
    url = upstream.as_uri()
    v1 = git(upstream, "rev-parse", "v1.0.0")
    v2 = git(upstream, "rev-parse", "v2.0.0^{commit}")

    r = resolve_ref("v1.0.0", cache, runner, url=url).ref
    assert (r.sha, r.kind, r.dirty) == (v1, "tag", False)
    # Annotated tags resolve to the commit, not the tag object.
    assert resolve_ref("v2.0.0", cache, runner, url=url).ref.sha == v2
    assert resolve_ref("feature", cache, runner, url=url).ref.kind == "branch"
    r = resolve_ref(v1[:10], cache, runner, url=url).ref
    assert (r.sha, r.kind) == (v1, "sha")
    with pytest.raises(RefNotFound):
        resolve_ref("v9.9.9", cache, runner, url=url)


def test_new_upstream_tag_triggers_fetch(upstream: Path, cache: Cache, runner: Runner) -> None:
    url = upstream.as_uri()
    resolve_ref("v1.0.0", cache, runner, url=url)
    (upstream / "VERSION").write_text("3.0\n")
    git(upstream, "commit", "-q", "-am", "release 3.0")
    git(upstream, "tag", "v3.0.0")
    assert resolve_ref("v3.0.0", cache, runner, url=url).ref.sha == git(
        upstream, "rev-parse", "v3.0.0"
    )


def test_checkout_is_isolated_and_reused(upstream: Path, cache: Cache, runner: Runner) -> None:
    rr = resolve_ref("v1.0.0", cache, runner, url=upstream.as_uri())
    dest = cache.workspaces / rr.ref.key / "zephyr"
    rr.mirror.checkout(rr.ref, dest)
    assert (dest / "VERSION").read_text() == "1.0\n"
    n = len(runner.invocations)
    rr.mirror.checkout(rr.ref, dest)
    assert len(runner.invocations) == n, "second checkout should be a cache hit"
    # The worktree belongs to the mirror, not to the upstream repo.
    assert not (upstream / ".git" / "worktrees").exists()


def test_path_ref_dirty_snapshot_is_read_only(
    upstream: Path, tmp_path: Path, cache: Cache, runner: Runner
) -> None:
    user = tmp_path / "user"
    git(tmp_path, "clone", "-q", str(upstream), str(user))
    (user / "VERSION").write_text("2.0-local\n")
    (user / "NEW.txt").write_text("untracked\n")
    before = tree_digest(user)

    rr = resolve_ref(str(user), cache, runner, url=upstream.as_uri())
    assert rr.ref.kind == "path" and rr.ref.dirty
    assert tree_digest(user) == before, "zmig wrote to the user's checkout or .git"

    dest = cache.workspaces / rr.ref.key / "zephyr"
    rr.mirror.checkout(rr.ref, dest, rr.dirty)
    assert (dest / "VERSION").read_text() == "2.0-local\n"
    assert (dest / "NEW.txt").read_text() == "untracked\n"


def test_path_ref_clean(upstream: Path, tmp_path: Path, cache: Cache, runner: Runner) -> None:
    user = tmp_path / "user"
    git(tmp_path, "clone", "-q", str(upstream), str(user))
    (user / "VERSION").write_text("local\n")
    git(user, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "-am", "local")
    rr = resolve_ref(str(user), cache, runner, url=upstream.as_uri())
    assert not rr.ref.dirty
    assert rr.ref.sha == git(user, "rev-parse", "HEAD")
    assert Mirror(upstream.as_uri(), cache, runner).resolve(rr.ref.sha)[0] == rr.ref.sha


def test_untracked_tree_rejected_for_scan(
    tmp_path: Path, cache: Cache, runner: Runner, upstream: Path
) -> None:
    tree = tmp_path / "copy"
    tree.mkdir()
    with pytest.raises(ZmigError, match="not a git checkout"):
        resolve_ref(str(tree), cache, runner, url=upstream.as_uri())
