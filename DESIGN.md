# zmig design

zmig tells a developer what a Zephyr upgrade changes **for their project**. It builds the
project at both Zephyr versions and intersects the upstream change set with what the
project actually builds and uses. Every finding comes from a real artifact (a resolved
`.config`, `edt.pickle`, an ELF, an SBOM, a build log, git objects, the target tree's own
docs) and can be reproduced from the inputs the report records. No LLM is involved.

This document describes how zmig works and why. The [README](README.md) covers usage;
the evidence behind the decisions (oracles, measurements, per-project results) is in
[eval/](eval/).

## Principles

- **Scoped to the project.** An upstream change matters only if the project sets, reads,
  references, includes, compiles or links what changed. Everything else is counted, not
  reported.
- **Evidence, not inference.** Findings come from build artifacts and git. Where a rule
  needs a starting point Zephyr doesn't expose (seed identifiers, the LTS list, a
  wire-format allowlist), it lives in a small cited data file under `zmig/data/`, and each
  entry is verified at both refs.
- **Each ref's own tools.** Kconfig, devicetree, init order and board renames are read with
  the code of the Zephyr version that produced them, so results match that version's
  build.
- **Never touch the user's tree.** zmig reads the user's repository and writes only to its
  cache. Builds and salvage work on copies. `fix --apply` is the one exception, and only
  on a new branch of a clean tree.
- **Not checked is never clean.** An analyzer that can't run is `skipped` or `error` with a
  reason; inputs it couldn't interpret are `not_checked` entries and make it `partial`.
  The field-upgrade verdict starts as `unknown` and is earned category by category.
- **Look up, never invent.** Secondary sources (guide renames, help text, the change
  database) rank and fill in replacements. They create findings only where the database
  stands in for an analyzer the target build couldn't feed, with the same finding
  identity.
- **Say how it was derived.** Every contributing heuristic is named on the finding, and a
  finding's confidence is never higher than its weakest source.
- **Additive schema.** New report fields are optional; old reports keep validating.
- **Regressions become tests.** A false positive or negative found on a real project gets
  a test, and the code comment at the fix names the case.

## Architecture

### Pipeline

```
 --from/--to → refs.py        resolve tag/SHA/branch/path → Ref (sha, kind, dirty)
 workspace.py   per-ref west workspace in the cache: zephyr worktree + needed modules
 buildctx.py    isolated configure/build per (ref, board, app, options) → BuildContext
                with one ImageContext per image; salvage; module recovery
 snapshots.py   extract/ helpers run with that ref's kconfiglib, edtlib,
                check_init_priorities.py → JSON snapshots cached in the build
 scan.py        ScanContext per image pair; usage.py: what the project references
 analyzers/     context → AnalyzerResult(findings, status, not_checked, checked)
 fixes.py       where each fix goes (`fix`) and the rendered `action`
 tiers.py       act-now / review / fyi
 verdict.py     field-upgrade verdict from the hazard categories
 findings.py    Finding, Report, stable IDs, ordering
 report/        json | markdown (MIGRATION.md) | human | sarif
```

### Commands

| Command | What it does |
|---|---|
| `plan` | workspace triage without building: upstream `west.yml` diff, forks and local patches, release hops |
| `scan` | builds the app at both refs and runs the build analyzers |
| `fix kconfig` | a scan, then renames or comments out Kconfig assignments with Kconfig-level evidence |
| `watch` | a scan compared with a committed baseline: CI exit codes, SARIF, PR comment |
| `changes` | prints the change database for a ref pair (no build) |
| `doctor` | checks host tools, Python packages and the SDK/toolchain a board needs |
| `verify` | stub; not implemented |

### Package layout

| Module | Role |
|---|---|
| `cli.py` | argparse, `scan_report()` (the scan driver), `plan`, `changes` |
| `refs.py`, `workspace.py`, `manifest.py` | refs, mirrors, worktrees, dirty snapshots; per-ref workspaces; manifests through west's API |
| `buildctx.py`, `modulecheck.py`, `diagnostics.py` | builds, images, keys, salvage, SDK checks; modules a build needs; facts from configure logs |
| `snapshots.py`, `extract/` | `.config`, Kconfig and EDT dumps, init order, Kconfig re-resolution (`kconfig_try`), ninja deps |
| `usage.py`, `scan.py` | `ProjectUsage`; `ScanContext` |
| `boards.py`, `sysbuild_rules.py`, `guides.py`, `changes.py` | board renames; sysbuild option rules; guide parser; change database |
| `apidiff.py`, `abitools.py` | libclang extraction; optional `pahole`, `abidiff` |
| `srcdiff.py`, `signed_image.py`, `hazards.py`, `verdict.py` | field-upgrade machinery |
| `forks.py`, `plan.py` | fork classification; `PlanContext` |
| `fixes.py`, `fixkconfig.py`, `fixcmd.py`, `tiers.py` | the `fix` field; `zmig fix kconfig`; tiers |
| `watch.py`, `watchcmd.py` | baseline comparison (pure, on report JSON); the command |
| `sbom.py`, `size.py`, `tools.py` | SPDX reader; ROM/RAM (pyelftools); logged subprocess wrapper |
| `findings.py`, `schema/report-1.json`, `report/` | data model, JSON Schema, renderers |
| `data/` | `releases.json`, `hazard_seeds.yaml`, `wire_format.yaml` |

### Cache layout

Root: `--cache-dir`, else `$ZMIG_CACHE`, else `$XDG_CACHE_HOME/zmig` (`~/.cache/zmig`).

| Path | Holds |
|---|---|
| `mirrors/<url-slug>.git` | bare mirrors cloned fresh from each upstream URL (Zephyr, modules) |
| `workspaces/<ref key>/` | `zephyr/` (a worktree of the mirror), `.west/config`, module clones, `apps/<tree hash>/<app>/` (app copies) |
| `builds/<key>/`, `builds/<key>.json` | build directories; the marker records the key's inputs. Snapshots sit in each image directory as `zmig-<name>.json` |
| `salvage/<key>/<app>` | the salvaged app copy |
| `snapshots/<digest>` | uncommitted changes of a dirty path ref |
| `changes/<from12>-<to12>-v<N>.json` | change database per ref pair |
| `api/<key>.json` | libclang parse results, keyed by content |
| `sdks/` | one place `doctor` looks for SDKs |

A process holds an exclusive lock on each workspace it uses until it exits: the active
module set is workspace state, and two runs with different module sets would otherwise
build against each other's.

### Tools and dependencies

Every subprocess goes through `tools.Runner`, which logs argv, cwd, environment overrides,
exit status, duration and output under `<out>/logs/` and records each invocation in
`logs/invocations.json`. Both are redacted: the cache root, home and app path are
replaced with `$ZMIG_CACHE`, `~` and `<app>`. Discovery order: explicit flag,
`$VIRTUAL_ENV`, `<workspace>/.venv`, `PATH`; a missing tool is a one-line error saying what
to do. Arguments are checked before the environment, so a wrong app path isn't reported
as a missing `dtc`. `PYTHONPATH` is removed from every command zmig runs.

Python 3.12+ (Zephyr's floor) and `west` at run time. kconfiglib and edtlib come from each
ref's tree. libclang is the optional extra `zmig[api]`; `pahole` and `abidiff` are optional.

## Findings and reports

### The finding

```json
{"id": "KCONFIG-SYMBOL-REMOVED-3f9a1c2e", "category": "kconfig", "kind": "symbol-removed",
 "subject": "NATIVE_UART_0_ON_STDINOUT", "title": "CONFIG_…, set in prj.conf, no longer exists in v4.4.0",
 "severity": "breaking", "confidence": "high", "old": "y", "new": {"candidates": […]},
 "evidence": [{"type": "file_line", "path": "prj.conf", "line": 4, "text": "…"},
              {"type": "tool_output", "invocation": "target configure", "excerpt": "…"}],
 "action": "In prj.conf:4, replace … with ….", "related": ["GUIDE-ENTRY-MATCH-…"],
 "analyzer": "kconfig", "heuristics": [], "introduced_by_migration": true, "tier": "act-now",
 "fix": {"file": "prj.conf", "line": 4, "old": "…", "new": "…", "source": "…", "confidence": "medium"}}
```

| Field | Rule |
|---|---|
| `id` | `{CATEGORY}-{KIND}-{hash8}`, sha256 over (category, kind, subject, scope). The scope is the comparison: both SHAs, the board, and the image for non-app images. Same inputs, same ID |
| `category` | `manifest fork hop board kconfig devicetree build guide sbom images crossimage init api sysbuild layout mcuboot storage ble wire` |
| `old`, `new` | opaque, per kind |
| `evidence` | `file_line`, `artifact_diff`, `git_commit`, `git_conflict`, `doc_entry` (doc, line, ref, entry id), `tool_output` (invocation, excerpt, optional log and line) |
| `related` | IDs about the same item; made symmetric after all analyzers run |
| `image` | the sysbuild image; absent means the app image or the whole build |
| `introduced_by_migration` | false when the source build already had it ([below](#pre-existing-problems)) |
| `remedy`, `provenance` | hazard findings ([Field-upgrade verdict](#field-upgrade-verdict)) |

### Severity, confidence, heuristics

- **breaking:** the build fails, or something the project relies on is gone: a removed
  symbol it sets, a removed node label it references, an assignment that no longer takes
  effect, a device that can't take the update.
- **risky:** it builds, but behavior may differ: a changed value the project reads, a new
  warning in project code, an upstream change to an included header, ROM/RAM growth
  above +1 KiB and +5%.
- **info:** context: bulk lists of symbols the project doesn't touch, a repinned module, a
  pre-existing assignment that never took effect.

Confidence is **high** when read from a resolved artifact, the build's own message or git
identity; **medium** for a structured match that relies on naming (a Sphinx role, a
subject-line commit match, help text, a compiler's "did you mean"); **low** for a text
heuristic (a guide literal, a token scan, one candidate among several). `heuristics` names
each contributor (`guide.token-match`, `kconfig.rename.medium`, `changes.lexical`, …).

**Outside the build.** After the analyzers run, a breaking or risky finding whose only
evidence is in configuration or devicetree files no image used (another board's `.conf`),
or in project sources the build doesn't compile, becomes info, says so in its title, and
gets the `outside-build` heuristic. Use comes from the build's records: merged fragments,
`zephyr.dts.d`, ninja dependency records.

### Pre-existing problems

**Diff findings** (something changed) are introduced by construction. **Condition
findings** (checkable on one build: cross-image consistency, slot sizes, Kconfig and
devicetree diagnostics) are evaluated on both builds: present only at the target is
introduced; present and unchanged at both is `introduced_by_migration: false` with
"pre-existing" in the title; present at both but different is introduced. Two
diagnostics are the same when symbol, node and values match, ignoring "defined at" lines
and paths.

Severity isn't lowered for pre-existing problems, except where nothing the app relies on
changes: a `value-not-taken` the source configure printed identically is info, and so is
a `module-required` the source build needed too (`new.required_at_source: true`): the
scan's module set lacked it, not the app.

### The `fix` field

`fixes.apply_fixes` runs after the analyzers, sets `fix = {file, line, old, new, source,
confidence}` and renders `action` from it. `file`/`line` is the first project location in
the evidence. `old`/`new` are set only when the finding, or one related to it, already
holds both sides:

| Kind | Edit from |
|---|---|
| `symbol-removed`, `symbol-deprecated`, `read-symbol-removed`, `kconfig-override-orphaned` | a candidate with Kconfig-level evidence or from the change database, or a related guide entry's rename |
| `compatible-unbound` | the database's binding replacement, on the line naming the compatible |
| `value-not-taken` | the one new, settable dependency that's off: `CONFIG_<dep>=y` |
| `include-missing` | the `#include` from the source build's include chain |
| `entry-match` | the guide's parsed rename, on a line containing the old name |
| `callback-type-changed` | definition site, its type, and the expected type |
| `enum-removed`, `target-build-failed` | the compiler's "did you mean" |
| `device-missing` | the node's label as a snippet for an overlay the build applies |

The pass never adds, drops or re-grades a finding, so it can't move recall or precision
(tested). `source` names the facts; `confidence` never exceeds theirs (a guide rename or
compiler suggestion is at most medium).

### Tiers

Each finding gets a `tier`, for grouping output only: the verdict, exit codes, watch
fingerprints and `summary.top` ignore it. Inputs are run-time facts only,
`tier(severity, confidence, evidence, use)`; evaluation labels never are.

| Tier | Rule |
|---|---|
| `act-now` | breaking, high confidence, with a project `file:line` the scanned build used |
| `review` | any other breaking finding; risky with a project `file:line` the build used |
| `fyi` | everything else |

"Used" is decided per file kind from the build's records (ninja deps for sources and
headers, overlays applied, fragments merged). Other files (Kconfig, CMake, YAML), or a
build without records, count as used: a finding is demoted only when the build shows the
file wasn't used. One exception: a breaking, high-confidence `module-required` whose
evidence includes the target build's own failure is act-now without a project location,
because the failure shows it blocks the upgrade. Tested invariants: exactly one tier per
finding, every finding still in its usual section, no breaking finding ever fyi,
order-independent.

### Report envelope

```json
{"schema": "https://zmig.dev/schema/report/1", "schema_version": "1.5", "zmig_version": "…",
 "command": "scan",
 "inputs": {"from": {"spec", "sha", "kind", "dirty"}, "to": {…}, "app", "app_tree_hash",
            "board": {…mapping…}, "build_mode": "full", "sysbuild": false},
 "environment": {"python", "west", "cmake", "dtc", "libclang", "sdk"},
 "contexts": [{"ref", "board", "status", "key", "images": […]}, {"salvage_of", "salvage_edits"}],
 "analyzers": [{"name": "kconfig", "status": "ok", "findings": 4, "hazards": 2}, …],
 "summary": {"counts": {…}, "top": […]}, "findings": […],
 "upgrade_path": {…}, "not_checked": […], "checked": […]}
```

- **Analyzer status:** `ok`, `partial`, `skipped` (with the producing step's reason) or
  `error`. Per-image runs in a multi-image build are named `name@image`.
- **`checked`** records what was examined and the result (`clean`, `found`, `partial`),
  so "ran, found nothing" differs from "didn't run".
- **`top`:** the first five breaking or risky findings; introduced ones first, then
  breaking findings with evidence in the user's files.
- **Order:** findings by (severity, confidence, category, id).
- **Schema history:** 1.1 `image`; 1.2 `introduced_by_migration`, `remedy`, `provenance`,
  `upgrade_path`, `not_checked`, `environment.sdk`, hazard categories; 1.3 `checked`; 1.4
  not-applicable categories need evidence; 1.5 `fix`. `tier` was added without a bump.
  Tests validate every report against `zmig/schema/report-1.json`.

### Outputs

- **`MIGRATION.md`:** versions, board(s), mode, counts, "Do these first", the
  field-upgrade verdict, "By tier", per-category sections (per image when there are
  several) as `- [ ]` lines with action and linked evidence, "Already true of the source
  build", heuristic and low-confidence findings, Checked, Not checked, Analyzers, Builds
  (with salvage edits).
- **`human`** (default stdout): the top five, every breaking and risky finding with its
  first location in the user's files, analyzer problems.
- **SARIF 2.1.0:** only findings with `file_line` evidence in the user's own files, paths
  relative to `APPROOT` (the app for `scan`, the workspace for `plan`); rule id
  `category/kind`; level error/warning/note by severity.
- **`--out DIR`:** `report.json`, `MIGRATION.md`, `report.sarif`, `logs/`.

### Exit codes

| Command | Codes |
|---|---|
| `scan`, `plan`, `changes` | 0 ok; 1 tool or internal error; 2 usage error. Findings never change it |
| `fix kconfig` | as above; 2 also for `--apply` on a dirty tree; 3 when the rebuilt target has a finding the fix introduced |
| `doctor` | 0 if no check failed, else 1 |
| `watch` | 0–4 ([CI mode](#exit-codes-1)) |

An interrupted run exits 130.

### Determinism

`report.json` holds no timestamps, absolute cache paths or invocation lists, so it is
byte-stable for identical inputs, cache warm or cold. Paths are relative to the app or
workspace, and tool messages quoting a built app copy say `<app>`. Invocations go to
`logs/` instead, since a cache hit changes them. Analyzers are pure functions of their
contexts and never run builds.

## Builds

### Ref resolution and isolation

`--from`/`--to` take a tag, SHA, branch or local path (a git checkout, or an *untracked
tree* without `.git`). `git worktree add` against the user's repo would write into their
`.git/worktrees`, so:

1. zmig keeps a **cache-owned bare mirror** per upstream URL, cloned fresh, fetched later.
2. A path ref is read with `git fetch <path>` (read-only on the source) and
   `--no-optional-locks`, so not even the index stat cache is refreshed.
3. Worktrees come off the mirror at `workspaces/<ref key>/zephyr`; a marker records the
   ref key once checkout completes, so an interrupted one is redone.
4. Uncommitted changes in a path ref are snapshotted, applied to the copy, and recorded
   (`dirty: true`; the ref key gains the diff's hash).

### Per-ref workspaces

A build at ref X needs modules at X's manifest revisions. The workspace is laid out like
`west init -l` (worktree at `zephyr/`, `.west/config` pointing at its `west.yml`), and
manifests are loaded through west's own API, so imports, allowlists and groups resolve as
west resolves them. Modules are cloned on demand with `west update --narrow <names>`: the
`--module` list plus whatever the build turns out to need. Clones stay cached, so reruns
work offline. `--shallow` fetches at depth 1, about 5× fewer bytes, but without module
history fork triage of modules is listed as not checked.

### Build contexts and cache keys

A build is `west build -p always -b <board> -d <dir> <app copy> -- <cmake args>` in the
ref's workspace, with `ZEPHYR_BASE` set and `ZEPHYR_TOOLCHAIN_VARIANT=host` for host
boards. The key hashes:

| Input | Why |
|---|---|
| ref key and every module's (name, sha) | the source tree |
| board, mode, sysbuild, CMake args, SDK path and version | the build |
| app content hash and directory name | the name is in ninja records and image names |
| zmig version, `RECIPE_VERSION` | recipe changes invalidate builds |
| interpreter, venv, Python version, digest of installed packages | a build that failed for want of a package (imgtool needs `cryptography`) must not be served from cache once it's installed |

A failed build is a valid cached outcome: a target configure error is itself what to
report. Markers record the key inputs so a miss can be explained (`miss_reason`).

**Modes:** `full` (default) configures and builds (ELF, map, objects, warnings, SBOM);
`--fast` only configures, so analyzers needing build outputs are skipped and guide
identifiers fall back to a token scan.

**Artifacts:** `ImageContext.path(Artifact)` locates `.config`, `zephyr.dts`, `edt.pickle`,
`compile_commands.json`, `zephyr_modules.txt`, `CMakeCache.txt`, ELF, map, SPDX and
`build.ninja`. ELF and map follow `CONFIG_KERNEL_BIN_NAME` (ZMK's is `zmk.elf`). A
`.config` alone isn't a completed configure (CMake can fail after Kconfig), so Kconfig
snapshots also require `build.ninja`.

### Building from a copy

The app is copied to `<workspace>/apps/<tree hash>/<app name>` and built there. Zephyr's
scripts search upward from the app for the west workspace (`west spdx` does), and an app
inside another west workspace gave an empty SBOM; a copy inside zmig's workspace isolates
those lookups. Any `<workspace>/apps/<hash>/<name>/` path in a log, including a reused
build of another copy, maps back to the app (`scan.from_copy`).

### Images and sysbuild

A build is a list of images; `ImageContext` holds name, role (`app`, `bootloader`,
`other`) and how it was decided, build directory, its own log, and status. A plain build
has one image. Under sysbuild, images, directories and flash order come from
`domains.yaml` (no directory name assumed; a configure that died before writing it falls
back to the directories CMake created). The default image is `app`; an image whose own
`.config` has `CONFIG_MCUBOOT=y` is `bootloader`. Images pair across refs by role, then
name.

The build runs in steps so every line belongs to the image that printed it:
1. **Configure** with `--cmake-only`, output merged in order and split at sysbuild's
   "Running CMake for <image>" banners (without banners, by which build directory a
   segment mentions; text before the first banner is sysbuild's).
2. **Build each image** with `cmake --build`, in flash order, into its own log.
3. **Sysbuild finish** (sign and merge), then **SPDX per image**.

### SDK selection

`--sdk` sets `ZEPHYR_SDK_INSTALL_DIR` and `ZEPHYR_TOOLCHAIN_VARIANT=zephyr`, removes
`Zephyr-sdk_ROOT`, `Zephyr-sdk_DIR` and `CMAKE_PREFIX_PATH`, and passes
`-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF`. CMake silently skips a hinted SDK that fails
Zephyr's version check, so after configure each image's "Found toolchain: zephyr <ver>
(<path>)" line must name the selected SDK, or the run fails.

### SBOM

zmig writes the empty CMake file-API query before configure (what `west spdx --init`
does on every version) and sets `-DCONFIG_BUILD_OUTPUT_META=y` on both builds, keeping it
out of the diff. After a successful build it runs that ref's `west spdx`. The exit status
isn't trusted (v3.6 exits 0 after "bailing"): a missing SBOM is reported with spdx's own
error. A target that doesn't compile has no SBOM.

### Salvage

Zephyr turns Kconfig warnings and binding violations into configure errors, so a real
migration's target configure usually fails. zmig maps each of Zephyr's messages to the
app line behind it, disables exactly those lines in a copy (`# zmig-salvage: …`), and
configures again, up to 4 rounds. Under sysbuild each image's log is read, so `prj.conf`
and `sysbuild/mcuboot.conf` are edited for their own images.

Analyzers compare against the salvaged build (`SalvageResult.effective`). The raw target
build (`new_raw`) is kept: diagnostics, `kconfig-override-orphaned` and
`input-not-applied` come from it, as the user would see them. The report lists every
edit. Salvage edits only the app's own files; a failure in a board, module or generated
file can't be salvaged, which is always a `not_checked` entry.

### Module discovery

When the target build fails, `modulecheck.diagnose` reads the failure for modules the
scan lacks: a missing header shipped by `zephyr/modules/<glue>/` for an inactive module
(`cmsis_core.h` with cmsis active means cmsis_6 is missing); an unmet dependency on
`ZEPHYR_<X>_MODULE`, or a glue symbol whose dependencies reduce to `&& 0`; a CMake error
naming `ZEPHYR_<X>_MODULE(_DIR)`. Only modules the target `west.yml` declares are
proposed. The scan adds them, rebuilds the target and reports `module-required`. If the
source build's failure names the same module, the source is rebuilt with it too, so both
sides compare like for like. Superseded contexts stay in the report.

### Snapshots

Helpers in `zmig/extract/` run in a subprocess with `sys.path` pointing into **that
ref's** `scripts/`: two kconfiglib or edtlib versions can't share a process, and
`edt.pickle` is only valid for the edtlib that wrote it.

| Snapshot | How |
|---|---|
| `.config` values | parsed directly |
| Kconfig symbols: type, prompt, values, depends, selects, implies, defaults, choice, defining file:line, deprecated | `kconfig_dump.py` |
| devicetree: nodes, labels, status, compatibles, properties, bindings and property specs, chosen, ordinals | `edt_dump.py` from `edt.pickle` |
| init order | `init_dump.py` with the ref's `check_init_priorities.py` over the ELF |
| Kconfig re-resolution | `kconfig_try.py`: the fragments the log lists ("Loaded/Merged configuration") plus extra assignments merged last |
| compiled files, included headers | `ninja -t deps` |

Kconfig's environment is the build's own, parsed from `build.ninja`'s `menuconfig` rule
(`cmake -E env K=V … menuconfig.py <root>`), which carries it on every version checked
(v3.6–v4.4). Extractors index kconfiglib's tuples instead of unpacking them (their shapes
changed after v3.7). Phandles are recorded by the target's label, so renaming a node
doesn't change every node that references it. Snapshots are versioned
(`SNAPSHOT_VERSION`).

## Project usage

`ProjectUsage` (`usage.py`) is what "for your project" means. Every entry has file and line.

| Kind | Source | Confidence |
|---|---|---|
| Kconfig assignments | the fragments each image's configure loaded or merged (`ScanContext.config_files`): `prj.conf`, board defconfigs and `.conf`, shields, `EXTRA_CONF_FILE`/`OVERLAY_CONFIG`, per-image sysbuild files; `*.conf` names only if configure never reached Kconfig. Sysbuild level from the sysbuild log segment | high |
| Kconfig reads | `CONFIG_\w+` / `IS_ENABLED()` in C, headers, CMake | high |
| Kconfig definitions | `config` blocks in the project's Kconfig files | high |
| DT references | overlays (labels, paths, `compatible`, properties set); C (`DT_NODELABEL`, `DT_ALIAS`, `DT_PATH`, `DT_PROP*`, `DT_DRV_COMPAT`, `DEVICE_DT_GET*`) | high / medium |
| Project DTS | board and DTS-root files of the project's own modules the source build read (`zephyr.dts.d`) | high |
| Includes, identifiers | `#include` lines; C identifiers, confirmed by `nm -u` on the source build's objects (`z_impl_<name>` accepted) | high; low from token scan only |
| Files used | ninja deps (sources and every header included), overlays and fragments applied | high |

Project code is the app plus every module outside zmig's workspace (the project's own
`ZEPHYR_EXTRA_MODULES`), shown as `<module>/path`; Zephyr's, other modules' and generated
files never are. The app image sees every app file except `sysbuild/` and
`sysbuild.conf`; other images see only the app files their build applied. Overlays are
read by a brace-tracking scanner, not a DTS parser (`&label {`, `&{/path} {`, nested
nodes, one-line properties); what it can't place is ignored, not guessed.

## Analyzers

### Protocol and registry

An analyzer is a class with `name`, `kind` (`"workspace"` for `plan`, `"build"` for
`scan`) and `analyze(ctx) -> AnalyzerResult(findings, status, reason, not_checked,
checked)`, taking a `PlanContext` or a `ScanContext`. Build analyzers also declare
`requires` (artifacts, e.g. `{Artifact.CONFIG, Artifact.BUILD_NINJA}`) and optionally
`per_image = False`.

- **Registry:** built-ins in a fixed order, then third-party classes from the
  `zmig.analyzers` entry-point group, by `kind`. `--analyzers a,b` selects; `none` runs
  none.
- **Order:** modules, board, kconfig, devicetree, buildlog, changes, initorder, api, sbom,
  images, crossimage, sysbuild, layout, mcuboot, storage, ble, wire, guide. Each sees
  earlier findings (`ctx.prior`): changes skips subjects already reported, buildlog drops
  Kconfig deprecation warnings kconfig reported at the assignment, guide runs last to link
  to the rest.
- **Per image:** a per-image analyzer runs for every image both builds have, and tags
  non-app findings with the image. Build-wide analyzers run once with the app's context.
- **Missing artifacts** in `requires`, or `ArtifactMissing` while snapshotting, make the
  run `skipped` with the reason, never `error` (that's for a `ZmigError`).
- **`ScanContext`** holds the source build, the effective and raw target builds, salvage
  edits, the board mapping, usage, memoized snapshots, module needs, and `shared` for
  cross-analyzer data (verdict categories, API coverage, tool versions).

### Plan analyzers

**manifest.** The upstream `west.yml` diff, imports resolved: projects added, removed or
changed (`revision`, `url`, `path`, `groups`), scoped to the user's workspace. Info, or
risky when a re-pinned project carries local commits or the app uses it.

**forks.** For every project in the user's workspace, zephyr and the manifest repo
included. All git work happens in the cache mirror after fetching the user's HEAD into it.

1. **Upstream:** `--upstream name=url`, else upstream Zephyr's manifest at `--from`, else a
   remote under a known upstream org. Base: the manifest revision at `--from`.
2. **Local commits:** `git rev-list <merge-base(HEAD, base)>..HEAD`.
3. **Classification,** first match wins. **upstreamed:** same `git patch-id --stable` as a
   commit in `merge-base..target` (high); else same normalized subject, checked by content
   similarity (Jaccard over whitespace-normalized changed lines per file): ≥ 0.5 agrees
   (medium), below disagrees (low, `fork.signals-disagree`); else an upstream commit on
   the same files with similarity ≥ 0.8 under another subject (low). Upstreamed commits
   are still trial-applied, because a real rebase stops on one whose content differs.
   **applies / conflicts:** replayed in order onto the target plus earlier local commits,
   as `git rebase --onto` does. **absorbed:** no upstream match, but its added lines are
   present and removed lines gone at the target. Independently, `overlap` lists files
   upstream also changed, and `overlap_near` those with upstream changes inside a hunk.
4. **Patch methods** (`--patch-method`): `3way` is `git merge-tree --write-tree` with the
   commit's parent as base (a cherry-pick, no worktree); `git-apply`/`git-am` applies the
   diff with exact context, as patch-file workflows do. Default runs both; the stricter
   wins and disagreements are recorded. `--patch-dir` names patch files in findings.
5. **Unrecorded forks:** `git ls-tree -r <claimed rev>` blob hashes against `git
   hash-object` of the files; for an untracked tree the revision is guessed from
   `VERSION` (high for content, medium for a guessed revision).
6. **Vendor distributions** (NCS-style) run the same with the vendor manifest as "user".

**hops.** Releases crossed come from each ref's `VERSION` and the release tags; a ref no
upstream tag or branch contains is `ref-is-fork`. LTS facts come from Zephyr's docs
first (`doc/releases/index.rst` at the target, then each crossed release's notes), with
`zmig/data/releases.json` as a dated fallback (`--release-data`, `$ZMIG_RELEASE_DATA`);
findings cite `hops.lts-from-release-docs` or `hops.release-data`. Boundaries (3.7,
Hardware Model v2) and `max_minor_gap` (2) exist only in the data file. The path stops at
every LTS or boundary, then adds stops so no step crosses more than the gap, each at the
latest patch of its minor. The suggested path is risky; each boundary is info.

### Scan analyzers: the build migration

**modules** (build-wide).
- `module-required`: a module the target needed and the scan lacked ([module
  discovery](#module-discovery)), breaking; info if the source needed it too. Cites the
  build's message, the target manifest's declaration, and the **user's** manifest line:
  the Zephyr import's `name-allowlist:` in the nearest workspace or `west.yml` above the
  app (up to the git root or 3 levels), else one of the project's own modules.
- The same fact from the change database when the target has no `.config`, same ID.
- `module-not-in-manifest` (breaking): the build used a module the user's allowlist
  doesn't list; the scan was given it, the user's workspace won't fetch it.
- `module-removed`, `module-repinned` for modules the source build used.

**board.** If `--board` doesn't resolve at the target, zmig maps it from the target's
`boards/deprecated.cmake`, then that file at each release tag in between, newest first
(Zephyr drops aliases a few releases after a rename); `--to-board` overrides.
`board-renamed` is risky while the alias works, breaking once gone; `board-unknown` when
nothing maps. `input-dropped` / `input-added`: board-specific app files applied at one
ref only, compared when both configures got that far.

**kconfig** (requires `.config` and `build.ninja`). Scoped to symbols the project sets
(per image) or reads. Diagnostics come from the raw target configure.

| Kind | When | Severity |
|---|---|---|
| `symbol-removed` | a set symbol isn't defined at the target. Candidates: guide renames, deprecation `select`s, pure `default` aliases, help text ("use X instead"), similar names | breaking |
| `value-not-taken` | "assigned X but got Y", or the value took at the source and not the target. The action names unmet dependencies, and new symbols in them that are off | breaking (info if pre-existing) |
| `not-configurable` | lost its prompt | breaking |
| `symbol-deprecated` | newly or still deprecated; replacements from help text | risky |
| `read-symbol-removed` / `read-value-changed` | code reads a symbol that's gone / changed value | breaking / risky |
| `dependency-changed` | a symbol the project's configuration leans on (one or two hops via depends, select, imply, default) changed value; the chain is shown | risky |
| `default-changed`, `newly-set`, `no-longer-set` | the rest, one bulk finding each | info |
| `dependency-expression-changed` | depends/selects/implies of used symbols changed, without a value change here | info |
| `salvaged` | salvage disabled lines | info |

**devicetree** (requires `edt.pickle`).

| Kind | Severity |
|---|---|
| `node-gone` (referenced node, label or alias), `node-disabled`, `read-property-gone` | breaking |
| `property-undeclared`, `dt-error`: the target rejects an overlay (undeclared or newly required property, dtc/edtlib error), at the overlay line | breaking |
| `compatible-unbound` (replacement from the change database), `device-missing` | breaking |
| `node-changed` (values written in the DTS), `property-type-changed`, `property-deprecated` | risky (info when the node is unused) |
| `node-moved`, `node-binding-defaults`, `other-nodes` | info |

Undefined-label errors name only the label; `dtc -T -T` on `zephyr.dts.pre` gives the line
of the property referencing it. Properties are compared as written in the DTS: edtlib's
`props` include binding defaults, which change when only a binding does.

**buildlog.** The target build as the user would build it.
- **Failures** (`target-configure-failed`, `target-build-failed`) quote the tool and are
  placed at project lines from the tool's own output, never by searching sources:
  `file:line:` errors; Kconfig's "Aborting due to Kconfig warnings" traced to the
  assignments to undefined symbols behind it; "Malformed board YAML file" re-validated
  against the target's board schema to name the missing key and line; dtc annotations.
  Compiler errors are sorted by file and line (a parallel build prints them in any
  order), and one error printed once per includer is one site.
- **`include-missing`** (breaking): an "undeclared" error in a project file whose symbols
  the source build got only through another header that no longer includes the
  declaring one (ninja deps); names the `#include` to add.
- **`input-not-applied`** (risky): a project fragment or overlay the source applied and the
  unmodified target doesn't; judged only if the target reached Kconfig / generated a
  devicetree.
- **`kconfig-override-orphaned`, `kconfig-definition`, `kconfig-name-collision`:** project
  Kconfig the target rejects: a re-declared symbol Zephyr removed ("defined without a
  type"), a choice default not in the choice, a dependency loop through a symbol the
  target now defines too.
- **`new-warning`:** compiler, Kconfig and CMake warnings new at the target. Identity is
  (source, path relative to zephyr/app/build dir, message), never the line. The site is
  the project's: GCC's macro-expansion notes and include chain; for a non-project
  header's warning about itself (its own `__WARN`), the project lines including that
  header; for a CMake warning naming `CONFIG_X`, the project's assignment of X. Risky
  when attributed or a deprecation, else info. `warnings-resolved` lists ones gone.
- **ROM/RAM** from both ELFs by section flags (matches binutils `size`), with per-symbol
  deltas; risky above +1 KiB and +5%. Host boards (`CONFIG_ARCH_POSIX=y`) are marked
  `[host-runtime-inflated]` (medium): deltas compare, totals include the host runtime.

**changes.** Stands in for kconfig and devicetree when the target never produced their
inputs (no `.config` and `build.ninja`, or no EDT), from the source build and the [change
database](#change-database). It uses **the same kind and subject**, so the ID doesn't
depend on whether the target configured, and watch baselines don't churn when a configure
starts or stops failing.

| Project use (source build) | Finding |
|---|---|
| a merged fragment sets a removed symbol | `symbol-removed` |
| the project's Kconfig re-declares a removed symbol | `kconfig-override-orphaned` |
| project code reads a removed symbol | `read-symbol-removed` |
| a newly deprecated symbol the source enables, assigned or referenced | `symbol-deprecated` |
| a compatible used by the app or project DTS, bound at the source, binding gone | `compatible-unbound` |

App image only; skips subjects already reported; ignores symbols and compatibles any
target-workspace module defines (moved, not vanished); tagged `changes.lexical`.

**initorder** (requires ELF and EDT). Device and `SYS_INIT` order at both refs from each
ref's `check_init_priorities.py`. A call is the project's when its function is defined in
project code (DWARF); calls match across refs by function and node path (ordinals
renumber). `level-changed` and `order-changed` (calls that swapped sides around a project
call) are risky: that's how an init that relied on a service being ready breaks.

**api.** See [API and ABI diff](#api-and-abi-diff).

**sbom** (requires SPDX). Modules added or dropped (`module-dropped` risky), files
compiled at one ref only, upstream commits touching files the target compiles. Headers
aren't in the SPDX inventory, so headers the app includes are checked against git:
`header-removed` (breaking when used); `header-changed` risky when used, info when every
including file is a TU the API diff compared cleanly with no finding.

**guide.** See [Migration-guide parsing](#migration-guide-parsing).

### Scan analyzers: images and field upgrade

All build-wide; the hazard ones are described under [Field-upgrade
verdict](#field-upgrade-verdict).

| Analyzer | Reports |
|---|---|
| `images` | images added, removed or changing role (`bootloader-identity-changed` instead of a removed+added pair); per-image status and changed compiled files; MCUboot revision and changed bootloader files |
| `crossimage` | in the target, and the source to tell old from new: `partition` (same `reg` in every image), `setting` (board target, SoC, flash size and base), `pair` (app/bootloader options sysbuild switches together) |
| `sysbuild`, `layout`, `mcuboot`, `storage`, `ble`, `wire` | the hazard categories |

## Migration-guide parsing

**Sources** (`guides.py`): `migration-guide-X.Y.rst` for every release in `(from, to]`
(plus the target's version if it's an unreleased snapshot), and the "Removed …" /
"Deprecated …" sections of `release-notes-X.Y.rst`. Each is read from the target tree, or
the release's tag if the target no longer has it; the report records which.

**Parsing** is a heuristic reStructuredText reader, not docutils: underlined headings,
top-level `*` bullets with indented continuations, directives skipped; stable from v3.5
through v4.5. Each top-level bullet or paragraph is an entry `MG-<ver>-<nnnn>` (`RN-…` for
release notes) with its heading path. Mentions are typed and located to their own line:

| Pattern | Kind | Strength |
|---|---|---|
| ``:kconfig:option:`CONFIG_X` `` | kconfig | role (medium) |
| ``:dtcompatible:`vnd,x` `` | compat | role |
| `:c:func:`, `:c:macro:`, `:c:struct:`, `:c:type:` … | ident | role |
| ``:zephyr_file:`include/….h` `` | header | role |
| ``CONFIG_X`` / bare `CONFIG_X`, ``x.h``, ``vnd,name`` | kconfig / header / compat | literal (low) |
| any other ``literal`` | DT property, identifier or board name | literal |

**Severity** comes from the mention's sentence, else enclosing bullets' leads, else the
entry: "removed / renamed / replaced / no longer / must / moved to / now requires /
instead of …" is breaking; "deprecated" is risky, and so is a removal announced for the
future. Otherwise info. Tagged `guide.severity-from-wording`. **Renames:** "A …
renamed/replaced/moved … B" in one sentence, and "* ``old`` to :c:struct:`new`" under a
lead saying renamed. Kconfig renames also feed kconfig's candidates and `fix`.

**Matching** against `ProjectUsage`: Kconfig symbols set or read; compatibles in overlays,
`DT_DRV_COMPAT`, project DTS, or on referenced nodes; includes by exact path (high) or
basename (low, `guide.header-basename`); identifiers, high only when `nm -u` shows them
(inline APIs never do), else low (`guide.identifier-token-scan`); DT property names
(except ones every node has) and the board.

**Gating by build facts.** A match is downgraded to info/low (`guide.gated-by-build`),
with the reason in its action, when:
- it's a compatible reached only through nodes disabled in both builds;
- the entry is under a SoC-vendor heading (NXP, STM32, Nordic, …) and the build's
  `SOC_FAMILY`/`SOC_SERIES`/`SOC` isn't that vendor's (`vendor_mismatch`);
- every sentence qualifies the identifier as a struct member (`bt_conn_le_info.interval`)
  and the project never uses that struct (`member_qualifiers`);
- every sentence naming it is conditional ("when / if / only / unless") on Kconfig
  options neither build enables, rename partners excluded.

**Output:** one `entry-match` per (entry, item), with `doc_entry` and `file_line`
evidence; confidence is the lower of guide and usage strength; `related` to kconfig and
devicetree findings on the same item. Unmatched entries are counted in `coverage`;
`--all-guide-entries` lists them.

## Change database

Facts about what changed between two refs that need **no build** (`changes.py`), so a scan
can report and fix what skipped analyzers can't when the target configure fails early.

### Contents

One JSON document per (source SHA, target SHA), `CHANGES_VERSION` 5, built on first use
into `<cache>/changes/` with `git grep` and `git log` on the mirror (no checkout). `zmig
changes --from A --to B [--symbol X]` prints it.

| Part | What |
|---|---|
| `kconfig.removed` | `config`/`menuconfig` symbols at the source and not the target (`git grep` over `*Kconfig*`), with source `defined_at` and ranked `candidates` |
| `kconfig.deprecated` | symbols whose block selects `DEPRECATED` at the target and not the source, with `instead` from their help text |
| `devicetree.removed` | `compatible:` lines of `dts/bindings/**` at the source and not the target, with candidates |
| `modules.glue_paths` | per `modules/<dir>/` glue directory, the modules it builds paths into (`${ZEPHYR_<M>_MODULE_DIR}/…`) |
| `modules.kconfig_moved` | symbols the target defines only in one module's glue, defined outside it at the source |

**Candidates** come from `git log -M -p -U0 from..to` over the Kconfig (or bindings)
paths: a definition removed and another added in the **same hunk** is a rename; the rest
of a **small commit** (≤ 3 definitions added and removed) is weaker, only without a
same-hunk rename and with names ≥ 0.5 similar; the removed symbol's **help text** ("use X
instead") also counts. Chains are followed to a symbol the target defines (A → B → C, up
to 5). Each candidate records its target location and its **conditions** (`depends on`
lines and enclosing `if` blocks, lexically). Order: direct before chained; same hunk,
help text, same commit; then similarity.

**Choosing for a build** (`choose`): conditions are evaluated on the source `.config`
with three-valued logic (a missing symbol or any comparison is unknown). False drops a
candidate. One whose conditions all hold comes first, medium if it's the only one; with
none holding, a single remaining candidate is medium. Everything else is low, and
same-commit candidates always are.

**Safeguards.** Generated symbols (`DT_HAS_*_ENABLED`, `ZEPHYR_*_MODULE(_BLOBS)`) are never
removed. At scan time anything a target-workspace module defines is dropped. Only
definitions are compared; values and dependencies need a target `.config`. A grep that
finds nothing at a ref is an error, never "nothing changed". Several candidates means low
confidence, all listed.

### How scans use it

- the **changes** analyzer, when the target lacks `.config`/`build.ninja` or EDT;
- the **devicetree** analyzer, for a removed binding's replacement;
- the **modules** analyzer (`modulecheck.tree_needs`): a source-used module whose target
  glue builds paths into a module **new in the target manifest** (glue also points into
  existing modules it merely supports), or a source-enabled symbol now defined only in
  M's glue, means M is needed;
- **`fix`**, for better replacements.

It never adds a finding when the analyzer that normally reports it ran.

## API and ABI diff

The `api` analyzer answers: *which declarations this project's compiled code uses changed,
and where does the project use them?* Every finding names a project `file:line`; a
declaration the project doesn't reference produces nothing.

### The project's own system calls

No libclang needed. A project `__syscall` that the source build generated (it's in
`zephyr/misc/generated/syscalls.json`) and the target doesn't is `syscalls-not-generated`
(breaking): every file including that header fails on the missing `syscalls/<h>.h`. The
remedy is read from the target tree: `zephyr_syscall_header()` in
`cmake/modules/extensions.cmake` and/or `CONFIG_APPLICATION_DEFINED_SYSCALL`. Project
headers come from the source build's ninja deps, so this works when the target fails.

### Parsing

With libclang (`zmig[api]`: the PyPI wheel, or a system library via `ZMIG_LIBCLANG`; the
version loaded is in `environment.libclang`):
- **TUs:** every project TU in the **source** `compile_commands.json`, parsed with the
  source flags and with the target's flags for the same file. Call sites come from the
  code as the user has it and survive a failed target build; the target only needs to be
  configured. A TU with no target compile command isn't interpreted.
- **Flags:** the build's own, translated: GCC-only and output options dropped (a table),
  `--target` added, clang's resource directory for builtin headers. Xtensa uses a stand-in
  triple; its layouts aren't checked.
- **Parse:** a detailed preprocessing record (macro definitions and instantiations
  visible) and keep-going (a fatal missing header doesn't stop it).
- **Recorded:** referenced non-project declarations (functions with canonical signature
  and deprecation; typedefs; fields with type and offset; enum values; macros: kind,
  arity, and for constant expressions the value and type clang evaluates), references
  with `file:line`, break-showing diagnostics (incompatible callback pointer types,
  "changes value from", undeclared `__device_dts_ord_N`), and errors.
- **Cache:** per TU, keyed by content (TU, normalized flags, every included file,
  `API_VERSION` 16) in `<cache>/api/`. A TU whose included files are identical at both
  refs isn't parsed.

### What is reported

| Change, with a project reference | Kind | Severity |
|---|---|---|
| function, typedef, field, enum constant or macro removed | `<kind>-removed` | breaking |
| function signature changed | `function-changed` | breaking |
| typedef/field type changed: function-pointer type, or a callback diagnostic at a project site | `<kind>-changed` | breaking |
| other typedef/field type change | `<kind>-changed` | risky |
| only const/volatile changed | `<kind>-changed` | info (functions), risky |
| function-like macro arity changed / object-like ↔ function-like | `macro-changed` | breaking / risky |
| object-like macro or enum value changed | `<kind>-changed` | risky; breaking when the type got wider and the project truncates it; info when the project only discards it or takes its size |
| field offset only | `field-changed` | info |
| function became a function-like macro | `function-became-macro` | info: calls compile, taking its address doesn't |
| other change of declaration kind | `<kind>-kind-changed` | risky |
| newly deprecated | `<kind>-deprecated` | risky |
| a project callback no longer matches, slot not a reported typedef | `callback-type-changed` | breaking, at the definition and the assignment |
| a value no longer fits where it's stored | `value-truncated` | risky |
| a device the code takes has no device at the target | `device-missing` | breaking |

- **Macro body changes alone aren't reported:** Zephyr refactors macro bodies constantly;
  same value and arity says nothing about breakage.
- **Compatibility aliases are followed.** A target object-like macro whose whole expansion
  is one identifier (`#define sockaddr_in net_sockaddr_in`) keeps the old name compiling:
  the old name is compared with what it now names, field names are mapped part by part,
  old types are spelled through the aliases.
- **Builtin headers are skipped.** Declarations from clang's resource headers (`stddef.h`,
  `stdarg.h`) at either ref aren't Zephyr's API; they move between libc and compiler
  headers across versions.
- **A removal is claimed only** if a target parse covered a file using the name; the same
  declaration in the same header at both refs is never a removal (a target parse with
  errors records no reference to a call it couldn't type-check).
- Header removal is sbom's `header-removed`.

**Gaps** are `not_checked` entries and make the status `partial`: a TU without a target
compile command, a TU with parse errors at either side (first error listed), libclang
missing (only the syscall check runs). When more than half an image's project TUs weren't
interpreted, the verdict's confidence is capped at low.

### Optional binary tools

`pahole` cross-checks clang's field offsets against the GCC-built ELF's DWARF; a
disagreement is a zmig limitation (`not_checked`), not a finding. `abidiff` runs only at a
binary boundary, a prebuilt static library from the project tree on the link line,
comparing the symbols it imports between the two ELFs (`abi-changed`, risky, medium).
LLEXT imports aren't part of the build and are always not checked. A missing tool is a
`not_checked` entry, never an error or a clean result.

## Field-upgrade verdict

Can a device running the `--from` build receive the `--to` build over the air and keep
working? If not, what has to happen in between? A category that wasn't positively
checked is **unknown**.

### Shared machinery

- **Inputs per image and ref:** `.config` and Kconfig metadata (choice group, defining
  file), final EDT and DTS, SPDX, and the `imgtool sign` command from the app image's
  `build.ninja`. Module sources at their **pinned SHA** come from cache mirrors of each
  module's upstream (narrow workspace clones may lack the other ref's objects); reads use
  `git show`/`git grep` at the SHA, never a checkout.
- **`srcdiff`** extracts `#define`s (conditional variants all kept), define prefixes,
  struct/union bodies and member lists, and initializers, and compares normalized text.
  What it can't list is "not interpreted", never "unchanged". All old definitions
  surviving plus new conditional variants is `extended`: reported, never cleared. Seeds
  are found by `git grep` at the pin, since definitions move; a prefix like
  `STRUCT_PACKED` is kept, so packing changes show.
- **Seeds** (`zmig/data/hazard_seeds.yaml`) name the identifiers and trees checks start
  from. Each is verified per ref; a missing seed makes that check unknown, not clean.
- **Provenance:** hazard findings carry `{evidence_source, role_source, role_evidence}`.
  `strong`: the image's own `.config` has `CONFIG_MCUBOOT=y` (or it's the default image);
  `weak`: the app targets MCUboot but no bootloader image was built; `unknown`. Weak or
  unknown roles cap confidence at low.
- **Always not checked:** sysbuild rules outside `MAIN`/`BOOTLOADER`, non-MCUboot
  bootloaders, salvage limits, whether `--deployed-image` was given, images zmig doesn't
  know (TF-M splits).

### sysbuild

At each ref: the user's `sysbuild.conf` and `sysbuild/*.conf`, sysbuild's resolved
`.config`, and its Kconfig metadata (environment from the top-level `build.ninja`). Kinds:
`sb-option-changed`, `-default-changed`, `-renamed`, `-deprecated`, `-removed`,
`sb-choice-changed`. Each changed `SB_CONFIG_*` is shown with the image options
`sysbuild_rules` derives from `share/sysbuild/image_configurations` at both refs (a small
CMake subset; an unknown line yields fewer rules, never wrong ones) and their resolved
values. **Behavior** changed only if those resolved image options differ: identical
means "renamed, behavior unchanged". Help text alone is never proof. Per-ref cache paths
(the default signing key) are replaced by placeholders before comparing.

### layout

Compared on the **bootloader** image's final devicetree (the app's without one), since a
deployed bootloader looks for its slots where the source put them. Both representations
(`fixed-partitions` children, `*-partition` nodes); offset, size, flash device, erase and
write block sizes. **Roles come from source, not names:** `bootloader` if MCUboot's Zephyr
port references the label, `storage` if the storage subsystems do, `code` for the chosen
code partition, else `unknown`. A `.c` hit counts only if compiled (SBOM); headers count;
Kconfig, CMake and overlays don't (conditional on what a grep can't see). Moved, resized,
removed, reordered, or moved to another device: breaking; added: info.
`usable-image-size-changed` uses imgtool's `--slot-size`, never a guessed trailer.
`storage-stranded`: a storage partition moved or resized. **Unknown** when an EDT or role
source is missing, or no partition nodes exist with `CONFIG_FLASH_MAP` set.

### mcuboot

The device keeps its bootloader, assumed to be the source build's MCUboot.
- **Options and behavior:** the bootloader's resolved `.config`, restricted to symbols
  defined in MCUboot's own Kconfig at its pin, grouped by choice (prompt as written) or
  menu. Same active choice members means "behavior unchanged".
- **Format definitions** at both pins via `srcdiff`: image header and magic, TLV magic and
  IDs, trailer and boot state. Reported as changed / unchanged / not interpreted.
- **imgtool** at each pin is read with `ast`, never run: literal constants, the `sign`
  command's flag options (so `build.ninja`'s command splits right), the `--endian` default.
  A constant change with no measured effect is info.
- **Signed images, measured:** both builds' signed app images (found from the `imgtool
  sign` output path) are parsed with `image.h` at the matching pin: magic, header and image
  size, protected TLV size, version, flags, each TLV's type, length and value hash. A
  field the definitions don't give is an error naming it, and dependent checks become
  unknown; no offset is guessed. `--deployed-image` replaces the source image.
- **Direction (a), old bootloader accepts new image** (always): header size and magic,
  signature type and key, encryption, alignment and padding, size within the source slot,
  max sectors, upgrade mode. App/bootloader option pairs come from
  `sysbuild_rules.pairs`; a mismatch is breaking only when compiled target-app code
  depends on the app-side option (grepped, filtered by the SBOM), else risky.
- **Direction (b), new bootloader reads old state:** only if the bootloader is updated;
  trailer and boot-state definition changes are breaking (remedy `reflash` when a swap in
  progress could be misread). Otherwise `not-applicable`.
- **Slot geometry:** the `if (...)` conditions of `boot_slots_compatible()` in the
  `swap_*.c` the target bootloader compiles, kept if they mention sectors and precede a
  `BOOT_LOG`, evaluated by an AST-whitelisted evaluator over EDT sector counts, macros
  resolved through MCUboot's `#define` chain to `CONFIG_` values. Uniform sectors are
  assumed (`mcuboot.uniform-sectors`); conditionally defined macros stay unevaluated.
- **Updatability** is unknown unless the build shows a mechanism (a bootloader-updating
  image, or an enabled MCUboot option whose prompt mentions updating the bootloader).
- **Implementation-only:** compiled bootloader files whose diffs touch no format
  definition, medium.
- **Unknown** with no bootloader image, a weak role, missing pin or seeds, no imgtool
  arguments, or a non-MCUboot bootloader.

### storage

- **Backends:** `y` symbols defined under a backend tree (`subsys/settings`, `fs/nvs`,
  `fs/zms`, `secure_storage`, other `subsys/fs`, module filesystems), trees checked per ref.
- **Switches and causes:** a backend disabled or a backend choice switched; for a value
  the user didn't set, the `select`/`imply` that appeared or vanished, or the unmet
  `depends on`, with the Kconfig line at each SHA.
- **Remedy, verified:** the settable symbols the causes name plus their unmet
  dependencies (up to four rounds), re-resolved with `kconfig_try`. `app-change` only if
  every source backend symbol and choice member comes back; else `unknown`, naming what
  didn't.
- **Partitions** referenced by each backend's compiled code, resolved in the EDT; unknown
  only when no enabled backend references one (NVS and ZMS get theirs from a caller).
  Layout hazards on them are linked, not duplicated.
- **On-media:** `srcdiff` of backend seeds (allocation-table structs, magic, version,
  CRC): "new code can't read old data"; "one-way migration on first boot" when the target
  has an upgrade path keyed on the old version; else implementation-only (medium).

### ble

Active when the app enables a `subsys/bluetooth` symbol that depends on or selects
`SETTINGS`. From compiled Bluetooth files: key names (settings handlers, with the `bt/`
prefix read from `BT_SETTINGS_DEFINE` at each ref; `bt_settings_store/delete` keys; the
root handler's leaves), persisted structs via `srcdiff`, identity options.
`ble-key-removed` and `ble-struct-changed` are breaking, medium (store calls may sit under
uncompiled `#if`s); `ble-identity-option-changed` is risky. Tests use the handlers the ELF
links as oracle.

### wire

An allowlist, not inference: `zmig/data/wire_format.yaml` (SMP, BLE MTU/ACL/name, USB
VID/PID/strings, console baud) plus `--wire-allowlist`. Entries are `kconfig: SYMBOL` or a
`dt:` node (by chosen or compatible) and property, verified at both refs
(`allowlist-entry-not-found` otherwise). A changed value counts only if a `.c` file
compiled into that image references the symbol; others are `changed_but_unused`. Clean
means only that no allowlisted item changed, and `basis` says so.

### Remedies

| Remedy | When |
|---|---|
| `config-pin` | the old option still exists, not deprecated, at the target, and selecting it restores the source's resolved image options |
| `app-change` | the app's own files can fix it |
| `intermediate-hop` | a one-time image must run in between |
| `bootloader-update` | the bootloader must change and the build evidences it can be updated |
| `reflash` | the bootloader must change and updatability is unknown, or in-progress state becomes unreadable |
| `unknown` | nothing can be established |

The bootloader owns the layout: a changed partition it uses needs `bootloader-update`,
which resolves to `reflash` unless updatability is evidenced.

### Verdict

| `upgrade_path.verdict` | Meaning |
|---|---|
| `blocked` | a hazard needs `bootloader-update` or `reflash` |
| `needs-changes` | hazards, all resolvable by app OTA (`config-pin`, `app-change`, `intermediate-hop`, `unknown`) |
| `unknown` | no hazard, some category unknown |
| `direct-ok` | every category clean or evidenced not-applicable, no hazard |

- **Categories:** layout, mcuboot, storage, ble, wire, sysbuild, images, crossimage (the
  last three derived from findings). An analyzer that didn't run cleanly, or recorded
  nothing, leaves its category unknown.
- **Hazard:** a breaking or risky finding with a remedy, introduced by the migration. A
  hazard-category finding without a remedy, or a category recorded as hazard, adds an
  `unknown` remedy.
- **Precedence** blocked > needs-changes > unknown > direct-ok; the verdict starts unknown
  and is never defaulted to OK. Not-applicable needs positive evidence, else unknown.
- **Pre-existing breaking findings** in layout, mcuboot, crossimage or storage (the source
  build is itself inconsistent) make that category unknown and cap confidence at low;
  other categories stand. Capping alone would let direct-ok rest on the assumption the
  finding disproves; forcing unknown would discard valid results.
- **Confidence** is the minimum over clean and hazard categories; low also when most of an
  image's TUs weren't interpreted by the API diff, or the target build failed.
- **`assumptions`** (deployed bootloader and app == the source build's, updatability) and
  **`basis`** (implementation-only files, the wire caveat, remedies resolved to reflash,
  API coverage, a failed target, "remedies are a lower bound" when a category is unknown
  under a hazard verdict). **`update_path`** is `reflash` when neither build has a
  bootloader.

**Invariants** (`verdict.invariant_violations`; tests, and every `watch` run exits 1 on a
violation): no direct-ok with a breaking/risky hazard-category finding, or with a hazard
or unknown category; a hazard category means needs-changes or blocked; unknown categories
are listed in `unchecked`, and an analyzer that didn't run cleanly can't leave its
category anything else; pre-existing breaking deployment findings mean low confidence; a
mostly uninterpreted image means `api` partial and low confidence; not-applicable carries
evidence. The hazard fixtures pin every ref by resolved SHA, so a retag fails the test
instead of changing it.

## Kconfig fixes

`zmig fix kconfig` scans, then classifies each assignment in the project's own fragments
(those each image merged, plus `SB_CONFIG_*` in `sysbuild.conf`) with the Kconfig trees of
the image that reads it. Nothing else is edited: no includes, boards, devicetree or code.

| Category | When | Edit |
|---|---|---|
| `rename` | absent at the target, and the **source** tree evidences the replacement: OLD was deprecated and selected NEW, or one is a pure `default` alias of the other. NEW exists at the target with OLD's type and a prompt; `kconfig_try` gives NEW the assigned value merged after the build's fragments; no guide entry about either mentions a value or unit change | rewrite |
| `remove` | absent at the target, no Kconfig-level replacement, no guide rename, and **no guide entry mentions it at all** | comment out with a note |
| `needs-human` | absent, without all of the above (guide- or help-only rename, type or dependency mismatch, several candidates, a value change, a guide mention) | none |
| `deps-unmet` | still defined, but the value no longer takes effect | none |

- A guide mention blocks `remove` because the guide may name a replacement outside
  Kconfig (ZMK's `SSD1306_REVERSE_MODE` became the property `inversion-on`); commenting
  the line out would drop behavior silently. A folded menu condition (`default y if CAN`)
  is not an alias.
- **Comment-out:** the line kept whole after `# `; `# zmig: CONFIG_X removed in <target>:
  no replacement evidenced` above it; comments kept; `# CONFIG_X is not set` becomes `#
  # CONFIG_X is not set`. Idempotent.
- **Never edited:** a file that already assigns NEW, images disagreeing about a line, a
  line changed since it was read.
- **`--apply`** refuses a dirty tree (exit 2), commits on a new branch
  (`zmig/fix-kconfig-<to>`) one commit per evidence kind, rebuilds the target, reruns the
  analyzers, and splits the difference into gone, revealed (hidden by the failed
  configure) and introduced (cites an edited line, is about a written symbol, or appears
  when nothing was hidden). Exit 3 if anything was introduced. Dry run by default.

**Kconfig-file overrides** (an app re-declaring a removed symbol, e.g. `config LV_Z_DPI` /
`default 77` in a shield's `Kconfig.defconfig`) are listed as needs-human, never edited,
at the locations the target's own "defined without a type" warning names. Renaming the
block would be safe only with Kconfig evidence of the replacement, a block that only sets
defaults (from kconfiglib's menu nodes, not text), a same-type replacement the app doesn't
already override, no guide value change, and `kconfig_try` showing the value preserved;
no measured case has passed yet. Deleting the block loses the default silently; moving it
to a non-Kconfig replacement is a codemod.

## CI mode (zmig watch)

`watch` runs on every PR and nightly, and fails only when the upgrade gets **worse**: a
breaking finding outside a committed baseline fails; accepted ones don't. It's a scan plus
a ref-independent identity, a baseline, an exit policy, and code-scanning and PR-comment
outputs. The comparison (`watch.py`) works on report JSON only, so it reruns on saved
reports (`--report`, `--pinned-report`).

### What a run compares

- **`--from`** defaults to `manifest`: the `zephyr` revision in the nearest `west.yml` above
  `--app` (or `--manifest`), mirrored from that project's URL, so a fork comes from the
  fork. A manifest pinning a **branch** resolves to today's tip, and the run warns in the
  report (`inputs.from_manifest.warning`, a `not_checked` entry), comment and `watch.json`.
- **`--to`:** omitted on PRs, **pinned** to the baseline's `to.sha`, so a PR's result
  depends only on the PR; `track:next-release` nightly (newest final tag above `--from`'s
  `VERSION`; `--include-rc` adds `-rc`); optional weekly `track:main`; or an explicit ref.
- **Target movement:** when `to.sha` differs from the baseline's, a second **pinned scan**
  runs against the baseline's target. A new finding also there is **due to this change**;
  absent there, **due to target movement**. `--no-attribute` skips it (unattributed).
- A report violating a verdict invariant exits 1.

### Fingerprints

Finding IDs hash both SHAs, so `watch` uses:

```
fingerprint = sha256(1, category, kind, normalize(subject), image or "", board,
                     normalize(primary location))[:16]
```

- **`normalize`:** this run's `--from`/`--to` specs, SHAs and 12-hex prefixes become
  `<ref>`, other SHAs `<sha>`, 16-hex cache directory names `<h>` (some subjects are a
  ref).
- **Primary location:** the first `file_line` path **without the line** (lines move with
  unrelated edits), else the first `doc_entry` id, else "".
- **Multisets:** same-fingerprint findings are counted (2 accepted, 3 found: 1 new).
- **States:** **updated** when the `new` value digest changed (it drops `line` keys and
  `file:NNN` suffixes); **escalated** when severity rose; **new-use-site** when the
  project evidence has a file the accepted entry's `[file, symbol]` set didn't (else a
  second call of an accepted removed API would fold in silently); **fixed**, or
  **unseen** when the run has an unaccepted gap and may not have looked. New, escalated
  and new-use-site gate.
- When zmig improves a finding's evidence, its fingerprint changes; that's what
  `--rebase-baseline` is for.

### The baseline file

`.zmig/baseline.json`, committed, written only by `--init`, `--update-baseline` or
`--rebase-baseline`, each requiring `--reason`. It holds `zmig_version`, `zmig_build` (a
content hash of the installed package; the version string doesn't change between
commits), `fingerprint_version`, `config` (app, board, to_board, sysbuild, fast, modules,
CMake args, analyzers), `from`, `to`, the verdict, `accepted_gaps`, `rebases`, and
`accepted` entries (fingerprint, severity, category, kind, subject, title, path, value
digest, evidence set, reason, `accepted_by`, `accepted_at`, `accepted_commit`, optional
`until`).

- **`--init`** refuses to overwrite. A missing baseline exits 2 printing the `--init`
  command to run.
- **`--update-baseline`** rewrites `accepted` and `accepted_gaps` to this run's breaking
  and risky findings and gaps; surviving entries keep their who/why/when/until, new ones
  get the new reason and optional `--until`.
- **Who:** `$GITHUB_ACTOR`, else `git config user.name`, else the login; UTC; `HEAD` of the
  repository holding the baseline.
- **Expiry:** past `until`, an entry no longer counts; its finding is new again,
  "acceptance expired", so accepted debt can't be forgotten.
- **Config must match,** else exit 2 listing differences. Paths under the repository are
  stored as `<repo>`, so laptop and CI match.
- **Version skew:** a different `zmig_build` exits 2, never with findings, since a new zmig
  can legitimately change findings; `--update-baseline` is refused too (it would drop
  who/why). **`--rebase-baseline`** runs the new zmig against the baseline's own target
  and maps old entries by (category, kind, normalized subject) as multisets: re-mapped
  (who/why kept, `rebased_from`), dropped (gone or escalated), and found-not-accepted
  (new next run). Each rebase is appended to `rebases`.
- **Policy:** pin zmig by commit, upgrade it in a PR that includes the rebase commit, and
  protect the file with CODEOWNERS so accepting a breaking finding needs a code owner.

### Gaps

| Gap id | When | Levels, best to worst |
|---|---|---|
| `target-build` | the effective target build isn't ok | build-failed < configure-failed < other |
| `analyzer:<name>` | an analyzer isn't ok | partial < skipped < error |
| `verdict-confidence` | confidence fell below the baseline's | medium < low |

Details are normalized like subjects. A gap is **accepted** (same id, level not worse, not
expired), **unaccepted**, **worse** or **expired**; only the last three block.

### Exit codes

| Code | When |
|---|---|
| 0 | no new gating finding and every gap accepted; or `--allow-inconclusive` (with a warning) |
| 1 | internal error: crash, invariant violation, tool failure |
| 2 | usage error, missing baseline, writer without `--reason`, config mismatch, another zmig build |
| 3 | a new, escalated or new-use-site finding at a gating severity |
| 4 | a blocking gap and no new gating finding |

Gating is breaking by default; `--fail-on risky` adds risky. `--on-target-movement warn`
turns findings due to target movement into warnings (default `fail`); unattributed ones
gate like changes. A new gating finding wins over inconclusive.

### Outputs

Stdout or `--pr-comment PATH` (the comment), `--format json` (`watch.json`), `--sarif
PATH`, and `--out DIR` (`report.json`, `MIGRATION.md`, `watch.json`, `watch-comment.md`,
`watch.sarif`, `pinned/`). When `$GITHUB_STEP_SUMMARY` is set the comment is appended
there, which is how fork PRs see it. zmig needs no GitHub token.

**The comment** starts with `<!-- zmig-watch -->` so the workflow edits one comment. It
has exit code and summary, verdict and baseline verdict, pinned or moved target, new
findings grouped by file (severity, line, cause, "was risky" / "acceptance expired", "N in
this file", fingerprint, per-file breaking/risky counts), fixed and unseen entries,
accepted entries whose value changed, counts still accepted, and each gap with age, who
and why.

**SARIF** results carry `partialFingerprints["zmigFingerprint/v1"]` and `baselineState`:
`new` (also escalated), `unchanged`, `updated`, or `absent` for fixed entries (at their
file without a region: the baseline has no line). `properties.zmigState` and `zmigCause`
give the finer state.

### GitHub Actions

The example workflow is `examples/zmig-watch.yml`.
- **Triggers:** `pull_request` (pinned); nightly cron `track:next-release`, failing on
  exit 4; optional weekly `track:main`; `workflow_dispatch` as nightly.
- **`pull_request`, never `pull_request_target`:** the job runs the PR's CMake, Kconfig
  and Python, which must not get a write token or secrets. Fork PRs therefore skip the
  comment and SARIF steps; the job summary carries the comment.
- **Exit 4 on PRs** becomes a `::warning::` and exit 0 in the step's shell, so 3 still
  fails. A neutral conclusion would need the Checks API, which fork PRs can't write.
- **Cache:** mirror, workspaces and parse cache under `zmig-<version>-<hashFiles('west.yml')>`;
  the baseline is never in the key. Only default-branch scheduled and dispatched runs save
  it, so a PR before the first nightly runs cold; `--shallow` roughly halves a cold run.
- **Actions pinned by commit SHA**; the comment is posted with `gh`, and the SDK
  downloaded directly, not through an action whose west handling would fight zmig's.
- A warm-cache PR run took about 5 minutes on a hosted runner; cold, the Zephyr clone
  dominates, so cache hit rate decides the PR p95.

## Limits and future work

### Known limits

- **A target that fails early hides most comparisons.** The database covers removed
  symbols, compatibles and module needs; values, API and anything compile-level need a
  target that configures or compiles.
- **No artifact, no finding:** run-time behavior, timing, stack and heap sizing, and
  changes inside a dependency's own code.
- **Both refs must build:** their toolchains and modules, from a copy of the app
  directory. Old refs on new host tools may need `CMAKE_POLICY_VERSION_MINIMUM` or another
  Python. Linux only. Salvage edits only the app's own files.
- **Text layers are heuristics,** always tagged: the guide parser, overlay scanning, the
  lexical database (no generated symbols; modules only at scan time). The guide form
  "removed X in favor of Y" isn't parsed as a rename.
- **Kconfig choices** aren't in the symbol dump, so re-declared removed choices can't be
  checked against the source tree.
- **API diff:** layouts are clang's model without `pahole`; no Xtensa layouts; LLEXT never
  checked; same-value macro body changes invisible by design (`CONTAINER_OF`-style changes
  are left to the guide matcher); code that mostly fails to parse (C++-heavy) gets little.
- **Field upgrade:** `srcdiff` reads declarations, not semantics (a new CRC with the same
  struct is only "implementation changed"); seeds need maintenance; uniform sectors are
  assumed; updatability is a prompt regex; only MCUboot; the wire allowlist is a starting
  set.
- **Hops:** boundaries, step size and the LTS fallback are hand-maintained data.
- **Watch:** zmig upgrades can change fingerprints (rebase needed); fork PRs can't comment
  or upload SARIF; how code scanning shows `absent` and region-less results, and Actions
  cache limits under nightly churn, are unverified.
- **First run** clones Zephyr in full (about 1 GB).

### Future work

Each item is tracked in [GitHub issues](https://github.com/beriberikix/zmig/issues), with
the evidence behind it.

- **Partial-clone mirror** (`--filter=blob:none`): a smaller first run, at the cost of lazy
  fetches mid-analysis (worst in fork triage) and offline reruns; it would need
  `GIT_NO_LAZY_FETCH=1` and every missing blob reported as not checked.
- **Mined fixes (change database Phase 2):** for a removed or changed token, mine how
  Zephyr adapted its own callers between the refs (`git log -S`, paired per hunk, ranked
  by agreeing files, never above medium), cached with the database; it would also say
  which kind of file a fix belongs in.
- **More database sources:** header moves, devicetree property and board renames, a
  curated per-release overlay in the repo with `checked` dates, a user overlay.
- **`zmig verify`:** post-migration verification.
- **Codemods beyond Kconfig assignments:** includes, devicetree, APIs, and Kconfig-file
  overrides once a measured case passes the checks above.
- **Security advisories** as an analyzer.
- **API diff:** the full syscall-list diff, devicetree macros resolved to exact nodes at
  their instantiation sites.
- **Watch:** `--accept <fingerprint>`, a Checks-API neutral conclusion, baseline schema
  validation, a watch mode for `plan`.
- **Other bootloaders** and sysbuild images beyond `MAIN`/`BOOTLOADER`; macOS and Windows.
