"""zmig.changes: Kconfig facts from the tree alone (docs/DESIGN-CHANGE-DB.md, step 1).
Texts are taken from Zephyr's history."""

from __future__ import annotations

# ruff: noqa: E501  (verbatim git output)
import os
from pathlib import Path

import pytest

from zmig.changes import conditions, kconfig_changes, parse_pairs, parse_table, rank

# `git log -M -p -U0` of 231b2a0c19f "drivers: sensor: rename tmp116 to tmp11x" (RIT FSW F1c),
# trimmed to the Kconfig hunks.
TMP_LOG = """\
commit 231b2a0c19f1
diff --git a/drivers/eeprom/Kconfig.tmp116 b/drivers/eeprom/Kconfig.tmp116
--- a/drivers/eeprom/Kconfig.tmp116
+++ /dev/null
@@ -4,7 +3,0 @@
-config EEPROM_TMP116
diff --git a/drivers/eeprom/Kconfig.tmp11x b/drivers/eeprom/Kconfig.tmp11x
--- /dev/null
+++ b/drivers/eeprom/Kconfig.tmp11x
@@ -0,0 +4,7 @@
+config EEPROM_TMP11X
diff --git a/drivers/sensor/ti/tmp116/Kconfig b/drivers/sensor/ti/tmp11x/Kconfig
--- a/drivers/sensor/ti/tmp116/Kconfig
+++ b/drivers/sensor/ti/tmp11x/Kconfig
@@ -1 +1 @@
-# TMP116 temperature sensor configuration options
+# TMP11X temperature sensor configuration options
@@ -6 +6 @@
-config TMP116
+config TMP11X
@@ -9 +9 @@
-	depends on DT_HAS_TI_TMP116_ENABLED
+	depends on DT_HAS_TI_TMP11X_ENABLED
"""

# drivers/can/Kconfig.stm32 at v4.4.0 (ardep A1), shortened: the FDCAN filter count is guarded
# by an enclosing `if`, not a `depends on`.
STM32_KCONFIG = """\
config CAN_STM32_BXCAN
	bool "STM32 bxCAN driver"
	depends on DT_HAS_ST_STM32_BXCAN_ENABLED

if CAN_STM32_BXCAN

config CAN_STM32_BXCAN_MAX_STD_ID_FILTERS
	int "Maximum number of standard (11-bit) ID filters"
	default 14

endif # CAN_STM32_BXCAN

config CAN_STM32_FDCAN
	bool "STM32 FDCAN driver"
	depends on DT_HAS_ST_STM32_FDCAN_ENABLED

if CAN_STM32_FDCAN

config CAN_STM32_FDCAN_MAX_STD_ID_FILTERS
	int "Maximum number of standard (11-bit) ID filters"
	default 28
	depends on !CAN_FD_MODE || SOC_SERIES_STM32H7X

endif # CAN_STM32_FDCAN
"""


def test_symbol_table_from_git_grep() -> None:
    out = (
        "v4.4.0:drivers/can/Kconfig.stm32:51:config CAN_STM32_FDCAN\n"
        "v4.4.0:drivers/can/Kconfig.stm32:60:config CAN_STM32_FDCAN_MAX_STD_ID_FILTERS\n"
        "v4.4.0:Kconfig.zephyr:12:menuconfig LOG\n"
        "v4.4.0:other/Kconfig:3:config LOG\n"  # defined twice: the first wins
    )
    assert parse_table(out) == {
        "CAN_STM32_FDCAN": "drivers/can/Kconfig.stm32:51",
        "CAN_STM32_FDCAN_MAX_STD_ID_FILTERS": "drivers/can/Kconfig.stm32:60",
        "LOG": "Kconfig.zephyr:12",
    }


def test_rename_in_a_moved_file_pairs_in_the_same_hunk() -> None:
    pairs = parse_pairs(TMP_LOG)
    assert ("TMP11X", "231b2a0c19f1", "same hunk") in pairs["TMP116"]
    # The EEPROM rename is a delete + add in other files: a same-commit candidate.
    assert ("EEPROM_TMP11X", "231b2a0c19f1", "same commit") in pairs["EEPROM_TMP116"]
    target = {
        "TMP11X": "drivers/sensor/ti/tmp11x/Kconfig:6",
        "EEPROM_TMP11X": "drivers/eeprom/Kconfig.tmp11x:4",
    }
    assert rank("TMP116", pairs, target)[0].symbol == "TMP11X"
    assert rank("EEPROM_TMP116", pairs, target)[0].symbol == "EEPROM_TMP11X"


def test_conditions_include_enclosing_if_blocks() -> None:
    assert conditions(STM32_KCONFIG, "CAN_STM32_FDCAN_MAX_STD_ID_FILTERS") == [
        "CAN_STM32_FDCAN",
        "!CAN_FD_MODE || SOC_SERIES_STM32H7X",
    ]
    assert conditions(STM32_KCONFIG, "CAN_STM32_BXCAN_MAX_STD_ID_FILTERS") == ["CAN_STM32_BXCAN"]
    assert conditions(STM32_KCONFIG, "CAN_STM32_FDCAN") == ["DT_HAS_ST_STM32_FDCAN_ENABLED"]
    assert conditions(STM32_KCONFIG, "NOPE") == []


def test_chained_renames_resolve_to_a_symbol_the_target_defines() -> None:
    """AUDIT F9: A -> B in one release, B -> C in a later one; the target has C."""
    log = (
        "commit 1111\n@@ -1 +1 @@\n-config OLD_A\n+config MID_B\n"
        "commit 2222\n@@ -1 +1 @@\n-config MID_B\n+config NEW_C\n"
    )
    (c,) = rank("OLD_A", parse_pairs(log), {"NEW_C": "Kconfig:1"})
    assert (c.symbol, c.via, c.commit) == ("NEW_C", ["MID_B"], "2222")


def test_unrelated_symbols_of_the_same_commit_are_not_candidates() -> None:
    log = "commit 3333\n@@ -1 +0,0 @@\n-config CAN_MAX_STD_ID_FILTER\n@@ -0,0 +1 @@\n+config BT_CTLR_FOO\n"
    assert rank("CAN_MAX_STD_ID_FILTER", parse_pairs(log), {"BT_CTLR_FOO": "x:1"}) == []


def test_generated_symbols_are_never_removed() -> None:
    db = kconfig_changes(
        {"DT_HAS_TI_TMP116_ENABLED": "a:1", "ZEPHYR_CMSIS_MODULE": "b:1", "TMP116": "c:1"},
        {"TMP11X": "d:1"},
        parse_pairs(TMP_LOG),
        lambda path: None,
    )
    assert sorted(db["removed"]) == ["TMP116"]
    assert db["removed"]["TMP116"]["candidates"][0]["symbol"] == "TMP11X"


MIRROR = (
    Path(os.environ.get("ZMIG_CACHE", Path.home() / ".cache" / "zmig"))
    / "mirrors"
    / "github.com_zephyrproject-rtos_zephyr.git"
)


@pytest.mark.skipif(not MIRROR.is_dir(), reason="no local Zephyr mirror")
def test_held_out_renames_on_the_real_tree(tmp_path: Path) -> None:
    """ardep A1 and RIT FSW F1c, from the mirror: the facts DESIGN-CHANGE-DB §1 relies on."""
    from zmig.cache import Cache
    from zmig.changes import build
    from zmig.refs import resolve_ref
    from zmig.tools import Runner

    cache = Cache(MIRROR.parent.parent)
    runner = Runner(log_dir=None, redactions={})
    a, b = (resolve_ref(t, cache, runner).ref.sha for t in ("v4.3.0", "v4.4.0"))
    mirror = resolve_ref("v4.4.0", cache, runner).mirror
    db = build(mirror, runner, a, b)["kconfig"]
    can = db["removed"]["CAN_MAX_STD_ID_FILTER"]["candidates"]
    by = {c["symbol"]: c for c in can}
    assert "CAN_STM32_FDCAN" in by["CAN_STM32_FDCAN_MAX_STD_ID_FILTERS"]["conditions"]
    assert "CAN_STM32_BXCAN" in by["CAN_STM32_BXCAN_MAX_STD_ID_FILTERS"]["conditions"]
    a, b = (resolve_ref(t, cache, runner).ref.sha for t in ("v4.1.0", "v4.2.0"))
    tmp = build(mirror, runner, a, b)["kconfig"]["removed"]["TMP116"]["candidates"]
    assert tmp[0]["symbol"] == "TMP11X" and tmp[0]["how"] == "same hunk"


def test_big_restructuring_commits_give_no_same_commit_candidates() -> None:
    """Mbed TLS 4 (c42fefcb1f2e) removed dozens of symbols and added others: none is a
    rename of another, and names alone (CIPHER_AES vs CIPHER_ALL) can't tell."""
    removed = "".join(f"-config MBEDTLS_CIPHER_{n}_ENABLED\n" for n in ("AES", "DES", "GCM", "CCM"))
    log = f"commit c42f\n@@ -1,4 +0,0 @@\n{removed}@@ -0,0 +1,2 @@\n+config MBEDTLS_CIPHER_ALL_ENABLED\n+config MBEDTLS_SOME_CIPHER_ENABLED\n"
    target = {"MBEDTLS_CIPHER_ALL_ENABLED": "a:1", "MBEDTLS_SOME_CIPHER_ENABLED": "a:2"}
    assert rank("MBEDTLS_CIPHER_AES_ENABLED", parse_pairs(log), target) == []


def test_a_same_hunk_rename_hides_the_rest_of_its_commit() -> None:
    """f2a6a191cf5f renamed STD and EXT in one hunk each; EXT isn't a candidate for STD."""
    log = (
        "commit f2a6\n@@ -17 +17 @@\n-config CAN_MAX_STD_ID_FILTER\n+config CAN_STM32_BXCAN_MAX_STD_ID_FILTERS\n"
        "@@ -33 +33 @@\n-config CAN_MAX_EXT_ID_FILTER\n+config CAN_STM32_BXCAN_MAX_EXT_ID_FILTERS\n"
    )
    target = {
        "CAN_STM32_BXCAN_MAX_STD_ID_FILTERS": "a:17",
        "CAN_STM32_BXCAN_MAX_EXT_ID_FILTERS": "a:33",
    }
    assert [c.symbol for c in rank("CAN_MAX_STD_ID_FILTER", parse_pairs(log), target)] == [
        "CAN_STM32_BXCAN_MAX_STD_ID_FILTERS"
    ]


def test_conditions_join_continued_lines() -> None:
    text = (
        "if MBEDTLS_BUILTIN && \\\n   !MBEDTLS_PSA\n\n"
        "config MBEDTLS_SOME_CIPHER_ENABLED\n\tbool\n\tdepends on \\\n\t\tMBEDTLS_CIPHER_C || \\\n\t\tMBEDTLS_GCM_C\n\nendif\n"
    )
    assert conditions(text, "MBEDTLS_SOME_CIPHER_ENABLED") == [
        "MBEDTLS_BUILTIN && !MBEDTLS_PSA",
        "MBEDTLS_CIPHER_C || MBEDTLS_GCM_C",
    ]


def test_a_deprecated_symbols_help_text_names_its_replacement() -> None:
    """Zephyr v4.3.0 drivers/serial/Kconfig.native_pty: NATIVE_UART_0_ON_STDINOUT is removed
    at v4.4.0 with no rename commit; its own help said where to go (CoffeeCaller C2)."""
    source_text = (
        "config NATIVE_UART_0_ON_STDINOUT\n"
        '\tbool "Connect the UART to the invoking shell stdin/stdout (deprecated)"\n'
        "\tselect DEPRECATED\n\thelp\n\t  Deprecated, use UART_NATIVE_PTY_0_ON_STDINOUT instead.\n\n"
        "endchoice\n"
    )
    db = kconfig_changes(
        {"NATIVE_UART_0_ON_STDINOUT": "drivers/serial/Kconfig.native_pty:64"},
        {"UART_NATIVE_PTY_0_ON_STDINOUT": "drivers/serial/Kconfig.native_pty:40"},
        {},
        lambda path: None,
        read_source=lambda path: source_text,
    )
    (c,) = db["removed"]["NATIVE_UART_0_ON_STDINOUT"]["candidates"]
    assert (c["symbol"], c["how"]) == ("UART_NATIVE_PTY_0_ON_STDINOUT", "help text")
