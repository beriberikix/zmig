# Oracle: edgehog-zephyr-device, Zephyr v4.3.0 → v4.4.0 (PR #175, `4ea9219cc6`)

Written 2026-10-30, **before zmig was run on this project**. Only `--analyzers none`
verification builds were run, and they produce no findings. The rules are in
[../ORACLE-RULES.md](../ORACLE-RULES.md). This file isn't edited after the run;
corrections go at the end, dated.

This is a held-out project. It's unseen: no zmig change has been informed by it.

## Upgrade and scanned build

**Project.** edgehog-zephyr-device
(https://github.com/edgehog-device-manager/edgehog-zephyr-device, Apache-2.0), the Edgehog
device-management library for Zephyr, built on the Astarte device SDK.

- The repository is a Zephyr module (`zephyr/module.yml`: `Kconfig` and `CMakeLists.txt` at
  the root). The library is `lib/edgehog_device/`. Its `lib/Kconfig:9` does
  `rsource "../../astarte-device-sdk-zephyr/Kconfig"`: it expects the Astarte SDK checked out
  beside it, as the west workspace lays it out.
- It contains a sample app (`samples/edgehog_app`), an end-to-end test app (`e2e/`, built by
  twister with a pytest harness), unit and integration tests (`tests/lib/edgehog_device/`).
- Its `west.yml` is the workspace manifest: Zephyr by tag with `import: true` and **no
  name-allowlist** (every module of Zephyr's manifest), `astarte-device-sdk-zephyr`
  (`revision: master`, `import: true`; its own manifest adds `zlib-zephyr`),
  `edgehog-astarte-interfaces` (JSON interfaces, read only by the e2e pytest harness) and
  `lz4` (`zephyrproject-rtos/lz4`, `revision: zephyr`). CI uses other manifests
  (`west-ci-reference.yml`, `west-e2e.yml`) that do have allowlists.

**Commits.** The upgrade is PR #175 "Support zephyr 4.4.0", merged with a merge commit. Its
content is an earlier PR (#154, three commits on a side branch) merged onto master, plus one
follow-up:

| Role | Commit | Date | Zephyr pin |
|---|---|---|---|
| Source tree (`3a531e5`'s first parent, master) | `bc1c98672545046ac5cf580dbd63436b6987e6b7` | 2026-05-26 | `revision: v4.3.0` |
| PR #154's commits | `3172127` "Support for Zephyr 4.4", `95ef965` "Use Zephyr UUID", `c83cad1` "Pin Astarte device"; merged on a side branch as `cbe01e3` | 2026-04-27 to 05-07 | `v4.4.0` |
| "Support for zephyr 4.4" (merge of master and `cbe01e3`) | `3a531e59a23c2f24a39db55f2b704b55efe8e546` | 2026-05-26 | `v4.4.0` |
| Follow-up, "Re-generate Astarte interfaces for zephyr 4.4" | `580ae79045ac59834f4b0b3ec1605adbfff1583a` | 2026-05-26 | `v4.4.0` |
| Merge into master (PR #175) | `4ea9219cc61270ca31b17831ebba21437d03c382` | 2026-05-26 | `v4.4.0` |

The upgrade is `bc1c986..4ea9219`: the 25 files of `3a531e5` against its first parent, and the
3 files of `580ae79`.

**Zephyr refs:**

- from `v4.3.0` = `3568e1b6d5cdd51a6b964a2a1d6d29200fea2056`;
- to `v4.4.0` = `684c9e8f32e4373a21098559f748f06915f950c9`.

**Configuration** (`eval/configs.toml`, configuration `edgehog`):

- **App:** `e2e/` on board `native_sim`. CI builds it (`e2e-test.yaml`: `west twister
  --integration -T e2e --test edgehog_device.e2e.default`; `native_sim` is the integration
  platform). The default test, so no `extra_configs` (file transfer stays off).
- **Why this build and not the sample.** The other CI build is `samples/edgehog_app` on
  `stm32h573i_dk` (twister `sample.default`, with sysbuild and MCUboot). Both compile the whole
  library, so the library items (H1, H2) are in scope either way, and each has its own copy
  of the Mbed TLS and fd-table conf lines (H3–H6, H8). The e2e build also parses
  `e2e/boards/native_sim.conf`, the only file of H7, so it has one more item in scope (8 vs 7).
  The sample's board files that changed (`stm32h573i_dk.conf`/`.overlay`) aren't items
  ("Not upgrade items"). The e2e build also needs no sysbuild, which the harness doesn't pass.
- **Sources** (all at one revision for both refs):
  - `edgehog` at the source tree `bc1c986`;
  - `edgehog-astarte`: `astarte-device-sdk-zephyr` at `a72dd16efa95bdbfc218d2fb0db293554558efa8`,
    the upgrade's pin (`c83cad1`). The source manifest says `master`, which on 2026-05-26 was
    a later commit (`6b59c72`). `a72dd16` already supports both Zephyr refs (its "Support for
    Zephyr 4.4.0", `5aefd03`, is from 2026-04-27), and it compiles at both in the
    verification builds. Its `dir` places it beside the edgehog checkout
    (`…/src/edgehog-bc1c98672545/astarte-device-sdk-zephyr`), because of edgehog's
    `lib/Kconfig:9`;
  - `edgehog-zlib`: `zlib-zephyr` tag `release-v1.3.1` (`db8d97ff`), the Astarte manifest's
    pin (`ASTARTE_DEVICE_SDK` selects `ZLIB`);
  - `edgehog-lz4`: `lz4` at `0ad20d0fc4ec`, the tip of its `zephyr` branch since 2026-04-29
    (so the same at the source date and after).
- **CMake args:**
  - `-DZEPHYR_EXTRA_MODULES={src:edgehog};{src:edgehog-astarte};{src:edgehog-zlib};{src:edgehog-lz4}`;
  - `-DEDGEHOG_ROOT={src:edgehog}` (for the patch below);
  - `-DCMAKE_PREFIX_PATH={sdk:0.17.4};{sdk:1.0.1}`, as for ardep. On `native_sim` both refs
    compile with the host compiler ("Found toolchain: host (gcc/ld)", GCC 15.2), so the SDK
    switch has no effect on this build.
- **Modules:** `mbedtls tf-psa-crypto picolibc mcuboot littlefs`. The project takes every
  module, so these are the ones this build uses:
  - `picolibc` (`CONFIG_PICOLIBC_USE_MODULE=y`; 959 of its files compiled), `mbedtls` (106),
    `littlefs` (2), `mcuboot` (bootutil, `CONFIG_MCUBOOT_BOOTUTIL_LIB=y`) at both refs;
  - `tf-psa-crypto` at v4.4.0 only ("not in v4.3.0's manifest, skipped: tf-psa-crypto"; at
    the target `CONFIG_ZEPHYR_TF_PSA_CRYPTO_MODULE=y`). As for ble-copro, it isn't a project
    change: the project's `west.yml` takes every module.
  - Extra modules compiled at the source: Astarte SDK (31 files), zlib (15), lz4 (4).
- **Sysbuild:** not used (the e2e app doesn't use it).
- **App shim (patch).** `eval/patches/edgehog-e2e-root.patch` (`app_patch`). `e2e/` reaches
  out of its directory three times, which breaks in zmig's copy of the app:
  - `e2e/CMakeLists.txt:20` `set(SAMPLE_DIR "${CMAKE_CURRENT_LIST_DIR}/../samples/edgehog_app")`
    (it compiles the sample's `src/eth.c`) and `:61`
    `target_include_directories(app PRIVATE ${CMAKE_SOURCE_DIR}/../lib/edgehog_device/include)`.
    The patch takes the root from `EDGEHOG_ROOT` when it's defined, else `..` as before.
  - `e2e/Kconfig:9` `rsource "../Kconfig"` (the repository root's Kconfig). The patch drops it:
    the repository is passed as a module, which sources the same file. In the maintainers'
    workspace it's sourced both ways (the manifest repository is a module too).
  - None of the upgrade's files is touched.
  - This is a zmig limitation in itself (as for rit-fsw): an app that reaches its repository by
    a relative path can't be scanned from a copy of its own directory. Likewise, the sibling
    layout `lib/Kconfig:9` needs is provided by the harness's checkout layout, not by zmig.

**Scope evidence** comes from the verification builds
(`eval/run.py scan --analyzers none --out ~/.cache/zmig/eval/runs/verify-edgehog edgehog`,
zmig `6227d2f`):

- **Source at v4.3.0: ok** (build `b68ebb9fc74a45b2`). One compiler warning in the log.
- **Target at v4.4.0: configure-failed** (build `6743380b5e76a765`). The devicetree was
  generated; Kconfig then stopped with "error: Aborting due to Kconfig warnings" after 11
  warnings (quoted under H3–H7). Nothing was compiled.
- **zmig's salvage rebuild of the target** (`0e6f8fdcec7522cb`, `salvage_of` the target; it
  deleted 11 conf lines: the 9 that the undefined-symbol warnings name, and the two
  key-exchange lines `prj.conf:83-84` that the "assigned but got" warnings name) configured, and failed
  compiling in two places that follow from those deletions: Mbed TLS's
  `mbedtls_check_config.h:141` "#error One or more versions of the TLS protocol are enabled but
  no key exchange methods defined" (H6), and the Astarte SDK's `crypto.c:91` and `:151`
  "implicit declaration of function `mbedtls_pk_write_key_pem`" /
  "`mbedtls_x509write_csr_pem`" (H3). Its log has the warnings quoted under H1, H2 and H8.
  The salvage build is zmig's own processing, so these are cited only as what the artifacts
  hold.
- **Project files compiled at the source** (`compile_commands.json`):
  - all 20 `lib/edgehog_device/*.c`, among them `nvs.c`, `ota.c`, `storage_usage.c`, `led.c`,
    `telemetry.c`, `wifi_scan.c`, `http.c`. Nothing under `lib/edgehog_device/file_transfer/`
    or `ztar/` (`CONFIG_EDGEHOG_DEVICE_FILE_TRANSFER` is not set);
  - `samples/edgehog_app/src/eth.c` (through `SAMPLE_DIR`);
  - `e2e/src/{device_handler,main,runner,shell_handlers,utilities}.c`.
- **Parsed at both refs:** `e2e/Kconfig` (and the module's `Kconfig`, `lib/Kconfig`,
  `lib/edgehog_device/Kconfig`, Astarte's `Kconfig`), the merged fragments `e2e/prj.conf` and
  `e2e/boards/native_sim.conf`, the overlay `e2e/boards/native_sim.overlay`.
- **Included at the source** (`ninja -t deps`): `zephyr/fs/nvs.h` by 14 objects (among them the
  library's `nvs.c` and `storage_usage.c`, both through the private `include/nvs.h`, and the
  Astarte SDK's `kv_storage_nvs.c`); `zephyr/storage/flash_map.h` by 12.
- **Config at the source:**
  - `CONFIG_EDGEHOG_DEVICE=y`, `CONFIG_ASTARTE_DEVICE_SDK=y`, `CONFIG_ZLIB=y`, `CONFIG_LZ4=y`;
  - `CONFIG_NVS=y`, `CONFIG_SETTINGS=y`, `CONFIG_SETTINGS_NVS=y` (so `nvs.c`'s body, under
    `#ifdef CONFIG_SETTINGS_NVS`, is compiled), `CONFIG_FLASH_MAP=y`, `CONFIG_IMG_MANAGER=y`,
    `CONFIG_MCUBOOT_IMG_MANAGER=y`;
  - `CONFIG_MBEDTLS=y`, `CONFIG_MBEDTLS_USE_PSA_CRYPTO=y`, `CONFIG_MBEDTLS_PEM_CERTIFICATE_FORMAT=y`,
    `CONFIG_MBEDTLS_SERVER_NAME_INDICATION=y`, `CONFIG_MBEDTLS_GENPRIME_ENABLED=y`,
    `CONFIG_MBEDTLS_ECDH_C=y`, `CONFIG_MBEDTLS_ECDSA_C=y`, `CONFIG_MBEDTLS_ECP_C=y`,
    `CONFIG_MBEDTLS_ECP_DP_SECP256R1_ENABLED=y`, `CONFIG_MBEDTLS_KEY_EXCHANGE_ECDHE_RSA_ENABLED=y`,
    `CONFIG_MBEDTLS_KEY_EXCHANGE_ECDHE_ECDSA_ENABLED=y`;
  - `CONFIG_NATIVE_UART_0_ON_STDINOUT=y` and so `CONFIG_UART_NATIVE_PTY_0_ON_STDINOUT=y`;
  - `CONFIG_ZVFS_OPEN_MAX=25`, `CONFIG_ZVFS_EVENTFD=y` (`ZVFS_EVENTFD_MAX=1`), and the fd
    requirements `ZVFS_OPEN_ADD_SIZE_NET=20`, `_SOCKETS_SERVICE=1`, `_TLS=4` (sum 25);
  - `CONFIG_DEPRECATED=y`, `CONFIG_WARN_DEPRECATED=y`; `CONFIG_COMPILER_WARNINGS_AS_ERRORS` not
    set. (Twister, which CI uses, builds with warnings as errors; zmig doesn't.)
- **Config at the target** (salvage build's `.config`): `CONFIG_ZVFS_OPEN_ADD_SIZE_EVENTFD=1`
  (new), so the sum is 26; `CONFIG_UART_NATIVE_PTY_0_ON_OWN_PTY=y`;
  `CONFIG_MBEDTLS_X509_CRT_PARSE_C`, the two `KEY_EXCHANGE_ECDHE_*`, `MBEDTLS_PEM_PARSE_C` and
  `MBEDTLS_SSL_SERVER_NAME_INDICATION` not set.

## Items

`3a531e5` changes 25 files against `bc1c986`, and `580ae79` 3 more. The commits on master
between the previous upgrade (`c70a870`, 2026-03-10, v4.2 → v4.3) and `bc1c986`, and those
after `4ea9219` up to "Zephyr main compatibility" (`945384b`, 2026-09-24) were checked. There
are ten upgrade items: eight in scope (H1–H8), two out (H9, H10). The other changes are
classified at the end.

### H1. NVS header moved to `zephyr/kvss/` (`lib/edgehog_device`): in scope

**Zephyr's change:**

- `919a4f1601e` "kvss: Move NVS and ZMS headers under kvss subdir" moves
  `include/zephyr/fs/nvs.h` to `include/zephyr/kvss/nvs.h`;
- `097fba75175` "fs: Add redirections for nvs.h and zms.h" leaves `zephyr/fs/nvs.h` as a
  redirect that includes the new header and then
  `__WARN("This header file is deprecated, please use zephyr/kvss/nvs.h instead.")` (v4.4.0
  `include/zephyr/fs/nvs.h:15-17`). Both are in v4.4.0, not v4.3.0.
- `migration-guide-4.4.rst:634-637` (File System): "NVS and ZMS have been moved to the new
  Key-Value Storage Systems (KVSS) subsystem; the move affects NVS and ZMS interface header
  paths which have been moved from `zephyr/fs/` to `zephyr/kvss/`."

**Parts:**

| Part | Where (`bc1c986`) | What changed |
|---|---|---|
| H1a | `lib/edgehog_device/include/nvs.h:18` | `#include <zephyr/fs/nvs.h>` → `#include <zephyr/version.h>` and `#if (KERNEL_VERSION_MAJOR >= 4) && (KERNEL_VERSION_MINOR >= 4)` `#include <zephyr/kvss/nvs.h>` `#else` the old include |
| H1b | `lib/edgehog_device/nvs.c:10` | the same, with `zephyr/version.h` added after `:11` |

The version `#if` is part of the item (the scaffolding that keeps v4.3 building).

**Scope:** in. `nvs.c` and `storage_usage.c` (which includes `include/nvs.h`) are compiled
at both refs, and `zephyr/fs/nvs.h` is in their deps at the source.

**Revealing artifacts:**

- **Seen** in the salvage build log of the target, twice (compiling `nvs.c` and
  `storage_usage.c`): `zephyr/include/zephyr/toolchain/gcc.h:357:20: warning: This header file
  is deprecated, please use zephyr/kvss/nvs.h instead.`, "in expansion of macro `__WARN`" at
  `zephyr/fs/nvs.h:17`. GCC's include chain for a `_Pragma` warning names
  `include/edgehog_device/device.h:21` → `nvs.h:15` → `nvs.c:7` (and `storage_usage.h:15` →
  `storage_usage.c:9`), not the include lines `nvs.h:18` and `nvs.c:10`. It's a warning, not
  an error, in zmig's build; under twister it fails the CI build.
- Not available in the plain target build (it doesn't compile).
- Also: the header at both refs (moved, with a redirect), the source build's include chain,
  and the guide entry `:634-637`.

### H2. `FIXED_PARTITION_*` → `PARTITION_*` (`nvs.c`, `ota.c`): in scope

**Zephyr's change:** `migration-guide-4.4.rst:1345-1379` (Flash map): the `FIXED_PARTITION_*`
macros of `include/zephyr/storage/flash_map.h` are deprecated, replaced by `PARTITION_*`
(`f8f83289c00`, in v4.4.0). At v4.4.0, `flash_map.h:383`, `:400` and `:501` are
`#define FIXED_PARTITION_ID(label) PARTITION_ID(label) __DEPRECATED_MACRO`, and the same for
`_OFFSET` and `_DEVICE`.

| Part | Where (`bc1c986`) | What changed |
|---|---|---|
| H2a | `lib/edgehog_device/nvs.c:19` | `#define NVS_PARTITION_DEVICE FIXED_PARTITION_DEVICE(NVS_PARTITION)` → `PARTITION_DEVICE(…)` under a v4.4 `#if`, the old line in `#else` |
| H2b | `lib/edgehog_device/nvs.c:20` | `NVS_PARTITION_OFFSET`: `FIXED_PARTITION_OFFSET` → `PARTITION_OFFSET`, same `#if` |
| H2c | `lib/edgehog_device/ota.c:46` | `#define FLASH_AREA_IMAGE_PRIMARY FIXED_PARTITION_ID(SLOT0_LABEL)` → `PARTITION_ID(…)`, `#if` (and `zephyr/version.h` added after `:26`) |
| H2d | `lib/edgehog_device/ota.c:47` | `FLASH_AREA_IMAGE_SECONDARY`: the same |
| H2e | `lib/edgehog_device/ota.c:523` | `boot_erase_img_bank(FIXED_PARTITION_ID(SLOT1_LABEL))` → `boot_erase_img_bank(FLASH_AREA_IMAGE_SECONDARY)` |

The comment lines `nvs.c:18` and `ota.c:45` ("FIXED_PARTITION_ID() values used below are
auto-generated by DT") are replaced by the `#if`. A finding at `:18`/`:45` meets the location
condition for the parts below it.

**Scope:** in. Both files are compiled at both refs, with `CONFIG_FLASH_MAP=y` and
`CONFIG_SETTINGS_NVS=y`. `FLASH_AREA_IMAGE_PRIMARY` (H2c) is defined but never used in `ota.c`
(only `_SECONDARY`, at `:448` and `:451`); the line is compiled, but its macro is never
expanded.

**Revealing artifacts:**

- **Seen** in the salvage build log of the target: `warning: Macro is deprecated`
  (`toolchain/gcc.h:357`), "in expansion of macro `FIXED_PARTITION_DEVICE`" at `nvs.c:19:30`
  (used at `:31`), `FIXED_PARTITION_OFFSET` at `nvs.c:20:30` (used at `:36`),
  `FIXED_PARTITION_ID` at `ota.c:47:36` (used at `:448` and `:451`) and at `ota.c:523:35`.
  Nothing for `ota.c:46` (never expanded). Warnings, not errors.
- Not available in the plain target build.
- Also: the guide table and the `flash_map.h` declarations at both refs (plain macros at
  v4.3.0, `__DEPRECATED_MACRO` at v4.4.0), which cover H2c too.

### H3. `MBEDTLS_PEM_CERTIFICATE_FORMAT` replaced by its underlying options (`e2e/prj.conf`): in scope

**Zephyr's change:** Mbed TLS 4.1 and TF-PSA-Crypto (`migration-guide-4.4.rst:1445-1454`).
`1ddcd197598` "modules: mbedtls: replace CONFIG_MBEDTLS_PEM_CERTIFICATE_FORMAT" (in v4.4.0)
removes the symbol; `:1459-1461`: "`CONFIG_MBEDTLS_PEM_CERTIFICATE_FORMAT` has been replaced by
the underlying options it used to enable: `CONFIG_MBEDTLS_PEM_PARSE_C`,
`CONFIG_MBEDTLS_PEM_WRITE_C` and `CONFIG_MBEDTLS_BASE64_C`." (At v4.3.0, `config-mbedtls.h:410-415`
defined those three from it.)

| Part | Where (`bc1c986`) | What changed | Scope |
|---|---|---|---|
| H3a | `e2e/prj.conf:74` | `CONFIG_MBEDTLS_PEM_CERTIFICATE_FORMAT=y` → `CONFIG_MBEDTLS_PEM_PARSE_C=y`, `CONFIG_MBEDTLS_PEM_WRITE_C=y`, `CONFIG_MBEDTLS_BASE64_C=y` | **in**: merged at both refs |
| H3b | `samples/edgehog_app/prj.conf:55` | the same | out: the sample isn't built |
| H3c | `tests/lib/edgehog_device/integration/prj.conf:54` | the same | out: tests aren't built |

**Revealing artifacts:**

- **Seen** in the target configure log: `e2e/prj.conf:74: warning: attempt to assign the value
  'y' to the undefined symbol MBEDTLS_PEM_CERTIFICATE_FORMAT`, then "error: Aborting due to
  Kconfig warnings". It names the file, line and symbol, not the replacement.
- In the salvage build (line deleted, nothing added), the Astarte SDK fails to compile:
  `crypto.c:91:14: error: implicit declaration of function 'mbedtls_pk_write_key_pem'` and
  `:151:11` `mbedtls_x509write_csr_pem`. So deleting the line alone isn't a fix.
- Also: the guide entry `:1459-1461` (it gives the replacement), and the Kconfig tree at the
  target (symbol gone; `MBEDTLS_PEM_PARSE_C` at `Kconfig.tf-psa-crypto:150`).

### H4. `MBEDTLS_SERVER_NAME_INDICATION` renamed (`e2e/prj.conf`): in scope

**Zephyr's change:** `e389d88a19c` "modules: mbedtls: rename
CONFIG_MBEDTLS_SERVER_NAME_INDICATION" (in v4.4.0); `migration-guide-4.4.rst:1463-1464`:
"`CONFIG_MBEDTLS_SERVER_NAME_INDICATION` has been renamed to
`CONFIG_MBEDTLS_SSL_SERVER_NAME_INDICATION`."

| Part | Where (`bc1c986`) | What changed | Scope |
|---|---|---|---|
| H4a | `e2e/prj.conf:77` | `CONFIG_MBEDTLS_SERVER_NAME_INDICATION=y` → `CONFIG_MBEDTLS_SSL_SERVER_NAME_INDICATION=y` | **in** |
| H4b | `samples/edgehog_app/prj.conf:58` | the same | out: not built |
| H4c | `tests/lib/edgehog_device/integration/prj.conf:57` | the same | out: not built |

**Revealing artifacts:** **seen** in the target configure log: `e2e/prj.conf:77: warning:
attempt to assign the value 'y' to the undefined symbol MBEDTLS_SERVER_NAME_INDICATION`. Also
the guide entry (gives the new name) and the target Kconfig tree. The salvage build compiles
without SNI (no build error follows from dropping it; the TLS client would no longer send
SNI, a run-time effect).

### H5. Removed Mbed TLS symbols dropped (`e2e/prj.conf`): in scope

**Zephyr's change:** `migration-guide-4.4.rst:1476-1526`, "The following Mbed TLS Kconfig
symbols have been removed", which lists all six below (`51325c4953c` "modules: mbedtls: remove
deprecated build symbols" and `c42fefcb1f2` "… split Mbed TLS and TF-PSA-Crypto configuration
files", both in v4.4.0). Crypto is configured through `PSA_WANT_*` at v4.4.

| Part | Where (`bc1c986`) | What changed | Scope |
|---|---|---|---|
| H5a | `e2e/prj.conf:69` | `CONFIG_MBEDTLS_USE_PSA_CRYPTO=y` removed (guide `:1526`) | **in** |
| H5b | `e2e/prj.conf:82` | `CONFIG_MBEDTLS_GENPRIME_ENABLED=y` removed (`:1510`) | **in** |
| H5c | `e2e/prj.conf:85` | `CONFIG_MBEDTLS_ECDH_C=y` removed (`:1491`) | **in** |
| H5d | `e2e/prj.conf:86` | `CONFIG_MBEDTLS_ECDSA_C=y` removed (`:1492`) | **in** |
| H5e | `e2e/prj.conf:87` | `CONFIG_MBEDTLS_ECP_C=y` removed (`:1496`) | **in** |
| H5f | `e2e/prj.conf:88` | `CONFIG_MBEDTLS_ECP_DP_SECP256R1_ENABLED=y` removed (`:1507`) | **in** |
| H5g | `samples/edgehog_app/prj.conf:50, :63, :66-69` | the same six lines removed | out: not built |
| H5h | `tests/lib/edgehog_device/integration/prj.conf:49, :62, :65-68` | the same | out: not built |

**Revealing artifacts:** **seen** in the target configure log, one warning per line:
`e2e/prj.conf:69: warning: attempt to assign the value 'y' to the undefined symbol
MBEDTLS_USE_PSA_CRYPTO`, and the same at `:82` (`MBEDTLS_GENPRIME_ENABLED`), `:85`
(`MBEDTLS_ECDH_C`), `:86` (`MBEDTLS_ECDSA_C`), `:87` (`MBEDTLS_ECP_C`), `:88`
(`MBEDTLS_ECP_DP_SECP256R1_ENABLED`). Also the guide list and the target Kconfig tree.

### H6. X.509 certificate parsing is now a Kconfig option (`e2e/prj.conf`): in scope

**Zephyr's change** (no migration-guide entry):

- At v4.3.0, `modules/mbedtls/configs/config-mbedtls.h:398-418` defined
  `MBEDTLS_X509_CRT_PARSE_C` whenever an ECDHE/RSA key exchange was enabled, and
  `MBEDTLS_X509_USE_C` from it.
- `f2615043c41` "modules: mbedtls: add Kconfig option for MBEDTLS_X509_CRT_PARSE_C" (in v4.4.0)
  makes it a Kconfig symbol (`Kconfig.mbedtls:248`, no default; `MBEDTLS_X509_USE_C` at
  `Kconfig.tf-psa-crypto:211`), and `MBEDTLS_KEY_EXCHANGE_ECDHE_RSA_ENABLED` (`:123`) and
  `_ECDHE_ECDSA_ENABLED` (`:129`) now `depends on MBEDTLS_X509_CRT_PARSE_C`.

| Part | Where (`bc1c986`) | What changed | Scope |
|---|---|---|---|
| H6a | `e2e/prj.conf`, after `:77` | adds `CONFIG_MBEDTLS_X509_USE_C=y` and `CONFIG_MBEDTLS_X509_CRT_PARSE_C=y` | **in** |
| H6b | `samples/edgehog_app/prj.conf`, after `:58` | the same | out: not built |
| H6c | `tests/lib/edgehog_device/integration/prj.conf`, after `:57` | the same | out: not built |

The location of H6a is the key-exchange lines it unblocks, `e2e/prj.conf:83-84`
(`CONFIG_MBEDTLS_KEY_EXCHANGE_ECDHE_ECDSA_ENABLED=y`, `…_ECDHE_RSA_ENABLED=y`, unchanged by the
maintainers), or the insertion point after `:77`: a finding at either meets the location
condition.

**Revealing artifacts:**

- **Seen** in the target configure log: `warning: MBEDTLS_KEY_EXCHANGE_ECDHE_RSA_ENABLED
  (defined at modules/mbedtls/Kconfig.mbedtls:123) was assigned the value 'y' but got the value
  'n'. Check these unsatisfied dependencies: ((PSA_WANT_KEY_TYPE_ECC_KEY_PAIR_BASIC &&
  PSA_WANT_KEY_TYPE_RSA_KEY_PAIR_BASIC && MBEDTLS_X509_CRT_PARSE_C && …`, and the same for
  `MBEDTLS_KEY_EXCHANGE_ECDHE_ECDSA_ENABLED` (`:129`, `PSA_WANT_ALG_ECDH && PSA_WANT_ALG_ECDSA
  && MBEDTLS_X509_CRT_PARSE_C && …`). These name the symbols, not the conf lines (`:83-84`).
  Whether the `PSA_WANT_*` terms were also unmet at the target isn't shown (the salvage build
  deleted `:83-84`, so its `.config` can't tell).
- In the salvage build, Mbed TLS fails: `mbedtls_check_config.h:141:2: error: #error "One or
  more versions of the TLS protocol are enabled but no key exchange methods defined with
  MBEDTLS_KEY_EXCHANGE_xxxx"`.
- Also the Kconfig tree at the target (new symbol, new `depends on`), and the v4.3.0 config
  header.

### H7. `NATIVE_UART_0_ON_STDINOUT` removed (`e2e/boards/native_sim.conf`): in scope

**Zephyr's change:** `40dc5b17a8b` "drivers uart_native_pty: Remove deprecated options &
binding" (in v4.4.0). At v4.3.0, `drivers/serial/Kconfig.native_pty:64` defined
`NATIVE_UART_0_ON_STDINOUT` ("deprecated, use UART_NATIVE_PTY_0_ON_STDINOUT instead", `select
DEPRECATED`), and `:27` made it the default of the `UART_NATIVE_PTY_0` choice. Deprecated in
the 4.2 rename (`cb53e40ff93`, per the commit message). No entry in the 4.4 migration guide.

| Part | Where (`bc1c986`) | What changed |
|---|---|---|
| H7 | `e2e/boards/native_sim.conf:6` | `CONFIG_NATIVE_UART_0_ON_STDINOUT=y` → `CONFIG_UART_NATIVE_PTY_0_ON_STDINOUT=y` |

**Scope:** in. The fragment is merged at both refs; at the source both symbols are `y`.

**Revealing artifacts:**

- **Seen** in the target configure log: `e2e/boards/native_sim.conf:6: warning: attempt to
  assign the value 'y' to the undefined symbol NATIVE_UART_0_ON_STDINOUT`.
- If the line is only deleted (the salvage build), the UART falls back to its own PTY
  (`CONFIG_UART_NATIVE_PTY_0_ON_OWN_PTY=y`) and the e2e harness, which reads the shell from
  stdout, wouldn't see it: a run-time effect.
- Also the Kconfig tree at both refs (`Kconfig.native_pty`, the replacement exists at both).
  The deprecation is visible at the source: the symbol `select`s `DEPRECATED`.

### H8. `ZVFS_OPEN_MAX` 25 → 26 for the eventfd descriptor (`e2e/prj.conf`): in scope

**Zephyr's change:** `3c855aeddd1` "zvfs: eventfd: count eventfd file descriptors for fdtable
size" (in v4.4.0) adds `ZVFS_OPEN_ADD_SIZE_EVENTFD` (`lib/os/zvfs/Kconfig:53`, default
`ZVFS_EVENTFD_MAX`, 1 here). `lib/os/CMakeLists.txt` (both refs) sums the
`ZVFS_OPEN_ADD_SIZE_*` options, and when a non-zero `CONFIG_ZVFS_OPEN_MAX` is below the sum it
warns and raises the table to the sum. No migration-guide entry.

| Part | Where (`bc1c986`) | What changed | Scope |
|---|---|---|---|
| H8a | `e2e/prj.conf:171` | `CONFIG_ZVFS_OPEN_MAX=25` → `26` | **in** |
| H8b | `samples/edgehog_app/prj.conf:159` | `25` → `26` | out: not built |
| H8c | `tests/lib/edgehog_device/integration/prj.conf:146` | `16` → `26` | out: not built |

**Scope:** in. At the source the requirements sum to 25 (`NET` 20, `SOCKETS_SERVICE` 1, `TLS`
4) and `CONFIG_ZVFS_EVENTFD=y`; at the target `ZVFS_OPEN_ADD_SIZE_EVENTFD=1` makes it 26.

**Revealing artifacts:**

- **Seen** in the salvage build's configure output: `CONFIG_ZVFS_OPEN_MAX is less than requested
  minimum: 25 < 26 / Setting the file descriptor size to 26` (a CMake warning). Not in the plain
  target build, which stops in Kconfig before it.
- Also the target Kconfig tree (the new symbol) and both refs' `.config`.
- Without the edit nothing breaks: the build rounds up. The maintainers' edit removes the
  warning (twister's CI build would show it).

### H9. `ztar` null check on a `[static N]` parameter (GCC 14, SDK 1.0): out of scope

- **What changed** (`580ae79`): `lib/edgehog_device/ztar/unpack.c:143`
  `if (!header || !buffer) {` → `if (!header) {` in `ztar_unpack_get_file_name(const
  ztar_header_t *header, char buffer[static ZTAR_FILE_NAME_BUFF_SIZE])`.
- **Cause:** v4.4 needs SDK ≥ 1.0.0 (`migration-guide-4.4.rst:26`), whose GCC 14.3 warns
  `'nonnull' argument 'buffer' compared to NULL [-Wnonnull-compare]` for a `[static N]`
  parameter; SDK 0.17.4's GCC 12.2 doesn't (checked with both SDKs' `arm-zephyr-eabi-gcc -Wall`
  on a reduced example). Twister builds with warnings as errors. `ztar` was added on master
  the day before (`b049000`, 2026-05-25), on v4.3.
- **Scope:** out. `ztar/` is compiled only with `CONFIG_EDGEHOG_DEVICE_FILE_TRANSFER_TAR=y`;
  the e2e default test has file transfer off. (Even in the e2e `file_transfer` variant,
  `native_sim` uses the host GCC at both refs.)
- **Revealing artifact:** a target compile with SDK 1.0's GCC (e.g. the sample on an Arm
  board with file transfer on).

### H10. pytest harness: `readlines_until` takes `regex=` by keyword (`e2e/pytest`): out of scope

- **What changed:** `e2e/pytest/test_e2e.py:49` and `:63`
  `dut.readlines_until(SHELL_IS_READY, timeout=60)` → `readlines_until(regex=SHELL_IS_READY,
  timeout=60)` (and `SHELL_IS_CLOSING`).
- **Zephyr's change:** `1b7810f0cb7` "scripts: twister: Support multiple ports in
  pytest-twister-harness" (in v4.4.0): `DeviceAdapter.readlines_until(self, connection_index: int
  = 0, **kwargs)` (v4.3.0: `(self, regex=None, num_of_lines=None, …)`). A positional regex is
  now taken as the connection index.
- **Scope:** out. Python test harness; no build parses it.
- **Revealing artifact:** the harness source at both refs; nothing in a build.

## Not upgrade items (with reasons)

Every file of `bc1c986..3a531e5` and of `580ae79` is accounted for below or in H1–H10.

| Commit / file | Change | Why it's not an item |
|---|---|---|
| `west.yml:20`, `west-ci-reference.yml`, `west-e2e.yml` | `revision: v4.3.0` → `v4.4.0` | The bump itself |
| `west.yml:24` (and the CI manifests) | `astarte-device-sdk-zephyr` `revision: master` → `a72dd16` (`c83cad1` "Pin Astarte device") | **Uncertain**, see below. `a72dd16` is Astarte's master of 2026-05-05 ("Use zephyr UUID", the counterpart of PR #154's `95ef965`). Astarte's Zephyr 4.4 support (`5aefd03`, 2026-04-27) was already on master before the pin, and master at the source date (`6b59c72`) supports both refs too. The scanned build uses `a72dd16` at both refs, so the change isn't visible to it |
| `west.yml:13-14` (and CI manifests) | `secomind` remote removed | Unused by the project's own projects (Astarte's manifest names its own); clean-up |
| `west-ci-reference.yml`, `west-e2e.yml` | allowlists gain `tf-psa-crypto` (and `hal_espressif` in the reference one); comments dropped | CI manifests (§1.1). `tf-psa-crypto` is the Mbed TLS 4 module (guide `:1448-1450`), so it is caused by the upgrade, but the project's `west.yml` takes every module and needed no change (as for ble-copro). The scanned build passes it at the target |
| `west-ci-msv.yml` (new), `samples/edgehog_app/prj-zephyr-msv.conf` (new; `580ae79` fixes a symbol name in it), `samples/edgehog_app/sample.yaml` (`sample.msv`), `.github/workflows/build.yml` | a minimum-supported-version CI variant | CI and a new configuration |
| `lib/edgehog_device/Kconfig:16` | adds `depends on UUID` to `EDGEHOG_DEVICE` | The library has used Zephyr's UUID since `ea7a5cf` (2026-05-05, on v4.3; `UUID` exists at both refs, and every conf already sets `CONFIG_UUID=y`). A missing dependency declaration from PR #154's "Use Zephyr UUID" |
| `lib/edgehog_device/http.c:14` | drops `#include <zephyr/version.h>` | Unused include; clean-up |
| `lib/edgehog_device/{led,ota,telemetry,wifi_scan}.c`, `samples/edgehog_app/src/main.c` | `k_thread_name_set(…)` under `#ifdef CONFIG_THREAD_NAME` after each `k_thread_create`; `main.c` keeps the returned `k_tid_t` | A debugging feature. `k_thread_name_set` and `THREAD_NAME` are unchanged between the refs |
| `lib/edgehog_device/wifi_scan.c:27` | `WIFI_SCAN_THREAD_STACK_SIZE` 2048 → 16384 | Stack tuning; not shown to be caused (and `wifi_scan.c`'s body needs `CONFIG_WIFI`, off here) |
| `samples/edgehog_app/src/eth.c` | removes the `KERNEL_VERSION_MINOR >= 2` `#if`/`#else` around the net-mgmt handlers (`uint64_t` vs `uint32_t mgmt_event`) | Drops pre-4.2 support; the kept branch is the one already compiled at v4.3 |
| `samples/edgehog_app/prj.conf`, `tests/…/prj.conf` (besides H3–H6, H8) | Astarte/Edgehog blocks moved to the top; `CONFIG_REQUIRES_FLOAT_PRINTF=y` added; `CONFIG_SYS_HASH_FUNC32=y`, `CONFIG_NET_SOCKETS_POLL_MAX=4` removed; stack sizes (`MAIN_STACK_SIZE` 4096 → 8192, `EDGEHOG_DEVICE_THREAD_STACK_SIZE`, `ZBUS_THREAD_STACK_SIZE`, `SYSTEM_WORKQUEUE_STACK_SIZE` dropped, `NET_MGMT_EVENT_STACK_SIZE=4096` added); `CONFIG_NET_MAX_CONN=6` added; tests: `CONFIG_POSIX_TIMERS=y` removed, `TLS_MAX_CREDENTIALS_NUMBER=8` and `THREAD_NAME=y` added | Reordering, tuning and clean-up. `REQUIRES_FLOAT_PRINTF`, `SYS_HASH_FUNC32`, `NET_MGMT_EVENT_STACK_SIZE`, `NET_MAX_CONN` and `POSIX_TIMERS` are unchanged between the refs; `NET_SOCKETS_POLL_MAX` is "[DEPRECATED]" at both. Not built in this configuration anyway |
| `samples/edgehog_app/prj-wifi.conf:14-15` | `NET_MGMT_EVENT_STACK_SIZE=2048` (moved to `prj.conf`) → `MAIN_STACK_SIZE=65536` | Stack tuning for the Wi-Fi variant |
| `samples/edgehog_app/boards/stm32h573i_dk.conf:12-13` | `CONFIG_LOG_BUFFER_SIZE` 2048 → 32768 | **Not shown to be caused** (uncertain): board tuning. Out of this build |
| `samples/edgehog_app/boards/stm32h573i_dk.overlay` | `chosen { zephyr,sram = &sram3; }` ("Move the default Zephyr RAM allocation to the 320KB SRAM3 bank") | **Not shown to be caused** (uncertain). The board's `zephyr,sram = &sram1` (256 KiB) and the SoC's `sram1`/`sram3` sizes are the same at both refs (`stm32h562.dtsi:59-74`); the board's partition changes are overridden by this overlay. It may be a RAM overflow from the bigger v4.4 build (Mbed TLS 4, a 32 KiB log buffer), which would need the sample's target build to show. Out of this build |
| `e2e/prj.conf` (besides H3–H6, H8) | nothing else | |
| `580ae79` `lib/edgehog_device/generated_interfaces.c` | drops a `// NOLINENUMBERLINT` comment (regenerated) | Comment only |
| `26e7df3` (2026-04-28, before) `lib/edgehog_device/CMakeLists.txt` | `zephyr_library_link_libraries(mbedTLS)`, "a workaround to make available the mbedTLS crypto headers in zephyr 4.3" | Made for v4.3, and not removed by the upgrade (still in `4ea9219`). No change, so no item |
| `ea7a5cf` (before), PR #154's `95ef965` | the library uses Zephyr's UUID instead of its own | A feature on v4.3; already on master at the source |
| `c70a870` (2026-03-10) | v4.2 → v4.3 | Another upgrade pair |
| master after `4ea9219`: `3822861`, `9326832`, `d691b66`, `0d865f4`…`1ead233`, `854d1bf`, `9f7ec1f`, `8d21955`, dependabot | file-transfer IDs, OTA UUID size fix, Astarte 1.3, network-interface property, storage usage, Astarte SDK updates | Features, fixes and dependency updates on v4.4, not attributed to Zephyr |
| `945384b` (2026-09-24) "Zephyr main compatibility" | | The next upgrade pair |

**Not changed, and not hazards in this build** (for precision checking):

- The Astarte SDK (`a72dd16`) includes `zephyr/fs/nvs.h` in `kv_storage_nvs.c` and is
  compiled at both refs; it's a dependency, and the maintainers didn't change it here.
- `lz4` left Zephyr's tree at v4.4.0 (`16fecc0276b` "manifest: optional: move lz4 to external":
  `modules/lz4/Kconfig` and `CMakeLists.txt` are gone). The project's `lz4` module carries its
  own since 2026-04-29 (`5402e54`), before the source date, so no project change was needed and
  `CONFIG_LZ4=y` resolves at both refs.

### Uncertain: the Astarte SDK pin

`c83cad1` "Pin Astarte device" is in the upgrade PR and changes a dependency, which §1.1 makes
an item when the upgrade causes it. It isn't counted, because nothing ties it to Zephyr:
Astarte's master supported v4.3 and v4.4 from 2026-04-27, and the pin's target is the commit
that matches the PR's own UUID change. If it were counted, it would be in scope (the build
uses the module) with no revealing artifact in this build: the scanned build has `a72dd16` at
both refs, and the source manifest's `master` isn't a revision a tool could compare.

## Counts

- **Items:** 10 (H1–H10).
- **In scope: 8 items:**
  - H1 (2 parts), H2 (5 parts), H7 (1 part);
  - H3, H4, H6, H8: part a in scope, parts b and c out;
  - H5: parts a–f in scope, g and h out.
- **Out of scope:** H9 (`ztar` not compiled) and H10 (pytest harness).
- **What the scanned target build shows:**
  - It stops in Kconfig. The 11 warnings point at the right file and line for H3a, H4a, H5a–f
    and H7, naming the undefined symbols. H6 shows as the two key exchanges "assigned 'y' but
    got 'n'" with `MBEDTLS_X509_CRT_PARSE_C` among the unmet dependencies, by symbol, not line.
  - H1, H2 and H8 need a target compile or configure past Kconfig. zmig's salvage build gets
    there, and its log has H1's deprecated-header warning (at the include chain, not the
    include line), H2's deprecated-macro warnings at `nvs.c:19, :20`, `ota.c:47, :523` (none
    for `ota.c:46`), and H8's "25 < 26" CMake warning.
  - Guide entries: H1 `:634-637`, H2 `:1345-1379`, H3 `:1459-1461`, H4 `:1463-1464`, H5
    `:1476-1526`. H6, H7 and H8 have none.
- **Reachable ceiling:** strict 8/8 in principle: every in-scope item has an artifact at the
  right file. H1, H2 and H8 are warnings, so catching them depends on zmig compiling (or
  configuring) the target past the Kconfig failure; H2c has only the guide and header. H6 needs
  a finding that reads the "assigned but got" dependency, or the new symbol, back to the conf.
