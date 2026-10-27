"""M1 end-to-end: real native_sim builds of the fixture app at two Zephyr releases.

Run with: pytest -m build   (needs network on first run, host gcc with -m32)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import jsonschema
import pytest

from zmig.cli import main

ROOT = Path(__file__).parents[1]
APP = ROOT / "tests" / "fixtures" / "hello"
SCHEMA = json.loads((ROOT / "zmig/schema/report-1.json").read_text())

pytestmark = [pytest.mark.build, pytest.mark.network]


def scan(out: Path, *extra: str) -> dict[str, object]:
    # Shares the user's real cache so the multi-GB clone and builds are reused.
    rc = main(
        [
            "scan",
            "--app",
            str(APP),
            "--from",
            "v3.6.0",
            "--to",
            "v3.7.0",
            "--analyzers",
            "none",
            "--format",
            "json",
            "--out",
            str(out),
            *extra,
        ]
    )
    assert rc == 0
    report: dict[str, object] = json.loads((out / "report.json").read_text())
    jsonschema.validate(report, SCHEMA)
    return report


@pytest.fixture(autouse=True)
def _no_pythonpath(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)


def test_full_build_both_versions_and_cache(tmp_path: Path) -> None:
    r1 = scan(tmp_path / "a", "--board", "native_sim")
    assert [c["status"] for c in r1["contexts"]] == ["ok", "ok"]  # type: ignore[index]
    keys = [c["key"] for c in r1["contexts"]]  # type: ignore[index]
    cache = Path(os.environ.get("ZMIG_CACHE", Path.home() / ".cache" / "zmig"))
    for k in keys:
        assert (cache / "builds" / k / "zephyr" / "zephyr.exe").is_file()

    scan(tmp_path / "b", "--board", "native_sim")
    inv = json.loads((tmp_path / "b" / "logs" / "invocations.json").read_text())
    assert not any("build" in i["argv"] for i in inv), "second run should reuse cached builds"
    assert (tmp_path / "a" / "report.json").read_bytes() == (
        tmp_path / "b" / "report.json"
    ).read_bytes()


def test_fast_mode_configures_only(tmp_path: Path) -> None:
    r = scan(tmp_path, "--board", "native_sim", "--fast")
    cache = Path(os.environ.get("ZMIG_CACHE", Path.home() / ".cache" / "zmig"))
    for c in r["contexts"]:  # type: ignore[attr-defined]
        assert c["status"] == "ok" and c["mode"] == "fast"
        assert (cache / "builds" / c["key"] / "zephyr" / ".config").is_file()
        assert not (cache / "builds" / c["key"] / "zephyr" / "zephyr.exe").exists()


def test_modules_are_exactly_the_requested_set(tmp_path: Path) -> None:
    """Regression: an earlier run that cloned a module must not leak it into later builds
    (and the cache key must change with the module set)."""
    cache = Path(os.environ.get("ZMIG_CACHE", Path.home() / ".cache" / "zmig"))
    with_mod = scan(tmp_path / "m", "--board", "native_sim", "--fast", "--module", "cmsis")
    without = scan(tmp_path / "n", "--board", "native_sim", "--fast")
    for ctx in with_mod["contexts"]:  # type: ignore[attr-defined]
        text = (cache / "builds" / ctx["key"] / "zephyr_modules.txt").read_text()
        assert text.startswith('"cmsis":') and text.count("\n") == 1
    for ctx in without["contexts"]:  # type: ignore[attr-defined]
        assert (cache / "builds" / ctx["key"] / "zephyr_modules.txt").read_text() == ""
    assert {c["key"] for c in with_mod["contexts"]}.isdisjoint(  # type: ignore[attr-defined]
        c["key"]
        for c in without["contexts"]  # type: ignore[attr-defined]
    )
