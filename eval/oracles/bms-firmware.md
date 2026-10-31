# Oracle: LibreSolar bms-firmware, Zephyr v4.0 → v4.2

Written 2026-10-28, before zmig was run on this project. The rules are in
[../ORACLE-RULES.md](../ORACLE-RULES.md). This file isn't edited after the run; corrections
go at the end, dated.

## Upgrade and scanned build

- **Project:** LibreSolar/bms-firmware. Its `west.yml` is an application manifest that
  imports upstream Zephyr by **release branch** with a module `name-allowlist`. It also
  imports `thingset-zephyr-sdk`, which in turn brings in `thingset-node-c`.
  - The repo is a Zephyr module: out-of-tree board `bms_16s100_sc` under `boards/libresolar`,
    BMS IC drivers under `drivers/`, and `lib/`.
- **Bump commit:** `c2566e481c44` ("Upgrade to Zephyr v4.2", 2025-11-10).
  - Source tree: its parent, `77c6206`.
  - Between it and the v4.3 bump (`eb60673`, 2025-11-14) there is only `cf0b7d5`, a CI
    workflow change. There's no v4.2 fix-up.
- **Zephyr refs:** the branch heads aren't pinned, so they were reconstructed as the
  branch tip on the day of each commit:
  - from `v4.0-branch` at `4ebecae78ad9` (v4.0.0-118);
  - to `v4.2-branch` at `636ad05950bb` (v4.2.1-8).
- **Scanned build:** `app/` on `bms_16s100_sc` (resolves to `bms_16s100_sc/stm32g0b1xx`), no
  extra conf or overlay files, no sysbuild.
  - Modules: zmig is given the **pre-bump** project. That means
    `ZEPHYR_EXTRA_MODULES=<bms-firmware>;<thingset-zephyr-sdk @4874076>;<thingset-node-c @68c7544>`,
    and `--module cmsis --module hal_stm32 --module zcbor`.
  - Those three modules are the subset of the source manifest's allowlist that this build
    uses. Left out of the list, because they aren't used by this board and app:
    `hal_espressif` (ESP32 boards only), `mcuboot` (no sysbuild), `edtt` (BT test harness),
    and `tinycrypt`.
  - `cmsis_6` is deliberately **not** passed, because the pre-bump manifest doesn't list it.
    See B1.
  - SDK: 0.16.8.
- **How scope was established:** from the verification builds at both refs
  (`compile_commands.json`, `zephyr/.config`, `zephyr_modules.txt`). Nothing came from zmig
  findings.
  - **Compiled from the project:** `app/src/{bms_common,bms_soc,button,data_objects,leds,main}.c`,
    `drivers/bms_ic/bq769x2/*.c` and `lib/helper.c`.
  - **Compiled from ThingSet:** `packetizer.c`, `sdk.c`, `serial.c`, `storage_common.c`,
    `storage_flash.c` and `subsys/canbus/isotp_fast/isotp_fast.c` from the SDK, plus
    `thingset-node-c` `src/*.c`.
  - **Not compiled:** `app/src/oled.c`, which is `zephyr_sources_ifdef(CONFIG_SHIELD_UEXT_OLED …)`.
    Also not compiled: the SDK's `lorawan.c`, `storage_eeprom.c`, `can.c`, `ble.c`, `shell.c`
    and `websocket.c`.
  - **Config:** `CONFIG_LOG_MODE_MINIMAL=y`, and `CONFIG_BT` isn't set.
  - **Modules:** the target build includes `cmsis_6` headers (all 164 compile commands) and
    no `mbedtls` sources.

## Items

### B1. Manifest module allowlist: `cmsis` → `cmsis_6`, add `mbedtls`: in scope (part B1a)

**What changed:** `west.yml` name-allowlist.

| Part | Change | In scope | Reason |
|---|---|---|---|
| B1a | `cmsis` → `cmsis_6` | **yes** | the target build compiles against `cmsis_6` (Cortex-M0+ STM32G0) |
| B1b | add `mbedtls` | no | no `mbedtls` sources or Kconfig in this build at the target |

**Revealing artifact (B1a):** the target build fails to configure or compile without the
`cmsis_6` module: missing CMSIS core headers, or a CMake/Kconfig error naming CMSIS.
`migration-guide-4.2.rst:795-802` says Cortex-M boards now require `CMSIS_6`. Zephyr's
`west.yml` at the target lists `cmsis_6`.

### B2. ThingSet SDK bump `4874076` → `b9d3007` (with `thingset-node-c` `68c7544` → `6033e55`): in scope (module used)

**What changed:** `west.yml` `thingset-zephyr-sdk` revision. The upgrade-related changes
inside the dependency are:

- `bb30cc3` (Zephyr v4.1): `src/lorawan.c` `downlink_callback(…, bool data_pending, …)`
  → `(…, uint8_t flags, …)`, following the Zephyr 4.1 LoRaWAN callback change.
- `b9d3007` (Zephyr v4.2): `src/storage_eeprom.c` also accepts the `eeprom-0` alias, and
  the `native_sim` test boards were dropped. Its manifest also goes `cmsis` → `cmsis_6`.
- The rest of the range is feature work: CAN control reporting, the live-period unit, a
  Kconfig typo, and binary report import in `thingset-node-c`.

**Scope:** in scope under ORACLE-RULES §1.2, because the build uses the module. Neither
upgrade-related file is compiled in this build.

**Revealing artifact:** none expected in this build. The ThingSet files this build compiles
didn't change for the upgrade, so the old revision should build at v4.2. A build with
`CONFIG_LORAWAN` would show B2 as an incompatible-pointer warning in `lorawan.c`.

### Out of scope

| # | Change | Reason |
|---|---|---|
| B3 | `CONFIG_BT_BUF_ACL_TX_COUNT=12`, `CONFIG_BT_BUF_EVT_RX_COUNT=16` added to `app/boards/bms_c1.conf` | only applies to the `bms_c1` board |
| B4 | `CONFIG_NATIVE_UART_0_ON_STDINOUT` → `CONFIG_UART_NATIVE_PTY_0_ON_STDINOUT` in `app/boards/native_sim.conf` | native_sim only |
| B5 | `CONFIG_UART_NATIVE_POSIX_PORT_1_ENABLE=y` removed; `&uart1 { status = "okay"; }` added to `app/boards/native_sim.overlay` | native_sim only |
| B6 | `CONFIG_LOG_MODE_MINIMAL=n` → `CONFIG_LOG_MODE_DEFERRED=y` in `app/boards/native_sim.conf` | native_sim only |
| B7 | `CFB_DISPLAY_HEIGH` → `CFB_DISPLAY_HEIGHT` at `app/src/oled.c:109` | `oled.c` is only built with the `uext_oled` shield |

Not an item: `west.yml` Zephyr `revision: v4.0-branch` → `v4.2-branch` (the bump itself).

## Counts

- **In scope: 2 items** (B1, with 1 of its 2 parts in scope; B2).
- **Out of scope: 5 items** (B3–B7).

For this board, nearly all of the maintainers' code and config edits are out of scope. The
in-scope oracle is two dependency-level items, and one of them (B2) has no artifact in this
build. Recall on this pair will rest on a single item. A board with more of the project's
code in its build would make a better held-out oracle. Candidates are native_sim (B4–B6) and
`bms_16s100_sc` with the `uext_oled` shield (B7).
