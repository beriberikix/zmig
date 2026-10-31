"""zmig.fixes: where the fix goes, and the edit when a finding's own facts give it
(DESIGN.md, "The `fix` field"). Shapes are taken from findings on the oracle projects."""

# ruff: noqa: E501  (verbatim compiler messages and Kconfig help text)

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from zmig.findings import Category, Confidence, Finding, Severity, file_line, tool_output
from zmig.fixes import apply_fixes


def _f(kind: str, cat: Category, ev: tuple[Any, ...], **kw: Any) -> Finding:
    return Finding(
        category=cat,
        kind=kind,
        subject=kw.pop("subject", kind),
        title=kw.pop("title", "t"),
        severity=kw.pop("severity", Severity.BREAKING),
        confidence=kw.pop("confidence", Confidence.HIGH),
        action=kw.pop("action", "Find its replacement in the migration guides."),
        analyzer=kw.pop("analyzer", "x"),
        scope=("s",),
        evidence=ev,
        **kw,
    )


CTX = SimpleNamespace(old_img=SimpleNamespace(has=lambda a: False))


def test_kconfig_rename_from_the_deprecated_symbols_help_text() -> None:
    """CoffeeCaller C2: the exact line, and Zephyr's own "use X instead"."""
    f = _f(
        "symbol-removed",
        Category.KCONFIG,
        (file_line("boards/extensions/native_sim/native_sim.conf", 11, "CONFIG_NATIVE_UART_0_ON_STDINOUT=y"),),
        subject="CONFIG_NATIVE_UART_0_ON_STDINOUT",
        action="Remove the assignment, or find its replacement in the migration guides.",
        new={"candidates": [{"symbol": "UART_NATIVE_PTY_0_ON_STDINOUT", "confidence": "medium",
                             "why": "the deprecated NATIVE_UART_0_ON_STDINOUT's help text says to use UART_NATIVE_PTY_0_ON_STDINOUT"}]},
    )  # fmt: skip
    (g,) = apply_fixes([f], CTX)  # type: ignore[arg-type]
    assert g.fix == {
        "file": "boards/extensions/native_sim/native_sim.conf",
        "line": 11,
        "old": "CONFIG_NATIVE_UART_0_ON_STDINOUT=y",
        "new": "CONFIG_UART_NATIVE_PTY_0_ON_STDINOUT=y",
        "source": "kconfig: the deprecated NATIVE_UART_0_ON_STDINOUT's help text says to use "
        "UART_NATIVE_PTY_0_ON_STDINOUT",
        "confidence": "medium",
    }
    assert g.action.startswith(
        "In boards/extensions/native_sim/native_sim.conf:11, change "
        "`CONFIG_NATIVE_UART_0_ON_STDINOUT=y` to `CONFIG_UART_NATIVE_PTY_0_ON_STDINOUT=y`"
    )
    assert "find its replacement" not in g.action  # the generic advice goes when we know


def test_kconfig_rename_from_a_related_guide_entry() -> None:
    k = _f("symbol-removed", Category.KCONFIG, (file_line("prj.conf", 3, "CONFIG_OLD_NAME=5"),),
           subject="CONFIG_OLD_NAME", related=("GUIDE-ENTRY-MATCH-1",))  # fmt: skip
    gd = _f("entry-match", Category.GUIDE, (file_line("prj.conf", 3, "CONFIG_OLD_NAME=5"),),
            subject="MG-4.2-0001:kconfig:OLD_NAME", confidence=Confidence.MEDIUM,
            new={"entry": "MG-4.2-0001", "item": {"kind": "kconfig", "value": "OLD_NAME"}, "renamed_to": "NEW_NAME"})  # fmt: skip
    gd = replace(gd, related=(k.id,))
    k = replace(k, related=(gd.id,))
    out = {g.kind: g for g in apply_fixes([k, gd], CTX)}  # type: ignore[arg-type]
    assert out["symbol-removed"].fix is not None
    assert (out["symbol-removed"].fix["old"], out["symbol-removed"].fix["new"]) == (
        "CONFIG_OLD_NAME=5",
        "CONFIG_NEW_NAME=5",
    )
    assert out["symbol-removed"].fix["source"] == "guide: MG-4.2-0001"
    assert out["entry-match"].fix is not None and out["entry-match"].fix["new"] == "CONFIG_NEW_NAME"


def test_callback_definition_gets_the_expected_signature() -> None:
    """ZMK #2: the definition sites and the type Zephyr expects are already known."""
    f = _f("callback-type-changed", Category.API,
           (file_line("src/activity.c", 87, "activity_init: definition to change"),
            file_line("src/activity.c", 112, "int (*)(void)")),
           new={"expected": "int (*)(void)",
                "message": "incompatible function pointer types initializing 'int (*)(void)' with an expression of type 'int (const struct device *)'"},
           action="Change their signatures to match.")  # fmt: skip
    (g,) = apply_fixes([f], CTX)  # type: ignore[arg-type]
    assert g.fix is not None
    assert (g.fix["file"], g.fix["line"]) == ("src/activity.c", 87)
    assert (g.fix["old"], g.fix["new"]) == ("int (const struct device *)", "int (void)")
    assert "activity_init" in g.action and "src/activity.c:87" in g.action


def test_compiler_did_you_mean_fixes_a_removed_enum() -> None:
    """bms B7: clang/GCC already named the replacement."""
    enum = _f("enum-removed", Category.API, (file_line("src/oled.c", 109, "CFB_DISPLAY_HEIGH"),),
              subject="CFB_DISPLAY_HEIGH")  # fmt: skip
    build = _f("target-build-failed", Category.BUILD, (file_line("src/oled.c", 109, "x"),),
               new={"errors": [{"kind": "compiler", "file": "<app>/src/oled.c", "line": 109,
                                "message": "'CFB_DISPLAY_HEIGH' undeclared (first use in this function); did you mean 'CFB_DISPLAY_HEIGHT'?"}]},
               action="The Kconfig and devicetree findings often explain these errors.")  # fmt: skip
    out = {g.kind: g for g in apply_fixes([enum, build], CTX)}  # type: ignore[arg-type]
    for k in ("enum-removed", "target-build-failed"):
        fx = out[k].fix
        assert fx is not None and (fx["file"], fx["line"]) == ("src/oled.c", 109)
        assert (fx["old"], fx["new"]) == ("CFB_DISPLAY_HEIGH", "CFB_DISPLAY_HEIGHT")
        assert fx["source"] == "compiler: did you mean" and fx["confidence"] == "medium"


def test_location_only_when_the_edit_is_unknown() -> None:
    f = _f("new-warning", Category.BUILD, (file_line("src/a.c", 5, "x"), file_line("src/b.c", 9, "y"),
                                           file_line("src/a.c", 7, "z")),
           severity=Severity.RISKY, action="Check whether it points at a real problem.")  # fmt: skip
    (g,) = apply_fixes([f], CTX)  # type: ignore[arg-type]
    assert g.fix is not None
    assert g.fix == {"file": "src/a.c", "line": 5, "old": None, "new": None, "source": "evidence",
                     "confidence": "high"}  # fmt: skip
    assert g.action == "In src/a.c:5, src/b.c:9: check whether it points at a real problem."


def test_no_project_location_no_fix() -> None:
    zephyr_only = _f("partition-moved", Category.LAYOUT,
                     (file_line("<v4.1.0 hello>/zephyr/zephyr.dts", 3, "x"), tool_output("t", "y")),
                     action="A deployed device keeps the old layout.")  # fmt: skip
    bulk = _f("newly-set", Category.KCONFIG, (), severity=Severity.INFO, action="Review the list.")
    out = apply_fixes([zephyr_only, bulk], CTX)  # type: ignore[arg-type]
    assert [g.fix for g in out] == [None, None]
    assert [g.action for g in out] == [zephyr_only.action, bulk.action]


def test_only_action_and_fix_change() -> None:
    """The invariant Phase 0's measurement depends on: no finding is added, dropped or
    re-graded, so recall and precision can't move (DESIGN.md, "The `fix` field")."""
    fs = [
        _f("symbol-removed", Category.KCONFIG, (file_line("prj.conf", 1, "CONFIG_A=y"),), subject="CONFIG_A",
           new={"candidates": [{"symbol": "B", "why": "w", "confidence": "low"}]}),
        _f("new-warning", Category.BUILD, (file_line("src/a.c", 5, "x"),), severity=Severity.RISKY),
        _f("partition-moved", Category.LAYOUT, ()),
    ]  # fmt: skip
    out = apply_fixes(fs, CTX)  # type: ignore[arg-type]
    assert [g.id for g in out] == [f.id for f in fs]
    for a, b in zip(fs, out, strict=True):
        assert replace(b, action=a.action, fix=None) == a


def test_callback_expected_type_is_canonical_not_a_typedef_name() -> None:
    """DOGFOOD-9 D2, CANnectivity N1: clang's message names the typedef and gives the
    canonical type in "aka"; a function can't be given a typedef'd pointer type."""
    f = _f("callback-type-changed", Category.API,
           (file_line("src/led.c", 192, "led_state_normal_run: definition to change"),),
           new={"expected": "const state_execution",
                "message": "incompatible function pointer types initializing 'const state_execution' (aka 'enum smf_state_result (*const)(void *)') with an expression of type 'void (void *)'"})  # fmt: skip
    (g,) = apply_fixes([f], CTX)  # type: ignore[arg-type]
    assert g.fix is not None
    assert (g.fix["old"], g.fix["new"]) == ("void (void *)", "enum smf_state_result (void *)")
    assert "led_state_normal_run" in g.action and "state_execution" not in g.action


def test_guide_rename_only_on_a_line_that_has_the_old_name() -> None:
    """DOGFOOD-9 D1, RIT FSW F1: the guide's rename was placed at a line that uses the node
    (`DEVICE_DT_GET(DT_ALIAS(thermometer))`), not one with `ti,tmp116`."""
    use = file_line("src/c_sensing_tenant.cpp", 20, "DEVICE_DT_GET(DT_ALIAS(thermometer))")
    new = {
        "entry": "MG-4.2-0083",
        "item": {"kind": "compat", "value": "ti,tmp116"},
        "renamed_to": "ti,tmp11x",
    }
    only_use = _f("entry-match", Category.GUIDE, (use,), confidence=Confidence.MEDIUM, new=new,
                  action='The guide says ti,tmp116 was renamed to ti,tmp11x: "…"')  # fmt: skip
    (g,) = apply_fixes([only_use], CTX)  # type: ignore[arg-type]
    assert g.fix is not None and g.fix["old"] is None and g.fix["new"] is None
    assert g.fix["file"] == "src/c_sensing_tenant.cpp"
    assert "change `ti,tmp116`" not in g.action
    # A line that does contain the old name gets the edit, wherever it is in the evidence.
    dts = file_line("boards/arm/x/x.dts", 202, 'compatible = "ti,tmp116";')
    both = replace(only_use, evidence=(use, dts))
    (h,) = apply_fixes([both], CTX)  # type: ignore[arg-type]
    assert h.fix is not None
    assert (h.fix["file"], h.fix["line"], h.fix["old"], h.fix["new"]) == (
        "boards/arm/x/x.dts",
        202,
        "ti,tmp116",
        "ti,tmp11x",
    )


def test_no_location_prefix_when_the_action_already_names_it() -> None:
    """ "In ../west.yml:18: add cmsis_6 to your manifest in ../west.yml:18 …" said it twice."""
    f = _f("module-required", Category.MANIFEST, (file_line("../west.yml", 18, "name-allowlist:"),),
           action="Add cmsis_6 to your manifest in ../west.yml:18 (the name-allowlist) and run `west update`.")  # fmt: skip
    (g,) = apply_fixes([f], CTX)  # type: ignore[arg-type]
    assert g.action == f.action
    assert g.fix is not None and (g.fix["file"], g.fix["line"]) == ("../west.yml", 18)


def test_an_orphaned_override_gets_its_rename_as_a_kconfig_line() -> None:
    """ardep A1: `config CAN_MAX_STD_ID_FILTER` in the board's Kconfig.defconfig."""
    f = _f("kconfig-override-orphaned", Category.BUILD,
           (file_line("ardep/boards/mercedes/ardep/Kconfig.defconfig", 17, "config CAN_MAX_STD_ID_FILTER"),),
           subject="CONFIG_CAN_MAX_STD_ID_FILTER",
           new={"candidates": [{"symbol": "CAN_STM32_FDCAN_MAX_STD_ID_FILTERS", "confidence": "medium",
                                "why": "renamed in 38c329742146; defined if CAN_STM32_FDCAN"}]})  # fmt: skip
    (g,) = apply_fixes([f], CTX)  # type: ignore[arg-type]
    assert g.fix is not None
    assert (g.fix["old"], g.fix["new"]) == (
        "config CAN_MAX_STD_ID_FILTER",
        "config CAN_STM32_FDCAN_MAX_STD_ID_FILTERS",
    )
    assert g.fix["confidence"] == "medium"


def test_a_read_of_a_removed_symbol_gets_the_rename() -> None:
    f = _f("read-symbol-removed", Category.KCONFIG, (file_line("src/main.c", 4, "#ifdef CONFIG_OLD"),),
           subject="CONFIG_OLD", new={"candidates": [{"symbol": "NEW", "confidence": "medium", "why": "w"}]})  # fmt: skip
    (g,) = apply_fixes([f], CTX)  # type: ignore[arg-type]
    assert g.fix is not None and (g.fix["old"], g.fix["new"]) == (
        "#ifdef CONFIG_OLD",
        "#ifdef CONFIG_NEW",
    )


def test_a_missing_include_is_an_include_to_add() -> None:
    """DOGFOOD-12 E4: the fix for `include-missing` is the #include line, at the file's
    include block (ble-copro B1/B2's edit)."""
    from zmig.findings import Category, Confidence, Finding, Severity, file_line
    from zmig.fixes import apply_fixes

    f = Finding(Category.BUILD, "include-missing", "src/stream_client.c:zephyr/logging/log.h",
                "t", Severity.BREAKING, Confidence.MEDIUM, "Include it.", "buildlog", ("s",),
                evidence=(file_line("src/stream_client.c", 4, "#include <zephyr/net/socket.h>"),),
                new={"header": "zephyr/logging/log.h", "symbols": ["LOG_ERR"]})  # fmt: skip
    (fixed,) = apply_fixes([f], None)  # type: ignore[arg-type]
    assert fixed.fix["file"] == "src/stream_client.c" and fixed.fix["line"] == 4
    assert fixed.fix["old"] is None and fixed.fix["new"] == "#include <zephyr/logging/log.h>"
    assert fixed.action.startswith(
        "In src/stream_client.c:4, add `#include <zephyr/logging/log.h>`"
    )


def test_a_single_new_dependency_is_a_line_to_add() -> None:
    """DOGFOOD-13 F3: an assignment that's no longer taken because of one new, settable,
    off dependency gets that dependency as the line to add (edgehog H6:
    CONFIG_MBEDTLS_X509_CRT_PARSE_C=y, which selects X509_USE_C)."""
    from zmig.findings import Category, Confidence, Finding, Severity, file_line
    from zmig.fixes import apply_fixes

    f = Finding(Category.KCONFIG, "value-not-taken", "MBEDTLS_KEY_EXCHANGE_ECDHE_ECDSA_ENABLED",
                "t", Severity.BREAKING, Confidence.HIGH, "It now needs ...", "kconfig", ("s",),
                evidence=(file_line("prj.conf", 83, "CONFIG_MBEDTLS_KEY_EXCHANGE_ECDHE_ECDSA_ENABLED=y"),),
                new={"assigned": "y", "got": "n", "new_dependencies": ["MBEDTLS_X509_CRT_PARSE_C"]})  # fmt: skip
    (fixed,) = apply_fixes([f], None)  # type: ignore[arg-type]
    assert (fixed.fix["file"], fixed.fix["line"]) == ("prj.conf", 83)
    assert fixed.fix["old"] is None and fixed.fix["new"] == "CONFIG_MBEDTLS_X509_CRT_PARSE_C=y"
