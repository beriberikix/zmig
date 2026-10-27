from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from zmig.cache import Cache
from zmig.tools import Runner


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_AUTHOR_DATE": "2024-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2024-01-01T00:00:00Z",
            "HOME": str(cwd),
            "PATH": "/usr/bin:/bin",
        },
    ).stdout.strip()


def tree_digest(root: Path) -> str:
    """Hash every file (path, bytes, mtime) under root, to prove nothing was written."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            h.update(f"{p.relative_to(root)}\0{st.st_mtime_ns}\0".encode() + p.read_bytes())
    return h.hexdigest()


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    """A tiny 'upstream' repo with two tagged releases, a branch, and a west.yml."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "west.yml").write_text("manifest:\n  projects: []\n  self:\n    path: zephyr\n")
    (repo / "VERSION").write_text("1.0\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "release 1.0")
    git(repo, "tag", "v1.0.0")
    (repo / "VERSION").write_text("2.0\n")
    git(repo, "commit", "-q", "-am", "release 2.0")
    git(repo, "tag", "-a", "v2.0.0", "-m", "annotated")
    git(repo, "branch", "feature")
    return repo


@pytest.fixture
def cache(tmp_path: Path) -> Cache:
    c = Cache(tmp_path / "cache")
    c.ensure()
    return c


@pytest.fixture
def runner(tmp_path: Path) -> Runner:
    return Runner(log_dir=tmp_path / "logs")
