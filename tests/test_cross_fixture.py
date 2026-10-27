"""Cross-compiled sysbuild + MCUboot fixtures, analyzed per image (tests/fixtures/xapp*).

Run with: pytest -m sdk   (needs the Zephyr SDK >= 1.0 with arm-zephyr-eabi, and network on
first run to clone the modules)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from tests.manual_diff import config_diff, dts_diff
from zmig.cli import main

ROOT = Path(__file__).parents[1]
SCHEMA = json.loads((ROOT / "zmig/schema/report-1.json").read_text())
CACHE = Path(os.environ.get("ZMIG_CACHE", Path.home() / ".cache" / "zmig"))
MODULES = ["mcuboot", "hal_nordic", "cmsis_6", "mbedtls", "tf-psa-crypto"]
FROM, TO = "v4.4.0", "v4.5.0-rc3"

pytestmark = [pytest.mark.sdk, pytest.mark.network]


def scan(fixture: str, out: Path) -> dict[str, Any]:
    os.environ.pop("PYTHONPATH", None)
    args = [
        "scan",
        "--app",
        str(ROOT / "tests" / "fixtures" / fixture),
        "--board",
        "nrf52840dk/nrf52840",
        "--sysbuild",
        "--from",
        FROM,
        "--to",
        TO,
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


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return scan("xapp", tmp_path_factory.mktemp("xapp"))


def image_dir(report: dict[str, Any], ctx: int, image: str) -> Path:
    return CACHE / "builds" / report["contexts"][ctx]["key"] / image


def of(
    report: dict[str, Any], image: str | None, category: str | None = None, kind: str | None = None
) -> list[dict[str, Any]]:
    """Findings of one image (None = the app image / whole build)."""
    return [
        f
        for f in report["findings"]
        if f.get("image") == image
        and (category is None or f["category"] == category)
        and (kind is None or f["kind"] == kind)
    ]


# --- images ----------------------------------------------------------------------------


def test_images_discovered(report: dict[str, Any]) -> None:
    for ctx in report["contexts"]:
        assert ctx["status"] == "ok"
        assert {(i["name"], i["role"], i["status"]) for i in ctx["images"]} == {
            ("mcuboot", "bootloader", "ok"),
            ("xapp", "app", "ok"),
        }
    # Every per-image analyzer ran for both images.
    names = {a["name"]: a["status"] for a in report["analyzers"]}
    for analyzer in ("board", "kconfig", "devicetree", "buildlog", "sbom"):
        for image in ("mcuboot", "xapp"):
            assert names[f"{analyzer}@{image}"] == "ok", (analyzer, image)
    assert names["images"] == names["crossimage"] == names["guide"] == "ok"


def test_toolchain_and_artifacts(report: dict[str, Any]) -> None:
    for ctx in (0, 1):
        for image in ("xapp", "mcuboot"):
            cache = (image_dir(report, ctx, image) / "CMakeCache.txt").read_text()
            nm = re.search(r"^CMAKE_NM:\w+=(.+)$", cache, re.M)
            assert nm and nm[1].endswith("arm-zephyr-eabi-nm")
        assert (image_dir(report, ctx, "xapp") / "zephyr" / "zephyr.signed.bin").is_file()


# --- per-image oracles -------------------------------------------------------------------


@pytest.mark.parametrize("image,tag", [("xapp", None), ("mcuboot", "mcuboot")])
def test_kconfig_and_dt_match_manual_diffs(
    report: dict[str, Any], image: str, tag: str | None
) -> None:
    old, new = image_dir(report, 0, image), image_dir(report, 1, image)
    covered: set[str] = set()
    for f in of(report, tag, "kconfig"):
        if f["kind"] in ("default-changed", "newly-set", "no-longer-set"):
            covered |= set(f["new"])
        elif f["kind"] != "salvaged":
            covered.add(f["subject"].removeprefix("CONFIG_"))
    assert config_diff(old / "zephyr/.config", new / "zephyr/.config") <= covered

    dcov: set[str] = set()
    for f in of(report, tag, "devicetree"):
        if f["kind"] == "other-nodes":
            for k in ("changed", "removed", "added"):
                dcov |= set(f["new"][k])
        elif f["kind"] in ("node-gone", "node-moved"):
            dcov |= {f["old"], f["new"]} - {None}
        elif f["kind"] == "node-changed":
            dcov.add(f["subject"].split(":")[-1])
    assert dcov == dts_diff(old / "zephyr/zephyr.dts", new / "zephyr/zephyr.dts")


def _spdx_files(spdx: Path) -> dict[str, set[str]]:
    out: dict[str, dict[str, str]] = {}
    pkg = cur = ""
    for line in spdx.read_text().splitlines():
        if line.startswith("PackageName: "):
            pkg = line.split(": ", 1)[1]
            pkg = "zephyr" if pkg in ("zephyr", "zephyr-sources") else pkg
            out.setdefault(pkg, {})
        elif line.startswith("FileName: "):
            cur = line.split(": ", 1)[1].removeprefix("./")
        elif line.startswith("FileChecksum: SHA1: "):
            out[pkg][cur] = line.rsplit(" ", 1)[1]
    return {k: {f"{f}:{h}" for f, h in v.items()} for k, v in out.items()}


@pytest.mark.parametrize("image,tag", [("xapp", None), ("mcuboot", "mcuboot")])
def test_sbom_per_image(report: dict[str, Any], image: str, tag: str | None) -> None:
    o = _spdx_files(image_dir(report, 0, image) / "spdx" / "zephyr.spdx")["zephyr"]
    n = _spdx_files(image_dir(report, 1, image) / "spdx" / "zephyr.spdx")["zephyr"]
    (f,) = of(report, tag, "sbom", "zephyr-sources")
    files_o = {x.rsplit(":", 1)[0] for x in o}
    files_n = {x.rsplit(":", 1)[0] for x in n}
    assert set(f["new"]["added"]) == files_n - files_o
    assert set(f["new"]["removed"]) == files_o - files_n
    assert set(f["new"]["changed"]) == {x.rsplit(":", 1)[0] for x in o - n} & files_n


@pytest.mark.parametrize("image,tag", [("xapp", None), ("mcuboot", "mcuboot")])
def test_footprint_per_image_matches_arm_size(
    report: dict[str, Any], image: str, tag: str | None
) -> None:
    (fp,) = of(report, tag, "build", "footprint")
    assert fp["new"]["host_runtime_inflated"] is False
    sizes = []
    for ctx in (0, 1):
        d = image_dir(report, ctx, image)
        nm = re.search(r"^CMAKE_NM:\w+=(.+)$", (d / "CMakeCache.txt").read_text(), re.M)
        assert nm
        text, data, bss = (
            subprocess.run(
                [
                    nm[1].strip()[: -len("nm")] + "size",
                    "--format=berkeley",
                    str(d / "zephyr" / "zephyr.elf"),
                ],
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


def test_warnings_are_attributed_to_their_image(report: dict[str, Any]) -> None:
    for f in report["findings"]:
        if f["kind"] != "new-warning":
            continue
        image = f.get("image") or "xapp"
        log = CACHE / "builds" / report["contexts"][1]["key"] / "zmig-logs" / f"{image}.log"
        text = " ".join(log.read_text().split())
        msg = f["title"].split(": ", 1)[1].removesuffix("...")
        assert msg[:60] in text, (image, msg)


# --- MCUboot ----------------------------------------------------------------------------


def _pin(ref: str, module: str) -> str:
    mirror = next((CACHE / "mirrors").glob("*zephyr.git"))
    wy = subprocess.run(
        ["git", "--git-dir", str(mirror), "show", f"{ref}:west.yml"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    block = wy.split(f"- name: {module}\n", 1)[1]
    return next(
        ln.split(":", 1)[1].strip()
        for ln in block.splitlines()
        if ln.strip().startswith("revision:")
    )


def test_mcuboot_repin_is_visible(report: dict[str, Any]) -> None:
    """Regression: the MCUboot re-pin used to report 0 compiled files changed, because only
    the app image was analyzed."""
    (f,) = of(report, None, "images", "mcuboot-revision")
    assert (f["old"]["sha"], f["new"]["sha"]) == (_pin(FROM, "mcuboot"), _pin(TO, "mcuboot"))
    assert sum(f["new"]["mcuboot_files"].values()) > 0
    assert f["severity"] == "risky"
    # And the bootloader image's own SBOM finding names MCUboot's sources.
    assert of(report, "mcuboot", "sbom", "zephyr-sources")


# --- cross-image and salvage fixtures -------------------------------------------------------


def test_cross_image_partition_mismatch(tmp_path: Path) -> None:
    r = scan("xapp-mismatch", tmp_path)
    hits = [
        f
        for f in r["findings"]
        if f["category"] == "crossimage"
        and f["kind"] == "partition"
        and "slot1_partition" in f["subject"]
    ]
    assert len(hits) == 1
    f = hits[0]
    assert f["severity"] == "breaking"
    # The app image is named after the app directory.
    assert f["new"] == {"mcuboot": [0x82000, 0x76000], "xapp-mismatch": [0x84000, 0x74000]}
    paths = [(e["path"], e["line"]) for e in f["evidence"] if e["type"] == "file_line"]
    assert ("app.overlay", 3) in paths
    assert any(p.endswith("mcuboot>/zephyr/zephyr.dts") for p, _ in paths)
    assert any(p.endswith("xapp-mismatch>/zephyr/zephyr.dts") for p, _ in paths)
    # The untouched xapp fixture has no cross-image findings at all.


def test_untouched_fixture_is_consistent(report: dict[str, Any]) -> None:
    assert not [f for f in report["findings"] if f["category"] == "crossimage"]


def test_salvage_under_sysbuild(tmp_path: Path) -> None:
    r = scan("xapp-salvage", tmp_path)
    raw, salvaged = r["contexts"][1], r["contexts"][2]
    assert raw["status"] == "configure-failed"
    assert salvaged["status"] == "ok"
    assert [(e["file"], e["line"]) for e in salvaged["salvage_edits"]] == [
        ("prj.conf", 2),
        ("sysbuild/mcuboot.conf", 1),
    ]
    app = [
        f
        for f in r["findings"]
        if f["kind"] == "symbol-removed" and f["subject"] == "CONFIG_BUILD_NO_GAP_FILL"
    ]
    boot = [
        f
        for f in r["findings"]
        if f["kind"] == "symbol-removed" and f["subject"] == "CONFIG_BOOT_USB_DFU_NO"
    ]
    assert len(app) == 1 and app[0].get("image") is None
    assert len(boot) == 1 and boot[0]["image"] == "mcuboot"
    assert [(e["path"], e["line"]) for e in boot[0]["evidence"] if e["type"] == "file_line"] == [
        ("sysbuild/mcuboot.conf", 1)
    ]


def test_mcuboot_category_has_no_hazard(report: dict[str, Any]) -> None:
    """v4.4.0 -> v4.5.0-rc3: same mode (swap-using-offset) and format; implementation only."""
    hazards = [f for f in of(report, None, "mcuboot") if f.get("remedy")]
    assert hazards == []
    (impl,) = of(report, None, "mcuboot", "implementation-changed")
    assert impl["confidence"] == "medium" and impl["new"]["files"]


def test_wire_ignores_values_no_compiled_code_reads(report: dict[str, Any]) -> None:
    """Regression: CONFIG_USB_DEVICE_PRODUCT in MCUboot goes "MCUBOOT" -> unset, but the USB
    stack isn't built at either ref, so nothing reaches a host."""
    assert not [f for f in report["findings"] if f["category"] == "wire"]


def test_verdict_xapp(report: dict[str, Any]) -> None:
    u = report["upgrade_path"]
    assert u["verdict"] == "direct-ok" and u["confidence"] == "medium"


def test_verdict_pre_existing_mismatch(tmp_path: Path) -> None:
    """§8.3: xapp-mismatch's slot1 disagreement exists at the source, so it is attributed
    as pre-existing, layout and crossimage are unknown, and confidence is capped at low."""
    r = scan("xapp-mismatch", tmp_path)
    (f,) = [x for x in r["findings"] if x["category"] == "crossimage" and "slot1" in x["subject"]]
    assert f["introduced_by_migration"] is False
    u = r["upgrade_path"]
    assert u["verdict"] == "unknown" and u["confidence"] == "low"
    assert u["categories"]["crossimage"]["status"] == "unknown"
    assert u["categories"]["layout"]["status"] == "unknown"
