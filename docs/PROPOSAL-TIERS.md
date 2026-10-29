# Proposal: presentation tiers (act now / review / FYI)

Status: **proposal only, not implemented**, waiting for review (2026-10-29).

## The idea

Today every breaking or risky finding is shown with the same weight. The proposal groups
them by evidence zmig already records. Nothing about how findings are produced changes,
and no finding is dropped.

| Tier | Rule (all from existing fields) |
|---|---|
| **Act now** | `severity` breaking, **and** `confidence` high, **and** at least one `file_line` evidence in a project file **the scanned build used** |
| **Review** | any other breaking finding; a risky finding with a project `file_line` the scanned build used |
| **FYI** | risky findings with no project location, or whose project locations the scanned build didn't use; all info findings |

"The scanned build used" already exists per image:

- C and C++: `ScanContext.build_files`, from ninja's dependency records;
- overlays and `.conf` files: the files that image's configure log applied (`usage` per
  image).

Order inside a tier stays as it is (severity, confidence, category).

## What it would have given on the clean measurements

Only three clean baseline reports are still on disk: CANnectivity C1 and C2
(`dogfood/d4/base`) and the OpenDeck scan (`dogfood/d6/scan`). The Golioth and bms-firmware
baseline reports were lost with an earlier scratch directory. Their numbers in
EVALUATION.md come from the written scoring, not from a report, so they can't be re-tiered.

Verdicts are the recorded ones: DOGFOOD-4-baseline §3 and DOGFOOD-5-baseline §5. Each
finding was assigned to a tier by hand with the rule above. The build-use facts come from
the same sections: `dfu.c` isn't compiled in C1 (M15); 1 of `counter2`'s 5 overlays is
C1's board.

| Clean report | Act now | Review | FYI (breaking/risky only) |
|---|---|---|---|
| CANnectivity C1 (11) | **2/2 confirmed**: `function-removed` `smf_set_handled` (`led.c:225`), `callback-type-changed` (`led.c:348`) | 5/8: build failed, cmsis_6 required, guide entry, 2 new warnings confirmed; `&zephyr_udc0` unverifiable; `led0`, `counter2` phandle harmless | 0/1: `mcuboot_led0` phandle (`dfu.c`, not compiled), harmless |
| CANnectivity C2 (8) | **2/2 confirmed**: the same two | 5/6: as C1, minus the phandle findings | — |
| OpenDeck scan (22) | **0**: the one breaking finding (configure failed) has no project location | 3/14: configure failed, `&mac`/`&mdio` in the bootloader confirmed; 7 false macro findings, `&mac`/`&mdio` in MCUboot and `&adc1`/`&adc2` in the bootloader harmless | 8/8 confirmed: MCUboot revision, 7 module repins (not oracle items) |
| **Total** | **4/4 (100%)** | 13/28 (46%) | 8/9 (89%) |

Precision of all breaking/risky findings on these three reports is 25/41 (61%), the same
as today, because no finding moves out.

**Recall in the act-now tier:**

- **CANnectivity:** N1b (`smf_set_handled`) is caught in it, at exactly the lines the
  maintainers changed. N1a's finding is in it too, at the initializers rather than the
  definitions (partly). N2 (`cmsis_6`) is in review, because `west.yml` evidence isn't a
  `file_line`.
- **OpenDeck:** no item reaches act-now. D1's symptom is in review.

**Actionable:**

- **Strict (the action names a file the maintainers edited): still 0 on every clean
  report.** Tiers don't change action text. The act-now actions are "Find its replacement
  in the migration guides" and "Change their signatures to match (…)".
- **Weaker (the act-now evidence names the edited file):** CANnectivity N1 in both
  configurations (`src/led.c`). That's 2 of the 3 clean reports' item sets.

## What it would and wouldn't fix

- **It helps** by keeping the few findings with a confirmed cause and a location in your
  build apart from the long risky tail. On OpenDeck that tail was 7 false macro findings
  and 4 harmless devicetree findings (since fixed, but this was the clean run).
- **It doesn't raise strict actionable.** That needs actions that name the file to edit,
  a separate change.
- **It can hide a real hazard in FYI.** The 7 module repins and the MCUboot revision were
  all real, though not oracle items. Mitigation: FYI stays in the report and in
  MIGRATION.md under its own heading. Only the terminal summary would show act-now first.
- **The sample is small:** 4 act-now findings, all from one project's two configurations.
  The 100% says little until more clean projects are measured.

## Open questions for review

1. Should a breaking finding with no location (configure or build failed) be act-now
   when it's the first failure? It's what blocks everything else, but it has no file to
   act on.
2. Should `west.yml` evidence count as a project location (N2 would move to act-now)?
3. Should the tier be a report field (schema 1.5) or presentation only (terminal and
   MIGRATION.md)?
