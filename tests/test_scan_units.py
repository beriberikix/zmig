"""Unit tests for scan building blocks that don't need a Zephyr build."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_header_changed_needs_api_coverage_to_be_demoted() -> None:
    """AUDIT.md F8/§5.2: <zephyr/settings/settings.h> changed (+51/-9) but libclang
    compared every declaration src/main.c uses and found none changed; a byte diff alone
    is not a risk. Conservative: every including site must be a TU compared cleanly at
    both refs with no API finding in it."""
    from zmig.analyzers.sbom import api_covers

    cov = {"tus": 2, "not_interpreted": 0, "clean_tus": ["src/main.c", "src/other.c"]}
    assert api_covers({"src/main.c"}, cov, set())
    assert api_covers({"src/main.c", "src/other.c"}, cov, set())
    assert not api_covers({"src/main.c"}, cov, {"src/main.c"})  # an API finding there
    assert not api_covers({"src/main.c", "src/bad.c"}, cov, set())  # bad.c had errors
    assert not api_covers({"src/app.h"}, cov, set())  # included via a project header
    assert not api_covers({"src/main.c"}, None, set())  # API analyzer didn't run
    assert not api_covers({"src/main.c"}, {"tus": 1, "not_interpreted": 0}, set())  # old record
    assert not api_covers(set(), cov, set())


def _copies(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Two copies of one app tree in a zmig workspace (apps/<tree hash>/<basename>), and
    a build directory configured from the first one."""
    ws = tmp_path / "ws"
    first, second = (
        ws / "apps" / "81bec4faf9ea0405" / "app",
        ws / "apps" / "81bec4faf9ea0405" / "fixture-app",
    )
    build = tmp_path / "build"
    build.mkdir()
    (build / "CMakeCache.txt").write_text(f"APPLICATION_SOURCE_DIR:PATH={first}\n")
    return first, second, build


def test_project_roots_follow_the_copy_a_cached_build_was_configured_from(tmp_path: Path) -> None:
    """The build key hashes the app's content, not its directory name, so tests/fixtures/app
    and a copy named fixture-app share one build whose ninja records name .../app/src/main.c
    (found while verifying AUDIT.md F7: every source then looked uncompiled)."""
    from types import SimpleNamespace

    from zmig.buildctx import project_path, project_roots

    first, second, build = _copies(tmp_path)
    b = SimpleNamespace(src=second, workspace=SimpleNamespace(root=tmp_path / "ws"))
    roots = project_roots(b, build)  # type: ignore[arg-type]
    assert project_path(f"{first}/src/main.c", roots) == "src/main.c"
    assert project_path(f"{second}/src/main.c", roots) == "src/main.c"

    # A sysbuild image configured from another app (MCUboot) is not an alias of this one.
    (build / "CMakeCache.txt").write_text(
        f"APPLICATION_SOURCE_DIR:PATH={tmp_path}/ws/bootloader/mcuboot/boot/zephyr\n"
    )
    roots = project_roots(b, build)  # type: ignore[arg-type]
    assert project_path(f"{tmp_path}/ws/bootloader/mcuboot/boot/zephyr/main.c", roots) is None


def test_build_files_unknown_when_no_record_maps_to_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An app always compiles a file of its own: ninja records that map to none mean the
    mapping failed, not that nothing was compiled."""
    from types import SimpleNamespace

    from zmig import scan

    first, second, build = _copies(tmp_path)
    (build / "CMakeCache.txt").write_text("")  # no recorded source dir to follow
    deps = {"CMakeFiles/app.dir/src/main.c.obj": [f"{first}/src/main.c", "/z/include/k.h"]}
    monkeypatch.setattr(scan, "build_dependencies", lambda img, runner: deps)
    fake = SimpleNamespace(
        old=SimpleNamespace(src=second, workspace=SimpleNamespace(root=tmp_path / "ws")),
        old_img=SimpleNamespace(dir=build),
        runner=None,
    )
    assert scan.ScanContext.build_files.func(fake) is None  # type: ignore[attr-defined]
    deps["CMakeFiles/app.dir/src/main.c.obj"].append(f"{second}/src/main.c")
    assert scan.ScanContext.build_files.func(fake) == {"src/main.c"}  # type: ignore[attr-defined]
