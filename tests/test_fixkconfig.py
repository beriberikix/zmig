"""`zmig fix kconfig`: conf rewriting and the classification rules (zmig.fixkconfig).

The symbol definitions are the real ones (from each tree's kconfig_dump), trimmed to the
fields the rules read.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from zmig.cli import main
from zmig.fixkconfig import (
    REMOVE_KIND,
    Assignment,
    FilePlan,
    apply_plan,
    classify,
    comment_out,
    drop_conflicts,
    parse_conf,
    plan_edits,
    rewrite,
)

# v3.6.0 lib/cpp/Kconfig.deprecated:6 and lib/cpp/Kconfig:8 (both refs).
CPLUSPLUS = {
    "type": "bool",
    "prompt": "C++ support for the application [DEPRECATED]",
    "deprecated": True,
    "selects": ["CPP", "DEPRECATED"],
    "defaults": [],
    "depends": "y",
    "locations": ["lib/cpp/Kconfig.deprecated:6"],
}
CPP = {
    "type": "bool",
    "prompt": "C++ support for the application",
    "deprecated": False,
    "selects": [],
    "defaults": [],
    "depends": "y",
    "locations": ["lib/cpp/Kconfig:8"],
}
# v4.1.0 subsys/debug/Kconfig:381; v4.2.0 renamed it to X86_DEBUG_INFO in arch/x86/Kconfig,
# which an ARM build's Kconfig tree doesn't source.
DEBUG_INFO = {
    "type": "bool",
    "prompt": "System debugging information",
    "deprecated": False,
    "selects": [],
    "defaults": [],
    "depends": "y",
    "locations": ["subsys/debug/Kconfig:381"],
}
X86_DEBUG_INFO = {**DEBUG_INFO, "depends": "X86", "locations": ["arch/x86/Kconfig:190"]}


def _a(sym: str, value: str = "y", path: str = "prj.conf", f: Path | None = None) -> Assignment:
    return Assignment(
        path, f or Path(path), 3, f"CONFIG_{sym}={value}", "CONFIG_", sym, value, "app"
    )


def _entry(text: str, renames: tuple[tuple[str, str], ...] = (), version: str = "4.2") -> Any:
    rn = [SimpleNamespace(kind="kconfig", old=o, new=n) for o, n in renames]
    return SimpleNamespace(full=text, renames=rn, version=version, id=f"MG-{version}-0001")


def _classify(
    a: Assignment,
    old: dict,
    new: dict,
    entries: list | None = None,
    got: str | None = "y",
    new_value: str | None = None,
) -> Any:
    return classify(a, old, new, new_value, entries or [], lambda s, v: got, "v1", "v2")


# --- parsing and rewriting ------------------------------------------------------------


def test_parse_conf_reads_values_comments_and_not_set() -> None:
    text = (
        "# comment\n"
        "CONFIG_A=y\n"
        '  CONFIG_B="x # not a comment"  # trailing\n'
        "# CONFIG_C is not set\n"
        "SB_CONFIG_D=0x10\n"
        "CONFIG_E =  5\n"
        "#CONFIG_F=y\n"
    )
    assert parse_conf(text) == [
        (2, "CONFIG_", "A", "y"),
        (3, "CONFIG_", "B", '"x # not a comment"'),
        (4, "CONFIG_", "C", "n"),
        (5, "SB_CONFIG_", "D", "0x10"),
        (6, "CONFIG_", "E", "5"),
    ]


def test_rewrite_keeps_values_spacing_comments_and_line_endings() -> None:
    text = "CONFIG_X=y\r\n  CONFIG_OLD = 5  # keep\r\n# CONFIG_OLD is not set\r\nCONFIG_OLD2=y"
    out = rewrite(text, {2: ("OLD", "NEW"), 3: ("OLD", "NEW"), 4: ("OLD2", "NEW2")})
    assert (
        out == "CONFIG_X=y\r\n  CONFIG_NEW = 5  # keep\r\n# CONFIG_NEW is not set\r\nCONFIG_NEW2=y"
    )
    # A line that no longer holds the expected symbol, or is out of range, is left alone.
    assert rewrite("CONFIG_Y=y\n", {1: ("OLD", "NEW"), 9: ("OLD", "NEW")}) == "CONFIG_Y=y\n"


def test_rewrite_is_idempotent() -> None:
    text = "CONFIG_CPLUSPLUS=y\n"
    once = rewrite(text, {1: ("CPLUSPLUS", "CPP")})
    assert rewrite(once, {1: ("CPLUSPLUS", "CPP")}) == once == "CONFIG_CPP=y\n"


# --- classification -------------------------------------------------------------------


def test_deprecation_select_with_matching_type_and_resolved_deps_is_a_rename() -> None:
    p = _classify(_a("CPLUSPLUS"), {"CPLUSPLUS": CPLUSPLUS, "CPP": CPP}, {"CPP": CPP})
    assert (p.category, p.new_symbol, p.evidence_kind) == ("rename", "CPP", "deprecation select")
    assert any("CPLUSPLUS is deprecated and selects CPP" in r for r in p.reasons)
    assert any("CONFIG_CPP=y resolves to y" in r for r in p.reasons)


def test_alias_default_is_kconfig_evidence_but_a_folded_menu_condition_is_not() -> None:
    old_sym = {**CPP, "locations": ["x:1"]}
    alias = {**CPP, "defaults": ["OLD"]}  # `config NEW ... default OLD`
    p = _classify(_a("OLD"), {"OLD": old_sym, "NEW": alias}, {"NEW": CPP})
    assert (p.category, p.new_symbol, p.evidence_kind) == ("rename", "NEW", "alias")
    # `y if CAN` is how kconfiglib folds `if CAN ... default y`: not an alias of CAN
    # (regression: CAN_AUTO_BUS_OFF_RECOVERY was proposed as a rename to CAN).
    can_child = {**CPP, "defaults": ["y if CAN"]}
    q = _classify(
        _a("CAN_AUTO_BUS_OFF_RECOVERY", "n"),
        {"CAN_AUTO_BUS_OFF_RECOVERY": can_child, "CAN": CPP},
        {"CAN": CPP},
    )
    # No Kconfig replacement and no guide entry: the remove category, never a rename to CAN.
    assert q.category == "remove" and q.new_symbol is None


def test_type_mismatch_unresolved_deps_and_no_prompt_block_the_rename() -> None:
    old = {"CPLUSPLUS": CPLUSPLUS, "CPP": CPP}
    t = _classify(_a("CPLUSPLUS"), old, {"CPP": {**CPP, "type": "int"}})
    assert t.category == "needs-human" and any("types differ" in r for r in t.reasons)
    d = _classify(_a("CPLUSPLUS"), old, {"CPP": CPP}, got="n")
    assert d.category == "needs-human"
    assert any("resolves to n (dependencies unmet" in r for r in d.reasons)
    n = _classify(_a("CPLUSPLUS"), old, {"CPP": {**CPP, "prompt": None}})
    assert n.category == "needs-human" and any("has no prompt" in r for r in n.reasons)
    u = _classify(_a("CPLUSPLUS"), old, {"CPP": CPP}, got=None)
    assert u.category == "needs-human" and any("could not re-resolve" in r for r in u.reasons)


def test_a_guide_value_or_unit_change_blocks_the_rename() -> None:
    entry = _entry("CONFIG_CPLUSPLUS was renamed to CONFIG_CPP; its value is now in ms.")
    p = _classify(_a("CPLUSPLUS"), {"CPLUSPLUS": CPLUSPLUS, "CPP": CPP}, {"CPP": CPP}, [entry])
    assert p.category == "needs-human"
    assert any("value or unit change" in r for r in p.reasons)


def test_a_guide_only_rename_needs_a_human() -> None:
    """v3.7's CAN_AUTO_BUS_OFF_RECOVERY -> CAN_MANUAL_RECOVERY_MODE ("renamed (and
    inverted)") has no Kconfig-level link: guide text only."""
    old = {"CAN_AUTO_BUS_OFF_RECOVERY": {**CPP, "defaults": ["y"]}}
    entry = _entry(
        "The Kconfig CONFIG_CAN_AUTO_BUS_OFF_RECOVERY was renamed (and inverted) to "
        "CONFIG_CAN_MANUAL_RECOVERY_MODE, which is disabled by default.",
        (("CAN_AUTO_BUS_OFF_RECOVERY", "CAN_MANUAL_RECOVERY_MODE"),),
        "3.7",
    )
    p = _classify(
        _a("CAN_AUTO_BUS_OFF_RECOVERY", "n"), old, {"CAN_MANUAL_RECOVERY_MODE": CPP}, [entry]
    )
    assert p.category == "needs-human" and p.new_symbol is None
    assert any("guide text alone is not enough evidence" in r for r in p.reasons)
    assert any("'inverted'" in r for r in p.reasons)


def test_debug_info_is_not_blindly_renamed_on_arm() -> None:
    """CannectivIty-test PR #2 sets CONFIG_DEBUG_INFO=y on an ARM board. v4.2 renamed it
    to CONFIG_X86_DEBUG_INFO (migration-guide-4.2.rst:816), an x86-only option that the
    ARM build's Kconfig tree doesn't contain. The fixer must not write it."""
    entry = _entry(
        "Renamed CONFIG_DEBUG_INFO to CONFIG_X86_DEBUG_INFO to better reflect its purpose.",
        (("DEBUG_INFO", "X86_DEBUG_INFO"),),
    )
    arm_old, arm_new = {"DEBUG_INFO": DEBUG_INFO}, {"CPP": CPP}
    p = _classify(_a("DEBUG_INFO"), arm_old, arm_new, [entry])
    assert p.category == "needs-human" and p.new_symbol is None
    assert any("X86_DEBUG_INFO, which is not defined in this image's tree" in r for r in p.reasons)
    assert plan_edits([p], "deprecation select") == plan_edits([p], "alias") == {}
    # Even if the tree held it (all arch Kconfigs sourced), guide text alone wouldn't do,
    # and Kconfig would refuse it on ARM: X86_DEBUG_INFO depends on X86.
    q = _classify(_a("DEBUG_INFO"), arm_old, {"X86_DEBUG_INFO": X86_DEBUG_INFO}, [entry], got="n")
    assert q.category == "needs-human" and q.new_symbol is None


def test_present_symbol_with_unmet_deps_is_not_a_removal() -> None:
    p = _classify(_a("CPP"), {"CPP": CPP}, {"CPP": {**CPP, "depends": "X86"}}, new_value=None)
    assert p.category == "deps-unmet" and "Not a rename" in p.reasons[0]
    assert _classify(_a("CPP"), {"CPP": CPP}, {"CPP": CPP}, new_value="y") is None
    # Not defined at the source either: not a migration issue.
    assert _classify(_a("NOPE"), {"CPP": CPP}, {"CPP": CPP}) is None


def test_existing_target_assignment_and_disagreeing_images_are_not_edited(tmp_path: Path) -> None:
    f = tmp_path / "prj.conf"
    f.write_text("CONFIG_CPLUSPLUS=y\nCONFIG_CPP=y\n")
    p = _classify(_a("CPLUSPLUS", f=f), {"CPLUSPLUS": CPLUSPLUS, "CPP": CPP}, {"CPP": CPP})
    (q,) = drop_conflicts([p])
    assert q.category == "needs-human" and any("already assigns CONFIG_CPP" in r for r in q.reasons)
    g = tmp_path / "other.conf"
    g.write_text("CONFIG_CPLUSPLUS=y\n")
    ok = _classify(_a("CPLUSPLUS", f=g), {"CPLUSPLUS": CPLUSPLUS, "CPP": CPP}, {"CPP": CPP})
    assert plan_edits([ok], "deprecation select")[g].edits == {3: ("CPLUSPLUS", "CPP")}
    bad = _classify(
        _a("CPLUSPLUS", f=g), {"CPLUSPLUS": CPLUSPLUS, "CPP": CPP}, {"CPP": CPP}, got="n"
    )
    assert plan_edits([ok, bad], "deprecation select") == {}


# --- the command ----------------------------------------------------------------------


def test_apply_refuses_a_dirty_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    app = tmp_path / "proj" / "app"
    app.mkdir(parents=True)
    (app / "CMakeLists.txt").write_text("project(x)\n")
    (app / "prj.conf").write_text("CONFIG_CPLUSPLUS=y\n")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "PATH": "/usr/bin:/bin",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "HOME": str(tmp_path),
    }
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "x"]):
        subprocess.run(["git", *cmd], cwd=app.parent, env=env, check=True)
    (app / "prj.conf").write_text("CONFIG_CPLUSPLUS=n\n")
    argv = [
        "fix",
        "kconfig",
        "--app",
        str(app),
        "--board",
        "native_sim",
        "--from",
        "v3.6.0",
        "--to",
        "v3.7.0",
        "--apply",
        "--cache-dir",
        str(tmp_path / "c"),
    ]
    assert main(argv) == 2
    assert "is dirty" in capsys.readouterr().err
    assert (app / "prj.conf").read_text() == "CONFIG_CPLUSPLUS=n\n"


# --- remove with no replacement ---------------------------------------------------------

PINMUX = {**CPP, "prompt": "Pinmux driver", "locations": ["drivers/pinmux/Kconfig:9"]}


def test_removed_with_no_replacement_and_no_guide_mention_is_removed() -> None:
    """ZMK's planck_rev6_defconfig:10 CONFIG_PINMUX=y: menuconfig PINMUX at v3.2
    (drivers/pinmux/Kconfig:9), gone at v3.5, no Kconfig replacement, no guide entry."""
    p = _classify(_a("PINMUX"), {"PINMUX": PINMUX}, {"CPP": CPP})
    assert (p.category, p.evidence_kind, p.new_symbol) == ("remove", REMOVE_KIND, None)
    assert p.note == "# zmig: CONFIG_PINMUX removed in v2: no replacement evidenced"
    assert any("no migration-guide entry mentions it" in r for r in p.reasons)


def test_a_guide_mention_keeps_a_removed_symbol_for_a_human() -> None:
    """The guide can name a replacement outside Kconfig (ZMK's SSD1306_REVERSE_MODE became
    the devicetree property inversion-on): then "no replacement evidenced" isn't true."""
    entry = _entry("CONFIG_SSD1306_REVERSE_MODE was removed; use the inversion-on property.")
    old = {"SSD1306_REVERSE_MODE": {**CPP, "locations": ["drivers/display/Kconfig.ssd1306:30"]}}
    p = _classify(_a("SSD1306_REVERSE_MODE"), old, {"CPP": CPP}, [entry])
    assert p.category == "needs-human"
    assert any("may name a replacement outside Kconfig" in r for r in p.reasons)
    # A guide rename keeps it for a human too (DEBUG_INFO on ARM, CAN_AUTO_BUS_OFF_RECOVERY).
    ren = _entry(
        "Renamed CONFIG_DEBUG_INFO to CONFIG_X86_DEBUG_INFO.", (("DEBUG_INFO", "X86_DEBUG_INFO"),)
    )
    assert (
        _classify(_a("DEBUG_INFO"), {"DEBUG_INFO": DEBUG_INFO}, {"CPP": CPP}, [ren]).category
        == "needs-human"
    )


def test_comment_out_keeps_comments_and_values_and_is_idempotent() -> None:
    note = "# zmig: CONFIG_PINMUX removed in v3.5: no replacement evidenced"
    text = (
        "CONFIG_PINCTRL=y\r\n"
        "# enable pinmux\r\n"
        "  CONFIG_PINMUX=y  # old driver\r\n"
        "# CONFIG_PINMUX is not set\r\n"
        "CONFIG_GPIO=y"
    )
    out = comment_out(text, {3: ("PINMUX", note), 4: ("PINMUX", note)})
    assert out == (
        "CONFIG_PINCTRL=y\r\n"
        "# enable pinmux\r\n"
        f"  {note}\r\n"
        "  # CONFIG_PINMUX=y  # old driver\r\n"
        f"{note}\r\n"
        "# # CONFIG_PINMUX is not set\r\n"
        "CONFIG_GPIO=y"
    )
    assert [s for _, _, s, _ in parse_conf(out)] == ["PINCTRL", "GPIO"]  # no longer assigned
    assert comment_out(out, {3: ("PINMUX", note)}) == out  # line 3 is now the note: untouched


def test_plan_edits_separates_the_remove_kind(tmp_path: Path) -> None:
    f = tmp_path / "planck_rev6_defconfig"
    f.write_text("# enable pinmux\nCONFIG_PINMUX=y\nCONFIG_CPLUSPLUS=y\n")
    rm = _classify(
        Assignment(
            "b/planck_rev6_defconfig", f, 2, "CONFIG_PINMUX=y", "CONFIG_", "PINMUX", "y", "app"
        ),
        {"PINMUX": PINMUX},
        {"CPP": CPP},
    )
    rn = _classify(
        Assignment(
            "b/planck_rev6_defconfig",
            f,
            3,
            "CONFIG_CPLUSPLUS=y",
            "CONFIG_",
            "CPLUSPLUS",
            "y",
            "app",
        ),
        {"CPLUSPLUS": CPLUSPLUS, "CPP": CPP},
        {"CPP": CPP},
    )
    removes = plan_edits([rm, rn], REMOVE_KIND)
    renames = plan_edits([rm, rn], "deprecation select")
    assert removes[f].edits == {2: ("PINMUX", rm.note)} and renames[f].edits == {
        3: ("CPLUSPLUS", "CPP")
    }
    after = apply_plan(apply_plan(f.read_text(), renames[f]), removes[f])
    assert after == f"# enable pinmux\n{rm.note}\n# CONFIG_PINMUX=y\nCONFIG_CPP=y\n"
    assert apply_plan("x\n", FilePlan(f, "p", REMOVE_KIND, {})) == "x\n"
