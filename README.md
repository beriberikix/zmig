# zmig

A migration assistant for Zephyr RTOS upgrades. Instead of summarizing everything that
changed in Zephyr, it tells you **what changed for your project**: it builds your app at
both versions and compares the results, intersects the migration guides with what your
code actually uses, and classifies your fork's local patches against the target.

Findings come from real artifacts (resolved `.config`, the final devicetree, SPDX SBOMs,
git objects, Zephyr's own docs), never from a model. Each one has a severity, a confidence,
evidence (a file and line, or an artifact diff), and a suggested action. Output is
`report.json` (versioned schema), a `MIGRATION.md` checklist, and SARIF.

zmig never modifies your tree: it clones its own copy of Zephyr into a cache and builds
copies of your app there.

## 5-minute quickstart

**Prerequisites:** Linux, Python 3.12+, git ≥ 2.38, CMake, Ninja, `dtc`, and a host GCC
with 32-bit multilib (for `native_sim`; e.g. `apt install gcc-multilib`). Other boards
also need their toolchain (the Zephyr SDK).

```sh
git clone <this repo> zmig && cd zmig
uv venv --python 3.12 .venv            # or: python3 -m venv .venv
uv pip install --python .venv/bin/python -e '.[zephyr]'
. .venv/bin/activate
```

The `zephyr` extra installs the Python packages Zephyr's own build scripts import
(pyelftools, PyYAML, pykwalify, …, reuse for `west spdx`), because zmig runs `west build`
with its own interpreter.

**1. Plan: what happens to your workspace and patches?**

```sh
zmig plan --from v3.6.0 --to v3.7.0 --workspace ~/my-zephyr-workspace --out out/plan
```

Reports modules added, removed or re-pinned in `west.yml`; for each project you've
forked or patched, which local commits are already upstream (by patch-id, or by subject),
which apply cleanly to the target, and which conflict (and in which files); uncommitted or
unrecorded changes; and a suggested upgrade path through LTS releases for large jumps.

**2. Scan: what changes for your app on your board?**

```sh
zmig scan --app path/to/app --board native_sim_64 --from v3.6.0 --to v3.7.0 --out out/scan
```

The first run clones Zephyr (~1 GB) into `~/.cache/zmig`; later runs reuse it and every
build. Add `--module <name>` for each west module your app needs, `--fast` for a
configure-only comparison, and `--to-board` if you know the target board name.

**3. Read the results**

- Terminal: a summary with "Do these first".
- `out/scan/MIGRATION.md`: the checklist, grouped by category, worst first.
- `out/scan/report.json`: everything, for scripts or an agent.
- `out/scan/report.sarif`: findings located in your files, for code-scanning UIs.
- `out/scan/logs/`: every command zmig ran, with its output.

Try it on the bundled fixture (a native_sim app touching real v3.6 → v3.7 changes; see
`tests/fixtures/app/README.md`):

```sh
zmig scan --app tests/fixtures/app --board native_sim_64 --module zcbor \
          --from v3.6.0 --to v3.7.0 --out out/fixture
```

## What `scan` checks

| Analyzer | Compares | Examples |
|---|---|---|
| board | `boards/deprecated.cmake` (incl. aliases dropped in later releases), app files each build applied | `native_sim_64` → `native_sim/native/64` |
| kconfig | final `.config` and each tree's Kconfig metadata (via its own kconfiglib) | symbol you set was removed (with likely replacement), value no longer takes effect, deprecated, value your code reads changed |
| devicetree | final devicetree (via each tree's edtlib) against your overlays and `DT_*` macros | node your code references is gone, property you read has no value, property you set is deprecated |
| buildlog | the target build | configure/compile failures with the real errors, new warnings, ROM/RAM delta with top symbols |
| sbom | `west spdx` at both versions | modules added/dropped/re-pinned, compiled files added/removed/changed, upstream commits touching them, headers you include that changed |
| guide | migration guides and release notes for every release crossed | entries mentioning symbols, compatibles, headers or APIs you use, with guide line and your line |

When the target configure fails (Zephyr turns Kconfig and devicetree warnings into
errors, so it usually does), zmig reports Zephyr's errors as findings, then disables
exactly those lines in a copy of your app and compares the rest. The report lists every
disabled line.

## Field upgrades (`--sysbuild` builds, or any build with persistent state)

`scan` also answers: can a device running the `--from` build take the `--to` build by app
OTA? The report's `upgrade_path` (and the "Field upgrade" section of MIGRATION.md) gives
one verdict: `direct-ok`, `needs-changes`, `blocked` or `unknown`. It is never OK by
default: every category below must be checked clean or shown not to apply.

| Category | What is compared |
|---|---|
| `layout` | flash partitions in the bootloader's final devicetree: moved, resized, removed, reordered; storage partitions left behind |
| `mcuboot` | whether the deployed (source) MCUboot accepts the target's signed image: header/TLV layout read from `image.h` at each MCUboot pin, measured on both signed `.bin` files; app/bootloader mode agreement; slot geometry from `boot_slots_compatible()` |
| `storage` | settings/NVS/ZMS/secure-storage backends enabled, implicit enables via `select`/`imply`, on-media definitions; remedies verified by re-resolving the target Kconfig |
| `ble` | Bluetooth settings keys, persisted key structs, identity options |
| `wire` | a documented allowlist (`zmig/data/wire_format.yaml`, extend with `--wire-allowlist`) of values peers see |
| `sysbuild`, `images`, `crossimage` | `SB_CONFIG` changes and how they reach images; bootloader added/removed; app/bootloader disagreements |

Each hazard finding carries a `remedy` (`config-pin`, `app-change`, `intermediate-hop`,
`bootloader-update`, `reflash`, `unknown`), its evidence and provenance, and whether the
migration introduced it. Assumptions (e.g. "the deployed bootloader is the source build's
MCUboot") and what isn't checked are listed in the report.

- `--sdk <dir>`: build with this Zephyr SDK (recorded in the report; the build fails if any
  image used another).
- `--deployed-image <file.bin>`: compare against a signed image read from a device instead of
  the source build's.
- `--wire-allowlist <file.yaml>`: extra wire-format entries.

Design: [docs/DESIGN-P1.md](docs/DESIGN-P1.md).

## Determinism and trust

- Same inputs → byte-identical `report.json` (no timestamps or machine paths; finding ids
  are hashes of what they are about).
- Each finding's `confidence` says how it was derived: `high` from build artifacts or git
  identity; `medium`/`low` from names or wording, with the heuristics listed.
- An analyzer that can't run is reported as skipped with the reason, never dropped.

## Development

```sh
uv pip install --python .venv/bin/python pytest ruff mypy types-PyYAML jsonschema
pytest                    # unit tests (seconds)
pytest -m build           # real Zephyr builds of the fixture (minutes; network on first run)
pytest -m network         # plan fixture (clones modules; ~1 minute)
ruff check zmig tests && mypy
```

Fixtures:

- `tests/fixtures/app` (native_sim, v3.6.0 → v3.7.0); `tests/fixtures/xapp`
  (nrf52840dk/nrf52840 with sysbuild + MCUboot, cross-compiled, v4.4.0 → v4.5.0-rc3),
  `xapp-mismatch` (app and bootloader disagree on a partition: the cross-image true
  positive) and `xapp-salvage` (a removed symbol per image): see each README.
- The fork workspace for `plan` is generated, not committed. It is built from real
  v3.6.0..v3.7.0 history with fixed identities and dates, so it is identical on every
  machine (a test regenerates it and compares):

  ```sh
  python tests/fixtures/make_fork.py /tmp/fork-ws ~/.cache/zmig/mirrors/github.com_zephyrproject-rtos_zephyr.git
  zmig plan --from v3.6.0 --to v3.7.0 --workspace /tmp/fork-ws
  ```

  `expected.json` in the generated workspace lists the classification each commit
  should get.

If your shell sets `PYTHONPATH` (ROS does), unset it for pytest: `env -u PYTHONPATH pytest`.
zmig itself unsets it for builds.

Design, findings schema and analyzer interface: [docs/DESIGN.md](docs/DESIGN.md).
Analyzers are plugins: register a class under the `zmig.analyzers` entry-point group.

Not implemented yet (extension points exist): `fix` (codemods), `verify`, `watch` (CI),
API/ABI diffing, MCUboot/flash-layout checks, security advisories.
