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
                "--module",
                "zcbor",
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
    assert (old["status"], raw["status"], salvaged["status"]) == ("ok", "configure-failed", "ok")
    assert [(e["file"], e["line"]) for e in salvaged["salvage_edits"]] == [
        ("prj.conf", 3),
        ("prj.conf", 5),
    ]


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
    assert lines(f)[0] == ("src/main.c", 6) and (f["old"], f["new"]) == ("/gpio@800", "/gpio_emul")
    f = find(report, "read-property-gone")
    assert lines(f) == [("src/main.c", 17)] and f["old"] == 875
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
    manual = config_diff(old / "zephyr/.config", new / "zephyr/.config")
    assert manual <= covered
    # Beyond value changes, zmig reports symbols the app sets that no longer exist, even
    # when the value didn't change (=n before, absent now).
    removed = {
        f["subject"].removeprefix("CONFIG_")
        for f in report["findings"]
        if f["kind"] == "symbol-removed"
    }
    assert covered - manual <= removed

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
                "--module",
                "zcbor",
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
        "initorder": "ok",
        "devicetree": "ok",
        "crossimage": "ok",
        "guide": "ok",
        "images": "ok",
        "kconfig": "ok",
        "sbom": "ok",
        "sysbuild": "ok",
        "layout": "ok",
        "mcuboot": "ok",
        "storage": "ok",
        "ble": "ok",
        "wire": "ok",
    }
    # A plain native_sim build: the verdict is computed and every category is decided.
    assert {c["status"] for c in r["upgrade_path"]["categories"].values()} <= {
        "clean",
        "hazard",
        "not-applicable",
    }
    assert r["inputs"]["board"]["listed_in"].startswith("v4.0.0:")
    f = find(r, "board-renamed", "native_sim_64")
    assert f["severity"] == "breaking"
    assert find(r, "symbol-removed", "CONFIG_CPLUSPLUS")["new"]["candidates"][0]["symbol"] == "CPP"
    assert lines(find(r, "node-gone", "DT_PATH(gpio_800)"))[0] == ("src/main.c", 6)


def test_guide_matches_point_at_real_lines(report: dict[str, Any]) -> None:
    """Every guide match cites a real line of the 3.7 guide that names the item, and a real
    app line that uses it."""
    guide = {f["subject"]: f for f in report["findings"] if f["kind"] == "entry-match"}
    assert set(guide) == {
        "MG-3.7-0004:header:version.h",
        "MG-3.7-0033:literal:bus-speed",
        "MG-3.7-0033:literal:sample-point",
        "MG-3.7-0034:ident:can_recover",
        "MG-3.7-0034:kconfig:CAN_AUTO_BUS_OFF_RECOVERY",
    }
    doc_lines = (CACHE / "mirrors").glob("*zephyr.git")
    mirror = next(doc_lines)
    import subprocess

    text = subprocess.run(
        ["git", "--git-dir", str(mirror), "show", "v3.7.0:doc/releases/migration-guide-3.7.rst"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for subject, f in guide.items():
        value = subject.split(":", 2)[2]
        docs = [e for e in f["evidence"] if e["type"] == "doc_entry"]
        assert docs and all(value in text[e["line"] - 1] for e in docs), subject
        token = value.replace("-", "_") if f["new"]["item"]["kind"] == "literal" else value
        for path, line in lines(f):
            src = (APP / path).read_text().splitlines()[line - 1]
            assert value in src or token in src, (subject, path, line)

    kc = find(report, "symbol-removed", "CONFIG_CAN_AUTO_BUS_OFF_RECOVERY")
    assert kc["new"]["candidates"][0]["symbol"] == "CAN_MANUAL_RECOVERY_MODE"
    assert guide["MG-3.7-0034:kconfig:CAN_AUTO_BUS_OFF_RECOVERY"]["id"] in kc["related"]
    assert (
        find(report, "read-property-gone")["id"]
        in (guide["MG-3.7-0033:literal:sample-point"]["related"])
    )
    assert guide["MG-3.7-0033:literal:bus-speed"]["severity"] == "risky"


def test_guide_gating_large_gap(tmp_path: Path) -> None:
    """Guide entries that match only incidentally are downgraded using build facts."""
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
    # hawkBit entry mentions CONFIG_BOARD (which main.c reads), gated on a hawkBit option.
    f = find(r, "entry-match", "MG-4.2-0140:kconfig:BOARD")
    assert (f["severity"], f["confidence"]) == ("info", "low")
    assert "CONFIG_HAWKBIT_CUSTOM_DEVICE_ID" in f["action"]
    # CAN filter entry names can0's compatible, but can0 is disabled in both builds.
    f = find(r, "entry-match", "MG-4.4-0032:compat:zephyr,native-linux-can")
    assert (f["severity"], "guide.gated-by-build" in f["heuristics"]) == ("info", True)
    # Real hits keep their severity.
    assert find(r, "entry-match", "MG-3.7-0034:ident:can_recover")["severity"] == "breaking"
    cov = find(r, "coverage", "documents")
    assert len(cov["new"]["documents"]) == 12 and cov["new"]["entries"] > 700


def _spdx_files(spdx_dir: Path) -> dict[str, dict[str, str]]:
    """Independent reading of zephyr.spdx: {package: {file: sha1}} by plain line scan."""
    out: dict[str, dict[str, str]] = {}
    pkg, cur = "", ""
    for line in (spdx_dir / "zephyr.spdx").read_text().splitlines():
        if line.startswith("PackageName: "):
            pkg = line.split(": ", 1)[1].removesuffix("-sources")
            pkg = "zephyr" if pkg in ("zephyr", "zephyr-sources") else pkg
            out.setdefault(pkg, {})
        elif line.startswith("FileName: "):
            cur = line.split(": ", 1)[1].removeprefix("./")
        elif line.startswith("FileChecksum: SHA1: "):
            out[pkg][cur] = line.rsplit(" ", 1)[1]
    return out


def test_sbom_matches_independent_sources(report: dict[str, Any]) -> None:
    import subprocess

    old_b = CACHE / "builds" / report["contexts"][0]["key"]
    new_b = CACHE / "builds" / report["contexts"][2]["key"]
    o, n = _spdx_files(old_b / "spdx"), _spdx_files(new_b / "spdx")

    f = find(report, "zephyr-sources", "zephyr")
    assert set(f["new"]["added"]) == n["zephyr"].keys() - o["zephyr"].keys()
    assert set(f["new"]["removed"]) == o["zephyr"].keys() - n["zephyr"].keys()
    assert set(f["new"]["changed"]) == {
        k for k in o["zephyr"].keys() & n["zephyr"].keys() if o["zephyr"][k] != n["zephyr"][k]
    }
    # HWMv2 moved native_sim's board sources.
    assert "zephyr/boards/posix/native_sim/cmdline.c" in f["new"]["removed"]
    assert "zephyr/boards/native/native_sim/cmdline.c" in f["new"]["added"]

    mirror = next((CACHE / "mirrors").glob("*zephyr.git"))

    def pin(ref: str) -> str:
        wy = subprocess.run(
            ["git", "--git-dir", str(mirror), "show", f"{ref}:west.yml"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        block = wy.split("- name: zcbor\n", 1)[1]
        return next(
            ln.split(":", 1)[1].strip()
            for ln in block.splitlines()
            if ln.strip().startswith("revision:")
        )

    z = find(report, "module-repinned", "zcbor")
    assert (z["old"], z["new"]["sha"]) == (pin("v3.6.0"), pin("v3.7.0"))
    assert z["new"]["changed"] == sorted(
        k for k in o["zcbor"].keys() & n["zcbor"].keys() if o["zcbor"][k] != n["zcbor"][k]
    )

    sizes = []
    for b in (old_b, new_b):
        text, data, bss = (
            subprocess.run(
                ["size", "--format=berkeley", str(b / "zephyr" / "zephyr.elf")],
                capture_output=True,
                text=True,
                check=True,
            )
            .stdout.splitlines()[1]
            .split()[:3]
        )
        sizes.append({"rom": int(text) + int(data), "ram": int(data) + int(bss)})
    fp = find(report, "footprint")
    assert fp["old"] == sizes[0]
    assert {k: fp["new"][k] for k in ("rom", "ram")} == sizes[1]


def test_sarif_validates_against_official_schema(report: dict[str, Any], tmp_path: Path) -> None:
    import urllib.request

    url = (
        "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/"
        "sarif-schema-2.1.0.json"
    )
    with urllib.request.urlopen(url, timeout=60) as resp:
        schema = json.load(resp)
    sarif = json.loads((Path(report["_out"]) / "report.sarif").read_text())
    jsonschema.validators.validator_for(schema)(schema).validate(sarif)
    uris = {
        loc["physicalLocation"]["artifactLocation"]["uri"]
        for r in sarif["runs"][0]["results"]
        for loc in r["locations"]
    }
    assert uris and all((APP / u).is_file() for u in uris)
