# Oracle: LibreSolar bms-firmware, Zephyr v4.0 → v4.2, additional configurations

Written 2026-10-28, **before zmig was run on these configurations** (only `--analyzers
none` verification builds, which produce no findings). The rules are in
[../ORACLE-RULES.md](../ORACLE-RULES.md). The items B1–B7 and the upgrade (bump commit
`c2566e481c44`, Zephyr `4ebecae78ad9` → `636ad05950bb`) are defined in
[bms-firmware.md](bms-firmware.md). This file only re-scopes them for two more builds. It
isn't edited after the runs; corrections go at the end, dated.

## Scanned builds

Both are `app/` from the pre-bump tree, with `ZEPHYR_EXTRA_MODULES=<bms-firmware>;<thingset-zephyr-sdk @4874076>;<thingset-node-c @68c7544>`,
which are both builds the project's own CI runs (`.github/workflows/zephyr.yml`):

| Config | Board / extra args | Modules given to zmig | SDK |
|---|---|---|---|
| **A1-native** | `native_sim` (CI: `west build -b native_sim`) | tinycrypt zcbor | host gcc |
| **A1-oled** | `bms_8s50_ic@0.2`, `-DEXTRA_CONF_FILE=oled.conf -DSHIELD=uext_oled` (CI's exact line) | cmsis hal_stm32 zcbor | 0.16.8 |

The module lists are what the **pre-bump** manifest allowlist provides for each build. The
allowlist is `cmsis edtt hal_espressif hal_stm32 mcuboot tinycrypt zcbor`; `tinycrypt` is
what Bluetooth host crypto selects at v4.0 on native_sim.

- Neither list includes the modules that the v4.2 manifest adds (`cmsis_6`, `mbedtls`).
  Those are oracle item B1.
- `hal_espressif`, `mcuboot` and `edtt` are left out, because neither build uses them.

A first verification attempt shows how these were chosen. It used native_sim without
`tinycrypt` and `bms_16s100_sc` with `uext_oled` but without `oled.conf`. It failed at the
source ref (TinyCrypt selected but absent; `cfb_print` undefined), so it was corrected to
CI's own invocations above.

Scope evidence comes from the verification builds: source = pre tree at `4ebecae78ad9`,
target = post tree at `636ad05950bb` (with the post manifest's `cmsis_6` / `mbedtls`), all
**ok**.

| | A1-native | A1-oled |
|---|---|---|
| project files compiled | `app/src/{bms_common,bms_soc,button,data_objects,leds,main}.c`, `drivers/bms_ic/bq769x2/{bq769x2,bq769x2_emul,bq769x2_interface}.c`, `lib/helper.c` | same app files **plus `app/src/oled.c`**, `drivers/bms_ic/isl94202/*.c`, `lib/helper.c` |
| ThingSet SDK files compiled | `ble.c can.c packetizer.c sdk.c serial.c shell.c storage_common.c storage_eeprom.c isotp_fast.c` (+ `thingset-node-c`) | `can.c packetizer.c sdk.c serial.c storage_common.c storage_eeprom.c isotp_fast.c` (+ node-c) |
| conf fragments | `prj.conf`, `app/boards/native_sim.conf` | `prj.conf`, `app/boards/bms_8s50_ic.conf`, `app/oled.conf` |
| overlays | `app/boards/native_sim.overlay` | `bms_8s50_ic_0_2_0.overlay`, `boards/shields/uext_oled/uext_oled.overlay` |
| target: `cmsis_6` in compile flags | no (not Cortex-M) | **yes** |
| target: `mbedtls` compiled | **yes** (Bluetooth host crypto) | no |
| `chosen thingset,eeprom` | set (`native_sim.overlay:20`) | set (board dts:36) |

## Scope per item

| Item | A1-native | A1-oled |
|---|---|---|
| **B1** module allowlist | **in**: part **B1b** (add `mbedtls`; the target build compiles it, and Bluetooth host crypto selects it). B1a (`cmsis_6`) out: native_sim isn't Cortex-M | **in**: part **B1a** (`cmsis_6`). B1b out: no mbedtls in this build |
| **B2** ThingSet SDK bump | **in**: module used. The upgrade-related part `src/storage_eeprom.c` (`eeprom-0` alias fallback) is compiled; `lorawan.c` isn't | **in**: same reasoning; `storage_eeprom.c` compiled |
| **B3** `bms_c1.conf` BT buffer counts | out (other board) | out |
| **B4** `NATIVE_UART_0_ON_STDINOUT` → `UART_NATIVE_PTY_0_ON_STDINOUT` (`app/boards/native_sim.conf:8`) | **in** (merged) | out |
| **B5** `UART_NATIVE_POSIX_PORT_1_ENABLE` removed → DT `&uart1` okay (`native_sim.conf:11`, `native_sim.overlay`) | **in** | out |
| **B6** `LOG_MODE_MINIMAL=n` → `LOG_MODE_DEFERRED=y` (`native_sim.conf:13`) | **in** | out |
| **B7** `CFB_DISPLAY_HEIGH` → `CFB_DISPLAY_HEIGHT` (`app/src/oled.c:109`) | out (`oled.c` not compiled) | **in** |

### Revealing artifacts

- **B1b (A1-native).** The target configure aborts on Kconfig warnings that `MBEDTLS
  (defined at modules/mbedtls/Kconfig:19)` and `PSA_WANT_*` have dependencies `0` but are
  selected by `BT_HOST_CRYPTO`. That was seen in a verification attempt without `mbedtls`.
  `west.yml` at the target declares `mbedtls`.
- **B1a (A1-oled).** As in [bms-firmware.md](bms-firmware.md): `cmsis_core.h: No such file
  or directory` in `zephyr/include/zephyr/arch/arm/asm_inline_gcc.h:24`, and
  `migration-guide-4.2.rst:795`.
- **B2 (both).** None expected. Both builds set `chosen thingset,eeprom`, so the new
  `DT_ALIAS(eeprom_0)` fallback can't change which device is used, and the files ThingSet
  changed for Zephyr compile the same way.
- **B4, B5.** At v4.2 both old symbols still exist, but are deprecated, so the Kconfig at
  the target shows `[DEPRECATED]`. B5 also has migration-guide-4.2 (Serial)
  `CONFIG_UART_NATIVE_POSIX_PORT_1_ENABLE has been deprecated`. Neither is an error at the
  target.
- **B6.** The maintainers' reason isn't stated. At the source, `prj.conf` sets
  `LOG_MODE_MINIMAL=y`, and `native_sim.conf`'s `=n` doesn't take a choice member away (the
  source `.config` has `LOG_MODE_MINIMAL=y`). Expected artifact, **not checked before the
  run**: a Kconfig value or deprecation change around `LOG_BACKEND_NATIVE_POSIX` /
  `LOG_MODE_*` at the target.
- **B7 (A1-oled).** A target compile error, `'CFB_DISPLAY_HEIGH' undeclared`, at
  `app/src/oled.c:109`. In `include/zephyr/display/cfb.h:31` the enumerator is
  `CFB_DISPLAY_HEIGH` at the source and `CFB_DISPLAY_HEIGHT` at the target. It's reachable
  only once B1a is resolved, because the target build otherwise stops at `cmsis_core.h`.

## Counts

| Config | In scope | Out of scope |
|---|---|---|
| A1-native | **5** (B1 [B1b], B2, B4, B5, B6) | 2 |
| A1-oled | **3** (B1 [B1a], B2, B7) | 4 |

B2 has no expected artifact in either build. B6's artifact is unconfirmed.
