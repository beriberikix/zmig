"""Misleading output found by the DOGFOOD-5 baseline (OpenDeck), M17-M20, on its real
report and real Zephyr header excerpts. Fixed after the baseline; not analyzer tuning."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

FIX = Path(__file__).parent / "fixtures" / "opendeck"


def report() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIX / "scan-report.json").read_text())
    return data


# (macro, the header it was declared in at the source, its arity there)
MACROS = [
    ("UMP_NUM_WORDS", "zephyr/include/zephyr/audio/midi.h:96", 1),
    ("UMP_MT", "zephyr/include/zephyr/audio/midi.h:78", 1),
    ("HTTP_SERVICE_DEFINE", "zephyr/include/zephyr/net/http/service.h:260", 8),
    ("DNS_SD_REGISTER_SERVICE", "zephyr/include/zephyr/net/dns_sd.h:112", 7),
    ("CONTAINER_OF", "zephyr/include/zephyr/sys/util.h:281", 3),
    ("__ASSERT", "zephyr/include/zephyr/sys/__assert.h:140", 3),
    ("DT_PHANDLE", "zephyr/include/zephyr/devicetree.h:1922", 2),
]


@pytest.mark.parametrize(("name", "where", "arity"), MACROS)
def test_m17_macro_fallback_matches_the_whole_name_and_multiline_params(
    name: str, where: str, arity: int
) -> None:
    """The 7 risky 'macro changed' findings of the baseline were false: the fallback took
    the first #define with that prefix (CONTAINER_OF_VALIDATE for CONTAINER_OF), and
    counted DNS_SD_REGISTER_SERVICE's parameters from its first line only."""
    from zmig.analyzers.api import _macro_at

    assert name in {f["subject"] for f in report()["findings"] if f["kind"] == "macro-changed"}
    got = _macro_at(SimpleNamespace(root=FIX / "headers"), where, name)
    assert got is not None and got["function_like"]
    assert got["tokens"].split("(")[0].split()[-1] == name, got["tokens"]
    assert got["arity"] == arity


def test_m18_configure_failure_shows_the_first_real_error() -> None:
    """The baseline's target-configure-failed finding showed sysbuild's wrapper ("CMake
    configure failed for Zephyr project: app"), not the devicetree error above it."""
    from zmig.analyzers.buildlog import configure_error

    old = next(f for f in report()["findings"] if f["kind"] == "target-configure-failed")
    assert "undefined node label" not in json.dumps(old["evidence"])
    image, lines = configure_error((FIX / "target-configure.log").read_text()) or (None, [])
    assert image == "app"
    assert lines[0] == (
        "devicetree error: /soc/ethernet@40028000: undefined node label 'eth_rxd0_pc4'"
    )


def test_m19_node_changes_in_images_without_a_device_for_the_node(tmp_path: Path) -> None:
    """MCUboot (no networking) got risky '&mac changed' / '&mdio changed' findings, and the
    bootloader (ADC off) '&adc1' / '&adc2': no device for those nodes exists there."""
    import shutil
    import subprocess

    from zmig.analyzers.devicetree import _no_device, device_ords
    from zmig.buildctx import Artifact

    base = report()
    hit = {(f.get("image"), f["subject"].split(":")[0]) for f in base["findings"]
           if f["kind"] == "node-changed" and f["severity"] == "risky"}  # fmt: skip
    assert {("mcuboot", "&mac"), ("mcuboot", "&mdio"), ("opendeck_bootloader", "&adc1")} <= hit
    cc = shutil.which("cc") or shutil.which("gcc")
    if cc is None:
        pytest.skip("no host C compiler to make an ELF")
    src = tmp_path / "d.c"
    src.write_text("const int __device_dts_ord_12 = 1;\nconst int __device_dts_ord_40 = 1;\n")
    elf = tmp_path / "zephyr.elf"
    subprocess.run([cc, "-c", "-o", str(elf), str(src)], check=True)
    assert device_ords(elf) == {12, 40}
    img = SimpleNamespace(has=lambda a: a is Artifact.ELF, artifact=lambda a: elf)
    ctx = SimpleNamespace(old_img=img, new_img=img)
    mac = {"compats": ["st,stm32-ethernet"], "ord": 77}  # no device: MCUboot's &mac
    eth = {"compats": ["st,stm32-ethernet"], "ord": 40}  # has a device: the bootloader's
    assert _no_device(ctx, mac, mac)  # type: ignore[arg-type]
    assert not _no_device(ctx, eth, eth)  # type: ignore[arg-type]
    assert not _no_device(ctx, {"compats": [], "ord": 77}, {"compats": [], "ord": 77})  # type: ignore[arg-type]
    gone = SimpleNamespace(has=lambda a: False, artifact=lambda a: elf)  # target didn't link
    assert not _no_device(SimpleNamespace(old_img=img, new_img=gone), mac, mac)  # type: ignore[arg-type]


def test_m20_parse_coverage_counts_are_per_image() -> None:
    """The app's record said '51 project TU(s) ... 0 parsed': the counts were MCUboot's,
    stored first in the dict every sysbuild image shares."""
    from zmig.analyzers.api import parse_stats, record_parse_stats

    rec = next(
        c for c in report()["checked"]
        if c["area"] == "libclang parse coverage" and "51 project TU" in c["detail"]
    )  # fmt: skip
    assert "0 parsed, 0 from the parse cache" in rec["detail"]
    shared: dict[str, Any] = {}
    record_parse_stats(shared, "mcuboot", {"parsed": 0, "cached": 0, "unchanged": 0})
    record_parse_stats(shared, "app", {"parsed": 51, "cached": 0, "unchanged": 0})
    assert parse_stats(shared, "app")["parsed"] == 51
    assert parse_stats(shared, "mcuboot")["parsed"] == 0
    assert parse_stats(shared, "opendeck_bootloader") == {}
