"""Unit tests for scan building blocks that don't need a Zephyr build."""

from __future__ import annotations

from pathlib import Path

from zmig.analyzers.kconfig import norm_value, rename_candidates
from zmig.boards import map_board
from zmig.buildctx import SALVAGE_TAG, _apply_edits, locate_salvage_edits
from zmig.findings import Confidence
from zmig.snapshots import menuconfig_env, parse_dotconfig


def test_rename_candidates() -> None:
    old = {
        "CPLUSPLUS": {
            "deprecated": True,
            "selects": ["CPP", "DEPRECATED"],
            "prompt": "[DEPRECATED] C++ support",
            "help": None,
        },
        "CPP": {"prompt": "C++ support", "help": None, "selects": []},
    }
    new = {
        "CPP": old["CPP"],
        "CPP_NEW": {"prompt": "Something", "help": "Replaces CONFIG_CPLUSPLUS.", "selects": []},
        "CPP2": {"prompt": "C++ support", "help": None, "selects": []},
    }
    got = rename_candidates("CPLUSPLUS", old, new)
    assert [(n, c) for n, _, c in got] == [
        ("CPP", Confidence.MEDIUM),
        ("CPP2", Confidence.LOW),
        ("CPP_NEW", Confidence.MEDIUM),
    ]


def test_norm_value() -> None:
    assert norm_value("0x10") == norm_value("16") == "16"
    assert norm_value('"a"') == '"a"' and norm_value(None) is None


def test_parse_dotconfig() -> None:
    assert parse_dotconfig('CONFIG_A=y\n# CONFIG_B is not set\nCONFIG_C="x"\n# comment\n') == {
        "A": "y",
        "B": "n",
        "C": '"x"',
    }


def test_menuconfig_env(tmp_path: Path) -> None:
    ninja = tmp_path / "build.ninja"
    ninja.write_text(
        "build CMakeFiles/menuconfig | ${cmake_ninja_workdir}CMakeFiles/menuconfig: "
        "CUSTOM_COMMAND\n"
        "  COMMAND = cd /b/kconfig && /usr/bin/cmake -E env ZEPHYR_BASE=/z srctree=/z "
        'ARCH=* "APPVERSION=1 2" /venv/bin/python /z/scripts/kconfig/menuconfig.py /z/Kconfig\n'
        "  pool = console\n"
    )
    env, root = menuconfig_env(ninja)
    assert env == {"ZEPHYR_BASE": "/z", "srctree": "/z", "ARCH": "*", "APPVERSION": "1 2"}
    assert root == Path("/z/Kconfig")


def test_salvage_edits_keep_line_numbers(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "boards").mkdir(parents=True)
    (app / "prj.conf").write_text("CONFIG_A=y\nCONFIG_GONE=y\nCONFIG_B=y\n")
    (app / "app.overlay").write_text("&can0 {\n\tsjw = <1>;\n\tbitrate = <1>;\n};\n")
    log = (
        f"-- Found devicetree overlay: {app}/app.overlay\n"
        f"Merged configuration '{app}/prj.conf'\n"
        f"{app}/prj.conf:2: warning: attempt to assign the value 'y' to the undefined "
        f"symbol GONE\n"
        "devicetree error: 'sjw' appears in /can in /b/zephyr.dts.pre, but is not declared "
        "in 'properties:' in /z/x.yaml\n"
    )
    edits = locate_salvage_edits(log, app)
    assert [(e.file, e.line, e.text) for e in edits] == [
        ("app.overlay", 2, "\tsjw = <1>;"),
        ("prj.conf", 2, "CONFIG_GONE=y"),
    ]
    _apply_edits(app, edits)
    conf = (app / "prj.conf").read_text().splitlines()
    assert conf == ["CONFIG_A=y", f"# {SALVAGE_TAG}: CONFIG_GONE=y", "CONFIG_B=y"]
    overlay = (app / "app.overlay").read_text().splitlines()
    assert overlay[1] == f"/* {SALVAGE_TAG}: sjw = <1>; */" and len(overlay) == 4


def test_map_board() -> None:
    table = "set(native_sim_64_DEPRECATED\n    native_sim/native/64\n)\n"
    m = map_board("native_sim_64", None, [("v3.7.0", table)])
    assert (m.target, m.how, m.tag, m.line) == (
        "native_sim/native/64",
        "deprecated.cmake",
        "v3.7.0",
        1,
    )
    # Alias gone at the target (v4.4.2), found in an intermediate release's table.
    m = map_board("native_sim_64", None, [("v4.4.2", ""), ("v4.0.0", table)])
    assert (m.target, m.tag) == ("native_sim/native/64", "v4.0.0")
    assert map_board("native_sim_64@2", None, [("t", table)]).target == "native_sim@2/native/64"
    assert map_board("qemu_x86", None, [("t", table)]).how == "same"
    assert map_board("native_sim_64", "x/y", []).target == "x/y"
