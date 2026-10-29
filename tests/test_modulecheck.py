"""Module-set changes: modules the target build needs that the scan lacked
(regression: bms-firmware v4.0 -> v4.2 lost its target build to cmsis_6), and how the
modules the source build uses changed in Zephyr's manifest."""

# ruff: noqa: E501  (verbatim log lines)

from __future__ import annotations

from pathlib import Path

from zmig.manifest import ProjectInfo
from zmig.modulecheck import diagnose, manifest_changes, module_var

WEST_YML = """\
manifest:
  projects:
    - name: cmsis
      revision: 512cc7e895e8
      path: modules/hal/cmsis
    - name: cmsis_6
      revision: 06d952b6713a
      path: modules/hal/cmsis_6
    - name: zcbor
      revision: 9b07780aca6f
      path: modules/lib/zcbor
    - name: tinycrypt
      revision: bbbb
      path: modules/crypto/tinycrypt
    - name: mbedtls
      revision: cccc
      path: modules/crypto/mbedtls
    - name: trusted-firmware-m
      revision: aaaa
      path: modules/tee/tf-m/trusted-firmware-m
"""

CMSIS_LOG = """\
[12/160] Building C object zephyr/CMakeFiles/zephyr.dir/lib/os/cbprintf_packaged.c.obj
FAILED: zephyr/CMakeFiles/zephyr.dir/lib/os/cbprintf_packaged.c.obj
In file included from /ws/zephyr/include/zephyr/arch/arm/arch_inlines.h:13,
                 from /ws/zephyr/include/zephyr/arch/inlines.h:15:
/ws/zephyr/include/zephyr/arch/arm/asm_inline_gcc.h:24:10: fatal error: cmsis_core.h: No such file or directory
   24 | #include <cmsis_core.h>
"""

ZCBOR_LOG = """\
warning: ZCBOR (defined at modules/zcbor/Kconfig:7, modules/zcbor/Kconfig:7) was assigned the value 'y' but got the value 'n'. Check these unsatisfied dependencies: (ZEPHYR_ZCBOR_MODULE || (ZEPHYR_ZCBOR_MODULE && 0)) (=n). See http://docs.zephyrproject.org/latest/kconfig.html#CONFIG_ZCBOR and/or look up ZCBOR in the menuconfig/guiconfig interface.

"""

CMAKE_LOG = """\
CMake Error at /ws/zephyr/modules/trusted-firmware-m/CMakeLists.txt:12 (message):
  ZEPHYR_TRUSTED_FIRMWARE_M_MODULE_DIR is not set
Call Stack (most recent call first):
  /ws/zephyr/cmake/modules/zephyr_default.cmake:133 (include)
"""


def _tree(tmp: Path) -> tuple[Path, dict[str, ProjectInfo]]:
    zephyr = tmp / "zephyr"
    for glue in ("cmsis", "cmsis_6"):
        (zephyr / "modules" / glue).mkdir(parents=True)
        (zephyr / "modules" / glue / "cmsis_core.h").write_text("")
    (zephyr / "west.yml").write_text(WEST_YML)
    manifest = {
        n: ProjectInfo(n, f"https://x/{n}", rev, path)
        for n, rev, path in (
            ("cmsis", "512cc7e895e8", "modules/hal/cmsis"),
            ("cmsis_6", "06d952b6713a", "modules/hal/cmsis_6"),
            ("zcbor", "9b07780aca6f", "modules/lib/zcbor"),
            ("tinycrypt", "bbbb", "modules/crypto/tinycrypt"),
            ("mbedtls", "cccc", "modules/crypto/mbedtls"),
            ("trusted-firmware-m", "aaaa", "modules/tee/tf-m/trusted-firmware-m"),
        )
    }
    return zephyr, manifest


def test_missing_header_names_the_inactive_module_that_ships_it(tmp_path: Path) -> None:
    zephyr, manifest = _tree(tmp_path)
    (need,) = diagnose(CMSIS_LOG, zephyr, manifest, active={"cmsis", "hal_stm32"})
    assert need.module == "cmsis_6"
    assert need.site == "/ws/zephyr/include/zephyr/arch/arm/asm_inline_gcc.h:24"
    assert need.declared == "west.yml:6"
    assert need.alternatives == ("cmsis",)
    assert "cmsis_core.h" in need.why


def test_no_guess_when_ambiguous_or_already_active(tmp_path: Path) -> None:
    zephyr, manifest = _tree(tmp_path)
    # Neither glue module is active: two candidates, so no claim.
    assert diagnose(CMSIS_LOG, zephyr, manifest, active=set()) == []
    # Both active: the header is missing for another reason.
    assert diagnose(CMSIS_LOG, zephyr, manifest, active={"cmsis", "cmsis_6"}) == []
    # A header no module glue ships says nothing about modules.
    log = CMSIS_LOG.replace("cmsis_core.h", "lfs.h")
    assert diagnose(log, zephyr, manifest, active={"cmsis"}) == []


def test_kconfig_and_cmake_module_dependencies(tmp_path: Path) -> None:
    zephyr, manifest = _tree(tmp_path)
    (need,) = diagnose(ZCBOR_LOG, zephyr, manifest, active={"cmsis"})
    assert (need.module, need.declared) == ("zcbor", "west.yml:9")
    assert "ZEPHYR_ZCBOR_MODULE" in need.why
    (need,) = diagnose(CMAKE_LOG, zephyr, manifest, active=set())
    assert need.module == "trusted-firmware-m"
    assert module_var("trusted-firmware-m") == "TRUSTED_FIRMWARE_M"


def test_manifest_changes_of_used_modules(tmp_path: Path) -> None:
    old = {
        "cmsis": ProjectInfo("cmsis", "u", "4b96cbb17467", "p"),
        "hal_stm32": ProjectInfo("hal_stm32", "u", "019d8255", "p"),
        "nanopb": ProjectInfo("nanopb", "u", "aaaa", "p"),
    }
    new = {
        "cmsis": ProjectInfo("cmsis", "u", "512cc7e895e8", "p"),
        "hal_stm32": ProjectInfo("hal_stm32", "u", "019d8255", "p"),
    }
    changes = manifest_changes(
        ["cmsis", "hal_stm32", "nanopb", "my-own-module"], old, new, tmp_path, tmp_path
    )
    assert [(c.module, c.kind) for c in changes] == [("cmsis", "repinned"), ("nanopb", "removed")]


NRF_KCONFIG_LOG = """\
-- Generated devicetree_generated.h: /b/zephyr/include/generated/zephyr/devicetree_generated.h

warning: CMSIS_CORE_HAS_SYSTEM_CORE_CLOCK (defined at soc/nxp/imxrt/imxrt6xx/Kconfig.defconfig:8, modules/cmsis_6/Kconfig:23) has direct dependencies (SOC_SERIES_IMXRT6XX && SOC_FAMILY_NXP_IMXRT) || (HAS_CMSIS_CORE && 0) with value n, but is currently being y-selected by the following symbols:
 - SOC_FAMILY_NORDIC_NRF (defined at soc/nordic/Kconfig.soc:19, soc/nordic/Kconfig:9), with value y, direct dependencies y (value: y), and select condition ARM (value: y)

error: Aborting due to Kconfig warnings
"""


def test_glue_kconfig_of_an_absent_module(tmp_path: Path) -> None:
    """nrf52840dk at v4.2 without cmsis_6 fails at configure, not compile."""
    zephyr, manifest = _tree(tmp_path)
    (need,) = diagnose(NRF_KCONFIG_LOG, zephyr, manifest, active={"cmsis", "hal_nordic"})
    assert need.module == "cmsis_6"
    assert need.site.startswith("warning: CMSIS_CORE_HAS_SYSTEM_CORE_CLOCK")
    assert diagnose(NRF_KCONFIG_LOG, zephyr, manifest, active={"cmsis_6"}) == []


# bms-firmware on native_sim: Bluetooth host crypto selects TinyCrypt at v4.0 and mbedtls
# at v4.2; the pre-upgrade module list had neither.
TINYCRYPT_LOG = """\
warning: TINYCRYPT (defined at modules/Kconfig.tinycrypt:9) has direct dependencies ZEPHYR_TINYCRYPT_MODULE with value n, but is currently being y-selected by the following symbols:
 - BT_HOST_CRYPTO (defined at subsys/bluetooth/host/Kconfig:168), with value y, direct dependencies BT_HCI_HOST && BT_HCI && BT (value: y), and select condition !BT_USE_PSA_API && BT_HCI_HOST && BT_HCI && BT (value: y)
Parsing /ws/apps/x/app/Kconfig
"""

MBEDTLS_LOG = """\
warning: MBEDTLS (defined at modules/mbedtls/Kconfig:19) has direct dependencies 0 with value n, but is currently being y-selected by the following symbols:
 - BT_HOST_CRYPTO (defined at subsys/bluetooth/host/Kconfig:172), with value y, direct dependencies BT_HCI_HOST && BT_HCI && BT (value: y), and select condition !BUILD_WITH_TFM && BT_HCI_HOST && BT_HCI && BT (value: y)

warning: PSA_WANT_KEY_TYPE_AES (defined at modules/mbedtls/Kconfig.psa.auto:267) has direct dependencies PSA_CRYPTO_CLIENT && 0 with value n, but is currently being y-selected by the following symbols:
 - BT_HOST_CRYPTO (defined at subsys/bluetooth/host/Kconfig:172), with value y
"""


def test_select_of_an_absent_module_symbol(tmp_path: Path) -> None:
    zephyr, manifest = _tree(tmp_path)
    (need,) = diagnose(TINYCRYPT_LOG, zephyr, manifest, active={"zcbor"})
    assert need.module == "tinycrypt"
    assert need.site.startswith("warning: TINYCRYPT (defined at modules/Kconfig.tinycrypt:9)")
    (need,) = diagnose(MBEDTLS_LOG, zephyr, manifest, active={"zcbor"})
    assert need.module == "mbedtls"
    assert diagnose(MBEDTLS_LOG, zephyr, manifest, active={"mbedtls"}) == []


# The v4.1.0 configure of samples/subsys/settings without littlefs (AUDIT.md §5.2 run 1);
# v4.2.0 printed the same.
LITTLEFS_LOG = """\
warning: FILE_SYSTEM_LITTLEFS (defined at subsys/fs/Kconfig.littlefs:6) was assigned the value 'y'
but got the value 'n'. Check these unsatisfied dependencies: ZEPHYR_LITTLEFS_MODULE (=n). See
http://docs.zephyrproject.org/latest/kconfig.html#CONFIG_FILE_SYSTEM_LITTLEFS and/or look up
FILE_SYSTEM_LITTLEFS in the menuconfig/guiconfig interface.

"""


def test_modules_the_source_build_needs_too(tmp_path: Path) -> None:
    """A module zmig added to the target that the source's own failure names as well is an
    invocation gap at both refs, not a migration requirement."""
    from zmig.modulecheck import needed_at_source

    zephyr, manifest = _tree(tmp_path)
    manifest["littlefs"] = ProjectInfo(
        "littlefs", "https://x/littlefs", "ed0531d5", "modules/fs/littlefs"
    )
    assert needed_at_source(LITTLEFS_LOG, zephyr, manifest, set(), ["littlefs"]) == ["littlefs"]
    assert needed_at_source(LITTLEFS_LOG, zephyr, manifest, set(), ["cmsis_6"]) == []
    assert needed_at_source("", zephyr, manifest, set(), ["littlefs"]) == []
    # Active at the source: the source didn't lack it.
    assert needed_at_source(LITTLEFS_LOG, zephyr, manifest, {"littlefs"}, ["littlefs"]) == []
