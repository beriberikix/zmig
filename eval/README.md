# Evaluation harness

Re-runs every scored configuration in [docs/EVALUATION.md](../docs/EVALUATION.md) with its
inputs pinned to public commits, and shows what changed against the last scored run. Use it
after any change that could move a number, and before editing EVALUATION.md.

```sh
env -u PYTHONPATH .venv/bin/python eval/run.py fetch                      # inputs, once
env -u PYTHONPATH .venv/bin/python eval/run.py scan --out /tmp/ev          # every configuration
env -u PYTHONPATH .venv/bin/python eval/run.py compare --out /tmp/ev       # what to score
env -u PYTHONPATH .venv/bin/python eval/run.py scan --out /tmp/ev-old --zmig <commit> bms-A1
```

## Files

- **`configs.toml`:** one entry per EVALUATION.md row.
  - Each entry has its exact invocation (board, refs, modules, SDK, CMake arguments), its
    oracle, and which run its baseline came from.
  - Each input is a `[sources.*]` entry: a public repository at a full commit SHA, plus any
    submodules the scored tree had initialized.
- **`run.py`:**
  - `fetch` clones inputs into `$ZMIG_EVAL_DIR` (default `~/.cache/zmig/eval`).
  - `scan` runs zmig. Use `--zmig REF` to run another commit from a git worktree.
  - `compare` diffs findings by ID against the baselines.
  - `snapshot` makes a new baseline from a scored report.
- **`baselines/<config>.json`:** the scored run's findings, as ID, severity, confidence, kind
  and title. There are no paths or environment details, so they can be checked in.

## What `compare` does and doesn't do

It lists, per configuration, the breaking/risky findings that are new, gone, or changed
severity, matched by finding ID (ORACLE-RULES §3: one ID is one finding). It exits 1 when a
breaking/risky finding needs a verdict.

It doesn't score. Verdicts and recall stay a person's job, against the oracle
(ORACLE-RULES §2–3):
- An unchanged finding keeps its published verdict.
- A finding that became info leaves the precision denominator.
- A new or changed one needs a verdict against both refs.

After scoring, record the result (a DOGFOOD report and an EVALUATION.md row), then
`snapshot` the new reports so the next comparison starts from them.

Numbers from this harness are **tuned**: every configuration here has already been scored
and has informed fixes (EVALUATION.md, "Clean vs tuned").

## Reproducibility, as checked

Every input was diffed against the tree the scored run used. All were identical apart from
`thingset-zephyr-sdk/src/certs/isrgrootx1.der.inc`, which a build generates. Each
configuration was then re-run with the zmig commit that was scored, and compared with its
baseline. Results are in [docs/DOGFOOD-7-harness.md](../docs/DOGFOOD-7-harness.md).

## Not covered

- **OpenDeck** (scan and plan). Its inputs are two Zephyr trees with the project's 31
  patches applied by scripts (`~/.cache/zmig/dogfood/d6/{trees,patched,run}.sh`). Re-run it
  with `OUT=<dir> bash ~/.cache/zmig/dogfood/d6/run.sh`. The scored reports are
  `d6/rescore2/scan` and `d6/rescore3-withdir/plan`.
- **smp_svr and the P1 fixtures.** smp_svr is a single field-upgrade hazard. The P1
  fixtures are asserted by `tests/test_p1_fixture.py` and `tests/test_cross_fixture.py`.
