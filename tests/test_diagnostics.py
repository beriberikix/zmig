"""Log parsing, pinned to messages copied from real Zephyr v3.7.0 builds."""

from __future__ import annotations

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
