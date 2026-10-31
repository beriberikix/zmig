# Dogfood 2 baseline: ZMK re-scored, and two held-out projects

Date: 2026-10-28. Scoring follows [ORACLE-RULES.md](../ORACLE-RULES.md) (committed `0328e44`).
The held-out oracles were committed before zmig ran on them (`a90dc3a`):
[oracles/golioth.md](../oracles/golioth.md) and [oracles/bms-firmware.md](../oracles/bms-firmware.md).

This is the **baseline**. It was measured with zmig as of DOGFOOD-1 (`ad1a390`; the commits
after it only add docs). No analyzer was changed for these projects. Results are reported
separately for each project.

---

## 1. ZMK v3.2 → v3.5, re-scored under the written rules

**Source:** DOGFOOD-1's final runs (`sdk16-corne`, `sdk16-niceview`: zmig `ad1a390`, SDK
0.16.8). These are the same reports DOGFOOD-1 scored. Only the scoring rules changed.

**Scope:** the union of the two builds (§1.2). The changed files' scope was re-checked
against both builds' `compile_commands.json`. For example, `split/bluetooth/peripheral.c`,
`kscan_gpio_demux.c` and `il0323.c` aren't compiled, while `kscan_gpio_matrix.c` and
`behavior_{hold_tap,sticky_key,tap_dance,ext_power}.c` are.

| # | Commit | Change (in-scope parts) | DOGFOOD-1 | **Rules** | Finding, and why |
|---|---|---|---|---|---|
| 1 | `f4fce9e1` | `LV_Z_DPI` → `LV_DPI_DEF`, `SSD1306_REVERSE_MODE` → DT `inversion-on` (corne, nice_view) | caught | **caught** | `ORPHANED-c7ef6f3a` (cites corne `:34` and nice_view `:9`), `ORPHANED-06da6565` (corne `:24`), guide `b3668791` (breaking) at `corne.dtsi:68` |
| 2 | `bf4008da` | `SYS_INIT` functions take no arguments (`src/*.c`) | partly | **partly (c)** | `NEW-WARNING-adbded30` is info, and is attributed to `zephyr/include/zephyr/init.h`, not to the app's call sites |
| 3 | `0b5afbf9` | `hci_err.h` → `hci_types.h` (`src/ble.c`; `peripheral.c` isn't compiled) | caught | **caught** | `TARGET-BUILD-FAILED-f5607720`: `src/ble.c:22`, no such file `hci_err.h` |
| 4 | `bd9c71ab` | `MAX17048` Kconfig name collision (the DT binding part is out of scope: no node uses it) | caught | **caught** | `KCONFIG-NAME-COLLISION-7c2cfcbc` at `module/drivers/sensor/max17048/Kconfig:6` |
| 5 | `d06e90e7` | `zephyr_syscall_header()` in `CMakeLists.txt` | partly | **partly (b)** | `TARGET-BUILD-FAILED-f5607720` names the missing `syscalls/behavior.h` / `ext_power.h`, but not the cause |
| 6 | `ba1a6c08` | `void main` → `int main` | missed | **missed** | no finding |
| 7 | `d6de8a3a` | behaviors: `APPLICATION` → `POST_KERNEL` | partly | **partly (c)** | guide `39073cd9` (risky) has the right cause. Its evidence points at module drivers (`il0323.c`, `gpio_595.c`, …), not `src/behaviors/` |
| 8 | `b6d9f3c9` | `setting_name[15]` → `[17]` | missed | **missed** | no finding |
| 9 | `98e3b8b4` | `pm_state_force()` → `sys_poweroff()` (`activity.c`, plus `select POWEROFF`) | caught | **caught** | guide `9754923b` (breaking) at `src/activity.c:73` |
| 10 | `f4e6d704` | `CONTAINER_OF` with delayable work (`kscan_gpio_matrix.c`, `behavior_hold_tap/sticky_key/tap_dance.c`) | missed | **partly (c)** | guides `5af82dd3`, `e6c9b721`, `f4027fe3` (all info) state the cause. Their evidence is `kscan_gpio_demux.c`, `kscan_gpio_charlieplex.c` and headers, none of which are in-scope parts |
| 11 | `413820fc` | nice!view: `text[9]` → `[10]`, arc 359 → 360, `LV_Z_MEM_POOL_SIZE` default | partly | **partly (d)** | `NEW-WARNING-f768b8ba` (risky) at `status.c:185` catches the `text` part only |
| 12 | `8e9b4fa5` | `select NRF_STORE_REBOOT_TYPE_GPREGRET` in `app/Kconfig` | caught | **partly (c)** | guide `f6e35e8e` (breaking) names the right Kconfig fix, but its evidence is `src/behaviors/behavior_reset.c:34`, not `app/Kconfig` (§2's location rule) |
| 13 | `a6c03994` | drop the `BT_LL_SW_LLCP_IMPL` override | caught | **caught** | `ORPHANED-4440a398` at `Kconfig:175` |
| 14 | `b2a0a357` | LVGL memory: `LV_Z_MEM_POOL_MIN/MAX_SIZE` → `LV_Z_MEM_POOL_SIZE` | caught | **caught** | `ORPHANED-c4fb7621`, `-8baa1c47` at `src/display/Kconfig:32/35` |
| 15 | `4bcecd98` | drop `HEAP_MEM_POOL_SIZE default 8192 if ZMK_DISPLAY` from `app/Kconfig` (the corneish_zen and `il0323.c` parts are out of scope) | caught ("same as #14") | **missed** | nothing points at `app/Kconfig`'s heap default, or says that LVGL no longer uses the system heap |
| 16 | `1d83f279` | `PM_DEVICE` default back on | partly | **excluded** | see below |
| 17 | `50a303b8` | re-implement device suspension (`activity.c`, linker file) | partly | **partly (b)** | guide `9754923b` is at `activity.c`, but its cause (the API was removed) isn't this item's cause (`sys_poweroff` doesn't suspend devices) |
| 18 | `0f49fa9a` | `ext_power` init priority | missed | **missed** | no finding about priority within a level |
| 19 | `104c73d3` | enum conversion warning in `src/endpoints.c` | missed | **missed** | the warning doesn't occur in this configuration |

**#16 is excluded as not an upgrade item.** `98e3b8b4`, earlier in the same series, removed
`config PM_DEVICE default y` from `app/Kconfig`, and `1d83f279` puts it back. Against the
pre-bump tree (`b8846cf6:app/Kconfig:394` already has it), the net change is zero.
`PM_DEVICE` stays set at v3.5 with the pre-bump app; neither bulk Kconfig list has it.
DOGFOOD-1 counted it as "partly", via `CONFIG_PM` in the bulk list.

### Totals

| ZMK (18 in-scope items) | Caught | Partly | Missed | **Strict recall** | **Lenient recall** |
|---|---|---|---|---|---|
| DOGFOOD-1 as reported (19 items, its own judgement) | 8 | 6 | 5 | 42% | 74% |
| **Under ORACLE-RULES** | **6** | **7** | **5** | **33%** | **72%** |
| Under ORACLE-RULES, with #16 kept and scored as missed | 6 | 7 | 6 | 32% | 68% |

What changed from DOGFOOD-1:

- **#12** drops from caught to partly. Under the written rules, the finding has to point at
  the file the maintainers changed.
- **#15** drops from caught to missed. DOGFOOD-1 had credited it via #14's findings, which
  are about different symbols.
- **#10** rises from missed to partly. Info guide matches state the cause, and DOGFOOD-1 had
  overlooked them.

Partly-caught items by reason: (b) ×2, (c) ×4, (d) ×1, (a) ×0.

**Out-of-scope catches** (not in recall): `802881b6` PINMUX (`READ-SYMBOL-REMOVED-3f9ca014`),
`d9c0ded4` `rand32.h` (guide `14a743b0`, `peripheral_status.c` isn't compiled in the
central-half build), and `2d6c9f79` `spi_is_ready` (guide `213c7c7b`, `il0323.c`).

**Precision under §3.** DOGFOOD-1 §3 counted a finding as confirmed when its claim was true
anywhere in ZMK. ORACLE-RULES §3 also requires that it *affects this build*, so re-checking
it against both builds' compile commands changes the result. 22 breaking/risky findings
(corne ∪ niceview):

| Verdict (§3) | Count | Findings |
|---|---|---|
| Confirmed | **11** | `NAME-COLLISION-7c2cfcbc`; 5× `OVERRIDE-ORPHANED`; `TARGET-BUILD-FAILED-f5607720`; guide `b3668791` (`corne.dtsi`), `f6e35e8e` (`behavior_reset.c`, compiled), `9754923b` (`activity.c`); niceview `NEW-WARNING-f768b8ba` |
| Harmless: a real hazard, but in files this build doesn't compile | 6 | `READ-SYMBOL-REMOVED-3f9ca014` (PINMUX, `boards/arm/mikoto` …); guides `14a743b0` (`peripheral_status.c`), `213c7c7b` (`il0323.c`), `39073cd9` and `04e4cb9c` (init levels in drivers and pinmux files not compiled here), `fca177c5` (`gpio_max7318.c`) |
| Harmless: claim false, or the code is unused | 4 | `9f30bf3e`, `106b2d8e`, `3f730cc9`, `2c28c79f` (as in DOGFOOD-1) |
| Unverifiable | 1 | `a7776724` (picolibc heap) |

**ZMK precision: 11 of 22 (50%).** If real hazards in other configurations count, it is
17 of 22 (77%), as DOGFOOD-1 reported. That second figure is given only for comparison
with DOGFOOD-1. The rule was written before the held-out runs and isn't changed here.

---

## 2. Held-out runs: setup

Both runs used zmig at `a90dc3a`. The analyzer code is the same as `ad1a390`, with all
analyzers on and SDK 0.16.8. zmig is given the **pre-bump** project tree, as a user about to
upgrade would. The exact configuration is frozen in the oracle files.

```
# Golioth (B)
zmig scan --app <golioth-firmware-sdk>/examples/zephyr/hello --board nucleo_h563zi/stm32h563xx \
  --from v4.1.0 --to v4.2.0 --sdk <sdk-0.16.8> \
  --module hal_stm32 --module cmsis --module cmsis_6 --module mbedtls --module zcbor \
  --cmake-arg=-DZEPHYR_EXTRA_MODULES=<golioth-firmware-sdk>

# bms-firmware (A1)
zmig scan --app <bms-firmware>/app --board bms_16s100_sc \
  --from 4ebecae78ad9 --to 636ad05950bb --sdk <sdk-0.16.8> \
  --module cmsis --module hal_stm32 --module zcbor \
  "--cmake-arg=-DZEPHYR_EXTRA_MODULES=<bms-firmware>;<thingset-zephyr-sdk>;<thingset-node-c>"
```

| | Golioth (B) | bms-firmware (A1) |
|---|---|---|
| Source build | ok | ok |
| Target build | ok | **build-failed**: 1 compiler error, `zephyr/include/zephyr/arch/arm/asm_inline_gcc.h:24: cmsis_core.h: No such file or directory` (the pre-bump manifest has `cmsis`, not `cmsis_6`; that's oracle item B1a) |
| Analyzers not run | none | `sbom` skipped: the target build failed |
| Wall clock (this run) | **92 s**: both builds from scratch; workspaces and modules already cloned | **25 s**: the source build was reused from the verification run (same build key), and the target build stopped at its first compile error |
| Wall clock (first ever, cold) | 32 min (source) / 93 min (target), from the verification runs: mostly first-time clones of `hal_stm32` and the Zephyr mirror, three builds in parallel | 31 min / 55 min, same conditions |
| Breaking / risky / info | 3 / 6 / 12 | 3 / 3 / 9 |
| Field-upgrade verdict | **direct-ok (medium)**: layout, storage, wire, images, crossimage clean; MCUboot, BLE, sysbuild not applicable | **unknown (medium)**: storage unknown ("no enabled storage backend's compiled code references a partition" at the source; no SBOM or devicetree at the target, which failed to build) |

---

## 3. Golioth (B): recall

| Item | In-scope parts | Label | Finding, and why |
|---|---|---|---|
| G1 `net_mgmt` event type 32 → 64 bit | G1a handler signature (`net_connect.c:20-22/36`); G1b `uint32_t event` truncation (`:32/37/69`) | **partly** | G1a **(a)**: `BUILD-NEW-WARNING-d8fbee54` (info) gives the right cause, `assignment to 'net_mgmt_event_handler_t' … from incompatible pointer type`, in `net_connect.c`, but without a line, and as info. G1b **(c)**: `BUILD-NEW-WARNING-3ce9e193` (info) gives the cause, `conversion from 'long long unsigned int' to 'uint32_t' changes value from '16154411863377969153' to '1'`, but it's attributed to `<zephyr>/include/zephyr/net/net_event.h`, not to the call at `net_connect.c:69`. The guide entry MG-4.2-0123 (`migration-guide-4.2.rst:624`) did not match: `net_connect.c` sits in the module's `examples/zephyr/common/`, outside the `--app` directory that the guide and Kconfig analyzers scan. |
| G2 `MCUMGR_TRANSPORT_NETBUF_USER_DATA_SIZE` default | `common/Kconfig.defconfig:175-176` | **missed** | no finding. As the oracle said, nothing in this build reveals it (the condition is false) |

**B: strict 0 of 2 (0%), lenient 1 of 2 (50%).** Partly: (a)+(c) ×1.

**Was the 32 → 64-bit change caught before P2?** No, only partly. Both symptoms are in the
report, but only as info-level `BUILD-NEW-WARNING` findings (`d8fbee54`, `3ce9e193`), with
no app line. The second one is a **run-time hang**: `NET_EVENT_IPV4_ADDR_ADD` truncates to
`1`, so `net_connect()` waits on `K_FOREVER` and never returns. zmig ranks it as info, below
nine breaking/risky findings about files this build doesn't use.

**Out-of-scope catches:** G3 (rak5010 `MODEM_CELLULAR_CMUX_MAX_FRAME_SIZE`):
`KCONFIG-SYMBOL-REMOVED-f4c32f9b` and guide `f0ba680d`, both at
`boards/rak5010_nrf52840.conf:27` with the replacement named. G4–G8: none.

## 4. Golioth (B): precision

| # | Finding | Verdict | Why |
|---|---|---|---|
| 1 | `KCONFIG-SYMBOL-REMOVED-f4c32f9b` (breaking) | harmless (other board) | true, but `rak5010_nrf52840.conf` isn't in this build (it's G3) |
| 2 | guide `f0ba680d` (breaking) | harmless (other board) | same as #1 |
| 3 | guide `7613c10e` (breaking), `ETH_NATIVE_POSIX` renamed | harmless (other board) | `socs/native.conf` is native_sim only, and the symbol is deprecated, not removed |
| 4 | `KCONFIG-SYMBOL-DEPRECATED-ba6df930` (risky) | harmless (other board) | same as #3 |
| 5 | `DEVICETREE-NODE-CHANGED-d62a0c73` (risky), `&zephyr_udc0` clocks / maximum-speed | harmless | it's the STM32H5 USB node, which this app doesn't use; the evidence is a line in `boards/rak5010_nrf52840.overlay`, which isn't in this build (see M2) |
| 6 | `SBOM-HEADER-CHANGED-24bcf578` (risky), `log.h` | harmless | the additions are userspace-only (`CONFIG_USERSPACE && CONFIG_LOG_ALWAYS_RUNTIME`) |
| 7 | `SBOM-HEADER-CHANGED-e7c787b9` (risky), `kernel.h` | harmless | none of the APIs `main.c` uses (`k_sleep`, `k_sem_*`, `K_SEM_DEFINE`, `K_SECONDS`, `K_FOREVER`) changed |
| 8 | `SBOM-MODULE-REPINNED-022f33fb` (risky), mbedtls | unverifiable | 31 compiled files changed; whether DTLS behaviour changes needs a run |
| 9 | `SBOM-MODULE-REPINNED-85ed84b2` (risky), hal_stm32 | unverifiable | `stm32h5xx_hal_eth.c` changed, and this build uses Ethernet; needs a run |

**B precision: 0 of 9 (0%)**: 7 harmless (2 are real hazards on another board: the
rak5010 rename), 2 unverifiable. The two real hazards in this build (G1) are both info.

---

## 5. bms-firmware (A1): recall

| Item | In-scope parts | Label | Finding, and why |
|---|---|---|---|
| B1 module allowlist | B1a `cmsis` → `cmsis_6` | **partly (b)** | `BUILD-TARGET-BUILD-FAILED-04286652` (breaking) shows the symptom, `cmsis_core.h: No such file or directory` in a Zephyr header, but doesn't name the module. The guide entry that does (`migration-guide-4.2.rst:795`, "Cortex-M boards/socs now require the CMSIS_6 module") didn't match, because there's no app text to match it against |
| B2 ThingSet SDK bump | the module | **missed** | no finding. As the oracle said, nothing in this build reveals it. The target build also stopped at the CMSIS error, before any ThingSet file compiled |

**A1: strict 0 of 2 (0%), lenient 1 of 2 (50%).** Partly: (b) ×1.

**Out-of-scope catches:**

- B4 (`NATIVE_UART_0_ON_STDINOUT`): `KCONFIG-SYMBOL-DEPRECATED-9b70af95`, at
  `boards/native_sim.conf:8`.
- B5 (`UART_NATIVE_POSIX_PORT_1_ENABLE`): `KCONFIG-SYMBOL-DEPRECATED-016803fb` and guide
  `df7ff923`, at `:11`. The DT replacement (`&uart1`) isn't named.
- B3, B6, B7: none. B7 is `CFB_DISPLAY_HEIGH` in `oled.c`, which isn't compiled in this
  build.

## 6. bms-firmware (A1): precision

| # | Finding | Verdict | Why |
|---|---|---|---|
| 1 | `BUILD-TARGET-BUILD-FAILED-04286652` (breaking) | **confirmed** (in the oracle, B1a) | the build really fails without `cmsis_6` |
| 2 | guide `77a28ac6` (breaking), "`NET_L2_OPENTHREAD` no longer implies `NVS`" | harmless | it matched `CONFIG_NVS=y` lines. The app sets `NVS` itself and doesn't use OpenThread, so the change can't affect it |
| 3 | guide `582003b3` (breaking, low), "management event type changed from `uint32_t` to `uint64_t`" | harmless | it matched the literal `uint32_t` in `bms_chg_error(uint32_t error_flags)`, a GPIO callback, and a counter. bms-firmware has no `net_mgmt` code (see M4) |
| 4 | `KCONFIG-SYMBOL-DEPRECATED-9b70af95` (risky) | harmless (other board) | real for native_sim (B4), not in this build |
| 5 | `KCONFIG-SYMBOL-DEPRECATED-016803fb` (risky) | harmless (other board) | real for native_sim (B5), not in this build |
| 6 | guide `df7ff923` (risky) | harmless (other board) | same as #5 |

**A1 precision: 1 of 6 (17%)**: 5 harmless (3 are real hazards on native_sim).

---

## 7. Side by side

| | ZMK (18 in scope) | Golioth B (2 in scope) | bms-firmware A1 (2 in scope) |
|---|---|---|---|
| Strict recall | 6/18 = **33%** | 0/2 = **0%** | 0/2 = **0%** |
| Lenient recall | 13/18 = **72%** | 1/2 = **50%** | 1/2 = **50%** |
| Precision (§3) | 11/22 = **50%** | 0/9 = **0%** | 1/6 = **17%** |
| Precision, counting real hazards on other configurations | 17/22 = 77% | 2/9 = 22% | 4/6 = 67% |
| Out-of-scope catches | 3 | 1 of 6 | 2 of 5 |
| Target build | failed (salvaged) | ok | failed (`cmsis_6`) |
| Verdict | (not scored in DOGFOOD-1) | direct-ok, medium | unknown, medium |

The held-out denominators are tiny (2 + 2). In each pair, one of the two items has no
artifact in the scanned build. These numbers say more about where zmig looks than they
estimate recall.

## 8. Known limitations that affected these results

1. **Code outside `--app` isn't scanned as project code.** Golioth's shared example code
   (`examples/zephyr/common/`) and bms-firmware's drivers/lib are passed in as
   `ZEPHYR_EXTRA_MODULES`. The guide matcher and Kconfig reference scan only the `--app`
   directory, so G1 had no guide match. Compiler diagnostics in those files are reported
   with absolute scratch paths and no line (`d8fbee54`).
2. **Warnings raised inside Zephyr headers aren't attributed to the call site** (G1b,
   `3ce9e193` → `net_event.h`). DOGFOOD-1 already noted this; it's a Step 2 class.
3. **Breaking/risky findings on files outside the scanned build.** Board, soc and native_sim
   `.conf`/overlay files in the app tree are scanned regardless of the build. That made 8
   of the 15 held-out breaking/risky findings, and it caught 3 out-of-scope items. The
   severity doesn't say the file isn't in this build.
4. **Module set changes between refs aren't detected.** zmig builds the target with the
   source's module list, so a module newly required at the target (`cmsis_6`) shows up as a
   missing header in a Zephyr file, with no cause.
5. **A failed target build hides everything after it.** A1's target build stopped at the
   first header, so no ThingSet or app code was compiled at v4.2, SBOM was skipped, and the
   storage verdict became unknown.
6. **The field-upgrade verdict doesn't cover functional regressions.** B is direct-ok
   while G1b hangs `net_connect()` at boot. This is by design (P1 covers persistent state),
   but the report doesn't say so next to the verdict.

## 9. Crashes and misleading-output bugs (the only fixes allowed on B/A1 from here)

No crashes: both runs exited 0, with no tracebacks in stderr.

Misleading output:

- **M1.** Diagnostics in extra-module files are reported with absolute scratch paths and no
  line (`d8fbee54`: `"file": "/tmp/…/golioth-firmware-sdk/examples/zephyr/common/net_connect.c"`),
  and warnings inside Zephyr headers are reported against the header (`3ce9e193`). The
  finding doesn't say where in the project the problem is.
- **M2.** `DEVICETREE-NODE-CHANGED-d62a0c73` attributes a change to the STM32H5 USB node to
  `boards/rak5010_nrf52840.overlay:9`, a file that isn't part of this build (it references
  the same node label on a different board).
- **M3.** Breaking/risky severity on files that aren't part of the scanned build, with no
  "not in this build" marker (limitation 3).
- **M4.** The guide matcher matched MG-4.2-0123 on the literal type name `uint32_t`, and
  rated it breaking (A1 `582003b3`).
- **M5.** The analyzer-skipped message says "the build build failed".

These will be fixed only where they're general bugs, not tuned to these projects. Each fix
gets a regression test, and is reported separately in DOGFOOD-2.md.
