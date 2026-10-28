# DESIGN-WATCH: `zmig watch` (P4), CI mode

Status: **approved with changes** (review of 2026-10-28), then implemented:

- **Implemented and tested locally:** fingerprints, the baseline file, the exit-code
  policy, SARIF and PR-comment output. They're in `zmig/watch.py` (comparison, no
  builds) and `zmig/watchcmd.py` (the command).
- **Not run in CI:** the GitHub Actions workflow
  ([examples/zmig-watch.yml](examples/zmig-watch.yml)) has never run on GitHub. §8 says
  what's needed to test it.

P3 isn't in scope.

`watch` runs zmig on every pull request and nightly, and fails the job only when the
upgrade gets **worse**:

- a breaking finding that wasn't there before fails the job;
- findings accepted in a committed baseline don't.

Everything builds on `scan`. `watch` adds a ref-independent finding identity, a baseline,
an exit-code policy, and outputs for code scanning and PR comments.

## 0. Review decisions and where they're implemented

| # | Decision | Where |
|---|---|---|
| 1 | Fingerprint stays path-without-line, multiset-counted. The PR comment shows per-file counts | §2, §5 |
| 2 | Inconclusive exits **4 by default**. `--allow-inconclusive` exits 0 with a warning. The workflow treats 4 as a warning on PRs and fails nightly on it | §4, §6 |
| 3 | Risky never fails by default. `--fail-on risky` opts in | §4 |
| 4 | Nightly tracks `track:next-release` (no `-rc` unless `--include-rc`). `track:main` is an optional weekly job | §1, §6 |
| 5 | Measure the shallow module fetch vs restoring cached workspaces from disk, on this machine. Make no claim about Actions cache throughput | §7 |
| a | PR runs pin the target to the baseline's `to.sha`; only nightly tracks a moving target. When the target moved, new findings are labelled by cause, and the exit policy for movement is configurable | §1, §4 |
| b | Cache key: `west.yml` + zmig version only | §6 |
| c | Fork PRs: comment also goes to `$GITHUB_STEP_SUMMARY`; `pull_request`, never `pull_request_target`; actions pinned by SHA, inputs marked unverified | §5, §6 |
| d | `--update-baseline` requires `--reason`; entries record who/when/commit; CODEOWNERS; `until` kept | §3 |
| e | `zmig watch --init`; a missing baseline exits 2 with instructions | §3 |
| f | Fingerprint stability test plan, run on real builds | §2.4 |

The earlier draft (`b5da360`) had five open questions; decisions 1–5 answer them, and no
other version of this document exists.

---

## 1. What a run compares

```
zmig watch --app app --board <board> [scan options...] \
           [--from manifest|<ref>] [--to <ref>|track:next-release|track:main] \
           [--baseline .zmig/baseline.json] [--sarif out.sarif] [--pr-comment out.md] [--out dir]
```

**`--from`.** Defaults to `manifest`: the `zephyr` project's revision in the nearest
`west.yml` above `--app` (or `--manifest PATH`). The URL comes from the project's `url` or
its remote, so a fork is mirrored from the fork.

- **Caveat:** a manifest that pins a **branch** (CANnectivity v1.1.0 pins `v4.1-branch`)
  resolves to that branch's tip today, not when the commit was made. Pass `--from <sha>`
  when that matters.

**`--to`** takes one of three forms, depending on the kind of run:

| Run | `--to` | Target |
|---|---|---|
| **Pull request** | omitted | **pinned** to the baseline's recorded `to.sha`. A PR's result then depends only on the PR |
| **Nightly** | `track:next-release` | the newest release tag above `--from`'s `VERSION` (final releases only; `--include-rc` adds `-rc` tags) |
| **Weekly (optional)** | `track:main` | Zephyr `main` after a fetch |

An explicit ref (`v4.4.0`, a SHA) is also accepted. The resolved SHA is in the report.

**The scan** is a normal `scan` run: same analyzers, `checked` / `not_checked`, and
verdict invariants. A report that violates an invariant exits 1 (internal error), never
silently.

**When the target moved** (this run's `to.sha` differs from the baseline's), `watch` runs
a **second scan** of the same project against the baseline's `to.sha`, called the pinned
scan. Every new finding then gets a cause:

- **due to this change:** also present in the pinned scan, so it comes from the project's
  changes since the baseline;
- **due to target movement:** absent from the pinned scan, so it comes from Zephyr moving.

With `--no-attribute`, the second scan is skipped and new findings are **unattributed**.
The policy for each cause is in §4. With warm workspaces, the second scan costs one more
target build plus analysis.

## 2. Finding identity across runs

Today's finding IDs hash both refs' SHAs, so every run would look new. `watch` uses:

```
fingerprint = sha256(v1, category, kind, normalize(subject), image or "", board,
                     normalize(primary location))[:16]
```

### 2.1 Normalization

The fingerprint must not depend on the refs. `normalize` replaces:

- this run's `--from`/`--to` specs and SHAs (and 12-character SHA prefixes) with `<ref>`;
- any other 40-hex SHA with `<sha>`;
- 16-hex cache directory names (`apps/<h>/`, `builds/<h>/`) with `<h>`.

This matters because some subjects are a ref: `target-build-failed`'s subject *is* the
target SHA. A unit test guards it.

### 2.2 Primary location

The primary location is the first `file_line` evidence's **path, without the line**,
because lines move with unrelated edits. Without one, it's the first `doc_entry` id, or
"".

### 2.3 Multisets, values, severities

- **Multisets.** Findings with the same fingerprint are counted: 2 accepted and 3 now
  means 1 new. The PR comment shows, per file, how many breaking/risky findings it has
  and, per finding, "N in this file" when a fingerprint repeats (decision 1).
- **Changed values.** A finding whose fingerprint matches but whose `new` value changed
  (a digest is stored in the baseline) is **updated**, not new, so it doesn't fail the
  run. Examples are a module repinned again, or a default changed to yet another value.
- **Escalation.** A match whose severity rose (risky → breaking) is **escalated**, and
  counts as new.

**A known cost.** When zmig improves a finding's evidence, its fingerprint changes. For
example, M14 gave the target-build failure a file (`src/led.c`). On the DOGFOOD-4 reports
that finding shows as new + fixed (`tests/test_watch.py`). Mitigation: re-run
`--update-baseline` after a zmig upgrade. The zmig version is part of the cache key
anyway (§6).

### 2.4 Stability test plan

The tests compare fingerprints, not finding IDs:

- **Unit tests** (`tests/test_watch.py`) on real reports, CANnectivity C1 (DOGFOOD-4) and
  Golioth B (DOGFOOD-2):
  - the target SHA and spec rewritten everywhere: identical fingerprints;
  - every evidence line shifted by 7: identical;
  - `src/led.c` renamed: 6 findings new + 6 fixed, nothing matched;
  - plus multiset, expiry, escalation, value changes, cause attribution, and the CLI
    lifecycle.
- **Real runs** (`zmig watch`, builds included), each in its own copy of the project with
  the baseline inside it:

| Run | Project / change | Expectation |
|---|---|---|
| c-same | CANnectivity C1, unchanged, pinned | every finding unchanged |
| c-target | same tree, `--to v4.2.0` instead of the baseline's `30de5e8` (v4.2-branch) | findings present at both targets keep their fingerprints; differences are real target differences, labelled by cause |
| c-shift | 5 comment lines prepended to `led.c`, `usb.c`, `main.c` | every finding unchanged; lines in the comment shift by 5 |
| c-rename | `src/led.c` → `src/led_ctrl.c` (CMakeLists updated) | `led.c` findings fixed, `led_ctrl.c` findings new; exit 3 |
| g-same | Golioth B, unchanged | every finding unchanged, exit 0 |
| g-target | Golioth B, `--to 30de5e8` instead of the baseline's `v4.2.0` | as c-target |
| g-shift | 3 lines prepended to `net_connect.c` and `hello/src/main.c` | every finding unchanged |

### 2.5 Stability results

Real `zmig watch` runs, 2026-10-28, script
[examples/watch-stability.sh](examples/watch-stability.sh). Each run's `watch.json` was
compared with the baseline made by `--init` on the unchanged copy. Golioth B uses the
pre-bump tree `1fc16f4`; CANnectivity C1 runs from `7823374` (v4.1.0).

| Run | Exit | States | Checked |
|---|---|---|---|
| c-same | 4 | 8 unchanged | inconclusive: the target build fails at v4.2 (also in the baseline) |
| c-target (`--to v4.2.0`; baseline `30de5e8`) | 4 | 8 unchanged | the pinned second scan ran; **8/8 fingerprints identical, 0/8 finding IDs identical**. No finding differs between these two targets, so neither cause label appeared |
| c-shift (+5 lines) | 4 | 8 unchanged | lines in the comment moved (e.g. `led.c:225` → `:230`, `:348` → `:353`) |
| c-rename | **3** | 6 fixed (`src/led.c`), 6 new (`src/led_ctrl.c`), 2 unchanged | rename reported as new + fixed, not matched; the 4 new breaking ones fail the run |
| g-same | 0 | 9 unchanged | conclusive: every analyzer ok |
| g-target (`--to 30de5e8`; baseline `v4.2.0`) | 0 | 9 unchanged | pinned second scan ran; **9/9 fingerprints identical, 0/9 finding IDs identical** |
| g-shift (+3 lines) | 0 | 9 unchanged | e.g. `net_connect.c:69` → `:72`, `src/main.c:7` → `:10` |

All 11 reports, including the 2 pinned ones, pass `invariant_violations`.

**A bug found and fixed by this plan.** In the first c-shift run, 3 findings came out
**updated** instead of unchanged. Their `new` values carry lines (`errors[].line`, compiler
`sites` such as `src/led.c:354`), so the value digest saw the shift. Fingerprints were
unaffected and the exit code didn't change, but the comment was noisy.

- The fix: the value digest drops `line` keys and `file:NNN` suffixes.
- A regression test covers it (`test_line_shifts_inside_values_are_not_value_changes`;
  it fails without the fix).
- The table above is from the re-run after the fix.

**Not exercised by real runs:** a finding present at one target and not the other, so the
"due to target movement" label was never produced by a real build. It's covered by
`test_target_movement_is_attributed_with_a_pinned_report` only.

**`--to track:*` resolved for real** (mirror fetched, 2026-10-28):

| From | `track:next-release` | `--include-rc` | `track:main` |
|---|---|---|---|
| v4.1.0 | v4.4.2 | v4.5.0-rc3 | `main` = `5c71a7331104` |
| v4.2.0 | v4.4.2 | v4.5.0-rc3 | |

**`--from manifest` parsed for real:**

- CANnectivity v1.1.0's `west.yml` gives `v4.1-branch` (a branch: see the caveat in §1);
- Golioth's `west-zephyr.yml` gives `v4.1.0`.

Golioth keeps it in `west-zephyr.yml`, not `west.yml`, so it needs `--manifest`.

## 3. The baseline file

`.zmig/baseline.json`, committed. It's written only by `zmig watch --init` or
`--update-baseline`; zmig never updates it on its own in CI.

```json
{
  "schema": "https://zmig.dev/schema/baseline/1",
  "zmig_version": "0.1.0",
  "fingerprint_version": 1,
  "config": {"app": "app", "board": "usb2canfdv1", "to_board": null, "sysbuild": false,
             "fast": false, "modules": ["cmsis", "hal_stm32"],
             "cmake_args": ["-DZEPHYR_EXTRA_MODULES=<repo>", "-DFILE_SUFFIX=usbd_next"],
             "analyzers": "all"},
  "from": {"spec": "…", "sha": "…"},
  "to": {"spec": "track:next-release", "resolved": "v4.2.0", "sha": "…"},
  "verdict": {"verdict": "direct-ok", "confidence": "low"},
  "inconclusive": ["the target build is build-failed: …"],
  "accepted": [
    {"fingerprint": "8ec5292b30029d23", "severity": "breaking", "category": "api",
     "kind": "function-removed", "subject": "smf_set_handled", "title": "…",
     "path": "src/led.c", "value": "<digest of new>",
     "reason": "v4.2 port in progress (#123)", "accepted_by": "jdoe",
     "accepted_at": "2026-10-28T14:02:11Z", "accepted_commit": "<project HEAD>",
     "until": "2026-12-31"}
  ]
}
```

### 3.1 Creating it

```
zmig watch <options> --to <target> --init --reason "why these are accepted"
```

- `--reason` is required.
- `--init` refuses to overwrite an existing baseline.

**A missing baseline** exits 2, printing the exact `--init` command line to run, the
current options plus `--to <target> --init --reason "..."`.

### 3.2 Updating it

`--update-baseline --reason "..."` rewrites `accepted` to this run's breaking and risky
findings:

- entries that are still found **keep** their original reason, author, date, commit and
  `until`;
- only newly accepted entries get the new `--reason`;
- `--until YYYY-MM-DD` sets an expiry on the entries this update adds.

### 3.3 Who accepted it

| Field | Source |
|---|---|
| `accepted_by` | `$GITHUB_ACTOR`, else `git config user.name`, else the login name |
| `accepted_at` | UTC |
| `accepted_commit` | `git rev-parse HEAD` of the repository holding the baseline |

### 3.4 Expiry

An entry whose `until` has passed is no longer accepted. Its finding is **new** again,
labelled "acceptance expired" in the comment, so accepted debt can't be forgotten.

### 3.5 Config must match

The run's `config` must equal the baseline's; otherwise the run exits 2 and lists each
difference.

Absolute paths under the repository (the parent of `.zmig/`) are written as `<repo>`, so
the same command line matches on a laptop and in CI. The app path is stored relative to
the repository.

### 3.6 Who may change it

**Recommendation:** add a `CODEOWNERS` entry:

```
/.zmig/baseline.json  @org/firmware-leads
```

With required reviews, accepting a new breaking finding then needs a code owner's
approval. The diff shows who accepted what, and why.

## 4. Exit codes

| Code | When |
|---|---|
| 0 | no new gating finding, and the run is conclusive (or `--allow-inconclusive`, with a warning) |
| 1 | internal error: crash, invariant violation, tool failure |
| 2 | usage error, a missing baseline, `--init`/`--update-baseline` without `--reason`, or a config mismatch |
| 3 | at least one **new or escalated** finding at a gating severity |
| 4 | **inconclusive** and no new gating finding (the default; `--allow-inconclusive` gives 0) |

### 4.1 Which findings fail the run

- **Gating severities.** Breaking by default; `--fail-on risky` adds risky. Non-gating new
  findings are listed but don't change the exit code.
- **A finding due to target movement** (§1) gates per `--on-target-movement`:
  - `fail` (default) exits 3;
  - `warn` lists it under warnings and doesn't fail.
- **Unattributed findings** (moved target with `--no-attribute`) gate like findings due to
  this change.
- **A new gating finding wins over inconclusive:** exit 3.

### 4.2 Inconclusive

A run is **inconclusive** when any of these hold:

- the effective target build isn't `ok`;
- any analyzer's status isn't `ok` (skipped, partial or error), with its reason;
- the verdict's confidence dropped below the baseline's.

These reasons are normalized like subjects. The comment marks the ones also recorded in
the baseline as "(also in the baseline)". Strict means they still exit 4.

**Example.** CANnectivity at v4.2 fails its target build, so every run of it is
inconclusive until the port lands. On PRs that's a warning (§6); nightly it fails.

### 4.3 Verdict regressions

A verdict regression always comes with a new hazard-category finding (verdict
invariants). The comment shows the verdict next to the baseline's.

## 5. Outputs

| Output | Option | Content |
|---|---|---|
| Comment | stdout (or `--format json` for watch.json) and `--pr-comment PATH` | the PR comment below |
| SARIF | `--sarif PATH` | §5.2 |
| Directory | `--out DIR` | `report.json`, `MIGRATION.md`, `watch.json`, `watch-comment.md`, `watch.sarif`, and `pinned/` when a pinned scan ran |
| Job summary | automatic | when `$GITHUB_STEP_SUMMARY` is set, the comment is appended to it. That's how fork PRs, whose token can't comment, still see the result |

zmig needs no GitHub token; posting the comment is the workflow's job.

### 5.1 The PR comment

The comment starts with `<!-- zmig-watch -->`, so the workflow edits one comment instead
of adding new ones. It contains:

- the exit code and summary;
- the verdict, and the baseline's verdict;
- whether the target was pinned or moved;
- new findings grouped by file, each with: severity, line, cause, "was risky" /
  "acceptance expired", "N in this file", and the fingerprint;
- fixed entries;
- accepted entries whose value changed;
- the counts of accepted findings still present;
- the inconclusive reasons.

Real output for the pre-fix CANnectivity report against a baseline from the fixed one:

```
### zmig watch: 7823374e8721 → 30de5e8a2cee (usb2canfdv1)
**Exit 3: new finding(s) outside the baseline: 1 breaking.**
Verdict: direct-ok (low); baseline: direct-ok (low).
Target pinned to the baseline's `30de5e8a2cee`.
#### New (4)
- **`(no file)`**: 1 here; 0 breaking/risky in this file
  - breaking: The target build fails: 1 error (step) (due to this change) `71f59634e842c907`
- **`src/led.c`**: 1 here; 6 breaking/risky in this file
  - risky line 79: alias led0 (/leds/led_ready) changed: phandle (due to this change) `916bd99bc66f566d`
…
#### Fixed (accepted, no longer found) (1)
…
#### Not fully checked
- the target build is build-failed: … (also in the baseline)
```

### 5.2 SARIF

SARIF 2.1.0 results carry:

- **`partialFingerprints["zmigFingerprint/v1"]`**, the fingerprint;
- **`baselineState`**, one of:
  - `new`, which also covers escalated findings;
  - `unchanged`;
  - `updated`, for value changes;
  - `absent`, for fixed entries. These are located at their file **without a region**,
    because the baseline has no line.

`properties.zmigState` / `zmigCause` give the finer state and the cause.

**Unverified:** how GitHub code scanning displays `absent` results, and results without a
region.

## 6. GitHub Actions

The full file is [examples/zmig-watch.yml](examples/zmig-watch.yml). It is **untested**.

### 6.1 Triggers and modes

| Trigger | Mode |
|---|---|
| `pull_request` | pinned to the baseline's target |
| cron, nightly | `track:next-release`; fails on exit 4 |
| cron, weekly (optional) | `track:main` |
| `workflow_dispatch` | as nightly |

### 6.2 `pull_request`, never `pull_request_target`

The job builds the PR's code: CMake, Kconfig and Python scripts from the PR all run.

- `pull_request_target` runs in the base repository's context, with a write token and
  secrets. A fork PR's code could exfiltrate them, or push.
- With `pull_request`, fork PRs get a read-only token and no secrets.

The cost is that fork PRs can't post the comment or upload SARIF. Those steps are skipped
for forks; the job summary carries the comment.

### 6.3 Exit 4 on PRs

The run step turns 4 into a `::warning::` annotation and exits 0, so the job passes with
a warning. This is the "continue-on-error" behaviour asked for, done in the step's shell
so that 3 still fails.

A true **neutral** check conclusion needs the Checks API (a check run with
`conclusion: neutral`, and `checks: write`, which fork PRs don't get). It isn't in the
example. **Unverified:** whether a step-level `continue-on-error` shows differently in
the PR checks list.

### 6.4 Cache

The cached paths are the mirror, workspaces and parse cache, under the key
`zmig-<zmig version>-<hashFiles('west.yml')>`. The baseline is **never** in the key, so
editing it doesn't invalidate the cache.

The scheduled run on the default branch populates the cache, and PRs restore it.
**Unverified (check against current GitHub docs before relying on them):**

- that a PR branch can restore a cache saved on the default branch (branch scoping);
- the per-repository cache size limit, and eviction.

The cached workspaces are about 5 GB (§7).

### 6.5 Actions

Actions are **pinned by commit SHA**: the commit each tag pointed to on 2026-10-28,
from `git ls-remote`.

| Action | Tag | Commit |
|---|---|---|
| `actions/checkout` | v4 | `11d5960a…` |
| `actions/setup-python` | v5 | `a26af69b…` |
| `actions/cache` | v4 | `0057852b…` |
| `actions/upload-artifact` | v4 | `ea165f8d…` |
| `github/codeql-action/upload-sarif` | v3 | `4bfa242a…` |

**Every action input is unverified.** The PR comment is posted with the `gh` CLI (find
the comment with the marker, then PATCH or POST) rather than a third-party action. The
Zephyr SDK is downloaded with `curl` (asset names unverified) rather than
`action-zephyr-setup`, whose west handling would fight zmig's own workspaces.

## 7. Runtime budget, and measurements

**Budget:**

- PR run ≤ 10 min p95, with the cache restored;
- nightly ≤ 60 min, cold.

### 7.1 Measured on this machine (2026-10-28)

Script: [examples/watch-measure.sh](examples/watch-measure.sh). Setup:

- the module set of Golioth B plus CANnectivity (cmsis, cmsis_6, hal_stm32, mbedtls,
  zcbor), at the v4.2-branch commit `30de5e8`;
- 14 cores, local SSD;
- the network re-measured after a change of location: about **150–170 KB/s from
  GitHub**, derived from the fetches below.

**Cold shallow fetch.** `west update --narrow -o=--depth=1 <module>`, one module at a time,
into a fresh workspace whose Zephyr tree was exported from the local mirror
(`git archive`, 1.1 s, 404 MB).

| Module | Time | git objects (shallow) | git objects (zmig's full clone today) | Checkout |
|---|---|---|---|---|
| cmsis | 4.1 s | 1.2 MB | 3.4 MB | 4.4 MB |
| cmsis_6 | 46.8 s | 7.9 MB | 34 MB | 28 MB |
| hal_stm32 | 198.1 s | 30 MB | 160 MB | 1009 MB |
| mbedtls | 51.4 s | 6.8 MB | — | 56 MB |
| zcbor | 8.8 s | 0.7 MB | — | 3.1 MB |
| **Total** | **309 s (5.2 min)** | **47 MB** | | **1.1 GB** |

**Restoring the same modules from a local tarball.** `tar` + `zstd -3 -T0`; the page cache
is evicted for the tarball (`posix_fadvise DONTNEED`), and the time includes `sync`.

| Archive | Size | Restore |
|---|---|---|
| shallow cmsis + cmsis_6 + hal_stm32 | 127 MB | **1.0 s** |
| zmig's current full-clone cmsis + cmsis_6 + hal_stm32 (1.2 GB on disk) | 292 MB | **3.9 s** |

Creating either archive took about 0.55 s, from a warm page cache.

### 7.2 What this shows, and what it doesn't

- **On this machine the network dominates.** Fetching the module set shallow takes 309 s;
  restoring it from local disk takes 1–4 s.
- **zmig's current fetch is far slower.** In the g-target run, its full-history fetch of
  mbedtls + zcbor into the `30de5e8` workspace took **11 min 02 s** on the same network.
  The shallow fetch of the same two took 60 s.
- **A shallow fetch moves about 5× fewer bytes** than zmig's current fetch:
  `materialize` runs `west update --narrow` without `--depth`, which is full history.
  Shallow fetching is **not implemented** in zmig; it was measured with west directly.
- **Excluded from the numbers above:**
  - GitHub Actions cache throughput: a restore there comes from remote storage, not local
    disk, so the 1–4 s above isn't a prediction for it;
  - a shallow fetch of Zephyr itself, which CI needs if it doesn't cache the 1.1 GB mirror;
  - hosted-runner network speed, which is likely far above 150 KB/s but is unmeasured.
  §8.3 lists what to measure on a real runner.

### 7.3 Against the budget

- **Analysis and builds** fit the PR budget when the workspaces are present:
  - two builds take ≤ 2 min for these projects;
  - analysis takes ≤ 1 min with the parse cache (DOGFOOD-3 §8);
  - a moved target adds one more build and analysis (c-target: 35 s; §2.5).
- **The open question is getting the workspaces onto the runner.** The two options are:
  1. a cache restore of about 5 GB, with throughput unknown;
  2. a shallow fetch, whose time is unknown at runner network speed. Here it took 5.2 min
     at 150 KB/s for 47 MB of objects plus a Zephyr tree.

  Decide after §8.3's measurements.

## 8. Testing the workflow (not done; what you need)

### 8.1 What you need

- **A private GitHub repository** containing a Zephyr project. The simplest is a copy of
  CANnectivity at `v1.1.0` (Apache-2.0): its v4.2 port gives known breaking findings and
  an inconclusive run.
- **zmig hosted** where `pip` can install it, e.g. a second private repository, pinned by
  commit in `ZMIG_PIP`. If it's private, the workflow needs a token that can read it: a
  fine-grained PAT stored as a repository secret, used as
  `git+https://x-access-token:${{ secrets.ZMIG_READ }}@github.com/...`. That's the only
  secret.
- **Code scanning on a private repository** needs GitHub Advanced Security / Code
  Security on the account. Unverified; without it the SARIF upload step fails, and the
  rest still works.
- **Runner:** GitHub-hosted `ubuntu-24.04`, the standard one first. It needs about 10 GB
  free for the SDK, mirror, workspaces and builds. A self-hosted runner with a persistent
  `~/.cache/zmig` is the fallback if the cache doesn't fit.
- **A fork** of the test repository, under another account, to test fork PRs.

### 8.2 Test steps

1. **Baseline.** Locally, run
   `zmig watch <ZMIG_ARGS> --from <sha> --to 30de5e8a2cee --init --reason "test"` in the
   repository. Commit `.zmig/baseline.json` and the workflow, and add the CODEOWNERS
   entry.
2. **Seed the cache.** Run the workflow by `workflow_dispatch` on the default branch.
   Record:
   - total job time, and each step's time;
   - the cache size saved;
   - the exit code (expect 4 for CANnectivity, so this run fails, as nightly should).
3. **Pinned PR, nothing new.** Open a same-repo PR that changes a comment in `led.c`.
   Expect:
   - cache restored, and its restore time recorded;
   - exit 4, turned into a warning;
   - one PR comment with the marker;
   - the job summary;
   - SARIF uploaded, with alerts matching the baseline.
4. **New breaking finding.** Push a commit that calls another API removed in 4.2, or adds
   a Kconfig symbol removed at the target. Expect exit 3, a failed job, the **same**
   comment edited (not a second one), and the finding under "New".
5. **Fork PR.** Open the step-3 change from the fork. Expect:
   - comment and SARIF steps skipped;
   - the job summary shows the comment;
   - no secrets available.
6. **Moved target.** Run `workflow_dispatch` with `track:next-release` (or temporarily
   change the nightly to `--to v4.2.0`). Expect the pinned second scan in the log, and
   causes in the comment.
7. **Baseline lifecycle.** In a PR, run `--update-baseline --reason ...` locally and
   commit. Expect CODEOWNERS review to be required. Separately, set an `until` in the
   past and expect exit 3, "acceptance expired".
8. **Record for §7:** cache restore time and size, cold vs warm job time, and whether the
   PR p95 fits 10 min.

### 8.3 What to measure on a real runner

These are the things this machine can't tell you:

- `actions/cache` restore and save throughput, and the time for about 5 GB;
- whether a cache saved on the default branch is restorable from PR branches;
- the repository cache limit and eviction under nightly churn;
- the hosted runner's network throughput to GitHub for the shallow module fetch (§7's
  alternative);
- build time on the runner's cores (this machine has 14);
- disk space left after SDK + cache + builds.

## 9. Not implemented

- `--accept <fingerprint>` (accept one finding without a full update).
- A Checks-API neutral conclusion (§6.3).
- Baseline schema validation beyond the schema URI and fingerprint version.
- `plan` has no watch mode; `watch` is scan-only.
