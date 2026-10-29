# CI test checklist: `zmig watch` in GitHub Actions

Status markers:

- **passed:** ran, run linked;
- **failed:** ran, see the note;
- **not run:** with the reason;
- **unverified:** not run yet.

Every workflow input counts as unverified until a real run passes.

Run links point to `https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/<id>`;
both repos are private.

## Summary: what is verified and what isn't

**Verified by real runs** (links in the table below):

- the nightly path (`workflow_dispatch`): target tracking to the next release, the pinned
  second scan, "due to target movement" labels, exit 3 failing the job;
- PR runs pinned to the baseline's target, exit 0;
- a new breaking finding (exit 3);
- an accepted-baseline change (`--update-baseline`), and expired acceptances for a gap
  (exit 4, turned into a warning) and for a finding (exit 3);
- a new use site of an accepted finding (exit 3, after the fix);
- the zmig upgrade with `--rebase-baseline`;
- the PR comment edited in place, and the job summary;
- installing zmig from a private repo pinned by commit;
- cache save on the default branch and restore on a PR, plus the SDK download and the
  checkout and setup actions.

**Not verified:**

| | Why |
|---|---|
| **Fork PRs** | needs a second GitHub account |
| **Cron schedules** | the test repo has no cron; `workflow_dispatch` runs the same steps, but the schedule trigger and its `case` branch never ran |
| **SARIF upload** | needs code scanning (GitHub Code Security) on the private repo, which wasn't enabled. The step runs and fails, and the job ignores that |
| **Cache limits** | the per-repository cache size limit and eviction under nightly churn (one new entry, about 1.65 GB, per nightly) weren't tested |

The `ZMIG_READ` secret and its token are still in place; they're removed only when CI
testing is declared finished.

## Repositories

Both are **private**, under the `beriberikix` account. Nothing else was pushed.

| Repo | Content |
|---|---|
| `beriberikix/zmig` | this repository, from a fresh copy with the commit email rewritten to the GitHub noreply address (trees unchanged; the original is backed up as git bundles in `~/.cache/zmig/backup/`). `main`: `23c42c5`, then `c4a606c` (the unseen/fixed fix), `c76a931`, `98162e3` (new-use-site detection) |
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

**Verified** means a real run passed with the expected result, and the run is linked.
**Unverified** means it hasn't run, or it never ran successfully.

| # | What | Result | Status |
|---|---|---|---|
| 1 | Nightly path (`workflow_dispatch`, `--to track:next-release`) | Target resolved to **v4.4.2**: **moved** from the baseline's `30de5e8`; the pinned second scan ran. Exit **3**: 3 new breaking findings, all "due to target movement"; the job fails, as a nightly should. Cache saved (1.65 GB). Earlier attempts: [40751179748](https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/40751179748) (empty secret), [40764423331](https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/40764423331) (found the cache and SARIF bugs) | **verified** [40768470550](https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/40768470550) |
| 2 | PR, nothing new (PR #1) | Exit **0**, pinned to `30de5e8`; 3 accepted gaps as "analysis incomplete"; the `--from manifest` branch warning; one PR comment, edited in place | **verified** [40767491146](https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/40767491146) |
| 3 | PR with a new breaking finding (PR #2, `CONFIG_DEBUG_INFO`) | Exit **3**; 2 new breaking findings at `prj_usbd_next.conf:15`, "due to this change" | **verified** [40769049428](https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/40769049428) |
| 4 | Accepted-baseline change (PR #2, `--update-baseline`) | Exit **0**. A PR **restored the default branch's cache** (74 s); zmig ran in 2m08s warm | **verified** [40771912976](https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/40771912976) |
| 5a | Expired gap acceptance | Exit **4** → `::warning::`, job passes | **verified** [40782024235](https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/40782024235) |
| 5b | Expired finding acceptance | Exit **3**, "acceptance expired 2026-10-01" | **verified** [40782735738](https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/40782735738) |
| 6 | New use site of an accepted finding (PR #3, a 2nd `smf_set_handled` in `main.c`) | **Before the fix** (zmig `c4a606c`): exit **0**, the false negative ([40787484968](https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/40787484968)). **After** (zmig `98162e3`): exit **3**, 2 breaking under "Accepted finding has a new use site", `src/main.c` line 46, accepted for `src/led.c` | **verified** [40788934683](https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/40788934683) |
| 7 | zmig upgrade with `--rebase-baseline` (PR #4: `c4a606c` → `98162e3`, 8 re-mapped, 0 dropped) | Exit **0** with the rebased baseline; cache restored in 72 s, zmig 85 s. The first upgrade (PR #1: `23c42c5` → `c4a606c`) passed in [40767491146](https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/40767491146) | **verified** [40788386870](https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/40788386870) |
| 8 | Fork PR | needs a second GitHub account | **unverified: not run, needs second account** |
| 9 | Scheduled (cron) triggers | the test repo has no cron, to save Actions minutes; `workflow_dispatch` takes the same path | **unverified: not run** |
| 10 | SARIF upload | the step runs, but GitHub rejects the upload: "Code scanning is not enabled for this repository" (needs GitHub Code Security on a private repo; not enabled, as instructed) | **unverified: needs code scanning** |

**Still unverified:**

- fork PRs (need a second account);
- cron schedules;
- SARIF upload (needs code scanning);
- the per-repository cache size limit, and eviction under nightly churn.

### Timing, against the budget (DESIGN-WATCH.md §7: PR ≤ 10 min p95, nightly ≤ 60 min cold)

| | Measured | Within budget? |
|---|---|---|
| PR, warm cache | about 5 min end to end (cache restore 72–74 s, zmig 85–128 s) | yes |
| PR, cold cache | 8–19 min end to end | not reliably ≤ 10 min, and the mirror clone dominates |
| **Runner mirror clone, cold** | **48 s** ([40768470550](https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/40768470550)) to **over 11 min** ([40769049428](https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/40769049428)), same repo, same day | |
| Nightly, cold | 6.5–7 min end to end, with the 48 s clone; would be about 17 min with the 11-minute clone | **yes in both observed cases** |

With the clone varying this much, the cache matters more than the analysis. Only scheduled
and dispatched runs save it.

## Bugs found by the runs, and fixes

| Found by | What failed | Fix |
|---|---|---|
| 40764423331 | **Workflow:** `actions/cache`'s post-step saves only when the job succeeds, and a nightly that finds something fails, so nothing was ever saved | Separate `actions/cache/restore` and `actions/cache/save` steps; save on non-PR runs with `if: always()`, under a per-run key. Verified in 40768470550 (saved) and 40771912976 (restored on a PR) |
| 40764423331 | **Workflow:** upload-sarif failed with `Resource not accessible by integration` | `permissions: actions: read`. The next error was the real limit (next row) |
| 40765935133 | **Workflow:** SARIF upload fails because "Code scanning is not enabled for this repository" (a private repo without GitHub Code Security), and that made the whole job red although zmig exited 0 | `continue-on-error: true` on the SARIF step. **Code scanning was not enabled** (it's a paid feature you didn't ask for), so SARIF upload remains unverified |
| 40764423331 | **Workflow:** CodeQL Action v3 deprecation warning (December 2026) | pinned v4 (`9b325408…`) |
| 40764423331 | **zmig (misleading output):** at v4.4.2 the target configure failed, and 7 accepted findings the run couldn't look for were reported as **fixed** (SARIF `absent`, which would close their alerts) | zmig `c4a606c`: during a blocking gap they're "unseen" (SARIF `unchanged`); regression test from this run's report |
| PR #2 diff | **zmig:** `--update-baseline` dropped `rebased_from` from kept entries | fixed (zmig `fa8c0e0`, published as `c76a931`; in the pinned `98162e3`) |
| PR #3, [40787484968](https://github.com/beriberikix/zmig-ci-cannectivity/actions/runs/40787484968) | **zmig watch design bug:** a new use site of an accepted finding (2nd `smf_set_handled` call in `main.c`) folded into the finding fingerprinted by `led.c`: exit 0 | evidence sets per accepted entry; "accepted finding has a new use site" at the finding's severity (zmig `91a5116`, published `98162e3`). Verified: run 6 |

## Seen in the runs, not fixed

- **Mirror clone time on hosted runners varies:** 48 s in one run, over 11 minutes in
  another, cold.
- **Node.js 20 deprecation notice** for `actions/cache` v4 and others. They're forced to
  run on Node.js 24 and still work.
- **Probably-spurious guide matches at v4.4.2** (CoAP and Bluetooth entries in a CAN/USB
  app) in the nightly. That's the CANnectivity held-out project, so it's only recorded,
  not tuned.
