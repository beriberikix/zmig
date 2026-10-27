"""Fork classification on a tiny real repository: one commit per outcome."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import git
from zmig.cache import Cache
from zmig.errors import ZmigError
from zmig.forks import blob_sha, classify, compare_tree, guess_version_tag, normalize_subject
from zmig.refs import Mirror
from zmig.tools import Runner


def write(repo: Path, rel: str, text: str) -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text)


def commit(repo: Path, msg: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


LONG = "".join(f"line {i}\n" for i in range(60))


@pytest.fixture
def forked(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    up = tmp_path / "up"
    up.mkdir()
    git(up, "init", "-q", "-b", "main")
    write(up, "a.txt", "alpha\n")
    write(up, "c.txt", "setting = 1\n")
    write(up, "e.txt", LONG)
    commit(up, "initial")
    git(up, "tag", "v1")
    u1 = commit_after(up, "a.txt", "alpha fixed\n", "core: fix the alpha value")
    u2 = commit_after(up, "b.txt", "widget\n", "widget: fix the parser bug")
    commit_after(up, "c.txt", "setting = 2\n", "config: bump setting")
    commit_after(up, "e.txt", "header\n" + LONG, "e: add header")
    git(up, "tag", "v2")

    fork = tmp_path / "fork"
    git(tmp_path, "clone", "-q", str(up), str(fork))
    git(fork, "checkout", "-q", "-b", "product", "v1")
    l1 = cherry(fork, u1)
    write(fork, "b.txt", "widget, our way\n")
    l2 = commit(fork, "[vendor noup] widget: fix the parser bug")
    l3 = commit_after(fork, "c.txt", "setting = 3\n", "config: our setting")
    l4 = commit_after(fork, "d.txt", "new\n", "add our file d")
    l5 = commit_after(fork, "e.txt", LONG + "footer\n", "e: add footer")
    return up, fork, {"l1": l1, "l2": l2, "l3": l3, "l4": l4, "l5": l5, "u1": u1, "u2": u2}


def commit_after(repo: Path, rel: str, text: str, msg: str) -> str:
    write(repo, rel, text)
    return commit(repo, msg)


def cherry(repo: Path, sha: str) -> str:
    git(repo, "cherry-pick", "-x", sha)
    return git(repo, "rev-parse", "HEAD")


def test_classify_each_outcome(
    forked: tuple[Path, Path, dict[str, str]], cache: Cache, runner: Runner
) -> None:
    up, fork, c = forked
    m = Mirror(up.as_uri(), cache, runner)
    m.ensure()
    head = m.fetch_path(fork)
    res = classify(m, head, m.resolve("v1")[0], m.resolve("v2")[0], frozenset({c["l3"]}))
    by = {r.sha: r for r in res.commits}
    assert [r.sha for r in res.commits] == [c[k] for k in ("l1", "l2", "l3", "l4", "l5")]
    assert (by[c["l1"]].state, by[c["l1"]].match) == ("upstreamed", "patch-id")
    assert by[c["l1"]].upstream is not None and by[c["l1"]].upstream[0] == c["u1"]
    assert (by[c["l2"]].state, by[c["l2"]].match) == ("upstreamed", "subject")
    # Kept instead of dropped, the subject-only match would conflict (add/add of b.txt).
    assert by[c["l2"]].conflict_files == ("b.txt",)
    assert by[c["l2"]].upstream is not None and by[c["l2"]].upstream[0] == c["u2"]
    assert by[c["l3"]].state == "conflicts" and by[c["l3"]].conflict_files == ("c.txt",)
    assert by[c["l3"]].unpushed and not by[c["l4"]].unpushed
    assert by[c["l4"]].state == "applies" and by[c["l4"]].overlap == ()
    assert by[c["l5"]].state == "applies" and by[c["l5"]].overlap == ("e.txt",)


def test_classification_agrees_with_git_rebase(
    forked: tuple[Path, Path, dict[str, str]], tmp_path: Path, cache: Cache, runner: Runner
) -> None:
    """Oracle: a real rebase drops patch-id matches and stops exactly on conflicts."""
    up, fork, _ = forked
    m = Mirror(up.as_uri(), cache, runner)
    m.ensure()
    res = classify(m, m.fetch_path(fork), m.resolve("v1")[0], m.resolve("v2")[0])
    # A rebase also stops on subject-only matches whose content conflicts.
    expected_conflicts = {r.sha for r in res.commits if r.conflict_files}
    expected_dropped = {r.sha for r in res.commits if r.match == "patch-id"}

    scratch = tmp_path / "rebase"
    git(tmp_path, "clone", "-q", str(fork), str(scratch))
    git(scratch, "checkout", "-q", "product")
    conflicts, dropped = set(), set()
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(scratch),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@e",
            "rebase",
            "--onto",
            "origin/main",
            "v1",
        ],
        capture_output=True,
        text=True,
    )
    out = proc.stdout + proc.stderr
    while True:
        dropped |= {ln.split()[1] for ln in out.splitlines() if ln.startswith("dropping ")}
        stopped = scratch / ".git" / "rebase-merge" / "stopped-sha"
        if not stopped.exists():
            break
        conflicts.add(git(scratch, "rev-parse", stopped.read_text().strip()))
        proc = subprocess.run(
            ["git", "-C", str(scratch), "rebase", "--skip"], capture_output=True, text=True
        )
        out = proc.stdout + proc.stderr
    assert conflicts == expected_conflicts
    assert {git(scratch, "rev-parse", d) for d in dropped} == expected_dropped


def test_unrelated_history(tmp_path: Path, upstream: Path, cache: Cache, runner: Runner) -> None:
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    write(other, "x", "x\n")
    commit(other, "unrelated")
    m = Mirror(upstream.as_uri(), cache, runner)
    m.ensure()
    head = m.fetch_path(other)
    with pytest.raises(ZmigError, match="shares no history"):
        classify(m, head, m.resolve("v1.0.0")[0], m.resolve("v2.0.0")[0])


def test_compare_tree(upstream: Path, tmp_path: Path, cache: Cache, runner: Runner) -> None:
    m = Mirror(upstream.as_uri(), cache, runner)
    m.ensure()
    tree = tmp_path / "tree"
    tree.mkdir()
    write(tree, "west.yml", "manifest:\n  projects: []\n  self:\n    path: zephyr\n")
    write(tree, "VERSION", "1.0-patched\n")
    write(tree, "extra.c", "int x;\n")
    write(tree, "build/ignored.o", "junk")
    d = compare_tree(m, m.resolve("v1.0.0")[0], tree)
    assert (d.modified, d.missing, d.added) == (("VERSION",), (), ("extra.c",))
    (tree / "VERSION").write_text("1.0\n")
    (tree / "extra.c").unlink()
    assert compare_tree(m, m.resolve("v1.0.0")[0], tree).clean


def test_blob_sha_matches_git(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_bytes(b"hello\n")
    assert blob_sha(b"hello\n") == git(tmp_path, "hash-object", str(f))


def test_normalize_subject() -> None:
    assert normalize_subject("[nrf fromlist] [noup]  Kernel: Fix  X") == "kernel: fix x"


def test_guess_version_tag(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text(
        "VERSION_MAJOR = 3\nVERSION_MINOR = 6\nPATCHLEVEL = 0\nVERSION_TWEAK = 0\nEXTRAVERSION =\n"
    )
    assert guess_version_tag(tmp_path).tag == "v3.6.0"
    (tmp_path / "VERSION").write_text(
        "VERSION_MAJOR = 4\nVERSION_MINOR = 4\nPATCHLEVEL = 99\nEXTRAVERSION =\n"
    )
    g = guess_version_tag(tmp_path)
    assert g.tag is None and "development snapshot" in g.reason


def test_subject_and_content_signals(tmp_path: Path, cache: Cache, runner: Runner) -> None:
    """Content similarity corroborates or contradicts a subject match, and finds the same
    change under a different subject when patch-id can't (different context)."""
    up = tmp_path / "up2"
    up.mkdir()
    git(up, "init", "-q", "-b", "main")
    write(up, "f.txt", "len = 1\nkeep\n")
    write(up, "h.txt", "ctx1\nctx2\ntimeout = 10\nctx3\nctx4\n")
    write(up, "k.txt", "a\nb\nc\nd\n")
    commit(up, "initial")
    git(up, "tag", "v1")
    u_agree = commit_after(up, "k.txt", "a\nB\nC\nd\n", "lib: fix the k table values")
    u_dis = commit_after(up, "f.txt", "len = 2\nkeep\n", "net: fix the buffer length")
    u_content = commit_after(
        up,
        "h.txt",
        "ctx1\nctx2\ntimeout = 20\nctx3\nctx4\n",
        "drivers: handle the timeout properly",
    )
    git(up, "tag", "v2")

    fork = tmp_path / "fork2"
    git(tmp_path, "clone", "-q", str(up), str(fork))
    git(fork, "checkout", "-q", "-b", "product", "v1")
    # Same subject, 2 of 3 changed lines in common: the signals agree.
    agree = commit_after(fork, "k.txt", "a\nB\nC\nD\n", "lib: fix the k table values")
    # Same subject, unrelated content: the signals disagree.
    dis = commit_after(fork, "g.txt", "something else entirely\n", "net: fix the buffer length")
    # Change the context first, then make upstream's exact +/- change under another
    # subject: patch-id differs (context is hashed), content is identical.
    commit_after(fork, "h.txt", "ctx1\nctx2-local\ntimeout = 10\nctx3\nctx4\n", "local: tweak")
    content = commit_after(
        fork, "h.txt", "ctx1\nctx2-local\ntimeout = 20\nctx3\nctx4\n", "my driver: bump the timeout"
    )

    m = Mirror(up.as_uri(), cache, runner)
    m.ensure()
    res = classify(m, m.fetch_path(fork), m.resolve("v1")[0], m.resolve("v2")[0])
    by = {r.sha: r for r in res.commits}
    a, d, c = by[agree], by[dis], by[content]
    assert (a.match, a.signals, a.upstream and a.upstream[0]) == ("subject", "agree", u_agree)
    assert a.similarity is not None and 0.5 <= a.similarity < 1
    assert (d.match, d.signals, d.similarity) == ("subject", "disagree", 0.0)
    assert d.upstream is not None and d.upstream[0] == u_dis
    assert (c.state, c.match, c.signals, c.similarity) == ("upstreamed", "content", "disagree", 1.0)
    assert c.upstream is not None and c.upstream[0] == u_content
    up_pid = subprocess.run(
        ["git", "-C", str(up), "patch-id", "--stable"],
        input=git(up, "show", u_content) + "\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()[0]
    assert c.patch_id and c.patch_id != up_pid  # patch-id alone would have missed it
