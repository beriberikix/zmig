# DOGFOOD-8: clean baseline on three new held-out projects

2026-10-30. zmig `d0567c6` (`main`; zmig code as at `aa95a10`). These are the first zmig
runs on these projects. Their oracles were committed and pushed before the runs
(`5ce3547`, `d0567c6`): [ardep](../oracles/ardep.md), [rit-fsw](../oracles/rit-fsw.md),
[coffeecaller](../oracles/coffeecaller.md). Scoring follows [ORACLE-RULES.md](../ORACLE-RULES.md),
including §5 (actionable and fix-actionable).

**These results are clean.** They are the "before" numbers for PROPOSAL-CHANGE-DB's Phase 0.
Any zmig change informed by the bugs in §3 makes later numbers on these projects tuned.

Runs: `eval/run.py scan --out ~/.cache/zmig/eval/runs/heldout-baseline ardep rit-fsw-sensor
coffeecaller-native`. Wall clock 51 s, 48 s and 28 s; the builds were already cached by the
verification runs.

## 1. Results

| Project | Upgrade | In scope | Strict | Lenient | Precision | Actionable | Fix-actionable | Target build | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| ardep `samples/uds` (ardep) | v4.3.0 → v4.4.0 | 6 | 0 | 3 (50%) | 4/5 (80%) | 0 | 0 | configure failed (`board.yml`) | unknown (low) |
| RIT FSW `sensor_module` | v4.1.0 → v4.2.0 | 3 | 0 | 1 (33%) | 3/6 (50%) | 0 | 0 | configure failed (Kconfig) | unknown (low) |
| CoffeeCaller (native_sim) | v4.2.0 → v4.4.0 | 4 | 0 | 2 (50%) | 3/4 (75%) | 0 | 0 | build failed (devicetree aliases) | direct-ok (low) |
| **Total** | | **13** | **0** | **6 (46%)** | **10/15 (67%)** | **0** | **0** | | |

**The target build failed in all three projects.** On two, it failed before Kconfig, so
the kconfig, devicetree and SBOM analyzers were skipped at the target. That's EVALUATION.md's
known limitation #1 ("a failed target configure blinds most analyzers"), now on clean data.

## 2. Per item

### ardep (6 in scope; A4 is out)

| Item | Label | Finding, and why |
|---|---|---|
| A1 CAN filter Kconfig rename (`Kconfig.defconfig:17,20`) | missed | Kconfig was skipped (no target `.config`), and the guide entry (`:246-264`) didn't match |
| A2 `STD_C11` → `STD_C17` (A2a `Kconfig.uds:19`, A2b `prj.conf:9`) | **partly (d)** | `GUIDE-ENTRY-MATCH-3906323a` (risky/medium) at `prj.conf:9` quotes the 4.4 entry "deprecated options … `CONFIG_STD_C11`", so A2b meets both conditions. Nothing points at A2a |
| A3 `board.yml` `full_name` | **partly (b)** | `BUILD-TARGET-CONFIGURE-FAILED-3e867c18` (breaking) quotes "Malformed board YAML file: …/boards/mercedes/ardep/board.yml … not valid under any of the given schemas". That's the right file, but not the cause: `full_name` isn't named, so a reader can't tell what to change |
| A5 Mbed TLS 4 / PSA (A5a `prj.conf:25-27`, A5b–d `authentication.c`) | **partly (d)** | `GUIDE-ENTRY-MATCH-12a8832a` and `-c99a2403` at `prj.conf:25` say `MBEDTLS_CIPHER_AES_ENABLED` was removed (A5a). Nothing for `authentication.c`: the target never compiled |
| A6 `tf-psa-crypto` allowlist | missed | Configure stopped at the board before Mbed TLS, so no module-required finding |
| A7 cannectivity re-pin | missed | No artifact (oracle) |

**Precision, 4/5:**
- Confirmed: the configure failure (true at the target); both Mbed TLS matches (the symbol
  is removed, and it's set in the merged `prj.conf`); the C17 match (`STD_C11` is
  deprecated at the target and set in `prj.conf`).
- **Harmless:** `API-MACRO-CHANGED-7feea320`, "`va_start` … 2 parameters → 1 … Update every
  use". `modules/iso14229/drv.c` isn't touched by the bump, and the maintainers' post-bump
  tree compiles it at v4.4.0, so the claim is false.

### RIT FSW (3 in scope; F4 and F5 are out)

| Item | Label | Finding, and why |
|---|---|---|
| F1 TMP116 → TMP11X (F1a `sensor_module.dts:202`, F1b `:208`, F1c `sensor_module_defconfig:33`) | **partly (c)** | `GUIDE-ENTRY-MATCH-7f0cd344` (breaking/medium) states the rename in its action ("ti,tmp116 was renamed to ti,tmp11x"), but its evidence is `src/c_sensing_tenant.cpp:20`, the user of the node, not the board files. `DEVICETREE-NODE-CHANGED-e569c9c3` (binding `ti,tmp116.yaml` → none) is the same node's symptom. The configure failure quotes only "Aborting due to Kconfig warnings", not the undefined-`TMP116` warning at `:33` |
| F2 `cmsis` → `cmsis_6` | missed | The scan passed `cmsis_6`, so the build has no failure; no finding names the allowlist |
| F3 enum log-argument casts (7 parts) | missed | No target compile, and no finding about the log macros |

**Precision, 3/6:**
- Confirmed: the configure failure; the `ti,tmp116` guide match (the node the code takes
  loses its binding); the thermometer binding change.
- **Harmless, 3:** `DEVICETREE-NODE-CHANGED-b38002c0` (`rtc` `clocks`), `-c97add6b` and
  `-d42c45e0` (`irq-gpios`). The values are identical at both refs; only the line wrapping
  of the rendered DTS text differs (`"< &rcc 0x40 0x10000000 >, < &rcc 0x3 0x10c80070 >"` vs
  the same with a newline). See §3, B2.

### CoffeeCaller (4 in scope; C5 and C6 are out)

| Item | Label | Finding, and why |
|---|---|---|
| C1 board-extension `.conf`/`.overlay` no longer applied (C1a overlay, C1b `.conf`) | **partly (b)** | `BUILD-TARGET-BUILD-FAILED-acc08695` (breaking) names the aliases the overlay defines (`sw0`, `watchdog0`, `pwm_buzzer0`) at their users in `lib/cc_lib/*.c`, but not the cause. `KCONFIG-DEPENDENCY-CHANGED` ×2 (`DT_HAS_ZEPHYR_PWM_NATIVE_SIM_ENABLED`, `…FAKE_LED_STRIP…` y → n) are symptoms too |
| C2 `NATIVE_UART_0_ON_STDINOUT` → `UART_NATIVE_PTY_0_ON_STDINOUT` (`native_sim.conf:11`) | **partly (a)** | `KCONFIG-SYMBOL-REMOVED-c7b5b7b2` is at the exact line and states the cause, but it's **info**, wrongly demoted as "only in files this build doesn't use" (§3, B1). As breaking it would have been caught |
| C3 `configdefault NVS` | missed | No artifact on native_sim (oracle) |
| C4 stack 1024 → 2048 | missed | No artifact (oracle) |

**Precision, 3/4:**
- Confirmed: the target build failure; both dependency changes (the PWM and fake LED-strip
  drivers are no longer built at the target).
- **Harmless:** `API-FUNCTION-REMOVED-299e3811`, "`led_strip_update_rgb` … no longer exists
  in v4.4.0". It's a `static inline` in `include/zephyr/drivers/led_strip.h` at both v4.2.0
  (`:107`) and v4.4.0 (`:125`).

**Field-upgrade verdict.** The verdict is direct-ok (low) while the target build fails. The
verdict is about a deployed device, not the build, and its confidence is low. That's
consistent with the design, but it reads oddly next to a failing build.

## 3. zmig defects these runs exposed (not fixed here)

Each one cost credit or precision above. Fixing any of them is a change informed by these
projects, so later numbers on them are **tuned**.

| # | Defect | Effect here |
|---|---|---|
| B1 | **`outside_build` compares path forms that don't match.** Kconfig usage cites a fragment outside the app directory as `../../boards/extensions/native_sim/native_sim.conf` (app-relative). The build's input set has the same file in its module-prefixed form, so the finding is judged "only in files this build doesn't use", although the source build merged it | C2's `symbol-removed` (it would be caught) and four true `value-not-taken` findings (`PWM`, `GPIO`, `LED_STRIP`, `REBOOT`, C1b's consequences) became info |
| B2 | **Devicetree property values compared as rendered text.** Whitespace and line wrapping count as a change | 3 harmless risky findings (RIT) |
| B3 | **The API diff reports a static-inline function as removed** when the target TU doesn't parse cleanly (`led_strip_update_rgb`), and a `va_start` arity change that the target compiler doesn't have | 2 harmless findings (CoffeeCaller, ardep) |
| B4 | **The configure-failure evidence doesn't quote the Kconfig warnings behind "Aborting due to Kconfig warnings"** (the undefined `TMP116` at `sensor_module_defconfig:33`). Salvage doesn't edit board defconfigs outside the app (AUDIT F13) | RIT F1c had no finding at its line |
| B5 | **An app that finds its repository by a relative path out of the app directory can't be scanned unmodified** (`oracles/rit-fsw.md`, App shim) | Needed `eval/patches/rit-fsw-fsw-root.patch` |

## 4. What this means for PROPOSAL-CHANGE-DB

- **Actionable and fix-actionable are 0/13.** That's the clean "before" for Phase 0.
- **Phase 0 can only help the 6 items with a finding** (A2, A3, A5, F1, C1, C2). For example:
  - A2b and A5a: the finding is already at the edited line, so an action that names the file
    and quotes the guide's replacement would make them actionable;
  - C2: if B1 were fixed, the existing finding at `native_sim.conf:11` plus a rename
    (`UART_NATIVE_PTY_0_ON_STDINOUT` comes from Kconfig help text or the 4.2 guide) would
    give the exact fix.
- **The other 7 in-scope items have no finding.** They need detection, not better actions:
  - A1, A6 and F3: the target build stopped before their artifacts (Kconfig, Mbed TLS glue,
    compile);
  - F2: no artifact in the scanned build, which was given `cmsis_6`;
  - A7, C3 and C4: no artifact (oracle).
- **C1's root cause (extension files no longer applied) isn't detected either.** Only its
  symptoms are.
- **A failed target configure is the dominant limit on this set.** The proposal's Phase 1
  database (Kconfig symbol table and guide data, independent of the target build) would
  give A1 and F1c their replacements even when the target can't configure.
