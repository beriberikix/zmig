"""Log parsing, pinned to messages copied from real Zephyr v3.7.0 builds."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from zmig.diagnostics import deprecated_boards, parse_log

# Verbatim from a v3.7.0 configure (paths shortened).
UNDEF_LOG = """\
-- Found devicetree overlay: /work/app/boards/native_sim_64.overlay
Loaded configuration '/z/boards/native/native_sim/native_sim_64_defconfig'
Merged configuration '/work/app/prj.conf'
Merged configuration '/work/app/boards/native_sim_64.conf'
/work/app/prj.conf:2: warning: attempt to assign the value 'y' to the undefined symbol CPLUSPLUS
error: Aborting due to Kconfig warnings
"""
DT_LOG = """\
devicetree error: 'sjw' appears in /can in /b/zephyr/zephyr.dts.pre, but is not declared in \
'properties:' in /z/dts/bindings/can/zephyr,native-linux-can.yaml
"""
# Format from scripts/kconfig/kconfig.py check_assigned_sym_values().
NOT_TAKEN_LOG = """\
warning: NET_TCP (defined at subsys/net/ip/Kconfig:340) was assigned the value 'y' but got \
the value 'n'. Check these unsatisfied dependencies: NET_IPV4 (=n), (NETWORKING && FOO) (=n). \
See http://docs.zephyrproject.org/latest/kconfig.html#CONFIG_NET_TCP and/or look up NET_TCP in \
the menuconfig/guiconfig interface.

"""
# kconfig.py wraps its final error() at 100 columns.
PROMPTLESS_LOG = """\
error: CONFIG_HAS_DTS (defined at dts/Kconfig:5) is assigned in a configuration file, but is
not directly user-configurable (has no prompt). It gets its value indirectly from other symbols.
"""


def test_undefined_symbol_and_inputs() -> None:
    f = parse_log(UNDEF_LOG)
    (d,) = f.diagnostics
    assert (d.kind, d.symbol, d.value, d.file, d.line) == (
        "kconfig-undefined",
        "CPLUSPLUS",
        "y",
        "/work/app/prj.conf",
        2,
    )
    assert f.overlays == ("/work/app/boards/native_sim_64.overlay",)
    assert f.configs == ("/work/app/prj.conf", "/work/app/boards/native_sim_64.conf")
    assert f.base_config.endswith("native_sim_64_defconfig")


def test_dt_undeclared_property() -> None:
    (d,) = parse_log(DT_LOG).diagnostics
    assert (d.kind, d.symbol, d.node) == ("dt-undeclared-property", "sjw", "/can")
    assert d.binding.endswith("zephyr,native-linux-can.yaml")


def test_value_not_taken_with_deps() -> None:
    (d,) = parse_log(NOT_TAKEN_LOG).diagnostics
    assert (d.kind, d.symbol, d.value, d.got) == ("kconfig-not-taken", "NET_TCP", "y", "n")
    assert d.deps == "NET_IPV4 (=n), (NETWORKING && FOO) (=n)"


def test_promptless_wrapped() -> None:
    (d,) = parse_log(PROMPTLESS_LOG).diagnostics
    assert (d.kind, d.symbol) == ("kconfig-promptless", "HAS_DTS")


def test_deprecated_boards(tmp_path: Path) -> None:
    (tmp_path / "boards").mkdir()
    (tmp_path / "boards" / "deprecated.cmake").write_text(
        "set(native_sim_64_DEPRECATED\n    native_sim/native/64\n)\n"
        "set(nrf52dk_nrf52832_DEPRECATED nrf52dk/nrf52832)\n"
    )
    assert deprecated_boards(tmp_path) == {
        "native_sim_64": "native_sim/native/64",
        "nrf52dk_nrf52832": "nrf52dk/nrf52832",
    }
    assert deprecated_boards(tmp_path / "nope") == {}


# Verbatim from the MCUboot image of a v4.4.0 sysbuild for qemu_cortex_m0 (wrapped).
WRAPPED_LOG = """\
warning: QEMU_ICOUNT_SHIFT (defined at boards/Kconfig:81) was assigned the value '6' but got the
value ''. Check these unsatisfied dependencies: QEMU_ICOUNT (=n). See
http://docs.zephyrproject.org/latest/kconfig.html#CONFIG_QEMU_ICOUNT_SHIFT and/or look up
QEMU_ICOUNT_SHIFT in the menuconfig/guiconfig interface.

"""


def test_value_not_taken_wrapped() -> None:
    (d,) = parse_log(WRAPPED_LOG).diagnostics
    assert (d.kind, d.symbol, d.value, d.got, d.deps) == (
        "kconfig-not-taken",
        "QEMU_ICOUNT_SHIFT",
        "6",
        "",
        "QEMU_ICOUNT (=n)",
    )


# The same warning from the v4.1.0 and v4.2.0 configures of samples/subsys/settings on
# native_sim: only the "defined at" line moved (eval/AUDIT.md §5.2).
STDOUT_CONSOLE_LOG = """\
warning: STDOUT_CONSOLE (defined at lib/libc/Kconfig:{line}) was assigned the value 'y' but got the
value 'n'. Check these unsatisfied dependencies: (!(NATIVE_APPLICATION || (NATIVE_LIBRARY &&
EXTERNAL_LIBC))) (=n). See http://docs.zephyrproject.org/latest/kconfig.html#CONFIG_STDOUT_CONSOLE
and/or look up STDOUT_CONSOLE in the menuconfig/guiconfig interface.

"""


def test_preexisting_diagnostic_ignores_moved_definitions() -> None:
    from zmig.diagnostics import preexisting

    (src,) = parse_log(STDOUT_CONSOLE_LOG.format(line=156)).diagnostics
    (tgt,) = parse_log(STDOUT_CONSOLE_LOG.format(line=160)).diagnostics
    assert preexisting(tgt, [src])
    other = parse_log(NOT_TAKEN_LOG).diagnostics
    assert not preexisting(tgt, other)
    assert not preexisting(tgt, [])
    # A different outcome for the same symbol is not the same diagnostic.
    assert not preexisting(tgt, [replace(src, got="m")])


def test_preexisting_dt_error_ignores_build_paths() -> None:
    from zmig.diagnostics import preexisting

    msg = "devicetree error: {}/dts/common/x.dtsi:3 (column 1): parse error: expected ';'"
    (src,) = parse_log(msg.format("/c/ws-a/zephyr")).diagnostics
    (tgt,) = parse_log(msg.format("/c/ws-b/zephyr")).diagnostics
    assert src.kind == "dt-error"
    assert preexisting(tgt, [src])
    (other,) = parse_log("devicetree error: something else entirely").diagnostics
    assert not preexisting(tgt, [other])


# edtlib's wording at v3.6.0 and v4.2.0 (paths shortened); the node is dtlib's repr.
REQUIRED_LOG = (
    "devicetree error: 'phy-handle' is marked as required in 'properties:' in "
    "/z/dts/bindings/ethernet/st,stm32-ethernet.yaml, but does not appear in "
    "<Node /soc/ethernet@40028000 in '/b/zephyr/zephyr.dts.pre'>\n"
)


def test_required_property_missing_names_property_node_and_binding() -> None:
    """Guide 4.2 "st,stm32-ethernet now requires phy-handle" (eval/AUDIT.md §3 #11): the target
    configure fails, so there is no target edt.pickle to compare; the error is the only
    evidence. Kind stays dt-error so finding IDs don't change."""
    (d,) = parse_log(REQUIRED_LOG).diagnostics
    assert d.kind == "dt-error"
    assert (d.symbol, d.node) == ("phy-handle", "/soc/ethernet@40028000")
    assert d.binding == "/z/dts/bindings/ethernet/st,stm32-ethernet.yaml"


def test_kconfig_warning_identity_ignores_moved_definitions() -> None:
    """The buildlog analyzer reported the settings sample's STDOUT_CONSOLE warning as new at
    v4.2.0 plus "1 warning resolved": only lib/libc/Kconfig:156 -> :160 differed (the F1
    class of eval/AUDIT.md, in BuildWarning.key)."""
    from zmig.diagnostics import parse_warnings

    (old,) = parse_warnings(STDOUT_CONSOLE_LOG.format(line=156))
    (new,) = parse_warnings(STDOUT_CONSOLE_LOG.format(line=160))
    assert old.source == "kconfig"
    assert old.key({}) == new.key({})
    other = STDOUT_CONSOLE_LOG.format(line=156).replace("got the\nvalue 'n'", "got the\nvalue 'm'")
    assert parse_warnings(other)[0].key({}) != new.key({})
