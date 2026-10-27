from __future__ import annotations

from pathlib import Path

import pytest

from zmig.errors import ToolFailed, ToolMissing
from zmig.tools import Runner, find_tool


def test_runner_logs_and_redacts(tmp_path: Path) -> None:
    r = Runner(log_dir=tmp_path / "logs", redactions={str(tmp_path): "$TMP"})
    r.run(["echo", str(tmp_path / "x")], env={"ZMIG_TEST": "1"})
    inv = r.invocations[0]
    assert inv.id == "cmd-001" and inv.exit == 0
    assert inv.argv == ["echo", "$TMP/x"]
    text = (tmp_path / "logs" / "cmd-001.log").read_text()
    assert "ZMIG_TEST=1" in text and str(tmp_path / "x") in text


def test_runner_failure_is_actionable(tmp_path: Path) -> None:
    r = Runner(log_dir=tmp_path / "logs")
    with pytest.raises(ToolFailed, match=r"exit 3.*cmd-001\.log"):
        r.run(["sh", "-c", "echo boom >&2; exit 3"])
    assert r.run(["false"], check=False).returncode == 1


def test_find_tool_missing_has_hint() -> None:
    with pytest.raises(ToolMissing, match="not found"):
        find_tool("definitely-not-a-real-tool-zmig")
    with pytest.raises(ToolMissing, match="apt install device-tree-compiler"):
        find_tool("dtc", explicit="/nonexistent/dtc")
