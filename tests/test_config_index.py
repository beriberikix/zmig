"""The scan's Kconfig usage index comes from the fragments the build itself read, not from
`*.conf` file names (post-baseline generic fix). Regression: ZMK's `CONFIG_PINMUX=y` in
`planck_rev6_defconfig:10` was never indexed, so the scan never reported it."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from zmig.analyzers.sysbuild import user_settings
from zmig.scan import fragments_named, user_files
from zmig.usage import scan as usage_scan

APP = Path(__file__).parent / "fixtures" / "zmk-defconfig" / "app"


def _build(tmp: Path, name: str) -> SimpleNamespace:
    """A BuildContext's shape: the app copy that was built (inside the workspace), the
    workspace (Zephyr and modules) and the build directory."""
    ws = tmp / name / "ws"
    return SimpleNamespace(
        src=ws / "apps" / "abc" / "app",
        workspace=SimpleNamespace(root=ws),
        dir=tmp / name / "build",
    )


def test_defconfig_assignments_come_from_the_build_log(tmp_path: Path) -> None:
    old, new = _build(tmp_path, "old"), _build(tmp_path, "new")
    extra = tmp_path / "elsewhere" / "extra.conf"  # EXTRA_CONF_FILE outside the app
    extra.parent.mkdir()
    extra.write_text("CONFIG_ZMK_SLEEP=y\n")
    # The configure log's own lines (copy paths, as Zephyr prints them).
    log = "\n".join(
        [
            "Parsing .../Kconfig",
            f"Loaded configuration '{old.src}/boards/arm/planck/planck_rev6_defconfig'",
            f"Merged configuration '{old.src}/prj.conf'",
            f"Merged configuration '{extra}'",
            f"Merged configuration '{old.workspace.root}/zephyr/boards/st/x/x_defconfig'",
            f"Merged configuration '{old.dir}/zephyr/misc/generated/extra_kconfig_options.conf'",
        ]
    )
    files = user_files(fragments_named(log), APP, [old, new])  # type: ignore[list-item]
    assert files == [
        (APP / "boards/arm/planck/planck_rev6_defconfig").resolve(),
        (APP / "prj.conf").resolve(),
        extra.resolve(),
    ]
    u = usage_scan(APP, conf_files=files)
    ((loc, value),) = u.kconfig_set["PINMUX"]
    assert (loc.path, loc.line, loc.text, value) == (
        "boards/arm/planck/planck_rev6_defconfig",
        10,
        "CONFIG_PINMUX=y",
        "y",
    )
    assert u.kconfig_set["CLOCK_CONTROL"][0][0].line == 17
    # An app .conf the build didn't read isn't indexed; a fragment outside the app is,
    # under a path relative to the app.
    assert all(loc.path != "boards/dz60rgb_unused.conf" for loc, _ in u.kconfig_set["PINMUX"])
    assert u.kconfig_set["ZMK_SLEEP"][0][0].path == os.path.relpath(extra.resolve(), APP.resolve())


def test_without_a_fragment_list_the_old_name_rule_applies() -> None:
    """When the configure never reached Kconfig, there's no list: `*.conf` names are the
    fallback (and a `_defconfig` isn't read)."""
    u = usage_scan(APP)
    assert [loc.path for loc, _ in u.kconfig_set["PINMUX"]] == ["boards/dz60rgb_unused.conf"]


def test_sysbuild_settings_come_from_the_build_log(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "sysbuild.conf").write_text("SB_CONFIG_BOOTLOADER_MCUBOOT=y\n")
    sb = tmp_path / "sb-extra.conf"  # SB_CONF_FILE outside the app
    sb.write_text("# x\nSB_CONFIG_MCUBOOT_MODE_SWAP_USING_MOVE=y\n")
    got = user_settings(app, [sb])
    assert got == {
        "MCUBOOT_MODE_SWAP_USING_MOVE": (
            "../sb-extra.conf",
            2,
            "y",
            "SB_CONFIG_MCUBOOT_MODE_SWAP_USING_MOVE=y",
        )
    }
    assert user_settings(app)["BOOTLOADER_MCUBOOT"][:2] == ("sysbuild.conf", 1)
