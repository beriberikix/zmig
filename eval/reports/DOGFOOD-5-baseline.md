# Dogfood 5 baseline: OpenDeck, a fourth unseen project

- **Date:** 2026-10-29.
- **zmig:** `e3c78e4`. That's the oracle commit; the analyzer code is that of `91a5116`
  plus `477d419`, with docs-only commits since.
- **Rules:** [ORACLE-RULES.md](../ORACLE-RULES.md).
- **Oracle:** [oracles/opendeck.md](../oracles/opendeck.md) (`e3c78e4`), committed before
  zmig ran.

**This project is clean.** No zmig change has been informed by it. From here on, the
only changes allowed on it are crash and misleading-output fixes, listed in §8.

## 1. Setup

| | |
|---|---|
| Project | [shanteacontrols/OpenDeck](https://github.com/shanteacontrols/OpenDeck) (Apache-2.0, mostly C++) |
| Pair | bump commit `ab8f6af`; zmig is given its parent `8db08c0`'s tree |
| Zephyr | `769c4f2` → `70be2ff` (both `main`, 4.4.99), **each plus OpenDeck's source-commit patch set**, as local checkouts with one commit per patch (oracle §"Both sides…") |
| Board | `nucleo_h563zi/stm32h563xx`, an OpenDeck CI target |
| Build | OpenDeck's zenv build: sysbuild with 3 images (`mcuboot`, `opendeck_bootloader`, `app`); preset `default`, build type `release`; `TARGET`, `ZENV_PROJECT_ROOT` and `ZEPHYR_WS` set as zenv sets them |
| Modules | cmsis_6, hal_stm32, mcuboot, mbedtls, tf-psa-crypto (`--shallow`); `zlibs` via `ZEPHYR_EXTRA_MODULES`; `u8g2`, `vl53l4cx`, `vl53l5cx` as `$ZEPHYR_WS/modules/…` |
| SDK | Zephyr SDK **1.0.1** (minimal + ARM toolchain, installed for this run; SHA-256 verified) |

### Patch application (`eval/scripts/opendeck-patched.sh`)

The source-commit patch set has 31 patches. Per-patch results are in
`oracles/opendeck-patches-{source,target}.tsv`.

| Side | Clean | Offset | Already applied | Fails |
|---|---|---|---|---|
| source, `769c4f2` | 15 | 16 | 0 | 0 |
| target, `70be2ff` | 9 | 14 | 0 | **8** |

## 2. Builds

| Image | Source (patched `769c4f2`) | Target (patched `70be2ff`, pre-bump tree) |
|---|---|---|
| `mcuboot` | ok | ok |
| `opendeck_bootloader` | ok | ok |
| `app` | ok | **configure-failed**. First error: `devicetree error: /soc/ethernet@40028000: undefined node label 'eth_rxd0_pc4'`, which is oracle item D1: the pre-bump `firmware.overlay` uses hal_stm32 labels the target no longer defines |

- **The next blocker would be Kconfig.** The verification build of the **post** tree
  (with D1 already fixed) stopped at `app/firmware/Kconfig:185`, because
  `$(dt_node_prop_len,…)` comes from the failed `kconfig_dt_node_prop_len` patch (P7). So
  with D1 fixed by hand, the target still couldn't configure without refreshing P7.
- **What the failed app configure blinded.** At the target, `kconfig@app`,
  `devicetree@app`, `initorder@app` and `sbom@app` were skipped, and `api@app` /
  `api@opendeck_bootloader` are partial.
- **Verdict:** needs-changes (low); remedies: unknown ×1; unknown: mcuboot, storage, ble,
  wire.
- **Invariants:** `invariant_violations` passes for both the scan and the plan report.

## 3. Recall: scan items (in scope only)

| Item | Label | Findings, and why |
|---|---|---|
| **D1** Ethernet pinctrl labels | **partly (b)** | `DEVICETREE-NODE-CHANGED` (risky) for `&mac` in `opendeck_bootloader` names `pinctrl-0` as changed, at `boards/opendeck/nucleo_h563zi/common.overlay:11`. That's the right node and property, but not the file the maintainers changed (`…/nucleo_h563zi_stm32h563xx/firmware.overlay`), and not the cause (the labels were removed). The breaking `target-configure-failed` finding has the right symptom, but its evidence is sysbuild's wrapper message, not the devicetree error (M18) |
| **D2** ADC clock source / prescaler | **partly (b)** | `&adc1` and `&adc2` changed, including `st,adc-clock-source` and `st,adc-prescaler`. But they're reported for `opendeck_bootloader`, at `…/bootloader.overlay:13,17`, where ADC is off; the app image's devicetree wasn't compared. The move to `&adc12_common` isn't stated |
| **K1** `CONFIG_ETH_DRIVER=y` | missed | `kconfig@app` was skipped (no target `.config`). As the oracle predicted, this isn't reachable while the app can't configure |
| **C1** CMake minimum 3.28 | missed | no artifact (host CMake 4.2.3) |
| **M1** `zlibs` bump | missed | no artifact; also, the scan uses the pre-bump tree's `zlibs` at both refs (L6) |

| | Items | Strict | Lenient | Reachable ceiling (oracle) |
|---|---|---|---|---|
| Scan items | 5 | **0 (0%)** | **2 (40%)** | 1–2 |

## 4. Recall: patch items (zmig plan's fork triage), against the maintainers' real changes

`zmig plan --workspace <checkout>` was run with the patched source checkout as Zephyr,
`769c4f2` → `70be2ff`. It classified 31 local commits: 0 upstreamed, 26 apply cleanly,
5 conflict.

| Item | Patch | Maintainers | Target (`git apply`, zenv's method) | Expected | Plan says | Label |
|---|---|---|---|---|---|---|
| P1 | `adc_stm32_polled_read` | edited | fails | conflicts | **conflicts** (breaking) | caught |
| P2 | `arduino_nano_33_ble_drop_led4_board_init` | edited | fails | conflicts | **conflicts** | caught |
| P3 | `bossac_reset_cap` | edited (voluntary) | applies, offset 33 | applies | **applies**; risky "touches files upstream changed" | caught |
| P4 | `bossac_runner_handle_no_flash_load_offset` | edited | fails | conflicts | **conflicts** | caught |
| P5 | `esp32_eth_enable_rmii_output_clock` | edited | fails | conflicts | applies; risky "touches files upstream changed" | **partly (b)**: wrong class |
| P6 | `esp32s3_devkitc_esp32s3_reset_once` | edited | fails | conflicts | **conflicts** | caught |
| P7 | `kconfig_dt_node_prop_len` | edited | fails, **blocks the target configure** | conflicts | applies; risky "touches files upstream changed" | **partly (b)**: wrong class |
| P8 | `esp32_eth_pass_multicast_frames` | removed (upstream has it) | fails | upstreamed | applies; risky "touches files upstream changed" | **partly (b)**: wrong class |
| P9 | `esp32s3_devkitc_use_wroom_n16r8` | removed (obsolete) | fails | conflicts | **conflicts** | caught |
| — | the 22 unchanged patches | kept | 9 clean, 13 offset | applies | **applies** (all 22); 6 also risky for overlap | no errors |

| | Items | Strict | Lenient |
|---|---|---|---|
| Patch items | 9 | **6 (67%)** | **9 (100%)** |

**Why P5, P7 and P8 come out "applies".** Fork triage models `git rebase`: its trial
application is a three-way `git merge-tree`, which merges these three cleanly. zenv's
`patch.sh` applies with plain `git apply`, which has no three-way fallback, so all three
fail, and the build stops at the first one.

- **P8:** upstream made the same one-line change under a different subject, so it
  wasn't detected as upstreamed either.

## 5. Precision

### Scan: 22 breaking/risky findings

| # | Finding | Verdict |
|---|---|---|
| 1 | `target-configure-failed` (breaking) | confirmed (D1 symptom) |
| 2–8 | 7 × `API-MACRO-CHANGED` (`UMP_NUM_WORDS`, `HTTP_SERVICE_DEFINE`, `DNS_SD_REGISTER_SERVICE`, `CONTAINER_OF`, `__ASSERT`, `UMP_MT`, `DT_PHANDLE`) | **harmless: false**. Each macro is identical at both refs. The "new" form shown is a *different*, longer macro (`CONTAINER_OF_VALIDATE`, `__ASSERT_PRINT`, `UMP_MT_UTILITY`…), or a multi-line parameter list counted from its first line only (M17) |
| 9, 10 | `&mdio`, `&mac` changed, `mcuboot` image | harmless: MCUboot here has no networking (`CONFIG_NETWORKING` unset) |
| 11, 13 | `&mac` (compatible, pinctrl-0, resets…), `&mdio` (binding, compatible), `opendeck_bootloader` | confirmed: Ethernet is on in the bootloader, and these nodes changed upstream. 11 is D1's partial |
| 12, 14 | `&adc1`, `&adc2` changed, `opendeck_bootloader` | harmless: ADC is off in the bootloader (D2's partial, wrong image) |
| 15 | MCUboot revision `0fae892` → `aa32eaa`, 8 MCUboot sources compiled into `mcuboot` changed | confirmed (not in the oracle) |
| 16–22 | 7 × `SBOM-MODULE-REPINNED` (tf-psa-crypto, mcuboot, hal-stm32 ×2, mbedtls ×2, tf-psa-crypto), with compiled files changed | confirmed (not in the oracle) |

**Scan precision: 11/22 (50%).** 11 confirmed, 11 harmless, 0 unverifiable. Without M17's
7 false findings it would be 11/15.

### Plan: 15 breaking/risky findings

- **Confirmed: 8.** The 5 conflicts (P1, P2, P4, P6, P9), plus P5, P7 and P8's risky
  overlap flags: those patches do need action.
- **Harmless: 7.** P3's overlap flag (its edit was voluntary), and 6 unchanged patches
  flagged for overlap that still apply.

**Plan precision: 8/15 (53%).**

### Actionable (DOGFOOD-3 §7.1)

**0 for scan, 0 for plan.**

- **Scan:** no action names a file the maintainers edited (`firmware.overlay`,
  `network.conf`, `CMakeLists.txt`).
- **Plan:** each conflict's action names the conflicting *Zephyr* files ("Resolve by hand
  when rebasing; conflicting files: …"), not OpenDeck's `zephyr/patch/*.patch` files,
  which the maintainers edited. The patch's file name appears only as the commit subject
  in the title.

## 6. Not interpreted (C++ parse gaps)

These are reported as gaps, not as tuning targets.

- **Most of the app wasn't interpreted.** libclang parsed **49 of 51** app C++ TUs at the
  source with errors, nearly all for C++ standard headers it couldn't find (`array`,
  `cstddef`, `algorithm`, `atomic`, `cerrno`: "file not found (parsed with an empty
  stand-in)").
- **The target side has none at all:** `api@app` has no target compile commands, since
  the configure failed.
- **The bootloader:** 3 TUs had source flags, 1 of them with errors.
- **Consequence:** `api` is partial for `app` and `opendeck_bootloader`, and the API diff
  of this C++ code base is essentially not interpreted. Each gap is listed in
  `not_checked`.

## 7. Timings and downloads

| Step | Time |
|---|---|
| Source verification build (patched; source workspace seeded from the earlier download) | 260 s |
| Target verification build (incl. the target module fetch, 27 s) | 157 s |
| `zmig scan` (builds reused by key; target mcuboot 21 s, bootloader 34 s) | **239 s** |
| `zmig plan` | **38 s** |

| Download (this project) | Size | Time |
|---|---|---|
| Zephyr SDK 1.0.1 minimal / ARM toolchain | 70.65 MB / 101.75 MB | 611 s / 592 s |
| OpenDeck clone | 129 MB | 1586 s |
| u8g2 at `8430f65`, depth 1 | 49 MB, fetched twice before the reuse instruction; the second copy was avoidable | 333 s + 224 s |
| zlibs, vl53l4cx, vl53l5cx (both sides) | about 3 MB | a few seconds |
| Source Zephyr modules (cmsis_6, hal_stm32, mcuboot; later mbedtls and tf-psa-crypto), depth 1 | 43.7 MB | 7 min 23 s + 18 s |
| Target Zephyr modules, depth 1 | **46 MB** | **27 s** |
| Zephyr itself | 0 (already in the mirror) | |
| **Total** | **about 490 MB** | |

## 8. Crashes and misleading output found (to fix after this baseline, listed separately)

No crashes: both runs exited 0, and invariants pass.

- **M17.** `api._macro_at`, the fallback that reads a macro's target definition from its
  source header, matches `#define NAME` **without a word boundary**, so it picks the
  first longer macro with that prefix. It also counts a multi-line parameter list from
  its first line. Result: 7 false risky "macro changed" findings. It triggers whenever
  the target has no parse for a TU, as here, where the target app didn't configure.
- **M18.** The `target-configure-failed` finding's evidence is sysbuild's wrapper message
  ("CMake configure failed for Zephyr project: app"). The first real error a few lines
  above it isn't shown: `devicetree error: … undefined node label 'eth_rxd0_pc4'`.
- **M19 (observation).** `node-changed` findings are reported for images where the node's
  subsystem is off: `&mac`/`&mdio` in MCUboot, without networking, and `&adc1`/`&adc2` in
  the bootloader, with ADC off. The overlay is applied to that image, but nothing there
  uses the node.
- **M20 (observation).** The `libclang parse coverage` checked record says "51 project
  TU(s) with the source flags (49 with errors) … **0 parsed, 0 from the parse cache**", so
  the counts contradict each other.

## 9. Limitations that mattered

| # | Limitation | Effect here |
|---|---|---|
| L1 | Projects that build only on a patched Zephyr | Handled by passing patched local checkouts (`--from`/`--to` paths). Without that, the source can't configure |
| L2 | **Missing-module detection doesn't know mbedtls needs `tf-psa-crypto`** (known limitation; not fixed on this project) | the first source attempt failed until `--module tf-psa-crypto` was added by hand |
| L3 | libclang doesn't find the toolchain's C++ standard library headers | §6: the C++ API diff is not interpreted |
| L4 | Fork triage models `git rebase` (three-way merge); this project uses `git apply` | P5, P7 and P8 come out "applies" (§4), including P7, the patch that blocks the target configure |
| L5 | A failed target configure blinds Kconfig and devicetree for that image | K1 unreachable; D1 and D2 seen only through other images |
| L6 | Project modules (`zlibs`) are taken from the scanned tree for both refs | M1's bump isn't analyzed |
| L7 | Multi-image builds: findings come from whichever images built | D1 and D2 are partials reported on the bootloader image |

## 10. After the fixes (second column; **not a clean measurement**)

Made after seeing these results, at zmig `c7aac37`:

| Commit | Fix |
|---|---|
| `ffd6660` | plan `--patch-method` (git-apply / git-am / 3way), absorbed detection, patch file and hunk named in the action |
| `1344f72` | M17–M20 |
| `c7aac37` | C++ include directories from the build's compiler (infrastructure); API-coverage confidence cap |

Both runs used the same patched checkouts, tree and board as the baseline:

- **plan:** `--patch-method git-apply --patch-dir <tree>/zephyr/patch`, because
  OpenDeck's build tool applies its patches with plain `git apply`
  (`paradajz/zenv@c7ca4fb scripts/patch.sh:80,84`);
- **scan:** as before.

**Every item whose result changed because of a fix is marked †, meaning not clean.** The
clean column stays the baseline.

### Scan items

| Item | Baseline | After fixes | Why it changed |
|---|---|---|---|
| D1 Ethernet labels | partly (b) | **partly (c) †** | M18: the configure finding now says "app image: devicetree error: … undefined node label 'eth_rxd0_pc4'", the right cause. But it has no project file:line (the overlay the maintainers changed), so it stays partly |
| D2 ADC properties | partly (b) | **missed †** | M19: the bootloader's `&adc1`/`&adc2` findings, D2's only partial, are now info. The bootloader has no ADC device, so they were harmless |
| K1, C1, M1 | missed | missed | unchanged: the target app never reaches Kconfig |

### Patch items

| Item | Baseline | After fixes |
|---|---|---|
| P5 `esp32_eth_enable_rmii_output_clock` | partly (b) | **caught †**: conflicts, hunk `eth_esp32.c:709` |
| P7 `kconfig_dt_node_prop_len` | partly (b) | **caught †**: conflicts, hunk `kconfigfunctions.py:663` |
| P8 `esp32_eth_pass_multicast_frames` | partly (b) | **caught †**: "upstreamed": "Drop the project's patch …: upstream already absorbed the change" |
| P1–P4, P6, P9 | caught | caught |

In the default mode (no `--patch-method`), P5, P7 and P8 show "(fails under git-apply;
applies under 3way)", and the verdict is the stricter one.

### Side by side

| | Baseline (clean) | After fixes † |
|---|---|---|
| Scan strict / lenient | 0/5 / 2/5 | 0/5 / **1/5** |
| Scan precision | 11/22 (50%) | **11/11 (100%)**: the 7 false macro findings (M17) and 4 no-device node findings (M19) are gone or info |
| Scan actionable | 0 | 0 |
| Plan strict / lenient | 6/9 / 9/9 | **9/9 / 9/9** |
| Plan precision | 8/15 (53%) | 8/15 (53%): 7 conflicts + P8 confirmed; P3 and 6 unchanged patches flagged for overlap are harmless |
| Plan actionable | 0 | **8/9**: each conflict or drop action names the patch file the maintainers edited or removed. P3, edited voluntarily, isn't named |
| C++ TUs parsed with errors (app, source) | 49/51 | **0/51** |
| API not interpreted (app) | 49/51 | 51/51: no target compile commands, because the target configure fails. Verdict confidence capped at low with that reason |
| Verdict | needs-changes (low) | needs-changes (low) |

### Regression check on the other held-out projects (zmig `c7aac37`)

| Config | Breaking/risky findings | Verdict | Compared with | Result |
|---|---|---|---|---|
| Golioth B | 9, the same set | direct-ok (medium) | `d5/golioth-B` (DOGFOOD-5 watch stability runs) | **no regression**. The typedef finding now also cites `net_connect.c:20`, G1a's handler definition (item c, `477d419`). G1 was already caught, so the numbers are unchanged |
| CANnectivity C1 | 8, the same set | direct-ok (low) | DOGFOOD-4 §6 | **no regression**. The callback finding cites the run-function definitions (item c; already marked not clean in the C2 correction) |
| CANnectivity C2 | 8, the same set | direct-ok (low) | `cannectivity-c2-correction-2026-10-28.md` | **no regression** |
| bms-firmware A1 | 9 (2 breaking, 7 risky): recall 1/1 of 2 items, precision 1/9 | unknown (medium) | DOGFOOD-3 §2–3 (P2 column): 1/1, 1/9, unknown (medium) | **no regression** (its earlier report was lost with the old scratchpad, so it's compared against the recorded numbers) |

API coverage in these runs: every TU interpreted (Golioth `hello` 31/31, CANnectivity
5/5, bms 19/19). The confidence cap doesn't trigger.

### Still a limitation

The missing detection of mbedtls's `tf-psa-crypto` dependency (L2) is recorded, not
fixed.

## 11. Round 2 after the baseline: plan precision and source locations (**not clean**)

### 11.1 The 7 false or unhelpful plan findings (§10's 8/15)

All 7 are risky `applies-cleanly` "applies cleanly but touches files upstream changed".
Plan wasn't scored on any other project: there were no patch sets before OpenDeck, and
DOGFOOD-1 only timed `plan` and recorded its hop and fork messages.

| Patch | Maintainers | Nearest upstream change to the patch's lines | Verdict | Cause | Generic? |
|---|---|---|---|---|---|
| P3 `bossac_reset_cap` | edited (voluntary) | 56 lines | harmless | file-level overlap counted as a hazard | yes |
| `esp32s3_mcuboot_rodata_region_start` | unchanged | 17 | harmless | same | yes |
| `esp32s3_reset_with_watchdog_after_flash` | unchanged | **1** (adjacent line) | unhelpful: true and worth a look, but no action needed | same | yes |
| `usb_midi2_01_configurable_buffer_size` | unchanged | 5 (`Kconfig.midi2`), 118 (`usbd_midi2.c`) | harmless | same | yes |
| `udc_rpi_pico_disconnect_on_disable` | unchanged | 176 | harmless | same | yes |
| `usb_midi2_02_ump_burst` | unchanged | 9 | harmless | same | yes |
| `esp32_mcuboot_rodata_region_start` | unchanged | 60 | harmless | same | yes |

None of them is **wrong**: each patch does touch a file upstream changed. The finding just
isn't a hazard.

**Fix (`76196d6`, post-baseline, generic).** Overlap is risky only when upstream changed
lines within 3 lines of the patch's own changed lines (3 is git's default diff context).
Otherwise it's info: "applies cleanly; upstream changed other parts of …". There's a
regression test on the real Zephyr files with two of these patches. (Replaced in §11.4 by
a window taken from each patch's own hunks.)

### 11.2 Source locations for configure errors (`a622add`, post-baseline, generic)

| Error | What the tools say | What the finding now cites |
|---|---|---|
| devicetree "undefined node label 'eth_rxd0_pc4'" | edtlib names only the node. `dtc -T -T` on the image's `zephyr.dts.pre` annotates the property: `firmware.overlay:20:5-26:34` | `boards/zephyr/nucleo_h563zi_stm32h563xx/firmware.overlay:20`, "pinctrl-0 references undefined label 'eth_rxd0_pc4'". Action: "Fix …/firmware.overlay:20 …" |
| Kconfig "…/app/firmware/Kconfig:185: error: couldn't parse …" (post-tree verification log) | the path and line are in the message | `firmware/Kconfig:185` |

The location comes from the tools' own output, never from searching the sources.
Regression tests use the real log and preprocessed devicetree.

### 11.3 Three columns (zmig `a622add`)

| | Baseline (clean) | After §10 fixes † | **After §11 fixes †** |
|---|---|---|---|
| Scan strict / lenient | 0/5 / 2/5 | 0/5 / 1/5 | **1/5 / 1/5**: D1 caught † (location and cause) |
| Scan precision | 11/22 | 11/11 | 11/11 |
| Scan actionable | 0 | 0 | **1/5**: D1's action names the overlay the maintainers edited |
| Plan strict / lenient | 6/9 / 9/9 | 9/9 / 9/9 | 9/9 / 9/9 |
| Plan precision | 8/15 (53%) | 8/15 (53%) | **8/9 (89%)**: the one left is the adjacent-line overlap (unhelpful) |
| Plan actionable | 0 | 8/9 | 8/9 |

† Not a clean measurement. D1 changed in both §10 and §11, D2 in §10, and P5, P7 and P8
in §10.

### 11.4 Proximity window from each patch's hunk context (post-baseline, **not clean**)

The fixed 3-line rule of §11.1 is replaced. Each hunk's window is now:

- its changed lines;
- plus the leading and trailing context lines **the patch itself carries**. These are the
  lines `git apply` matches.

With `--patch-dir`, the context comes from the patch file matched to the commit. Without
one, it is git's default 3 lines, cut short at the ends of the file (as `git diff` makes
them). Overlap is risky only if upstream changed or inserted lines **inside** a window.

OpenDeck's 31 patches have 63 hunks:

- 61 carry 3 leading context lines, and 2 hand-edited hunks carry 4 or 5;
- 13 carry fewer than 3 trailing lines.

**Re-run** (zmig with this change; `--patch-method git-apply`; with and without
`--patch-dir`; `~/.cache/zmig/dogfood/d6/rescore3-*`):

| Patch | §11 (fixed 3 lines) | §11.4, with `--patch-dir` | §11.4, no patch dir |
|---|---|---|---|
| `esp32s3_reset_with_watchdog_after_flash` | risky: "upstream changed lines next to it" | **info**: "upstream changed other parts of board.cmake" | info |
| every other patch | unchanged | unchanged | unchanged |

- **Why it changed.** The patch's one hunk (`board.cmake @@ -6,4 +6,5 @@`) ends at the end
  of the file, with 1 trailing context line (line 9). Upstream appended its QEMU block
  after line 9: outside the lines the patch reads.
- **Totals.** Plan goes from 7 breaking, 2 risky to 7 breaking, 1 risky. The risky one left
  is `esp32_eth_pass_multicast_frames` (absorbed upstream; the patch no longer applies).
- **Score.** Recall is unchanged: 9/9 strict and lenient, 8/9 actionable. Precision goes
  from 8/9 to **8/8**. The finding removed was scored "unhelpful: true and worth a look,
  but no action needed" (§11.1); the maintainers left that patch unchanged.

This is **tuned**: the rule was changed after seeing this case. Two things limit what the
8/8 says:

- **Under `git-apply`, an applying patch rarely has near overlap.** An upstream change
  inside a window changes the context `git apply` matches, so the patch conflicts instead.
  The window matters mostly for `3way` (rebase) workflows, where such changes still merge.
- **Regression tests.** `tests/test_patch_methods.py` has two:
  - the watchdog case, on the real file, with and without the patch text;
  - a constructed 3way case. The patch is made with 5 context lines, and upstream changes
    a line 4 away from the patch's change. That is risky with the patch file's context,
    and info with the default 3.
