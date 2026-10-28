# CI test checklist: `zmig watch` in GitHub Actions

Status markers: **unverified** (not run yet), **passed** (ran, output linked),
**failed** (ran, see note), **not run** (with the reason). Every workflow input counts as
unverified until a real run passes.

## Repositories

Both are **private**, under the `beriberikix` account. Nothing else is pushed.

| Repo | Content |
|---|---|
| `beriberikix/zmig` | this repository, with the commit email rewritten to the GitHub noreply address (a fresh copy; trees unchanged; the original is backed up as a git bundle) |
| `beriberikix/zmig-ci-cannectivity` | CANnectivity's history up to `v1.1.0` (`80612ee`, Apache-2.0, attribution kept), plus one commit: upstream CI and Dependabot removed, `UPSTREAM.md`, the workflow and the baseline |

Layout of the test repo:

```
zmig-ci-cannectivity/
  app/                       upstream app (built with -DFILE_SUFFIX=usbd_next)
  west.yml                   upstream: zephyr at v4.1-branch (a branch: --from manifest warns)
  LICENSE, UPSTREAM.md
  .zmig/baseline.json        made locally by the pinned zmig commit (--init), committed
  .github/workflows/zmig-watch.yml
```

## How zmig is installed

The workflow installs zmig from a git URL pinned by commit:

```
pip install "zmig[zephyr,api] @ git+https://x-access-token:${ZMIG_READ}@github.com/beriberikix/zmig@<commit>"
```

**This needs a secret.** `beriberikix/zmig` is private, and a workflow's own
`GITHUB_TOKEN` only reads its own repository. You create it, because zmig's runs never
handle credentials:

1. Create a fine-grained personal access token with repository access to
   `beriberikix/zmig` only, permission **Contents: Read-only**, and a short expiry.
2. Store it in the test repo:
   `gh secret set ZMIG_READ -R beriberikix/zmig-ci-cannectivity`, pasting the token at
   the prompt.

The pinned commit must be the one whose zmig made `.zmig/baseline.json`. The baseline
records `zmig_build`, so any other build exits 2 (§3.6 of DESIGN-WATCH.md).

## Runs

| # | What | How | Expect | Status |
|---|---|---|---|---|
| 1 | Nightly, cold | `workflow_dispatch` on `main` (runs as nightly: `--to track:next-release`) | mirror, workspaces and SDK fetched; cache saved. The target resolves to the newest release (v4.4.2 on 2026-10-28), so it **moved** from the baseline's `30de5e8`: a second scan against `30de5e8` runs, and new findings are labelled "due to target movement". Expect exit 3, which fails the job (that's the nightly's purpose) | unverified |
| 2 | PR run, nothing new | PR changing a comment in `app/src/led.c` | cache restored; target pinned to the baseline's SHA; exit 0; one PR comment with the marker; job summary; SARIF upload (fails if code scanning isn't available on this private repo; allowed) | unverified |
| 3 | PR with a new breaking finding | push a commit calling another API removed in v4.2 | exit 3, job fails; the **same** comment edited, the finding under "New" | unverified |
| 4 | Accepted-baseline change | `zmig watch ... --update-baseline --reason ...` locally, committed to the PR | exit 0; the comment shows nothing new; the baseline diff shows who/why | unverified |
| 5 | Expiring acceptance | set an `until` in the past on one accepted entry (finding) and one accepted gap | exit 3 (expired finding, "acceptance expired"); with only the gap expired, exit 4 | unverified |
| 6 | Fork PR | needs a second GitHub account | comment and SARIF steps skipped, the job summary has the comment, no secrets | **not run: needs second account** |

Record for each run: run URL, job time, step times, cache size and restore time, exit
code, comment URL.
