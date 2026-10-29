# zmig: evaluation so far

One page for every project and configuration zmig has been measured on, as of 2026-10-29.
The detail is in the DOGFOOD-1 to DOGFOOD-5 reports and the oracles under `oracles/`.

## How it's measured

- **Oracles.** For each upgrade, an oracle lists the maintainers' own changes, the
  "items", and says which are in scope for the scanned build and why. It's written before
  zmig runs on the project ([ORACLE-RULES.md](ORACLE-RULES.md)).
- **Strict recall:** items caught. A breaking or risky finding at the location the
  maintainers changed, stating why it breaks.
- **Lenient recall:** caught or partly caught. Partly means info only, the symptom without
  the cause, the cause at the wrong location, or some parts of a multi-part item.
- **Precision:** confirmed breaking/risky findings ÷ all breaking/risky findings. Each one
  is checked against both refs; unverifiable findings count against it.
- **Actionable:** items where some scored finding's *action* names a file the maintainers
  edited (DOGFOOD-3 §7.1).
- **Clean vs tuned.** A result is **clean** only if no zmig change was informed by that
  project before it was measured. From the first fix a project's output inspired, its later
  numbers are **tuned**, even when the fix was an allowed misleading-output fix, or one the
  reviewer asked for.

## Results

| Project / configuration | Upgrade | Items in scope | Clean? | Strict | Lenient | Precision | Actionable | Target build | Field-upgrade verdict | Source |
|---|---|---|---|---|---|---|---|---|---|---|
| **ZMK** corne ∪ niceview | v3.2 → v3.5 | 18 | **tuned** (the development project) | 6 (33%) | 13 (72%) | 11/22 (50%) → 12/22 (55%) → 15/28 (54%) | 1/18 | configure failed (salvaged) | unknown (medium) | DOGFOOD-2-baseline §1, DOGFOOD-2 §2, DOGFOOD-3 §2–3, §7.1 |
| ″ | ″ | 18 | tuned (audit fixes, re-run at `aa95a10`) | 6 (33%) | 13 (72%) | **15/21 (71%)** | 1/18 | configure failed (salvaged) | unknown (low) | DOGFOOD-7 |
| **smp_svr** (nrf52840dk, MCUboot, BLE DFU) | v4.2.2 → v4.3.0 | 1 field-upgrade hazard | **tuned** | caught after fixes | — | before the fixes, 4 spurious remedies and a spurious storage pin | — | ok | needs-changes (medium): MCUboot mode mismatch, remedy config-pin | DOGFOOD-1 §1, "Verdict honesty" |
| **Golioth B** nucleo_h563zi | v4.1.0 → v4.2.0 | 2 | clean: DOGFOOD-2 baseline | 0 (0%) | 1 (50%) | 0/9 (0%) | — | ok | direct-ok (medium) | DOGFOOD-2-baseline §3–4 |
| ″ | ″ | 2 | tuned (Step 2, P2) | 1 (50%) | 1 (50%) | 2/6 (33%) → 5/9 (56%) | 0 | ok | direct-ok (medium) | DOGFOOD-2 §3, DOGFOOD-3 §2–3 |
| ″ | ″ | 2 | tuned (audit fixes, re-run at `aa95a10`) | 1 (50%) | 1 (50%) | **5/7 (71%)** | 0 | ok | direct-ok (medium) | DOGFOOD-6 |
| Golioth B-rak | ″ | 3 | clean: DOGFOOD-3 baseline | 2 | 2 | 4/8 (50%) | 0 | ok | | DOGFOOD-3 §2–3 |
| ″ | ″ | 3 | tuned (P2) | 2 | 2 | 8/13 (62%) | 0 | ok | | ″ |
| ″ | ″ | 3 | tuned (audit fixes, re-run at `aa95a10`) | 2 | 2 | **8/11 (73%)** | 0 | ok | blocked (medium) | DOGFOOD-7 |
| Golioth B-esp | ″ | 2 | clean: DOGFOOD-3 baseline | 1 | 1 | 7/11 (64%) | 0 | ok | | ″ |
| ″ | ″ | 2 | tuned (P2) | 1 | 1 | 15/22 (68%) | 0 | ok | | ″ |
| ″ | ″ | 2 | tuned (audit fixes, re-run at `aa95a10`) | 1 | 1 | **15/20 (75%)** | 0 | ok | blocked (medium) | DOGFOOD-7 |
| Golioth B-native | ″ | 1 | clean: DOGFOOD-3 baseline | 0 | 0 | 2/5 (40%) | 0 | ok | | ″ |
| ″ | ″ | 1 | tuned (audit fixes, re-run at `aa95a10`) | 0 | 0 | **2/3 (67%)** | 0 | ok | direct-ok (medium) | DOGFOOD-7 |
| **bms-firmware A1** bms_16s100_sc | v4.0 → v4.2 | 2 | clean: DOGFOOD-2 baseline | 0 (0%) | 1 (50%) | 1/6 (17%) | — | **build failed** (no cmsis_6) | unknown (medium) | DOGFOOD-2-baseline §5–6 |
| ″ | ″ | 2 | tuned (Step 2 → P2; re-run 2026-10-29) | 0 → 1 | 1 | 1/2 (50%) → **1/9 (11%)** | 0 | ok after module recovery | unknown (medium) | DOGFOOD-2 §4, DOGFOOD-3 §2–3, DOGFOOD-5 §10 |
| ″ | ″ | 2 | tuned (audit fixes, re-run at `aa95a10`) | 1 | 1 | **1/4 (25%)** | 0 | ok after module recovery | unknown (medium) | DOGFOOD-6 |
| bms A1-native | ″ | 5 | clean: DOGFOOD-3 baseline (one caveat, §7.3) | 2 | 4 | 10/10 (100%) | 0 | configure-failed | | DOGFOOD-3 §2–3 |
| ″ | ″ | 5 | tuned (P2) | 2 | 4 | 9/9 (100%) | 0 | | | ″ |
| ″ | ″ | 5 | tuned (audit fixes, re-run at `aa95a10`) | 2 | 4 | 8/8 (100%); P2 corrected to 8/9 (89%) | 0 | | direct-ok (low) | DOGFOOD-7 §2 correction |
| bms A1-oled | ″ | 3 | clean: DOGFOOD-3 baseline (same caveat) | 2 | 2 | 2/2 (100%) | 0 | build failed | | ″ |
| ″ | ″ | 3 | tuned (P2) | 2 | 2 | 3/3 (100%) | 0 | | | ″ |
| ″ | ″ | 3 | tuned (audit fixes, re-run at `aa95a10`) | 2 | 2 | 3/3 (100%) | 0 | | direct-ok (low) | DOGFOOD-7 |
| **CANnectivity C1** usb2canfdv1 | v1.1.0 → v1.2.0 (Zephyr v4.1.0 → v4.2-branch) | 3 | clean: DOGFOOD-4 baseline | 1 (33%) | 2 (67%) | 7/11 (64%) | 0 | build failed (link: `smf_set_handled`) | direct-ok (**low**) | DOGFOOD-4-baseline §2–3 |
| ″ | ″ | 3 | tuned (M13–M15; definition sites) | 1 → **2** | 2 | 7/8 (88%) | 0 | ″ | ″ | DOGFOOD-4 §6, C2 correction |
| ″ | ″ | 3 | tuned (audit fixes, re-run at `aa95a10`) | 2 | 2 | 7/8 (88%) | 0 | ″ | ″ | DOGFOOD-6 |
| CANnectivity C2 candlelight | ″ | 3 | clean: DOGFOOD-4 baseline | 1 (33%) | 2 (67%) | 7/8 (88%) | 0 | build failed | direct-ok (low) | ″ |
| ″ | ″ | 3 | tuned (definition sites) | **2** | 2 | 7/8 (88%) | 0 | ″ | ″ | `oracles/cannectivity-c2-correction-2026-10-28.md` |
| ″ | ″ | 3 | tuned (audit fixes, re-run at `aa95a10`) | 2 | 2 | 7/8 (88%) | 0 | ″ | ″ | DOGFOOD-6 |
| CANnectivity frdm_k64f | ″ | — | **not run** (the download was stopped) | | | | | | | ″ |
| **OpenDeck** nucleo_h563zi, scan | bump `ab8f6af` (Zephyr `main` 4.4.99, both sides plus OpenDeck's patches) | 5 | clean: DOGFOOD-5 baseline | 0 (0%) | 2 (40%) | 11/22 (50%) | 0 | **configure failed** (devicetree label; a patch-supplied Kconfig function behind it) | needs-changes (low) | DOGFOOD-5-baseline §2–5 |
| ″ | ″ | 5 | tuned (§10, §11) | 1 (20%) | 1 (20%) | 11/11 (100%) | 1/5 | ″ | ″ | DOGFOOD-5 §10–11 |
| ″ | ″ | 5 | tuned (audit fixes, re-run at `aa95a10`) | 1 (20%) | 1 (20%) | 11/11 (100%) | 1/5 | ″ | ″ | DOGFOOD-6 |
| **OpenDeck** plan (patch triage) | ″ | 9 | clean: DOGFOOD-5 baseline | 6 (67%) | 9 (100%) | 8/15 (53%) | 0 | — | — | DOGFOOD-5-baseline §4–5 |
| ″ | ″ | 9 | tuned (§10, §11) | 9 (100%) | 9 (100%) | 8/9 (89%) | 8/9 | — | — | DOGFOOD-5 §10–11 |
| ″ | ″ | 9 | tuned (§10, §11, §11.4 hunk window) | 9 (100%) | 9 (100%) | 8/8 (100%) | 8/9 | — | — | DOGFOOD-5 §11.4 |
| ″ | ″ | 9 | tuned (audit fixes, re-run at `aa95a10`) | 9 (100%) | 9 (100%) | 8/8 (100%) | 8/9 | — | — | DOGFOOD-6 |
| **P1 fixtures** (`tests/fixtures/p1-layout`, `p1-mode`, `p1-rename`, `p1-ble`, `xapp*`) | v4.2.2 → v4.3.0 (constructed) | — | **tuned by construction** (written to exercise P1) | — | — | — | — | | each fixture's expected verdict (layout hazard, MCUboot mode, rename, BLE clean; cross-image) is asserted by `tests/test_p1_fixture.py`, `test_cross_fixture.py` | DESIGN-P1 §11 |

### Clean results only

| Set | Items | Strict | Lenient | Precision | Actionable |
|---|---|---|---|---|---|
| Golioth (B baseline; B-rak, B-esp, B-native from DOGFOOD-3) | 8 | 3 (38%) | 4 (50%) | 13/33 (39%) | 0 |
| bms-firmware (A1 baseline; A1-native, A1-oled) | 10 | 4 (40%) | 7 (70%) | 13/18 (72%) | 0 |
| CANnectivity (C1, C2) | 6 | 2 (33%) | 4 (67%) | 14/19 (74%) | 0 |
| OpenDeck scan | 5 | 0 | 2 (40%) | 11/22 (50%) | 0 |
| OpenDeck plan (patches) | 9 | 6 (67%) | 9 (100%) | 8/15 (53%) | 0 |

Across every clean scan result, zmig names *what* breaks more often than *where to fix it*:
**actionable was 0 on every clean measurement.** The only non-zero actionable figures are
tuned: ZMK 1/18, OpenDeck plan 8/9, OpenDeck scan 1/5.

## The ceiling: items no tool could catch in these builds

Some in-scope items leave no artifact in the scanned build: no compiler message, Kconfig
difference, devicetree difference or file a scan can see. No analyzer can catch them, so
they bound recall.

- **Golioth and bms-firmware held-out set** (DOGFOOD-3 §7.2): 7 of 18 items have no
  revealing artifact (G2 in 4 Golioth configurations, B2 in 3 bms configurations).
  Lenient recall there is **11/18, which is the ceiling**.
- **CANnectivity:** N3 (`select UDC_ENABLE_SOF`) is parsed but has no effect in either
  build. The ceiling is 2/3 per configuration.
- **OpenDeck scan:** C1 (CMake minimum) and M1 (the `zlibs` bump) have no artifact. K1
  (`CONFIG_ETH_DRIVER` lost its default) is unreachable while the target app can't get past
  Kconfig, which is blocked by a patch that no longer applies. The ceiling is 1–2/5.

Gains on these sets therefore show up only as strict recall, precision and actionable.
Lenient recall can't move further without new pairs.

## Where results got worse

| Where | Before → after | Why |
|---|---|---|
| bms A1 precision | 1/2 (50%, DOGFOOD-2 Step 2) → 1/9 (11%, DOGFOOD-3 P2 and the 2026-10-29 re-run) | more risky findings, all harmless here: `header-changed` for 5 headers the app includes (from the SBOM), 2 module repins, and the OpenThread guide false positive. Strict recall rose from 0 to 1 in the same step (module recovery). Re-measured after the audit fixes: **1/4 (25%)**, the 5 `header-changed` are now info (DOGFOOD-6) |
| ZMK precision | 12/22 (55%) → 15/28 (54%) | P2 added 6 findings, 3 of them harmless (`SENSOR_CHAN_*` renumbering) |
| OpenDeck scan lenient | 2/5 → 1/5 (§10) | M19 demoted the bootloader's ADC findings, D2's only partial, which were harmless; the item is now missed |
| OpenDeck verdict confidence | low → low, now also for "51 of 51 app TUs not interpreted" | the API diff of the app can't run, because the target never configures |
| Golioth B precision | 2/9 → 0/9 at the DOGFOOD-2 baseline | not a regression: the stricter written rules re-scored DOGFOOD-1's count |

## Build failures that shaped the results

| Project | Failure | Effect |
|---|---|---|
| ZMK | target configure fails | Kconfig, devicetree and init-order analysis ran only on salvaged builds |
| bms A1 | no `cmsis_6` | led to module recovery (then the target builds) |
| bms A1-native, A1-oled | configure or build failed | partial results |
| CANnectivity C1, C2 | link error at the target (`smf_set_handled` removed) | no target ELF: init-order and SBOM skipped; verdict confidence low |
| OpenDeck | target app configure fails at devicetree (then Kconfig) | no app Kconfig, devicetree, SBOM or API comparison at the target |
| OpenDeck (setup) | upstream Zephyr can't build it at all (needs its 31 patches) | scanned on patched local checkouts |

## Known limitations (all still open)

- **Quickstart timing is unmeasured.** The first-run clone is about 1 GB. The time of a
  scan after that (warm mirror) hasn't been measured on a clean cache. A run on
  2026-10-29 was stopped on hotel wifi after 44 minutes: 224 MB of the mirror at about
  50 KB/s, before any scan. The steps before the clone took 3 s, and `zmig doctor` passed.
  `~/.cache/zmig/dogfood/d8/quickstart2.sh` reruns it from a clean cache and times three
  scans: cold, warm mirror without builds, and fully warm.

1. **A failed target configure blinds most analyzers** for that image. That's the common
   case in real upgrades: ZMK, OpenDeck, A1-native.
2. **Missing-module inference is partial.** mbedtls's need for `tf-psa-crypto` isn't
   detected (OpenDeck); it's recorded, not fixed.
3. **The C++ API diff:** parsing works since the include-directory fix, but nothing is
   compared when the target has no compile commands.
4. **Projects that patch Zephyr** need patched checkouts set up by hand; zmig doesn't read
   a project's patch directory by itself.
5. **Project modules bumped together with Zephyr** (OpenDeck's `zlibs`, bms's ThingSet SDK)
   are taken from the scanned tree at both refs, so their changes aren't analyzed.
6. **Locations:** zmig often points at the code that breaks, not the build file or
   overlay the maintainers edited. That's why actionable is low.
7. **Run-time behaviour** (defaults that change behaviour without a build artifact) is out
   of reach by design.
8. **watch in CI:** fork PRs, cron, SARIF upload and cache limits are unverified
   ([CI-TEST.md](CI-TEST.md)).
