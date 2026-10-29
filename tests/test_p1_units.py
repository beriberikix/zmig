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
    assert doc["schema_version"] == "1.4"
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


# --- verdict (§8) ----------------------------------------------------------------------------


def _f(
    cat: Category, kind: str, sev: Severity, remedy: Remedy | None, intro: bool = True
) -> Finding:
    return Finding(
        category=cat,
        kind=kind,
        subject=kind,
        title=kind,
        severity=sev,
        confidence=Confidence.HIGH,
        action="",
        analyzer=cat.value,
        scope="s",
        remedy=remedy,
        introduced_by_migration=intro,
    )


def _clean_shared() -> dict[str, Any]:
    cats = {
        n: {"status": "clean", "confidence": "high", "checked": ["x"]}
        for n in ("layout", "mcuboot", "storage", "ble", "wire")
    }
    return {"categories": cats}


def _ok() -> list[Any]:
    from zmig.findings import AnalyzerStatus
    from zmig.verdict import CATEGORIES

    return [AnalyzerStatus(a, "ok") for a in CATEGORIES.values()]


def test_verdict_precedence_and_never_default_ok() -> None:
    from zmig.findings import AnalyzerStatus
    from zmig.verdict import upgrade_path

    assert upgrade_path(_clean_shared(), [], _ok())["verdict"] == "direct-ok"
    # A category whose analyzer didn't run is unknown, so the verdict is unknown.
    statuses = [s for s in _ok() if s.name != "ble"]
    u = upgrade_path(_clean_shared(), [], statuses)
    assert u["verdict"] == "unknown" and u["categories"]["ble"]["status"] == "unknown"
    # A failed per-image run counts as not run.
    u = upgrade_path(_clean_shared(), [], [*_ok(), AnalyzerStatus("layout@mcuboot", "error", "x")])
    assert u["categories"]["layout"]["status"] == "unknown"
    # needs-changes beats unknown; blocked beats needs-changes.
    pin = _f(Category.MCUBOOT, "m", Severity.BREAKING, Remedy.CONFIG_PIN)
    assert upgrade_path(_clean_shared(), [pin], statuses)["verdict"] == "needs-changes"
    unk = _f(Category.STORAGE, "s", Severity.RISKY, Remedy.UNKNOWN)
    assert upgrade_path(_clean_shared(), [unk], _ok())["verdict"] == "needs-changes"
    rf = _f(Category.LAYOUT, "l", Severity.BREAKING, Remedy.REFLASH)
    u = upgrade_path(_clean_shared(), [pin, rf], _ok())
    assert u["verdict"] == "blocked"
    assert u["remedies"] == {"config-pin": [pin.id], "reflash": [rf.id]}
    # Info findings with a remedy are not hazards.
    info = _f(Category.WIRE, "w", Severity.INFO, Remedy.CONFIG_PIN)
    assert upgrade_path(_clean_shared(), [info], _ok())["verdict"] == "direct-ok"


def test_verdict_bootloader_update_needs_evidenced_updatability() -> None:
    from zmig.verdict import upgrade_path

    bu = _f(Category.LAYOUT, "l", Severity.BREAKING, Remedy.BOOTLOADER_UPDATE)
    u = upgrade_path(_clean_shared(), [bu], _ok())
    assert u["remedies"] == {"reflash": [bu.id]} and u["verdict"] == "blocked"
    shared = _clean_shared()
    shared["categories"]["mcuboot"]["updatable"] = "yes"
    assert upgrade_path(shared, [bu], _ok())["remedies"] == {"bootloader-update": [bu.id]}


def test_verdict_pre_existing_breaking() -> None:
    """§8.3: the touched categories become unknown and confidence is capped at low."""
    from zmig.verdict import upgrade_path

    pre = _f(Category.CROSSIMAGE, "partition", Severity.BREAKING, None, intro=False)
    u = upgrade_path(_clean_shared(), [pre], _ok())
    assert u["verdict"] == "unknown" and u["confidence"] == "low"
    assert u["categories"]["crossimage"]["status"] == "unknown"
    assert u["categories"]["layout"]["status"] == "unknown"
    assert any("contradicted by" in a for a in u["assumptions"])
    # A proven hazard elsewhere still stands.
    rf = _f(Category.MCUBOOT, "m", Severity.BREAKING, Remedy.REFLASH)
    u = upgrade_path(_clean_shared(), [pre, rf], _ok())
    assert u["verdict"] == "blocked" and u["confidence"] == "low"


def test_report_rendering_separates_pre_existing_and_lists_not_checked() -> None:
    ref = {"spec": "v1", "sha": "a" * 40, "kind": "tag", "url": "u", "dirty": False}
    new = _f(Category.LAYOUT, "partition-moved", Severity.RISKY, Remedy.REFLASH)
    pre = _f(Category.CROSSIMAGE, "partition", Severity.BREAKING, None, intro=False)
    r = Report(
        command="scan",
        zmig_version="0",
        inputs={"from": ref, "to": ref},
        environment={},
        findings=[pre, new],
        not_checked=[NotChecked("bootloaders other than MCUboot", "only MCUboot", "mcuboot")],
        upgrade_path={
            "verdict": "blocked",
            "confidence": "low",
            "categories": {
                "layout": {"status": "hazard", "confidence": "high", "findings": [new.id]}
            },
            "remedies": {"reflash": [new.id]},
            "assumptions": ["deployed == source build: contradicted by x"],
            "basis": [],
        },
    )
    # Introduced findings first, even when a pre-existing one is more severe.
    assert r.top()[0] is new
    md = render(r, "markdown")
    assert "**Verdict: blocked** (low confidence)" in md
    assert f"- **reflash**: `{new.id}`" in md
    assert "## Not checked" in md and "bootloaders other than MCUboot" in md
    pre_section = md.split("## Already true of the source build", 1)[1]
    assert pre.id in pre_section
    assert (
        pre.id
        not in md.split("## Already true of the source build", 1)[0]
        .split("## Do these first", 1)[1]
        .split("\n\n", 2)[2]
    )
    human = render(r, "human")
    assert "Field-upgrade verdict: blocked (low confidence); remedies: reflash x1" in human


def test_verdict_with_unchecked_categories_is_a_lower_bound() -> None:
    from zmig.verdict import upgrade_path

    shared = _clean_shared()
    shared["categories"]["storage"] = {"status": "unknown", "confidence": "low", "checked": []}
    pin = _f(Category.MCUBOOT, "m", Severity.BREAKING, Remedy.CONFIG_PIN)
    u = upgrade_path(shared, [pin], _ok())
    assert u["verdict"] == "needs-changes" and u["unchecked"] == ["storage"]
    assert any("lower bound" in b for b in u["basis"])


def test_verdict_confidence_low_when_the_target_did_not_build() -> None:
    """bms-firmware native_sim/OLED were direct-ok (medium) with failed target builds
    (docs/DOGFOOD-3-baseline.md M11)."""
    from zmig.verdict import upgrade_path

    ok = upgrade_path(_clean_shared(), [], _ok())
    failed = upgrade_path(_clean_shared(), [], _ok(), target_status="build-failed")
    assert ok["confidence"] != "low"
    assert failed["verdict"] == "direct-ok" and failed["confidence"] == "low"
    assert any(b.startswith("the target build failed") for b in failed["basis"])
