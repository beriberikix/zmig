# DOGFOOD-11: the change database (PROPOSAL-CHANGE-DB Phase 1), measured

2026-10-30. zmig `983fe98`, on `main` `2341504`. [DESIGN-CHANGE-DB.md](DESIGN-CHANGE-DB.md)
steps 1–4 are implemented; this report is step 5. Scoring follows
[ORACLE-RULES.md](ORACLE-RULES.md), including §5.

Commits:
- `8366fd9`: Kconfig facts and `zmig changes`.
- `c5ca2be`: the `changes` analyzer for Kconfig.
- `87cb54c`: bindings and `compatible-unbound` for project DTS.
- `2341504`: modules the tree says the target needs.
- `983fe98`: B3's `va_start` half.

Each commit message has its own re-scan deltas.

Runs:
- `eval/run.py scan`: all 14 configurations, after each step (`eval/runs/step2`,
  `step3`/`step3c`, `step4`/`step4b`, `b3va`, `b3vb`).
- OpenDeck scan and plan with `dogfood/d6/run.sh`
  (`dogfood/d12/opendeck-{step2,step3,step4,step4b,b3va,b3vb}`).

## 1. Clean or tuned

| Project | Status | Why |
|---|---|---|
| ardep | **clean** through step 4's first run (`eval/runs/step4`); **tuned** from then on | Two fixes were made after reading ardep's scan output: `hostap` (step 4, §4), then `va_start` (B3) |
| RIT FSW | tuned (since DOGFOOD-9 D1) | |
| CoffeeCaller | tuned (since DOGFOOD-9) | Step 3's "node gone isn't unbound" rule came from its run (§4) |

"Clean" uses the convention so far: no zmig change made in response to a zmig run on the
project. The design itself took the held-out oracles' items as its motivating examples
(DESIGN-CHANGE-DB §1).

## 2. Held-out results

### ardep (6 in scope)

| Item | DOGFOOD-10 | Now | Finding, and why | Actionable | Fix-actionable |
|---|---|---|---|---|---|
| A1 CAN filter override (A1a `Kconfig.defconfig:17`, A1b `:20`) | missed | **caught** | `BUILD-KCONFIG-OVERRIDE-ORPHANED-0cadde25` and `-171ecfed` (breaking): "your Kconfig re-declares CONFIG_CAN_MAX_STD/EXT_ID_FILTER, gone in v4.4.0", each at its line | yes | **yes**: `config CAN_MAX_STD_ID_FILTER` → `config CAN_STM32_FDCAN_MAX_STD_ID_FILTERS`, and the same for EXT. FDCAN, not bxCAN, because the source build has `CAN_STM32_FDCAN=y` |
| A2 `STD_C11` → `STD_C17` | partly (d) | partly (d) | unchanged | yes | no |
| A3 `board.yml` `full_name` | partly (b) | partly (b) | unchanged | no | no |
| A5 Mbed TLS 4 / PSA | partly (d) | partly (d) | A5a is now caught: `KCONFIG-SYMBOL-REMOVED-57758c3d` (breaking) at `prj.conf:25`, with no candidate. A5b–d (`authentication.c`) are still missed | yes | no (the maintainers replaced the symbols with the PSA ones; zmig says "remove it") |
| A6 `tf-psa-crypto` | missed | **caught** | `MANIFEST-MODULE-REQUIRED-2f167d68` (breaking/medium) at `west.yml:17`, the allowlist | yes | **yes**: "Add tf-psa-crypto to your manifest". By PROPOSAL-CHANGE-DB §2's convention ("Add `cmsis_6`") |
| A7 cannectivity re-pin | missed | missed | no artifact (oracle) | | |

**Precision, tuned (`b3vb`): 8/8.** The new findings are the two override findings, the
removed Mbed TLS symbol and `tf-psa-crypto`, each one an oracle item. The unchanged findings
keep their DOGFOOD-8 verdicts. `va_start` is gone.

**Clean (`step4`): 8/10.** Two harmless findings:
- `va_start` (DOGFOOD-8);
- `MANIFEST-MODULE-REQUIRED-35bc836f` `hostap`: the v4.4.0 mbedtls glue sets a path into
  hostap unconditionally, but the build doesn't need hostap.

### RIT FSW (3 in scope)

| Item | DOGFOOD-10 | Now | Finding, and why | Actionable | Fix-actionable |
|---|---|---|---|---|---|
| F1 TMP116 → TMP11X (F1a `sensor_module.dts:202`, F1b `:208`, F1c `sensor_module_defconfig:33`) | partly (c) | **caught** | F1a and F1b: `DEVICETREE-COMPATIBLE-UNBOUND-08d6189e` and `-8bf9a35b` (breaking) at their lines. F1c: `KCONFIG-SYMBOL-REMOVED-17222542` (breaking) at `:33` | yes | **yes**: `"ti,tmp116"` → `"ti,tmp11x"`, `"ti,tmp116-eeprom"` → `"ti,tmp11x-eeprom"`, `CONFIG_TMP116=y` → `CONFIG_TMP11X=y`. The maintainers also dropped a comment, which is formatting |
| F2 `cmsis` → `cmsis_6` | missed | **caught** | `MANIFEST-MODULE-NOT-IN-MANIFEST-a029f432` (breaking/medium) at `fsw/west.yml:17`, the allowlist key; the maintainers edited `:18`. Cause: `CONFIG_HAS_CMSIS_CORE_M`, enabled at the source, is defined only in `modules/cmsis_6/` at v4.2.0 | yes | **yes**, by the same convention: the maintainers replaced `cmsis` with `cmsis_6`, and "add `cmsis_6`" configures the same way |
| F3 enum log-argument casts | missed | missed | no target compile | | |

**Precision 7/7.** The three new findings are F1a, F1b and F2. The others keep their
verdicts.

### CoffeeCaller (4 in scope)

Unchanged by Phase 1 (its target configures). Strict 1, lenient 2, precision 8/8,
actionable 2, fix-actionable 1 (DOGFOOD-10).

### Totals

| | Clean ardep (`step4`) | Held-out, now (tuned) | DOGFOOD-10 |
|---|---|---|---|
| Items | 6 | 13 | 13 |
| Strict | **2** (0 before) | **5 (38%)** | 1 (8%) |
| Lenient | **5** (3 before) | **9 (69%)** | 6 (46%) |
| Precision | 8/10 | **23/23** | 15/16 |
| Actionable | **4** (2 before) | **8** | 5 |
| Fix-actionable | **2** (0 before) | **5** | 1 |

**The first clean strict recall and fix-actionable above 0 on a held-out project:**
ardep A1 and A6, before any change made in response to its output.

## 3. The gate: the other 11 configurations and OpenDeck

Across steps 2–5, every finding on Golioth (4 configurations), bms-firmware (3),
CANnectivity (2) and ZMK (2), and on OpenDeck's scan and plan, kept its ID, severity,
confidence, evidence, action and fix. Each commit message has the per-step diff.

**So precision and recall are unchanged on all of them**, and the original 44 items stay at
27/44 actionable and 15/44 fix-actionable (DOGFOOD-9).

**Two intermediate false positives were caught by this gate before commit:**
- CoffeeCaller: 5 breaking `compatible-unbound` findings (§4, step 3);
- ZMK: `size_t` "removed" (§4, B3).

Neither shipped.

## 4. What the measurement changed

| Where | Wrong output | Fix | Informed by |
|---|---|---|---|
| Step 3 | `gpio-keys`, `pwm-leds` and 3 more "lost their binding": the target doesn't apply CoffeeCaller's `native_sim.overlay`, so the nodes are gone, not unbound | `compatible-unbound` needs a target node that still lists the compatible | CoffeeCaller (tuned) |
| Step 4 | `hostap` "required" (ardep, OpenDeck) | glue paths count only into a project new in Zephyr's manifest, as DESIGN-CHANGE-DB §2.4 said and the first version left out | ardep (now tuned), OpenDeck |
| B3 | `va_start` "2 parameters → 1": both declarations were clang's own builtin header | declarations in clang's resource directory are recorded as `builtin`, and never compared | ardep (DOGFOOD-8) |
| B3, first version | dropping builtin declarations made ZMK's `size_t` look removed (Zephyr's libc at v3.2, clang's `stddef.h` at v3.5) | skip a name that is builtin at **either** ref | ZMK (tuned) |

## 5. The database: size and build time (PROPOSAL-CHANGE-DB §7)

Built in memory with `zmig.changes.build`, from the local mirror:

| Pair | Build | JSON | Kconfig removed (paired) | Compatibles removed (paired) | Glue dirs with paths | Symbols moved into glue |
|---|---|---|---|---|---|---|
| v4.1.0 → v4.2.0 | 1.1 s | 80 KiB | 181 (113) | 22 (14) | 8 | 114 |
| v4.2.0 → v4.4.0 | 2.4 s | 204 KiB | 941 (253) | 61 (40) | 9 | 19 |
| v4.3.0 → v4.4.0 | 1.6 s | 143 KiB | 682 (165) | 58 (31) | 9 | 1 |
| Golioth's pair (`4ebecae` → `636ad05`) | 1.8 s | 123 KiB | 317 (159) | 65 (38) | 8 | 112 |
| CANnectivity's pair (`7823374` → `30de5e8`) | 1.1 s | 80 KiB | 181 (113) | 22 (14) | 8 | 114 |
| **v2.7.0 → v4.4.0** (DESIGN-CHANGE-DB §9.2) | **7.7 s** | 423 KiB | 2237 (285) | 102 (43) | 9 | 227 |

ZMK's fork pair (`1ae0eb5` → `339570e`) is in the cache at 149 KiB, with 581 Kconfig
symbols removed.

**§9.2 is answered.** The whole database for a large gap takes seconds. Pairing works from
one `git log` per file class, not one per symbol.

## 6. Known gaps (open)

1. **Module-defined symbols (DESIGN-CHANGE-DB §2.1).** The design says a symbol isn't
   removed if a module tree the scan uses defines it at the target. That check isn't
   implemented: a symbol that moved from Zephyr into a module's own `Kconfig` would be
   reported removed. Nothing in these 15 configurations hit it, but it is a precision risk.
2. **A database that can't be built is silent in two places.**
   - The `changes` analyzer reports `error` (AUDIT §4).
   - The devicetree analyzer (replacements for `compatible-unbound`) and the modules
     analyzer (tree needs) go on without the database and say nothing. Their findings
     then lack a replacement, or a module need goes unreported.
3. **A6's cited line is a conditional use.** `legacy_support.cmake:18` sits inside
   `if(CONFIG_MBEDTLS_DECLARE_PRIVATE_IDENTIFIERS)`. The module is needed, but for the
   mbedtls build itself, which no glue line states.
4. **Glue paths only into new projects.** A module that the source build doesn't use,
   already in Zephyr's manifest and newly required through glue paths, is missed by
   design. The `hostap` case shows why.
5. **`zmig changes --symbol`** treats a name with a comma as a compatible, so a
   compatible without a vendor prefix (`arduino-nano-header-r3`) can't be selected.
6. **RIT's paths.** F1c's evidence path is `../../../src/rit-fsw-…/…_defconfig`, the
   harness's app copy (B5), while F1a cites `fsw/boards/…`. Kconfig fragments are cited
   relative to the app; DTS files and the manifest are cited module-prefixed.

## 7. Verdict on Phase 1

- **Detection where the target stops early works, and stays out of the way otherwise:**
  - +4 caught items on the held-out set: A1, A6, F1 and F2, all at the maintainers' lines,
    and all fix-actionable;
  - A5a is caught within a still-partial item;
  - no change on the 11 tuned configurations or OpenDeck.
- **The remaining held-out misses need compiles or have no artifact.** A5b–d and F3 need a
  target compile. A7, C3 and C4 have no artifact. A3's action still doesn't name
  `full_name`, a schema fact this phase didn't cover.
- **Next candidates:**
  - gap 1 (module-defined symbols), before relying on this beyond the measured set;
  - gap 2 (make a missing database visible);
  - A3: the board schema diff names `full_name`.

## 8. Addendum (2026-10-30): gaps 1 and 2 closed

- **Gap 1:** the `changes` analyzer reads what the target workspace's modules define, with
  one `git grep` per module for `config` lines and binding `compatible:` lines.
  - A removed symbol or compatible that a module defines isn't reported.
  - The project's own extra modules don't count, so ardep A1's re-declaration is still
    reported.
- **Gap 2:** when the database can't be built, the devicetree and modules analyzers
  report it under "not checked" (area "change database"), with the error.

**Re-scan** (`eval/runs/gaps`, all 14 configurations; OpenDeck scan and plan), against
`b3vb`: no finding changed, no analyzer errored, and `compare` exits 0. Gap 1's safeguard
removed nothing on the measured set, so §2's numbers stand.

Gaps 3–6 are still open.

## 9. Addendum (2026-10-30): ardep A3 (tuned)

**What changed.** When a target configure fails with "Malformed board YAML file", zmig now
validates that `board.yml` against the target's own `scripts/schemas/board-schema.yaml`,
as `list_boards.py` does, and against the source's.
- Zephyr prints only "is not valid under any of the given schemas".
- For a `oneOf` of required-key branches, zmig names the missing key of the branch the
  entry already partly satisfies (`name` without `full_name`), at that key's line.

**ardep's `BUILD-TARGET-CONFIGURE-FAILED-3e867c18`** (ID unchanged) now reads "add
`full_name`: required next to `name` in $.board (the target's board schema; the file is
valid at the source)". Its evidence is at `ardep/boards/mercedes/ardep/board.yml:2`, where
the maintainers added `full_name: ARDEP`.

**A3: partly (b) → caught, actionable, fix-actionable.** Fix-actionable is a judgement
call: the action states the construct (add `full_name`) but not the value. Any string
configures the same way.

**Re-scan** (`eval/runs/a3`, all 14 configurations; OpenDeck scan and plan): nothing else
changed, and `compare` exits 0.

| | ardep, tuned | Held-out total (13), tuned |
|---|---|---|
| Strict | 2 → **3** | 5 → **6** |
| Lenient | 5 | 9 |
| Precision | 8/8 | 23/23 |
| Actionable | 4 → **5** | 8 → **9** |
| Fix-actionable | 2 → **3** | 5 → **6** |

## 10. Addendum (2026-10-30): CoffeeCaller C1 (tuned)

**What changed.** A new risky finding, `input-not-applied` (buildlog): a project
configuration fragment or devicetree overlay that the source build applied and the
unmodified target build doesn't.
- The data comes from the configure logs' "Merged configuration" and "Found devicetree
  overlay" lines, as DOGFOOD-10 §4 proposed.
- Fragments are judged only when the target merged any, and overlays only when it
  generated a devicetree.

**CoffeeCaller** gets `BUILD-INPUT-NOT-APPLIED-1423caeb` (`native_sim.conf`, C1b) and
`-a3a66af1` (`native_sim.overlay`, C1a).
- **Both are true**, per the oracle's observed logs.
- **C1: partly (b) → caught.** Each part has a finding at its file that says it is no
  longer applied, and how to apply it again.
- **Judgement call:** the finding doesn't say why (Zephyr `c02c6add101` dropped HWMv1
  extension handling). The cause condition counts as met because a reader knows what to
  change.
- **Not fix-actionable:** the maintainers made a board variant, which the action offers
  only as one of the options.

**Re-scan** (`eval/runs/c1`, all 14 configurations; OpenDeck scan and plan): nothing else
changed.

| | CoffeeCaller, tuned | Held-out total (13), tuned |
|---|---|---|
| Strict | 1 → **2** | 6 → **7** |
| Lenient | 2 | 9 |
| Precision | 8/8 → **10/10** | 23/23 → **25/25** |
| Actionable | 2 | 9 |
| Fix-actionable | 1 | 6 |

## 11. Addendum (2026-10-30): ardep A2 (tuned)

**What changed.**
- **Database (v5):** it records symbols newly deprecated at the target, those whose block
  gains `select DEPRECATED`. v4.3.0 → v4.4.0 has 47, `STD_C11` among them.
- **`changes` analyzer:** when the target has no `.config`, it reports the kconfig
  analyzer's `symbol-deprecated` (same kind and subject) for a newly deprecated symbol
  that the source build enables.
- **Evidence:** the project's assignments, plus references in its own Kconfig files
  (`default`, `select`, `depends on`, …). Those files are read from the source build's
  `zephyr/kconfig/sources.txt`.

**ardep:** `KCONFIG-SYMBOL-DEPRECATED-ae676b9e` (risky) at `prj.conf:9` (A2b),
`ardep/boards/mercedes/ardep/Kconfig.uds:19` (A2a, `default STD_C11` in the `choice STD_C`
override) and `ardep/modules/iso14229/Kconfig:10` (`depends on STD_C11 || …`, unchanged
by the maintainers).
- **A2: partly (d) → caught.** It's actionable, but not fix-actionable: no replacement is
  named, and the maintainers moved to `STD_C17`.
- **True** (oracle A2).

**OpenDeck scan** (the gate): `KCONFIG-SYMBOL-DEPRECATED-ddcedfef`, `CONFIG_RING_BUFFER`,
at `zlibs/module/src/drivers/uart_hw/Kconfig:5` (`select RING_BUFFER`).
- **Verdict: confirmed hazard, not in the oracle.** At the target, `RING_BUFFER` became
  "Restore legacy claim/finish and item ring buffer APIs [DEPRECATED]"
  (`lib/utils/Kconfig:39-41`). At the source it was "Ring buffers". The project's driver,
  which the source build parsed with `RING_BUFFER=y`, would select a deprecated legacy
  mode at the target.
- OpenDeck scan precision 11/11 → **12/12** (tuned). Recall is unchanged.

**Re-scan** (`eval/runs/a2`, all 14 configurations; OpenDeck scan and plan): nothing else
changed.

| | ardep, tuned | Held-out total (13), tuned |
|---|---|---|
| Strict | 3 → **4** | 7 → **8** |
| Lenient | 5 | 9 |
| Precision | 8/8 → **9/9** | 25/25 → **26/26** |
| Actionable | 5 | 9 |
| Fix-actionable | 3 | 6 |
