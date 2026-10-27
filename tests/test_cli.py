from __future__ import annotations

from pathlib import Path

import pytest

from zmig.cli import main


def test_plan_not_yet(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["plan", "--from", "v1", "--to", "v2", "--cache-dir", str(tmp_path)]) == 1
    assert "M2" in capsys.readouterr().err


def test_out_of_scope_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["watch", "--cache-dir", str(tmp_path)]) == 1
    assert "outside the MVP" in capsys.readouterr().err


def test_scan_rejects_non_app(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            "scan",
            "--app",
            str(tmp_path),
            "--board",
            "native_sim",
            "--from",
            "v1",
            "--to",
            "v2",
            "--cache-dir",
            str(tmp_path / "c"),
        ]
    )
    assert rc == 1 and "not a Zephyr application" in capsys.readouterr().err


def test_unknown_analyzer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    app = Path(__file__).parent / "fixtures" / "app"
    rc = main(
        [
            "scan",
            "--app",
            str(app),
            "--board",
            "native_sim",
            "--from",
            "v1",
            "--to",
            "v2",
            "--analyzers",
            "bogus",
            "--cache-dir",
            str(tmp_path),
        ]
    )
    assert rc == 1 and "unknown analyzer" in capsys.readouterr().err
