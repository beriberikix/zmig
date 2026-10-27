"""Cross-compiled sysbuild + MCUboot fixture (tests/fixtures/xapp).

Run with: pytest -m sdk   (needs the Zephyr SDK >= 1.0 with arm-zephyr-eabi, and network on
first run to clone the modules)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from zmig.cli import main

ROOT = Path(__file__).parents[1]
APP = ROOT / "tests" / "fixtures" / "xapp"
SCHEMA = json.loads((ROOT / "zmig/schema/report-1.json").read_text())
CACHE = Path(os.environ.get("ZMIG_CACHE", Path.home() / ".cache" / "zmig"))
MODULES = ["mcuboot", "hal_nordic", "cmsis_6", "mbedtls", "tf-psa-crypto"]

pytestmark = [pytest.mark.sdk, pytest.mark.network]


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    os.environ.pop("PYTHONPATH", None)
    out = tmp_path_factory.mktemp("xscan")
    args = [
        "scan",
        "--app",
        str(APP),
        "--board",
        "nrf52840dk/nrf52840",
        "--sysbuild",
        "--from",
        "v4.4.0",
        "--to",
        "v4.5.0-rc3",
        "--format",
        "json",
        "--out",
        str(out),
    ]
    for m in MODULES:
        args += ["--module", m]
    assert main(args) == 0
    r: dict[str, Any] = json.loads((out / "report.json").read_text())
    jsonschema.validate(r, SCHEMA)
    return r


def _build(report: dict[str, Any], i: int) -> Path:
    return CACHE / "builds" / report["contexts"][i]["key"]


def test_builds_with_sdk_toolchain(report: dict[str, Any]) -> None:
    assert [c["status"] for c in report["contexts"]] == ["ok", "ok"]
    for i in (0, 1):
        b = _build(report, i)
        cache = (b / "xapp" / "CMakeCache.txt").read_text()
        nm = re.search(r"^CMAKE_NM:\w+=(.+)$", cache, re.M)
        assert nm and nm[1].endswith("arm-zephyr-eabi-nm"), nm
        assert (b / "mcuboot" / "zephyr" / "zephyr.elf").is_file()  # the other image
        assert (b / "xapp" / "zephyr" / "zephyr.signed.bin").is_file()  # MCUboot-signed


def test_all_analyzers_ran(report: dict[str, Any]) -> None:
    assert {a["name"]: a["status"] for a in report["analyzers"]} == {
        k: "ok" for k in ("board", "buildlog", "devicetree", "guide", "kconfig", "sbom")
    }


def test_footprint_matches_arm_size(report: dict[str, Any]) -> None:
    fp = next(f for f in report["findings"] if f["kind"] == "footprint")
    assert fp["new"]["host_runtime_inflated"] is False
    assert "build.host-runtime-inflated" not in fp["heuristics"]
    sizes = []
    for i in (0, 1):
        b = _build(report, i) / "xapp"
        nm = re.search(r"^CMAKE_NM:\w+=(.+)$", (b / "CMakeCache.txt").read_text(), re.M)
        assert nm
        size = nm[1].strip()[: -len("nm")] + "size"
        assert shutil.which(size) or Path(size).is_file()
        text, data, bss = (
            subprocess.run(
                [size, "--format=berkeley", str(b / "zephyr" / "zephyr.elf")],
                capture_output=True,
                text=True,
                check=True,
            )
            .stdout.splitlines()[1]
            .split()[:3]
        )
        sizes.append({"rom": int(text) + int(data), "ram": int(data) + int(bss)})
    assert fp["old"] == sizes[0]
    assert {k: fp["new"][k] for k in ("rom", "ram")} == sizes[1]


def test_sbom_for_cross_build(report: dict[str, Any]) -> None:
    for i in (0, 1):
        spdx = _build(report, i) / "xapp" / "spdx" / "zephyr.spdx"
        assert spdx.is_file() and spdx.read_text().count("FileName:") > 50
    names = {f["kind"] for f in report["findings"] if f["category"] == "sbom"}
    assert "zephyr-sources" in names


def test_underscored_modules_are_tracked(report: dict[str, Any]) -> None:
    """Regression: hal_nordic (underscore in the west name) was re-pinned between the refs
    and compiles files into the app image; it must be reported."""
    f = next(
        f
        for f in report["findings"]
        if f["kind"] == "module-repinned" and f["subject"] == "hal-nordic"
    )
    assert f["old"] and f["new"]["sha"] and f["old"] != f["new"]["sha"]
