# DOGFOOD-7: a reproducible evaluation harness, and the last six rows re-measured

2026-10-29. zmig `aa95a10` code (`main` after the audit fixes). Every number here is
**tuned** (`docs/EVALUATION.md`, "Clean vs tuned").

## 1. The harness

`eval/` (see [eval/README.md](../eval/README.md)) re-runs every scored configuration except
OpenDeck, with each input pinned to a public commit.

### How the invocations were recovered

DOGFOOD-6 left six rows unmeasured because their commands weren't recorded: Golioth B-rak,
B-esp and B-native, bms A1-native and A1-oled, and ZMK. The scratch directory of the session
that scored them was still on disk. It held:
- the scripts (`base3.sh` for the Golioth and bms configurations, `zmk.sh` for ZMK);
- the scored reports (`p2final/`). Their breaking/risky counts match the published
  denominators: B-esp has 22, where `p2/` has 26, and ZMK has 28 as a union over corne and
  niceview.

### Pinned inputs

The app trees in that directory were exports, not git checkouts. Each was pinned by diffing
it against a fresh checkout:

| Source | Commit | Evidence |
|---|---|---|
| golioth-firmware-sdk (+ `external/libcoap`, `external/zcbor`) | `1fc16f4a` | identical to the scored tree |
| bms-firmware | `77c6206b` | identical |
| thingset-zephyr-sdk | `48740769` | identical except `src/certs/isrgrootx1.der.inc`, which a build generates |
| thingset-node-c | `68c75448` | identical |
| CANnectivity | `80612eec` (tag v1.1.0) | identical |
| zmk | `b8846cf6` (DOGFOOD-1: parent of the bump series) | identical |
| zmkfirmware/zephyr | `1ae0eb5c` → `339570e2` (DOGFOOD-1) | identical; both still fetchable |

### Reproduction

Every configuration was re-run from freshly fetched inputs with the zmig commit that was
scored:
- `6f0bf46` for the six DOGFOOD-3 P2 rows. That is `615cbc1` in the rewritten history,
  with an identical `zmig/` tree.
- `aa95a10` for the four DOGFOOD-6 rows.

**All 11 configurations reproduced exactly: every finding ID and severity, info included
(339 findings).**

### Three things it took

1. **Run a worktree's zmig from the worktree.** `python -c` puts the working directory first
   on `sys.path`, so the repository's `zmig/` shadowed the worktree's. An assert in the
   runner caught it before any scan ran.
2. **Directory names are inputs.**
   - Zephyr names a module after its directory (`ZEPHYR_GOLIOTH_FIRMWARE_SDK_MODULE_DIR`),
     so checkouts keep the repository name.
   - zmig labels a path ref after its directory, and that label is in finding subjects and
     IDs (`zephyr-to@339570e2393b`), so ZMK's Zephyr trees keep their scored names.
3. **No `@` in paths.** A module path containing two `@` reached Kconfig with everything
   between them removed (`/home/<user>@<domain>/…/golioth@1fc16f4afec5/…` became
   `/home/<user>1fc16f4afec5/…`). That looks like CMake's `@VAR@` substitution in
   Zephyr's build. This home directory already contains one `@`. Not investigated further.

## 2. The six rows at current `main`

`eval/run.py compare` against the scored `p2final` baselines found demotions only. No
breaking/risky finding was added or removed, and no field-upgrade verdict changed. Every
demotion carries the tag of the audit fix that caused it.

| Config | Demoted to info | Cause |
|---|---|---|
| B-rak, B-esp, B-native | `log.h`, `kernel.h` `header-changed` (2 each) | F8, `sbom.api-covered-no-change` |
| A1-native | `KCONFIG-VALUE-NOT-TAKEN-48a2e9d2` (`LOG_BACKEND_NATIVE_POSIX`) | F1, `kconfig.preexisting-diagnostic` |
| ZMK corne and niceview | 7 guide matches (`a7776724`, `106b2d8e`, `14a743b0`, `213c7c7b`, `39073cd9`, `3f730cc9`, `fca177c5`) | F7, `outside-build` |

### Verdicts of the demoted findings, as published

- **Golioth `header-changed`:** harmless in all three configurations
  (DOGFOOD-3-baseline §3).
- **ZMK, under the written rules** (DOGFOOD-2-baseline):
  - `a7776724`: unverifiable.
  - `106b2d8e` and `3f730cc9`: harmless, test harness only.
  - `14a743b0`, `213c7c7b`, `39073cd9` and `fca177c5`: harmless, "in files this build
    doesn't compile", which is exactly F7's test.
- **A1-native `48a2e9d2`:** confirmed. See the correction below.

### Recall

The only demoted findings that carried recall credit were ZMK #7 (`39073cd9`) and A1-native
B6 (`48a2e9d2`). Both were **partly (c)**: the right cause at a location the maintainers
didn't edit. ORACLE-RULES §2 (c) has no severity condition, unlike (b), and DOGFOOD-2-baseline
already credited ZMK #10 as partly (c) from info-only guide matches. So both stay partly (c),
and recall is unchanged everywhere.

### Results

| Config | Precision (P2 → now) | Strict / lenient | Actionable | Verdict |
|---|---|---|---|---|
| Golioth B-rak | 8/13 (62%) → **8/11 (73%)** | 2 / 2 of 3, unchanged | 0 | blocked (medium), unchanged |
| Golioth B-esp | 15/22 (68%) → **15/20 (75%)** | 1 / 1 of 2, unchanged | 0 | blocked (medium), unchanged |
| Golioth B-native | 2/5 (40%) → **2/3 (67%)** | 0 / 0 of 1, unchanged | 0 | direct-ok (medium), unchanged |
| bms A1-native | 9/9 (100%) → **8/8 (100%)**; corrected P2: 8/9 | 2 / 4 of 5, unchanged | 0 | direct-ok (low), unchanged |
| bms A1-oled | 3/3 (100%), unchanged | 2 / 2 of 3, unchanged | 0 | direct-ok (low), unchanged |
| ZMK corne ∪ niceview | 15/28 (54%) → **15/21 (71%)** | 6 / 13 of 18, unchanged | 1/18 | unknown (low), unchanged |

Wall clock with cold builds: 133–215 s per configuration.

ZMK's verdict: EVALUATION.md's ZMK row says unknown (**medium**). The scored P2 report
(`p2final/zmk`), its reproduction and today's run all say unknown (**low**). The new row
uses the measured value.

### Correction to DOGFOOD-3 (A1-native precision)

DOGFOOD-3 counted `KCONFIG-VALUE-NOT-TAKEN-48a2e9d2` ("`CONFIG_LOG_BACKEND_NATIVE_POSIX=y` no
longer takes effect") as confirmed. The source configure (`4ebecae`, a successful build)
prints the same warning with the same unsatisfied dependency, `!LOG_MODE_MINIMAL`.
`oracles/bms-firmware-configs.md` (B6) says the same: at the source the assignment already
didn't take effect.

The claim "no longer" is false at the source, and ORACLE-RULES §3 requires a confirmed
claim to be true at both refs, so the finding was **harmless**. A1-native's P2 precision
should have been **8/9 (89%)**, not 9/9. The published row stays as it was, and this note
corrects it. Today's finding states the true cause: "has no effect …; already not taken at
4ebecae".

## 3. Coverage of EVALUATION.md

Every row is now re-runnable, except:
- **OpenDeck:** patched Zephyr trees; re-runnable with `dogfood/d6/run.sh`.
- **smp_svr:** a single field-upgrade hazard.
- **The P1 fixtures:** asserted by tests.

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
| bms A1-native | 0 | **1/5** (B1b, `MANIFEST-MODULE-REQUIRED-559fd34e`: "Add mbedtls to your manifest in ../west.yml:18") |
| bms A1-oled | 0 | **1/3** (B1a, `MANIFEST-MODULE-REQUIRED-194d1ea7`) |

The Golioth configurations (0) and ZMK (1/18, #5) are unaffected.

The comparison in `eval/run.py` now also reports changed action text and evidence files of
breaking/risky findings, so this kind of change is listed for the scorer.

## Open question (2026-10-30): ZMK #3's location

ZMK #3 (`hci_err.h` → `hci_types.h`, `src/ble.c`) is labelled caught through
`BUILD-TARGET-BUILD-FAILED-f5607720` (DOGFOOD-2-baseline §1).

| Run | `src/ble.c:22` in the finding's evidence? |
|---|---|
| DOGFOOD-1/2 (`dogfood/out/final-corne`) | **yes**, as file_line evidence |
| P2 (`p2final`), its reproduction, and today's run | **no**: the evidence lists only the five syscall-header errors. `src/ble.c:22` and `hci_err.h` survive only in the finding's raw error list (`new`) |

So the change predates the audit fixes, and P2 kept the label. ORACLE-RULES §2 requires the
*evidence* to point at the location. Under a strict reading, #3 has been **partly (c)**
(the right cause, evidence elsewhere) since P2, which makes ZMK's strict recall 5/18 rather than 6/18. Lenient recall is
unchanged. This is left for the maintainer to decide; the published numbers stand until
then.
