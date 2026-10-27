"""Per-image building blocks: log splitting, image discovery, sysbuild pair rules,
partition extraction, wrapped warnings, linker errors."""

from __future__ import annotations

from pathlib import Path

from zmig.analyzers.buildlog import build_errors
from zmig.analyzers.crossimage import partitions
from zmig.buildctx import SYSBUILD_SEGMENT, BuildSpec, discover_images, split_configure_log
from zmig.diagnostics import parse_warnings
from zmig.sysbuild_rules import pairs, parse

# Shape of a real sysbuild configure (v4.4.0), stdout and stderr merged.
CONFIGURE = """\
-- west build: generating a build system
Parsing /z/share/sysbuild/Kconfig
--
   *****************************
   * Running CMake for mcuboot *
   *****************************

-- Application: /ws/bootloader/mcuboot/boot/zephyr
warning: QEMU_ICOUNT_SHIFT (defined at boards/Kconfig:81) was assigned the value '6' but got the
value ''. Check these unsatisfied dependencies: QEMU_ICOUNT (=n).
-- Build files have been written to: /b/mcuboot
--
   **************************
   * Running CMake for xapp *
   **************************

-- Application: /ws/apps/x/xapp
warning: Deprecated symbol FOO is enabled.
-- Build files have been written to: /b/xapp
-- Build files have been written to: /b
"""
IMAGES = [("mcuboot", Path("/b/mcuboot")), ("xapp", Path("/b/xapp"))]


def test_split_configure_by_banner() -> None:
    seg = split_configure_log(CONFIGURE, IMAGES)
    assert set(seg) == {SYSBUILD_SEGMENT, "mcuboot", "xapp"}
    assert "sysbuild/Kconfig" in seg[SYSBUILD_SEGMENT]
    assert "QEMU_ICOUNT_SHIFT" in seg["mcuboot"] and "QEMU_ICOUNT_SHIFT" not in seg["xapp"]
    assert "Deprecated symbol FOO" in seg["xapp"] and "FOO" not in seg["mcuboot"]


def test_split_without_banners() -> None:
    text = CONFIGURE.replace("Running CMake for", "Configuring")
    seg = split_configure_log(text, IMAGES)
    assert "QEMU_ICOUNT_SHIFT" in seg["mcuboot"] and "FOO" in seg["xapp"]
    plain = "-- Application: /a\nwarning: x\n-- Build files have been written to: /b\n"
    assert split_configure_log(plain, [("app", Path("/b"))]) == {"app": plain}


def test_discover_images(tmp_path: Path) -> None:
    spec = BuildSpec(app=tmp_path / "xapp", board="b", sysbuild=True)
    (tmp_path / "domains.yaml").write_text(
        f"default: xapp\nbuild_dir: {tmp_path}\ndomains:\n"
        f"  - name: xapp\n    build_dir: {tmp_path}/xapp\n"
        f"  - name: mcuboot\n    build_dir: {tmp_path}/mcuboot\n"
        "flash_order:\n  - mcuboot\n  - xapp\n"
    )
    assert [n for n, _ in discover_images(tmp_path, spec)] == ["mcuboot", "xapp"]
    (tmp_path / "domains.yaml").unlink()
    (tmp_path / "mcuboot").mkdir()
    (tmp_path / "mcuboot" / "CMakeCache.txt").write_text("")
    # Sysbuild configure failed before domains.yaml: what CMake created, plus the app.
    assert [n for n, _ in discover_images(tmp_path, spec)] == ["mcuboot", "xapp"]
    plain = BuildSpec(app=tmp_path / "hello", board="b")
    assert discover_images(tmp_path, plain) == [("hello", tmp_path)]


MAIN = """\
set_config_bool(${ZCMAKE_APPLICATION} CONFIG_BOOTLOADER_MCUBOOT "${SB_CONFIG_BOOTLOADER_MCUBOOT}")
if(SB_CONFIG_BOOTLOADER_MCUBOOT)
  if(SB_CONFIG_MCUBOOT_MODE_SWAP_USING_OFFSET)
    set_config_bool(${ZCMAKE_APPLICATION} CONFIG_MCUBOOT_BOOTLOADER_MODE_SWAP_USING_OFFSET y)
  elseif(SB_CONFIG_MCUBOOT_MODE_SWAP_USING_MOVE)
    set_config_bool(${ZCMAKE_APPLICATION} CONFIG_MCUBOOT_BOOTLOADER_MODE_SWAP_USING_MOVE y)
  endif()
  if(SB_CONFIG_X)
    set_config_bool(${ZCMAKE_APPLICATION} CONFIG_REBOOT y)
  endif()
endif()
"""
BOOT = """\
set(bootmodes CONFIG_BOOT_SWAP_USING_OFFSET
              CONFIG_BOOT_SWAP_USING_MOVE)
if(SB_CONFIG_MCUBOOT_MODE_SWAP_USING_OFFSET)
  set(bootmode CONFIG_BOOT_SWAP_USING_OFFSET)
elseif(SB_CONFIG_MCUBOOT_MODE_SWAP_USING_MOVE)
  set(bootmode CONFIG_BOOT_SWAP_USING_MOVE)
endif()
foreach(loopbootmode ${bootmodes})
  if("${loopbootmode}" STREQUAL "${bootmode}")
    set_config_bool(${ZCMAKE_APPLICATION} ${loopbootmode} y)
  else()
    set_config_bool(${ZCMAKE_APPLICATION} ${loopbootmode} n)
  endif()
endforeach()
if(SB_CONFIG_X)
  set_config_bool(${ZCMAKE_APPLICATION} CONFIG_RETENTION y) # generic option
endif()
"""


def test_sysbuild_pairs_from_source() -> None:
    a, b = parse(MAIN, "app", "MAIN.cmake"), parse(BOOT, "bootloader", "BOOT.cmake")
    got = {
        (p.trigger, p.app.option, p.bootloader.option, p.app.line, p.bootloader.line)
        for p in pairs(a, b)
    }
    assert got == {
        (
            "SB_CONFIG_MCUBOOT_MODE_SWAP_USING_OFFSET",
            "CONFIG_MCUBOOT_BOOTLOADER_MODE_SWAP_USING_OFFSET",
            "CONFIG_BOOT_SWAP_USING_OFFSET",
            4,
            10,
        ),
        (
            "SB_CONFIG_MCUBOOT_MODE_SWAP_USING_MOVE",
            "CONFIG_MCUBOOT_BOOTLOADER_MODE_SWAP_USING_MOVE",
            "CONFIG_BOOT_SWAP_USING_MOVE",
            6,
            10,
        ),
        ("SB_CONFIG_X", "CONFIG_REBOOT", "CONFIG_RETENTION", 9, 16),
    }


def test_partitions() -> None:
    def node(name: str, labels: list[str], compats: list[str], reg: object) -> dict[str, object]:
        return {"name": name, "labels": labels, "compats": compats, "props": {"reg": reg}}

    tree = {
        "nodes": {
            "/flash@0": node("flash@0", [], [], None),
            "/flash@0/partitions": node("partitions", [], ["fixed-partitions"], None),
            "/flash@0/partitions/partition@0": node(
                "partition@0", ["boot_partition"], [], [0, 0xC000]
            ),
            "/flash@0/partitions/partition@c000": node("partition@c000", [], [], [0xC000, 4]),
            "/other": node("other", ["x"], [], [1, 2]),
        }
    }
    assert partitions(tree) == {
        "boot_partition": ("/flash@0/partitions/partition@0", [0, 0xC000]),
        "partition@c000": ("/flash@0/partitions/partition@c000", [0xC000, 4]),
    }


def test_wrapped_kconfig_warning_is_whole() -> None:
    (w,) = [w for w in parse_warnings(CONFIGURE) if "QEMU" in w.message]
    assert w.message.endswith("Check these unsatisfied dependencies: QEMU_ICOUNT (=n).")


def test_linker_error_names_the_image() -> None:
    log = (
        "FAILED: [code=1] zephyr/zephyr_pre0.elf /b/0123abcd/builds/0123abcd/mcuboot/zephyr/"
        "zephyr_pre0.map\n"
        "/sdk/arm-zephyr-eabi/bin/ld.bfd: region `RAM' overflowed by 14816 bytes\n"
        "collect2: error: ld returned 1 exit status\n"
    )
    log = log.replace("/b/0123abcd/builds/0123abcd/", "/c/zmig/builds/0123abcd/")
    (e,) = build_errors(log)
    assert (e.kind, e.image, e.message) == (
        "linker",
        "mcuboot",
        "region `RAM' overflowed by 14816 bytes",
    )


def test_partitions_mapped_representation() -> None:
    """v4.4+: each partition is a `zephyr,mapped-partition` node under a `partitions` node
    that has no compatible (the fixed-partitions rule alone found nothing here)."""

    def node(name: str, labels: list[str], compats: list[str], reg: object) -> dict[str, object]:
        return {"name": name, "labels": labels, "compats": compats, "props": {"reg": reg}}

    tree = {
        "nodes": {
            "/f/partitions": node("partitions", [], [], None),
            "/f/partitions/partition@82000": node(
                "partition@82000",
                ["slot1_partition"],
                ["zephyr,mapped-partition"],
                [0x82000, 0x76000],
            ),
        }
    }
    assert partitions(tree) == {
        "slot1_partition": ("/f/partitions/partition@82000", [0x82000, 0x76000])
    }
