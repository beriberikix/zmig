# Dogfood 14: an assisted migration of ardep, v4.3.0 → v4.4.0

Date: 2026-11-02. A coding assistant, working from the `ASSISTANT.md` of the first scan,
migrated ardep's `samples/uds` in batches, scanning and running `zmig compare` after each
batch. Every edit was shown to the user and applied only once approved.

This is an **assisted run**: findings were acted on between scans. So it is not a scored run
([ORACLE-RULES.md](../ORACLE-RULES.md)), and it changes no score. It is the manual run that
the coding-assistant design asked for, and it doubles as the first end-to-end use of
`zmig compare` and `scan --pin-source`.

## Setup

- **App:** a copy of ardep at `5b6d3cacd284` (the eval source,
  [configs.toml](../configs.toml) `ardep`), with its own git branch for the edits.
- **Scan:** the eval configuration's `scan` command, with ardep pointed at the copy.
  - From batch 2 on, the command also passed `--module tf-psa-crypto` (approved with batch
    2). The pin accepts it, because the module doesn't exist at v4.3.0, so the source
    side's inputs are unchanged.
- **Loop:** after each batch, scan the edited app with `--out <dir> --pin-source
  <original>/report.json`, then run `zmig compare <original>/report.json <dir>/report.json`.
  The comparison is always against the original scan.
- **zmig:**
  - Batch 1 ran on `4fa72c2` without a pin, then on `ffb1571` pinned. `--pin-source`
    didn't exist before `18ae96c`.
  - Batches 2, 3 and 2b first ran on `df48c4c` (#26 merged).
  - Batch 3 was re-run on the #27 code (`74eb7ab`) before it merged.
  - Batches 2b and 4 ran on `6151ede` (#27 merged).
  - Each re-run was against a fresh original scan.
  - Batches 1 and 2 were not re-run after #27.

## Batches

| Batch | ardep commit | Edits (approved) | `compare` | What it said |
|---|---|---|---|---|
| 1 (build) | `25e8ec8` | 2 zmig fixes (Kconfig override renames), 1 own (`full_name` in `board.yml`) | **4** unpinned; **0** pinned | Unpinned: 5 unseen, `unseen_source_build_broke` 5 of 5. Pinned: both overrides fixed; the board error updated |
| 2 (kconfig) | `825a1cb` | 2 own (`MBEDTLS_CIPHER_AES_ENABLED` → `PSA_WANT_KEY_TYPE_AES` + `PSA_WANT_ALG_ECB_NO_PADDING`; `STD_C11` → `STD_C17`) | **3** | 7 fixed; new: `MANIFEST-MODULE-NOT-IN-MANIFEST` (tf-psa-crypto) |
| 3 (manifest) | `58add29` | 1 own (`tf-psa-crypto` in `west.yml`'s allowlist) | **0** on `df48c4c`; **3** on `6151ede` | The batch-2 PSA edit never took effect. Before #27 zmig missed that; now there are 2 new `value-not-taken` findings |
| 2b (kconfig) | `b9e84a9` | 2 own (`CONFIG_PSA_CRYPTO=y`; `MBEDTLS_CTR_DRBG_ENABLED` → `_C`) | **3** | Target configures. 9 fixed; 21 new (17 removed Mbed TLS APIs in `authentication.c`, the compile failure, `fread`/`FILE`); 6 revealed |
| 4 (api) | `bfa1d55` | 1 own (`authentication.c` on the PSA Crypto API) | **4** | **Both builds `ok`.** 9 fixed, 0 new, 10 revealed |

**Totals:**
- 9 edits approved: 2 from zmig fixes, 7 the assistant's own. None was declined.
- Every breaking finding of the original scan ends fixed.
- Nothing has been run on hardware: the AES and random-seed code compiles, but whether it
  still authenticates is not checked.

**`unseen_source_build_broke`.** Without pinning, batch 1's edits stopped the app from
building at v4.3.0, and all 5 unseen findings were unseen for that reason. That met the
trigger for pinned-source scans. Pinned, every later batch has
`unseen_source_build_broke` 0 and nothing unseen.

**Revealed findings.** The original target never configured, so once it did, analyses that
had been skipped produced findings the original scan couldn't have seen:
- after batch 2b: 6 revealed;
- after batch 4: 10 revealed.

Of the 10 after batch 4:
- **6 cite lines or symbols the edits touched:**
  - 4 `dependency-changed` on the PSA and CTR-DRBG lines;
  - STD_C11 going from y to n for `MODULE_ISO14229_EXTERNAL`, from the C17 edit;
  - a 4.4 guide entry about OpenThread that matched the added `CONFIG_PSA_CRYPTO=y` line.
    The app doesn't use OpenThread, so this one is a false match.
- **The other 4 were hidden by the failing target:**
  - `fread` and `FILE` "changed" (below);
  - `MBEDTLS_SHA256` no longer set for `MBEDTLS_ENTROPY_C`;
  - Mbed TLS repinned (`sbom`).

`compare` claims no cause for any of them, which is right: telling them apart took reading
the evidence.

## What the run found in zmig

| # | Problem | Status |
|---|---|---|
| 1 | The build key ignored the contents of `-DZEPHYR_EXTRA_MODULES` directories. ardep's board lives in one, so the first after-scan reused the before-scan's builds, and `compare` exited 0 on edits it never built | fixed in `4fa72c2` (#26) |
| 2 | An edit that made the target configure fail broke the source build too, so every finding was unseen (exit 4) | `scan --pin-source` (#26) |
| 3 | An assignment that didn't take effect went unreported while the target configure aborted in Kconfig: no `.config`, so no kconfig analyzer. `compare` exited 0 on a broken edit | fixed in #27 (`value-not-taken` from the target log) |
| 4 | `depends on STD_C11 \|\| STD_C17 \|\| STD_C23` counted as a use of the deprecated STD_C11, so C17 left the finding "updated" | fixed in #27 |
| 5 | A target failure keeps its id whatever the error, so "board error fixed, Kconfig abort now" read "updated (1 -> 0 sites)" | fixed in #27 (`(was: …)`) |
| 6 | `fread`/`FILE` reported as a breaking change: the libc's `FILE` struct tag changed (`struct __sFILE` → `struct __file`) behind a typedef the code uses opaquely | open: needs typedef-aware comparison in the API analyzer |
| 7 | Adding `--module tf-psa-crypto` to the scan made `MANIFEST-MODULE-REQUIRED` "fixed" without any edit. `--module` isn't in the report, so `compare` can't see the change | known limit (DESIGN.md) |
| — | `MBEDTLS_CTR_DRBG_ENABLED`'s deprecation unreported: v4.3.0 already deprecated it, so it isn't new | not a bug |

**Also observed:**
- The configure-failure finding gave the user no sites while the target aborted on Kconfig
  warnings. The warnings themselves (what to fix) came only from the logs. #27's
  `value-not-taken` findings now carry them, for assignments the project makes.
- The `compare` summary line still uses watch's baseline wording ("outside the baseline",
  "accepted gaps").

## Notes for the next assisted run

- The original scan has to be re-run whenever zmig itself changes: a pin requires the same
  `zmig_build`, and `compare` refuses otherwise. That happened three times in this run.
- An edit can be the assistant's best reading and still be wrong (batch 2). The loop caught
  it only once #27 made Kconfig warnings visible without a configured target.
