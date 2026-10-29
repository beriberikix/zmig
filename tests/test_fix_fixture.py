"""`zmig fix kconfig --apply` end to end on a git copy of the fixture app (v3.6.0 to
v3.7.0): the one Kconfig-evidenced rename is committed, the target is rebuilt, nothing is
introduced, and a second run changes nothing.

Run with: pytest -m build
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from zmig.cli import main

APP = Path(__file__).parent / "fixtures" / "app"
pytestmark = [pytest.mark.build, pytest.mark.network]
IDENT = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(root: Path, *args: str) -> str:
    env = {**os.environ, **IDENT}
    return subprocess.run(
        ["git", "-C", str(root), *args], env=env, check=True, capture_output=True, text=True
    ).stdout


def test_apply_commits_the_rename_rebuilds_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for k, v in IDENT.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    root = tmp_path / "proj"
    shutil.copytree(APP, root / "app")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "fixture app")
    argv = [
        "fix",
        "kconfig",
        "--app",
        str(root / "app"),
        "--board",
        "native_sim_64",
        "--module",
        "zcbor",
        "--from",
        "v3.6.0",
        "--to",
        "v3.7.0",
        "--apply",
    ]
    out = tmp_path / "out"
    assert main([*argv, "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "app/prj.conf:3  CONFIG_CPLUSPLUS=y -> CONFIG_CPP" in text
    assert "CPLUSPLUS is deprecated and selects CPP" in text
    # The guide-only, inverted rename is reported, not edited.
    assert "CONFIG_CAN_AUTO_BUS_OFF_RECOVERY=n" in text and "'inverted'" in text
    assert _git(root, "branch", "--show-current").strip() == "zmig/fix-kconfig-v3.7.0"
    assert _git(root, "log", "--format=%s", "main..HEAD").splitlines() == [
        "zmig fix kconfig: rename (deprecation select)"
    ]
    assert _git(root, "status", "--porcelain") == ""
    prj = (root / "app" / "prj.conf").read_text()
    assert "CONFIG_CPP=y" in prj and "CONFIG_CPLUSPLUS" not in prj
    assert "CONFIG_CAN_AUTO_BUS_OFF_RECOVERY=n" in prj
    fix = json.loads((out / "fix.json").read_text())
    assert fix["introduced"] == [] and len(fix["fixed"]) >= 1

    head = _git(root, "rev-parse", "HEAD")
    assert main(argv) == 0
    again = capsys.readouterr().out
    assert "Fix: rename" not in again and "Committed" not in again
    assert _git(root, "rev-parse", "HEAD") == head
    assert _git(root, "branch", "--list").count("\n") == 2  # main and the fix branch
