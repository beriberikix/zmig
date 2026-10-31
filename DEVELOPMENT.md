# Developing zmig

How to set up, test and measure zmig. How it works is in [DESIGN.md](DESIGN.md); how to
propose a change is in [CONTRIBUTING.md](CONTRIBUTING.md).

## Setup

You need the prerequisites from the [README](README.md#install) (Linux, Python 3.12+,
git ≥ 2.38, CMake, Ninja, `dtc`, `gcc-multilib`), plus [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/beriberikix/zmig && cd zmig
uv sync --extra zephyr --extra mcuboot --extra api --group dev
. .venv/bin/activate
```

This is what CI installs. `pip install -e '.[zephyr,mcuboot,api]'` plus the `dev` group's
packages (pytest, ruff, mypy, types-PyYAML, jsonschema) works too.

**`PYTHONPATH`.** If your shell sets it (ROS does), unset it for everything here:
`env -u PYTHONPATH pytest`. zmig unsets it for the builds it runs, but not for itself.

## Checks

Run all three before every commit. CI runs the same on every push and pull request
([.github/workflows/ci.yml](.github/workflows/ci.yml)).

```sh
env -u PYTHONPATH pytest      # unit tests: no builds, network or SDK (about a minute)
ruff check zmig tests
mypy                          # strict, on zmig/
```

The slower tests are opt-in, by pytest marker:

| Marker | Runs | Needs |
|---|---|---|
| `build` | real Zephyr builds of the fixtures | the Zephyr mirror (about 1 GB, cloned on first run), host GCC; minutes |
| `network` | the `plan` fixture, which clones modules | network; about a minute |
| `sdk` | cross builds (nRF52840 with sysbuild and MCUboot) | Zephyr SDK ≥ 1.0 with `arm-zephyr-eabi` |

```sh
env -u PYTHONPATH pytest -m build
env -u PYTHONPATH pytest -m network
env -u PYTHONPATH pytest -m sdk
```

Run the `build` tests when a change touches building, salvage, snapshots or an
analyzer's inputs.

## Layout

```
zmig/              the package (DESIGN.md#architecture has the module map)
  analyzers/       one module per analyzer
  extract/         helper scripts run with each Zephyr ref's own Python libraries
  data/            release data, hazard seeds, the wire-format allowlist
  schema/          the report.json JSON Schema
tests/             unit tests, and fixtures/ (below)
eval/              the evaluation harness, oracles and reports (below)
examples/          the GitHub Actions workflow for `zmig watch`
scripts/           publish.sh, the pre-push scan
```

## Fixtures

Each has a README.

| Fixture | What it is |
|---|---|
| `tests/fixtures/app` | a `native_sim` app touching real v3.6.0 → v3.7.0 changes; the main scan fixture |
| `tests/fixtures/xapp` | nRF52840 DK with sysbuild and MCUboot, cross-compiled, v4.4.0 → v4.5.0-rc3 |
| `tests/fixtures/xapp-mismatch` | app and bootloader disagree on a partition: the cross-image true positive |
| `tests/fixtures/xapp-salvage` | a removed symbol in each image |
| `tests/fixtures/p1-*` | field-upgrade hazards: layout, mode, rename, BLE |
| `tests/fixtures/patch-methods` | `git apply` versus three-way patch application, for `plan` |
| `tests/fixtures/zmk-*`, `opendeck`, `hints-app`, `watch` | regressions from evaluated projects |

The fork workspace for `plan` is generated, not committed. It's built from real
v3.6.0..v3.7.0 history with fixed identities and dates, so it's identical on every machine
(a test regenerates it and compares):

```sh
python tests/fixtures/make_fork.py /tmp/fork-ws ~/.cache/zmig/mirrors/github.com_zephyrproject-rtos_zephyr.git
zmig plan --from v3.6.0 --to v3.7.0 --workspace /tmp/fork-ws
```

`expected.json` in the generated workspace lists the classification each commit should get.

Don't hand-edit `tests/fixtures/opendeck/*.json`. They're recorded output; regenerate them,
and say in the commit why they changed.

## The evaluation harness

zmig is measured against real upgrades of open-source projects. [eval/](eval/) holds:

| Path | What |
|---|---|
| [eval/EVALUATION.md](eval/EVALUATION.md) | every scored configuration and its numbers |
| [eval/ORACLE-RULES.md](eval/ORACLE-RULES.md) | how an upgrade is scored: oracles, items, recall, precision, actionability |
| [eval/oracles/](eval/oracles/) | one oracle per project: the maintainers' own changes, frozen before zmig ran |
| [eval/reports/](eval/reports/) | the dogfood reports (`DOGFOOD-N`), one per measurement or fix round |
| [eval/configs.toml](eval/configs.toml), [eval/run.py](eval/run.py) | each configuration's exact inputs, pinned to public commits, and the runner |
| [eval/baselines/](eval/baselines/) | the last scored findings of each configuration |
| [eval/scripts/](eval/scripts/) | the OpenDeck and `watch` measurement scripts |
| [eval/AUDIT.md](eval/AUDIT.md) | an independent audit that shaped the scoring rules, and the dated responses |
| [eval/SHA-REMAP.md](eval/SHA-REMAP.md) | old to new commit IDs, for SHAs the reports quote from before the history rewrite |

Re-run every configuration after any change that could move a number:

```sh
env -u PYTHONPATH python eval/run.py fetch                    # inputs, once
env -u PYTHONPATH python eval/run.py scan --out /tmp/ev       # every configuration
env -u PYTHONPATH python eval/run.py compare --out /tmp/ev    # what changed, to score
```

[eval/README.md](eval/README.md) explains `compare`, `snapshot`, and what the harness
doesn't cover.

**Clean and tuned.** A result on a project no zmig change was informed by is *clean*. Once
a fix is made because of a project, its numbers are *tuned*. Every configuration in the
harness is tuned. A clean number needs a new project, with its oracle written, committed
and pushed before zmig first runs on it ([ORACLE-RULES.md §1.4](eval/ORACLE-RULES.md)).

## Cache

Everything zmig downloads or builds lives under `$ZMIG_CACHE` (default `~/.cache/zmig`):

| Path | What |
|---|---|
| `mirrors/` | bare clones of Zephyr and modules |
| `workspaces/` | per-ref west workspaces |
| `builds/`, `salvage/` | builds, keyed by everything that affects them, and salvaged copies of apps |
| `snapshots/`, `api/`, `changes/` | Kconfig and devicetree snapshots, API declaration tables, the change database |
| `sdks/` | one of the places `doctor` looks for Zephyr SDKs |
| `eval/` | the evaluation harness's inputs and runs |

Deleting any of it is safe; it's rebuilt on the next run.

## Publishing

`scripts/publish.sh` scans the commits a push would publish (secrets, email addresses,
home paths, internal hosts, and a private pattern list kept outside the repo), and pushes
only when the scan is clean and you confirm. It never force-pushes. Allowed exceptions are
in `scripts/publish-allow.txt`.

```sh
scripts/publish.sh --scan-only    # scan without pushing
scripts/publish.sh                # scan, then push main
```
