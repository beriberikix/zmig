# Design: the change database (PROPOSAL-CHANGE-DB Phase 1)

Status: **implemented** (steps 1–4: `8366fd9`, `c5ca2be`, `87cb54c`, `2341504`) and
**measured** in [DOGFOOD-11-change-db.md](DOGFOOD-11-change-db.md), which also lists where
the implementation falls short of this design (§6 there: §2.1's module-defined symbols).
Written 2026-10-30 as a design for review. It builds on
[PROPOSAL-CHANGE-DB.md](PROPOSAL-CHANGE-DB.md); decisions 1–5 there apply. Every fact
quoted here was probed on the local Zephyr mirror; nothing has been built.

## 1. What it is for

Phase 0 improved actions where a finding exists. On the held-out projects
(DOGFOOD-8/10), most of the remaining misses have **no finding at all**, because the
**target configure failed before Kconfig**. With no target `.config`, `edt.pickle` or
compile, the kconfig, devicetree and API analyzers are skipped at the target.

| Item | Why it's missed today | Reachable from the Zephyr tree alone? |
|---|---|---|
| ardep A1: `config CAN_MAX_STD_ID_FILTER` override in the board's `Kconfig.defconfig` | the target stops at `board.yml`; Kconfig is skipped | **yes**: the symbol is defined at v4.3.0 and gone at v4.4.0; the commit that removed it adds `CAN_STM32_FDCAN_MAX_STD_ID_FILTERS` |
| ardep A5 (rest): Mbed TLS symbols in `prj.conf` | same | the removed-symbol part, yes; the code port, no |
| ardep A6: `tf-psa-crypto` | same | **yes**: at v4.4.0 the mbedtls glue the build uses references `ZEPHYR_TF_PSA_CRYPTO_MODULE_DIR` |
| RIT F1a/b: `ti,tmp116` in the project's board DTS | the devicetree analyzer judges app overlays and C references, not a project board's DTS | **yes**: no binding has the compatible at v4.2.0; the guide and the directory rename give `ti,tmp11x` |
| RIT F2: `cmsis_6` in the user's allowlist | the scan was given `cmsis_6`, so nothing failed | from the build's own module list against the user's `west.yml` (§6), not the database |
| RIT F3, CoffeeCaller C3/C4, ardep A7 | no artifact | **no**: out of scope |

**Goal:** facts about what changed between two Zephyr refs that need **no target
build**, computed once per release pair, and used by scans to report and fix what the
skipped analyzers can't.

## 2. Sources, all from the mirror at the two refs

Each source records where every fact came from (commit, file:line).

### 2.1 Kconfig symbol table (lexical)

- **How:** `git grep -E '^\s*(menu)?config\s+NAME' <ref> -- '*Kconfig*'` gives each
  symbol's defining file:line. Probed at 15,375 symbols (v4.1.0) to 20,188 (v4.4.0), in a
  few seconds per ref.
- **Per symbol:** its prompt, help text and conditions are read from the defining block.
  Conditions are its `depends on` lines **and the enclosing `if … endif` blocks**, tracked
  lexically per file. `CAN_STM32_FDCAN_MAX_STD_ID_FILTERS` has no `depends on`; it sits
  inside `if CAN_STM32_FDCAN` (`drivers/can/Kconfig.stm32:58-76` at v4.4.0).
- **Diff:** removed and added symbols. The probes gave the right answer for all three
  held-out cases: `CAN_MAX_STD_ID_FILTER` (v4.3 → v4.4), `TMP116` (v4.1 → v4.2),
  `NATIVE_UART_0_ON_STDINOUT` (v4.3 → v4.4).
- **Not covered:** symbols Zephyr *generates* (`DT_HAS_*_ENABLED`, `ZEPHYR_*_MODULE`,
  board and SoC selections from `Kconfig.boards`/`Kconfig.soc`), and symbols defined in
  module trees. A removal is claimed **only** when:
  - the symbol's source-side definition is in the Zephyr tree;
  - the symbol isn't generated;
  - no module tree the scan uses defines it at the target (those checkouts exist in the
    workspace).

### 2.2 Removal pairing (the replacement)

**How:** for a removed symbol X:
1. `git log -S'config X' from..to -- '*Kconfig*'`;
2. in each commit, the `+config Y` lines of the same hunks are candidates.

Probes:
- `CAN_MAX_STD_ID_FILTER` (0.7 s): two commits, giving `CAN_STM32_FDCAN_MAX_STD_ID_FILTERS`
  (`38c32974214`) and `CAN_STM32_BXCAN_MAX_STD_ID_FILTERS` (`f2a6a191cf5`).
- `TMP116` → `TMP11X`: the driver directory `tmp116/` → `tmp11x/` is a rename that
  `git diff -M` sees through its `CMakeLists.txt` (63% similarity).

**Ranking:**
1. A candidate whose conditions (§2.1) name something the **source build enables** wins.
   ardep has `CONFIG_CAN_STM32_FDCAN=y`, so it gets FDCAN (`if CAN_STM32_FDCAN`), not bxCAN
   (`if CAN_STM32_BXCAN`).
2. Then name-token similarity.
3. Then the other sources: the deprecated symbol's help text (Phase 0), and guide renames.

**Confidence:**
- **Medium:** one candidate that the source build disambiguates.
- **Low:** several candidates left, all of which are listed.

### 2.3 Devicetree bindings table

- **How:** `compatible:` lines of `dts/bindings/**/*.yaml` at both refs give compatible →
  binding file.
- **Removed compatibles** pair with a replacement through:
  - git rename detection on the binding file;
  - the commit that removed it;
  - guide renames (`ti,tmp116 has been renamed to ti,tmp11x`, already parsed).

### 2.4 Module requirements

- **New projects** in Zephyr's `west.yml`. Probed: v4.3 → v4.4 adds `tf-psa-crypto`,
  `dhara`, `mbedtls-3.6` and others.
- **Glue references** at the target: `ZEPHYR_<M>_MODULE(_DIR)` mentioned in
  `modules/<x>/` for a module `<x>` the source build uses. Probed: the v4.4.0
  `modules/mbedtls/CMakeLists.txt:35` references `ZEPHYR_TF_PSA_CRYPTO_MODULE_DIR`.
- **Fact recorded:** "module M is needed at the target by glue of module X".

### 2.5 Migration guides

- Today's parsed renames, plus the "removed X in favor of Y" form, which isn't parsed yet
  (Golioth G3).
- Guide renames only *rank* candidates. On their own, without one of the sources above,
  they never make a finding.

## 3. The database

- **One JSON document per (from SHA, to SHA)**, generated on first use into
  `~/.cache/zmig/changes/<from>-<to>.json`, keyed like builds (zmig data version
  included).
- **Release pairs compose:** a scan crossing several minors reads each pair's entries.
  Chained renames resolve A → B → C, the AUDIT F9 gap.
- **Entry fields:** `kind` (`kconfig-removed`, `kconfig-renamed`, `compat-removed`,
  `compat-renamed`, `module-needed`), `old`, `new` (candidates), `sources` (commit,
  file:line, guide entry), `confidence`.
- **Curated overlay** (decision 3): `zmig/data/changes/<X.Y>-<X.Y+1>.json`, in the repo.
  Entries in the same shape:
  - they add or **correct** generated entries, matched by kind and old;
  - each carries a `checked` date and the commit it was checked against.
- **Inspection:** `zmig changes FROM TO [--json]` prints the database.

## 4. How scans use it: look up, never invent

A new build analyzer, `changes`. It needs only the **source** build's artifacts and the
usage index.

| What the project does | Looked up | Finding |
|---|---|---|
| sets `CONFIG_X` in a fragment the source build merged, or re-declares `config X` in its own Kconfig (A1) | `kconfig-removed/renamed` | `symbol-removed` (or `kconfig-override-orphaned` for a re-declaration), **the same kind and subject as the kconfig analyzer's**, with `fix` from the ranked candidate |
| uses compatible `C` in its own DTS or overlays, including a project board's DTS (RIT F1a) | `compat-removed/renamed` | `compatible-unbound` at the DTS line, with `fix` |
| builds with module X whose target glue needs M, which the scan lacks (A6) | `module-needed` | `module-required`, with the user-manifest location (as today) |

**No duplicates, no ID churn.** The analyzer reports a fact only when the analyzer that
normally reports it was **skipped** for that image (target artifact missing). It uses the
same kind and subject, so the **finding ID is the same** whether the target configured or
not, and `watch` baselines don't churn when a configure starts or stops failing.

When the normal analyzer did run, the database only feeds `fix` (Phase 0's renderer) with
better replacements. It never adds a finding then.

**Severity and confidence:**
- A removed symbol that the project sets is breaking. Its confidence is high: a definition
  that disappears from the tree is a git fact. Its `fix` is medium or low, per §2.2.
- Each finding has the heuristic tag `changes.lexical`, so the report says how it was
  derived.

## 5. Precision safeguards

1. **Generated and module-defined symbols are never "removed"** (§2.1).
2. **Only definitions are compared, not values or dependencies.** A symbol that exists but
   whose value would change is the kconfig analyzer's job, and needs a target `.config`.
3. **Ambiguity is visible.** Several candidates means low confidence, with all of them
   listed.
4. **Gate:** precision may not drop on any of the 14 harness configurations plus OpenDeck,
   and recall may not drop anywhere (PROPOSAL §6).

## 6. Separate, small: the user's allowlist against the build's modules

RIT F2 and Golioth/bms/CANnectivity's `cmsis_6` are a different check. The build used
module M (`zephyr_modules.txt`), and the user's own `west.yml` allowlist
(`modulecheck.user_manifest`) doesn't list it.

That's a manifest-consistency finding at the allowlist line, and it needs no database. It's
listed here because it closes F2. It ships as its own commit.

## 7. Measurement

| Project | Status | Expected (to be measured, not promised) |
|---|---|---|
| **ardep** | **clean**, the primary measure | A1: missed → caught, actionable and fix-actionable (`CAN_STM32_FDCAN_MAX_STD_ID_FILTERS` at `Kconfig.defconfig:17`). A6: missed → caught and actionable (`../west.yml`). A5a gains a `symbol-removed` at `prj.conf:25` |
| RIT FSW | tuned | F1a: `compatible-unbound` at the board DTS:202, with `ti,tmp11x`. F2: caught via §6 |
| CoffeeCaller | tuned | no change expected (its target configures; Kconfig ran) |
| The other 11 | tuned | the gate only; their targets mostly configure, so few new findings |

**Recording:**
- each item's label, actionable and fix-actionable, before and after;
- the database size and build time per release pair (PROPOSAL §7 asked for both);
- **every new breaking/risky finding gets a precision verdict.** This phase adds findings,
  unlike Phase 0.

## 8. Plan

Each step is one commit, test first, with re-scan deltas:
1. Kconfig symbol table + removal pairing + `zmig changes` (no scan change yet; the CLI
   shows the database).
2. The `changes` analyzer for Kconfig (sets and re-declarations), with the dedupe rule.
3. The bindings table, plus `compatible-unbound` for project DTS.
4. Module glue references, plus §6's allowlist check.
5. Measure: a DOGFOOD report, EVALUATION rows (ardep clean), then the `va_start` half of B3
   (ardep becomes tuned after its measurement).

## 9. Open questions

1. **Generated in the cache vs checked into the repo.** Generated per pair in the cache,
   with only the curated overlay in the repo, is what's proposed. Checking in generated
   databases for released pairs would make them reviewable but add churn.
2. **Removal pairing cost on large gaps** (2.7 → 4.4): one `git log -S` per removed symbol
   the project uses, which is at most dozens. This hasn't been measured on a large gap.
3. **"Removed" for a symbol a project only *reads*:** is it breaking (an `#ifdef` that is
   now always false) or risky? The kconfig analyzer says breaking (`read-symbol-removed`).
   Proposed: the same kind and severity, for consistency.
