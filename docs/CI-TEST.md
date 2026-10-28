# CI test checklist: `zmig watch` in GitHub Actions

Status markers:

- **passed:** ran, run linked;
- **failed:** ran, see the note;
- **not run:** with the reason;
- **unverified:** not run yet.

Every workflow input counts as unverified until a real run passes.

Run links point to `https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/<id>`;
both repos are private.

## Repositories

Both are **private**, under the `beriberikix` account. Nothing else was pushed.

| Repo | Content |
|---|---|
| `beriberikix/zmig` | this repository, from a fresh copy with the commit email rewritten to the GitHub noreply address (trees unchanged; the original is backed up as git bundles in `~/.cache/zmig/backup/`). `main`: `23c42c5`, then `c4a606c` (the fix found by run 1 below) |
| `beriberikix/zmig-ci-cannectivity` | CANnectivity's history up to `v1.1.0` (`80612ee`, Apache-2.0, attribution kept), plus commits on top: upstream CI and Dependabot removed and `UPSTREAM.md` added; the workflow and baseline; the zmig upgrade (PR #1) |

Layout of the test repo:

```
zmig-ci-cannectivity/
  app/                       upstream app (built with -DFILE_SUFFIX=usbd_next)
  west.yml                   upstream: zephyr at v4.1-branch (a branch: --from manifest warns)
  LICENSE, UPSTREAM.md
  .zmig/baseline.json        made locally by the pinned zmig commit, committed
  .github/workflows/zmig-watch.yml   no cron (to save Actions minutes); runs by workflow_dispatch
```

## How zmig is installed

```
pip install "zmig[zephyr,api] @ git+https://x-access-token:${{ secrets.ZMIG_READ }}@github.com/beriberikix/zmig@<commit>"
```

- `ZMIG_READ` is a fine-grained token: access to `beriberikix/zmig` only, Contents
  read-only. You created it and stored it with `gh secret set`.
- **Passed**, from run 40764423331 on: 11–40 s. The runner's log shows the URL with the
  token part masked (`***github.com/...`).
- The first attempt, run 40751179748, **failed**: `Invalid username or token`. The
  secret had been set from a prompt that didn't receive the paste; re-setting it from a
  terminal fixed it.

**The pinned commit must be the one whose zmig made `.zmig/baseline.json`.** A zmig
upgrade changes the pin and adds a `--rebase-baseline` commit in the same PR
(DESIGN-WATCH.md §3.6), as PR #1 did.

## Runs

| # | What | Run(s) | Result | Status |
|---|---|---|---|---|
| 1 | Nightly (`workflow_dispatch` = the nightly's path: `--to track:next-release`) | 40764423331, 40768470550 | Target resolved to **v4.4.2**: **moved** from the baseline's `30de5e8`; the pinned second scan ran. Exit **3** (3 new breaking findings, all "due to target movement"); job failed, as a nightly should. The cache was saved in the second run (1.65 GB) | **passed** (40768470550) |
| 2 | PR, nothing new | PR #1: 40765935133, 40767491146 | Exit **0**, pinned to `30de5e8`; 3 accepted gaps listed as "analysis incomplete"; the `--from manifest` branch warning shown; one PR comment, edited in place on the second run | **passed** (40767491146) |
| 3 | PR with a new breaking finding | PR #2: 40769049428 | `CONFIG_DEBUG_INFO=y` added (v4.2 renames it). Exit **3**; 2 new breaking findings at `prj_usbd_next.conf:15`, "due to this change"; job failed | **passed** |
| 4 | Accepted-baseline change | PR #2: 40771912976 | `--update-baseline --reason …` run locally and committed. Exit **0**; the 2 new entries carry the new reason and the existing ones keep theirs. The PR **restored the nightly's cache from `main`** (74 s); zmig took 2m08s warm vs 8–17 min cold | **passed** |
| 5a | Expired gap acceptance | PR #2: 40782024235 | `until: 2026-10-01` on the target-build gap. Exit **4** ("acceptance expired 2026-10-01"); the workflow turned it into a `::warning::` and the job passed | **passed** |
| 5b | Expired finding acceptance | PR #2: 40782735738 | `until: 2026-10-01` on the `CONFIG_DEBUG_INFO` entry too. Exit **3**: "(due to this change; acceptance expired 2026-10-01)" | **passed** |
| 6 | Fork PR | none | needs a second GitHub account | **not run: needs second account** |
| 7 | Scheduled (cron) triggers | none | the test repo has no cron, to save Actions minutes; `workflow_dispatch` takes the same path | **not run** |

## Bugs found by the runs, and fixes

| Found by | What failed | Fix |
|---|---|---|
| 40764423331 | **Workflow:** `actions/cache`'s post-step saves only when the job succeeds, and a nightly that finds something fails, so nothing was ever saved | Separate `actions/cache/restore` and `actions/cache/save` steps; save on non-PR runs with `if: always()`, under a per-run key. Verified in 40768470550 (saved) and 40771912976 (restored on a PR) |
| 40764423331 | **Workflow:** upload-sarif failed with `Resource not accessible by integration` | `permissions: actions: read`. The next error was the real limit (next row) |
| 40765935133 | **Workflow:** SARIF upload fails because "Code scanning is not enabled for this repository" (a private repo without GitHub Code Security), and that made the whole job red although zmig exited 0 | `continue-on-error: true` on the SARIF step. **Code scanning was not enabled** (it's a paid feature you didn't ask for), so SARIF upload remains unverified |
| 40764423331 | **Workflow:** CodeQL Action v3 deprecation warning (December 2026) | pinned v4 (`9b325408…`) |
| 40764423331 | **zmig (misleading output):** at v4.4.2 the target configure failed, and 7 accepted findings the run couldn't look for were reported as **fixed** (SARIF `absent`, which would close their alerts) | zmig `c4a606c`: during a blocking gap they're "unseen" (SARIF `unchanged`); regression test from this run's report |
| PR #2 diff | **zmig:** `--update-baseline` dropped `rebased_from` from kept entries | fixed after the runs (not in the pinned zmig; regression test added) |

## Seen in the runs, not fixed

- **A new use of an already-accepted removed API isn't "new".** A second `smf_set_handled`
  call in `main.c` folded into the accepted `function-removed` finding, whose
  fingerprint's location is its first evidence file, `led.c`. That follows decision 1
  (path without line). Only the new compiler warning (risky) showed it. Local check only;
  this branch wasn't pushed.
- **Mirror clone time on hosted runners varies:** 48 s in one run, over 11 minutes in
  another, cold.
- **Node.js 20 deprecation notice** for `actions/cache` v4 and others. They're forced to
  run on Node.js 24 and still work.
- **Probably-spurious guide matches at v4.4.2** (CoAP and Bluetooth entries in a CAN/USB
  app) in the nightly. That's the CANnectivity held-out project, so it's only recorded,
  not tuned.
