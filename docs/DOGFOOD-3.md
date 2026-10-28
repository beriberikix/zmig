# Dogfood 3: P2 (public API diff), compared with the DOGFOOD-3 baseline

Date: 2026-10-28. zmig `615cbc1` (P2 steps 1–5, plus the M8–M12 output fixes). The
baseline is [DOGFOOD-3-baseline.md](DOGFOOD-3-baseline.md) (zmig `9c8da4a` code). Scoring
follows [ORACLE-RULES.md](ORACLE-RULES.md). The oracles weren't edited and no corrections
were needed. libclang 18.1.1 (the `libclang` wheel); `pahole` 1.31 and `abidiff` 2.9.0 were
installed for this run.

Every configuration was run with the same command as in the baseline (`base3.sh`). The
column "fixes only" is a run on `70d6f11` (M8–M12, before P2), so the effect of P2 can be
told apart from the output fixes.

---

## 1. What P2 added (as built; DESIGN-P2.md §10 decisions)

| Step | What it reports | Commit |
|---|---|---|
| 1 | The project's own `__syscall` functions the source build generated and the target doesn't, at each declaration, with the target's registration mechanism (`zephyr_syscall_header()`, `APPLICATION_DEFINED_SYSCALL`) | `3318d9a` |
| 2 | Functions/typedefs the project references: removed, became a macro (info), changed (const/volatile-only: compatible), newly deprecated. Callbacks whose expected type changed (clang incompatible-pointer diagnostics new at the target) | `e960590` |
| 3 | Macros at instantiation sites: removed, arity, value/type (evaluated by clang in the using TU); width change plus truncation at a project line = breaking. Enum constants and struct fields | `700548e` |
| 4 | Devicetree devices the code takes that the target defines no device for (node path, status at both refs, chosen property) | `eded5ce` |
| 5 | `pahole` cross-check of clang's struct layouts against the GCC-built ELFs; `abidiff` on binary boundaries (prebuilt static libraries, LLEXT). Missing tools are reported as not checked | `eded5ce` |
| fix | A use spelled inside a Zephyr macro body isn't the project's (found on ZMK: `LOG_DBG` "used" `Z_LOG_MSG2_MODE_*`) | `615cbc1` |

How P2 parses:

- **Call sites** come from the source build's project TUs. Each TU is parsed again with the
  target build's flags for the same file, so P2 works even when the target build fails
  (ZMK: 24 TUs parsed at the target, despite GCC failing).
- **Parse gaps** are reported as not checked, and the `api` status is `partial`. That
  includes missing headers re-parsed with an empty stand-in, TUs with errors, and TUs with
  no target compile command.

**Cost.** P2 parses every project TU twice, plus once more for macro values; the results
are cached per build.

| Configuration | Scan time, first run (both builds cached) | Before P2 |
|---|---|---|
| Golioth B | 141 s | 8 s |
| ZMK corne | 117 s | 8 s |

---

## 2. Recall (in-scope items only)

| Config | Items | Baseline strict / lenient | **P2 strict / lenient** | What P2 adds to the items |
|---|---|---|---|---|
| ZMK (corne ∪ niceview) | 18 | 6 / 13 (33% / 72%) | **6 / 13 (33% / 72%)** | #2 `SYS_INIT`: `API-CALLBACK-TYPE-CHANGED-c363f00f` names the new type, `int (*)(void)`, at all 10 call sites that can show it, `ble.c:778` included (GCC never compiled it). The other 4 in-scope parts can't show it in this build: `combo.c` declares `combo_init()` with empty parentheses (compatible), `behavior.c`'s `SYS_INIT` is under `CONFIG_LOG` (off), `sensors.c`'s under `ZMK_KEYMAP_HAS_SENSORS` (no sensors), and `hog.h` has no call. So #2 stays partly (d). #5 syscall headers: `API-SYSCALLS-NOT-GENERATED-47f7b545/-ca8024d7` give the exact cause and remedy (`zephyr_syscall_header(...)` and `CONFIG_APPLICATION_DEFINED_SYSCALL=y`), but they point at the `__syscall` lines in `behavior.h`/`ext_power.h`, not at `CMakeLists.txt`/`prj.conf`, which is what the maintainers edited. Under §2's location rule that's partly (c) (was (b)) |
| Golioth B | 2 | 1 / 1 (50% / 50%) | **1 / 1** | G1 now has its cause: `API-TYPEDEF-CHANGED-6363121e` (`net_mgmt_event_handler_t`: `unsigned int` → `unsigned long long`, `net_mgmt.h:135`/`:153`, at `net_connect.c:36`) and `API-MACRO-CHANGED-ecad1e6d` (`NET_EVENT_IPV4_ADDR_ADD`: `unsigned long` 4 bytes `= 3758358529` → `unsigned long long` 8 bytes `= 16154411863377969153`, truncated at `net_connect.c:69`, breaking) |
| Golioth B-rak | 3 | 2 / 2 | **2 / 2** | same G1 causes |
| Golioth B-esp | 2 | 1 / 1 | **1 / 1** | G1c causes at `wifi.c`: `net_mgmt_init_event_callback` (`wifi.c:563/568`), the typedef (`:564/:569`), and the width of `NET_EVENT_WIFI_CONNECT_RESULT`, `…_DISCONNECT_RESULT` and `NET_EVENT_IPV4_ADDR_DEL` at the `switch` case labels (`:508/:523/:485`) |
| Golioth B-native | 1 | 0 / 0 | **0 / 0** | — (G2 has no artifact) |
| bms A1 | 2 | 1 / 1 | **1 / 1** | — |
| bms A1-native | 5 | 2 / 4 | **2 / 4** | B5's overlay part: `API-DEVICE-MISSING-69d84431` says the code at `thingset-sdk/src/serial.c:28` takes the device for `/uart_1` (`chosen thingset,serial`), whose status went from `'okay'` to `'disabled'`, and the action is to enable it in an overlay. The location is ThingSet's `serial.c`, not the overlay the maintainers changed, so that part is partly (c), and B5 stays partly (d) |
| bms A1-oled | 3 | 2 / 2 | **2 / 2** | B7 also by `API-ENUM-REMOVED-53f2fa22` (`CFB_DISPLAY_HEIGH`, `src/oled.c:109`) |
| **Held-out total** | **18** | **9 / 11 (50% / 61%)** | **9 / 11 (50% / 61%)** | |

**Recall is unchanged everywhere under the written rules.** P2 gives the exact cause for
every item the compiler already caught, and for ZMK #5 and A1-native B5, where it had only
symptoms. The location rule keeps #5 and B5 partly, because the findings name the header or
the code that breaks, not the build file or overlay where the maintainers applied the fix.

Under a reading where "names the fix location in its action" counted, ZMK #5 would be caught
(7 / 13 = 39% strict). That isn't the written rule, and the rules aren't changed after a
run.

## 3. Precision (every breaking/risky finding checked against both refs)

| Config | Baseline | Fixes only (`70d6f11`) | **P2 (`615cbc1`)** | P2's own findings: verdicts |
|---|---|---|---|---|
| ZMK | 12/22 (55%) | 12/22 (55%) | **15/28 (54%)** | 3 confirmed: the callback type, and both syscall headers. 3 harmless: `SENSOR_CHAN_*` values renumbered, but recompiled consistently and not persisted |
| Golioth B | 2/6 (33%) | 2/6 (33%) | **5/9 (56%)** | 3 confirmed: the typedef, the macro, and the `event_mask` field (`unsigned int` → `unsigned long long`, stored from a `uint32_t` at `:37`) |
| Golioth B-rak | 4/8 (50%) | 5/10 (50%) | **8/13 (62%)** | 3 confirmed (as B). M8 restored two layout findings: slot0 resized is confirmed (the code partition shrinks); slot1 moved is harmless (no MCUboot) |
| Golioth B-esp | 7/11 (64%) | 8/13 (62%) | **15/22 (68%)** | 7 confirmed: `net_mgmt_init_event_callback`, the typedef, `NET_EVENT_IPV4_ADDR_ADD`, `event_mask`, `NET_EVENT_WIFI_CONNECT_RESULT`/`…_DISCONNECT_RESULT`/`NET_EVENT_IPV4_ADDR_DEL` (case labels truncated). 2 harmless: `NET_REQUEST_WIFI_CONNECT/DISCONNECT` got wider, but they're passed to `net_mgmt()`, whose parameter widened too |
| Golioth B-native | 2/5 (40%) | 2/5 | **2/5 (40%)** | none |
| bms A1 | 1/9 (11%) | 1/9 | **1/9 (11%)** | none |
| bms A1-native | 10/10 (100%) | 8/8 | **9/9 (100%)** | 1 confirmed: the missing device. The 10 → 8 is M10 removing two duplicate deprecation warnings |
| bms A1-oled | 2/2 (100%) | 2/2 | **3/3 (100%)** | 1 confirmed: the removed enum constant |
| **Held-out total** | **28/51 (55%)** | **28/53 (53%)** | **43/70 (61%)** | |

What changed precision:

- **The macro-body fix** (`615cbc1`) removed, before scoring:
  - ZMK: 2 breaking "enum removed" false positives (`Z_LOG_MSG2_MODE_*`);
  - B-esp: 2 breaking "function changed" findings (`net_mgmt_NET_REQUEST_WIFI_*`, reached through the `net_mgmt()` macro) and 2 risky enum-value findings, all harmless.

  It's reported here because it changed held-out results. It's a general bug found on ZMK,
  not tuned on B or A1.
- **The remaining P2 false positives are value changes the user asked to see as risky**
  (§10.3): renumbered enums (ZMK) and wider request codes (B-esp). They're true claims with
  no effect on a consistently rebuilt image.

**Optional tools.** No configuration reports a `pahole` disagreement with clang's field
offsets. The analyzer records only disagreements, not agreements, so the reports can't show
that `pahole` actually ran on these ELFs. That's unverified here; see §5. No project links a prebuilt
library or uses LLEXT, so `abidiff` is exercised only by `tests/test_abitools.py`. That has
a real `gcc`/`ar`/`abidiff` run (present path) and a forced-absent path.

## 4. Regression tests added in this round

| Test | Covers |
|---|---|
| `tests/test_modulecheck.py` (6) | cmsis_6 from a missing header; ambiguity; already active; ZCBOR Kconfig; TF-M CMake; the nRF glue `&& 0`; TinyCrypt/mbedtls selects; manifest re-pin/removal |
| `test_target_needs_a_module_the_scan_lacks` (sdk) | the end-to-end module recovery on nrf52840dk v4.1.0 → v4.2.0 |
| `test_findings_only_in_unused_input_files_become_info` | M2/M3 and M8 (artifact paths) |
| `test_build_error_in_a_header_keeps_the_project_call_site` | M9 |
| `test_verdict_confidence_low_when_the_target_did_not_build` | M11 |
| `test_guide_entry_full_text_keeps_the_remedy` | M12; `test_guide_matches_point_at_real_lines` (build) guards the cited line |
| `tests/test_api_units.py` (7) | syscall JSON formats; `__syscall` declarations; only used declarations reported (functions, typedef-through-parameter, field, enum, function-like arity, 32→64-bit truncation); **zero findings for symbols the project doesn't use**; a missing header is a gap; uses inside Zephyr macro bodies |
| `tests/test_abitools.py` (4) | pahole and abidiff present; tools absent; the analyzer turns missing tools into not-checked |

Not covered by a test (each needs a feature, not a fix):

- the remaining false negatives: G2/B2 (no artifact), #6 `void main`, #8, #15, #18, #19;
- the false positives that come from §10.3's severity choice (renumbered enums, widened
  request codes);
- the OpenThread/NVS guide match (A1).

Full suites at `615cbc1`: 113 unit tests pass. The build/SDK suite passed 56/56 at
`eded5ce`, before the macro-body fix, which touches only `apidiff.py` and unit tests.

## 5. Known limitations seen in this round

1. **The oracle's location rule hides causes.** ZMK #5 and B5 are now fully explained but
   still "partly": the fix lands in a build file or overlay that the finding names only in
   its action.
2. **The cost of P2** is roughly ×15 on a first scan (cached afterwards).
3. **`pahole` agreement isn't recorded** as a checked item, so "no disagreement" and "not
   run" look the same in the report.
4. **G2 and B2 (7 of 18 held-out items) have no artifact in any scanned build.** The
   held-out ceiling stays 11/18 until zmig models default changes of symbols the build
   doesn't evaluate, or changes inside dependencies that don't affect the compiled files.
5. **Value-change findings are risky by design (§10.3).** On a consistently rebuilt image
   most are harmless; they matter only for persisted or transmitted values, which the wire
   category (P1) would have to link.

## 6. Ranked recommendation (from the measured numbers)

1. **Link P2's value changes to persistence and the wire** (P1's storage/wire analyzers):
   keep "risky" only for enum or macro values the project stores in settings/NVS or sends
   on the wire, and make the rest info. Measured effect on this data: the 5 harmless P2
   findings (3 ZMK, 2 B-esp) leave the breaking/risky count, so held-out precision would be
   43/68 = 63% and ZMK 15/25 = 60%, at no recall cost.
2. **Have findings point at the fix location** where zmig knows it, as evidence, not only
   in the action. This has to be a product change judged on its own merit, not a scoring
   change. `syscalls-not-generated` knows the remedy goes in the app's `CMakeLists.txt`
   and `prj.conf`. `device-missing` knows the node needs `status = "okay"` in an overlay,
   and can cite the overlay the build applied for that node, or say none sets it. On this
   data that would turn ZMK #5 and B5 into caught, if the oracle's location rule then
   matches; that should be confirmed with you first, because it touches the scoring.
3. **Record checked items** (pahole agreement, boundaries searched and found none), so the
   report separates "checked, clean" from "not run".
4. **Cut P2's first-run cost:** skip the macro-value probe for TUs whose macro set was
   already evaluated, and parse only TUs that include a header that changed between the
   refs (`git diff --name-only` on the include set). Both are safe, because "provably
   unused" is decided on the parsed TUs.
5. **Per-side SDK support.** It's still not needed by any current pair, and is needed
   only to add bms-firmware v4.3 → v4.4 (A2) as a held-out pair. Keep it after the items
   above.

P3 and P4 weren't started.

---

## 7. Review additions (2026-10-28)

### 7.1 A third metric: actionable

**Definition.** An in-scope item is *actionable* if at least one finding scored for it
(caught or partly) has an `action` text that names a file the maintainers edited for that
item. "Names" means the file's path or basename appears in the text. It's reported next to
strict and lenient recall, and replaces neither. Evidence is never changed to improve it:
the evidence is what proves the hazard.

Measured on the P2 runs (`615cbc1`):

| Config | Items | Actionable | Which |
|---|---|---|---|
| ZMK | 18 | **1 (6%)** | #5: `API-SYSCALLS-NOT-GENERATED-*` says "register it in your CMakeLists.txt with zephyr_syscall_header(…)" (the maintainers edited `app/CMakeLists.txt` and `app/prj.conf`) |
| Golioth B, B-rak, B-esp, B-native | 2 + 3 + 2 + 1 | **0** | e.g. B-rak G3: "Remove the assignment, or find its replacement…" (the file is in the title and evidence, not the action) |
| bms A1, A1-native, A1-oled | 2 + 5 + 3 | **0** | e.g. "Add cmsis_6 to your workspace (the name-allowlist …)" (the maintainers edited `west.yml`, which isn't named); "Enable the node in an overlay" (the overlay isn't named) |
| **Held-out total** | 18 | **0 (0%)** | |

Most actions are generic ("Read MG-… and update the lines listed", "Check whether it points
at a real problem"). Measured this way, zmig says what breaks far more often than it says
where to fix it.

### 7.2 The held-out lenient recall is at its ceiling

**Held-out lenient recall, 11 of 18, is the most any tool can reach on this set.** The
other 7 items have no revealing artifact in any scanned build:

- G2 in 4 Golioth configs: a `configdefault` whose condition is false, and `MCUMGR` is off;
- B2 in 3 bms-firmware configs: the ThingSet SDK's upgrade-related changes don't affect the
  compiled files, and `chosen thingset,eeprom` is set.

Further analyzer work can't move lenient recall on this set. It can only move strict recall
(4 items are partly: B5, B6, and ZMK-style location misses) and precision. Measuring recall
gains needs new pairs or configurations whose changes have artifacts.

### 7.3 Which results were measured on projects I had tuned on

A project counts as tuned on from the first run whose output led to a zmig change, even
when the change was an allowed misleading-output fix or one you asked for.

| Project / config | Clean measurements (no zmig change informed by it yet) | Measurements after zmig changes it informed |
|---|---|---|
| ZMK | none: it was the development project from DOGFOOD-1 on | DOGFOOD-1, DOGFOOD-2, DOGFOOD-3 baseline and P2: all tuned (Step 2 classes derived from it; P2's macro-body fix found on it) |
| smp_svr | none | DOGFOOD-1, and its use as an init-order check in Step 2 |
| Golioth B (nucleo_h563zi) | **DOGFOOD-2 baseline only** | Step 2 onward: M1, M2/M3 and M4 were found on B's baseline output |
| bms A1 (bms_16s100_sc) | **DOGFOOD-2 baseline only** | Step 2 onward: M4, M5; module-set detection (your item 1) was derived from its cmsis_6 failure; the SBOM compiled-includes fix was found on it |
| Golioth B-rak, B-esp | **DOGFOOD-3 baseline** | P2 numbers: M8 was found on their baseline output |
| Golioth B-native | **DOGFOOD-3 baseline** | P2 numbers: no fix came from it, but it ran on code changed by fixes from the others. Its results are unchanged |
| bms A1-native, A1-oled | **DOGFOOD-3 baseline**, with one caveat: the Kconfig rules for absent modules (TinyCrypt, mbedtls) were extended after seeing A1-native's *verification* build log, before the oracle and the baseline run | P2 numbers: M9–M12 were found on their baseline output |

The clean numbers are the DOGFOOD-2 baseline for B and A1, and the DOGFOOD-3 baseline for
the five configurations added there. Every later number is on projects whose output has
informed zmig changes.
