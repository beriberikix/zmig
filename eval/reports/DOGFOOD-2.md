# Dogfood 2: after Step 2, compared with the baseline

Date: 2026-10-28. Scoring follows [ORACLE-RULES.md](../ORACLE-RULES.md). The baseline is
[DOGFOOD-2-baseline.md](DOGFOOD-2-baseline.md) (zmig `0cb691a`). The oracles weren't edited
after the runs, and no corrections were needed. ZMK, Golioth (B) and bms-firmware (A1) are
reported separately.

Step 3 (P2, public API/ABI diff) isn't implemented yet. Its design is in
[DESIGN-P2.md](https://github.com/beriberikix/zmig/blob/c07a0bf/docs/DESIGN-P2.md), awaiting review. §6 will hold the after-P2 numbers.

---

## 1. What Step 2 changed

### The three ZMK-derived classes

1. **Init-order diff** (`zmig/analyzers/initorder.py`, `zmig/extract/init_dump.py`, new
   category `init`).
   - Each ref's own `scripts/build/check_init_priorities.py` reads the init calls from the
     ELF. It's run on `zephyr.exe` when a native runner exists, as Zephyr's own check does.
   - Device ordinals are mapped to devicetree paths with the same ref's `edt.pickle`.
   - Each init function's `file:line` comes from DWARF.
   - Project init calls that exist at both refs are reported when their level changed, or
     when calls swapped sides around them.

   What exists at each ref was checked, not assumed:

   | Ref | Reader available |
   |---|---|
   | ≤ v3.3 (including ZMK's v3.2 fork) | no script; reported as not checked |
   | v3.4 | script, but it reads object files and has no reader class; not checked |
   | v3.5 – v4.2 | `ZephyrInitLevels(path)`; device init functions are listed by name up to v4.1 and as `NULL` from v4.2, so devices are matched by node |
   | v4.3 – v4.4 | `ZephyrInitLevels(path, open_file)` |

2. **Kconfig dependency promotion** (`kconfig.py`, `dependency_chains`). A symbol in the
   bulk default-changed / newly-set / no-longer-set lists becomes **risky** when the app
   leans on it. Evidence is the chain from the app's line.
   - The app leans on a root `R` when `R` is assigned in a conf fragment this build
     applied, or defined/overridden in the project's own Kconfig **and** set at the source.
   - A changed symbol `X` is promoted if `R` depends on, selects or implies `X`, or names
     it in a default.
   - A two-hop chain (`R` → `A` → `X`) also counts, but only when the intermediate symbol
     `A` changed too.

3. **Warnings raised in Zephyr headers, attributed to the project's call site**
   (`diagnostics.py`, `buildlog.py`).
   - GCC's "in expansion of macro" notes, the "In function" line and the include chain are
     parsed. The first location in the project is reported as `file:line`.
   - One finding covers all call sites of the same warning. It's risky when a project site
     exists, info otherwise.

### Progress output

`Runner.run(progress=…)` prints a step when it starts, a heartbeat every 60 s while it runs,
and its duration when it ends. It's used for the mirror clone and fetch, `west update` of
modules, configure, each image's compile, and the sysbuild merge.

### Crash and misleading-output fixes, listed separately (the only changes aimed at B/A1 findings)

| # | Bug | Fix | Test |
|---|---|---|---|
| M1 | Diagnostics in the project's own modules (`ZEPHYR_EXTRA_MODULES`) showed absolute scratch paths and no line, and were info | modules outside zmig's workspace count as project code, shown as `<module>/<path>:<line>` (`project_roots`, `project_path`) | `test_net_mgmt_warnings_point_at_the_module_call_site` |
| M2 | A devicetree change to the STM32H5 USB node was blamed on `boards/rak5010_nrf52840.overlay`, a file this build doesn't use | breaking/risky findings whose only evidence is `.conf`/`.overlay`/`.dts`/`.dtsi`/`.keymap` files that no image of either build used (merged fragments plus every file in `zephyr.dts.d`) become info, titled "only in files this build doesn't use: …" | `test_findings_only_in_unused_input_files_become_info` |
| M3 | Breaking/risky severity on other boards' `.conf` files | same as M2. They stay in the report as info, so out-of-scope hints aren't lost | same |
| M4 | A guide entry matched the literal `uint32_t` (A1, breaking) | standard C type names are no longer guide literals | `test_c_standard_type_names_are_not_guide_literals` |
| M5 | "the build build failed" | "the build failed" | covered by the reports |
| M6 | (found by Step 2's first ZMK run) ELF and map looked up as `zephyr/zephyr.elf` regardless of `CONFIG_KERNEL_BIN_NAME`. ZMK's is `zephyr/zmk.elf`, so the size check and the init-order analyzer never saw it | `ImageContext.path()` follows `KERNEL_BIN_NAME` | `test_elf_named_after_kernel_bin_name` |
| M7 | (found by the test suite) the init-order reader crashed on native_sim's intermediate `zephyr.elf` and on v4.4's new constructor; the traceback, with temp paths, made reports non-deterministic | read `zephyr.exe` when present; call the constructor by signature; a reader failure becomes "not checked" with its last line | `test_deterministic`, `test_large_gap_v442` (build) |

The new class tests:

- `test_header_warning_keeps_the_macro_call_site`
- `test_dependency_chains_one_hop_or_through_a_changed_symbol`
- `test_init_order_swaps_and_device_identity`
- `test_long_steps_report_progress`

---

## 2. ZMK v3.2 → v3.5

Runs: `corne` and `niceview` as in DOGFOOD-1, with the same cached builds (the build recipe
is unchanged). The analyzers took 7.7 s per run.

### Recall (18 in-scope items)

Only item #2 changed its evidence:

| # | Item | Baseline | After Step 2 | Why |
|---|---|---|---|---|
| 2 | `SYS_INIT` functions take no arguments | partly (c) | **partly (d)** | `BUILD-NEW-WARNING-adbded30` is now **risky**, with 9 app call sites (`src/activity.c:98`, `usb.c:81`, `endpoints.c:366`, `battery.c:137`, `workqueue.c:27`, `usb_hid.c:198`, `hog.c:409`, `split/bluetooth/central.c:877`, `wpm.c:85`). That's 9 of the 14 in-scope parts. The other five can't warn. `behavior.c`, `ble.c`, `combo.c` and `sensors.c` stop at an earlier fatal missing header (`syscalls/behavior.h`, `hci_err.h`; GCC runs with `-Wfatal-errors`), and `include/zmk/hog.h` has no warning of its own |
| 7, 18 | init level / priority | partly (c), missed | unchanged | the init-order analyzer can't run: there's no reader at the v3.2 source, and the v3.5 target build fails before linking |
| 15, 16 | heap default / `PM_DEVICE` | missed / excluded | unchanged | the dependency promotion found no chain: neither symbol is reached from a root that's set in this build |

| ZMK | Caught | Partly | Missed | Strict | Lenient |
|---|---|---|---|---|---|
| Baseline | 6 | 7 | 5 | 33% | 72% |
| **After Step 2** | **6** | **7** | **5** | **33%** | **72%** |

### Precision

There are still 22 breaking/risky findings (corne ∪ niceview):

- `GUIDE-ENTRY-MATCH-9f30bf3e` (bq274xx units, `corneish_zen*.dts`) is now info, labelled
  outside this build. It was harmless.
- `BUILD-NEW-WARNING-adbded30` is now risky. It's confirmed: these are real call sites in
  compiled files.

| ZMK | Confirmed | Harmless (real elsewhere) | Harmless | Unverifiable | **Precision** | With other configs |
|---|---|---|---|---|---|---|
| Baseline | 11 | 6 | 4 | 1 | 50% | 77% |
| **After Step 2** | **12** | 6 | 3 | 1 | **55%** | 82% |

---

## 3. Golioth v4.1.0 → v4.2.0 (B)

Same command as the baseline. The analyzers took 6.9 s (11.6 s on the first run, which
included the init-level dumps); the builds are unchanged.

### Recall (2 in-scope items)

| Item | Baseline | After Step 2 | Finding |
|---|---|---|---|
| G1 `net_mgmt` event type 32 → 64 bit | partly (a)+(c) | **caught** | G1a: `BUILD-NEW-WARNING-fca02eb3` (**risky**) at `golioth-firmware-sdk/examples/zephyr/common/net_connect.c:36`, "assignment to 'net_mgmt_event_handler_t' {aka 'void (*)(…, long long unsigned int, …)'} from incompatible pointer type 'void (*)(…, uint32_t, …)'" (M1: the file is in the project's own module). G1b: `BUILD-NEW-WARNING-3ce9e193` (**risky**) at `…/net_connect.c:69`, "conversion from 'long long unsigned int' to 'uint32_t' changes value from '16154411863377969153' to '1'". It's raised in `net_event.h:344` and attributed through the `NET_EVENT_IPV4_ADDR_ADD` expansion note (class 3) |
| G2 `MCUMGR_TRANSPORT_NETBUF_USER_DATA_SIZE` default | missed | missed | no artifact in this build |

| B | Caught | Partly | Missed | Strict | Lenient |
|---|---|---|---|---|---|
| Baseline | 0 | 1 | 1 | 0% | 50% |
| **After Step 2** | **1** | **0** | **1** | **50%** | **50%** |

**Was the 32 → 64-bit change caught before P2?** Yes, after Step 2. It was caught by
`BUILD-NEW-WARNING-fca02eb3` (handler signature, `net_connect.c:36`) and
`BUILD-NEW-WARNING-3ce9e193` (event truncated to 1, `net_connect.c:69`), both risky. The
baseline zmig did not catch it: the same two warnings were info, with no line in the
project. The credit is split. G1a came from the M1 path fix. G1b came from the
header-attribution class. Neither came from P2.

### Precision

| # | Finding | Verdict |
|---|---|---|
| 1 | `BUILD-NEW-WARNING-fca02eb3` | **confirmed** (G1a) |
| 2 | `BUILD-NEW-WARNING-3ce9e193` | **confirmed** (G1b: `net_connect()` waits forever) |
| 3 | `SBOM-HEADER-CHANGED-24bcf578` (`log.h`) | harmless (userspace-only additions) |
| 4 | `SBOM-HEADER-CHANGED-e7c787b9` (`kernel.h`) | harmless (no API `main.c` uses changed) |
| 5 | `SBOM-MODULE-REPINNED-022f33fb` (mbedtls) | unverifiable |
| 6 | `SBOM-MODULE-REPINNED-85ed84b2` (hal_stm32) | unverifiable |

The five baseline findings on `rak5010_nrf52840.conf/.overlay` and `socs/native*.conf` are
now info, labelled "only in files this build doesn't use". They were harmless (M2/M3).

| B | Confirmed | Harmless | Unverifiable | **Precision** |
|---|---|---|---|---|
| Baseline | 0 | 7 | 2 | 0/9 = 0% |
| **After Step 2** | **2** | 2 | 2 | **2/6 = 33%** |

The out-of-scope catch G3 (the rak5010 rename) is still in the report, now as info.
Init-order: it ran at both refs on two project init calls (`golioth_boot_banner`,
`settings_autoload`, both APPLICATION), with no level change and no swaps, and no findings.
Kconfig promotion: no chain.

The field-upgrade verdict is unchanged: **direct-ok (medium)**.

---

## 4. bms-firmware v4.0 → v4.2 (A1)

Same command as the baseline. The analyzers took 4.3 s; the target build still fails on the
missing `cmsis_6` (B1a).

| Item | Baseline | After Step 2 |
|---|---|---|
| B1a `cmsis` → `cmsis_6` | partly (b) | partly (b) (unchanged: `cmsis_core.h` missing, module not named) |
| B2 ThingSet SDK bump | missed | missed |

| A1 | Strict | Lenient | Confirmed / breaking+risky | **Precision** |
|---|---|---|---|---|
| Baseline | 0% | 50% | 1/6 | 17% |
| **After Step 2** | **0%** | **50%** | **1/2** | **50%** |

Precision changes:

- The `uint32_t` guide false positive is gone (M4).
- The three native_sim deprecations are now info, outside this build (M3). They're still
  listed for native_sim.
- The remaining false positive is the guide entry "`NET_L2_OPENTHREAD` no longer implies
  `NVS`" (`77a28ac6`). It matches `CONFIG_NVS=y` in an applied conf, but the app doesn't use
  OpenThread.

Init-order: skipped (the target has no ELF). Verdict: **unknown (medium)**, storage
unknown, as at the baseline.

---

## 5. Side by side

| | ZMK base | ZMK step 2 | B base | B step 2 | A1 base | A1 step 2 |
|---|---|---|---|---|---|---|
| Strict recall | 33% | 33% | 0% | **50%** | 0% | 0% |
| Lenient recall | 72% | 72% | 50% | 50% | 50% | 50% |
| Precision (§3) | 50% | **55%** | 0% | **33%** | 17% | **50%** |
| Breaking + risky | 22 | 22 | 9 | 6 | 6 | 2 |

What each Step 2 part bought, measured:

- **Header attribution plus M1:** catches B's G1 (one item). It moves ZMK #2 from (c) to
  (d) with 9 attributed call sites, but #2 isn't fully caught: earlier fatal errors stop
  the other files.
- **Init-order diff:** no measured effect. ZMK, the project it was derived from, can't use
  it (no reader at v3.2, and no target ELF). It ran cleanly with no findings on Golioth
  and on smp_svr (app and MCUboot, v4.2.2 → v4.3.0).
- **Kconfig promotion:** no measured effect, and no false positives. On ZMK's first run it
  produced 4 false positives through a shield's Kconfig that the build doesn't enable;
  that was fixed by requiring the root to be set.
- **M2/M3/M4:** most of the precision gain on B and A1.

The held-out denominators are still 2 + 2 items, so these are directional, not estimates.
