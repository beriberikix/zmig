"""Regressions found by dogfooding zmig on public projects (docs/DOGFOOD-1.md).

Run with: pytest -m sdk
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from zmig.cli import main

ROOT = Path(__file__).parents[1]
CACHE = Path(os.environ.get("ZMIG_CACHE", Path.home() / ".cache" / "zmig"))
SDK = Path(os.environ.get("ZMIG_SDK_0174", CACHE / "sdks" / "zephyr-sdk-0.17.4"))

pytestmark = [
    pytest.mark.sdk,
    pytest.mark.network,
    pytest.mark.skipif(not SDK.is_dir(), reason=f"SDK 0.17.4 not at {SDK}"),
]


def test_app_that_finds_zephyr_by_west_layout(tmp_path: Path) -> None:
    """ZMK's app/CMakeLists.txt uses find_package(Zephyr HINTS ../zephyr). zmig copies the
    app out of that layout and (with --sdk) disables the package registry, so configure
    failed, and was reported as "could not confirm the toolchain" with no log path."""
    os.environ.pop("PYTHONPATH", None)
    args = ["scan", "--app", str(ROOT / "tests/fixtures/hints-app")]
    args += ["--board", "nrf52840dk/nrf52840", "--from", "v4.2.2", "--to", "v4.3.0"]
    args += ["--sdk", str(SDK), "--module", "hal_nordic", "--module", "cmsis"]
    args += ["--module", "cmsis_6", "--fast", "--analyzers", "none"]
    args += ["--format", "json", "--out", str(tmp_path)]
    assert main(args) == 0
    r: dict[str, Any] = json.loads((tmp_path / "report.json").read_text())
    assert [c["status"] for c in r["contexts"]] == ["ok", "ok"]


def test_smp_svr_p1_verdict(tmp_path: Path) -> None:
    """Zephyr's smp_svr sample (as of v4.2.2, BLE transport) v4.2.2 -> v4.3.0 with sysbuild
    and MCUboot. Regressions: the firmware-updater sysbuild pair matched on CONFIG_REBOOT
    (its trigger is off) four times; new FUSE/virtiofs symbols were reported as changed
    storage parameters."""
    import subprocess

    mirror = next((CACHE / "mirrors").glob("*zephyr.git"))
    app = tmp_path / "src"
    app.mkdir()
    archive = subprocess.run(
        [
            "git",
            "--git-dir",
            str(mirror),
            "archive",
            "v4.2.2",
            "samples/subsys/mgmt/mcumgr/smp_svr",
        ],
        capture_output=True,
        check=True,
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(app), "--strip-components=4"], input=archive, check=True)
    os.environ.pop("PYTHONPATH", None)
    args = ["scan", "--app", str(app / "smp_svr"), "--board", "nrf52840dk/nrf52840"]
    args += ["--sysbuild", "--cmake-arg=-DEXTRA_CONF_FILE=overlay-bt.conf"]
    args += ["--from", "v4.2.2", "--to", "v4.3.0", "--sdk", str(SDK)]
    for m in ("mcuboot", "hal_nordic", "cmsis", "cmsis_6", "mbedtls", "tinycrypt", "zcbor"):
        args += ["--module", m]
    args += ["--module", "segger", "--module", "littlefs", "--format", "json"]
    args += ["--out", str(tmp_path / "out")]
    assert main(args) == 0
    r: dict[str, Any] = json.loads((tmp_path / "out" / "report.json").read_text())
    mism = [f for f in r["findings"] if f["kind"] == "app-bootloader-mismatch"]
    assert [f["subject"] for f in mism] == ["SB_CONFIG_MCUBOOT_MODE_SWAP_USING_OFFSET"]
    assert mism[0]["remedy"] == "config-pin"
    assert not [f for f in r["findings"] if f["kind"] == "backend-parameters-changed"]
    u = r["upgrade_path"]
    assert u["verdict"] == "needs-changes" and set(u["remedies"]) == {"config-pin"}
    assert u["unchecked"] == ["storage"]  # littlefs mounts through a devicetree fstab


SDK_0168 = Path(os.environ.get("ZMIG_SDK_0168", CACHE / "sdks" / "zephyr-sdk-0.16.8"))


@pytest.mark.skipif(not SDK_0168.is_dir(), reason=f"SDK 0.16.8 not at {SDK_0168}")
def test_target_needs_a_module_the_scan_lacks(tmp_path: Path) -> None:
    """bms-firmware v4.0 -> v4.2 (docs/DOGFOOD-2-baseline.md): Cortex-M needs cmsis_6 from
    v4.2, the pre-upgrade module list has only cmsis, and the whole target build was lost
    to `cmsis_core.h: No such file or directory`. On nRF the same gap stops the configure
    (a cmsis_6 glue Kconfig symbol with `&& 0` dependencies). zmig now names the module,
    where it's required and declared, and analyzes the target built with it."""
    os.environ.pop("PYTHONPATH", None)
    args = ["scan", "--app", str(ROOT / "tests/fixtures/hints-app")]
    args += ["--board", "nrf52840dk/nrf52840", "--from", "v4.1.0", "--to", "v4.2.0"]
    args += ["--sdk", str(SDK_0168), "--module", "hal_nordic", "--module", "cmsis"]
    args += ["--analyzers", "modules", "--format", "json", "--out", str(tmp_path)]
    assert main(args) == 0
    r: dict[str, Any] = json.loads((tmp_path / "report.json").read_text())
    assert [c["status"] for c in r["contexts"]] == ["ok", "configure-failed", "ok"]
    assert r["contexts"][1]["superseded_by_module_recovery"] == ["cmsis_6"]
    assert r["contexts"][2]["modules_added"] == ["cmsis_6"]
    (need,) = [f for f in r["findings"] if f["kind"] == "module-required"]
    assert need["severity"] == "breaking"
    assert need["new"]["module"] == "cmsis_6"
    assert need["new"]["added_by_zmig"] is True
    assert need["new"]["site"].startswith("warning: CMSIS_CORE_HAS_SYSTEM_CORE_CLOCK")
    assert need["new"]["declared"].startswith("west.yml:")
