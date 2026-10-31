# Oracle: ARDEP Zephyr v4.3.0 → v4.4.0 (`266e0aa36e`)

Written 2026-10-30, **before zmig was run on this project**. Only `--analyzers none`
verification builds were run, and they produce no findings. The rules are in
[../ORACLE-RULES.md](../ORACLE-RULES.md). This file isn't edited after the run;
corrections go at the end, dated.

This is a held-out project. It's unseen: no zmig change has been informed by it.

## Upgrade and scanned build

**Project.** ARDEP (https://github.com/mercedes-benz/ardep, Apache-2.0), an automotive
development platform built around an STM32G474 board.

- It's a Zephyr module (`zephyr/module.yml`, name `ardep`) with `board_root`, `dts_root`
  and `module_ext_root` at the repo root. Its own board is `boards/mercedes/ardep`.
- It also contains libraries (`lib/uds`, `lib/iso14229`, `lib/can_log`, …), drivers, a
  `firmware_loader/` app, samples and tests.
- Its `west.yml` imports upstream Zephyr by tag with a name-allowlist. It also pulls in
  three non-Zephyr projects:
  - `zephboards` (dragonlock2/zephyrboards), pinned to `9443ad0e`;
  - `iso14229` (frickly-systems remote), on branch `zephyr`;
  - `cannectivity`, pinned by tag.

**Commits.** The upgrade is one squashed PR:

| Role | Commit | Date | Zephyr pin | Other pins |
|---|---|---|---|---|
| Source tree (the bump's parent) | `5b6d3ca` | 2026-01-21 | `v4.3.0` (`3568e1b6d5cd`) | cannectivity `v1.2.1` (`faad60d`); zephboards `9443ad0e`; iso14229 `zephyr` |
| The maintainers' state after the upgrade | `266e0aa` "chore: upgrade zephyr to v4.4.0 (#40)" | 2026-06-25 | `v4.4.0` (`684c9e8f32e4`) | cannectivity `v1.4.0` (`f229e12`); zephboards and iso14229 unchanged |

- **Allowlist change:** the bump adds `tf-psa-crypto`. Before the bump it was `cmsis_6
  hal_stm32 segger hal_st mcuboot mbedtls littlefs`.
- **iso14229 on 2026-06-25:** the `zephyr` branch tip is `50bea70` (2025-11-03). It's the
  same on both sides of the bump.
- **zephboards:** it doesn't float. It's pinned to `9443ad0e` on both sides, and the
  upstream repository has no `zephyr` branch.

**Zephyr refs.** From `v4.3.0` to `v4.4.0`.

**Configuration** (this file):

- **App:** `samples/uds/` on board `ardep` (default revision 2.0.0, SoC `stm32g474xx`).
  `sample.yaml` lists `ardep` as its integration platform; the CI builds it with `west
  twister -T samples --integration`.
- **Why this app:** it compiles as many bump-touched project files as `firmware_loader/`:
  the board's `Kconfig.defconfig`, `Kconfig.uds` and `board.yml`, plus the app's own
  `prj.conf` and one `.c` file. It also covers more items (the Mbed TLS 4 port and the
  `tf-psa-crypto` module).
- **Modules:**
  - `cmsis_6 hal_stm32 segger hal_st mcuboot mbedtls littlefs`: the source-tree allowlist,
    without `tf-psa-crypto`;
  - `ZEPHYR_EXTRA_MODULES` = the ardep tree; iso14229 `50bea70`; zephyrboards `9443ad0e`;
    cannectivity `v1.2.1` (`faad60d`).
- **SDK:** both 0.17.4 and 1.0.1 are put on `CMAKE_PREFIX_PATH`, and `--sdk` isn't used. The
  evaluation harness reproduces this with `{sdk:0.17.4};{sdk:1.0.1}` (`eval/configs.toml`,
  configuration `ardep`).
  - v4.3.0 asks for `find_package(Zephyr-sdk 0.16)` and rejects 1.0.1.
  - v4.4.0 needs SDK ≥ 1.0.0 (`migration-guide-4.4.rst:26`).
  - Configure logs: "Found toolchain: zephyr 0.17.4" at v4.3.0, and "zephyr 1.0.1" at v4.4.0
    in the post-tree build.
- **Sysbuild:** not used. `sample.yaml` sets `sysbuild: true`, but the board's
  `Kconfig.defconfig` covers the non-sysbuild case (`if !MCUBOOT`: `BOOTLOADER_MCUBOOT`,
  `FLASH_MAP`, `Kconfig.uds`).

**Scope evidence** comes from the verification builds:

- **Source tree at v4.3.0: ok** (build `30e41567a7418691`). The scanned build.
- **Source tree at v4.4.0: configure-failed** (build `f46916d2991f03e5`). The scanned build.
  The failure is at board discovery:
  - the message is "Error finding board: ardep / ERROR: Malformed board YAML file:
    …/boards/mercedes/ardep/board.yml … is not valid under any of the given schemas in
    $.board";
  - so no Kconfig, devicetree or compile step runs at the target in the scanned build.
- **Post tree `266e0aa` at v4.4.0: ok** (build `a3d6cc4e2d5b19ed`):
  - with `tf-psa-crypto` added and cannectivity `v1.4.0`;
  - it's used only to read the target-side scope of the maintainers' fixed state;
  - the post tree at v4.3.0 fails to configure, which is expected.
- **Project files compiled, the same 34 at v4.3.0 (source tree) and at v4.4.0 (post tree):**
  - the app: `samples/uds/src/{authentication,data_by_id,diag_session_ctrl,
    dtc_information,main,mem_by_address,routine_control,security_access}.c`;
  - `lib/uds/*.c` (18 files), `lib/iso14229/iso14229_common.c`, `lib/can_log/can_log.c`,
    `lib/ardep_usb/ardep_usb.c`, `lib/gearshift_address_providers/*.c` (2 files),
    `drivers/binary_encoded_gpio/binary_encoded_gpio.c`, `modules/iso14229/drv.c`;
  - the iso14229 module's `iso14229.c`.
  - No zephyrboards or cannectivity source is compiled or included (ninja deps).
- **Config, source tree at v4.3.0:**
  - `CONFIG_STD_C11=y`;
  - `CONFIG_CAN_STM32_FDCAN=y`, `CONFIG_CAN_MAX_STD_ID_FILTER=12`,
    `CONFIG_CAN_MAX_EXT_ID_FILTER=8`;
  - `CONFIG_MBEDTLS=y`, `CONFIG_MBEDTLS_BUILTIN=y`, `CONFIG_MBEDTLS_CIPHER_AES_ENABLED=y`,
    `CONFIG_MBEDTLS_CTR_DRBG_ENABLED=y` (already deprecated at v4.3.0, so
    `CONFIG_DEPRECATED=y`), `CONFIG_MBEDTLS_ENTROPY_C=y`;
  - `CONFIG_UDS=y`, `CONFIG_ISO14229=y`, `CONFIG_BOOTLOADER_MCUBOOT=y`,
    `CONFIG_FLASH_MAP=y`, `CONFIG_FILE_SYSTEM_LITTLEFS=y`;
  - `CONFIG_ZEPHYR_{ARDEP,ISO14229,ZEPHYRBOARDS,CANNECTIVITY}_MODULE=y`: all four extra
    modules' Kconfig files are parsed (`Kconfig/Kconfig.modules`).
- **Config, post tree at v4.4.0:**
  - `CONFIG_STD_C17=y`;
  - `CONFIG_CAN_STM32_FDCAN_MAX_STD_ID_FILTERS=12`, `CONFIG_CAN_STM32_FDCAN_MAX_EXT_ID_FILTERS=8`;
  - `CONFIG_MBEDTLS_PSA_CRYPTO_C=y`, `CONFIG_PSA_WANT_KEY_TYPE_AES=y`,
    `CONFIG_PSA_WANT_ALG_ECB_NO_PADDING=y`, `CONFIG_MBEDTLS_CTR_DRBG_C=y`,
    `CONFIG_MBEDTLS_ENTROPY_C=y`, `CONFIG_ZEPHYR_TF_PSA_CRYPTO_MODULE=y`;
  - `CONFIG_DEPRECATED` not set.
- **Merged fragments:** `samples/uds/prj.conf` and `samples/uds/boards/ardep.conf`.
- **Overlays:** the devicetree overlay `boards/mercedes/ardep/ardep_2_0_0.overlay` (the board
  revision); there's no app overlay.

## Items

The bump is one squashed commit touching 13 files. The ten commits before it and the three
after it contain no follow-up fix of the upgrade (see the table at the end). The bump has six
upgrade items plus the cannectivity re-pin.

### A1. CAN filter-count Kconfig rename (`boards/mercedes/ardep/Kconfig.defconfig`): in scope

**Zephyr's change** (`migration-guide-4.4.rst:246-264`):

- `CONFIG_CAN_MAX_STD_ID_FILTER` and `CONFIG_CAN_MAX_EXT_ID_FILTER` were removed;
- for `st,stm32-fdcan` they're replaced by `CONFIG_CAN_STM32_FDCAN_MAX_STD_ID_FILTERS` and
  `CONFIG_CAN_STM32_FDCAN_MAX_EXT_ID_FILTERS` (`drivers/can/Kconfig.stm32:60,68` at
  v4.4.0; the old names were at the same lines at v4.3.0).

**Parts:**

| Part | Where (`5b6d3ca`) | What changed |
|---|---|---|
| A1a | `boards/mercedes/ardep/Kconfig.defconfig:17` | `config CAN_MAX_STD_ID_FILTER` (`default 12`) → `config CAN_STM32_FDCAN_MAX_STD_ID_FILTERS` |
| A1b | `boards/mercedes/ardep/Kconfig.defconfig:20` | `config CAN_MAX_EXT_ID_FILTER` (`default 8`) → `config CAN_STM32_FDCAN_MAX_EXT_ID_FILTERS` |

**Scope:** in. The file is parsed, and both symbols are evaluated at the source ref
(`CONFIG_CAN_MAX_STD_ID_FILTER=12`, `…EXT…=8`, `CONFIG_CAN_STM32_FDCAN=y`).

**Revealing artifacts**, expected from the code (not checked before the run):

- **Kconfig tree:** both symbols are removed at the target, and the new FDCAN symbols are
  added.
- **Kconfig at the target:**
  - the board's re-declaration becomes a symbol defined without a type, which kconfiglib
    warns about;
  - Zephyr's `kconfig.py` turns that warning into an error ("Aborting due to Kconfig
    warnings", `scripts/kconfig/kconfig.py:126-133`);
  - if it didn't, the board's 12-filter default would silently be lost (the FDCAN default is
    28 standard filters).
- **In the scanned target build,** configure stops at `board.yml` (A3) before Kconfig runs,
  so there's no Kconfig error to read. The evidence is the Kconfig tree diff and the guide
  entry.
- A1a and A1b have the same artifacts.

### A2. C standard `STD_C11` → `STD_C17`: in scope

**Zephyr's change** (`migration-guide-4.4.rst:32-35`):

- Zephyr now defaults to C17;
- `CONFIG_STD_C99` and `CONFIG_STD_C11` are deprecated. At v4.4.0, `Kconfig.zephyr:435-437`
  has `config STD_C11` with `bool "C11 [DEPRECATED]"` and `select DEPRECATED`. At v4.3.0,
  `:403` has no deprecation.
- The symbol is deprecated, not removed: a C11 build still configures, with the warning
  "Deprecated symbol STD_C11 is enabled".
- **The decision:** it counts as an upgrade item, because the maintainers changed every
  occurrence in the same bump, to follow the deprecation and the new default. Nothing else
  explains it.
- **Not a compatibility need:** the ardep wrapper for iso14229 accepts C11, C17 or C23
  (`modules/iso14229/Kconfig:10`, `depends on STD_C11 || STD_C17 || STD_C23`). So the
  change isn't forced by iso14229, though the comment says "requirement for external
  iso14229 module".

**Parts:**

| Part | Where (`5b6d3ca`) | What changed | Scope |
|---|---|---|---|
| A2a | `boards/mercedes/ardep/Kconfig.uds:19` | `choice STD_C` `default STD_C11` → `default STD_C17` | **in**: sourced from `Kconfig.defconfig` under `if !MCUBOOT`, and parsed |
| A2b | `samples/uds/prj.conf:9` | `CONFIG_STD_C11=y` → `CONFIG_STD_C17=y` | **in**: merged fragment |
| A2c | `firmware_loader/prj.conf:18` | same | out: another app |
| A2d | `samples/iso14229/prj.conf:7` | same | out: another sample |
| A2e | `samples/uds_bus_sample/prj.conf:7` | same | out: another sample |
| A2f | `tests/lib/iso14229/prj.conf:13` | same | out: a test, not built |
| A2g | `tests/lib/uds/prj.conf:17` | same | out: a test, not built |

**Revealing artifacts**, expected from the code (not checked before the run):

- the Kconfig tree at the target (`STD_C11` gains `select DEPRECATED` and the
  "[DEPRECATED]" prompt);
- the guide entry;
- at the target, a configure-time "Deprecated symbol STD_C11 is enabled" warning. It isn't
  reached in the scanned build, because of A3.
- No compiler error is expected: `-std=c11` still works.

### A3. `board.yml` needs `full_name`: in scope

**Zephyr's change** (`migration-guide-4.4.rst:36-37`):

- "The `full_name` property of `board`/`boards` entries corresponding to new boards in
  board.yml files is now required".
- It's enforced at build time: `scripts/schemas/board-schema.yaml:170-172` at v4.4.0
  (`oneOf: required [name, full_name] | required [extend]`), validated by
  `scripts/list_boards.py:163`.
- **The decision:** Zephyr forced this one. The board can't be found without it.

**Parts:**

| Part | Where (`5b6d3ca`) | What changed |
|---|---|---|
| A3 | `boards/mercedes/ardep/board.yml:2` (after `name: ardep`) | adds `full_name: ARDEP` |

**Scope:** in. It's the scanned board's own `board.yml`.

**Revealing artifact:** a configure error at the target. **This one was seen** in the
verification build log of the scanned build at v4.4.0 (`zmig-logs/uds.log`):

- "CMake Error at …/cmake/modules/boards.cmake:191: Error finding board: ardep / Error
  message: ERROR: Malformed board YAML file: …/boards/mercedes/ardep/board.yml {…} is not
  valid under any of the given schemas in $.board";
- the schema diff (`board-schema.yaml`) and the guide entry are also evidence.

The message doesn't name `full_name`.

### A4. `FIXED_PARTITION_ID` → `PARTITION_ID`: out of scope

**Zephyr's change** (`migration-guide-4.4.rst:1345-1380`):

- the `FIXED_PARTITION_*` macros in `include/zephyr/storage/flash_map.h` are deprecated and
  replaced by `PARTITION_*`;
- at v4.4.0, `flash_map.h:382-383` has `#define FIXED_PARTITION_ID(label) PARTITION_ID(label)
  __DEPRECATED_MACRO`, which is a compiler warning on use.

**Parts:**

| Part | Where (`5b6d3ca`) | What changed | Scope |
|---|---|---|---|
| A4a | `firmware_loader/src/uds_routine_control.c:60` | `flash_area_open(FIXED_PARTITION_ID(slot0_partition), &fa)` → `PARTITION_ID(...)` | out: `firmware_loader/` isn't the scanned app |
| A4b | `tests/lib/uds/src/fixture.c:259, :265` | `FIXED_PARTITION_ID(storage_partition)` → `PARTITION_ID(...)` (2 sites) | out: test, not built (CI builds it for `native_sim`) |

No compiled file of the scanned build uses a `FIXED_PARTITION_*` macro. A grep of the source
tree finds only these three sites.

**Revealing artifacts** (for the record), expected from the code (not checked before the
run):

- the header declaration at both refs (the macro gains `__DEPRECATED_MACRO`);
- a "Macro is deprecated" compiler warning at those lines in a target build of those apps;
- the guide entry.

### A5. Mbed TLS 4.x: UDS sample crypto ported from the legacy API to PSA: in scope

**Zephyr's change** (`migration-guide-4.4.rst:1445-1454, 1476-1480`):

- Mbed TLS was upgraded to 4.1.0, and crypto moved to the new TF-PSA-Crypto module (1.1.0);
- `CONFIG_MBEDTLS_CIPHER_AES_ENABLED` is removed (`:1480`);
- in TF-PSA-Crypto, the legacy `aes.h`, `ctr_drbg.h` and `entropy.h` are private headers:
  they're at `drivers/builtin/include/mbedtls/private/{aes,ctr_drbg,entropy}.h` in
  `tf-psa-crypto@dc575a2`, and no `mbedtls/aes.h` is on the include path of the target
  build;
- in the post-tree build at v4.4.0, `authentication.c` gets the include path `-I` for
  `tf-psa-crypto/include`, `tf-psa-crypto/drivers/builtin/include` and
  `mbedtls/include`.

**Parts:**

| Part | Where (`5b6d3ca`) | What changed |
|---|---|---|
| A5a | `samples/uds/prj.conf:25-27` | removes `CONFIG_MBEDTLS_CIPHER_AES_ENABLED=y` (removed at the target) and `CONFIG_MBEDTLS_CTR_DRBG_ENABLED=y` (deprecated at both refs). Adds `CONFIG_MBEDTLS_PSA_CRYPTO_C=y`, `CONFIG_PSA_WANT_KEY_TYPE_AES=y`, `CONFIG_PSA_WANT_ALG_ECB_NO_PADDING=y`, `CONFIG_MBEDTLS_CTR_DRBG_C=y`. Keeps `CONFIG_MBEDTLS_ENTROPY_C=y` |
| A5b | `samples/uds/src/authentication.c:17-19` | `#include <mbedtls/aes.h>`, `<mbedtls/ctr_drbg.h>`, `<mbedtls/entropy.h>` → `#include <psa/crypto.h>` |
| A5c | `samples/uds/src/authentication.c:37-69` (`aes_encrypt_ecb`, `aes_decrypt_ecb`) | `mbedtls_aes_init/setkey_enc/setkey_dec/crypt_ecb/free` → one `aes_ecb_crypt()` using `psa_crypto_init`, `psa_import_key`, `psa_cipher_encrypt/decrypt`, `psa_destroy_key` |
| A5d | `samples/uds/src/authentication.c:71-95` (`generate_random_seed`) | `mbedtls_entropy_*` and `mbedtls_ctr_drbg_*` → `psa_crypto_init()` + `psa_generate_random()` |

**Scope:** in, all parts. `prj.conf` is merged, `authentication.c` is compiled, and
`CONFIG_MBEDTLS=y` at both refs.

**Revealing artifacts**, expected from the code (not checked before the run):

- **A5a:**
  - the Kconfig tree at the target: `MBEDTLS_CIPHER_AES_ENABLED` is gone, and
    `MBEDTLS_CTR_DRBG_ENABLED` is still in `Kconfig.deprecated`;
  - at the target, assigning the undefined symbol in a hand-written fragment is a Kconfig
    error (`kconfig.py:54-57, 126-133`);
  - the guide line `:1480`.
- **A5b:**
  - the headers missing from the target include path: a fatal "mbedtls/aes.h: No such file
    or directory" at `authentication.c:17`;
  - the header trees at both refs: public `mbedtls/aes.h` at v4.3.0, only
    `mbedtls/private/aes.h` at v4.4.0;
  - the guide entry `:1448-1454`.
- **A5c/A5d:** no separate artifact beyond A5b. Once the include fails, nothing else in the
  file is diagnosed. The functions still exist in TF-PSA-Crypto's private headers, so a
  declaration diff of the public headers is the only per-call evidence.
- **In the scanned target build,** configure stops at A3, so neither the Kconfig error nor
  the compiler error is produced. The evidence is the Kconfig tree, the header trees and the
  guide.

### A6. `tf-psa-crypto` added to the manifest allowlist (`west.yml`): in scope

- **What changed:** `west.yml` name-allowlist gains `tf-psa-crypto`.
- **Why:** Mbed TLS 4 needs the new module (`migration-guide-4.4.rst:1448-1451`).
  - `modules/mbedtls/CMakeLists.txt:35` at v4.4.0 uses `ZEPHYR_TF_PSA_CRYPTO_MODULE_DIR`.
  - Zephyr's own `west.yml` at v4.4.0 adds `tf-psa-crypto` (`dc575a2`, line 397).
- **Scope:** in. The build uses Mbed TLS (`CONFIG_MBEDTLS=y`), and the post-tree target
  build compiles against `tf-psa-crypto` (`CONFIG_ZEPHYR_TF_PSA_CRYPTO_MODULE=y`). The scanned
  build doesn't pass it, matching the source-tree allowlist.
- **Revealing artifacts**, expected from the code (not checked before the run):
  - the target `west.yml` declares a new module, which the source allowlist filters out;
  - the guide entry;
  - a target build without the module fails in the mbedtls glue (`modules/mbedtls`), at
    Kconfig or CMake. In the scanned build, configure stops at A3 first.

### A7. cannectivity `v1.2.1` → `v1.4.0` (`west.yml`): in scope (by the parsed-file rule)

- **What changed:** the cannectivity module pin, `v1.2.1` (`faad60d`, tracks Zephyr
  `v4.2-branch`) → `v1.4.0` (`f229e12`, tracks `v4.4-branch`). That's 65 upstream commits,
  including `e7bd450` "app: boards: switch to new Kconfig options for maximum CAN RX
  filters".
- **Cause:** a dependency re-pin for Zephyr compatibility. **No project change in the bump
  is caused by the re-pin:** the only ardep user, `samples/can_usb_bridge/` (it builds
  cannectivity's own app with ardep's `.conf`/overlays), isn't modified.
- **Scope:** in, but only by the letter of §1.2.
  - cannectivity is an extra module of the scanned build, and its `Kconfig` (`rsource
    "subsys/Kconfig"`) is parsed (`CONFIG_ZEPHYR_CANNECTIVITY_MODULE=y`).
  - No cannectivity source is compiled or included, and none of its symbols is enabled
    (`USBD_GS_USB` is unset; `samples/uds` has no USB-CAN function).
- **Revealing artifact:** none in this build. The module's code isn't built, so nothing
  in this configuration breaks or changes with the pin. A tool could only flag the pin
  from the manifest (an old module tracking `v4.2-branch` against a v4.4.0 target).

## Not upgrade items (with reasons)

Every file of `266e0aa` is accounted for: all 13 files are in A1–A7. The only other
change is the Zephyr revision line in `west.yml`.

| Commit(s) / file | Change | Why it's not an item |
|---|---|---|
| `266e0aa` `west.yml` `revision: v4.3.0` → `v4.4.0` | the Zephyr pin | The bump itself |
| `5b6d3ca` | upgrade Zephyr to v4.3.0 (PR #35) | The previous upgrade (v4.2→v4.3). It's the source tree of this one |
| `397d74f`, `5ee4f61`, `2412f85`, `0a8ebf0` | readme / docs / board-revision changelog | Docs |
| `5cb53d0`, `13de835`, `c555710`, `c8551a5`, `47c74cd` | UDS flash runner; UDS default instance and gearshift addressing; power-IO shield tester and support; UDS bus sample | Features, before the v4.4.0 release (2026-04-14) and before the bump |
| `d53732a`, `7b21a10` | hardware source files; CANnectivity capitalization in a README | Hardware files, docs |
| `27f0520`, `a23d595` (after the bump) | OpenSCAD enclosure; README | Hardware, docs |
| `1c8636e` (after the bump) | UDS catch-all data-identifier handler (`lib/uds`, `tests/lib/uds/src/fixture.c`) | A feature. Its `fixture.c` edits add fakes and a handler, not partition or other upgrade API |

## Counts

- **Items:** 7 (A1–A7).
- **In scope: 6 items:**
  - A1 (2 parts), A3 (1 part), A5 (4 parts), A6, A7;
  - A2: 2 of its 7 parts in scope (A2a, A2b).
- **Out of scope: 1 item**, A4 (`firmware_loader/` and `tests/lib/uds`).
- **What the scanned target build shows:**
  - It stops at board discovery (A3), so the only target-build artifact is A3's configure
    error.
  - A1, A2, A5 and A6 have evidence only in the Kconfig and header trees, the target
    manifest and the migration guide.
  - A7 has no artifact in this build.
- **Reachable ceiling:** strict recall 5/6. A7 can't be caught from any artifact of this
  build.
