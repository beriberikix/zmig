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

## Quickstart

**Time:** the first run makes an about 1 GB mirror clone of Zephyr into `~/.cache/zmig`:
roughly 20 minutes at 1 MB/s, hours on slow wifi. How long later runs take hasn't been
measured yet (docs/EVALUATION.md).

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

**0. Check the machine**

```sh
zmig doctor --board native_sim_64     # or the board you will scan, and --sdk <dir>
```

This checks git (≥ 2.38), CMake, Ninja, `dtc`, the host GCC (32-bit for `native_sim`), the
Python packages, and a Zephyr SDK with the board's toolchain. Each failed check says what
to install. The SDK check reads the board's architecture from the cached Zephyr mirror, so
it can only match the board after the first `scan` or `plan`.

If `PYTHONPATH` is set (ROS sets it, for example), zmig warns and removes it from every
command it runs. If zmig itself then fails to import, run it as
`env -u PYTHONPATH zmig …`.

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

The first run clones Zephyr (about 1 GB: roughly 20 minutes at 1 MB/s, hours on slow wifi)
into `~/.cache/zmig`; later runs reuse it and every build. Add `--module <name>` for each west module your app needs, `--fast` for a
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
| kconfig | final `.config` and each tree's Kconfig metadata (via its own kconfiglib) | symbol you set was removed (with likely replacement), value no longer takes effect, deprecated, value your code reads changed, a symbol your settings depend on/select changed (with the chain) |
| devicetree | final devicetree (via each tree's edtlib) against your overlays and `DT_*` macros | node your code references is gone, property you read has no value, property you set is deprecated |
| buildlog | the target build | configure/compile failures with the real errors, new warnings (a warning raised in a Zephyr header is reported at your call site), ROM/RAM delta with top symbols |
| initorder | the init calls in both ELFs, read by each version's own `check_init_priorities.py` (v3.5+) | your `SYS_INIT`/device init now runs at another level, or Zephyr inits it relied on now run after it |
| sbom | `west spdx` at both versions | modules added/dropped/re-pinned, compiled files added/removed/changed, upstream commits touching them, headers you include that changed |
| guide | migration guides and release notes for every release crossed | entries mentioning symbols, compatibles, headers or APIs you use, with guide line and your line |

Your own modules (passed with `--cmake-arg=-DZEPHYR_EXTRA_MODULES=...`) count as your code:
findings in them are shown as `<module>/<path>:<line>`. Findings whose only evidence is in
`.conf`/overlay/devicetree files this build doesn't use (another board's `.conf`, say) are
kept as info and say so. Long steps (clones, builds) print progress every minute.

If the target build fails because it needs a Zephyr module the scan wasn't given (a header
only that module's glue ships, a Kconfig dependency on `ZEPHYR_<X>_MODULE`, or a CMake error
naming it), zmig reports the module, where it's required and where the target's `west.yml`
declares it, then rebuilds the target with it so the rest of the analysis still runs.

When the target configure fails (Zephyr turns Kconfig and devicetree warnings into
errors, so it usually does), zmig reports Zephyr's errors as findings, then disables
exactly those lines in a copy of your app and compares the rest. The report lists every
disabled line.

## Field-upgrade verdict (`--sysbuild` builds, or any build with persistent state)

`scan` also answers: can a device running the `--from` build take the `--to` build by app
OTA? The report's `upgrade_path` (and the "Field-upgrade verdict" section of MIGRATION.md) gives
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

## `zmig fix kconfig`: renames with Kconfig evidence only

```sh
zmig fix kconfig --app path/to/app --board <board> --from v3.6.0 --to v3.7.0          # dry run
zmig fix kconfig --app path/to/app --board <board> --from v3.6.0 --to v3.7.0 --apply  # commit
```

It takes the same arguments as `scan`. It checks every assignment in your own Kconfig
fragments: `prj.conf`, board `.conf` and defconfig files, per-image files such as
`sysbuild/mcuboot.conf`, `EXTRA_CONF_FILE`/`OVERLAY_CONFIG` fragments, and `SB_CONFIG_*`
in `sysbuild.conf`.

It renames a symbol only when all of these hold:

- the target's Kconfig tree no longer defines it;
- the source tree shows what replaced it: the old symbol was deprecated and selects the
  new one, or one is `default` the other;
- the replacement has the same type and a prompt;
- the target's own kconfiglib gives it the assigned value;
- no migration-guide entry about either symbol mentions a value or unit change.

Everything else is listed, not edited:

- renames only the guide names;
- symbols that are still defined but whose dependencies are unmet;
- fragments outside the project.

`--apply` refuses a dirty tree. It commits each kind of evidence separately on a new
branch, then rebuilds the target and reruns the analyzers. It exits 3 only if the fix
introduced a finding; findings that an earlier configure failure had hidden are reported
as revealed. Results: [docs/P3-FIX-KCONFIG.md](docs/P3-FIX-KCONFIG.md).

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

Not implemented yet (extension points exist): `verify`, codemods for C sources (the fix
command renames Kconfig assignments only), security advisories.
