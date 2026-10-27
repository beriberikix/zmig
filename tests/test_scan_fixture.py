"""M3 end-to-end: `zmig scan` of the fixture app from v3.6.0 to v3.7.0 (see its README).

Run with: pytest -m build
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from tests.manual_diff import config_diff, dts_diff
from zmig.cli import main

ROOT = Path(__file__).parents[1]
APP = ROOT / "tests" / "fixtures" / "app"
SCHEMA = json.loads((ROOT / "zmig/schema/report-1.json").read_text())
CACHE = Path(os.environ.get("ZMIG_CACHE", Path.home() / ".cache" / "zmig"))

pytestmark = [pytest.mark.build, pytest.mark.network]


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    os.environ.pop("PYTHONPATH", None)
    out = tmp_path_factory.mktemp("scan")
    assert (
        main(
            [
                "scan",
                "--app",
                str(APP),
                "--board",
                "native_sim_64",
                "--from",
                "v3.6.0",
                "--to",
                "v3.7.0",
                "--format",
                "json",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    r: dict[str, Any] = json.loads((out / "report.json").read_text())
    jsonschema.validate(r, SCHEMA)
    r["_out"] = str(out)
    return r


def find(report: dict[str, Any], kind: str, subject: str | None = None) -> dict[str, Any]:
    hits = [
        f
        for f in report["findings"]
        if f["kind"] == kind and (subject is None or f["subject"] == subject)
    ]
    assert len(hits) == 1, (
        kind,
        subject,
        [f["subject"] for f in report["findings"] if f["kind"] == kind],
    )
    return hits[0]


def lines(f: dict[str, Any]) -> list[tuple[str, int]]:
    return [(e["path"], e["line"]) for e in f["evidence"] if e["type"] == "file_line"]


def test_builds_and_salvage(report: dict[str, Any]) -> None:
    assert report["inputs"]["board"] == {
        "from": "native_sim_64",
        "to": "native_sim/native/64",
        "mapping": "deprecated.cmake",
        "listed_in": "v3.7.0:boards/deprecated.cmake:442",
    }
    old, raw, salvaged = report["contexts"]
    assert (old["status"], raw["status"]) == ("ok", "configure-failed")
    # The salvaged configure succeeds; compiling then fails on DT_PROP(sample_point),
    # which the devicetree analyzer predicts below.
    assert salvaged["status"] == "build-failed"
    assert [(e["file"], e["line"]) for e in salvaged["salvage_edits"]] == [("prj.conf", 3)]
    log = (CACHE / "builds" / f"{salvaged['key']}.log").read_text()
    assert "DT_N_S_can_P_sample_point" in log


def test_expected_findings(report: dict[str, Any]) -> None:
    f = find(report, "board-renamed", "native_sim_64")
    assert f["severity"] == "risky" and f["new"] == "native_sim/native/64"

    f = find(report, "symbol-removed", "CONFIG_CPLUSPLUS")
    assert f["severity"] == "breaking" and lines(f) == [("prj.conf", 3)]
    assert f["new"]["candidates"][0]["symbol"] == "CPP"
    assert any(
        e["type"] == "tool_output" and "undefined symbol CPLUSPLUS" in e["excerpt"]
        for e in f["evidence"]
    )

    f = find(report, "read-value-changed", "CONFIG_BOARD")
    assert (f["old"], f["new"]) == ('"native_sim_64"', '"native_sim"')

    f = find(report, "node-gone", "DT_PATH(gpio_800)")
    assert lines(f)[0] == ("src/main.c", 4) and (f["old"], f["new"]) == ("/gpio@800", "/gpio_emul")
    f = find(report, "read-property-gone")
    assert lines(f) == [("src/main.c", 13)] and f["old"] == 875
    f = find(report, "property-deprecated", "/can:bus-speed")
    assert lines(f) == [("boards/native_sim_64.overlay", 2)]

    top = report["summary"]["top"]
    assert len(top) == 5 and all(
        next(x for x in report["findings"] if x["id"] == i)["severity"] != "info" for i in top
    )


def test_diffs_match_manual_diffs(report: dict[str, Any]) -> None:
    old = CACHE / "builds" / report["contexts"][0]["key"]
    new = CACHE / "builds" / report["contexts"][2]["key"]

    covered: set[str] = set()
    for f in report["findings"]:
        if f["category"] != "kconfig" or f["kind"] == "salvaged":
            continue
        if f["kind"] in ("default-changed", "newly-set", "no-longer-set"):
            covered |= set(f["new"])
        else:
            covered.add(f["subject"].removeprefix("CONFIG_"))
    assert covered == config_diff(old / "zephyr/.config", new / "zephyr/.config")

    dcov: set[str] = set()
    for f in report["findings"]:
        if f["category"] != "devicetree":
            continue
        if f["kind"] == "other-nodes":
            for k in ("changed", "removed", "added"):
                dcov |= set(f["new"][k])
        elif f["kind"] in ("node-gone", "node-moved"):
            dcov |= {f["old"], f["new"]} - {None}
        elif f["kind"] == "node-changed":
            dcov.add(f["subject"].split(":")[-1])
        elif f["kind"] in ("read-property-gone", "property-deprecated"):
            dcov.add(f["subject"].split(":")[0])
    assert dcov == dts_diff(old / "zephyr/zephyr.dts", new / "zephyr/zephyr.dts")


def test_deterministic(report: dict[str, Any], tmp_path: Path) -> None:
    assert (
        main(
            [
                "scan",
                "--app",
                str(APP),
                "--board",
                "native_sim_64",
                "--from",
                "v3.6.0",
                "--to",
                "v3.7.0",
                "--format",
                "json",
                "--out",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert (tmp_path / "report.json").read_bytes() == (
        Path(report["_out"]) / "report.json"
    ).read_bytes()


def test_large_gap_v442(tmp_path: Path) -> None:
    """Regression: the board alias is gone at v4.4.2 (last listed in v4.0.0), and
    v4.4.2's kconfiglib uses different tuple shapes."""
    os.environ.pop("PYTHONPATH", None)
    assert (
        main(
            [
                "scan",
                "--app",
                str(APP),
                "--board",
                "native_sim_64",
                "--from",
                "v3.6.0",
                "--to",
                "v4.4.2",
                "--format",
                "json",
                "--out",
                str(tmp_path),
            ]
        )
        == 0
    )
    r = json.loads((tmp_path / "report.json").read_text())
    jsonschema.validate(r, SCHEMA)
    assert {a["name"]: a["status"] for a in r["analyzers"]} == {
        "board": "ok",
        "buildlog": "ok",
        "devicetree": "ok",
        "kconfig": "ok",
    }
    assert r["inputs"]["board"]["listed_in"].startswith("v4.0.0:")
    f = find(r, "board-renamed", "native_sim_64")
    assert f["severity"] == "breaking"
    assert find(r, "symbol-removed", "CONFIG_CPLUSPLUS")["new"]["candidates"][0]["symbol"] == "CPP"
    assert lines(find(r, "node-gone", "DT_PATH(gpio_800)"))[0] == ("src/main.c", 4)
