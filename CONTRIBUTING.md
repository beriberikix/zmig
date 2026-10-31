# Contributing to zmig

The most useful contribution right now is a report of how zmig did on a real upgrade of
your project. Code changes are welcome too. Read [DEVELOPMENT.md](DEVELOPMENT.md) for setup
and [DESIGN.md](DESIGN.md) for how zmig works.

## Report how zmig did on your project

Pick an upgrade you already did, or one you're about to do. If you already did it, the
commits that did it are the answer key.

1. Run `zmig doctor --board <board> --sdk <SDK dir>` on a good connection, and fix every
   `FAIL`.
2. Run `zmig plan` and `zmig scan` with the build you normally use: the board,
   `--sysbuild` if you use it, extra modules, and your CMake arguments
   (`--cmake-arg=-DEXTRA_CONF_FILE=…`, `-DSHIELD=…`, `-DFILE_SUFFIX=…`).
3. Optionally, run `zmig fix kconfig` with the same arguments, as a dry run first.
4. [Open an issue](https://github.com/beriberikix/zmig/issues/new?template=results.md)
   with the "Results on my project" template. It asks for:
   - **each finding you have an opinion on:** useful, noisy (true but irrelevant, or a
     duplicate) or wrong, with its ID (`KCONFIG-…`) from MIGRATION.md or `report.json`;
   - **what zmig missed:** each change you had to make that zmig didn't mention, with the
     file and line, and the commit if you have one;
   - **how long each command took**, and whether it was the first run;
   - **anything you had to work out** to get the build running under zmig.

`report.json` and `logs/invocations.json` help, but **check them before attaching**. They
contain paths from your machine, including your home directory.

## Report a bug

[Open an issue](https://github.com/beriberikix/zmig/issues/new?template=bug.md) with the
command, zmig's version (`zmig --version`, or the commit), and the last 20 lines of
stderr. For a wrong finding, include the finding and the evidence line it cites.

## Change the code

Open an issue first for anything bigger than a small fix, so the approach can be agreed
before you write it. Then:

1. **Branch from `main`.** Pull requests are merged by fast-forward, so keep the history
   linear and each commit reviewable on its own.
2. **Write the failing test first.** Every fix needs a regression test. If the bug came
   from a real project, take the shape of the test from that project's output.
3. **Run the checks** in [DEVELOPMENT.md](DEVELOPMENT.md#checks): `pytest`,
   `ruff check zmig tests` and `mypy`. Run the `build` tests if you touched building or an
   analyzer's inputs.
4. **Re-run the evaluation harness** if the change can move a finding, and paste the
   before/after `eval/run.py compare` output into the commit message.
5. **Say so in the pull request if the change:**
   - changes the report schema (`zmig/schema/`, `SCHEMA_VERSION`);
   - changes finding IDs, or the fingerprints `zmig watch` matches on. Committed
     baselines in users' repos depend on them, so it needs `--rebase-baseline` support;
   - changes the scope of an existing behaviour, for example drops or narrows a check.

### Rules that keep the findings trustworthy

- **Findings come from artifacts and the Zephyr tree, never from a model.** A heuristic is
  allowed, but it must be named in the finding's `heuristics`, and its confidence must be
  `medium` or `low`.
- **Never invent a fix.** A `fix` edit is filled in only when the finding's own facts give
  it ([DESIGN.md](DESIGN.md#findings-and-reports)).
- **An analyzer that can't run says why.** It reports `skipped` or `error` with a reason;
  it never drops out silently.
- **zmig never writes to the user's tree,** except `fix kconfig --apply`, on a new branch.
- **Reports stay byte-stable.** No timestamps, machine paths or unordered output in
  `report.json`.

### Rules for evaluation results

These are in [eval/ORACLE-RULES.md](eval/ORACLE-RULES.md). In short:
- An oracle is written, committed and pushed **before** zmig first runs on that project,
  and isn't edited afterwards. A correction goes in a separate, dated file.
- Each result is marked **clean** (no zmig change informed by that project) or **tuned**.
- Numbers in [eval/EVALUATION.md](eval/EVALUATION.md) come from a scored run, never from
  hand-editing.
- [eval/AUDIT.md](eval/AUDIT.md) isn't edited; responses are appended as dated sections.

## License

By contributing, you agree that your contributions are licensed under the
[Apache-2.0 license](LICENSE).
