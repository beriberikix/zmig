"""M2 end-to-end: `zmig plan` on the synthetic fork built from real Zephyr history.

Run with: pytest -m network   (first run clones tinycrypt and segger; ~1-2 minutes)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from tests.conftest import tree_digest
from tests.fixtures.make_fork import build as build_fork
from zmig.cache import Cache, default_cache_root
from zmig.cli import main
from zmig.refs import UPSTREAM_ZEPHYR, Mirror
from zmig.tools import Runner

ROOT = Path(__file__).parents[1]
SCHEMA = json.loads((ROOT / "zmig/schema/report-1.json").read_text())

pytestmark = [pytest.mark.network]


@pytest.fixture(autouse=True)
def _no_pythonpath(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)


@pytest.fixture(scope="session")
def mirror() -> Path:
    m = Mirror(UPSTREAM_ZEPHYR, Cache(default_cache_root()), Runner())
    m.ensure()
    return m.path


@pytest.fixture(scope="session")
def fork_ws(tmp_path_factory: pytest.TempPathFactory, mirror: Path) -> tuple[Path, dict[str, Any]]:
    dest = tmp_path_factory.mktemp("fork") / "ws"
    return dest, build_fork(dest, mirror)


def run_plan(out: Path, *args: str) -> dict[str, Any]:
    assert main(["plan", "--format", "json", "--out", str(out), *args]) == 0
    report: dict[str, Any] = json.loads((out / "report.json").read_text())
    jsonschema.validate(report, SCHEMA)
    return report


def by_subject(report: dict[str, Any], category: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for f in report["findings"]:
        if f["category"] == category:
            out.setdefault(f"{f['kind']}|{f['subject']}", f)
    return out


def test_fork_classification(fork_ws: tuple[Path, dict[str, Any]], tmp_path: Path) -> None:
    ws, expected = fork_ws
    before = tree_digest(ws)
    report = run_plan(tmp_path, "--from", "v3.6.0", "--to", "v3.7.0", "--workspace", str(ws))
    assert tree_digest(ws) == before, "plan wrote to the user's workspace"

    commits = {
        f["subject"].split(":", 1)[1]: f
        for f in report["findings"]
        if f["category"] == "fork" and f["subject"].startswith("zephyr:")
    }
    assert set(commits) == set(expected["zephyr"])
    for sha, exp in expected["zephyr"].items():
        got = commits[sha]["new"]
        assert got["state"] == exp["state"], sha
        assert got["provenance"] == exp["provenance"], sha
        if "match" in exp:
            assert got["upstream"] == exp["upstream"]
            assert commits[sha]["confidence"] == (
                "high" if exp["match"] == "patch-id" else "medium"
            )
        if "conflict_files" in exp:
            assert got["conflict_files"] == exp["conflict_files"]
            assert commits[sha]["severity"] == "breaking"
        if "overlap" in exp:
            assert got.get("overlap", []) == exp["overlap"]

    f = by_subject(report, "fork")
    assert f["uncommitted-changes|zephyr"]["new"]["files"] == expected["zephyr_uncommitted"]
    unrec = f["unrecorded-fork|tinycrypt"]
    assert unrec["new"]["commits"] == [expected["tinycrypt"]["local_commit"]]
    seg = f["content-mismatch|segger"]["new"]
    assert (seg["modified"], seg["missing"], seg["added"]) == (
        expected["segger"]["modified"],
        [],
        [],
    )


def test_fork_classification_matches_git_rebase(
    fork_ws: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    """Oracle: rebase the fork's commits onto v3.7.0 with plain git in a scratch clone."""
    ws, expected = fork_ws
    scratch = tmp_path / "z"
    subprocess.run(["git", "clone", "-q", "--shared", str(ws / "zephyr"), str(scratch)], check=True)
    git = ["git", "-C", str(scratch), "-c", "user.name=o", "-c", "user.email=o@o"]
    subprocess.run([*git, "checkout", "-q", "product"], check=True)
    proc = subprocess.run(
        [*git, "rebase", "--onto", "v3.7.0", "v3.6.0"], capture_output=True, text=True
    )
    out, conflicts = proc.stdout + proc.stderr, set()
    dropped = {ln.split()[1] for ln in out.splitlines() if ln.startswith("dropping ")}
    stopped = scratch / ".git" / "rebase-merge" / "stopped-sha"
    while stopped.exists():
        conflicts.add(
            subprocess.run(
                [*git, "rev-parse", stopped.read_text().strip()], capture_output=True, text=True
            ).stdout.strip()
        )
        subprocess.run([*git, "rebase", "--skip"], capture_output=True)
    exp = expected["zephyr"]
    assert dropped == {s for s, e in exp.items() if e.get("match") == "patch-id"}
    assert conflicts == {s for s, e in exp.items() if e["state"] == "conflicts"}


def manual_manifest(mirror: Path, ref: str) -> dict[str, tuple[Any, ...]]:
    """west.yml + submanifests/*.yaml parsed with plain YAML: name -> (url, rev, path)."""

    def show(path: str) -> str:
        return subprocess.run(
            ["git", "--git-dir", str(mirror), "show", f"{ref}:{path}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    files = ["west.yml"] + [
        "submanifests/" + n
        for n in subprocess.run(
            ["git", "--git-dir", str(mirror), "ls-tree", "--name-only", f"{ref}:submanifests"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        if n.endswith((".yml", ".yaml"))
    ]
    remotes: dict[str, str] = {}
    default_remote = None
    projects: dict[str, tuple[Any, ...]] = {}
    for f in files:
        m = yaml.safe_load(show(f))["manifest"]
        remotes.update({r["name"]: r["url-base"] for r in m.get("remotes", [])})
        default_remote = m.get("defaults", {}).get("remote", default_remote)
        for p in m.get("projects", []):
            url = (
                p.get("url")
                or f"{remotes[p.get('remote', default_remote)]}/{p.get('repo-path', p['name'])}"
            )
            projects[p["name"]] = (
                url,
                p.get("revision", "main"),
                p.get("path", p["name"]),
                tuple(p.get("groups", [])),
            )
    return projects


@pytest.mark.parametrize("to", ["v3.7.0", "v4.4.2"])
def test_manifest_diff_matches_manual_diff(mirror: Path, tmp_path: Path, to: str) -> None:
    report = run_plan(tmp_path, "--from", "v3.6.0", "--to", to, "--analyzers", "manifest")
    old, new = manual_manifest(mirror, "v3.6.0"), manual_manifest(mirror, to)
    want = {
        "project-added": set(new) - set(old),
        "project-removed": set(old) - set(new),
        "project-changed": {n for n in set(old) & set(new) if old[n] != new[n]},
    }
    got: dict[str, set[str]] = {k: set() for k in want}
    for f in report["findings"]:
        got[f["kind"]].add(f["subject"])
    assert got == want


def test_untracked_from_tree(mirror: Path, tmp_path: Path) -> None:
    tree = tmp_path / "zephyr-copy"
    tree.mkdir()
    archive = subprocess.run(
        ["git", "--git-dir", str(mirror), "archive", "v3.6.0"], capture_output=True, check=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)
    (tree / "kernel" / "sched.c").write_text("/* patched */\n")
    report = run_plan(
        tmp_path / "out", "--from", str(tree), "--to", "v3.7.0", "--analyzers", "hops"
    )
    assert report["inputs"]["from"]["spec"] == "v3.6.0"
    mism = [f for f in report["findings"] if f["kind"] == "content-mismatch"]
    assert len(mism) == 1 and mism[0]["new"]["modified"] == ["kernel/sched.c"]
    assert mism[0]["confidence"] == "medium"
