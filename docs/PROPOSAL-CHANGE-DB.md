# Proposal: say where the fix goes and what it is (actions, a change database, mined fixes)

Status: **proposal, not implemented** (2026-10-30). Nothing here has been built.
Measurements are from the evaluation harness (`eval/`) and from read-only probes of the
Zephyr mirror.

## 1. The problem, measured

"Actionable" (DOGFOOD-3 §7.1) counts the in-scope oracle items where a scored finding's
`action` names a file the maintainers edited. A per-item audit of all 44 in-scope items,
using the current reports at `aa95a10`, gives this:

| | Items |
|---|---|
| Actionable today | **12 / 44**. That's 4 / 35 for scans: ZMK #5, bms B1, CANnectivity N2, OpenDeck D1. The other 8 are OpenDeck's patch triage |
| **B: the file and line are already in the finding's title or evidence, not in its action** | **15** |
| A: generic action, and the file isn't in the evidence either | 1 (ZMK #3) |
| C: detected in one file, but the fix belongs in a build or config file | 1 (ZMK #12) |
| D: no finding at all | 12 |
| E: other (wrong project files matched; one voluntary edit) | 3 |

**What dominates is the action template, not detection.** Most actions are one fixed string
per finding kind:
- "Check whether it points at a real problem."
- "Read MG-3.5-0025 and update the lines listed."
- "Find its replacement in the migration guides."

They're written once per kind, even when the finding carries `src/activity.c:73` and the
guide sentence that says `sys_poweroff()` replaces `pm_state_force()`.

For the 16 A+B items, here is what zmig already knows:

| | Items |
|---|---|
| The maintainers' full fix is derivable from what the finding already holds | 9: ZMK #2, #4, #9, #13; G1; B4, B5, B7; N1 |
| Partly derivable | 3: ZMK #1, #11; G3 |
| The file, but not the replacement | 4: ZMK #3 (a header rename), #14 (a Kconfig replacement), #17 (semantic), B6 (reason unknown) |

The 12 D items need artifacts that the scanned builds don't have. No change to actions or
data reaches them, so they are out of scope here (§8).

## 2. Two metrics, so this can't be won by naming files

§7.1 only asks that the action **name** the file. An action that repeats the evidence's path
would raise it mechanically, even when the suggested change is wrong. So every phase below
reports a second, stricter metric next to it.

**Fix-actionable:** an in-scope item where a scored finding's action:
- names a file the maintainers edited, **and**
- states the edit they made there, as the old → new text or construct, closely enough that
  applying it would compile or configure the way theirs did. Formatting differences don't
  count.

It is scored by hand against the oracle, like recall, and recorded per item in the DOGFOOD
report. My count today is **10 / 44**:
- ZMK #5;
- bms B1 and CANnectivity N2. "Add `cmsis_6`" configures like the maintainers' replacement of
  `cmsis`;
- OpenDeck P1, P2, P4–P7 (refresh the named patch) and P8 (drop it).

Not P9: it says "refresh", where the maintainers dropped the patch. Not D1: it names the
overlay but not the label renames. This count should be checked by a second scorer when the
metric is adopted.

Precision and recall stay the gate: **no phase may lower either on any harness
configuration.**

## 3. Phase 0: actions built from the finding's own facts (no new data)

**Change.** Every analyzer that reports a project file:line puts it into the action, along
with the concrete edit wherever the finding already holds both sides. A new optional finding
field carries it as data:

```json
"fix": {
  "file": "src/led.c", "line": 225,
  "old": "smf_set_handled(SMF_CTX(lctx));",
  "new": "return SMF_EVENT_HANDLED;",
  "source": "guide:MG-4.2-0137",
  "confidence": "medium"
}
```

`action` is then rendered from `fix`: "In src/led.c:225, replace `…` with `…` (migration guide
4.2, State Machine Framework)." A finding with no derivable fix keeps today's action, but
names its file.

**Per kind, the facts already exist:**

| Kind | Fix from |
|---|---|
| `callback-type-changed`, `function-changed` | old and new canonical types, and the definition sites (already cited) |
| `enum-removed`, `macro-changed` | clang's "did you mean", and the enum diff (`CFB_DISPLAY_HEIGH` → `…HEIGHT`) |
| `symbol-deprecated` / `symbol-removed` | a guide rename already parsed (`Entry.renames`), linked to the finding (B4) |
| `device-missing` | the node, plus the overlays the build applied (B5: "add `&uart1 { status = "okay"; };` to `boards/native_sim.overlay`") |
| Guide matches | the entry's full text, which already holds the remedy in N1, ZMK #9 and B5. The rename pattern is already parsed |
| `module-required` | say **replace** `cmsis` when the source manifest lists it (B1a, N2) |

**Expected effect on the harness** (to be measured, not promised):
- actionable from 12 to about 28 of 44;
- fix-actionable from 10 to about 19 of 44 (the 9 "full fix" items);
- **no change to recall or precision**, because Phase 0 adds no findings and changes no
  severity.

**Cost.** Analyzer-local, with one shared renderer. No new inputs.

## 4. Phase 1: a change database per release pair

Phase 0 runs out where the finding doesn't hold the replacement:
- header moves (ZMK #3: `hci_err.h` → `hci_types.h`);
- Kconfig replacements that no parsed guide sentence names (ZMK #1 `LV_Z_DPI` → `LV_DPI_DEF`,
  #14 `LV_Z_MEM_POOL_MIN/MAX_SIZE` → `LV_Z_MEM_POOL_SIZE`);
- renames that chain across releases (AUDIT F9: A→B in 3.7, then B→C in 4.1, reports "B").

**What it is.** A JSON document of changes between two Zephyr commits. Each entry carries:
- `kind` (`kconfig-rename`, `kconfig-removed`, `header-moved`, `api-replaced`, `dt-prop-renamed`,
  `module-required`, …);
- `old` and `new`;
- `release`;
- `sources` (evidence for the entry);
- `confidence`.

Sources, from strongest to weakest:

1. **Mechanical diffs.**
   - The Kconfig symbol table: removed symbols, and `select`/`imply` chains from deprecated to
     new symbols, as `rename_candidates()` does per scan today.
   - Header moves from `git diff -M --name-status old..new -- include`.
   - Binding properties.
   - Board renames (`deprecated.cmake`).
2. **The migration guides.** Today's parsed entries and renames, with their entry IDs.
3. **Mined adaptations** (Phase 2), never stronger than medium.

**Where it lives.**
- Generated on first use into the cache, keyed by `(from_sha, to_sha, zmig data version)`,
  like builds.
- A curated overlay in the repo, `zmig/data/changes/<X.Y>-<X.Y+1>.json`, holds corrections
  and additions, which a contributor edits as JSON (AUDIT F9).
- Pairs compose through the minors in between, so A→B plus B→C gives A→C.

**How scans use it.** Analyzers keep detecting exactly as today. They **look up** what they
found (a removed symbol, a missing header, a removed function) to fill `fix.new` and `source`.
**The database never creates or demotes a finding**, so precision and recall can't move
because of it. Only actions improve.

**Commands.**
- `zmig changes OLD NEW` prints or exports the database for inspection.
- `--changes FILE` adds a user overlay to a scan.

**Schema.** `fix` is a new optional finding field, so it means schema 1.5 (additive; DESIGN
§3 practice). `watch` fingerprints don't include actions, so nothing gates differently.

## 5. Phase 2: mining how Zephyr adapted its own code

For a token that zmig reports as removed or changed, Zephyr's history between the two refs
usually contains the same adaptation, applied to upstream's own callers.

**Algorithm.**
1. `git log -S<token> old..new -- samples tests drivers subsys boards lib modules`.
2. In those commits' hunks, take the removed lines containing the token, and the added lines
   of the same hunk.
3. Count the tokens or constructs that are added but not removed, across files.
4. Also count which *kinds of file* the commits touched (`CMakeLists.txt`, `Kconfig`,
   `prj.conf`, overlays). That answers category C: where the fix goes when it isn't where the
   breakage shows.
5. Rank by the number of distinct files that agree.

Medium confidence needs at least 3 agreeing files and no rival above half its count;
anything less is low. A mined entry is never high confidence.

**Probes on the real mirror** (read-only; one token each):

| Oracle item | Token, refs | Time | What the upstream adaptations show | Maintainers' fix |
|---|---|---|---|---|
| CANnectivity N1 | `smf_set_handled`, v4.1.0..v4.2.0 | 2.0 s | one commit, 23 files. `smf_set_handled(SMF_CTX(obj)); break;` → `return SMF_EVENT_HANDLED;`, and `static void X_run(void *o)` → `static enum smf_state_result X_run(void *o)` with `return SMF_EVENT_PROPAGATE;` | the same |
| ZMK #3 | `hci_err.h`, v3.2.0..v3.5.0 | 4.5 s | 4 commits. Added includes: `hci_types.h` ×55; removed: `hci.h` ×49, `hci_err.h` ×5 | `hci_err.h` → `hci_types.h` |
| Golioth G1 | handler signature, v4.1.0..v4.2.0, samples | 0.4 s | `uint32_t` → `uint64_t` in the callbacks' event parameter | the same |
| ZMK #5 | `zephyr_syscall_header`, v3.2.0..v3.5.0 | 4.1 s | the commits touched `CMakeLists.txt` ×51, `Kconfig` ×4, `prj.conf` ×1 | `app/CMakeLists.txt` (+ `prj.conf`) |

These are four probes chosen because the answer was known, not a measurement. The ZMK #3 row
shows the noise problem: `hci.h` → `hci_types.h` outnumbers the `hci_err.h` pairs. Ranking has
to pair each removed line with the added lines of its own hunk, not count globally.

**Cost.** 0.4–4.5 s per token over a minor-release range. That's acceptable once per pair,
and too slow per scan for dozens of tokens. Hence mining runs while building the database
(Phase 1) and is cached with it.

**Limits.**
- **Module code isn't in the Zephyr mirror.** LVGL's options (ZMK #1, #14) live in Zephyr's
  `modules/lvgl` glue, which *is* mined, but changes inside LVGL itself aren't.
- **History is only as good as upstream's own migrations.** A change no upstream caller had
  to make gives nothing.

## 6. How to measure (the harness)

Each phase is merged only with a DOGFOOD report that includes:
- `eval/run.py scan` + `compare` on all 11 configurations, and OpenDeck through
  `dogfood/d6/run.sh`;
- per item: actionable and fix-actionable, scored by hand, with before/after;
- **recall and precision unchanged or better on every configuration**. Any regression blocks
  the merge;
- the clean/tuned label. Every current configuration is tuned, so gains on them show
  feasibility, not generalization.

**For a clean measurement, the oracles of 2–3 new held-out projects are frozen before
Phase 0's code is written** (ORACLE-RULES §1.4). Their actionable and fix-actionable before
and after are the numbers to quote.

## 7. Risks

- **Confidently wrong fixes do more damage than generic text.** Mitigations:
  - `fix.confidence` is capped by its source: high only for mechanical facts at both refs;
    medium for guides, mined fixes and evidence-linked ones;
  - every `fix` cites its source;
  - `zmig fix` keeps applying only Kconfig-level evidence (P3's rule) and never a mined
    fix.
- **Goodhart on actionable:** §2's fix-actionable metric, scored against the maintainers'
  actual edit.
- **Data drift:** the curated overlay can go stale. Generated entries are recomputed per pair,
  and overlay entries carry a `checked` date plus the commit they were checked against.
- **Size:** a pair's database is expected to be a few thousand entries (symbols, headers,
  guide items). This hasn't been measured; Phase 1 must report it.

## 8. Out of scope

- **The 12 D items:** no artifact in the scanned build, target configure blocked, or semantic
  knowledge (buffer sizes, LVGL heap use). They need new evidence sources, not better
  actions.
- **Changing what is detected or its severity.** This proposal only changes what a finding
  says to do.
- **Auto-applying fixes** beyond `zmig fix kconfig`'s existing scope.

## 9. Open questions for the maintainer

1. **Adopt fix-actionable** as a reported metric next to actionable (§2)? Its baseline,
   10 / 44, needs a second pass by someone other than me.
2. **The `fix` field** (schema 1.5, additive) or **action text only** (no schema change, but
   nothing machine-readable for `zmig fix` or agents)?
3. **Where the curated overlay lives:** in the repo, per release pair, or as user-supplied
   files only?
4. **Which new held-out projects** to freeze oracles for before Phase 0 (§6)?
5. **ZMK #3's label**, which this audit surfaced (DOGFOOD-7 open question). It doesn't block
   this proposal, but changes the strict-recall baseline it is measured against.
