from __future__ import annotations

from pathlib import Path

import pytest

from zmig.cli import main


def test_plan_rejects_non_workspace(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from zmig.cli import _kv

    assert _kv("hal_x=https://example.com/x") == ("hal_x", "https://example.com/x")
    with pytest.raises(Exception, match="NAME=URL"):
        _kv("nope")


def test_out_of_scope_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["verify", "--cache-dir", str(tmp_path)]) == 1
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


def test_readme_lists_no_shipped_subcommand_as_unimplemented() -> None:
    """README's "Not implemented yet" line went stale once watch, API diffing and the
    MCUboot checks shipped (eval/AUDIT.md F15). A subcommand is unimplemented only if its own
    help says so."""
    import argparse
    import re

    from zmig.cli import build_parser

    sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    helps = {a.dest: a.help or "" for a in sub._choices_actions}
    readme = (Path(__file__).parent.parent / "README.md").read_text()
    m = re.search(r"^Not implemented yet.*?(?:\n\n|\Z)", readme, re.M | re.S)
    assert m, "README lost its 'Not implemented yet' paragraph"
    listed = set(re.findall(r"`(\w+)`", m[0]))
    shipped = {c for c, h in helps.items() if "not in the MVP" not in h}
    assert not listed & shipped, (
        f"README calls shipped subcommands unimplemented: {listed & shipped}"
    )
    for feature in ("API/ABI diffing", "MCUboot"):
        assert feature not in m[0]
