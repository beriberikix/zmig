# Oracle: RIT Launch Initiative FSW, Zephyr v4.1.0 → v4.2.0

Written 2026-10-30, **before zmig was run on this project**. Only `--analyzers none`
verification builds were run, and they produce no findings. The rules are in
[../ORACLE-RULES.md](../ORACLE-RULES.md). This file isn't edited after the run;
corrections go at the end, dated.

This is a held-out project: no zmig change has been informed by it.

## Upgrade and scanned build

**Project.** RIT Launch Initiative FSW (https://github.com/RIT-Launch-Initiative/FSW,
AGPL-3.0), rocket flight software, mostly C++.

- The repository is itself a Zephyr module (`zephyr/module.yml`, name `fsw`, with
  `board_root`, `dts_root` and `snippet_root` set to `.`). Its own boards are in
  `boards/arm/`, its C++ framework in `lib/f_core/`, and apps in `app/`.
- Its `west.yml` imports upstream Zephyr by **release tag**, with a name-allowlist.
- At this bump the manifest has no `zscilib` project. `zscilib` (on `master`) was added
  later, in `36d8323` (2026-01-28), so it isn't part of this pair.

**Commits.** The upgrade is one squashed PR:

| Ref | Commit | Date | Zephyr pin |
|---|---|---|---|
| source tree (the bump's parent) | `f7a43b10cd76930bf7cb24e4ca7987d8c1d02546` | 2025-08-27 | `revision: v4.1.0` |
| the bump, "Update to Zephyr 4.2 (#333)" | `7aada9dfd3274b0ccc81622740a4915582d59e4f` | 2025-09-11 | `revision: v4.2.0` |

**Zephyr refs:**

- from `v4.1.0` = `7823374e872145b5bd018bfe447839eb36042611`;
- to `v4.2.0` = `413b789deb391d3a37d06b463288a5fe765ee57e`.

**Configuration S1** (this file):

- `app/backplane/sensor_module` on board `sensor_module` (STM32F446, Cortex-M4; the
  project's own board, `boards/arm/sensor_module/`). This is the CI's
  `backplane.sensor_module.default` test (`sample.yaml`, integration platform
  `sensor_module`) and the Makefile's `sensor-flight` target.
- `-DBOARD_ROOT=<FSW> -DZEPHYR_EXTRA_MODULES=<FSW>`. The project's CI builds inside a west
  workspace where FSW is the manifest repository, so it's a module there automatically.
- Modules `cmsis cmsis_6 hal_stm32 littlefs`: the subset of the allowlist this build uses,
  plus `cmsis_6` for the target.
- SDK 0.16.8. (0.17.4 fails at v4.1.0, in Zephyr's own `lib/libc/picolibc/locks.c`.)
- **App shim.** The app's `CMakeLists.txt` finds the repository by a relative path,
  `set(FSW_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../../..)`. zmig builds a copy of the app
  inside its workspace, where that path is wrong. So the app passed to zmig is a copy of
  `app/backplane/sensor_module` with that line wrapped in `if(NOT DEFINED FSW_ROOT)`, and
  `-DFSW_ROOT=<FSW>` is passed. No other file differs, and none of the bump's files is in
  the app directory. The evaluation harness applies the same change from
  `eval/patches/rit-fsw-fsw-root.patch` (`app_patch` in `eval/configs.toml`, configuration
  `rit-fsw-sensor`).
  - **This is a zmig limitation in itself.** An app that reaches outside its own directory
    by relative path can't be scanned without a change like this one.

**Scope evidence** comes from the verification builds:

- **Source (`v4.1.0`): ok.** Project files compiled:
  - app: `src/{c_detection_handler,c_sensing_tenant,c_sensor_module,main}.cpp`;
  - `lib/f_core/device/sensor/{c_accelerometer,c_barometer,c_gyroscope,c_magnetometer,c_shunt,c_temperature_sensor}.cpp`
    (every sensor file the bump touches);
  - `lib/f_core/net/application/{c_udp_alert_tenant,c_sntp_server_tenant,c_tftp_server_tenant}.cpp`;
  - `lib/f_core/device/{c_gpio,c_rtc}.cpp`, `lib/f_core/net/{device/c_rs485,network/c_ipv4,transport/c_udp_socket}.cpp`,
    `lib/f_core/os/*.cpp`, `lib/f_core/utils/n_time_utils.cpp`;
  - the project's drivers `drivers/sensor/adxl375/*.c` and `drivers/sensor/ms5611/ms5611.c`.
- **Target (`v4.2.0`): configure-failed** at Kconfig ("Aborting due to Kconfig
  warnings"). The devicetree was generated (`zephyr.dts`, `edt.pickle`), so it was parsed.
  Kconfig parsed and merged the same files as at the source. Nothing was compiled.
- **Board files:** `boards/arm/sensor_module/sensor_module.dts` (BOARD.dts) and
  `sensor_module_defconfig` (loaded first), at both refs.
- **Merged fragments, at both refs:**
  - `core.conf`;
  - the snippets `bpl-networking`, `cpp` and `lfs`, which the app adds itself through
    `AddCommonSnippets()`.
- **Overlay, at both refs:** `boards/sensor_module.overlay` (it sets the alias
  `thermometer = &tmp117`). The app-root `sensor_module.overlay` isn't used.
- **Config at the source:**
  - `CONFIG_TMP116=y`, `CONFIG_DT_HAS_TI_TMP116_ENABLED=y`,
    `CONFIG_DT_HAS_TI_TMP116_EEPROM_ENABLED=y`, `CONFIG_EEPROM` **not set**;
  - `CONFIG_CPP=y` (C++20), `CONFIG_LOG=y`, `CONFIG_LOG_MODE_DEFERRED=y`;
  - `CONFIG_ZEPHYR_CMSIS_MODULE=y`;
  - `CONFIG_POSIX_MAX_FDS=4` (the deprecated symbol's default; this build doesn't set it).
- **Modules:** `cmsis` headers (`core_cm4.h`) are in the source build's ninja deps. Both
  `cmsis` and `cmsis_6` are in the target's `zephyr_modules.txt`.
- **TMP116 sources:** `drivers/sensor/ti/tmp116/tmp116.c` and `tmp116.h` are compiled at
  the source.

## Items

The bump commit changes 11 files. The 10 commits before it and the 8 after it, up to the
v4.3 bump `1b7d13c`, were checked. There are five upgrade items: four come from the bump
and one is a follow-up. The rest are classified at the end.

### F1. TMP116 driver renamed to TMP11X (bump `7aada9d`): in scope

**Zephyr's change:**

- `migration-guide-4.2.rst:351-352` (Sensors): `ti,tmp116` was renamed to `ti,tmp11x`.
- `:168-169` (EEPROM): `ti,tmp116-eeprom` was renamed to `ti,tmp11x-eeprom`.
- The driver moved from `drivers/sensor/ti/tmp116` to `drivers/sensor/ti/tmp11x`. The
  Kconfig symbol `TMP116` became `TMP11X` (`drivers/sensor/ti/tmp11x/Kconfig:6`,
  `depends on DT_HAS_TI_TMP11X_ENABLED`), and `EEPROM_TMP116` became `EEPROM_TMP11X`.
- The Kconfig rename isn't stated in the migration guide. No `TMP116` symbol remains at
  `v4.2.0`.

**Parts:**

| Part | Where (`f7a43b1`) | What changed | Scope |
|---|---|---|---|
| F1a | `boards/arm/sensor_module/sensor_module.dts:202` | node `tmp117@48`: `compatible = "ti,tmp116"` → `"ti,tmp11x"` (and the comment is dropped) | in: BOARD.dts, parsed at both refs; the node is enabled and the app's `thermometer` alias points at it (`c_sensing_tenant.cpp:20` takes `DEVICE_DT_GET(DT_ALIAS(thermometer))`) |
| F1b | `boards/arm/sensor_module/sensor_module.dts:208` | child `ti_tmp116_eeprom@0`: `"ti,tmp116-eeprom"` → `"ti,tmp11x-eeprom"` | in by the parsed-file rule (`DT_HAS_TI_TMP116_EEPROM_ENABLED=y`), but `CONFIG_EEPROM` isn't set, so no driver binds it in this build |
| F1c | `boards/arm/sensor_module/sensor_module_defconfig:33` | `CONFIG_TMP116=y` → `CONFIG_TMP11X=y` | in: loaded at both refs |

**Revealing artifacts**, expected from the code (not checked before the run):

- **F1c:** the Kconfig tree at the target has no `TMP116` symbol. The assignment at
  `sensor_module_defconfig:33` is to an undefined symbol, which Zephyr's Kconfig treats as
  fatal. The verification build's target configure did fail at Kconfig; which warnings
  caused it wasn't read.
- **F1a:** the target's binding set has no `ti,tmp116`. The generated devicetree has the
  node with no binding, and `DT_HAS_TI_TMP11X_ENABLED` isn't set. With F1c fixed alone,
  `TMP11X` would be unmet and no driver would instantiate `tmp117@48`. That gives an
  undefined `__device_dts_ord_*` at link time from `c_sensing_tenant.cpp:20`. Also
  available: the migration guide `:351-352`.
- **F1b:** the generated devicetree, the same way (no `ti,tmp116-eeprom` binding at the
  target). Also available: the migration guide `:168-169`. No build failure follows in this
  build, because EEPROM is off.

### F2. `cmsis` → `cmsis_6` in the manifest (bump `7aada9d`): in scope

- **What changed:** `west.yml:18`, name-allowlist entry `cmsis` → `cmsis_6`.
- **Zephyr's change:** "Cortex-M boards/socs now require the `CMSIS_6` module"
  (`migration-guide-4.2.rst:795-804`).
- **Scope:** in, because the build uses `cmsis` at the source (`core_cm4.h` in ninja
  deps) and needs `cmsis_6` at the target (STM32F446, Cortex-M4).
- **Revealing artifact:** the target `west.yml` declares `cmsis_6`, and the migration-guide
  entry exists. A target build without `cmsis_6` would fail on a missing CMSIS header or a
  Kconfig `&& 0` dependency, the forms seen on other projects. The scanned build passes
  `cmsis_6`, so it doesn't show this failure.

### F3. C++ log arguments of enum type cast to `int` (bump `7aada9d`): in scope

**Zephyr's change** (no migration-guide entry): commit `1ac19d3e981` "sys: cbprintf:
logging: Fix opaque struct pointer warning" is in `v4.2.0` and not in `v4.1.0`.

- `include/zephyr/logging/log_msg.h`, `Z_LOG_LOCAL_ARG_CREATE`: each log argument's local
  copy was `Z_AUTO_TYPE x = (arg) + 0`. It is now `= Z_ARGIFY(arg)`, which is
  `((0) ? (arg) : (arg))` (`include/zephyr/sys/cbprintf_internal.h:89`).
- At `v4.1.0`, `+ 0` promoted an unscoped enum to `int`. At `v4.2.0`, the local keeps the
  enum type.

In C++ the static log package is built by the templates in
`include/zephyr/sys/cbprintf_cxx.h`:

- an enum type matches the generic `z_cbprintf_cxx_store_arg(uint8_t *, T)`;
- that copies `MAX(sizeof(T), sizeof(int)) / sizeof(int)` words from `&arg`.

On `arm-zephyr-eabi` enums are short by default. `sizeof(enum sensor_channel)` is 2 (its
largest value is `SENSOR_CHAN_MAX = INT16_MAX`). `NAlerts::AlertType` is
`enum : uint16_t`, also 2 bytes. A 4-byte word is therefore read from a 2-byte object:

- the logged `%d` or `%c` value has two undefined upper bytes;
- GCC can flag the over-read. A standalone reproduction of the two templates with the
  SDK's g++ 12.2 at `-Os -Wall` gives `-Warray-bounds` ("array subscript 'int[0]' is
  partly outside array bounds of 'AlertType [1]'").

So the upgrade did force the casts. `-Wformat` isn't the cause: the same probe gives no
format warning for unscoped enums passed to `%d`/`%c`.

**Parts**, all compiled at the source (`CONFIG_CPP=y`, `CONFIG_LOG=y`):

| Part | Where (`f7a43b1`) | What changed |
|---|---|---|
| F3a | `lib/f_core/device/sensor/c_accelerometer.cpp:26` | `LOG_ERR("… (%d) …", chan)` → `static_cast<int>(chan)` |
| F3b | `lib/f_core/device/sensor/c_barometer.cpp:41` | same |
| F3c | `lib/f_core/device/sensor/c_gyroscope.cpp:26` | same |
| F3d | `lib/f_core/device/sensor/c_magnetometer.cpp:26` | same |
| F3e | `lib/f_core/device/sensor/c_shunt.cpp:50` | same |
| F3f | `lib/f_core/device/sensor/c_temperature_sensor.cpp:18` | same |
| F3g | `lib/f_core/net/application/c_udp_alert_tenant.cpp:24` | `LOG_INF("Received alert type %c", alertType)` → `"%d", static_cast<int>(alertType)`. The specifier change goes with the cast. |

**Scope:** in. All seven files are in `compile_commands.json` at the source. None is
compiled at the target, because the target configure fails (F1c).

**Revealing artifacts**, expected from the code (not checked before the run):

- the header diff of `log_msg.h` (`Z_LOG_LOCAL_ARG_CREATE`) and `cbprintf_internal.h`
  (`Z_ARGIFY`, new);
- if the target compiled, possibly a `-Warray-bounds` warning inlined from
  `cbprintf_cxx.h` and attributed to each `LOG_*` line above. That's uncertain, since it
  depends on inlining in the real macros;
- otherwise run time only: wrong values in the error and info logs.

In this scanned build the target doesn't compile, so only the header diff is available.

### F4. `CONFIG_POSIX_MAX_FDS` removed from the radio module (bump `7aada9d`): out of scope

- **What changed:** `boards/arm/radio_module/radio_module_defconfig:22-24` drops
  `CONFIG_POSIX_MAX_FDS=10` and its two comment lines.
- **Zephyr's change:** `POSIX_MAX_FDS` was deprecated at `v4.1.0`
  (`lib/posix/options/Kconfig.deprecated:103`, "Please use CONFIG_POSIX_OPEN_MAX
  instead"). It was removed by `7623a18c2e7` "posix: remove deprecated kconfig options",
  which is in `v4.2.0`. There's no migration-guide entry.
- **Scope:** out. `radio_module_defconfig` belongs to board `radio_module` and isn't loaded
  in S1. The symbol is evaluated in S1 at the source (`CONFIG_POSIX_MAX_FDS=4`, its
  default), but nothing in this build sets it, so the change doesn't touch it.
  ORACLE-RULES §1.2 names "another board" as a typical reason for out of scope. Reading the
  "symbol this build evaluates" bullet as covering any board's assignment of that symbol
  would put every other board's defconfig edit in scope.
- **Revealing artifact** (for a `radio_module` build): the Kconfig tree at the target has
  no `POSIX_MAX_FDS`, so the assignment is to an undefined symbol and the configure
  aborts.

### F5. Out-of-tree board `grim_reefer` ported to HWMv2 (follow-up `c51160e`, 2025-10-06): out of scope

- **Zephyr's change:** "HWMv1 support has been removed, any out-of-tree boards or SoCs in
  HWMv1 format must be migrated to HWMv2" (`migration-guide-4.2.rst:26-27`). `v4.1.0`
  still handles HWMv1 boards (`cmake/modules/boards.cmake:144-243`); `v4.2.0` doesn't.
- **What changed** in `boards/arm/grim_reefer/`:
  - `Kconfig.board` → `Kconfig.grim_reefer` (`depends on` → `select SOC_STM32F446XX`);
  - new `board.yml`;
  - `config BOARD` default removed from `Kconfig.defconfig`;
  - SoC lines removed from `grim_reefer_defconfig`;
  - `grim_reefer.dts` gets an `led0` alias;
  - the retired app's `CMakeLists.txt` stops setting `BOARD`/`BOARD_ROOT`.
- **Attribution:** the commit title says the reason ("update to v2 hw model"), and it lands
  four weeks after the bump. Only the HWMv1 → HWMv2 parts are the item. The `defconfig`
  clean-up and the alias are incidental.
- **Scope:** out. Board `grim_reefer` isn't built in S1, and its app is under
  `app/payload/.retired/`.
- **Revealing artifact** (for a `grim_reefer` build): at the target the board isn't
  found; `list_boards` no longer discovers HWMv1 boards. There's also the migration-guide
  entry.
- **Not part of the item:** the other HWMv1 board in the tree, `boards/arm/potato`, wasn't
  ported, so it isn't an item.

## Not upgrade items (with reasons)

| Commit / file | Change | Why it's not an item |
|---|---|---|
| `7aada9d` `west.yml:15` | `revision: v4.1.0` → `v4.2.0` | The bump itself |
| `7aada9d` `sensor_module_defconfig:37` | newline added at end of file | Formatting |
| `7aada9d` `c_udp_alert_tenant.cpp:24, :26` | removes the "%d if alerts go past 8 bits" comment; `"NOtifying"` → `"Notifying"` | Comment and a typo |
| `9834af7` (2025-10-02) `lib/f_core/device/c_rtc.cpp` | drops `#ifdef CONFIG_RTC_STM32` around `tm_year = 100`, sets `tm_mday = 1`, logs the time | BLTPlane board bring-up. The STM32 RTC driver's `set_time` checks are the same at both refs (`drivers/rtc/rtc_ll_stm32.c` commits in range only rework locking and backup-domain access), so the upgrade isn't shown to cause it. `c_rtc.cpp` is compiled in S1, so if zmig flags it, it's scored as precision (§3) |
| `9834af7` other files, `ea8cb41` | new boards `blt_plane`, `sw_board` (HWMv2); power-module log tweak | Features |
| `f43e5db` | Callback and Runnable tenants refactor | Project refactor |
| `0e53c91` | Socket Service support (`NET_SOCKET_SERVICE` handlers, UDP socket rework) | Feature. No migration-guide entry for socket services in 4.2 |
| `697e2b3` | `CLora` configuration functions | Feature |
| `6dd1780`, `0bb8800` | dev containers, Makefile, README; Grim Reefer docs and `.ioc` files | Docs and tooling |
| `f7a43b1`, `278de2b`, `329bd20`, `3b66e58`, `475fe26`, `a9682e6`, `220ec81`, `9a620e6`, `8363976`, `0a32351`/`abe86af` (before the bump) | flight-data downloader, CI fixes, scoped CI, CODEOWNERS, sample template, Control Freak and OTIS app/board work | Features, CI and tools, made while on v4.1.0 |
| `8b02feb` (earlier bump, v4.0.0 → v4.1.0) | `c_lora.cpp` `lora_recv_async(…, nullptr)`; `west.yml` | Another upgrade pair. Nothing from it recurs in `7aada9d`, and `c_lora.cpp` isn't touched in this range |

## Counts (S1)

- **In scope: 3 items:** F1 (3 parts), F2, F3 (7 parts).
- **Out of scope: 2 items:** F4 (board `radio_module`), F5 (board `grim_reefer`).
- **Reachable ceiling:**
  - The target build stops at Kconfig, so it has no compiler output.
  - F1 and F2 have build-time artifacts here: the Kconfig tree, the devicetree, the
    manifest, the migration guide.
  - F3's only artifact in this build is the Zephyr header diff. It has no migration-guide
    entry, and its compiler warning, if any, needs a target compile. So it's caught only if
    a tool reasons from the `Z_LOG_LOCAL_ARG_CREATE` change to the project's enum-typed log
    arguments.
