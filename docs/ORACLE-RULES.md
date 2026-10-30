# Oracle and scoring rules

These rules say how a zmig run is scored against a project's real upgrade history. They were
written on 2026-10-28, before zmig was run on the held-out projects (Golioth, bms-firmware),
and they apply to every dogfood report from DOGFOOD-2 on. ZMK (DOGFOOD-1) is re-scored under
them in DOGFOOD-2-baseline.md.

## 1. The oracle

The oracle for an upgrade is the list of changes the project's maintainers made because of
it, read from their commits. It is written and committed **before** zmig is run on that
project, in `docs/oracles/<project>.md`.

### 1.1 Items

- One **item** is one logical change. If a commit makes two unrelated changes, they are two
  items. If several commits make one change (a fix and its follow-up), that's one item.
- An item may have **parts**: the same change applied in several places, such as a Kconfig
  rename used in three files. Parts are listed under the item.
- Docs, CI configuration, changelogs, formatting and version-number bumps of the project
  itself aren't items. A bump of a *dependency* (a module, a submodule) is an item.

### 1.2 Scope

Each item, and each part, is marked **in scope** or **out of scope** for the build that is
scanned (board, app, extra conf/overlay files, modules), with a one-line reason.

An item is in scope if, in the scanned build at the source ref or the target ref, the
change touches any of:

- a file that is compiled (`compile_commands.json`) or included (ninja deps);
- a Kconfig, `.conf`, devicetree, overlay or binding file that is parsed;
- a Kconfig symbol, devicetree node or property that this build evaluates;
- a module or submodule that this build uses.

Otherwise it's out of scope. Typical reasons: another board, a shield or config file not in
the scanned build, or a test or sample that isn't built.

A part is scored only if it is in scope. An item with no in-scope part is out of scope.

When several build configurations of one project are scanned (DOGFOOD-1 scanned ZMK with
and without the nice!view shield), the scope is their union, and a finding from any of
those runs counts.

### 1.3 Revealing artifact

Each item records **the artifact that would reveal it**: the evidence a tool could use to
flag the change in advance. Examples:

- a compiler error or warning in the target build, at a given file:line;
- the Kconfig tree at the target (a symbol removed, a default changed);
- the generated devicetree;
- a header declaration at both refs (a signature change);
- a migration-guide entry;
- nothing available before run time.

This is what the maintainers' change shows, not what zmig does. It's recorded so a miss can
be told apart: zmig either had the evidence and didn't use it, or no build artifact had it.

### 1.4 Freezing

Oracle files aren't edited after zmig has been run on the project. If a scope call or an
item turns out wrong, a dated **correction** is appended at the end of the file. It says
what changed and why, and the original entry stays. Scores are reported both under the
original oracle and with the corrections applied, whenever a correction changes them.

## 2. Scoring an item

Each in-scope item gets one label: the best label any single finding (or set of findings,
for a multi-part item) earns. Only findings emitted by the scored run count. Reasoning by
hand from zmig's raw outputs (logs, snapshots) doesn't count.

### Caught

A breaking or risky finding that meets both of these:

1. **Location.** Its evidence points at the project's own file and line, or at the project's
   own symbol, that the maintainers changed. For a dependency bump, it names the dependency.
2. **Cause.** It states why that location breaks or changes at the target, accurately
   enough that someone reading only the finding would know what to change. A compiler
   error that names the missing header, symbol or type counts. The finding doesn't have to
   give the replacement.

For a multi-part item, every in-scope part must meet both conditions. They can be met by
different findings.

### Partly caught

At least one of these is true, and the item isn't caught:

- **(a) Info only.** A finding meets both conditions, but its severity is info, including a
  specific mention in a bulk info list.
- **(b) Symptom only.** A breaking or risky finding is at the right location, or names the
  right symbol, but its cause is missing or wrong. For example, a build-failed finding
  reporting a missing generated header without the reason it's missing.
- **(c) Cause only.** A finding states the right cause (for example, the right
  migration-guide entry), but its evidence points elsewhere: a Zephyr header, or project
  code the change doesn't touch.
- **(d) Some parts.** Some, but not all, in-scope parts of a multi-part item are caught or
  partly caught.

### Missed

Nothing above applies.

### How "partly" counts

- **Strict recall** = caught ÷ in-scope items.
- **Lenient recall** = (caught + partly caught) ÷ in-scope items.

"Partly" counts as one whole item in lenient recall and zero in strict recall. There's no
half credit. Every report gives both numbers, plus the counts behind them and, for each
partly-caught item, the letter (a)–(d) that applies.

Out-of-scope items are never in the denominator. If zmig flags one anyway (it scans the
whole app tree for some references), the report lists it separately as an out-of-scope
catch.

## 3. Precision

Every **breaking** or **risky** finding in the scored run is checked against the source at
both refs. Info findings aren't scored. Each gets one verdict:

- **Confirmed hazard:** the finding's claim is true at the source and target refs, and it
  affects this build: the code is compiled, the symbol is evaluated, the data persists.
  This is noted as "in the oracle" (it matches an item) or "not in the oracle" (a real
  hazard the maintainers didn't change, with the evidence).
- **Harmless:** the claim is false, or it's true but can't affect this build. For example,
  the changed API isn't called, or the matched text is in a test harness.
- **Unverifiable:** checking it would need run-time or hardware behaviour, or information
  that isn't in either tree.

**Precision** = confirmed ÷ all breaking and risky findings. Unverifiable findings are in
the denominator and not the numerator. Reports also give the counts, so precision without
the unverifiable findings can be worked out.

A finding is one finding ID. Findings that repeat the same claim at different locations
count separately. That's deliberate: duplicates cost the reader time.

## 4. What every report records

For each project and upgrade pair:

- the zmig commit, refs, board, app, modules and SDK;
- strict and lenient recall (in scope only), with counts, and out-of-scope catches;
- precision, with each finding's verdict;
- build failures: which image, which ref, and why;
- wall-clock time, split into first run (cold caches) and repeat run when both exist;
- the field-upgrade verdict and its confidence;
- which known limitations affected the result.

## 5. Actionable and fix-actionable (added 2026-10-30)

These are reported next to recall, and replace neither. Evidence is never changed to
improve them: the evidence is what proves the hazard.

- **Actionable** (DOGFOOD-3 §7.1): an in-scope item where some finding scored for it (caught
  or partly) has an `action` that names a file the maintainers edited for that item, by
  path or basename.
- **Fix-actionable**: an actionable item where that action also states the edit the
  maintainers made in that file, as the old → new text or construct. It must be close
  enough that applying it would build or configure the way their edit did; formatting
  differences don't matter. A `fix` field (PROPOSAL-CHANGE-DB §3) counts as part of the
  action.

Both are scored by hand, per item, against the oracle. Every report gives the counts and
the items behind them.

