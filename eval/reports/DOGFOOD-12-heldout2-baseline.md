# DOGFOOD-12: clean baseline on two more held-out projects

2026-10-30. zmig `c919498` (code as at `4e97c2e`). These are the first zmig runs on these
projects. Their oracles were committed and pushed before the run (`c919498`):
- [ble-copro](../oracles/ble-copro.md);
- [proves](../oracles/proves.md).

Only `--analyzers none` verification builds ran before that.

Scoring follows [ORACLE-RULES.md](../ORACLE-RULES.md), including §5.

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

## 5. Addendum (2026-10-30): E1 and E2 fixed (tuned)

**E1:** a completed configure now means `build.ninja`, not `zephyr/.config`
(`Artifact.BUILD_NINJA`).
- The kconfig analyzer requires it.
- A snapshot that hits a missing build artifact makes an analyzer `skipped`, with the
  reason, not `error`.
- The `changes` stand-ins and step 4's module gate treat a `.config` without `build.ninja`
  as a configure that didn't finish.

**E2:** at the target, an object-like macro whose whole expansion is one identifier is a
compatibility alias (`#define sockaddr_in net_sockaddr_in`).
- An old name, or each part of a field name, is compared with what it now names, and old
  type spellings go through the aliases.
- A name the project never wrote at the target is looked up in the headers
  (`#define sa_family_t net_sa_family_t`) before it's called removed.
- A name that was a macro at the source keeps the old comparison. The first version
  followed a macro alias and gave CANnectivity C1 and C2 a new risky "`K_THREAD_STACK_SIZEOF`
  changed", which the full re-scan caught before commit.

**Re-scan** (`eval/runs/e1e2b`, all 16 configurations; OpenDeck scan and plan), against the
baselines:
- **ble-copro:** 12 breaking/risky findings gone, and `API-FIELD-CHANGED-d638ba4d` risky →
  info. Those are all 13 of E2's harmless findings.
- **proves:**
  - **+1** `MANIFEST-MODULE-REQUIRED-491c6f62` (breaking/medium) at `west.yml:25`, the
    allowlist: `tf-psa-crypto`, from the mbedtls glue at the target (step 4). **True: P3.**
  - ble, kconfig, storage and wire are `skipped` instead of `error`.
- **Everything else, and OpenDeck:** no finding changed. ZMK's `BUILD-NEW-WARNING-adbded30` and
  CoffeeCaller's `BUILD-TARGET-BUILD-FAILED-acc08695` list their sites in a different order
  (E8 below). Their IDs, severities and first locations are unchanged.

| | ble-copro, tuned | proves, tuned | Second set (7), tuned |
|---|---|---|---|
| Strict | 0 | 1 → **2** (P3) | 1 → **2** |
| Lenient | 2 | 4 | 6 |
| Precision | 5/23 → **5/10** | 20/24 → **21/25** | 25/47 → **26/35 (74%)** |
| Actionable | 2 | 2 → **3** | 4 → **5** |
| Fix-actionable | 0 | 0 → **1** (P3, "Add tf-psa-crypto", as ardep A6) | 0 → **1** |

**E8 (found here, open): build-error sites follow the log's order.** Installing F´'s tools
into zmig's venv changed every build key, since the key includes the Python environment,
so every configuration was rebuilt. A parallel build prints errors and warnings in a
different order, so findings that list "the first few sites" list different ones. The
first location was the same this time, but a watch fingerprint takes the primary location,
so the sites should be ordered by file and line, not by log order.

## 6. Addendum (2026-10-30): E8 fixed

**What changed.**
- Build errors are ordered compiler first, then by file and line, instead of log order.
- A new warning's sites are ordered by file and line.
- The build-failure evidence lists distinct sites: the same missing-header error, printed
  once per file that includes the header, is one site.

**Re-scan** (`eval/runs/e8b`, all 16 configurations; OpenDeck scan and plan), against `e1e2b`:
- No ID, severity or set of findings changed.
- Site order changed in `BUILD-TARGET-BUILD-FAILED` (ble-copro, bms-A1-oled, CoffeeCaller,
  ZMK ×2) and `BUILD-NEW-WARNING-adbded30` (ZMK ×2).
- ZMK's build failure now lists three distinct sites (it had two). Before, five copies of
  `behavior.h:276` could fill the evidence.
- CoffeeCaller's lists the first five of its errors by file.

**Primary location changed once in one finding:** zmk-corne's
`BUILD-TARGET-BUILD-FAILED-f5607720`, `ext_power.h:104` → `behavior.h:276`. Its watch
fingerprint changes once.

**No score changes.** ZMK #2 and #5, and CoffeeCaller C1, are scored on other findings
(DOGFOOD-2/3; §10 of DOGFOOD-11).

## 7. Addendum (2026-10-30): E3 fixed (tuned)

The guide analyzer's gate demotes a match to info, saying why, in two more cases:
- **Vendor sections:** the entry sits under a section named for a SoC vendor (NXP, STM32,
  ESP32, Infineon, Raspberry Pi, Silabs, Nordic, …), and the source build's SoC
  (`CONFIG_SOC_FAMILY`, `_SERIES`, `CONFIG_SOC`) isn't that vendor's.
- **Struct members:** the item is a struct's member (`coap_resource.user_data`,
  `bt_conn_le_info.interval`), and the project never names that struct. The extractor had
  reduced these to the bare member name.

**Re-scan** (`eval/runs/e3`, all 16 configurations; OpenDeck scan and plan), against `e8b`:
- **Demoted to info, the E3 harmless findings:**
  - proves: `GUIDE-ENTRY-MATCH-3e75e69a` and `-bd6a5722` (NXP), `-8d41001f` (STM32),
    `-34879899` (CoAP);
  - ble-copro: `-664a2463` (CoAP), `-14ca8a29` and `-69d46ce5` (`bt_conn_le_info.interval`).
- Two findings that were already info (proves' `interval` matches in RadioLib) only
  changed wording.
- **Nothing else changed**, including on the four STM32 projects, where STM32 sections
  still apply.

| | ble-copro, tuned | proves, tuned | Second set (7), tuned |
|---|---|---|---|
| Precision | 5/10 → **5/7** | 21/25 → **21/21** | 26/35 → **26/28 (93%)** |

Recall, actionable and fix-actionable are unchanged.

## 8. Addendum (2026-10-30): E4 fixed (tuned)

**New breaking finding `include-missing` (buildlog, medium confidence).** When the target
build reports a symbol undeclared in a project file ("implicit declaration of function",
"undeclared", "type defaults to 'int' in declaration of", "unknown type name"), zmig
checks the **source** build's include chain for that file (ninja deps).
- If a header there declares the symbol, and the file doesn't include that header itself,
  the finding names the header and the symbols.
- It sits at the file's last `#include` line.
- Its `fix` is the `#include` to add.

**ble-copro** gains three findings, each at `src/stream_client.c:8`, the end of its include
block:

| Finding | Symbols | Fix | Item |
|---|---|---|---|
| `BUILD-INCLUDE-MISSING-ef59be30` | `connect`, `recv`, `send`, `socket` | `#include <zephyr/posix/sys/socket.h>` | B1 |
| `BUILD-INCLUDE-MISSING-a963db41` | `close` | `#include <zephyr/posix/unistd.h>` | B1 |
| `BUILD-INCLUDE-MISSING-20d4e9f6` | `LOG_DBG`, `LOG_ERR`, `LOG_INF`, `LOG_MODULE_REGISTER`, `LOG_WRN` | `#include <zephyr/logging/log.h>` | B2 |

- **All three are true, and they are the maintainers' three added lines.**
- **B1 and B2: partly (c) → caught, and fix-actionable.**
- **Judgement call on location:** the maintainers put the includes in the Zephyr group
  (`:1-5`); zmig points at the end of the same include block (`:8`), and an include
  placed there compiles the same.

**Re-scan** (`eval/runs/e4`, all 16 configurations; OpenDeck scan and plan): nothing else
changed.

| | ble-copro, tuned | Second set (7), tuned |
|---|---|---|
| Strict | 0 → **2** | 2 → **4 (57%)** |
| Lenient | 2 | 6 |
| Precision | 5/7 → **8/10** | 26/28 → **29/31 (94%)** |
| Actionable | 2 | 5 |
| Fix-actionable | 0 → **2** | 1 → **3** |
