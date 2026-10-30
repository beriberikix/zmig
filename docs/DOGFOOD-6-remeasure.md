# DOGFOOD-6: re-measurement after the audit fixes

2026-10-29. zmig `aa95a10` (`main` after PR #1, the `AUDIT.md` fixes). Every number here is
**tuned**: these projects were scored before, and some fixes since then were informed by
them (`docs/EVALUATION.md`, "Clean vs tuned"). The audit's `header-changed` fix was
motivated partly by bms A1's published precision.

## Method

- **Same inputs as the last scored run.** Each configuration was re-run with the harness
  that produced its scored report, unchanged except for the output directory:
  - Golioth B, CANnectivity C1 and C2, bms A1: `dogfood/d7/regress.sh` → `dogfood/d12/regress.sh`;
  - OpenDeck scan and plan: `dogfood/d6/run.sh` with `OUT=dogfood/d12/opendeck`.
- **Baselines.**
  - The four `d7` reports, at zmig `c7aac37`.
  - OpenDeck `d6/rescore2/scan` (scan 11/11) and `d6/rescore3-withdir/plan` (plan 8/8).
- **History rewrite.** `c7aac37` isn't in today's `main` history, which was rewritten after
  those runs. Its `zmig/` tree is identical to `9b741e5` on `main`, so the baseline is
  `9b741e5`. That leaves 30 commits between the baseline and the audit's starting point
  `ddf6b2b`, so the deltas below are attributed per finding (next point), not per run.
- **Scoring.** Findings were matched by ID (ORACLE-RULES §3: "a finding is one finding ID").
  - An unchanged breaking/risky finding keeps its published verdict.
  - A finding demoted to info leaves the precision denominator.
  - A new or changed breaking/risky finding needs a new verdict against both refs. **There
    were none**, so no new verdict was needed.
- **Attribution.** Every severity change carries the heuristic tag
  `sbom.api-covered-no-change`, which is commit `d0f9e03` (audit F8). The tier labels
  (`act-now`/`review`/`fyi`) that are new since the baseline don't affect scoring.

## Results

| Config | Breaking/risky | Precision | Strict / lenient | Actionable | Verdict | Wall clock (cold builds) |
|---|---|---|---|---|---|---|
| Golioth B | 9 → 7 | 5/9 (56%) → **5/7 (71%)** | 1 / 1 of 2, unchanged | 0, unchanged | direct-ok (medium), unchanged | 187 s |
| CANnectivity C1 | 8 → 8 | 7/8 (88%), unchanged | 2 / 2 of 3, unchanged | 0, unchanged | direct-ok (low), unchanged | 135 s |
| CANnectivity C2 | 8 → 8 | 7/8 (88%), unchanged | 2 / 2 of 3, unchanged | 0, unchanged | direct-ok (low), unchanged | 116 s |
| bms A1 | 9 → 4 | 1/9 (11%) → **1/4 (25%)** | 1 / 1 of 2, unchanged | 0, unchanged | unknown (medium), unchanged | 159 s |
| OpenDeck scan | 11 → 11 | 11/11 (100%), unchanged | 1 / 1 of 5, unchanged | 1/5, unchanged | needs-changes (low), unchanged | 557 s |
| OpenDeck plan | 8 → 8 | 8/8 (100%), unchanged | 9 / 9 of 9, unchanged | 8/9, unchanged | — | 46 s |

All builds were cold because of the build-key change (`cb90465`).

### What changed, by finding

- **Golioth B:** `SBOM-HEADER-CHANGED-24bcf578` (`log.h`) and `-e7c787b9` (`kernel.h`) went
  from risky to info. Both were scored **harmless** (DOGFOOD-2 §3, findings 3–4).
- **bms A1:** five `header-changed` went from risky to info: `gpio.h`, `device.h`,
  `hwinfo.h`, `crc.h` and `kernel.h` (`-35fcf306`, `-4d1c5020`, `-9e29743c`, `-a8b6ec35`,
  `-e608c20f`). EVALUATION.md counted all five as harmless. The four findings that remain:
  - `MANIFEST-MODULE-REQUIRED-cd65f61b` (`cmsis_6`): confirmed, the caught item. It stays
    breaking because the source, v4.0, didn't need `cmsis_6`.
  - The OpenThread guide match: harmless.
  - Two module repins: unverifiable.
- **No recall change.** None of the demoted findings was an item match, so recall can't
  move.

### Info-level changes (not scored)

- **One new info finding** in each of bms A1 and OpenDeck scan: `dependency-expression-changed`.
- **Golioth B lost two info findings that only concerned `boards/rak5010_nrf52840.conf`.**
  These were `symbol-removed CONFIG_MODEM_CELLULAR_CMUX_MAX_FRAME_SIZE` and a deprecation.
  Commit `6f11022` (before the audit) indexes only the fragments a build reads. G3 is in
  scope for B-rak, not B (`oracles/golioth-configs.md:44`), so B's scoring is unaffected.
  B-rak's behaviour wasn't re-measured.

## Not re-measured

These have no recorded invocation. Reconstructing them from the board names in
`DOGFOOD-3-baseline.md` would not reproduce the scored runs, so their published numbers
stand as published:

- Golioth B-rak, B-esp and B-native;
- bms A1-native and A1-oled;
- ZMK corne ∪ niceview.

For B-rak, `6f11022` and the audit fixes are the two changes most likely to matter
(G3 is found through `rak5010_nrf52840.conf`).

## Takeaway

- **Precision moved where the audit predicted, and nowhere else.** The `header-changed`
  demotion removed 7 harmless findings across two projects.
- **No confirmed finding was demoted, and recall is unchanged everywhere.**
- **Actionable is still 0 on every scan except OpenDeck's 1/5.** The audit fixes weren't
  aimed at it, and it remains the weakest number.

## Correction (2026-10-30): actionable

This report said actionable was unchanged. **It wasn't**: the table's actionable column was
copied from the scored runs, and nobody checked the action text.

Commit `ddf6b2b` changed the action of `module-required`, between this report's baseline
and the audit:
- before: "Add cmsis_6 to your workspace (the name-allowlist or project list that imports
  Zephyr)…";
- after: "Add cmsis_6 to your manifest in ../west.yml:18 (the name-allowlist of the Zephyr
  import)…" (`:14` for CANnectivity).

`west.yml` is the file the maintainers edited for bms-firmware B1 and CANnectivity N2. Under
the definition (DOGFOOD-3 §7.1), the action names an edited file. The finding IDs and
severities didn't change, which is why matching by ID missed it.

| Config | Actionable, as reported | Corrected |
|---|---|---|
| bms A1 | 0 | **1/2** (B1, `MANIFEST-MODULE-REQUIRED-cd65f61b`) |
| CANnectivity C1 | 0 | **1/3** (N2, `MANIFEST-MODULE-REQUIRED-f4a43627`) |
| CANnectivity C2 | 0 | **1/3** (N2, `MANIFEST-MODULE-REQUIRED-e7f9a024`) |

Golioth B (0) and OpenDeck (scan 1/5, plan 8/9) are unaffected.

The comparison in `eval/run.py` now also reports changed action text and evidence files of
breaking/risky findings, so this kind of change is listed for the scorer.
