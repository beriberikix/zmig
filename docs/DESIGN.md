# zmig design

Status: reviewed 2026-10-27. Decisions are recorded in §12.

zmig tells a developer what a Zephyr upgrade changes **for their project**. It
intersects the upstream change set with what the project actually builds and
uses. Every finding is derived from a real artifact (resolved `.config`,
`edt.pickle`, SBOM, git objects, the target tree's own docs) and is reproducible
from the inputs recorded in the report. No LLM is involved.

---

## 1. Environment inspection (2026-10-27)

| Item | Found | Consequence |
|---|---|---|
| Python | 3.14.4 system; `uv` 0.12 available | Zephyr main requires **3.12+** (`PYTHON_MINIMUM_REQUIRED 3.12`; v3.6.0 required 3.8). zmig matches this and requires 3.12+ (§12). |
| west | not on PATH; v1.5.0 in `~/zephyr-hci/.venv` and `~/code/.venv` | zmig must locate west (flag, `$VIRTUAL_ENV`, workspace `.venv`) and fail clearly if it's missing. |
| CMake / Ninja / dtc | 4.2.3 / 1.13.2 / 1.7.2 | CMake 4 removed compatibility with `cmake_minimum_required(<3.5)`. Old module CMakeLists may need `CMAKE_POLICY_VERSION_MINIMUM=3.5`. Check this at M1. |
| Host gcc | `gcc -m32` works | `native_sim` (32-bit) builds with no SDK. |
| Zephyr SDK | 1.0.1 at `~/zephyr-hci/sdk`, registered in the CMake package registry | Fine for new refs. Probably rejected by v3.6.0's SDK version check (expects 0.16.x). That only matters for `qemu_*` boards on old refs. |
| Zephyr checkout | `~/zephyr-hci/zephyr`: full history, main at `v4.5.0-rc1-513`, clean tree, all release tags v3.6.0 through v4.4.2 | Not used by zmig. Fixtures clone fresh from GitHub (§12). |
| Other workspaces | `~/code/presto-zephyr` (shallow, no tags), `~/code/pico-coex/zephyr` (a real fork: `fork` remote, local changes), `~/.pico-sdk/zephyr_workspace/zephyr` (no `.git`, so an untracked copy) | Real-world test material for fork detection later. Not used as the primary fixture. |
| Kconfig / DT libraries | Not pip-installed. `scripts/kconfig/kconfiglib.py` and `scripts/dts/python-devicetree` are vendored in every ref, including v3.6.0. | Load them **from each ref's own tree**, as the brief asks (see §4.3). |
| `west spdx` | main: `--init` is deprecated, and `CONFIG_BUILD_OUTPUT_META` makes the build request the CMake file API. v3.6.0: `west spdx --init -d BUILD` is required **before** configuring. | The SBOM step must be version-aware. We detect the behavior from that ref's `spdx.py`, not from a version table. |
| Migration guides | `doc/releases/migration-guide-{3.5..4.5}.rst` and `release-notes-*.rst` present | A large hop (3.6 → 4.4) needs every guide in between, read from the **target** ref. |

`~/code/zmig` is empty and is not a git repository.

---

## 2. Architecture overview

```
               ┌───────────── refs.py ─────────────┐
 --from/--to → │ resolve tag/SHA/branch/path → Ref │
               │ (sha, source repo, dirty?, kind)  │
               └──────────────┬────────────────────┘
                              ▼
 workspace.py   per-ref west workspace in the cache (zephyr worktree + modules
                at that ref's manifest revisions, narrowed to what's needed)
                              ▼
 buildctx.py    isolated configure/build per (ref, board, app, config) → BuildContext
                (artifact locator: .config, zephyr.dts, edt.pickle, compile_commands,
                 CMake file API reply, map/ELF, build log, SPDX docs)
                              ▼
 extract/       per-tree helper scripts run in a subprocess with that ref's
                kconfiglib/edtlib → normalized JSON snapshots (cached)
                              ▼
 usage.py       ProjectUsage: what the app references (symbols, compatibles,
                node labels, headers, APIs, file:line for each)
                              ▼
 analyzers/     each: (old, new, usage) → AnalyzerResult(findings, status)
                              ▼
 findings.py    Finding model, stable IDs, ordering, JSON schema
                              ▼
 report/        json | markdown (MIGRATION.md) | human | sarif
```

Package layout, as specified in the brief:

```
zmig/
  cli.py            argparse; subcommands plan/scan (+ stubs for fix/verify/watch)
  refs.py           Ref resolution, cache-owned mirrors and worktrees
  workspace.py      west manifest loading/diffing, per-ref workspace materialization
  forks.py          fork/patch classification (used by plan)
  buildctx.py       BuildContext, the build runner, artifact locator
  tools.py          external tool discovery + logged subprocess wrapper
  usage.py          ProjectUsage indexer
  extract/          helper scripts executed inside a ref's Python env
    kconfig_dump.py
    edt_dump.py
  docs/             migration-guide / release-note RST parser
  analyzers/
    __init__.py     Analyzer protocol, registry
    manifest.py     (plan)  west.yml diff
    forks.py        (plan)  local-commit classification, unrecorded forks
    hops.py         (plan)  intermediate-hop suggestion
    board.py        (scan)  board name/qualifier resolution across versions
    kconfig.py      (scan)  resolved .config diff
    devicetree.py   (scan)  final DT + binding diff
    buildlog.py     (scan)  warnings and ROM/RAM deltas
    guide.py        (scan)  migration-guide ∩ project usage
    sbom.py         (scan)  SPDX diff and compiled-file scoping
  findings.py
  report/{json,markdown,human,sarif}.py
tests/
docs/DESIGN.md
```

---

## 3. Findings schema

### 3.1 Finding

The values below are illustrative and not taken from a real run.

```json
{
  "id": "KCONFIG-REMOVED-3f9a1c2e",
  "category": "kconfig",
  "kind": "symbol-removed",
  "subject": "CONFIG_NATIVE_SIM_SLOWDOWN_TO_REAL_TIME",
  "title": "Kconfig symbol set in prj.conf no longer exists",
  "severity": "breaking",
  "confidence": "high",
  "old": "y",
  "new": null,
  "evidence": [
    {"type": "file_line", "path": "app/prj.conf", "line": 4,
     "text": "CONFIG_NATIVE_SIM_SLOWDOWN_TO_REAL_TIME=y"},
    {"type": "artifact_diff", "artifact": "zephyr/.config",
     "old_ref": "v3.6.0", "new_ref": "v3.7.0",
     "old": "CONFIG_NATIVE_SIM_SLOWDOWN_TO_REAL_TIME=y", "new": null},
    {"type": "doc_entry", "doc": "doc/releases/migration-guide-3.7.rst",
     "line": 212, "ref": "v3.7.0", "entry_id": "MG-3.7-0041"}
  ],
  "action": "Replace with CONFIG_NATIVE_SIM_REAL_TIME (see migration guide 3.7).",
  "related": ["MG-3.7-0041"],
  "analyzer": "kconfig",
  "heuristics": []
}
```

Field rules:

- **`id`** = `{CATEGORY}-{KIND}-{hash8}`. The hash is sha256 over `(analyzer, kind, subject, old_ref_sha, new_ref_sha, board)`. The same inputs always give the same ID, so `watch` can later diff reports by ID.
- **`category`**: `manifest | fork | hop | board | kconfig | devicetree | build | guide | sbom`.
- **`severity`**:
  - `breaking`: the build fails, or something the project relies on is gone or renamed. Examples: a removed symbol set in prj.conf, a removed node label referenced from C, a dropped compatible used in an overlay, a conflicting local patch.
  - `risky`: it still builds but behavior may differ. Examples: a silently changed default, a value flipped by a new dependency, a changed property value, an upstream change to a file the project compiles, a ROM/RAM delta above a threshold.
  - `info`: anything else relevant (new module, re-pinned revision with no local patches, an upstreamed patch you can drop).
- **`confidence`**:
  - `high`: read directly from a resolved artifact or from git object identity (patch-id or blob hash).
  - `medium`: a structured match that relies on naming (subject-line commit match, or a guide entry linked through an RST role such as ``:kconfig:option:`CONFIG_X` ``).
  - `low`: a text heuristic (a free-text guide entry matched by tokens, or an API usage inferred from a source token scan).
- **`heuristics`** lists every heuristic that contributed, by name (e.g. `["guide.token-match"]`). The report can then explain each low-confidence finding.
- **`evidence`** types: `file_line`, `artifact_diff`, `git_commit` (sha, subject, patch-id), `git_conflict` (files), `doc_entry`, `tool_output` (command id plus an excerpt).
- **`old`/`new`** are opaque JSON values specific to the analyzer. Each analyzer documents them.

### 3.2 Report envelope (`report.json`)

```json
{
  "schema": "https://zmig.dev/schema/report/1",
  "schema_version": "1.0",
  "zmig_version": "0.1.0",
  "command": "scan",
  "inputs": {
    "from": {"spec": "v3.6.0", "sha": "…", "kind": "tag", "dirty": false},
    "to":   {"spec": "v3.7.0", "sha": "…", "kind": "tag", "dirty": false},
    "app": "fixtures/app", "app_tree_hash": "…",
    "board": {"from": "native_sim_64", "to": "native_sim/native/64"},
    "build_mode": "configure"
  },
  "environment": {"python": "3.14.4", "west": "1.5.0", "cmake": "4.2.3",
                  "dtc": "1.7.2", "toolchain": "host"},
  "analyzers": [{"name": "kconfig", "status": "ok"},
                {"name": "sbom", "status": "skipped",
                 "reason": "CONFIG_BUILD_OUTPUT_META unsupported at v3.6.0 for native_sim"}],
  "summary": {"counts": {"breaking": 3, "risky": 7, "info": 12},
              "top": ["KCONFIG-REMOVED-3f9a1c2e", "…"]},
  "findings": [ … ]
}
```

- The report contains **no timestamps, absolute cache paths, or invocation lists**, so it is byte-stable for identical inputs. That holds whether or not the cache was warm (verified in M1). Paths are relative to the app or workspace.
- The commands that ran are recorded separately, in `logs/invocations.json` next to the per-command logs, with the cache root and home directory redacted. A cache hit changes which commands run, so they are kept out of the report.
- Findings are sorted by (severity, confidence, category, id).
- **`top`** holds the first five **breaking or risky** findings in that order, never `info`. It gives breaking findings with file_line evidence pointing into the user's files a small boost, because those are actionable.
- An analyzer that cannot run is reported as `skipped` or `error` with a reason. **It never silently drops out.**
- A JSON Schema file ships in `zmig/schema/report-1.json`, and tests validate against it.

---

## 4. Core components

### 4.1 Ref resolution and isolation (`refs.py`)

`--from`/`--to` accept a tag, SHA, branch, or local path. A path is
classified as a git checkout or, if it has no `.git`, as an *untracked tree*.

**Never mutating the user's tree.** A `git worktree add` against the user's repo
writes into their `.git/worktrees`. That changes their repo, if only slightly.
Instead:

1. zmig creates a **cache-owned bare mirror**, cloned fresh from the project's upstream URL with `git clone --bare`, at `<cache>/mirrors/<url-slug>.git`. Later runs `git fetch` into it.
   - When a ref is a local path, zmig `git fetch`es that path's commits into the mirror. Fetching only reads from the source repo, so nothing is written to it.
2. Worktrees are created off that mirror: `<cache>/worktrees/<sha>/`.
3. Uncommitted changes in a path ref are snapshotted. The diff is recorded, `dirty: true` is set, and the dirty state is applied to the worktree copy. A dirty ref also produces an `info` finding.

The cache lives in `$ZMIG_CACHE` or `$XDG_CACHE_HOME/zmig`, with
`mirrors/`, `worktrees/`, `workspaces/`, `builds/`, and `snapshots/` under it.
`zmig cache {ls,prune}` comes later.

### 4.2 Per-ref workspace (`workspace.py`)

A build at ref X needs modules at X's manifest revisions. For each ref:

- Load the manifest **through west's own `west.manifest` API**, so imports, allowlists, and groups resolve exactly as west resolves them.
- Materialize `<cache>/workspaces/<sha>/` with `west init -l` on the worktree. Then run `west update --narrow`, restricted to the modules the project needs: the user's own manifest allowlist, plus anything the build log reports missing. Module clones come from upstream and stay in the cache, so repeat runs don't touch the network.
- Pass `ZEPHYR_MODULES` explicitly to the build so module discovery is deterministic.

### 4.3 Build context (`buildctx.py`)

Each context is keyed by `(ref sha, board, app tree hash, extra conf/overlay
hashes, mode, sysbuild)`, at `<cache>/builds/<key>/`.

- **Modes:**
  - `full` (default): configure and build. Produces the ELF, map file, object files (for `nm`-based API usage), and build warnings.
  - `--fast`: CMake configure only. Produces `.config`, `zephyr.dts`, `edt.pickle`, and `compile_commands.json`. Analyzers that need build outputs report `skipped`, and API usage falls back to the low-confidence token scan.
- We invoke `west build -p always -b <board> -d <dir> <app> -- <cmake args>` under the ref's workspace. `ZEPHYR_BASE` is set explicitly, and `ZEPHYR_TOOLCHAIN_VARIANT=host` is used for native_sim.
- **The app is built from a copy** at `<workspace>/apps/<content hash>/<name>`. Zephyr's scripts locate the west workspace by searching upward from the app's directory (`west spdx` does). Found in M5: with the app under a directory that is itself inside another west workspace, the SBOM came out empty. Building a copy inside zmig's workspace keeps every such lookup isolated.
- **SBOM preparation:** zmig writes the empty CMake file-API query (`.cmake/api/v1/query/codemodel-v2`) before configure. That is exactly what `west spdx --init` does on v3.6 through v4.4, so no per-version branching is needed.
  - It sets `-DCONFIG_BUILD_OUTPUT_META=y` on both builds; every version's `west spdx` requires it, and setting it on both keeps it out of the diff.
  - After a successful build it runs that ref's `west spdx`. Its exit status isn't trusted: v3.6 exits 0 after "bailing". A missing SBOM is reported with spdx's own error.
  - SPDX needs a completed build (`zephyr_meta.yml`), so a target that doesn't compile has no SBOM, and the analyzer says so.
- **Artifact locator:** exposes typed paths and raises `ArtifactMissing` with the producing step named.
- **Snapshot extraction:** we run `extract/kconfig_dump.py` and `extract/edt_dump.py` with **that ref's Python interpreter and `sys.path` pointing into that ref's `scripts/`**. Each writes a normalized JSON snapshot. Why subprocesses:
  - Two `edtlib`/`kconfiglib` versions can't coexist in one process.
  - The pickle in `edt.pickle` is only valid for the edtlib that wrote it.
  - It gives exact parity with the real build.
- **Kconfig environment.** Kconfiglib needs the same environment variables the build used. Resolved in M3: Zephyr's `build.ninja` carries that exact environment on the `menuconfig` target's command line (`cmake -E env K=V … menuconfig.py <Kconfig root>`). This was verified at v3.6.0, v3.7.0, and v4.4.2, so it is parsed rather than reconstructed.
- **Extractor robustness:** the extractors index kconfiglib's tuples instead of unpacking them, because their shapes changed after v3.7.
- **Phandles** are recorded by the target's label when it has one, so renaming the target doesn't show up as a change in every node that references it.
- **Salvage** (added in M3). Zephyr turns every Kconfig warning and devicetree binding violation into a configure error, so a real migration's target configure normally fails.
  - zmig parses Zephyr's own messages into high-confidence findings. It then disables exactly the offending lines in a cache copy of the app, recording each one, and configures again, up to 4 rounds.
  - The analyzers compare against the salvaged build. The report lists every edit, and the user's app is never touched.

### 4.4 External tools (`tools.py`)

Every subprocess goes through one wrapper. It:

- logs argv, cwd, the relevant env keys, exit code, and duration;
- stores output under `<out>/logs/`;
- records each invocation in `logs/invocations.json`.

Tool discovery order: explicit flag, then `$VIRTUAL_ENV`, then `<workspace>/.venv`,
then PATH. Missing tools produce a one-line actionable error, for example:
`west not found; activate your Zephyr venv or pass --west /path/to/west`.

### 4.5 Project usage index (`usage.py`)

This is what "for your project" means. All entries carry file and line.

| Kind | Source | Confidence |
|---|---|---|
| Kconfig symbols set | `prj.conf`, `boards/*.conf`, extra conf files, app `Kconfig*` (`depends on`/`select`) | high |
| Kconfig symbols read | `CONFIG_\w+` / `IS_ENABLED(...)` in app C/H/CMake | high (lexical, exact name) |
| DT references | overlays: node labels, paths, `compatible`, properties. C: `DT_NODELABEL`, `DT_ALIAS`, `DT_PATH`, `DT_COMPAT_*`, `DT_INST`-with-`DT_DRV_COMPAT`, `DEVICE_DT_GET*` | high for overlays, medium for C macros |
| Headers | app translation units in `compile_commands.json`, plus depfiles after a full build | high |
| APIs | undefined symbols in app object files (`nm`) after a full build. In configure mode, a token scan of app sources, intersected with identifiers from the guide. | high when from `nm`; low when from the token scan |
| Compiled files | CMake file API codemodel, cross-checked against the SBOM | high |

### 4.6 Analyzer interface (`analyzers/__init__.py`)

```python
class Finding: ...          # pydantic-free dataclass, see findings.py

@dataclass(frozen=True)
class AnalyzerResult:
    findings: list[Finding]
    status: Literal["ok", "skipped", "error"]
    reason: str | None = None

class WorkspaceAnalyzer(Protocol):          # used by `plan`
    name: ClassVar[str]
    def analyze(self, ctx: PlanContext) -> AnalyzerResult: ...
    # PlanContext (zmig/plan.py): upstream workspaces and resolved manifests at both
    # refs, the user's workspace as found on disk, and cache mirrors on demand.

class BuildAnalyzer(Protocol):              # used by `scan`
    name: ClassVar[str]
    requires: ClassVar[frozenset[Artifact]]  # e.g. {Artifact.CONFIG, Artifact.EDT}
    def analyze(self, ctx: ScanContext) -> AnalyzerResult: ...
    # ScanContext (zmig/scan.py): old build, new build (salvaged if needed), the raw
    # target build, ProjectUsage, salvage edits, board mapping, and memoized snapshots.
```

- **Registry:** built-in analyzers come first, then third-party ones via the `zmig.analyzers` entry-point group. That entry point is the extension slot for the out-of-scope work: DWARF API/ABI, MCUboot/flash layout, settings storage, advisories.
- **Missing artifacts:** if an analyzer's `requires` isn't satisfied, for example `MAP` in configure mode, the runner reports `skipped` with the reason.
- **Contract:** analyzers are pure functions of their inputs. They may read files from the contexts but must not run builds. That keeps tests fast and makes cached reruns deterministic.
- **Cross-linking:** the guide analyzer runs last and attaches `related` links to findings already emitted by the kconfig and devicetree analyzers. It does this instead of duplicating them.

---

## 5. Analyzers (MVP)

### 5.1 `plan`

**manifest**

- Diff `west.yml` at both refs, with imports resolved.
- Report projects that were added or removed, changes of `revision`, `url`, `path`, or `groups`, and changes to the import allowlist.
- Default severity is `info`. A change becomes `risky` when the re-pinned project carries local commits, or when the app uses it per the SBOM or `ZEPHYR_MODULES`.

**forks.** Run for every project in the *user's* workspace, including the manifest repo and zephyr itself.

1. **Upstream:**
   - The upstream URL comes from `--upstream name=url` if given.
   - Otherwise from the upstream Zephyr manifest at `--from` for the same project name.
   - Otherwise from a remote whose URL matches a known upstream org (`zephyrproject-rtos`).
   - **The base revision** is the manifest revision at `--from`.
2. Local-only commits = `git rev-list <merge-base(HEAD, upstream base)>..HEAD`, run in a cache mirror that has the upstream fetched.
3. Classify each commit, first match wins:
   - **upstreamed**: its `git patch-id --stable` matches a commit in `base..target-upstream`. Confidence high. If only the normalized subject line matches, confidence is medium.
     - A subject-only match is still trial-applied, like a rebase would. The finding then says whether keeping the commit would conflict. Found in M2: a real rebase stops on such a commit when its content differs.
     - The replay is sequential, onto the target plus the previously applied local commits, so it mirrors `git rebase --onto <target>`. The M2 tests compare it against a real rebase.
   - **applies-cleanly**: `git merge-tree --write-tree --merge-base=<c>^ <target> <c>` produces no conflicts. This emulates a cherry-pick with **no worktree at all** (git ≥ 2.38; this machine has 2.53).
   - **conflicts**: the same command, but it reports conflicted paths. We list those files.
   - **overlaps** (an orthogonal flag): the commit touches files upstream also changed in `base..target`. We list both sides' commits.
4. **Vendor distributions** (NCS-style): we run the same algorithm with the vendor manifest as "user" and upstream Zephyr as "upstream". Commits are grouped per vendor fork project.

**Unrecorded forks**

- *Git checkouts:* compare `git ls-tree -r <claimed rev>` blob hashes with `git hash-object` over the checked-out files (modified, added, deleted).
- *Untracked trees* (like `~/.pico-sdk/zephyr_workspace/zephyr`): infer the claimed revision from `VERSION`, or from `--assume-rev`, and hash the files the same way.
- Any mismatch produces a `fork` finding: high confidence for content, medium for the inferred revision.

**hops**

- Count the releases between the two refs using tags and `VERSION`.
- If the gap spans more than N minor releases (default 2), or crosses a flagged boundary, suggest hops. Prefer a hop at an LTS release.
- **LTS knowledge is a small data file in zmig**, `zmig/data/releases.json`: 1.14, 2.7, and 3.7, with 4.6 planned. It was sourced from the Zephyr releases page on 2026-10-27. Zephyr git doesn't mark LTS, so this is a maintained list, and findings that use it carry the `hops.release-data` heuristic.
- **Hop path:** stop at every LTS or boundary release crossed, then add filler stops so no step crosses more than `max_minor_gap` (2) minors. Each stop is the latest patch release of that minor. The suggested path is `risky` (one jump across more minors than the gap is the risk); each boundary crossed is `info`.
- **Flagged boundaries** are also a data file, e.g. "3.7: Hardware Model v2". Any guide section that changes the board or toolchain contract can add one.

### 5.2 `scan`

**board**

- Resolve `--board` at the source ref. At the target ref, if the same name no longer resolves, zmig **maps it automatically** and builds with the mapped name, e.g. `native_sim_64` → `native_sim/native/64`.
  - Zephyr drops aliases a few releases after a rename: `native_sim_64` was listed through v4.0.0. So when the target's table lacks the board, the `deprecated.cmake` of each release between the refs is searched, newest first.
  - The finding is `risky` if the alias still works at the target and `breaking` if it's gone.
- The applied app files (overlays and conf fragments, from the CMake log) are compared only when both configures got far enough to report them.
  - The primary mapping source is the target ref's `boards/deprecated.cmake` (`<old>_DEPRECATED` → new target). Zephyr itself uses it to redirect old names with a CMake warning; this was verified at v3.7.0, where `native_sim_64` → `native_sim/native/64`. That gives `high` confidence. The resolved `CONFIG_BOARD_TARGET` in the target `.config` confirms the result.
  - When no deprecation entry exists, a fallback uses the target's `list_boards.py` output plus the migration guide, at `medium` confidence.
  - The mapping is reported as a `board` finding: `risky` when the old name only works through the deprecation alias, which upstream removes in a later release; `info` when zmig had to map it.
  - If no unique candidate exists, zmig stops with a clear error listing the candidates. `--to-board` overrides the mapping.
- A board rename also renames board-specific files the app relies on (`boards/<board>.overlay`/`.conf`). Those get findings with file_line evidence.

**kconfig.** Uses the `.config` snapshots and the Kconfig tree metadata from each ref.

- **removed**: a symbol set in the old `.config` is undefined in the new tree. Breaking if the app sets it; otherwise risky.
- **renamed**: a removed symbol is linked to a new symbol by one of these, in order:
  - a guide entry (medium);
  - a new symbol whose `help` mentions the old name, or a deprecation `select`/`imply` (medium);
  - a same-value symbol with a similar name (low).
- **default-changed**: the app doesn't set the symbol, it is visible in both versions, and its value differs. Risky. Evidence includes the old and new default expressions.
- **dependency-flip**: the app sets the symbol, but the user value was ignored in the new version because of unmet `depends on`. Breaking. Kconfiglib's `user_value` vs `str_value` reveals this precisely.
- **newly-set**: the symbol is new and enabled by default. Info, or risky if it affects code size and memory (`*_STACK_SIZE`, `HEAP`).
- **deprecated**: the symbol now `depends on DEPRECATED` or selects `DEPRECATED`. Risky.
- Scope rule: all diffs are computed, but only symbols the app sets, reads, or that change a compiled file's configuration are reported above `info`.

**devicetree.** Works on the EDT snapshots: nodes, labels, compatibles, properties, and the binding path and properties for each node.

- Nodes, labels, or aliases the app references that are gone: breaking.
- Properties the app sets or reads that are removed or renamed in the binding, or newly `required`: breaking.
- Changed values on nodes the app uses: risky.
- A compatible used by the app that has no binding at the target: breaking.
- **An overlay no longer applies:** the target configure fails in DTS. We capture the dtc/edtlib error and map it back to the overlay line. Breaking.
- Changes to nodes the app doesn't reference are only counted.

**buildlog** (implemented in M3 and M5)

- Target configure and compile failures, quoting the real errors.
- **New warnings** (compiler, Kconfig, CMake) at the target that the source build didn't have. Identity is the source, the path relative to `<zephyr>`/`<app>`/`<build>`, and the message; line numbers are ignored. Risky when the warning is in app code or is a deprecation; info otherwise. Resolved warnings are listed too.
- **ROM/RAM** from the ELFs by section flags (pyelftools), matching binutils `size` exactly on the fixture. Per-symbol deltas come from the ELF symbol table. The finding is risky above +1 KiB and +5%.

**sbom** (implemented in M5)

- Run `west spdx` at both refs. Parse the tag-value documents: SPDX 2.2 at v3.6, 2.3 later, with module packages named `<m>` or `<m>-sources`.
- **Packages added or dropped** means modules. Files that appeared or disappeared from the build. Hash changes.
- **Scoping:** the compiled-file set from the target build limits which upstream changes (`git diff --stat old..new -- <files>`) are listed as "upstream changed a file you compile": risky when an app-visible header or driver for a used compatible changed, info otherwise.

**guide.** See §6.

---

## 6. Migration-guide parsing

Implemented in M4 (`zmig/guides.py`, `zmig/analyzers/guide.py`).

**Sources:**
- `doc/releases/migration-guide-X.Y.rst` for every release in `(from, to]`, plus the target's own version if it is an unreleased snapshot.
- The "Removed …" and "Deprecated …" sections of `release-notes-X.Y.rst`.
- Each document is read from the target tree, or from the release's tag if the target no longer has it. The report records which ref each document came from.

**Parsing:**
- A heuristic reStructuredText reader, not docutils. It understands underlined headings, top-level `*` bullets with indented continuations and nested lists, and directives (skipped).
- **Entries:** each top-level bullet or paragraph becomes an entry `MG-<ver>-<nnnn>` (`RN-…` for release notes), with its heading path.
- **Mentions:** each mention is typed and located to its own doc line:

| Role / pattern | Kind | Strength |
|---|---|---|
| ``:kconfig:option:`CONFIG_X` `` | kconfig | role (medium) |
| ``:dtcompatible:`vnd,x` `` | compat | role (medium) |
| ``:c:func:`` ``:c:macro:`` ``:c:struct:`` ``:c:type:`` … | ident | role (medium) |
| ``:zephyr_file:`include/….h` `` | header | role (medium) |
| ``CONFIG_X`` (in a literal or bare), ``x.h``, ``vnd,name`` | kconfig / header / compat | literal (low) |
| other ``literal`` | literal (matched against DT property names, underscore identifiers, board names) | literal (low) |

**Severity:**
- Taken from the wording of the mention's sentence, else the lead sentences of enclosing bullets, else the entry as a whole.
- "Removed / renamed / no longer / must …" → breaking. "Deprecated" → risky, and also when a removal is announced for the future. Otherwise info.
- Always tagged `guide.severity-from-wording`.

**Renames:** "A … renamed/replaced/moved … B" within one sentence, plus the list form "* ``old`` to :c:struct:`new`" under a lead that says renamed. Renamed Kconfig symbols also feed the Kconfig analyzer's replacement candidates (medium, citing the entry).

**Intersection with the app**, using `ProjectUsage`:
- Kconfig symbols set or read.
- Compatibles named in overlays or `DT_DRV_COMPAT`, or carried by nodes the app references.
- `#include`s: an exact path, or the bare basename (low, `guide.header-basename`).
- C identifiers. These count as high usage only when `nm -u` on the app's objects from the source build shows them, accepting `z_impl_<name>` for syscalls. Otherwise they're a token scan (low). Inline APIs and calls the compiler eliminated never show up in `nm`.
- DT property names set or read, and the board name.

**Gating by build facts:** a match is downgraded to info/low (`guide.gated-by-build`), with the reason in its action, when:
- every sentence naming the item is conditional ("when / if / only …") on other Kconfig options that neither resolved `.config` enables, rename partners excluded; or
- it is a compatible reached only through referenced nodes that are disabled in both devicetrees.

Verified on v3.6.0 → v4.4.2: a hawkBit entry that mentions `CONFIG_BOARD`, and a CAN-filter entry for a disabled node.

**Output:**
- One finding per (entry, item), with `doc_entry` evidence at the exact doc lines and `file_line` evidence at the app lines.
- Confidence is the lower of the guide strength and the usage strength.
- Findings are `related` to the Kconfig and devicetree findings for the same item. Links are made symmetric after all analyzers run.
- Unmatched entries are counted in a `coverage` finding; `--all-guide-entries` lists them.

## 7. Reporting

- **`report.json`**: the §3.2 envelope, validated against the shipped schema.
- **`MIGRATION.md`**:
  - Header: versions with SHAs, board(s), build mode, counts by severity, and the "Do these first" top 5.
  - Then one section per category, severity first. Each finding is a `- [ ]` checklist line with the action and linked evidence (relative `path:line`).
  - A "Low-confidence and heuristic findings" appendix, and an "Analyzers skipped" note.
- **`human`**: a compact terminal summary: the top 5, every breaking and risky finding with its first location in your files, and analyzer problems.
- **`sarif`**: SARIF 2.1.0. Only findings with `file_line` evidence in the user's files become `results`, with paths relative to the app (`APPROOT`). The output validates against the OASIS schema; a test checks this.
- **`--out <dir>`** writes `report.json`, `MIGRATION.md`, `report.sarif`, and `logs/` (including `invocations.json`).

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | tool error |
| 2 | usage error |
| 3 | breaking findings exist, only when `--fail-on breaking` is set (for the future `watch`) |

---

## 8. Dependencies and tooling

- **Runtime:**
  - `west`, for the manifest API. It also brings in PyYAML and pykwalify.
  - Nothing else. kconfiglib and edtlib come from each ref's tree via the extract subprocess.
- **Dev:** `pytest`, `ruff` (lint and format), `mypy --strict` on `zmig/`. Environment managed with `uv`, and `pyproject.toml` with hatchling.
- **Python versions:** zmig requires 3.12+, matching the latest Zephyr's `PYTHON_MINIMUM_REQUIRED`. CI runs 3.12 and the newest release. The extract subprocess still runs separately for each ref, to isolate library versions.

---

## 9. Testing and fixtures

- **Fixture Zephyr:** cloned fresh from `https://github.com/zephyrproject-rtos/zephyr` into the cache mirror (§4.1). The pairs are **v3.6.0 → v3.7.0** (primary: HWMv2 crossing; the guide is small enough to check by hand) and **v3.6.0 → v4.4.2** (the large-gap case for hop suggestions and multi-guide intersection).
- **Fixture app** (`tests/fixtures/app/`). A small native_sim app, deliberately exposed to the hop. It includes:
  - prj.conf symbols that are removed, renamed, or have changed defaults across the hop (picked by reading the actual guide, not invented);
  - `boards/native_sim_64.overlay`, which must become `native_sim_native_64.overlay` under HWMv2;
  - a DT overlay that adds a node using a binding that changed;
  - C code using an API the guide lists as changed.
- **Synthetic fork** (`tests/fixtures/make_fork.sh`). A script builds a throwaway repo from the fixture mirror at `--from`, with three local commits:
  1. a cherry-pick of a real commit that landed upstream between from and to, which should come out **upstreamed** by patch-id;
  2. a patch to a file upstream later rewrote, which should **conflict**;
  3. a patch to a file untouched in the range, which should **apply cleanly**.

  It adds a fourth case: a reworded copy of (1) with a different patch-id but the same subject, which should be upstreamed at **medium** confidence. It also makes an untracked copy of a subdirectory with one file edited, to test the unrecorded-fork check.
- **Golden tests:** each milestone checks in the real `report.json` produced from the fixture, with tool versions masked. A test reruns the tool and diffs the result against that file.
- **Manual-diff tests:** a test recomputes the `.config` diff with plain `diff` and the DT diff with `dtc -I dts -O dts` normalization. It asserts that every difference is either a finding or a counted, unreferenced change.
- **Slow marker:** tests that build carry `@pytest.mark.build`. Unit tests (RST parser, ID stability, renderers) run in under a second.

---

## 10. Milestones

Each milestone ends by running the tool on the fixture, showing the real output,
and listing what is heuristic.

| M | Scope | Done when |
|---|---|---|
| **M1** | `tools.py`, `refs.py` (mirror and worktree, path and dirty handling), `workspace.py` materialization, `buildctx.py` full build (and `--fast` configure) for native_sim at both refs, `findings.py` plus the JSON schema, `cli.py` skeleton, pyproject, ruff, pytest | `zmig scan … --analyzers none` builds v3.6.0 and v3.7.0 in isolated dirs from a fresh GitHub clone and emits a valid empty report. Rerunning hits the cache. No user repo is read or written. |
| **M2** | `plan`: manifest diff, fork classifier, unrecorded-fork check, hop suggestion | On the synthetic fork, all four commits are classified correctly. The manifest diff matches a manual `west.yml` diff. 3.6 → 4.4 suggests a hop via 3.7. |
| **M3** | extract helpers, plus the board, kconfig, and devicetree analyzers | Findings match a manual `.config` / `zephyr.dts` diff, and every app-referenced change is listed. |
| **M4** | RST guide parser, `ProjectUsage`, guide analyzer, cross-linking | Every guide hit points to a real app line, verified by a test that opens the file. A manual check against the guide for the fixture pair finds no misses among role-tagged entries. |
| **M5** | SBOM ingestion and diff, compiled-file scoping, buildlog analyzer (full mode), Markdown/human/SARIF polish, README quickstart | The SBOM diff matches the module and file changes between the two builds. `MIGRATION.md` reads well end to end. |

---

## 11. Known risks and heuristics, up front

- **Kconfig environment reconstruction** for kconfiglib (§4.3). The location differs across versions. This must be verified in M3.
- **Old refs on new host tools:** Python 3.14 with v3.6.0 scripts, and CMake 4 with old module CMakeLists. Mitigations: `CMAKE_POLICY_VERSION_MINIMUM` and a `--python` flag per ref. Verified in M1.
- **SBOM on old refs:** `west spdx` at v3.6.0 uses the `--init` flow. Its output fidelity may differ (SPDX 2.2 vs 2.3). The diff normalizes to file paths and hashes.
- **Rename detection** in Kconfig, DT, and guide text is heuristic unless the guide uses roles. These findings are always tagged.
- **API usage in configure mode** is a token scan, so its confidence is low. Full mode upgrades it via `nm`.
- **The LTS list and "flagged boundaries"** are hand-maintained data.

---

## 12. Decisions (2026-10-27 review)

1. **Fixture pairs:** v3.6.0 → v3.7.0 is primary. v3.6.0 → v4.4.2 is the large-gap secondary.
2. **Zephyr source:** cloned fresh from GitHub into the zmig cache. No existing local checkout is used.
3. **Worktrees:** created off a cache-owned mirror, never off the user's repo.
4. **Board mapping across HWMv2:** automatic. Reported as a finding; `--to-board` overrides.
5. **Build mode:** full builds by default. `--fast` does configure only.
6. **Python floor:** 3.12+, matching the latest Zephyr requirement.
7. **Repo:** a git repo, with milestone work landing as reviewable commits.
