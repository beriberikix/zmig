"""ASSISTANT.md: rendering, drift against the schema, and where _emit writes it."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pytest

from zmig.cli import _emit
from zmig.findings import SCHEMA_VERSION, Report
from zmig.report.assistant import ASSISTANT_MD, FIELDS, POINTER, render_assistant
from zmig.tools import Runner

SCHEMA = json.loads((Path(__file__).parents[1] / "zmig/schema/report-1.json").read_text())
REF = {"spec": "v3.6.0", "sha": "a" * 40}


def report(command: str = "scan") -> Report:
    return Report(
        command=command,
        zmig_version="9.8.7",
        inputs={"from": REF, "to": REF},
        environment={"python": "3"},
    )


def resolve(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        node = schema["$defs"][node["$ref"].rsplit("/", 1)[1]]
    return node


def schema_node(path: str) -> dict[str, Any] | None:
    """The schema node for a dotted path; `x[]` steps into an array's items."""
    node: dict[str, Any] = SCHEMA
    for part in path.split("."):
        is_array = part.endswith("[]")
        props = resolve(SCHEMA, node).get("properties", {})
        if part.removesuffix("[]") not in props:
            return None
        node = resolve(SCHEMA, props[part.removesuffix("[]")])
        if is_array:
            node = resolve(SCHEMA, node["items"])
    return node


def test_render_fills_versions() -> None:
    text = render_assistant(report())
    assert "zmig 9.8.7" in text
    assert f"schema {SCHEMA_VERSION}" in text
    assert not re.search(r"\{[a-z_]+\}", text), "unfilled placeholder"


@pytest.mark.parametrize("path", FIELDS)
def test_field_exists_in_schema(path: str) -> None:
    assert schema_node(path) is not None, path


@pytest.mark.parametrize("path", FIELDS)
def test_field_named_in_text(path: str) -> None:
    shown = path.removeprefix("findings[].")
    assert f"`{shown}`" in ASSISTANT_MD, shown


def test_text_has_no_paths_or_per_run_values() -> None:
    text = render_assistant(report())
    assert "/home/" not in text
    assert "--app " not in text
    assert "--board" not in text


def emit(tmp_path: Path, command: str, out: bool) -> Path:
    dest = tmp_path / "out"
    if out:
        (dest / "logs").mkdir(parents=True)
    args = argparse.Namespace(out=dest if out else None, format="json")
    _emit(report(command), Runner(), args)
    return dest


def test_emit_with_out_writes_assistant_and_pointer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = emit(tmp_path, "scan", out=True)
    assert (dest / "ASSISTANT.md").read_text() == render_assistant(report())
    migration = (dest / "MIGRATION.md").read_text().splitlines()
    assert migration[0] == "# Zephyr migration report"
    assert migration[2] == POINTER
    assert POINTER in capsys.readouterr().err


def test_emit_without_out_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    emit(tmp_path, "scan", out=False)
    assert not (tmp_path / "out").exists()
    assert POINTER not in capsys.readouterr().err


def test_emit_for_plan_writes_no_assistant(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = emit(tmp_path, "plan", out=True)
    assert not (dest / "ASSISTANT.md").exists()
    assert POINTER not in (dest / "MIGRATION.md").read_text()
    assert POINTER not in capsys.readouterr().err
