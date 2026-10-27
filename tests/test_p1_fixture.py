"""P1 field-upgrade hazard fixtures A, B, C (docs/DESIGN-P1.md §11), built with SDK 0.17.4.

Run with: pytest -m sdk   (needs ~/.cache/zmig/sdks/zephyr-sdk-0.17.4 or ZMIG_SDK_0174, and
network on first run). Resolved SHAs are asserted so the expectations stay tied to the
trees they were derived from.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from zmig.cli import main

ROOT = Path(__file__).parents[1]
SCHEMA = json.loads((ROOT / "zmig/schema/report-1.json").read_text())
CACHE = Path(os.environ.get("ZMIG_CACHE", Path.home() / ".cache" / "zmig"))
SDK = Path(os.environ.get("ZMIG_SDK_0174", CACHE / "sdks" / "zephyr-sdk-0.17.4"))
COMMON = ["mcuboot", "cmsis", "cmsis_6", "mbedtls", "tinycrypt"]

SHAS = {
    "v4.0.0": "8469084dfae85f854555f0607f2c838dad097235",
    "v4.1.0": "7823374e872145b5bd018bfe447839eb36042611",
    "v4.2.2": "dbb536326e611dc8e97cc6a322d379ba9cac1ab7",
    "v4.3.0": "3568e1b6d5cdd51a6b964a2a1d6d29200fea2056",
}
FIXTURES = {
    "A": ("p1-layout", "nucleo_h753zi", "v4.2.2", "v4.3.0", ["hal_stm32"]),
    "B": ("p1-mode", "nrf52840dk/nrf52840", "v4.2.2", "v4.3.0", ["hal_nordic", "segger"]),
    "C": ("p1-rename", "nrf52840dk/nrf52840", "v4.0.0", "v4.1.0", ["hal_nordic", "segger"]),
}

pytestmark = [
    pytest.mark.sdk,
    pytest.mark.network,
    pytest.mark.skipif(not SDK.is_dir(), reason=f"SDK 0.17.4 not at {SDK}"),
]


def _scan(name: str, out: Path) -> dict[str, Any]:
    app, board, frm, to, extra = FIXTURES[name]
    os.environ.pop("PYTHONPATH", None)
    args = ["scan", "--app", str(ROOT / "tests/fixtures" / app), "--board", board]
    args += ["--from", frm, "--to", to, "--sysbuild", "--sdk", str(SDK)]
    for m in COMMON + extra:
        args += ["--module", m]
    args += ["--format", "json", "--out", str(out)]
    assert main(args) == 0
    r: dict[str, Any] = json.loads((out / "report.json").read_text())
    jsonschema.validate(r, SCHEMA)
    assert r["inputs"]["from"]["sha"] == SHAS[frm]
    assert r["inputs"]["to"]["sha"] == SHAS[to]
    for c in r["contexts"]:
        assert c["status"] == "ok"
        assert {i["status"] for i in c["images"]} == {"ok"}
        assert c["sdk"]["version"] == "0.17.4"
    return r


@pytest.fixture(scope="module")
def reports(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    return {n: _scan(n, tmp_path_factory.mktemp(n)) for n in FIXTURES}


def of(r: dict[str, Any], category: str, kind: str | None = None) -> list[dict[str, Any]]:
    return [f for f in r["findings"] if f["category"] == category and kind in (None, f["kind"])]


# --- A: nucleo_h753zi v4.2.2 -> v4.3.0 (layout, storage, scratch; no mode change) ----------


def test_a_layout_exact_deltas(reports: dict[str, dict[str, Any]]) -> None:
    r = reports["A"]
    got = {
        (f["kind"], f["subject"]): (f.get("old"), f.get("new"), f["remedy"])
        for f in of(r, "layout")
    }
    assert got[("partition-moved", "slot0_partition")] == (
        {"reg": [0x40000, 0x40000]},
        {"reg": [0x20000, 0x40000]},
        "reflash",
    )
    assert got[("partition-moved", "slot1_partition")] == (
        {"reg": [0x80000, 0x40000]},
        {"reg": [0x60000, 0x40000]},
        "reflash",
    )
    assert got[("partition-removed", "scratch_partition")] == (
        {"reg": [0xC0000, 0x20000]},
        None,
        "reflash",
    )
    assert got[("storage-stranded", "storage_partition")] == (
        {"reg": [0x20000, 0x20000]},
        {"reg": [0xA0000, 0x40000]},
        "app-change",
    )
    # One finding per partition: storage_partition is not also reported as "moved".
    assert ("partition-moved", "storage_partition") not in got
    # Regression: MCUboot's optional serial-recovery erase code must not make the storage
    # partition a bootloader partition.
    (st,) = of(r, "layout", "storage-stranded")
    assert "[role: storage]" in st["title"]


def test_a_no_mode_change(reports: dict[str, dict[str, Any]]) -> None:
    r = reports["A"]
    assert not of(r, "mcuboot", "app-bootloader-mismatch")
    assert not of(r, "mcuboot", "bootloader-choice-changed")
    assert not [f for f in of(r, "sysbuild") if "MCUBOOT_MODE" in f["subject"]]


# --- B: nrf52840dk v4.2.2 -> v4.3.0 (mode change, slot requirement, no layout) ------------


def test_b_mode_hazard_with_config_pin(reports: dict[str, dict[str, Any]]) -> None:
    r = reports["B"]
    assert not of(r, "layout")
    (m,) = of(r, "mcuboot", "app-bootloader-mismatch")
    assert m["severity"] == "breaking" and m["remedy"] == "config-pin"
    assert m["subject"] == "SB_CONFIG_MCUBOOT_MODE_SWAP_USING_OFFSET"
    assert m["old"]["deployed_bootloader"] == "CONFIG_BOOT_SWAP_USING_MOVE"
    paths = {(e["path"], e["line"]) for e in m["evidence"] if e["type"] == "file_line"}
    # The old option still exists at the target (the config pin).
    assert ("<zephyr v4.3.0>/share/sysbuild/images/bootloader/Kconfig", 55) in paths
    # The consequence, from compiled app code: the image manager writes offset uploads.
    assert any(p.endswith("subsys/dfu/img_util/flash_img.c") for p, _ in paths)
    (sb,) = of(r, "sysbuild", "sb-choice-default-changed")
    assert sb["remedy"] == "config-pin"


def test_b_slot_requirement(reports: dict[str, dict[str, Any]]) -> None:
    (g,) = of(reports["B"], "mcuboot", "slot-geometry")
    assert g["subject"] == "swap_offset"
    assert g["new"]["sectors"] == {"primary": 118, "secondary": 118, "erase_block": 0x1000}
    by_line = {c["line"]: c["result"] for c in g["new"]["conditions"]}
    assert by_line[316] is False  # compatible sector counts (swap_offset.c:316 @ v4.3.0 pin)
    assert by_line[329] is True  # non-optimal: secondary not one sector larger
    assert g["severity"] == "risky"


# --- C: nrf52840dk v4.0.0 -> v4.1.0 (renamed, behavior unchanged) ---------------------------


def test_c_rename_behavior_unchanged(reports: dict[str, dict[str, Any]]) -> None:
    r = reports["C"]
    (f,) = of(r, "sysbuild", "sb-option-renamed")
    assert f["subject"] == "SB_CONFIG_MCUBOOT_MODE_SWAP_WITHOUT_SCRATCH"
    assert f["new"]["replacement"] == "MCUBOOT_MODE_SWAP_USING_MOVE"
    assert f["new"]["behavior_unchanged"] is True
    assert f["new"]["renamed_image_options"] == [
        {
            "image": "p1-rename",
            "option": "CONFIG_MCUBOOT_BOOTLOADER_MODE_SWAP_WITHOUT_SCRATCH",
            "old": "y",
            "new": "n",
            "renamed_to": "CONFIG_MCUBOOT_BOOTLOADER_MODE_SWAP_USING_MOVE",
        }
    ]
    assert not of(r, "mcuboot", "app-bootloader-mismatch")
    assert not of(r, "mcuboot", "bootloader-choice-changed")
    # Regression: SB_CONFIG_DEPRECATED is not a finding of its own.
    assert not [f for f in of(r, "sysbuild") if f["subject"] == "SB_CONFIG_DEPRECATED"]


# --- all fixtures ------------------------------------------------------------------------------


def test_no_per_ref_path_false_positives(reports: dict[str, dict[str, Any]]) -> None:
    for r in reports.values():
        assert not [f for f in of(r, "sysbuild") if "KEY_FILE" in f["subject"]]


def test_signed_image_parser_matches_imgtool_dumpinfo(
    reports: dict[str, dict[str, Any]],
) -> None:
    """Oracle: imgtool's own dumpinfo, at the same MCUboot pin, on the same files."""
    for r in reports.values():
        (d,) = of(r, "mcuboot", "signed-image-diff")
        for side, ref in (("old", r["inputs"]["from"]["sha"]), ("new", r["inputs"]["to"]["sha"])):
            img = d[side]
            tool = CACHE / "workspaces" / ref / "bootloader/mcuboot/scripts/imgtool.py"
            out = subprocess.run(
                [sys.executable, str(tool), "dumpinfo", img["path"]],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            head, _, tail = out.partition("#### Payload")
            hdr = dict(
                re.findall(
                    r"^(magic|hdr_size|img_size|protected_tlv_size|flags):\s+(\S+)", head, re.M
                )
            )
            areas = re.findall(r"^magic:\s+(0x[0-9a-f]+)\narea size:\s+(0x[0-9a-f]+)", tail, re.M)
            assert [(int(m, 16), int(n, 16)) for m, n in areas] == [
                (a["magic"], a["size"]) for a in img["tlv_areas"]
            ]
            assert int(hdr["magic"], 0) == img["header"]["ih_magic"]
            assert int(hdr["hdr_size"], 0) == img["header"]["ih_hdr_size"]
            assert int(hdr["img_size"], 0) == img["header"]["ih_img_size"]
            assert int(hdr["protected_tlv_size"], 0) == img["header"]["ih_protect_tlv_size"]
            assert int(hdr["flags"], 0) == img["header"]["ih_flags"]
            tlvs = [
                (int(t, 16), int(n, 16))
                for t, n in re.findall(r"type: \S+ \((0x[0-9a-f]+)\)\s+len:\s+(0x[0-9a-f]+)", out)
            ]
            assert tlvs == [(t["type"], t["len"]) for t in img["tlvs"]]


# --- storage ---------------------------------------------------------------------------------

C_RESTORE = ["FLASH", "FLASH_MAP", "FLASH_PAGE_LAYOUT", "NVS", "SETTINGS"]


def test_c_storage_implicit_enable(reports: dict[str, dict[str, Any]]) -> None:
    """v4.1.0: the ITS settings store stops selecting SETTINGS / implying NVS
    (Kconfig.its_store:15 and :14 at v4.0.0) and depends on SETTINGS (:34 at v4.1.0)."""
    r = reports["C"]
    got = {(f["kind"], f["subject"]): f for f in of(r, "storage") if f.get("remedy")}
    assert set(got) == {
        ("backend-disabled", "settings"),
        ("backend-disabled", "nvs"),
        ("backend-switched", "secure_storage"),
    }
    ev = {
        (e["path"], e["line"], e.get("text"))
        for f in got.values()
        for e in f["evidence"]
        if e["type"] == "file_line"
    }
    assert ("<zephyr v4.0.0>/subsys/secure_storage/Kconfig.its_store", 15, "select SETTINGS") in ev
    assert ("<zephyr v4.0.0>/subsys/secure_storage/Kconfig.its_store", 14, "imply NVS") in ev
    assert (
        "<zephyr v4.1.0>/subsys/secure_storage/Kconfig.its_store",
        34,
        "depends on SETTINGS",
    ) in ev
    sw = got[("backend-switched", "secure_storage")]
    assert sw["new"]["choices"] == {
        "SECURE_STORAGE_ITS_STORE_IMPLEMENTATION": "SECURE_STORAGE_ITS_STORE_IMPLEMENTATION_NONE"
    }
    for f in got.values():
        assert f["severity"] == "breaking" and f["remedy"] == "config-pin"
        assert f["new"]["restore"]["verified"] is True
        assert f["new"]["restore"]["assign"] == [f"CONFIG_{s}=y" for s in C_RESTORE]


def test_c_storage_remedy_builds(tmp_path: Path) -> None:
    """Oracle for the config-pin: a real build of the fixture with the derived assignments
    keeps the source's backends (only informational storage findings remain)."""
    app = tmp_path / "p1-rename"
    import shutil

    shutil.copytree(ROOT / "tests/fixtures/p1-rename", app)
    with (app / "prj.conf").open("a") as f:
        f.writelines(f"CONFIG_{s}=y\n" for s in C_RESTORE)
    _, board, frm, to, extra = FIXTURES["C"]
    args = ["scan", "--app", str(app), "--board", board, "--from", frm, "--to", to]
    args += ["--sysbuild", "--sdk", str(SDK), "--analyzers", "storage"]
    for m in COMMON + extra:
        args += ["--module", m]
    args += ["--format", "json", "--out", str(tmp_path / "out")]
    assert main(args) == 0
    r = json.loads((tmp_path / "out" / "report.json").read_text())
    assert [c["status"] for c in r["contexts"]] == ["ok", "ok"]
    assert {f["severity"] for f in of(r, "storage")} <= {"info"}


def test_a_storage_links_layout_stranding(reports: dict[str, dict[str, Any]]) -> None:
    r = reports["A"]
    assert not [f for f in of(r, "storage") if f.get("remedy")]
    (st,) = of(r, "layout", "storage-stranded")
    assert st["subject"] == "storage_partition"
    (impl,) = of(r, "storage", "implementation-changed")
    assert "subsys/settings/src/settings_store.c" in impl["new"]["files"]


def test_b_has_no_storage(reports: dict[str, dict[str, Any]]) -> None:
    assert not of(reports["B"], "storage")
