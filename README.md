# zmig

A migration assistant for Zephyr RTOS upgrades. Zephyr's release notes cover everything
that changed. zmig tells you **what changed for your project**: which of your settings,
devicetree references, includes and API calls are affected, at which line, and what to do
about it.

It works from real artifacts, never from a model. zmig builds your app at both Zephyr
versions and compares what the builds produced. It reads each version's own Kconfig and
devicetree tooling, and it matches the migration guides against what your code actually
uses. Every finding has:
- a severity (`breaking`, `risky`, `info`) and a confidence;
- evidence: a file and line in your project, an artifact diff, or a guide entry;
- a suggested action, and an exact edit when the evidence gives one.

zmig never modifies your tree. It clones its own copy of Zephyr into a cache and builds
copies of your app there.

## Status

**Alpha.** The CLI, the report schema (`report.json`, schema 1.5) and finding IDs may still
change.

zmig is measured against real upgrades of open-source Zephyr projects. For each one, what
the maintainers changed is written down before zmig runs on it, then zmig's findings are
scored against that list ([eval/EVALUATION.md](eval/EVALUATION.md)). The latest result on
a project no zmig change was tuned for (edgehog, v4.3 → v4.4,
[report](eval/reports/DOGFOOD-13-edgehog-baseline.md)):
- 4 of 8 upgrade items caught at the line the maintainers changed;
- 7 of 8 caught or partly caught;
- 38 of 40 breaking or risky findings true.

See [Limitations](#limitations) before relying on it.

## Install

**Prerequisites:** Linux, Python 3.12+, git ≥ 2.38, CMake, Ninja, `dtc`, and a host GCC
with 32-bit multilib for `native_sim` (`apt install gcc-multilib`). Other boards also need
their toolchain, from the Zephyr SDK.

zmig isn't on PyPI yet. Install it from GitHub:

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install "zmig[zephyr,api] @ git+https://github.com/beriberikix/zmig"
```

| Extra | What for |
|---|---|
| `zephyr` | Python packages Zephyr's build scripts import. zmig runs `west build` with its own interpreter, so these are needed |
| `api` | libclang, for the API diff. Without it the `api` analyzer is skipped |
| `mcuboot` | MCUboot's imgtool dependencies, for sysbuild builds with MCUboot |

The first `scan` or `plan` clones Zephyr into `~/.cache/zmig`: about 1 GB, roughly 20
minutes at 1 MB/s. Later runs reuse the clone and every build.

## Quickstart

```sh
# 0. Check the machine: host tools, Python packages, the board's toolchain.
zmig doctor --board native_sim

# 1. What happens to your west workspace and your local patches?
zmig plan --workspace ~/my-workspace --from v4.3.0 --to v4.4.0 --out out/plan

# 2. What changes for your app on your board?
zmig scan --app path/to/app --board nrf52840dk/nrf52840 \
          --from v4.3.0 --to v4.4.0 --out out/scan
```

Then read, in this order:
1. **The terminal summary:** counts, whether the target built, and "Do these first".
2. **`out/scan/MIGRATION.md`:** a checklist grouped by tier (act now, review, FYI) and
   category, worst first, with links to your files.
3. **`out/scan/logs/`:** every command zmig ran, with its output. Look here when a finding
   doesn't make sense.

To try it without a project of your own, scan the bundled fixture, a `native_sim` app that
touches real v3.6 → v3.7 changes:

```sh
git clone https://github.com/beriberikix/zmig && cd zmig
zmig scan --app tests/fixtures/app --board native_sim_64 --module zcbor \
          --from v3.6.0 --to v3.7.0 --out out/fixture
```

## Commands

| Command | What it does |
|---|---|
| [`zmig doctor`](#zmig-doctor) | Checks the host tools and the toolchain a board needs |
| [`zmig plan`](#zmig-plan) | Workspace and fork triage: module changes in `west.yml`, and what happens to each of your local patches |
| [`zmig scan`](#zmig-scan) | Builds your app at both versions and reports what changed for it |
| [`zmig changes`](#zmig-changes) | What Zephyr removed, renamed or deprecated between two refs, from the tree alone (no build) |
| [`zmig fix kconfig`](#zmig-fix-kconfig) | Renames or removes Kconfig assignments where Kconfig itself says what replaced them |
| [`zmig watch`](#zmig-watch) | CI mode: scans, compares with a committed baseline, and fails on new findings |

Options every command takes:
- `--out DIR`: write `report.json`, `MIGRATION.md`, `report.sarif` and `logs/` there.
- `--format human|json|markdown|sarif`: what goes to stdout (default `human`).
- `--cache-dir DIR`: the cache root (default `$ZMIG_CACHE` or `~/.cache/zmig`).
- `-v`: verbose.

`--from` and `--to` accept a tag, a SHA, a branch, or a path to a local Zephyr checkout
(uncommitted changes included).

### `zmig doctor`

```sh
zmig doctor --board <board> [--sdk <dir>]
```

Checks git, CMake, Ninja, `dtc`, the host GCC, Zephyr's Python packages, and a Zephyr SDK
with the board's toolchain. Each failed check says what to install. Exit 1 if any check
failed. The SDK check reads the board's architecture from the cached Zephyr clone, so it
can only match the board after the first `scan` or `plan`.

### `zmig plan`

```sh
zmig plan --workspace <west topdir> --from <ref> --to <ref> [--out DIR]
```

Reports, without building anything:
- modules added, removed or re-pinned in `west.yml`;
- for every project you've forked or patched, each local commit as **upstreamed**
  (already in the target), **applies cleanly**, or **conflicts** (with the files);
- uncommitted or unrecorded changes in your checkouts;
- a suggested path through LTS releases for large jumps.

If your project applies patch files with `git apply`, pass
`--patch-method git-apply --patch-dir <dir>` so findings name the patch file.

### `zmig scan`

```sh
zmig scan --app <dir> --board <board> --from <ref> --to <ref> [options] --out DIR
```

| Option | Use |
|---|---|
| `--module NAME` | a west project the app needs, cloned into zmig's workspaces (repeatable). zmig also adds one itself when the target build needs it |
| `--cmake-arg=-D...` | passed to CMake at both versions, e.g. `-DEXTRA_CONF_FILE=…`, `-DSHIELD=…`, `-DFILE_SUFFIX=…`, `-DZEPHYR_EXTRA_MODULES=…` (repeatable) |
| `--sysbuild` | build with sysbuild; adds the field-upgrade verdict |
| `--sdk DIR` | the Zephyr SDK to build with |
| `--to-board NAME` | the board's name at the target, if zmig can't map it |
| `--fast` | configure only: quicker, but skips the analyzers that need a compiled build |
| `--shallow` | fetch modules without history |
| `--analyzers a,b` | run only these analyzers (or `none`, to just build) |
| `--all-guide-entries` | also list migration-guide entries that don't match your app |
| `--deployed-image FILE` | a signed image read from a device, for the MCUboot checks |
| `--wire-allowlist FILE` | extra wire-format values your peers depend on |

What it checks:

| Area | Examples |
|---|---|
| Board | the board was renamed (`native_sim_64` → `native_sim/native/64`); board-specific files that no longer apply |
| Kconfig | a symbol you set was removed (with its replacement), is deprecated, or no longer takes effect because of a new dependency; a default you rely on changed |
| Devicetree | a node, label or property your code references is gone; a compatible lost its binding; a property you set is deprecated |
| Build | configure and compile errors and new warnings, at your lines; a missing `#include`; ROM/RAM growth |
| API | a Zephyr function, macro, struct or type you use changed signature, type or layout, at your call sites |
| Modules | a module the target needs that your build or manifest doesn't have |
| Init order | your `SYS_INIT` or device now runs at another level, or after something it relied on |
| Compiled files | modules and files that joined or left the build; upstream changes to files you compile |
| Migration guides | guide and release-note entries that mention what you use, with the guide line and yours |
| Field upgrade | with `--sysbuild` or persistent state: can a device running the old build take the new one over the air? (below) |

If the target doesn't build, which is normal for a real upgrade, zmig reports Zephyr's
errors as findings, disables exactly those lines in its own copy of your app, and compares
the rest. The report lists every line it disabled.

**Field-upgrade verdict.** `scan` also answers whether a device running the `--from` build
can take the `--to` build by OTA: `direct-ok`, `needs-changes`, `blocked` or `unknown`. It
compares flash partitions, MCUboot's image format (measured on both signed images), storage
backends, Bluetooth settings and keys, and values peers see on the wire. It's never OK by
default: every category has to be checked clean or shown not to apply.

Exit codes: 0 ok, 1 tool error, 2 usage error.

### `zmig changes`

```sh
zmig changes --from <ref> --to <ref> [--symbol CONFIG_X|vnd,name ...]
```

Lists what the Zephyr tree itself records between two refs, with no build:
- Kconfig symbols removed, with the symbols that replaced them and when those apply;
- symbols newly deprecated;
- devicetree compatibles whose binding was removed or renamed;
- symbols that moved into a module.

`scan` uses the same database when the target build fails too early to compare.

### `zmig fix kconfig`

```sh
zmig fix kconfig <the same arguments as scan>            # dry run: the plan and a diff
zmig fix kconfig <the same arguments as scan> --apply    # commit on a new branch
```

Edits your own Kconfig fragments (`prj.conf`, board `.conf` and defconfig files, per-image
files such as `sysbuild/mcuboot.conf`, extra conf fragments, `sysbuild.conf`) only where
Kconfig evidences the change:
- a rename, when the target no longer defines the symbol, the source tree shows the
  replacement, the types match, and the target resolves the replacement to your value;
- a removal with no replacement, which it comments out with a note.

Everything else is listed under "Needs a human". `--apply` refuses a dirty tree, commits
each kind of fix separately on `zmig/fix-kconfig-<target>` (or `--branch`), then rebuilds
and re-runs the analyzers. Exit 3 means the fix introduced a finding. To undo:
`git switch -` and delete the branch.

### `zmig watch`

```sh
zmig watch --app <dir> --board <board> --to <ref> --init --reason "why"  # create .zmig/baseline.json
zmig watch --app <dir> --board <board>                    # compare; exit 3 on new findings
zmig watch ... --update-baseline --reason "why"           # accept the current findings
```

For CI. `--from` defaults to the Zephyr revision your `west.yml` pins, and `--to` to the
target recorded in the baseline. A nightly job can use `--to track:next-release` or `track:main` instead.
Findings are matched across runs by fingerprint, so line moves don't count as new. Accepted
findings can expire (`--until`), and a new use site of an accepted finding still fails.

| Exit | Meaning |
|---|---|
| 0 | nothing new |
| 2 | usage error, a config mismatch, or no baseline |
| 3 | a new breaking finding (`--fail-on risky` to include risky) |
| 4 | inconclusive: something couldn't be checked (`--allow-inconclusive` to pass) |

It writes a PR comment (`--pr-comment`) and SARIF (`--sarif`).
[examples/zmig-watch.yml](examples/zmig-watch.yml) is a GitHub Actions workflow to start
from, with PR and nightly runs and caching.

## How it works

The key techniques, grouped. [DESIGN.md](DESIGN.md) has the detail.

- **Build both versions, compare what the builds produced.** zmig compares the resolved
  `.config`, the final devicetree, the ELF (sizes, init calls), SPDX SBOMs and the build
  logs, not source text.
  [Builds](DESIGN.md#builds), [Analyzers](DESIGN.md#analyzers).
- **Use each Zephyr version's own tools.** Kconfig and devicetree are read with each ref's
  own kconfiglib and edtlib, in a subprocess, so the result matches what that version's
  build does. Board renames come from Zephyr's own `boards/deprecated.cmake`, and init
  order from its `check_init_priorities.py`. [Builds](DESIGN.md#builds).
- **Scope everything to what your project uses.** A usage index records every setting your
  fragments assign, every `CONFIG_` your code reads, every devicetree label, path and
  compatible you reference, your includes, the Zephyr symbols your object files link
  against, and the files you compile. Changes outside it are counted, not reported.
  [Project usage](DESIGN.md#project-usage).
- **Read Zephyr's own record of what changed.**
  - Kconfig: removed symbols, newly deprecated ones (`select DEPRECATED`), and their
    replacements, from deprecation `select`s, `default` aliases and help text.
  - Devicetree: bindings that were removed or renamed.
  - Modules: symbols that moved into a module's glue.
  - Migration guides and release notes for every release crossed. Entries are parsed and
    matched to your usage, and gated by build facts: an entry about a vendor's SoCs or a
    disabled subsystem doesn't apply to your build.

  [Change database](DESIGN.md#change-database),
  [Migration-guide parsing](DESIGN.md#migration-guide-parsing).
- **Diff the public API at your call sites.** zmig parses Zephyr's headers with libclang
  at both refs, for the declarations your compiled code uses. It reports signature, type
  and struct-layout changes where you use them, and follows compatibility aliases so a
  rename that still compiles isn't reported as broken. [API and ABI diff](DESIGN.md#api-and-abi-diff).
- **Keep going when the target build breaks.** zmig turns configure errors into findings,
  disables the offending lines in its own copy of your app, and builds again. It adds
  modules the target turns out to need. When the target can't be built at all, it falls
  back on the change database. [Builds](DESIGN.md#builds).
- **Put each error and warning at your line.** A warning raised inside a Zephyr header is
  reported at your call site, using the compiler's include chain and macro-expansion notes.
  A CMake warning about a `CONFIG_` value is reported at your assignment.
  [Analyzers](DESIGN.md#analyzers).
- **Triage forks with git plumbing.** zmig matches upstreamed commits by patch-id, subject
  and content similarity, and trial-applies the rest with `git merge-tree`, in its own
  clone. [Analyzers](DESIGN.md#analyzers).
- **Check field upgrades category by category.** It compares partitions in the
  bootloader's devicetree, MCUboot's header and TLV layout measured on the signed images,
  storage backends, Bluetooth key state and wire values. Anything not positively checked
  makes the verdict `unknown`. [Field-upgrade verdict](DESIGN.md#field-upgrade-verdict).
- **Only evidenced fixes.** A fix's edit is filled in only when the finding's own facts
  give it. `fix kconfig` applies only renames Kconfig evidences, and verifies them by
  rebuilding. [Kconfig fixes](DESIGN.md#kconfig-fixes).
- **Stable, reproducible output.** The same inputs give a byte-identical `report.json`,
  with no timestamps or machine paths. Finding IDs are hashes of what they're about, which
  lets `watch` compare runs. [Findings and reports](DESIGN.md#findings-and-reports),
  [CI mode](DESIGN.md#ci-mode-zmig-watch).

## Output

With `--out DIR`:

| File | For |
|---|---|
| `MIGRATION.md` | people: a checklist by tier and category, with the field-upgrade verdict |
| `report.json` | scripts and agents: every finding, with evidence, inputs and analyzer status (versioned schema in `zmig/schema/`) |
| `report.sarif` | code-scanning UIs: findings located in your files |
| `logs/` | every command zmig ran, with its output (`invocations.json` lists them) |

An analyzer that can't run is listed as skipped, with the reason, never silently dropped.
Each finding's `confidence` says how it was derived:
- `high`: from a build artifact or git identity;
- `medium` or `low`: from names or wording, with the heuristics used listed.

## Limitations

- **A target build that fails early hides most comparisons.** zmig falls back on the
  change database, but compile-level changes need a target that compiles.
- **Some upgrade items leave no trace in any build:** run-time behaviour, timing, stack
  sizes, or a dependency's own changes.
- **zmig has to build your app at both versions.** That needs the toolchains and modules
  both refs require, and an app that can be built from a copy of its directory.
- **Linux only so far.**
- If `PYTHONPATH` is set (ROS sets it, for example), zmig removes it from the commands it
  runs. If zmig itself then fails to import, run it as `env -u PYTHONPATH zmig …`.

Not implemented yet: `zmig verify`, code changes beyond Kconfig assignments, and security
advisories. Open work is tracked in [GitHub issues](https://github.com/beriberikix/zmig/issues).

## More

- [DESIGN.md](DESIGN.md): how zmig works, and why.
- [DEVELOPMENT.md](DEVELOPMENT.md): setting up, tests, fixtures, the evaluation harness.
- [CONTRIBUTING.md](CONTRIBUTING.md): reporting results on your project, and changes.
- [eval/](eval/): how zmig is measured, and every result so far.

Licensed under [Apache-2.0](LICENSE).
