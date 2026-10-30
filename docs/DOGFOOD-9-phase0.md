# DOGFOOD-9: Phase 0 (actions built from the findings' own facts), measured

2026-10-30. zmig `phase0-fix` (on `9d09bbd`); see PROPOSAL-CHANGE-DB §3. Runs:
- `eval/run.py scan --out ~/.cache/zmig/eval/runs/phase0` (all 14 configurations);
- OpenDeck with `dogfood/d6/run.sh` (`OUT=dogfood/d12/opendeck-phase0`).

Scoring follows ORACLE-RULES §5.

## 1. What changed

- **`Finding.fix`** (schema 1.5): `{file, line, old, new, source, confidence}`.
- **`zmig/fixes.py`**: a pass after every analyzer that sets `fix` and renders `action` from
  it. It names the first project location, and states the edit when the finding (or a
  finding linked to it) holds both sides:
  - a Kconfig rename candidate with Kconfig-level evidence, or a related guide rename;
  - a guide entry's parsed rename;
  - a callback definition's expected type;
  - the compiler's "did you mean";
  - an overlay snippet for a disabled device.
- **Kconfig:** a deprecated symbol's own help text ("use X instead") is now a rename
  candidate, for removed symbols and on `symbol-deprecated` findings.

## 2. The gate: nothing but `action` and `fix` changed

Across 16 runs (all 14 harness configurations, plus OpenDeck's scan and plan), every
finding's ID, severity, confidence, kind, subject and evidence equals its value in the last
scored run, info included: 482 findings.

**One apparent exception, explained:** bms A1's four guide findings carry the Zephyr ref in
their evidence. The `d7` harness spelled it `636ad05950bb`; `eval/configs.toml` spells the
full SHA. That's a difference in how the input was written, not a change.

**So recall and precision are unchanged on every configuration.** That holds by
construction, and was checked.

## 3. Results

### Held-out (DOGFOOD-8's 13 items)

**Clean or tuned.** ardep and RIT FSW informed no Phase 0 code, so their numbers are
**clean**.

CoffeeCaller's are **tuned**. The help-text rename source was written after reading its
baseline output (DOGFOOD-8 §4 on C2), and its test cites C2.

| Project | Clean? | Actionable (before → after) | Fix-actionable (before → after) | Items that changed |
|---|---|---|---|---|
| ardep (6) | **clean** | 0 → **2** | 0 → 0 | A2 and A5: "In prj.conf:9 / :25: read MG-… and update the lines listed." The file the maintainers edited is named, but not the edit |
| RIT FSW (3) | **clean** | 0 → 0 | 0 → 0 | F1's action names `src/c_sensing_tenant.cpp`, not the board files the maintainers edited (§4, D1) |
| CoffeeCaller (4) | tuned | 0 → 1 | 0 → **1** | C2: "In …/native_sim.conf:11, change `CONFIG_NATIVE_UART_0_ON_STDINOUT=y` to `CONFIG_UART_NATIVE_PTY_0_ON_STDINOUT=y`". That's the maintainers' edit. The finding is still info, because of DOGFOOD-8's B1 |
| **Clean subtotal (9)** | | **0 → 2** | **0 → 0** | |

### The original 44 items (all tuned)

| | Before | After |
|---|---|---|
| Actionable | 12 / 44 | **27 / 44** |
| Fix-actionable | 10 / 44 | **14 / 44** |

**Newly actionable (15):**
- ZMK #1, #2, #4, #9, #11, #13, #14, #17;
- Golioth G1, G3;
- bms B4, B5, B6, B7;
- CANnectivity N1.

Each action now names a file the maintainers edited.

**Newly fix-actionable (4):**
- **ZMK #2:** `activity_init`, `int (const struct device *)` → `int (void)`, at
  `src/activity.c:87`.
- **bms B4:** `CONFIG_NATIVE_UART_0_ON_STDINOUT=y` → `CONFIG_UART_NATIVE_PTY_0_ON_STDINOUT=y`
  at `boards/native_sim.conf:8`.
- **bms B5:** `&uart1 { status = "okay"; };` in `boards/native_sim.overlay`, the overlay the
  maintainers edited.
- **bms B7:** `CFB_DISPLAY_HEIGH` → `CFB_DISPLAY_HEIGHT` at `src/oled.c:109`.

**Not reached:**
- ZMK #3 (its location isn't in the evidence since P2);
- ZMK #12 (detected in `behavior_reset.c`; the fix is in `app/Kconfig`).

**Compared with the proposal's estimates** (about 28 actionable and 19 fix-actionable):
- Actionable came out on target.
- Fix-actionable came out lower. Most of the "full fix derivable" items need facts Phase 0
  doesn't structure yet: a guide's remedy prose (N1b, ZMK #9), or "in favor of" renames
  (G3).
- One fix came out wrong (§4, D2).

## 4. Defects in Phase 0 that this run exposed

| # | Defect | Where it showed |
|---|---|---|
| **D1** | A guide rename's fix is placed at the first project location, even when that line doesn't contain the old name. RIT F1: "In `src/c_sensing_tenant.cpp:20`, change `ti,tmp116` to `ti,tmp11x`" is wrong, because that line is `DEVICE_DT_GET(DT_ALIAS(thermometer))`. It should fall back to location-only unless the line contains the old token | RIT FSW (clean) |
| **D2** | A callback's expected type is taken as clang spells it, which can be a typedef name. CANnectivity N1 got "`void (void *)` → `const state_execution`" instead of the canonical `enum smf_state_result (void *)` (clang's "aka" form) | CANnectivity C1, C2 |
| D3 | Fix paths repeat the evidence's path form, so they inherit DOGFOOD-8's B1 (`../../boards/extensions/…`) | CoffeeCaller |

**D1 and D2 are confidently wrong fixes**, which is the risk §7 of the proposal names.
Fixing them is informed by these projects, so the numbers after the fixes are **tuned** for
RIT FSW and CANnectivity.

## 5. Verdict on Phase 0

- **Actionable improved where findings already held the location:** 12 → 27 on the tuned
  set, and 0 → 2 on the clean held-out projects.
- **The clean gain is small,** because two of three held-out projects stop at the target
  configure, so few of their items have a finding at all (DOGFOOD-8 §4).
- **Fix-actionable:** +4 on the tuned set, 0 on the clean set.
- **Two defects must be fixed before this ships.** D1 and D2 are the "confidently wrong
  fix" risk.
- **Next:** fix D1 and D2 (tuned), then Phase 1, the change database, for replacements no
  finding holds today.

## 6. Addendum (2026-10-30): D1 and D2 fixed (tuned)

**D1:** a guide rename now becomes an edit only at an evidence line that contains the old
name. Otherwise the fix is location-only.

**D2:** a callback's expected and current types come from clang's canonical "aka" form,
with the pointer declarator removed (`R (*const)(A)` → `R (A)`).

Regression tests:
- `test_guide_rename_only_on_a_line_that_has_the_old_name`;
- `test_callback_expected_type_is_canonical_not_a_typedef_name`.

`test_guide_rename_of_a_compatible`, which asserted D1's behaviour, is removed.

**Re-run of all 14 configurations** (`eval/runs/phase0-d1d2`, against `eval/runs/phase0`):
- every finding's ID, severity, confidence and evidence is unchanged;
- exactly three fixes changed:

| Finding | Was | Now |
|---|---|---|
| CANnectivity C1 `…-4b10a6e3` and C2 `…-b61ee94c` | `void (void *)` → `const state_execution` | `void (void *)` → `enum smf_state_result (void *)` |
| RIT FSW `GUIDE-ENTRY-MATCH-7f0cd344` | `ti,tmp116` → `ti,tmp11x` at `src/c_sensing_tenant.cpp:20` | location-only (that line doesn't name the compatible) |

**Effect on the scores.** Both fixes were informed by these runs, so all of this is
**tuned**:
- CANnectivity N1 is now fix-actionable, which takes the original 44 items to **15 / 44**.
- RIT FSW's scores are unchanged (still 0 / 0). Its action is now correct, where before it
  was wrong. RIT FSW is **tuned** from here on. ardep remains the only clean held-out
  project.
