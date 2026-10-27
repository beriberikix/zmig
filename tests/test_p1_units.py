"""P1 building blocks: SDK verification, schema 1.2 fields, image pairing, sysbuild."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from zmig.analyzers.sysbuild import SysbuildAnalyzer, user_settings
from zmig.buildctx import BuildContext, BuildSpec, ImageContext, pair_images, verify_sdk
from zmig.errors import ZmigError
from zmig.findings import Category, Confidence, Finding, NotChecked, Remedy, Report, Severity
from zmig.report import render

SCHEMA = json.loads((Path(__file__).parents[1] / "zmig/schema/report-1.json").read_text())


def test_verify_sdk(tmp_path: Path) -> None:
    sdk = tmp_path / "zephyr-sdk-0.17.4"
    sdk.mkdir()
    ok = f"-- Found toolchain: zephyr 0.17.4 ({sdk})\n-- Found toolchain: zephyr 0.17.4 ({sdk})\n"
    verify_sdk(ok, sdk)
    other = ok + "-- Found toolchain: zephyr 1.0.1 (/home/u/sdk)\n"
    with pytest.raises(ZmigError, match="different Zephyr SDK"):
        verify_sdk(other, sdk)
    with pytest.raises(ZmigError, match="could not confirm"):
        verify_sdk("-- Board: x\n", sdk)


def test_schema_12_fields_validate() -> None:
    ref = {"spec": "v1", "sha": "a" * 40, "kind": "tag", "dirty": False}
    f = Finding(
        category=Category.LAYOUT,
        kind="partition-moved",
        subject="slot0_partition",
        title="moved",
        severity=Severity.BREAKING,
        confidence=Confidence.HIGH,
        action="reflash",
        analyzer="layout",
        scope=("a",),
        remedy=Remedy.REFLASH,
        introduced_by_migration=False,
        provenance={"evidence_source": "x", "role_source": "y", "role_evidence": "weak"},
    )
    r = Report(
        command="scan",
        zmig_version="0",
        inputs={"from": ref, "to": ref},
        environment={"python": "3", "sdk": {"path": "/s", "version": "0.17.4"}},
        findings=[f],
        not_checked=[NotChecked("ble", "not built", "ble")],
        upgrade_path={
            "verdict": "blocked",
            "confidence": "low",
            "categories": {"layout": {"status": "hazard"}},
            "assumptions": ["deployed == source"],
        },
    )
    doc = json.loads(render(r, "json"))
    jsonschema.validate(doc, SCHEMA)
    assert doc["schema_version"] == "1.2"
    (j,) = doc["findings"]
    assert (j["introduced_by_migration"], j["remedy"]) == (False, "reflash")


def _img(name: str, role: str) -> ImageContext:
    return ImageContext(
        name,
        role,
        "test",
        Path(f"/b/{name}"),
        Path("/b/l"),
        "ok",  # type: ignore[arg-type]
        "full",
        "b",
        "v",
    )


def _build(*imgs: ImageContext) -> BuildContext:
    return BuildContext(
        None,
        BuildSpec(app=Path("/a"), board="b"),
        "k",
        Path("/b"),  # type: ignore[arg-type]
        "ok",
        Path("/l"),
        Path("/s"),
        imgs,
    )


def test_pair_images_by_role_then_name() -> None:
    old = _build(_img("mcuboot", "bootloader"), _img("app", "app"), _img("net", "other"))
    new = _build(_img("bootloader", "bootloader"), _img("app", "app"), _img("net", "other"))
    pairs = [(o.name, n.name) for o, n in pair_images(old, new)]
    assert pairs == [("mcuboot", "bootloader"), ("app", "app"), ("net", "net")]


def test_sysbuild_user_settings_and_replacement(tmp_path: Path) -> None:
    (tmp_path / "sysbuild.conf").write_text(
        "# comment\nSB_CONFIG_BOOTLOADER_MCUBOOT=y\nSB_CONFIG_MCUBOOT_MODE_SWAP_WITHOUT_SCRATCH=y\n"
    )
    u = user_settings(tmp_path)
    assert u["MCUBOOT_MODE_SWAP_WITHOUT_SCRATCH"][:2] == (3, "y")
    # Shape of v4.1.0 share/sysbuild/images/bootloader/Kconfig:61-65.
    nk = {
        "MCUBOOT_MODE_SWAP_WITHOUT_SCRATCH": {
            "deprecated": True,
            "help": "This Kconfig is deprecated, use MCUBOOT_MODE_SWAP_USING_MOVE instead.",
        },
        "MCUBOOT_MODE_SWAP_USING_MOVE": {"deprecated": False, "help": ""},
    }
    assert (
        SysbuildAnalyzer._replacement("MCUBOOT_MODE_SWAP_WITHOUT_SCRATCH", {}, nk)
        == "MCUBOOT_MODE_SWAP_USING_MOVE"
    )


# --- regressions from running step 3 / layout on the P1 fixtures -----------------------


def test_sysbuild_values_normalize_per_ref_paths(tmp_path: Path) -> None:
    """SB_CONFIG_BOOT_SIGNATURE_KEY_FILE differed only by the per-ref workspace path."""
    from types import SimpleNamespace

    from zmig.analyzers.sysbuild import _normalized

    def build(ws: str) -> Any:
        return SimpleNamespace(workspace=SimpleNamespace(root=Path(ws)), dir=Path(f"{ws}-b"))

    key = "/bootloader/mcuboot/root-rsa-2048.pem"
    a = _normalized({"BOOT_SIGNATURE_KEY_FILE": f'"/c/ws/aaa{key}"'}, build("/c/ws/aaa"))
    b = _normalized({"BOOT_SIGNATURE_KEY_FILE": f'"/c/ws/bbb{key}"'}, build("/c/ws/bbb"))
    assert a == b == {"BOOT_SIGNATURE_KEY_FILE": f'"<workspace>{key}"'}


def test_derived_markers_are_not_reported_on_their_own() -> None:
    from zmig.analyzers.sysbuild import DERIVED

    assert {"DEPRECATED", "EXPERIMENTAL"} <= DERIVED


def test_role_hits_must_be_compiled() -> None:
    """storage_partition got the bootloader role from MCUboot's optional serial-recovery
    storage-erase command (boot_serial_extension_zephyr_basic.c), not compiled in."""
    from zmig.analyzers.layout import _counts

    compiled = {"boot/zephyr/main.c"}
    assert not _counts("boot/zephyr/boot_serial_extension_zephyr_basic.c", compiled)
    assert _counts("boot/zephyr/main.c", compiled)
    assert _counts("boot/zephyr/include/sysflash/sysflash.h", compiled)
    assert not _counts("boot/zephyr/Kconfig", compiled)
    # No SBOM: every hit counts (and the provenance says so).
    assert _counts("boot/zephyr/boot_serial_extension_zephyr_basic.c", None)


# --- MCUboot category units ------------------------------------------------------------


def test_c_condition_evaluator() -> None:
    from zmig.analyzers.mcuboot import _c_condition

    env = {"num_sectors_pri": 118, "num_sectors_sec": 118}
    # swap_offset.c:316 at the v4.3.0 pin
    cond = "num_sectors_pri != num_sectors_sec && (num_sectors_pri + 1) != num_sectors_sec"
    assert _c_condition(cond, env) == (False, None)
    assert _c_condition("(num_sectors_pri + 1) != num_sectors_sec", env) == (True, None)
    assert _c_condition("!(num_sectors_pri > num_sectors_sec)", env) == (True, None)
    # Unknown names, calls and non-integers are never evaluated.
    assert _c_condition("num_sectors_pri > BOOT_MAX_IMG_SECTORS", env)[0] is None
    assert _c_condition("x != f(state, i)", {"x": 1})[0] is None
    assert _c_condition("__import__('os')", env)[0] is None


def test_srcdiff_extended_and_decl() -> None:
    from zmig.srcdiff import Definition, Seed, compare

    seed = Seed("define-prefix", "BOOT_STATUS_", "")
    old = [Definition("BOOT_STATUS_OP_SWAP", "define-prefix", "2", "a.h", 81)]
    new = [
        Definition("BOOT_STATUS_OP_SWAP", "define-prefix", "1", "a.h", 85),
        Definition("BOOT_STATUS_OP_SWAP", "define-prefix", "2", "a.h", 88),
    ]
    # A new conditional variant, the old one kept: reported, not "changed".
    assert compare(seed, old, new, "o", "n").status == "extended"
    assert compare(seed, new, old, "o", "n").status == "changed"
    s = Seed("struct", "image_header", "")
    m = (("uint32_t", "ih_magic", ""),)
    a = Definition("image_header", "struct", "uint32_t ih_magic;", "i.h", 1, m, "STRUCT_PACKED")
    b = Definition("image_header", "struct", "uint32_t ih_magic;", "i.h", 1, m, "struct")
    d = compare(s, [a], [b], "o", "n")
    assert d.status == "unchanged"
    assert d.names[0]["decl_changed"] == {"old": "STRUCT_PACKED", "new": "struct"}


def test_struct_locate_accepts_packing_macro(tmp_path: Path) -> None:
    """MCUboot's image.h at older pins declares `STRUCT_PACKED image_header {`."""
    import subprocess

    from zmig.srcdiff import Seed, Source
    from zmig.tools import Runner

    repo = tmp_path / "r"
    (repo / "inc").mkdir(parents=True)
    (repo / "inc" / "image.h").write_text(
        "#define STRUCT_PACKED struct __attribute__((__packed__))\n"
        "STRUCT_PACKED image_tlv {\n    uint16_t it_type;\n    uint16_t it_len;\n};\n"
        '_Static_assert(sizeof(struct image_tlv) == 4, "x");\n'
    )
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run(
        [*git, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], check=True
    )
    (d,) = Source(repo, "HEAD", Runner()).locate(Seed("struct", "image_tlv", ""), ["inc"])
    assert d.decl == "STRUCT_PACKED"
    assert d.members == (("uint16_t", "it_type", ""), ("uint16_t", "it_len", ""))
