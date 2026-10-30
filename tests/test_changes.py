"""zmig.changes: Kconfig facts from the tree alone (docs/DESIGN-CHANGE-DB.md, step 1).
Texts are taken from Zephyr's history."""

from __future__ import annotations

# ruff: noqa: E501  (verbatim git output)
import os
from pathlib import Path
from typing import Any

import pytest

from zmig.changes import (
    COMPAT,
    compat_changes,
    conditions,
    kconfig_changes,
    parse_pairs,
    parse_table,
    rank,
)

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
    db = build(mirror, runner, a, b)
    tmp = db["kconfig"]["removed"]["TMP116"]["candidates"]
    assert tmp[0]["symbol"] == "TMP11X" and tmp[0]["how"] == "same hunk"
    # RIT FSW F1a/F1b: the binding files were renamed with their compatibles.
    dt = db["devicetree"]["removed"]
    assert [c["symbol"] for c in dt["ti,tmp116"]["candidates"]] == ["ti,tmp11x"]
    assert [c["symbol"] for c in dt["ti,tmp116-eeprom"]["candidates"]] == ["ti,tmp11x-eeprom"]


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


def test_condition_evaluation_is_three_valued() -> None:
    """Against the source build's .config: a symbol it doesn't know is unknown (None), not
    false: DT_HAS_TI_TMP11X_ENABLED is new at the target."""
    from zmig.changes import evaluate

    cfg = {"CAN_STM32_FDCAN": "y", "CAN_FD_MODE": "n", "LOG": "y", "STACK": "1024"}
    assert evaluate("CAN_STM32_FDCAN", cfg) is True
    assert evaluate("!CAN_FD_MODE || SOC_SERIES_STM32H7X", cfg) is True
    assert evaluate("CAN_STM32_BXCAN", cfg) is None  # not in the source .config
    assert evaluate("CAN_FD_MODE && LOG", cfg) is False
    assert evaluate("(LOG && !CAN_FD_MODE) && CAN_STM32_FDCAN", cfg) is True
    assert evaluate("DT_HAS_TI_TMP11X_ENABLED", cfg) is None
    assert evaluate("STACK > 512", cfg) is None  # comparisons aren't evaluated
    assert evaluate("LOG && STACK = 1024", cfg) is None


def test_choose_the_candidate_the_build_enables() -> None:
    """ardep A1: FDCAN (if CAN_STM32_FDCAN, which ardep enables) over bxCAN."""
    from zmig.changes import choose

    cands = [
        {
            "symbol": "CAN_STM32_BXCAN_MAX_STD_ID_FILTERS",
            "how": "same hunk",
            "commit": "f2a6",
            "via": [],
            "conditions": ["CAN_STM32_BXCAN"],
        },
        {
            "symbol": "CAN_STM32_FDCAN_MAX_STD_ID_FILTERS",
            "how": "same hunk",
            "commit": "38c3",
            "via": [],
            "conditions": ["CAN_STM32_FDCAN", "!CAN_FD_MODE || SOC_SERIES_STM32H7X"],
        },
    ]
    picked = choose(cands, {"CAN_STM32_FDCAN": "y", "CAN_FD_MODE": "n"})
    assert [(c["symbol"], c["confidence"]) for c in picked] == [
        ("CAN_STM32_FDCAN_MAX_STD_ID_FILTERS", "medium"),
        ("CAN_STM32_BXCAN_MAX_STD_ID_FILTERS", "low"),
    ]
    # A condition the build makes false drops the candidate.
    assert [
        c["symbol"] for c in choose(cands, {"CAN_STM32_BXCAN": "n", "CAN_STM32_FDCAN": "y"})
    ] == ["CAN_STM32_FDCAN_MAX_STD_ID_FILTERS"]
    # One candidate whose conditions are unknown (TMP11X) stays medium; two unknowns are low.
    tmp = [
        {
            "symbol": "TMP11X",
            "how": "same hunk",
            "commit": "231b",
            "via": [],
            "conditions": ["DT_HAS_TI_TMP11X_ENABLED"],
        }
    ]
    assert [c["confidence"] for c in choose(tmp, {})] == ["medium"]
    assert [c["confidence"] for c in choose(cands, {})] == ["low", "low"]


DB = {
    "kconfig": {
        "removed": {
            "TMP116": {"defined_at": "drivers/sensor/ti/tmp116/Kconfig:6", "candidates": [
                {"symbol": "TMP11X", "commit": "231b2a0c19fc", "how": "same hunk", "similarity": 0.83, "via": [],
                 "defined_at": "drivers/sensor/ti/tmp11x/Kconfig:6", "conditions": ["DT_HAS_TI_TMP11X_ENABLED"]}]},
            "CAN_MAX_STD_ID_FILTER": {"defined_at": "drivers/can/Kconfig.stm32:17", "candidates": [
                {"symbol": "CAN_STM32_BXCAN_MAX_STD_ID_FILTERS", "commit": "f2a6a191cf5f", "how": "same hunk", "similarity": 0.8, "via": [],
                 "defined_at": "drivers/can/Kconfig.stm32:17", "conditions": ["CAN_STM32_BXCAN"]},
                {"symbol": "CAN_STM32_FDCAN_MAX_STD_ID_FILTERS", "commit": "38c329742146", "how": "same hunk", "similarity": 0.8, "via": [],
                 "defined_at": "drivers/can/Kconfig.stm32:60", "conditions": ["CAN_STM32_FDCAN"]}]},
            "GONE_READ": {"defined_at": "x/Kconfig:1", "candidates": []},
        },
        "added": 0, "symbols": {},
    }
}  # fmt: skip


def _ctx(tmp_path: Path, target_configured: bool, prior: list[Any] | None = None) -> Any:
    from types import SimpleNamespace

    from zmig.buildctx import Artifact
    from zmig.usage import Loc

    zephyr = tmp_path / "ws" / "zephyr"
    board = tmp_path / "ardep" / "boards" / "mercedes" / "ardep"
    board.mkdir(parents=True)
    rel = os.path.relpath(board / "Kconfig.defconfig", zephyr)
    old_kconfig = {
        "TMP116": {"locations": ["drivers/sensor/ti/tmp116/Kconfig:6"]},
        "CAN_MAX_STD_ID_FILTER": {"locations": ["drivers/can/Kconfig.stm32:17", f"{rel}:17"]},
        "GONE_READ": {"locations": ["x/Kconfig:1"]},
    }
    img = SimpleNamespace(dir=tmp_path / "b", has=lambda a: a is Artifact.CONFIG)
    return SimpleNamespace(
        is_app=True,
        old_img=img,
        new_img=SimpleNamespace(dir=tmp_path / "c", has=lambda a: target_configured),
        old_config={"TMP116": "y", "CAN_STM32_FDCAN": "y", "CAN_MAX_STD_ID_FILTER": "12"},
        old_kconfig=old_kconfig,
        old=SimpleNamespace(workspace=SimpleNamespace(zephyr=zephyr), src=tmp_path / "app"),
        usage=SimpleNamespace(
            kconfig_set={"TMP116": [(Loc("boards/arm/sensor_module/sensor_module_defconfig", 33, "CONFIG_TMP116=y"), "y")]},
            kconfig_read={"GONE_READ": [Loc("src/main.c", 4, "#ifdef CONFIG_GONE_READ")]},
        ),
        new_spec="v4.4.0", scope=("s",), prior=prior or [],
        runner=SimpleNamespace(redact=lambda s: s),
        shared={"module_definitions": (set(), set())},
    )  # fmt: skip


def test_changes_analyzer_reports_what_the_skipped_kconfig_analyzer_would(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DESIGN-CHANGE-DB §4: when the target never reached Kconfig, removed symbols the
    project sets, re-declares or reads still get findings, with the IDs the kconfig and
    buildlog analyzers give them."""
    from zmig.analyzers import changes as mod
    from zmig.findings import Category, Severity, make_id

    monkeypatch.setattr(mod, "database", lambda ctx: DB)
    monkeypatch.setattr(mod, "project_roots", lambda b, d: {str(tmp_path / "ardep"): "ardep/"})
    ctx = _ctx(tmp_path, target_configured=False)
    found = {f.kind: f for f in mod.ChangesAnalyzer().analyze(ctx).findings}

    tmp = found["symbol-removed"]
    assert tmp.id == make_id(Category.KCONFIG, "symbol-removed", "CONFIG_TMP116", ("s",))
    assert tmp.severity is Severity.BREAKING and "changes.lexical" in tmp.heuristics
    assert tmp.new["candidates"][0]["symbol"] == "TMP11X"

    can = found["kconfig-override-orphaned"]
    assert can.id == make_id(
        Category.BUILD, "kconfig-override-orphaned", "CONFIG_CAN_MAX_STD_ID_FILTER", ("s",)
    )
    assert can.evidence[0]["path"] == "ardep/boards/mercedes/ardep/Kconfig.defconfig"
    assert can.evidence[0]["line"] == 17
    assert can.new["candidates"][0]["symbol"] == "CAN_STM32_FDCAN_MAX_STD_ID_FILTERS"
    assert can.new["candidates"][0]["confidence"] == "medium"

    read = found["read-symbol-removed"]
    assert read.id == make_id(Category.KCONFIG, "read-symbol-removed", "CONFIG_GONE_READ", ("s",))

    # A subject an earlier analyzer already reported isn't reported twice.
    monkeypatch.setattr(
        mod, "project_roots", lambda b, d: {str(tmp_path / "2" / "ardep"): "ardep/"}
    )
    again = mod.ChangesAnalyzer().analyze(_ctx(tmp_path / "2", False, prior=[tmp]))
    assert "symbol-removed" not in {f.kind for f in again.findings}


def test_changes_analyzer_stays_out_when_the_target_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zmig.analyzers import changes as mod

    monkeypatch.setattr(mod, "database", lambda ctx: DB)
    assert mod.ChangesAnalyzer().analyze(_ctx(tmp_path, target_configured=True)).findings == []


def test_an_empty_symbol_table_is_an_error_not_no_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ref the mirror can't grep (or a grep that fails) must not read as "nothing was
    removed": the analyzer reports error instead of a silent ok."""
    from types import SimpleNamespace

    from zmig import changes as mod
    from zmig.errors import ZmigError

    monkeypatch.setattr(
        mod, "definitions", lambda m, r, sha: {} if sha == "bad" else {"A": ["x:1"]}
    )
    monkeypatch.setattr(mod, "glue_references", lambda m, r, sha: "")
    monkeypatch.setattr(mod, "binding_table", lambda m, r, sha: {"v,a": "y:1"})
    with pytest.raises(ZmigError, match="no Kconfig symbols"):
        mod.build(SimpleNamespace(path="m"), SimpleNamespace(), "good", "bad")  # type: ignore[arg-type]


# `git log -M -p -U0 -- dts/bindings` of 231b2a0c19f (RIT FSW F1a/F1b), trimmed.
BINDING_LOG = """\
commit 231b2a0c19fc
diff --git a/dts/bindings/mtd/ti,tmp116-eeprom.yaml b/dts/bindings/mtd/ti,tmp11x-eeprom.yaml
similarity index 85%
rename from dts/bindings/mtd/ti,tmp116-eeprom.yaml
rename to dts/bindings/mtd/ti,tmp11x-eeprom.yaml
--- a/dts/bindings/mtd/ti,tmp116-eeprom.yaml
+++ b/dts/bindings/mtd/ti,tmp11x-eeprom.yaml
@@ -6 +6 @@
-compatible: "ti,tmp116-eeprom"
+compatible: "ti,tmp11x-eeprom"
diff --git a/dts/bindings/sensor/ti,tmp116.yaml b/dts/bindings/sensor/ti,tmp11x.yaml
--- a/dts/bindings/sensor/ti,tmp116.yaml
+++ b/dts/bindings/sensor/ti,tmp11x.yaml
@@ -6 +6 @@
-compatible: "ti,tmp116"
+compatible: "ti,tmp11x"
"""


def test_binding_table_and_renamed_compatibles() -> None:
    """DESIGN-CHANGE-DB §2.3: compatibles from the bindings' `compatible:` lines; a binding
    file renamed with its compatible pairs them, each with its own (not the sibling's)."""
    table = parse_table(
        'v4.1.0:dts/bindings/sensor/ti,tmp116.yaml:6:compatible: "ti,tmp116"\n'
        "v4.1.0:dts/bindings/x/vnd,plain.yaml:3:compatible: vnd,plain # unquoted\n",
        COMPAT,
    )
    assert table == {
        "ti,tmp116": "dts/bindings/sensor/ti,tmp116.yaml:6",
        "vnd,plain": "dts/bindings/x/vnd,plain.yaml:3",
    }
    db = compat_changes(
        {"ti,tmp116": "s:6", "ti,tmp116-eeprom": "e:6", "vnd,keep": "k:1"},
        {"ti,tmp11x": "s2:6", "ti,tmp11x-eeprom": "e2:6", "vnd,keep": "k:1"},
        parse_pairs(BINDING_LOG, COMPAT),
    )
    rem = db["removed"]
    assert sorted(rem) == ["ti,tmp116", "ti,tmp116-eeprom"]
    assert [(c["symbol"], c["how"], c["defined_at"]) for c in rem["ti,tmp116"]["candidates"]] == [
        ("ti,tmp11x", "same hunk", "s2:6")
    ]
    assert [c["symbol"] for c in rem["ti,tmp116-eeprom"]["candidates"]] == ["ti,tmp11x-eeprom"]


DT_DB = {
    "kconfig": {"removed": {}, "added": 0, "symbols": {}},
    "devicetree": {"removed": {
        "ti,tmp116": {"defined_at": "dts/bindings/sensor/ti,tmp116.yaml:6", "candidates": [
            {"symbol": "ti,tmp11x", "commit": "231b2a0c19fc", "how": "same hunk", "similarity": 0.89,
             "via": [], "defined_at": "dts/bindings/sensor/ti,tmp11x.yaml:6", "conditions": []}]},
    }},
}  # fmt: skip
BOARD_DTS = "fsw/boards/arm/sensor_module/sensor_module.dts"


def _edt(matching: str | None) -> dict[str, Any]:
    node = {"name": "tmp117@48", "labels": ["tmp117"], "aliases": [], "compats": ["ti,tmp116"],
            "matching_compat": matching, "status": "okay", "ord": 5, "binding": None,
            "props": {}, "raw_props": [], "raw_values": {}, "specs": {}}  # fmt: skip
    return {"nodes": {"/soc/i2c@40005400/tmp117@48": node}, "chosen": {}}


def _dt_ctx(tmp_path: Path, target_edt: bool) -> Any:
    from types import SimpleNamespace

    from zmig.buildctx import Artifact
    from zmig.diagnostics import parse_log
    from zmig.usage import Loc

    return SimpleNamespace(
        is_app=True,
        old_edt=_edt("ti,tmp116"), new_edt=_edt(None),
        old_facts=parse_log(""), new_raw_facts=parse_log(""),
        new_raw=SimpleNamespace(src=tmp_path),
        old_img=SimpleNamespace(dir=tmp_path / "b", has=lambda a: a in (Artifact.CONFIG, Artifact.EDT)),
        new_img=SimpleNamespace(dir=tmp_path / "c", has=lambda a: target_edt and a is Artifact.EDT),
        old_config={}, old_kconfig={},
        old=SimpleNamespace(workspace=SimpleNamespace(zephyr=tmp_path / "ws"), src=tmp_path / "app"),
        usage=SimpleNamespace(
            app=tmp_path, dt_props_set=[], dt_props_read=[], dt_nodes=[], kconfig_set={}, kconfig_read={},
            dt_compats={"ti_tmp116": [Loc(BOARD_DTS, 202, 'compatible = "ti,tmp116"; /* TMP117 */')]},
        ),
        build_files=set(), old_spec="v4.1.0", new_spec="v4.2.0", scope=("s",), prior=[],
        runner=SimpleNamespace(redact=lambda s: s),
        shared={"module_definitions": (set(), set())},
    )  # fmt: skip


def test_a_renamed_compatible_in_a_project_board_dts_gets_its_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RIT FSW F1a: the target generated its devicetree, so the devicetree analyzer reports
    `ti,tmp116` unbound at the board DTS; the database adds `ti,tmp11x`, and the fix is the
    line edit."""
    from zmig.analyzers import devicetree as dt
    from zmig.fixes import apply_fixes

    monkeypatch.setattr(dt, "_compat_database", lambda ctx: DT_DB)
    found = [f for f in dt.DevicetreeAnalyzer().analyze(_dt_ctx(tmp_path, True)).findings
             if f.kind == "compatible-unbound"]  # fmt: skip
    assert len(found) == 1
    f = found[0]
    assert f.subject == "ti_tmp116" and f.old == "ti,tmp116"
    assert f.new["candidates"][0]["symbol"] == "ti,tmp11x"
    fixed = apply_fixes([f], None)[0]  # type: ignore[arg-type]
    assert fixed.fix["file"] == BOARD_DTS and fixed.fix["line"] == 202
    assert fixed.fix["old"] == 'compatible = "ti,tmp116"; /* TMP117 */'
    assert fixed.fix["new"] == 'compatible = "ti,tmp11x"; /* TMP117 */'
    assert fixed.fix["confidence"] == "medium"
    assert fixed.action.count("ti,tmp11x") == 1  # the edit names it; no second "Use ..."


def test_changes_analyzer_reports_an_unbound_compatible_when_the_target_has_no_devicetree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The target stopped before generating its devicetree: the bindings table stands in
    for the devicetree analyzer, with its ID. With the target devicetree, it stays out."""
    from zmig.analyzers import changes as mod
    from zmig.analyzers import devicetree as dt
    from zmig.findings import Category, make_id

    monkeypatch.setattr(mod, "database", lambda ctx: DT_DB)
    monkeypatch.setattr(dt, "_compat_database", lambda ctx: DT_DB)
    found = mod.ChangesAnalyzer().analyze(_dt_ctx(tmp_path, False)).findings
    assert [f.kind for f in found] == ["compatible-unbound"]
    f = found[0]
    assert f.id == make_id(Category.DEVICETREE, "compatible-unbound", "ti_tmp116", ("s",))
    assert f.old == "ti,tmp116" and f.new["candidates"][0]["symbol"] == "ti,tmp11x"
    assert "changes.lexical" in f.heuristics
    assert mod.ChangesAnalyzer().analyze(_dt_ctx(tmp_path / "2", True)).findings == []


def test_a_node_gone_at_the_target_is_not_an_unbound_compatible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CoffeeCaller (tuned): the target doesn't apply boards/extensions/native_sim.overlay,
    so its `gpio-keys` node is gone, not unbound; `gpio-keys` still has its binding.
    Only a node the target still has, listing the compatible with no binding matching it,
    is `compatible-unbound`."""
    from zmig.analyzers import devicetree as dt

    monkeypatch.setattr(dt, "_compat_database", lambda ctx: DT_DB)
    ctx = _dt_ctx(tmp_path, True)
    ctx.new_edt = {"nodes": {}, "chosen": {}}
    assert [
        f for f in dt.DevicetreeAnalyzer().analyze(ctx).findings if f.kind == "compatible-unbound"
    ] == []


def test_glue_paths_and_symbols_moved_into_a_modules_glue() -> None:
    """DESIGN-CHANGE-DB §2.4. ardep A6: at v4.4.0 the mbedtls glue builds paths into
    tf-psa-crypto (a mere variable pass-through, like MLDSA_NATIVE_DIR, isn't a use).
    RIT FSW F2: HAS_CMSIS_CORE_M moved from modules/cmsis/ to modules/cmsis_6/ at v4.2.0."""
    from zmig.changes import glue_paths, moved_into_glue, parse_definitions

    grep = (
        "v4.4.0:modules/mbedtls/CMakeLists.txt:40:    add_subdirectory(${ZEPHYR_MBEDTLS_MODULE_DIR}/x)\n"
        'v4.4.0:modules/mbedtls/legacy_support.cmake:18:  set(P "${ZEPHYR_TF_PSA_CRYPTO_MODULE_DIR}/drivers")\n'
        'v4.4.0:modules/mbedtls/legacy_support.cmake:33:  set(Q "${ZEPHYR_TF_PSA_CRYPTO_MODULE_DIR}/src")\n'
    )
    assert glue_paths(grep) == {
        "mbedtls": {"TF_PSA_CRYPTO": "modules/mbedtls/legacy_support.cmake:18"}
    }
    src = parse_definitions(
        "v4.1.0:modules/cmsis/Kconfig:21:config HAS_CMSIS_CORE_M\n"
        "v4.1.0:modules/cmsis/Kconfig:7:config HAS_CMSIS_CORE\n"
        "v4.1.0:modules/cmsis_6/Kconfig:5:config ZEPHYR_CMSIS_6_MODULE\n"
        "v4.1.0:drivers/x/Kconfig:3:config STAYS\n"
    )
    tgt = parse_definitions(
        "v4.2.0:modules/cmsis_6/Kconfig:14:config HAS_CMSIS_CORE_M\n"
        "v4.2.0:modules/cmsis/Kconfig:7:config HAS_CMSIS_CORE\n"
        "v4.2.0:modules/cmsis_6/Kconfig:8:config HAS_CMSIS_CORE\n"
        "v4.2.0:modules/cmsis_6/Kconfig:5:config ZEPHYR_CMSIS_6_MODULE\n"
        "v4.2.0:modules/foo/Kconfig:3:config STAYS\n"
    )
    # HAS_CMSIS_CORE is still defined in modules/cmsis/; ZEPHYR_*_MODULE is generated;
    # STAYS moved into foo's glue too.
    assert moved_into_glue(src, tgt) == {
        "HAS_CMSIS_CORE_M": {
            "dir": "cmsis_6",
            "defined_at": "modules/cmsis_6/Kconfig:14",
            "was": "modules/cmsis/Kconfig:21",
        },
        "STAYS": {
            "dir": "foo",
            "defined_at": "modules/foo/Kconfig:3",
            "was": "drivers/x/Kconfig:3",
        },
    }


def test_a_symbol_or_compatible_a_module_defines_at_the_target_is_not_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DESIGN-CHANGE-DB §2.1 (DOGFOOD-11 gap 1): the database covers Zephyr's tree only. A
    symbol (or compatible) that moved into a module the scan uses still exists at the
    target, so it isn't reported removed. The project's own re-declaration (ardep A1) is
    in the project's module, not a workspace module, and doesn't count."""
    from zmig.analyzers import changes as mod

    monkeypatch.setattr(mod, "database", lambda ctx: DB)
    monkeypatch.setattr(mod, "project_roots", lambda b, d: {str(tmp_path / "ardep"): "ardep/"})
    monkeypatch.setattr(mod, "module_definitions", lambda ctx: ({"TMP116", "GONE_READ"}, set()))
    kinds = {(f.kind, f.subject) for f in mod.ChangesAnalyzer().analyze(
        _ctx(tmp_path, target_configured=False)).findings}  # fmt: skip
    assert kinds == {("kconfig-override-orphaned", "CONFIG_CAN_MAX_STD_ID_FILTER")}

    monkeypatch.setattr(mod, "database", lambda ctx: DT_DB)
    monkeypatch.setattr(mod, "module_definitions", lambda ctx: (set(), {"ti,tmp116"}))
    assert mod.ChangesAnalyzer().analyze(_dt_ctx(tmp_path / "2", False)).findings == []


def test_module_definitions_from_the_target_workspace(tmp_path: Path) -> None:
    import subprocess
    from types import SimpleNamespace

    from zmig.analyzers.changes import module_definitions

    m = tmp_path / "hal_x"
    (m / "zephyr" / "dts" / "bindings").mkdir(parents=True)
    (m / "zephyr" / "Kconfig").write_text("config MOVED_SYM\n\tbool\n")
    (m / "zephyr" / "dts" / "bindings" / "v,x.yaml").write_text('compatible: "vnd,moved"\n')
    (m / "notes.txt").write_text("config NOT_KCONFIG\n")
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.email=a@b", "-c", "user.name=a",
                                               "commit", "-qm", "x"]):  # fmt: skip
        subprocess.run(["git", "-C", str(m), *cmd], check=True)
    from zmig.tools import Runner

    ws = SimpleNamespace(modules=lambda runner: [SimpleNamespace(name="hal_x", path=m)])
    ctx = SimpleNamespace(new=SimpleNamespace(workspace=ws), runner=Runner(log_dir=None, redactions={}),
                          shared={})  # fmt: skip
    assert module_definitions(ctx) == ({"MOVED_SYM"}, {"vnd,moved"})  # type: ignore[arg-type]


def test_a_change_database_that_cant_be_built_is_reported_not_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DOGFOOD-11 gap 2: without the database the devicetree analyzer still reports
    `compatible-unbound` (from the EDTs), but without a replacement, and says why under
    "not checked" instead of nothing."""
    from zmig.analyzers import changes as ch
    from zmig.analyzers import devicetree as dt
    from zmig.errors import ZmigError

    def fail(ctx: Any) -> Any:
        raise ZmigError("no Kconfig symbols found at 0123456789ab in /mirror")

    monkeypatch.setattr(ch, "database", fail)
    res = dt.DevicetreeAnalyzer().analyze(_dt_ctx(tmp_path, True))
    (f,) = [f for f in res.findings if f.kind == "compatible-unbound"]
    assert f.new is None
    assert [(n.area, "0123456789ab" in n.reason) for n in res.not_checked] == [
        ("change database", True)
    ]
