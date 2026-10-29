# Presentation tiers (act now / review / FYI)

Status: **implemented, as an additive presentation field** (2026-10-29). Code:
`zmig/tiers.py`; tests: `tests/test_tiers.py`.

## What it is, and what it isn't

Each finding in `report.json` gets a `tier` field: `act-now`, `review` or `fyi`.
MIGRATION.md gets a "By tier" section that groups every finding by tier.

**Nothing else changes:**

- the field is additive; the schema allows it as an optional property;
- `schema_version` stays 1.4, since no existing field changed;
- the verdict, exit codes, watch fingerprints, `report.top()`, the terminal summary, and
  every other field and section are computed without it.

## Inputs: run-time evidence only

| Input | Source at run time |
|---|---|
| severity | `Finding.severity` |
| confidence | `Finding.confidence`. It may only move a breaking finding between act-now and review, never into or out of FYI (review decision) |
| evidence kind, user file:line | `file_line` evidence whose path is a project file. The rule is the same as SARIF's: Zephyr's and generated files are shown as `<…>`, absolute or `(…)` |
| used by the scanned build | `ScanContext.build_use`, per image, from the build's own records (see below) |

**Oracle labels are never inputs.** That covers confirmed, harmless, unverifiable, recall
labels and oracle items. They appear below only to evaluate the result. A test asserts the
rule's signature, `tier(severity, confidence, evidence, use)`.

**"Used by the scanned build"** is decided per file kind:

- **compiled sources and headers:** ninja's dependency records (`build_files`);
- **overlays:** the overlays the image applied;
- **Kconfig fragments:** the fragments it merged (`config_files`).

A file of any other kind (Kconfig, CMake, YAML) counts as used, and so does every file of
a build with no such records. **A finding is only demoted when the build shows the file
wasn't used.**

## Rule

| Tier | A finding is in it if |
|---|---|
| **act-now** | breaking, high confidence, and a project file:line the scanned build used |
| **review** | any other breaking finding; or risky with a project file:line the build used |
| **fyi** | everything else: risky without such a location, and all info |

## Invariants, all tested (`tests/test_tiers.py`)

1. **Nothing is hidden or dropped.**
   - Every finding gets exactly one tier.
   - The By tier section lists every finding once.
   - Every finding is still in the usual sections.
   - All other JSON fields are identical with and without the tier.
2. **A breaking finding in the scanned build is never FYI.** In fact no breaking finding
   is ever FYI. This is checked over every finding in every stored report fixture, both
   with no build records and with records saying the build used nothing (the most
   demoting case).
3. **Assignment is deterministic.** Shuffling the findings doesn't change any finding's
   tier; the rule is a pure function.
4. **Watch fingerprints are unchanged by the tier.**

## On the clean measurements (anecdotal)

**How the numbers were computed:**

- `zmig.tiers.tier`, applied to the 3 clean baseline reports still on disk: CANnectivity
  C1 and C2, and the OpenDeck scan. The Golioth and bms-firmware baseline reports are gone.
- Build use was read from each report's own cached source build: ninja's dependency
  records and the configure logs, nothing rebuilt
  (`$CLAUDE_JOB_DIR/tmp/tiers_clean.py`).
- The function gives the same counts as the earlier hand assignment.
- Labels come from DOGFOOD-4-baseline §3 and DOGFOOD-5-baseline §5, and are used only to
  evaluate.

| Clean report (breaking/risky) | act-now | review | fyi |
|---|---|---|---|
| CANnectivity C1 (11) | 2: `function-removed`, `callback-type-changed`; both labelled confirmed | 8 (5 confirmed) | 1: `mcuboot_led0` phandle at `dfu.c`, not compiled (harmless) |
| CANnectivity C2 (8) | 2: the same two; both confirmed | 6 (5 confirmed) | 0 |
| OpenDeck scan (22) | 0 | 14 (3 confirmed) | 8: MCUboot revision and 7 module repins, all confirmed (not oracle items) |
| **Total (41 findings, 3 reports)** | **4, all from one project** | 28 | 9 |

**These numbers are anecdotal.**

- 4 act-now findings from 3 reports, and all 4 from **one project** (CANnectivity, in two
  configurations of the same code). They say nothing about act-now precision in general.
- The guide `entry-match` findings are breaking but medium confidence, so they're in
  review. With confidence not an input there would be 6 act-now findings.
- FYI holds real hazards on OpenDeck: 8 of 9 are labelled confirmed. That's why FYI is
  never collapsed out of the report.
- **Strict actionable is unchanged (0 on all 3 reports).** Tiers don't change action text.

On the bundled fixture (`tests/fixtures/app`, a constructed project) the run gives 4
act-now, 12 review and 13 FYI, out of 29 findings.

## Open questions

1. Should the first configure or build failure, which has no location, be act-now? It
   blocks everything else, but there's no file to act on.
2. Should `west.yml` evidence count as a project location? CANnectivity's `cmsis_6`
   finding would move to act-now.
3. Should the terminal summary show act-now first? Not done, since the terminal output
   was out of scope.
