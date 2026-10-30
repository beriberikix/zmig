# DOGFOOD-12: clean baseline on two more held-out projects

2026-10-30. zmig `c919498` (code as at `4e97c2e`). These are the first zmig runs on these
projects. Their oracles were committed and pushed before the run (`c919498`):
- [ble-copro](oracles/ble-copro.md);
- [proves](oracles/proves.md).

Only `--analyzers none` verification builds ran before that.

Scoring follows [ORACLE-RULES.md](ORACLE-RULES.md), including §5.

**These results are clean.** No zmig change was informed by either project. This is the
first clean measurement since the change database (DOGFOOD-11) and the A3, C1 and A2 work,
which were all tuned on the earlier held-out set.

Run: `eval/run.py scan --out ~/.cache/zmig/eval/runs/heldout2-baseline ble-copro proves`
(206 s and 120 s).

## 1. Results

| Project | Upgrade | In scope | Strict | Lenient | Precision | Actionable | Fix-actionable | Target build | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| ble-copro (nrf52840dk) | v4.3.0 → v4.4.2 | 2 | 0 | 2 (100%) | 5/23 (22%) | 2 | 0 | build failed (`stream_client.c`) | needs-changes |
| proves (`proves_flight_control_board_v5e`) | v4.3.0 → v4.4.1 | 5 | 1 (20%) | 4 (80%) | 20/24 (83%) | 2 | 0 | configure failed (Mbed TLS module, after Kconfig) | unknown |
| **Total** | | **7** | **1 (14%)** | **6 (86%)** | **25/47 (53%)** | **4** | **0** | | |

**Compared with the earlier held-out set's clean baseline** (DOGFOOD-8: strict 0/13,
lenient 6/13, precision 10/15, actionable 0):
- recall and actionable are higher;
- precision is lower, mostly from one defect (§3, E2).

## 2. Per item

### ble-copro (2 in scope)

| Item | Label | Finding, and why | Actionable | Fix-actionable |
|---|---|---|---|---|
| B1 POSIX headers no longer included by `net/socket.h` (`stream_client.c:4-5`) | **partly (c)** | `BUILD-TARGET-BUILD-FAILED-04dd1e65` (breaking) quotes "implicit declaration of function `socket`/`connect`/`close`/`send`/`recv`", which names the missing symbols, but at the call sites (`:188` …), not the include block the maintainers edited. `GUIDE-ENTRY-MATCH-3c126192` and `-5ad0c6c4` (breaking/medium) sit at the include lines `:3` and `:4`, but quote only the first sentence of the Networking entry ("… is namespaced"). The sentence that states the cause ("include relevant POSIX header files like `sys/socket.h` … as Zephyr networking header files will no longer include those") is a separate match, `-f1b795f8`, at `examples/server.c`: info, and correctly so | yes (`stream_client.c`) | no |
| B2 `net_core.h` no longer includes `log.h` (`stream_client.c:1`) | **partly (c)** | The same build failure, at `:10` (`LOG_MODULE_REGISTER`, "type defaults to 'int'") and the `LOG_*` call sites, plus `BUILD-NEW-WARNING-2376cb7f` at `:10`. The cause is right (the symbols are undeclared), but no finding is at the include block, and none names `log.h` | yes | no |

**Precision 5/23.**
- **Confirmed (5):**
  - the build failure (B1, B2);
  - the two Networking guide matches at `:3` and `:4` (B1; the namespacing is true and is
    why the includes are needed);
  - the new warning at `:10` (B2);
  - `KCONFIG-SYMBOL-DEPRECATED-76dbe10c`, `USB_DEVICE_STACK` still deprecated: true, and
    enabled in this build. Not in the oracle.
- **Harmless (18):**
  - **13 API findings about the network namespacing:**
    - `API-FIELD-REMOVED-14a323fe`, `-62cf43c2`, `-cadc86b8`, `-ff266ef2`
      (`sockaddr_in.sin_*`, `net_addr.in_addr`);
    - `API-FUNCTION-CHANGED-2f9b88de`, `-ac677689`, `-d4acffa5`, `-fa715125`
      (`net_if_ipv4_*`: `struct in_addr *` → `struct net_in_addr *`);
    - `API-TYPEDEF-REMOVED-af2798b4` (`sa_family_t`);
    - `API-ENUM-KIND-CHANGED-7985a677`, `-dda7bb0f` (`IPPROTO_TCP`, `SOCK_STREAM`);
    - `API-FIELD-CHANGED-14132866`, `-d638ba4d`.

    At v4.4.2, `CONFIG_NET_NAMESPACE_COMPAT_MODE=y` and `include/zephyr/net/net_compat.h`
    defines the old names as the new ones (`#define sockaddr_in net_sockaddr_in`, `in_addr`,
    `sa_family_t`, `SOCK_STREAM`, `IPPROTO_TCP`). The fields keep their names
    (`net_ip.h:187-191`). None of the 11 target compile errors is on these lines, and the
    maintainers didn't change them. (§3, E2.)
  - `BLE-BLE-STRUCT-CHANGED-8ef8e2de` (`struct bt_keys`): the two new fields `cfg_version` and
    `cfg_flags` come before `storage_start`, and only the bytes from `storage_start` on are
    persisted (`BT_KEYS_STORAGE_LEN`). Stored bonds keep their layout. (E5.)
  - `GUIDE-ENTRY-MATCH-25bfb3b1` (Bluetooth host no longer selects `POLL`): networking still
    selects it, and `CONFIG_POLL=y` at the target (oracle).
  - `GUIDE-ENTRY-MATCH-664a2463` (CoAP `.well-known/core` metadata): the app has no CoAP; it
    matched generic identifiers. (E3.)
  - `GUIDE-ENTRY-MATCH-14ca8a29` and `-69d46ce5` (`bt_conn_le_info.interval` deprecated) at
    `ble_observer.c:101`: that line is `.interval` in a `bt_le_scan_param` initializer, not
    `bt_conn_le_info`. (E3.)

### proves (5 in scope)

| Item | Label | Finding, and why | Actionable | Fix-actionable |
|---|---|---|---|---|
| P1 tca9548a channel `compatible` (P1a) and `label` (P1b) lines | **partly (d)** | **P1a is caught:** `DEVICETREE-COMPATIBLE-UNBOUND-ab02f96b` (breaking) at the eight `compatible` lines (`dtsi:211, :248, …`), "No node matches compatible ti_tca9548a_channel through a binding" (step 3, DOGFOOD-11). **P1b is partly (b):** 16 `DEVICETREE-NODE-CHANGED` findings (risky), 8 at the channel nodes and 8 at `Main.cpp`'s `&mux_channel_N` references, each saying "changed: (binding)". That's the right nodes, but it doesn't say `label` is no longer declared | yes (`…v5.dtsi`) | no: "renamed or its binding removed; check the migration guide", where the maintainers deleted the lines |
| P2 `FIXED_PARTITION_ID` → `PARTITION_ID` (`Main.cpp:62`) | **caught** | `GUIDE-ENTRY-MATCH-92671beb` (risky/low) at `Main.cpp:62`, quoting the flash-map table (`FIXED_PARTITION_ID` → `PARTITION_ID`) | yes | no: "read MG-4.4-0155 and update the lines listed". The table's rename isn't parsed into a fix |
| P3 `tf-psa-crypto` in the manifest | **partly (b)** | `BUILD-TARGET-CONFIGURE-FAILED-38835789` quotes the Mbed TLS module's "add_subdirectory given source …/mbedtls/tf-psa-crypto which is not an existing directory". That names the module, but not that it must be added to the manifest. It has no project location. No `module-required` finding (E1) | no | no |
| P4 module re-pins (P4a–c, e, f) | **partly (a), (d)** | Info `MANIFEST-MODULE-REPINNED` for `hal_rpi_pico`, `hal_st`, `mbedtls` and `mcuboot` (plus `IMAGES-MCUBOOT-REVISION`). Each names the module and both revisions, at info. Nothing for `cmsis_6` (P4a): Zephyr's two manifests may pin it the same, where the project's pin differed | no | no |
| P5 fprime-zephyr `LoRaBandwidth` values | missed | The `lora_signal_bandwidth` values changed, but the project reaches them only through a cast, so the API diff has no use of the enumerators. Run time only (oracle) | | |

**Precision 20/24.**
- **Confirmed (20):**
  - the configure failure (P3);
  - `compatible-unbound` (P1a);
  - the 16 `NODE-CHANGED` binding findings (P1: the channels do lose their binding);
  - the two flash-map guide matches: `-92671beb` at `:62`, and `-15554f60` at `:17`, the
    same entry at the `flash_map.h` include.
- **Harmless (4), all guide matches for another vendor's or subsystem's change:**
  - `-3e75e69a` and `-bd6a5722` (NXP `zephyr,flash` / FlexSPI) at the RP2350 board's
    `dtsi:65`;
  - `-8d41001f` (STM32 SPI mode) at `dtsi:137`;
  - `-34879899` (CoAP) at `RtcManager.cpp:292`, matched on generic identifiers.

  (E3.)

## 3. zmig defects this run exposed (not fixed here)

Each one cost recall or precision above. Fixing any of them is informed by these projects,
so later numbers on them are **tuned**.

| # | Defect | Effect here |
|---|---|---|
| E1 | **A target configure that fails after Kconfig but before `build.ninja` exists.** The kconfig, ble, storage and wire analyzers report `error` ("build.ninja missing"). The `changes` stand-in and step 4's tree needs stay out, because a `.config` exists. | P3 had no `module-required` (the mbedtls glue's `tf-psa-crypto` path is exactly step 4's signal). No Kconfig comparison ran at all, and no stand-in replaced it |
| E2 | **The API diff doesn't follow compatibility macros.** An old name `#define`d to a renamed type (`sockaddr_in` → `net_sockaddr_in`, `in_addr`, `sa_family_t`, `SOCK_STREAM`) is reported as removed or changed, though the code compiles unchanged | 13 harmless breaking/risky findings (ble-copro) |
| E3 | **Guide matches outside the build's hardware or subsystems.** NXP and STM32 entries match an RP2350 board's DTS; the CoAP entry matches generic identifiers; `bt_conn_le_info.interval` matches another struct's `.interval` | 6 harmless findings (4 proves, 2 ble-copro) |
| E4 | **A compile error for a symbol a header stopped providing isn't tied to that header.** The source build's include chain (ninja deps) shows `stream_client.c` got `sys/socket.h`, `unistd.h` and `log.h` only through `net/socket.h` and `net_core.h`. A finding at the include block naming the missing include would catch B1 and B2, and make them fix-actionable | B1, B2 partly (c) |
| E5 | **BLE persisted-struct check ignores which part is persisted.** `bt_keys` fields added before `storage_start` don't change stored bonds | 1 harmless breaking finding |
| E6 | **Module re-pins are info and don't name the user's manifest pin.** proves pins module revisions in its own `west.yml`; zmig compares Zephyr's manifests only | P4 partly (a) |
| E7 | **A table-form guide rename isn't parsed into a fix** (`FIXED_PARTITION_ID` → `PARTITION_ID`) | P2 not fix-actionable |

## 4. What this means

- **The change database's bindings check works on a project it wasn't built for:** P1a is
  caught at all eight lines.
- **The rest of Phase 1 didn't get a chance on proves (E1).** The target reached Kconfig, so
  the stand-ins stayed out, and then failed before the analyzers that need a build
  directory. That's a state the earlier held-out set never hit: ardep stopped before
  Kconfig, RIT stopped at Kconfig.
- **Precision is the weak spot.** 18 of 22 harmless findings come from two defects (E2 and
  E3) that the earlier, tuned set didn't exercise. Zephyr 4.4's network namespacing is new,
  and neither earlier project used sockets.
- **Fix-actionable is 0 on clean data again.** E4 and E7 are the closest.
