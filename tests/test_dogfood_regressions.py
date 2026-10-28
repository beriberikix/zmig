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
