# Oracle: CoffeeCaller PR #16 (Zephyr v4.2.0 → v4.4.0)

Written 2026-10-30, **before zmig was run on this project**. Only `--analyzers none`
verification builds were run, and they produce no findings. The rules are in
[../ORACLE-RULES.md](../ORACLE-RULES.md). This file isn't edited after the run;
corrections go at the end, dated.

This is a held-out project. It's unseen: no zmig change has been informed by it.

## Upgrade and scanned build

**Project.** CoffeeCaller (https://github.com/Alicipy/CoffeeCaller, Apache-2.0), firmware for
a small OpenThread board that coordinates coffee breaks.

- It's a Zephyr module (`zephyr/module.yml`, name `coffeecaller`, `board_root: .`,
  `dts_root: .`) with the app in `applications/coffeecaller/`, its own subsystems
  (`subsys/cc_broadcast`, `cc_coffeecall`, `cc_temperature`), a library (`lib/cc_lib`) and
  two native_sim drivers (`drivers/pwm`, `drivers/led_strip`).
- Its `west.yml` imports upstream Zephyr by tag, with a name-allowlist, and adds the
  `bridle` module (tiacsys/bridle), which holds the real board, `coffeecaller_nrf52`.
- It adds files to two boards it doesn't own through **board-extension directories**:
  `boards/extensions/native_sim/` (a `.conf` and an `.overlay` that give `native_sim` the
  button, LED strip, buzzer PWM and watchdog the app needs) and
  `boards/extensions/coffeecaller/` (a `.conf` for bridle's `coffeecaller_nrf52`).

**The upgrade** is PR #16, merge `0aefa36` "Upgrade to Zephyr 4.4.0" (2026-10-18). The range
`0aefa36^1..0aefa36^2` has 6 commits:

| Commit | Date | Subject |
|---|---|---|
| `84c51d5` | 2026-09-06 | chore: bump zephyr and bridle to v4.3.0 |
| `fc972c5` | 2026-09-06 | fix: use HWMv2 layout for local board extensions |
| `473a01b` | 2026-09-06 | chore: migrate to Zephyr 4.4 bridle 4.4 SDK 1.0.1 |
| `1f2c1f2` | 2026-09-06 | fix: enlarge temp stack for OpenThread CoAP send |
| `809002b` | 2026-10-18 | fix: use board variant for native_sim extension |
| `904416f` | 2026-10-18 | ci: pass gcov tool to twister explicitly |

| Ref | Commit | Notes |
|---|---|---|
| Source tree (pre-upgrade, `0aefa36^1`) | `2b0a8f25b168680b883ab0df4d4ec87264c367cd` | `west.yml`: zephyr `v4.2.0`, bridle `v4.2.1` |
| Maintainers' state after the upgrade | `0aefa367f44bf6ab9a892ce9ab8d40067fe0a852` | zephyr `v4.4.0`, bridle `v4.4.0`, `tf-psa-crypto` added |
| Zephyr from | `v4.2.0` = `413b789deb391d3a37d06b463288a5fe765ee57e` | |
| Zephyr to | `v4.4.0` = `684c9e8f32e4373a21098559f748f06915f950c9` | crosses v4.3.0 (`3568e1b6d5cd`) |
| bridle from | `v4.2.1` = `d12204cf20b8c1039337f0afe67d64291c0674e4` | not in the scanned build (see below) |
| bridle to | `v4.4.0` = `98ad034927f2c9acc2f01e56aac7ab11c4c87310` | |

**Configuration `coffeecaller-native`** (this file):

- `applications/coffeecaller` on `native_sim` (no SDK: host gcc). The pre-upgrade CI builds
  the app's `integration_platforms` `native_sim` and `coffeecaller_nrf52/nrf52840`, and the
  README names `native_sim` as a supported board.
- `-DZEPHYR_EXTRA_MODULES=<CoffeeCaller root>`: this is how the maintainers' west workspace
  sees the repo (it's a module). Its `board_root: .` puts `<root>/boards/extensions/native_sim`
  into the v4.2 board-extension search. No patch to the app is needed.
- **Modules: none.** On `native_sim` the broadcast transport is `TRANSPORT_LOCALQUEUE`
  (`subsys/cc_broadcast/Kconfig`: `default TRANSPORT_LOCALQUEUE if BOARD_NATIVE_SIM`), so
  OpenThread, Mbed TLS and the HALs aren't used. The `.config` has no `MBEDTLS`/`PSA`/
  `OPENTHREAD` symbol at either ref.
- **bridle is not in the build.** The app doesn't need it on `native_sim` (the build
  configures and compiles without it). With bridle v4.2.1 as an extra module, the source
  build fails at Kconfig (bridle's `lib/gui/lvgl/Kconfig:8` warning, "Aborting due to Kconfig
  warnings", because the `lvgl` module isn't present), and the target build fails at board
  discovery (bridle v4.2.1's `boards/cytron/maker_rp2040/board.yml` is malformed under v4.4's
  schema). zmig would keep bridle at v4.2.1 at the target (it takes project modules from the
  scanned tree at both refs), so including it would only destroy the target build.

The `coffeecaller_nrf52/nrf52840` configuration isn't scanned: it needs bridle at a matching
revision at each ref, which zmig can't do yet.

**Scope evidence** comes from the verification builds:

- **Source build (v4.2.0): ok.** Compiled project files (`compile_commands.json`, 16 files
  plus the app's `src/main.c`): `drivers/led_strip/fake_led_strip.c`,
  `drivers/pwm/pwm_native_sim.c`, `lib/cc_lib/{aldrin,board,button,leds,watchdog}.c`,
  `subsys/cc_broadcast/src/{cc_bc_client_handling,cc_bc_serialization,cc_bc_transport_localqueue}.c`,
  `subsys/cc_coffeecall/src/{cc_leds,main,participant_list,state_machine}.c`,
  `subsys/cc_temperature/src/{fake_sensor,main}.c`. Headers in ninja deps include
  `include/cc_lib/*.h` and `subsys/cc_broadcast/src/*.h`.
- **Source config:** "Found devicetree overlay: `boards/extensions/native_sim/native_sim.overlay`"
  and "Merged configuration `boards/extensions/native_sim/native_sim.conf`", then the app's
  `prj.conf`. `CONFIG_LED_STRIP=y`, `CONFIG_PWM=y`, `CONFIG_GPIO=y`, `CONFIG_REBOOT=y`,
  `CONFIG_FAKE_LED_STRIP=y`, `CONFIG_PWM_NATIVE_SIM=y`, `CONFIG_TRANSPORT_LOCALQUEUE=y`,
  `CONFIG_CC_TEMPERATURE_SENSOR_FAKE=y`; `CONFIG_SERIAL` not set. `subsys/cc_broadcast/Kconfig`
  is in `zephyr/kconfig/sources.txt`.
- **Source Kconfig warning (observed):** "The choice symbol NATIVE_UART_0_ON_STDINOUT (defined
  at drivers/serial/Kconfig.native_pty:63) was selected (set =y), but no symbol ended up as the
  choice selection" (the choice is inactive because `SERIAL` is off).
- **Target build (v4.4.0): build-failed (compile).** It configures; the log has no
  "Found devicetree overlay" and no "Merged configuration" for the extension files, and
  `LED_STRIP`, `PWM`, `GPIO`, `REBOOT` are no longer set. Compile errors (observed) in
  `lib/cc_lib/button.c:18` (`#error "Unsupported board: sw0 devicetree alias is not defined"`),
  `lib/cc_lib/watchdog.c:21` (same, `watchdog0`) and `:24`, `lib/cc_lib/leds.c:15,18`
  (`DT_N_ALIAS_led_strip_*` undeclared), `lib/cc_lib/aldrin.c:16`
  (`DT_N_ALIAS_pwm_buzzer0_*` undeclared). The same 16 project files are in its
  `compile_commands.json`.
- **Maintainers' post-upgrade tree** at v4.4.0 on `native_sim/native/coffeecaller` (an extra
  verification build): ok. "Found BOARD.dts: `boards/extensions/native_sim/native_sim_native_coffeecaller.dts`",
  "Loaded configuration `…/native_sim_native_coffeecaller_defconfig`" (the variant loads only
  its own defconfig, not `native_sim_defconfig`). Same 16 project files compiled.

## Items

The range has 6 commits. Six upgrade items; the rest are classified at the end.

### C1. Legacy board-extension directories are no longer applied (`fc972c5`, `809002b`): in scope

**Zephyr's change:** `cmake/modules/boards.cmake` at v4.2.0 (`:317-334`) and v4.3.0 collects
`<BOARD_ROOT>/boards/extensions/<board dir name>/` into `BOARD_EXTENSION_DIRS`;
`kconfig.cmake:94` merges `<board>.conf` and `dts.cmake:176` applies `<board>.overlay` from
those dirs. Zephyr commit `c02c6add101` ("cmake: modules: boards: Remove deprecated HWMv1
extension handling", in v4.4.0) deletes the collection, so nothing sets
`BOARD_EXTENSION_DIRS` at v4.4.0 and the files are silently ignored. It isn't in
`migration-guide-4.3.rst` or `-4.4.rst`, nor in either release note. The HWMv2 replacement is
a `board.yml` with `board: extend:`; such an extension only adds a board directory, whose
overlay isn't applied (809002b: "Zephyr 4.4 no longer applies overlays from board
extensions"), hence the maintainers' new board **variant**
`native_sim/native/coffeecaller` with a full `.dts`.

**Parts** (pre-upgrade tree):

| Part | Where | What changed | Scope |
|---|---|---|---|
| C1a | `boards/extensions/native_sim/native_sim.overlay` (whole file; aliases `:53-57`) | → `native_sim_native_coffeecaller.dts`, adding `#include <../boards/native/native_sim/native_sim.dts>`; `board.yml` declares `extend: native_sim` with variant `coffeecaller`, qualifier `native` | **in**: applied at source ("Found devicetree overlay") |
| C1b | `boards/extensions/native_sim/native_sim.conf` (`:5-9`) | → `native_sim_defconfig` (fc972c5), then `native_sim_native_coffeecaller_defconfig` with `CONFIG_CONSOLE=y` and `CONFIG_SYS_CLOCK_HW_CYCLES_PER_SEC=1000000` added, copying `boards/native/native_sim/native_sim_defconfig`, which a variant doesn't load | **in**: merged at source |
| C1c | `boards/extensions/coffeecaller/coffeecaller_nrf52.conf` | → `boards/extensions/coffeecaller_nrf52/{board.yml (extend: coffeecaller_nrf52), coffeecaller_nrf52_defconfig}` | out: another board (bridle's `coffeecaller_nrf52`) |
| C1d | `applications/coffeecaller/sample.yaml:10`, `samples/subsys/cc_coffeecall/sample.yaml:11`, `tests/subsys/cc_coffeecall/testcase.yaml:8,11`; new `native_sim_native_coffeecaller.yaml` | twister platform `native_sim` → `native_sim/native/coffeecaller`; twister board metadata for the variant | out: twister metadata, not parsed by the build |
| C1e | `tests/lib/cc_lib/prj.conf` | `+CONFIG_PWM=y`: the test still runs on plain `native_sim`, which no longer gets `CONFIG_PWM=y` from the extension `.conf` | out: test not built |

**Revealing artifacts**, expected from the code (not checked before the run), except where
marked observed:

- **C1a:** target compile errors at `lib/cc_lib/button.c:18`, `watchdog.c:21,24`,
  `leds.c:15,18`, `aldrin.c:16` (observed in the verification build: missing `sw0`,
  `watchdog0`, `led-strip`, `pwm-buzzer0` aliases). These point at the consumers, not at the
  overlay. The generated devicetree at the target lacks those nodes and aliases; the build
  log lacks the "Found devicetree overlay" line the source has. The CMake module diff
  (`boards.cmake`) shows the cause. No migration-guide entry.
- **C1b:** the Kconfig/`.config` diff: `CONFIG_LED_STRIP`, `CONFIG_PWM`, `CONFIG_GPIO`,
  `CONFIG_REBOOT` set at source and unset at target (observed), and the "Merged
  configuration" line for the extension `.conf` gone from the target log. No compiler error of
  its own (the C1a errors come first).

### C2. `NATIVE_UART_0_ON_STDINOUT` removed (`473a01b`): in scope

**Zephyr's change:** `migration-guide-4.2.rst:388-396` (deprecation): "The choice
`CONFIG_NATIVE_UART_0` has been replaced with `CONFIG_UART_NATIVE_PTY_0`". At v4.2.0 and
v4.3.0 `NATIVE_UART_0_ON_STDINOUT` exists with `select DEPRECATED`
(`drivers/serial/Kconfig.native_pty:63`). Zephyr commit `40dc5b17a8b` ("drivers
uart_native_pty: Remove deprecated options & binding", in v4.4.0) removes it. The removal
isn't in `migration-guide-4.3.rst`/`-4.4.rst`. It's counted: the symbol is removed at the
target, not only deprecated.

**Parts:**

| Part | Where | What changed | Scope |
|---|---|---|---|
| C2a | `boards/extensions/native_sim/native_sim.conf:11` | `CONFIG_NATIVE_UART_0_ON_STDINOUT=y` → `CONFIG_UART_NATIVE_PTY_0_ON_STDINOUT=y` | **in**: merged and the symbol evaluated at source (Kconfig warning observed) |
| C2b | `samples/subsys/cc_coffeecall/boards/native_sim.conf:5` | same rename | out: sample not built |

**Revealing artifact**, expected from the code (not checked before the run): the Kconfig
tree at the target (symbol gone from `Kconfig.native_pty`); the deprecation at the source
(`select DEPRECATED`, the v4.2 guide entry). In this build the target never merges the file
(C1), so there's no "undefined symbol" warning at the target. In both trees the choice is
inactive (`CONFIG_SERIAL` not set), so the value has no effect in this build.

### C3. `configdefault NVS` for the OpenThread transport (`473a01b`): in scope (by the parsed-file rule)

**Zephyr's change** (attribution inferred; the commit message doesn't say):
`migration-guide-4.4.rst:1538-1548` (OpenThread): `CONFIG_OPENTHREAD_CRYPTO_PSA` now selects
`CONFIG_PSA_CRYPTO` and is the default, and without TF-M `CONFIG_SECURE_STORAGE` is implied,
which "requires a backend for Secure Storage (Settings, ZMS, or a custom one) to be
configured". `migration-guide-4.4.rst:634-638` moves NVS under KVSS (header paths only). No
Zephyr or bridle v4.2.1 Kconfig selects or defaults `NVS` at either ref.

**Parts:** `subsys/cc_broadcast/Kconfig:86`, inside `if TRANSPORT_OPENTHREAD`, gains
`configdefault NVS` / `default y`.

**Scope:** in, because `subsys/cc_broadcast/Kconfig` is parsed (`kconfig/sources.txt`). But
`TRANSPORT_OPENTHREAD` isn't set on `native_sim`, so the default has no effect here.

**Revealing artifact:** none in this build. On `coffeecaller_nrf52` it would be the target
Kconfig tree (OpenThread's PSA/secure-storage implications) and the migration-guide entry
above.

### C4. Larger `cc_temperature` thread stack (`1f2c1f2`): in scope (compiled), no artifact

**Zephyr's change:** none named. The commit says the 1024-byte stack overflowed by about 40 B
in the OpenThread CoAP send path ("enlarge temp stack for OpenThread CoAP send"). It's in the
upgrade series, and the most likely cause is the v4.4 OpenThread/Mbed TLS 4.1/PSA change
(`migration-guide-4.4.rst:1448-1454`, `:1538-1552`). It's counted as an upgrade item: the
same code ran at v4.2 with 1024 bytes, and the fix is needed for the app to run at v4.4.

**Parts:** `subsys/cc_temperature/src/main.c:80`, `K_THREAD_DEFINE(cc_temperature_thread,
1024, …)` → `2048`.

**Scope:** in, because the file is compiled at both refs. The overflowing path
(`cc_bc_transport_openthread.c`, `send_coap_message`) isn't compiled on `native_sim`.

**Revealing artifact:** nothing before run time (a stack overflow on the nRF target).

### C5. `tf-psa-crypto` added to the manifest allowlist (`473a01b`): out of scope

- **What changed:** `west.yml` name-allowlist gains `tf-psa-crypto`.
- **Why:** `migration-guide-4.4.rst:1448-1451`: crypto moved from Mbed TLS to the new
  TF-PSA-Crypto west module. OpenThread needs it on the real board.
- **Scope:** out. The `native_sim` build uses neither Mbed TLS nor PSA crypto (no such symbol in
  either `.config`), and `west.yml` isn't parsed by the build.
- **Revealing artifact:** on an OpenThread build, a target configure or compile failure for the
  missing module; the v4.4 guide entry.

### C6. bridle v4.2.1 → v4.4.0 (`84c51d5`, `473a01b`): out of scope

- **What changed:** `west.yml` bridle `v4.2.1` → `v4.3.0` → `v4.4.0`, a dependency bump.
- **Why:** bridle tracks Zephyr releases. bridle v4.2.1's board files are rejected by v4.4.0
  (observed: "Malformed board YAML file … boards/cytron/maker_rp2040/board.yml", consistent with
  `migration-guide-4.4.rst:36-37`, `full_name` now required).
- **Scope:** out. The scanned build doesn't use bridle (see "Upgrade and scanned build").
- **Revealing artifact:** the target configure failure above, with bridle v4.2.1 in the build.

## Not upgrade items (with reasons)

| Commit(s) | Change | Why it's not an item |
|---|---|---|
| `84c51d5`, `473a01b` | `west.yml` zephyr `v4.2.0` → `v4.3.0` → `v4.4.0` | The bump itself |
| `84c51d5`, `473a01b` | `.devcontainer/devcontainer.json` `ZEPHYR_TAG` v0.27.4 → v0.29.2 | Dev container (docs/tooling) |
| `473a01b` | `.github/workflows/ci.yml` `sdk-version` 0.17.4 → 1.0.1 | CI configuration (forced by `migration-guide-4.4.rst:26`, SDK ≥ 1.0.0, but it isn't project code) |
| `904416f` | `.github/workflows/ci.yml` `--gcov-tool gcov` for twister | CI configuration (twister 4.4 crash) |
| `809002b` | the variant itself (`board.yml` variants, the `.dts`, the twister `.yaml`, the defconfig additions) | Not separate items: they are C1's fix. Using a variant board rather than moving the overlay into the app was the maintainers' choice, but it's the replacement for a mechanism v4.4 removed, so it's counted under C1, not as an unforced change |

## Counts (coffeecaller-native)

- **In scope: 4 items:** C1 (2 in-scope parts: C1a, C1b), C2 (1 in-scope part: C2a), C3, C4.
- **Out of scope: 2:** C5 (no crypto in this build), C6 (bridle not in this build).
- **Reachable ceiling:** C3 and C4 have no artifact in this build, so the best possible strict
  recall is 2/4.
