# Oracle: PROVES Core Reference, Zephyr v4.3.0 → v4.4.1 (PR #421)

Written 2026-10-30, **before zmig was run on this project**. Only `--analyzers none`
verification builds were run, and they produce no findings. The rules are in
[../ORACLE-RULES.md](../ORACLE-RULES.md). This file isn't edited after the run;
corrections go at the end, dated.

This is a held-out project. It's unseen: no zmig change has been informed by it.

## Upgrade and scanned build

**Project.** PROVES Core Reference
(https://github.com/Open-Source-Space-Foundation/proves-core-reference, Apache-2.0), CubeSat
flight software built with F´ (fprime) on Zephyr, for the PROVES flight control board
(RP2350A, Cortex-M33).

- The repository root is both the F´ project and the Zephyr application: its
  `CMakeLists.txt` does `find_package(Zephyr …)`, `list(APPEND BOARD_ROOT
  ${CMAKE_CURRENT_LIST_DIR})`, then includes `lib/fprime/cmake/FPrime.cmake` and adds the
  deployment `PROVESFlightControllerReference/`. Its `prj.conf` and `Kconfig` are at the root.
- Its own boards are in `boards/bronco_space/`: `proves_flight_control_board_v5c`, `v5d`
  and `v5e` (HWMv2), which all `#include` the shared
  `proves_flight_control_board_v5/proves_flight_control_board_v5.dtsi` (and its
  `-pinctrl.dtsi`).
- F´ code comes from git submodules: `lib/fprime` (the framework), `lib/fprime-zephyr`
  (F´ drivers and OS layer for Zephyr, the project's own fork), `lib/fprime-extras`, and
  `lib/RadioLib`. None of them, and not the repository itself, is a Zephyr module (no
  `zephyr/module.yml`).
- Zephyr is pinned twice: `west.yml` (`revision:` plus a name-allowlist and explicit module
  pins) and the submodule `lib/zephyr-workspace/zephyr`.

**Commits.** The upgrade is one squashed PR:

| Role | Commit | Date | Zephyr | fprime-zephyr submodule |
|---|---|---|---|---|
| Source tree (the PR's base) | `5c67bae2fde7a2c43807d6e9b4e04fd89db45cd3` "add throttle (#453)" | 2026-07-20 | `revision: v4.3.0`; submodule `3568e1b6d5cd` | `3139971` |
| The upgrade, "Upgrade Zephyr to 4.4.1 (#421)" | `606eb0ab9e786951a1b12f26ea78cca26e4cb880` (squash) | 2026-07-20 | `revision: v4.4.1`; submodule `1f6485eca254` | `7643509` |

**Zephyr refs:**

- from `v4.3.0` = `3568e1b6d5cdd51a6b964a2a1d6d29200fea2056`;
- to `v4.4.1` = `1f6485eca25431b5ff27ce9a754218c9e559bbbb`.

**Configuration** (this file; `eval/configs.toml`, configuration `proves`):

- **App:** the repository root, on board `proves_flight_control_board_v5e/rp2350a/m33`.
  That's the board in `settings.ini` (`fprime-util generate`, which `make build` and the CI
  build job run) and the board the PR was tested on ("board v5e/rp2350a/m33, SDK 1.0.1").
- **CMake arguments**, the ones `fprime-util generate` derives from `settings.ini`:
  - `-DCMAKE_TOOLCHAIN_FILE=lib/fprime-zephyr/cmake/toolchain/zephyr.cmake` (sets
    `FPRIME_PLATFORM Zephyr` and defines `register_fprime_zephyr_deployment`);
  - `-DFPRIME_LIBRARY_LOCATIONS=lib/fprime-zephyr;lib/fprime-extras`.
  - Both are relative, so they resolve inside zmig's copy of the app. `BOARD_ROOT` comes
    from the app's own `CMakeLists.txt`, and no `ZEPHYR_EXTRA_MODULES` is needed.
  - The other `settings.ini` options (`FPRIME_ENABLE_*_UTS=OFF`, `FPRIME_CMAKE_QUIET`,
    `FPRIME_INSTALL_STATIC_LIBRARIES`) aren't passed. The first two are forced by the
    `CMakeLists.txt` anyway.
- **Submodules:** `lib/fprime`, `lib/fprime-zephyr`, `lib/fprime-extras`, `lib/RadioLib` at
  the base commit's pins. `lib/zephyr-workspace/zephyr` isn't checked out (zmig supplies
  Zephyr).
- **Modules:** `cmsis cmsis_6 hal_rpi_pico picolibc mbedtls tinycrypt mcuboot fatfs hal_st
  loramac-node`: the source `west.yml`'s allowlist plus its explicit `loramac-node`.
  - `tinycrypt` is in neither Zephyr manifest (removed in v4.3.0), so zmig skips it at both
    refs ("not in v4.3.0's manifest, skipped").
  - `tf-psa-crypto` (v4.4 only) isn't passed, matching the source manifest (as for ardep).
  - zmig takes each module's revision from **Zephyr's** manifest, not from the project's
    explicit pins. At the source those differ for `cmsis_6` (project `06d952b`, v4.3.0
    `30a859f`), `picolibc` (`560946f` vs `ca8b6eb`) and `mbedtls` (`85440ef` vs `c5b06d8`).
    So the scanned source build isn't byte-for-byte the maintainers' source build. After the
    PR, every pin equals v4.4.1's manifest (item P4).
- **SDK:** no `sdk` key; 0.17.4 and 1.0.1 both go on `CMAKE_PREFIX_PATH` (as for ardep).
  The source configure log says "Found toolchain: zephyr 0.17.4".
- **Python tools.** CMake needs `fprime-util` and the `fpp-*` tools on `PATH`
  (`lib/fprime/cmake/required.cmake`). zmig puts its own venv's `bin/` on `PATH`, so
  `fprime-tools==4.1.0` and `fprime-fpp==3.1.0` (the pins in `lib/fprime/requirements.txt`)
  were installed into `/home/…/code/zmig/.venv` with `uv pip install`. That added 21
  packages and changed none of zmig's. `fprime-version-check` then fails on a missing
  `pkg_resources` (Python 3.14); F´ prints a warning and skips the check, which is harmless.
- **App shim (generated files).** `make generate` creates two git-ignored files before
  CMake runs, and the source build needs both:
  - `PROVESFlightControllerReference/Components/TcSecurityDeframer/AuthDefaultKey.h` (from
    `scripts/generate_auth_default_key.h`, with a random key; `TcSecurityDeframer.cpp:17`
    includes it). The shim uses an all-zero key.
  - `keys/proves.pem`, a copy of MCUboot's `root-rsa-2048.pem` (the Makefile's
    `keys/proves.pem` rule). `prj.conf:77` sets `CONFIG_MCUBOOT_SIGNATURE_KEY_FILE` to it,
    and `cmake/mcuboot.cmake` stops the configure with a fatal error when the file is
    missing.
  - The harness applies `eval/patches/proves-generated-files.patch` (`app_patch`):
    - it adds `AuthDefaultKey.h`;
    - instead of adding the key file (a private key isn't committed to zmig's repository),
      it changes `prj.conf:77` to `CONFIG_MCUBOOT_SIGNATURE_KEY_FILE="bootloader/mcuboot/root-rsa-2048.pem"`.
      That is the same key, found through the west workspace (relative key paths are
      searched there). A `-DCONFIG_…` argument doesn't work, because F´'s CMake sub-build
      doesn't pass it on.
  - `prj.conf:77` is the only tracked line that differs, and it is identical at both refs.
    None of the PR's files is touched.
  - **This is a zmig limitation in itself**: an app whose build depends on files a wrapper
    (`make`, `fprime-util`) generates before CMake can't be scanned unmodified.

**Scope evidence** comes from the verification builds
(`eval/run.py scan --analyzers none … proves`):

- **Source (`v4.3.0`): ok**, full build (build `23c068fcaa5a6c20` with the first shim,
  1315 ninja steps; `4ac806dc5ac5bcd8` with the final one; `zephyr.elf` and
  `zephyr.signed.{bin,hex}` produced by both).
- **Target (`v4.4.1`): configure-failed** (build `4acb4d4fc20b512c`; `a04e7e1a39235a84`
  with the final shim, the same failure). The devicetree was
  generated (`zephyr.dts`, `edt.pickle`), and Kconfig wrote `.config` with the same two
  warnings as the source (`PRINTK`, `LOG_DEFAULT_LEVEL`). CMake then stopped in the
  Mbed TLS module:
  - `modules/crypto/mbedtls/CMakeLists.txt:346 (add_subdirectory): add_subdirectory given
    source ".../modules/crypto/mbedtls/tf-psa-crypto" which is not an existing directory`;
  - `:366: TF-PSA-Crypto target tfpsacrypto does not exist.`
  - Nothing was compiled at the target. zmig's salvage rebuild (`9ede4f181abb780f`,
    2 lines disabled) failed the same way.
- **Project files compiled at the source** (`compile_commands.json`), among them:
  - `PROVESFlightControllerReference/ReferenceDeployment/Main.cpp`;
  - `PROVESFlightControllerReference/Components/TcSecurityDeframer/Authenticator.cpp`
    (PSA HMAC);
  - `lib/fprime-zephyr/fprime-zephyr/Drv/LoRa/LoRa.cpp`, and the autocoded
    `fprime-zephyr/Drv/LoRa/LoRaBandwidthEnumAc.cpp` (from `LoRa.fpp`).
- **Zephyr and module code compiled at the source:** `drivers/i2c/i2c_tca954x.c`,
  `drivers/adc/adc_rpi_pico.c`, `subsys/storage/flash_map/flash_map.c`,
  `drivers/lora/loramac_node/{sx12xx_common,sx126x,sx126x_standalone}.c`, loramac-node's
  `sx126x` radio, Mbed TLS (`psa_crypto.c`, …), MCUboot's bootutil, fatfs.
- **Modules used at the source** (ninja deps): `cmsis_6` (1296 headers), `hal_rpi_pico`
  (660), `mbedtls` (2725), `loramac-node` (59), `mcuboot` (23, plus imgtool for signing),
  `fatfs` (17), `hal_st` (8). Not used: `cmsis` (no file) and `picolibc` (no file:
  `CONFIG_PICOLIBC_USE_TOOLCHAIN=y`).
- **Devicetree, at both refs** (`zephyr.dts.d`): the board's
  `proves_flight_control_board_v5e_rp2350a_m33.dts`, which includes
  `../proves_flight_control_board_v5/proves_flight_control_board_v5.dtsi` and
  `…-pinctrl.dtsi`.
- **Kconfig, at both refs** (`kconfig/sources.txt`): the app's `Kconfig`, the board's
  `Kconfig.proves_flight_control_board_v5e`, and
  `proves_flight_control_board_v5/Kconfig.defconfig`. Merged: the board defconfig and
  `prj.conf`.
- **Config at the source:**
  - `CONFIG_I2C_TCA954X=y`, `CONFIG_DT_HAS_TI_TCA9548A_ENABLED=y`;
  - `CONFIG_LORA=y`, `CONFIG_LORA_SX126X=y`, `CONFIG_LORA_MODULE_BACKEND_LORAMAC_NODE=y`
    (v5e's `lora0` is overridden to `semtech,sx1262`);
  - `CONFIG_MBEDTLS=y`, `CONFIG_MBEDTLS_BUILTIN=y`, `CONFIG_MBEDTLS_PSA_CRYPTO_C=y`;
  - `CONFIG_FLASH_MAP=y`, `CONFIG_BOOTLOADER_MCUBOOT=y`, `CONFIG_MCUBOOT_BOOTUTIL_LIB=y`;
  - `CONFIG_ADC=y`, `CONFIG_ADC_RPI_PICO=y`;
  - `CONFIG_CPP=y`, `CONFIG_WARN_DEPRECATED=y`, `CONFIG_COMPILER_WARNINGS_AS_ERRORS` not set.
- **Config at the target** (`.config` written before the CMake failure): the same LoRa,
  Mbed TLS, flash map and ADC symbols; `CONFIG_ZEPHYR_TF_PSA_CRYPTO_MODULE` absent.

## Items

The squash touches 10 files (+104 −44). The 15 commits before it (back to 2026-05-25) and
the commits after it up to the next Zephyr bump (`3541730`, v4.4.1 → v4.4.2, 2026-09-19,
another pair) were checked. There are five upgrade items. The rest are classified at the
end.

### P1. TCA954x mux channels: `compatible` and `label` dropped (`proves_flight_control_board_v5.dtsi`): in scope

**Zephyr's change:** `69a8ac6a020` "bindings: tca954x: drop child compatible" (in `v4.4.0`,
not in `v4.3.0`; no migration-guide entry):

- `dts/bindings/i2c/ti,tca9548a.yaml` (and `-9544a`, `-9546a`) lose `child-binding:
  compatible: "ti,tca9548a-channel"`; no binding for `ti,tca9548a-channel` remains;
- `ti,tca954x-base.yaml`'s `child-binding` no longer has `include: [i2c-controller.yaml]`
  (which brought `label` and the other controller properties). It's now `bus: i2c` with
  only `#address-cells`, `#size-cells` and `reg`;
- Zephyr's own boards drop the channel `compatible` lines in the same commit.

**Parts** (`5c67bae`, `boards/bronco_space/proves_flight_control_board_v5/proves_flight_control_board_v5.dtsi`,
nodes `mux_channel_0`…`_7: i2c_mux@0`…`@7` under `tca9548a@77`):

| Part | Where (`5c67bae`) | What changed |
|---|---|---|
| P1a | `:211, :248, :285, :322, :360, :397, :435, :473` | `compatible = "ti,tca9548a-channel";` removed (8 lines) |
| P1b | `:212, :249, :286, :323, :361, :398, :436, :474` | `label = "mux_channel_N";` removed (8 lines) |

The eight lines of a part are one edit repeated in one file: a finding with the cause at
any of them, or at the channel nodes as a group, meets the location condition for that
part.

**Scope:** in. The file is included by BOARD.dts at both refs (`zephyr.dts.d`), the mux is
enabled (`CONFIG_I2C_TCA954X=y`), and the driver (`i2c_tca954x.c`, compiled at the source)
defines a device per channel (`DT_FOREACH_CHILD_VARGS`). The channels carry the sensors,
load switches and haptics the deployment takes with `DEVICE_DT_GET` (e.g. `Main.cpp:44-60`:
the `face*_light_sens` and `face*_drv2605` nodes are children of the channels).

**Revealing artifacts:**

- **Observed in the verification builds** (`edt.pickle`): at the source, `mux_channel_0` has
  `matching_compat` `ti,tca9548a-channel` and binding `ti,tca9548a.yaml` (the child-binding).
  At the target it has the same compatible but **no binding** (`binding_path` None), so the
  child-binding isn't applied to it. The node keeps `compatible`, `label` and `reg`. The
  devicetree generated without an error or warning, and the sensors below the channels
  still bind (`ti,tmp112`, `vishay,veml6031`, `ti,drv2605`).
- The binding diff at both refs (`ti,tca9548a.yaml`, `ti,tca954x-base.yaml`).
- Expected from the code (not checked before the run): with the maintainers' edit, the
  channels match the new child-binding. Without it, `label` would also be an undeclared
  property under that binding. No build failure is known to follow from leaving the lines
  in; the effect of a binding-less channel at run time wasn't checked.

### P2. `FIXED_PARTITION_ID` → `PARTITION_ID` (`Main.cpp`): in scope

**Zephyr's change:** `migration-guide-4.4.rst:1346-1379` (Flash map): the `FIXED_PARTITION_*`
macros of `include/zephyr/storage/flash_map.h` are deprecated, replaced by `PARTITION_*`
(`f8f83289c00` "storage: Add support for zephyr,mapped-partition", in `v4.4.0`). At
`v4.4.1`, `flash_map.h:383` is
`#define FIXED_PARTITION_ID(label) PARTITION_ID(label) __DEPRECATED_MACRO`.

| Part | Where (`5c67bae`) | What changed |
|---|---|---|
| P2 | `PROVESFlightControllerReference/ReferenceDeployment/Main.cpp:62` | `const int storage_partition_id = FIXED_PARTITION_ID(storage_partition);` → `PARTITION_ID(storage_partition)` |

**Scope:** in. `Main.cpp` is compiled at the source; `CONFIG_FLASH_MAP=y`.

**Revealing artifacts**, expected from the code (not checked before the run):

- the migration-guide table entry `FIXED_PARTITION_ID` → `PARTITION_ID`;
- the `flash_map.h` declaration at both refs (plain macro at v4.3.0, `__DEPRECATED_MACRO` at
  v4.4.1);
- a target compile would give the "Macro is deprecated" warning (`__WARN`, since
  `CONFIG_WARN_DEPRECATED=y`) at `Main.cpp:62`. It's a warning, not an error
  (`COMPILER_WARNINGS_AS_ERRORS` is off). The scanned target doesn't compile, so that
  warning isn't available in this build.

### P3. `tf-psa-crypto` added to the manifest (`west.yml`): in scope

- **What changed:** `west.yml` gains a project, in place of the `tinycrypt` entry (`:77-81`):
  `tf-psa-crypto`, revision `dc575a2ddcc8cb16275d24c42a52eaf79ebe2231`, path
  `lib/zephyr-workspace/modules/crypto/mbedtls/tf-psa-crypto`.
- **Zephyr's change:** Mbed TLS 4.1: "crypto support was moved to TF-PSA-Crypto. A new west
  module has been introduced for the latter" (`migration-guide-4.4.rst:1446-1455`). Zephyr's
  `west.yml` at v4.4.1 has `tf-psa-crypto` `dc575a2` (path
  `modules/crypto/tf-psa-crypto`; `e55b3612d72`).
- **Scope:** in. The build uses Mbed TLS at both refs (`CONFIG_MBEDTLS=y`,
  `CONFIG_MBEDTLS_PSA_CRYPTO_C=y`; `psa_crypto.c` compiled at the source; the project's
  `Authenticator.cpp` calls `psa_crypto_init()` and the PSA MAC API). The scanned build
  doesn't pass `tf-psa-crypto`, matching the source manifest.
- **Revealing artifacts:**
  - **observed** in the verification target configure: the Mbed TLS module's
    `CMakeLists.txt:346` `add_subdirectory` of `.../mbedtls/tf-psa-crypto` fails ("not an
    existing directory"), then `:366` "TF-PSA-Crypto target tfpsacrypto does not exist";
  - the target `west.yml` declares a module the source manifest doesn't list;
  - the guide entry.

### P4. Module pins re-aligned with v4.4.1's manifest (`west.yml`): in scope

- **What changed:** the explicit module revisions in `west.yml`, each to Zephyr v4.4.1's
  manifest revision. Each is a dependency bump (ORACLE-RULES §1.1), and they're one change
  made in six places.
- **Zephyr's change:** v4.4.1's `west.yml` moves these modules (the Zephyr release that the
  project now builds against requires its own module revisions). There's no migration-guide
  entry per module; the Mbed TLS 4 entry (`:1446-1455`) covers P4e.

| Part | Where (`5c67bae` `west.yml`) | What changed | Scope |
|---|---|---|---|
| P4a | `:49` `cmsis_6` | `06d952b6` → `30a859f4` | **in**: 1296 headers in the source build's ninja deps |
| P4b | `:56` `hal_rpi_pico` | `09e95752` → `562b41e1` | **in**: 660 headers, and HAL sources compiled |
| P4c | `:61` `hal_st` | `9f81b442` → `7a792882` | **in**: 8 headers (ST sensor drivers `lsm6dso`, `lis2mdl`) |
| P4d | `:68` `picolibc` | `560946f2` → `01254932` | out: the toolchain's picolibc is used (`CONFIG_PICOLIBC_USE_TOOLCHAIN=y`); no module file is compiled or included |
| P4e | `:72` `mbedtls` | `85440ef5` → `a3e190fe` (Mbed TLS 3.6 → 4.1) | **in**: compiled at the source |
| P4f | `:84` `mcuboot` | `96576b34` → `6d3b3d2c` (v2.4.0) | **in**: bootutil compiled and imgtool signs the image (`BOOTLOADER_MCUBOOT=y`) |

`cmsis` (`512cc7e`), `fatfs` (`f4ead3b`) and `loramac-node` (`fb00b38`) are unchanged: their
revisions are the same in both Zephyr manifests.

**Note on the scanned build:** zmig builds each ref with that ref's Zephyr manifest
revisions. So the scanned source build already uses v4.3.0's `cmsis_6`, `picolibc` and
`mbedtls`, not the project's older pins, and the scanned target build uses v4.4.1's
revisions for all six.

**Revealing artifact:** the target Zephyr manifest's revision for each module, against the
project's pin in its own `west.yml`. Nothing in a build shows it: zmig substitutes the
manifest revisions. Build-level effects (P4e's Mbed TLS 4 needs P3) are covered by P3.

### P5. `lib/fprime-zephyr` submodule bump: F´ `LoRaBandwidth` values follow Zephyr's enum: in scope

**What changed:** the submodule pointer `lib/fprime-zephyr` `3139971` → `7643509`
(27 commits). The upgrade content is `fee77a1325` (2026-07-15) "Fix LoRaBandwidth enum values
to match Zephyr 4.4.1's lora_signal_bandwidth", merged as fprime-zephyr PR #16:

| Part | Where (fprime-zephyr `3139971`) | What changed |
|---|---|---|
| P5 | `fprime-zephyr/Drv/LoRa/LoRa.fpp:17-21` | `enum LoRaBandwidth : U8 { BW_125_KHZ = 0, BW_250_KHZ = 1, BW_500_KHZ = 2 }` → `: U16 { … = 125, … = 250, … = 500 }` |

The other 25 commits (a `ZephyrKmallocAllocator` in `default/zephyr-config/`, its CI tests,
and a revert) aren't caused by the upgrade, but they arrive with the same bump.

**Zephyr's change** (no migration-guide entry):

- `6b2f1c19aca` "drivers: lora: Add missing Bandwidth and Spreading Factor values" (in
  `v4.4.0`): `enum lora_signal_bandwidth` in `include/zephyr/drivers/lora.h` changes from an
  index (`BW_125_KHZ = 0`, `BW_250_KHZ`, `BW_500_KHZ`) to the kHz value (`= 125`, `= 250`,
  `= 500`), with new members.
- `4303a3d7d88` "lora: Fix bandwidth enum breakage in loramac-node driver" (in `v4.4.0`):
  `drivers/lora/loramac-node/sx12xx_common.c` maps the enum through
  `sx12xx_get_bandwidth_idx()`, and `sx12xx_lora_config()` returns `-EINVAL` ("Unsupported
  bandwidth: %d") for any other value.

`fprime-zephyr/Drv/LoRa/LoRa.cpp:77` and `:97` do
`static_cast<enum lora_signal_bandwidth>(bandwidth.e)` on the F´ parameter. With the old
values, `BW_125_KHZ` reaches Zephyr as 0, which v4.4 rejects: LoRa configuration fails at run
time (the commit message: "TRANSMIT configuration fails with ConfigurationFailed").

**Scope:** in. `lib/fprime-zephyr` is a submodule this build uses (`FPRIME_LIBRARY_LOCATIONS`);
`LoRa.cpp` and the autocoded `LoRaBandwidthEnumAc.cpp` are compiled at the source; the
deployment instantiates the LoRa component (`ReferenceDeployment.lora`, whose
`BANDWIDTH_RX`/`_TX` parameters the project's `sequences/startup.seq:13-14` set to
`BW_125_KHZ`), and the board's radio uses the loramac-node `sx126x` driver
(`CONFIG_LORA_SX126X=y`, `sx12xx_common.c` compiled).

**Revealing artifacts**, expected from the code (not checked before the run):

- the `lora.h` enum at both refs (the enumerator values change);
- `sx12xx_common.c` at the target (the new `-EINVAL` path);
- nothing at build time: the cast compiles at both refs. The failure is run time only.
- Location: under §2 a finding meets the location condition by naming the dependency
  (`lib/fprime-zephyr`), or by pointing at `LoRa.fpp`/`LoRa.cpp`'s cast.

## Not upgrade items (with reasons)

Every file of `606eb0a` is accounted for.

| Commit / file | Change | Why it's not an item |
|---|---|---|
| `606eb0a` `west.yml:20`, `lib/zephyr-workspace/zephyr` | `revision: v4.3.0` → `v4.4.1`; submodule `3568e1b` → `1f6485e` | The bump itself |
| `606eb0a` `west.yml:31`, `:77-81` | `tinycrypt` dropped from the allowlist and its explicit project removed | Not caused by this upgrade. Zephyr removed TinyCrypt in v4.3.0 (`fec22fd85b5` "crypto: remove deprecated tinycrypt"; no `tinycrypt` in v4.3.0's manifest), so at the from-ref it was already a dead pin no Zephyr code used. It's a clean-up left over from the previous upgrade. The PR text pairs it with P3 ("Replace tinycrypt … with tf-psa-crypto"), but the cause is different |
| `606eb0a` `proves_flight_control_board_v5-pinctrl.dtsi:60-61` | `adc_default` `pinmux = <ADC_CH0_P26>, <ADC_CH1_P27>` → `<ADC_CH1_P27>` | **Not shown to be caused by the upgrade** (uncertain). The commit gives a board reason: "GP26 is the tca9548a reset GPIO" (`proves_flight_control_board_v5.dtsi:207` `reset-gpios = <&gpio0 26 …>`). Between v4.3.0 and v4.4.1, `ADC_CH0_P26` is unchanged, and `drivers/adc/adc_rpi_pico.c`, `drivers/pinctrl/pinctrl_rpi_pico.c` and `drivers/i2c/i2c_tca954x.c` have no changes; `drivers/gpio/gpio_rpi_pico.c` changes only a config initializer (`04f652c4bd1`); `ADC_INIT_PRIORITY` is unchanged. The `hal_rpi_pico` bump (P4b) wasn't read. The file is parsed in the scanned build, so a zmig finding on it is scored as precision (§3) |
| `606eb0a` `makelib/zephyr.mk`, `tools/patch-zephyr-sdk-toolchain-download.py` | SDK install retries, macOS quarantine/curl fallback for the toolchain download | SDK-install tooling |
| `606eb0a` `Makefile` | `submodules` cleans submodule working trees first | Tooling |
| `606eb0a` `.github/workflows/ci.yaml` | drops the reset step; GDS console log artifact | CI |
| `6944fca` (2026-07-22, after) | `prj.conf` Mbed TLS heap 32 KiB → 8 KiB | RAM tuning, measured on hardware; the commit says the old size had no rationale. Not caused by the upgrade |
| `24968fa` (2026-07-22, after) | fprime-zephyr `CONTINUOUS_WAVE` repeated-call fix, radio test | Driver bug fix in the project's fork, not attributed to Zephyr |
| `3d761fc` (2026-07-20, after), `a477893` | F´ v4.2.2 and v4.3.0 upgrades | Another dependency's upgrade (F´), not Zephyr |
| `c222dd8`, `87e40c9`, `c03b206`, `451c5bc`, `ea143d0`, `9873731` (after) | docs hook, hard-coded startup, boot-count hardening, timebase parameter, tests, power monitor | Features and fixes |
| `5c67bae`, `b2437a9`, `e13330c`, `04c9db8`, `482611b`, `1af2a0c`, `65f8cb4`, `dc3e0af`, `e5e0a1c`, `1cc18b2`, `8726bfe`, `638f8e2`, `d3df5c5`, `ae1e09e`, `9bb0602` (before) | throttle, TcSecurityDeframer, yamcs pins, `I2C_DW_CLOCK_SPEED` for the 150 MHz RP2350 clock, CI and tests | Made on v4.3.0, features and fixes. `04c9db8` is a clock-rate correction, the same at both refs |
| `3541730` (2026-09-19) | Zephyr v4.4.1 → v4.4.2, Mbed TLS / TF-PSA-Crypto / fatfs bumps | The next upgrade pair |

## Counts

- **Items:** 5 (P1–P5).
- **In scope: 5 items:**
  - P1 (2 parts), P2, P3, P5;
  - P4: 5 of its 6 parts in scope (P4a–c, P4e, P4f; P4d `picolibc` is out).
- **Out of scope:** no whole item; the part P4d.
- **What the scanned target build shows:**
  - It stops in CMake at the Mbed TLS module (P3), after devicetree and Kconfig. So the
    target has a generated devicetree and `.config`, but no compiler output.
  - P1 shows in the generated devicetree (the channel nodes lose their binding), but not as
    an error.
  - P2 has the migration guide and the header; its compile warning needs a target compile.
  - P4 has only the manifests.
  - P5 has only the Zephyr header and driver diff; it fails at run time.
- **Reachable ceiling:** every in-scope item has some artifact in this build (P4 only the
  target manifest; P5 only the header and driver source). None is unreachable in principle,
  but only P3 produces a build failure.
