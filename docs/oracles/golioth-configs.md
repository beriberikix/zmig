# Oracle: Golioth firmware SDK, Zephyr v4.1.0 → v4.2.0, additional configurations

Written 2026-10-28, **before zmig was run on these configurations** (only `--analyzers
none` verification builds, which produce no findings). The rules are in
[../ORACLE-RULES.md](../ORACLE-RULES.md). The items G1–G8 and the upgrade (bump commit
`58a652256fee`, Zephyr `v4.1.0` → `v4.2.0`) are defined in [golioth.md](golioth.md). This
file only re-scopes them for three more builds of the same pre-bump tree. It isn't edited
after the runs; corrections go at the end, dated.

## Scanned builds

All are `examples/zephyr/hello` from the pre-bump tree, with the SDK as module
`golioth-firmware-sdk` via `ZEPHYR_EXTRA_MODULES`. The project's `west-zephyr.yml` imports
every Zephyr module, so the module list is what each build needs.

| Config | Board / extra args | Modules | SDK |
|---|---|---|---|
| **B-rak** | `rak5010/nrf52840` | hal_nordic cmsis cmsis_6 mbedtls zcbor | 0.16.8 |
| **B-esp** | `nrf52840dk/nrf52840`, `-DSHIELD=golioth_esp_at` (`sample.yaml`'s own `extra_args`) | hal_nordic cmsis cmsis_6 mbedtls zcbor | 0.16.8 |
| **B-native** | `native_sim` | mbedtls zcbor | host gcc |

Scope evidence comes from the verification builds: source = pre tree at v4.1.0, target =
post tree at v4.2.0, all **ok**. It's taken from `compile_commands.json`, the merged conf
fragments and overlays in the configure log, and `zephyr/.config`.

| | B-rak | B-esp | B-native |
|---|---|---|---|
| `common/net_connect.c` compiled | yes | yes | **no** (offloaded sockets: `common/credentials_nsi.c` instead) |
| `common/wifi.c` compiled | no | **yes** | no |
| conf fragments merged | `prj.conf`, `boards/rak5010_nrf52840.conf` | `prj.conf`, `nrf52840dk_nrf52840.conf` | `prj.conf`, `socs/native.conf` |
| overlays | `boards/rak5010_nrf52840.overlay` | `golioth_esp_at.overlay`, `nrf52840dk_nrf52840.overlay` | none |
| `CONFIG_MODEM_CELLULAR` | y | n | n |
| `CONFIG_WIFI_ESP_AT` | n | y | n |
| `CONFIG_NET_NATIVE_OFFLOADED_SOCKETS` | n | n | **y** |
| `CONFIG_MCUMGR` | n | n | n |
| `external/zcbor` compiled | no (Zephyr's `modules/lib/zcbor`) | no | no |

## Scope per item

| Item | B-rak | B-esp | B-native |
|---|---|---|---|
| **G1** `net_mgmt` 32 → 64 bit | **in**: G1a, G1b (`net_connect.c:20-22/32/36/37/69`) | **in**: G1a, G1b, and **G1c** (`common/wifi.c:471-473, 497-499`, two handlers plus their `%x` log formats) | out: no `net_mgmt` handler is compiled |
| **G2** `MCUMGR_TRANSPORT_NETBUF_USER_DATA_SIZE` default (`common/Kconfig.defconfig:175-176`) | **in** (parsed file); condition false | **in** (parsed file); condition false | **in** (parsed file). `NET_NATIVE_OFFLOADED_SOCKETS=y`, but `MCUMGR` is off, so the default still isn't evaluated |
| **G3** `MODEM_CELLULAR_CMUX_MAX_FRAME_SIZE` → `MODEM_CMUX_WORK_BUFFER_SIZE` | **in**: part `hello/boards/rak5010_nrf52840.conf:27`, merged in this build (the other 8 examples' copies are out) | out | out |
| **G4** `fw_update` nrf52840dk counts | out (other example) | out (other example; `hello` doesn't merge `fw_update/boards/…`) | out |
| **G5** `MBEDTLS_PK_PARSE_EC_EXTENDED=n` in nRF91 confs | out | out | out |
| **G6** `nrf91_lte_monitor.c` | out (not compiled) | out | out |
| **G7** `external/zcbor` bump | out | out | out |
| **G8** `sample.yaml` board rename | out (test metadata) | out | out |

### Revealing artifacts for the newly in-scope parts

- **G3 (B-rak).** The target configure should abort on an assignment to an undefined
  symbol, `CONFIG_MODEM_CELLULAR_CMUX_MAX_FRAME_SIZE`, at `boards/rak5010_nrf52840.conf:27`.
  Kconfig's "undefined symbol" warning is fatal in Zephyr. Also `migration-guide-4.2.rst`
  (Modem), which names both replacements.
- **G1c (B-esp).** The target build should warn `-Wincompatible-pointer-types` where
  `ipv4_changed` and `wifi_mgmt_event_handler` are assigned to `net_mgmt_event_handler_t`
  (`net_mgmt_init_event_callback(...)` calls in `wifi.c`). The declaration diff of
  `net_mgmt_event_handler_t` shows it too. The `%x` log formats are a consequence, not a
  separate artifact.
- **G1a, G1b, G2:** as in [golioth.md](golioth.md).

## Counts

| Config | In scope | Out of scope |
|---|---|---|
| B-rak | **3** (G1 [2 of 4 parts], G2, G3 [1 of 10 parts]) | 5 |
| B-esp | **2** (G1 [3 of 4 parts], G2) | 6 |
| B-native | **1** (G2) | 7 |

As in the original oracle, G2 has no artifact in any of these builds, so the best possible
strict recall is 2/3, 1/2 and 0/1.
