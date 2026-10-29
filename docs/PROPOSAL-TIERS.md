# Design proposal: presentation tiers (act now / review / FYI)

Status: **design only, not implemented, not a measured improvement.** Waiting for review
(revised 2026-10-29).

## What it is

Today every breaking and risky finding is shown with the same weight. The proposal groups
them into three tiers for presentation. It doesn't change how findings are produced, and
it doesn't change any finding.

## Inputs: run-time evidence only

Tier assignment may use **only what zmig has when it runs**:

| Input | Where it comes from at run time |
|---|---|
| severity | `Finding.severity` |
| evidence kind | the `type` of each evidence item (`file_line`, `artifact_diff`, `tool_output`, …) |
| user file:line present | a `file_line` evidence item whose path is a project file, not Zephyr's or a module's |
| in the scanned build | the cited project file is one this image's build used: compiled or included (`ScanContext.build_files`, from ninja's dependency records), or applied as an overlay or `.conf` (the image's configure log) |

**Oracle labels are never inputs.** "Confirmed", "harmless", "unverifiable", recall
labels and oracle items exist only in evaluation documents written after a run. They
appear below only to *evaluate* the design, never to assign a tier.

## Rule

| Tier | A finding is in it if |
|---|---|
| **Act now** | severity breaking **and** at least one user `file_line` in the scanned build |
| **Review** | any other breaking finding; or risky with at least one user `file_line` in the scanned build |
| **FYI** | everything else: risky without a user location in the scanned build, and all info |

Within a tier, the order stays as it is today (severity, confidence, category, id).

## Invariants (to be enforced by tests when implemented)

1. **Nothing is hidden or dropped.**
   - Every finding appears in exactly one tier.
   - `report.json`, MIGRATION.md and SARIF keep every finding.
   - The count per tier is printed even when a tier is collapsed in the terminal.
2. **A breaking finding in the scanned build can never be in FYI.** By the rule, no
   breaking finding is ever FYI: it is act-now or review.
3. **Tier assignment is deterministic.**
   - It is a pure function of the finding's fields and the build-use sets, with no
     ordering, time or environment dependence.
   - Tests: the tier of every finding in the stored report fixtures (`tests/fixtures/watch/*.json`,
     `tests/fixtures/opendeck/scan-report.json`) is asserted, and shuffling the finding
     order must not change any finding's tier.
   - Invariant 2 is checked over all fixtures, as `invariant_violations` does for the verdict.
4. **Oracle labels are not read.** The tier function takes no evaluation data. A test asserts
   its signature takes only a finding and the build-use sets.

## Evaluation on the clean reports (small sample, does not generalize)

**Sample:**

- 3 clean baseline reports: CANnectivity C1 and C2 (`dogfood/d4/base`), and the OpenDeck
  scan (`dogfood/d6/scan`). The Golioth and bms-firmware baseline reports are no longer on
  disk, so they can't be tiered.
- 41 breaking/risky findings.
- Tiers were assigned by hand with the rule above, from each report's fields and the
  build-use facts the scan records. `dfu.c` isn't compiled in C1; 1 of `counter2`'s 5
  overlays is C1's board.
- The labels used to evaluate them are the recorded ones (DOGFOOD-4-baseline §3,
  DOGFOOD-5-baseline §5).

| Clean report | Act now | Review | FYI (breaking/risky only) |
|---|---|---|---|
| CANnectivity C1 (11) | 3, all labelled confirmed: `function-removed`, `callback-type-changed`, guide `entry-match` (all at `src/led.c`) | 7, 4 confirmed: build failed, cmsis_6 required (no `file_line`: `west.yml`), 2 new warnings; `&zephyr_udc0` unverifiable; `led0`, `counter2` phandle harmless | 1, harmless: `mcuboot_led0` phandle (`dfu.c` not compiled) |
| CANnectivity C2 (8) | 3, all confirmed: the same three | 5, 4 confirmed | 0 |
| OpenDeck scan (22) | 0: its one breaking finding (configure failed) has no project location | 14, 3 confirmed | 8, all confirmed (MCUboot revision, 7 module repins; not oracle items) |
| **Total (n = 41, 3 reports)** | **6 findings, 6 confirmed** | 26 findings, 11 confirmed | 9 findings, 8 confirmed |

The previous draft also required `confidence: high` for act-now. That is run-time data
too, but not among the inputs listed above. With it, act-now held **4 findings** (the two
guide `entry-match` findings are medium confidence and drop to review), all 4 confirmed.
Whether confidence may be an input is open question 1.

**What the numbers can and can't say:**

- **The sample is too small to generalize.** 6 (or 4) act-now findings, from 3 reports.
  Every one comes from **one project**, in two configurations of the same code. OpenDeck
  contributes none. "All confirmed" here is 6/6 from one project, not a precision estimate.
- **Precision of all breaking/risky findings is unchanged:** 25/41 on these reports. The
  tiers move no finding out of the report.
- **Strict actionable stays 0 on all 3 reports.** Tiers don't change action text. The
  act-now actions are "Find its replacement in the migration guides", "Change their
  signatures to match (…)", and "Read MG-4.2-0137 and update the lines listed".
- **FYI can hold real hazards.** On OpenDeck it holds 8 findings, all real. That's why
  invariant 1 matters: FYI is collapsed, never removed.

## Open questions for review

1. May `confidence` be an input (4 act-now findings instead of 6 here)?
2. Should a breaking finding with no location (configure or build failed) go to act-now
   when it is the first failure? It blocks everything else, but there is no file to act on.
3. Should `west.yml` evidence count as a user location? CANnectivity's `cmsis_6` finding
   would then move to act-now.
4. Should the tier be a report field (schema 1.5), or presentation only (terminal and
   MIGRATION.md)?
