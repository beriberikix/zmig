# DESIGN-WATCH: `zmig watch` (P4), CI mode

Status: **draft for review**. Not implemented; the `watch` subcommand is still the
"not in the MVP" stub in `zmig/cli.py`. P3 isn't in scope.

`watch` runs zmig on every pull request and nightly, and fails the job only when the
upgrade gets **worse**. A breaking finding that wasn't there before is a failure; findings
already accepted in a committed baseline aren't. Everything below builds on `scan`. `watch`
adds a baseline, a ref-independent finding identity, an exit-code policy, and outputs for
code scanning and PR comments.

---

## 1. What a run compares

```
zmig watch --app app --board <board> [scan options...] \
           --from manifest --to <ref|track:main|track:next-release> \
           --baseline .zmig/baseline.json [--sarif out.sarif] [--pr-comment out.md]
```

- **`--from manifest`**: the Zephyr revision the project's own `west.yml` pins at the
  commit being tested. This makes "the version you're on" follow the project.
- **`--to`**: either a fixed upgrade target (`v4.4.0`), or a moving one:
  - `track:main` is Zephyr `main` at fetch time;
  - `track:next-release` is the newest release tag above `--from`.

  The resolved SHA is recorded in the report and the baseline, so a later run can say
  "the target moved".
- **The scan itself** is a normal `scan` run, with the same analyzers, the `checked` /
  `not_checked` records and the verdict invariants (`zmig.verdict.invariant_violations`).
  A run whose report violates an invariant fails as an internal error (exit 1), never
  silently.

## 2. Finding identity across runs

**Today's IDs can't be used.** `make_id` hashes `(category, kind, subject, old_sha,
new_sha, board)` (DESIGN.md line 129 planned to diff by it). In CI, `--from` changes when
the project bumps its manifest, and `--to` moves under `track:` modes. Every finding would
then look new on every run.

**`watch` uses a fingerprint instead:**

```
fingerprint = sha256(category, kind, normalized subject, image or "", board target,
                     primary location)[:16]
```

- **Normalized subject:** the subject with ref SHAs and absolute paths removed. Most
  subjects are already symbolic, e.g. `CONFIG_X`, `net_mgmt_event_handler_t`, `/uart_1`.
- **Primary location:** the first `file_line` evidence as `path` **without the line**,
  because lines move with unrelated edits. Without one, the first `doc_entry` id, or "".
- **Several findings with the same fingerprint** are counted as a multiset: 2 in the
  baseline and 3 now means 1 new.
- **The same fingerprint goes into SARIF** as `partialFingerprints.zmigFingerprint/v1`, so
  GitHub code scanning tracks one alert across runs. Today's SARIF uses `zmigFindingId`,
  which has the same ref problem.

**What this costs.** A finding whose subject embeds a moving value keeps a stable
fingerprint and is reported as "changed", not "new":

- a value change `old → new` with the new value moving;
- a `module-repinned` revision.

`watch` compares those findings' `new` field separately: "baseline finding changed"
(info).

## 3. The baseline file

`.zmig/baseline.json`, committed, and written only by `zmig watch --update-baseline` or by
`zmig scan --format baseline`:

```json
{
  "schema": "https://zmig.dev/schema/baseline/1",
  "zmig_version": "0.x",
  "config": {"app": "app", "board": "usb2canfdv1", "cmake_args": ["-DFILE_SUFFIX=usbd_next"],
             "modules": ["cmsis", "hal_stm32"], "analyzers": "all"},
  "from": {"spec": "manifest", "sha": "…"},
  "to": {"spec": "track:next-release", "resolved": "v4.4.0", "sha": "…"},
  "verdict": {"verdict": "needs-changes", "confidence": "medium"},
  "accepted": [
    {"fingerprint": "…", "severity": "breaking", "category": "kconfig",
     "kind": "symbol-removed", "subject": "CONFIG_…", "title": "…",
     "reason": "fixing in #123", "until": "2026-12-31"}
  ]
}
```

- **`accepted`** is the set the team has seen. `reason` and `until` are optional. An
  expired `until` turns the entry back into "new", so accepted debt can't be forgotten
  silently.
- **`config`** must match the run. A mismatch is a usage error (exit 2), because comparing
  different boards or analyzer sets gives meaningless "new" findings.
- **Updating:**
  - `--update-baseline` rewrites `accepted` to the current breaking/risky set and keeps the
    `reason`/`until` of fingerprints that remain;
  - `--accept <fingerprint> --reason …` adds one entry.

  The tool never updates the baseline on its own in CI.

## 4. Exit codes

The policy: **nonzero only for new breaking findings**, plus the existing error and usage
codes. Code 3 is reserved by DESIGN.md for this.

| Code | When |
|---|---|
| 0 | no breaking finding outside the baseline, and the run is conclusive (below) |
| 1 | internal error: crash, invariant violation, tool failure |
| 2 | usage error, or a baseline `config` mismatch |
| 3 | at least one **new breaking** finding (fingerprint not in `accepted`, or expired) |
| 4 | **inconclusive**, and `--fail-on-inconclusive` is set; otherwise 0 with a warning |

**Risky findings never fail the job.** New risky findings are listed in the comment and in
SARIF as warnings.

**Verdict-confidence handling.** A run is inconclusive when it can't establish that there
are no new breaking findings. That's the case when any of these hold:

- the effective target build failed (the verdict's confidence is capped at low, M11);
- an analyzer that produces breaking findings was skipped or errored at the target
  (`kconfig`, `devicetree`, `buildlog`, `api`, `modules`, `layout`, `mcuboot`, `storage`);
- `api` is `partial` for a TU that had a breaking finding in the baseline;
- the verdict's confidence is lower than the baseline's.

An inconclusive run that does have a new breaking finding exits 3; the new finding wins.
An inconclusive run without one exits 0 with a warning, or 4 when strict, and the comment
says what wasn't checked, using the `not_checked` records.

**A verdict regression** (e.g. direct-ok → needs-changes, or a new remedy class such as
`reflash`) always comes with a new breaking or risky finding in a hazard category. The
verdict invariants guarantee it, so it needs no exit code of its own. The comment shows it
on its own line.

## 5. Outputs

**SARIF** (`--sarif`) is today's `render_sarif` with three changes:

- `partialFingerprints.zmigFingerprint/v1` (§2);
- a `baselineState` per result, one of `new`, `unchanged`, `updated` (subject value
  changed), or `absent` (fixed: emitted for accepted fingerprints no longer found);
- `level` stays `error` for breaking, `warning` for risky, `note` for info.

Results without a `file_line` have no location. GitHub then lists them without an
annotation, so they also go in the PR comment.

**PR comment** (`--pr-comment out.md`) is a short Markdown body with a hidden marker
(`<!-- zmig-watch -->`), so the CI step edits one comment instead of adding new ones:

```
zmig watch: Zephyr v4.3.0 → v4.4.0 (usb2canfdv1)   verdict: needs-changes (medium), was direct-ok
New breaking (1):  app/src/led.c:201 Function smf_set_handled, which your code uses, no longer exists …
New risky (2):     …
Fixed (1):         CONFIG_FOO no longer set …
Unchanged accepted: 7 breaking, 12 risky
Not checked: api partial (1 TU), sbom skipped (target build failed)
Full report: <artifact link>
```

The tool writes the file; posting is the workflow's job (§6), so zmig needs no GitHub
token.

## 6. GitHub Actions example

```yaml
name: zmig watch
on:
  pull_request:
  schedule: [{cron: "0 3 * * *"}]   # nightly against the moving target
jobs:
  watch:
    runs-on: ubuntu-24.04
    permissions: {contents: read, security-events: write, pull-requests: write}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - name: Restore zmig cache (mirror, workspaces, parse cache)
        uses: actions/cache@v4
        with:
          path: |
            ~/.cache/zmig/mirrors
            ~/.cache/zmig/workspaces
            ~/.cache/zmig/api
          key: zmig-${{ hashFiles('west.yml', '.zmig/baseline.json') }}
          restore-keys: zmig-
      - name: Zephyr SDK (ARM toolchain only)
        uses: zephyrproject-rtos/action-zephyr-setup@v1   # or a cached SDK tarball
        with: {toolchains: arm-zephyr-eabi, app-path: ., west-group-filter: -optional}
      - run: pip install 'zmig[api]'
      - name: zmig watch
        id: zmig
        run: |
          zmig watch --app app --board usb2canfdv1 --cmake-arg=-DFILE_SUFFIX=usbd_next \
            --module cmsis --module hal_stm32 \
            --from manifest --to track:next-release \
            --baseline .zmig/baseline.json \
            --sarif zmig.sarif --pr-comment zmig-comment.md --out zmig-report
      - if: always()
        uses: github/codeql-action/upload-sarif@v3
        with: {sarif_file: zmig.sarif, category: zmig}
      - if: always() && github.event_name == 'pull_request'
        uses: marocchino/sticky-pull-request-comment@v2
        with: {header: zmig-watch, path: zmig-comment.md}
      - if: always()
        uses: actions/upload-artifact@v4
        with: {name: zmig-report, path: zmig-report}
```

This is untested. The action inputs shown for `action-zephyr-setup` and the sticky-comment
action are from memory and must be checked against their current READMEs before this goes
in the docs.

## 7. Runtime budget, and whether the cached-parse design meets it

**Budget proposed for review:**

- **PR run: ≤ 10 min p95** on a 4-core hosted runner with the cache restored.
- **Nightly cold run: ≤ 60 min**, with no cache, or after the target moved.

Measured on this machine (DOGFOOD-2/3, DOGFOOD-3 §8.4). These aren't hosted-runner numbers:

| Step | Cold | Warm (what a PR run sees) |
|---|---|---|
| Zephyr mirror clone | minutes (1.1 GB) | cached |
| Module clones per workspace (`west update --narrow`) | ~30–90 min with 3 builds in parallel, first time (hal_stm32, hal_nxp, cmsis_6, mbedtls) | 0 s when the workspace is cached ("updating modules … done in 0s") |
| Configure + compile, per side | 5–60 s (e.g. Golioth `hello` 29 s; bms 7 s + 7 s) | rebuilt every PR (the app tree changed) |
| Analyzers with P2, parse cache cold | 31–48 s (Golioth, bms), 75–80 s (ZMK) | — |
| Analyzers with P2, parse cache warm | — | 6–8 s; ZMK 35–44 s (24 TUs whose target compile failed can't be cached: no dependency record) |

On cache sizes, a scan's own inputs are:

- the mirror (1.1 GB);
- two workspaces, 1.7 GB (v4.1.0) and 2.0 GB (v4.2.0) with this module set;
- the ARM-only SDK (0.94 GB);
- the parse cache, ~10 MB for nine configurations.

**Does the cached-parse design meet the PR budget?** Yes for the analysis, if the
workspaces are cached.

- The parse cache is keyed by content, not build directory. A PR rebuild of an unchanged
  file therefore produces the same key: the same TU text, flags and included-file hashes,
  including the regenerated `autoconf.h`, which is identical for an identical
  configuration. Only the TUs the PR changed are re-parsed, at about 0.3 s each.
- With the workspaces restored, a PR run is about two builds (≤ 2 min for these projects)
  plus ≤ 1 min of analysis, well inside 10 min.

**The risk isn't the parse cache. It's restoring about 5 GB of workspaces with
`actions/cache`.** GitHub's cache limit and restore throughput weren't measured here. If
restoring is too slow or too big, there are two options:

1. Cache only the mirror and the parse cache, and let `west update --narrow` fetch modules
   with `--depth 1`. That adds an estimated 3–10 min, which needs measuring.
2. Run on a self-hosted runner with a persistent `~/.cache/zmig`.

The design doesn't depend on either choice.

The nightly cold run fits 60 min only if module fetches are shallow. The 30–90 min
measured here were full clones running three at a time on one machine.

## 8. Open questions for review

1. **Fingerprint without line numbers:** two identical findings in one file (e.g. two
   `SYS_INIT` sites reported separately) merge into a count. Acceptable, or should the
   enclosing function be part of the fingerprint?
2. **Inconclusive runs:** 0 with a warning by default, 4 only with
   `--fail-on-inconclusive`. Or should strict be the default?
3. **Should risky ever fail** (e.g. `--fail-on risky`), or never?
4. **`track:next-release` vs `track:main`** as the documented default for nightly runs.
5. **Workspace caching strategy** (§7): measure `actions/cache` restore of ~5 GB first, or
   go straight to shallow module fetches?
