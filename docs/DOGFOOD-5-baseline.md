# Dogfood 5 baseline: OpenDeck, a fourth unseen project

- **Date:** 2026-10-29.
- **zmig:** `e3c78e4`. That's the oracle commit; the analyzer code is that of `91a5116`
  plus `477d419`, with docs-only commits since.
- **Rules:** [ORACLE-RULES.md](ORACLE-RULES.md).
- **Oracle:** [oracles/opendeck.md](oracles/opendeck.md) (`e3c78e4`), committed before
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

### Patch application (`docs/examples/opendeck-patched.sh`)

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
