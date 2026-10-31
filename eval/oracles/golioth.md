# Oracle: Golioth firmware SDK, Zephyr v4.1.0 → v4.2.0

Written 2026-10-28, before zmig was run on this project. The rules are in
[../ORACLE-RULES.md](../ORACLE-RULES.md). This file isn't edited after the run; corrections
go at the end, dated.

## Upgrade and scanned build

- **Project:** golioth/golioth-firmware-sdk. It's a Zephyr module, with a `zephyr/module.yml`
  that has no `name:`. Zephyr therefore names it after its directory, and the examples expect
  `golioth-firmware-sdk`. Its `west-zephyr.yml` imports upstream Zephyr by tag, with
  `import: true`.
- **Bump commit:** `58a652256fee` ("bump Zephyr to 4.2.0 and NCS to 3.1.0", 2025-09-15).
  - Source tree: its parent. Zephyr `v4.1.0` (`7823374e8721`).
  - Target ref: Zephyr `v4.2.0` (`413b789deb39`).
  - The next commits (`1fc16f4`, `12591ea`) don't relate to Zephyr: a Python `utcnow()`
    fix, and removing a duplicate line from a `prj.conf` that this build doesn't use.
- **Scanned build:** `examples/zephyr/hello` on `nucleo_h563zi/stm32h563xx` (STM32H5,
  Ethernet), no extra conf or overlay files, no sysbuild.
  - Modules: `hal_stm32 cmsis cmsis_6 mbedtls zcbor`, plus the SDK itself via
    `ZEPHYR_EXTRA_MODULES`. Its submodules `external/libcoap` and `external/zcbor` are
    checked out.
  - SDK: 0.16.8.
  - zmig is given the **pre-bump** tree. The project's manifest imports every Zephyr module,
    so the module list doesn't stand in for any item below.
- **How scope was established:** from the verification builds at both refs
  (`compile_commands.json`, `zephyr/.config`, the parsed Kconfig tree). Nothing came from
  zmig findings; the builds ran with `--analyzers none`.
  - **Compiled from the project:** `examples/zephyr/common/{net_connect,credentials_settings,settings_autoload,settings_shell}.c`,
    `src/zcbor_utils.c`, the SDK's `src/`, and `hello/src/main.c`.
  - **Not compiled:** `common/wifi.c`, `common/nrf91_lte_monitor.c`, and anything under
    `external/`. The build compiles Zephyr's own `modules/lib/zcbor` instead.
  - **Parsed Kconfig:** `hello/Kconfig` sources `common/Kconfig.defconfig`, and the
    module's `port/zephyr/Kconfig` sources `common/Kconfig`.
  - **Config:** `CONFIG_NET_MGMT_EVENT=y`, `CONFIG_GOLIOTH_SAMPLE_DHCP_BIND=y`.
    `CONFIG_MCUMGR` and `CONFIG_NET_NATIVE_OFFLOADED_SOCKETS` are not set.

## Items

### G1. Network-management event type widened from 32 to 64 bits: in scope

Zephyr 4.2 changed `net_mgmt_event_handler_t`'s event argument and
`struct net_mgmt_event_callback.event_mask` from `uint32_t` to `uint64_t`. It also moved the
layer/code bits above bit 32: `NET_MGMT_LAYER_CODE_MASK` is now `GENMASK64(58, 52)`, which was
`0x07FF0000`.

**What the maintainers changed:**
- Handler signatures switched to a new `net_event_t`.
- A new Kconfig symbol, `GOLIOTH_SAMPLE_NET_EVENT_SIZE`, picks 32 bits for NCS and 64 bits
  for Zephyr. The symbol is how they implemented the fix, not a separate part.

**Parts:**

| Part | Where | In scope | Reason |
|---|---|---|---|
| G1a | `examples/zephyr/common/net_connect.c:20-22`: `event_cb_handler(…, uint32_t mgmt_event, …)`, assigned to `cb.handler` at `:36` | **yes** | compiled |
| G1b | `examples/zephyr/common/net_connect.c:32`: `wait_for_net_event(…, uint32_t event)`, called at `:69` with `NET_EVENT_IPV4_ADDR_ADD` and stored in `cb.event_mask` at `:37` | **yes** | compiled |
| G1c | `examples/zephyr/common/wifi.c:471-473, 497-499` (two handlers, plus `%x` log formats) | no | `wifi.c` isn't compiled; Wi-Fi isn't enabled in this build |
| G1d | `examples/zephyr/location/src/main.c:105-107`, plus `location/Kconfig` | no | different example |

**Revealing artifacts:**

- **G1a:** the target build should warn that `event_cb_handler` is assigned from an
  incompatible pointer type at `net_connect.c:36` (`-Wincompatible-pointer-types`, a warning
  under GCC 12 / SDK 0.16.8).
  - The declaration diff shows the same thing: `include/zephyr/net/net_mgmt.h`,
    `net_mgmt_event_handler_t`, v4.1.0 line 135 vs v4.2.0 line 153.
  - So does `migration-guide-4.2.rst:624-630`.
- **G1b:** a silent hang at run time.
  - `NET_EVENT_IPV4_ADDR_ADD` now has bits above 32. Passing it as `uint32_t` drops the layer
    bits, so `event_mask` never equals the delivered event, and `net_connect()` waits on
    `K_FOREVER` without returning.
  - At build time this should show as GCC's default `-Woverflow` "conversion … changes value"
    warning at `net_connect.c:69`. (Expected from the code; not checked before the run.)
  - It also shows in the declaration diff of `NET_MGMT_LAYER_CODE_MASK` and
    `struct net_mgmt_event_callback`.

### G2. `MCUMGR_TRANSPORT_NETBUF_USER_DATA_SIZE` default 110 → 112 for offloaded sockets: in scope (by the parsed-file rule)

- **What changed:** `examples/zephyr/common/Kconfig.defconfig:175-176`,
  `configdefault … default 112 if NET_NATIVE_OFFLOADED_SOCKETS`.
- **Scope:** in scope under ORACLE-RULES §1.2, because `common/Kconfig.defconfig` is parsed
  in this build. But neither `MCUMGR` nor `NET_NATIVE_OFFLOADED_SOCKETS` is set, so the
  default isn't evaluated, and the change has no effect on this build.
- **Revealing artifact:** none in this build. A native_sim build with MCUmgr over UDP would
  show it, as a `range`/size check failure or a run-time buffer overrun. The reason for the
  2 extra bytes isn't stated in the commit.

### Out of scope

| # | Change | Reason |
|---|---|---|
| G3 | `MODEM_CELLULAR_CMUX_MAX_FRAME_SIZE=160` → `MODEM_CMUX_WORK_BUFFER_SIZE=1024` in `boards/rak5010_nrf52840.conf` of 9 examples (incl. `hello/`) and `tests/hil` | only applies to rak5010 |
| G4 | `NET_PKT_TX_COUNT` 10 → 12, `NET_BUF_TX_COUNT` 20 → 24 in `fw_update/boards/nrf52840dk_nrf52840.conf` | other example, other board |
| G5 | `CONFIG_MBEDTLS_PK_PARSE_EC_EXTENDED=n` added to `socs/nrf9160_ns.conf` / `nrf9151_ns.conf` (incl. `hello/`) and `hello_nrf91_offloaded/prj.conf` | nRF91 / NCS only |
| G6 | `lte_lc_factory_reset()` → `nrf_modem_at_printf("AT%XFACTORYRESET=0")` and `#include <nrf_modem_at.h>` in `common/nrf91_lte_monitor.c` | NCS API; file not compiled |
| G7 | `external/zcbor` submodule `d3093b5` → `9b07780` | Zephyr builds compile Zephyr's `modules/lib/zcbor`, not the submodule (checked in `compile_commands.json` and `build.ninja` at both refs) |
| G8 | `esp32_devkitc_wrover/esp32/procpu` → `esp32_devkitc/esp32/procpu` in `sample.yaml` of every example | ESP32 board rename; test metadata for another board |

Not items: `west-zephyr.yml` / `west-ncs.yml` / `.ci-*.yml` revisions (the bump itself),
README and CI workflow edits.

## Counts

- **In scope: 2 items** (G1, with 2 of its 4 parts in scope; G2).
- **Out of scope: 6 items** (G3–G8).

The in-scope oracle is small. One item (G2) has no artifact in this build, so the best
possible strict recall is 1 of 2, unless a tool flags a configdefault on a symbol that isn't
evaluated.
