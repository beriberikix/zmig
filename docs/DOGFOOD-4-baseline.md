# Dogfood 4 baseline: CANnectivity, a third unseen project

Date: 2026-10-28. zmig `d4308de`: the code of `4a56a77` (P2 plus round 3), with only oracle
commits since. Scoring follows [ORACLE-RULES.md](ORACLE-RULES.md).

The oracles were committed before zmig was run:

- [oracles/cannectivity.md](oracles/cannectivity.md) for C1 (`459a3c8`);
- [oracles/cannectivity-c2.md](oracles/cannectivity-c2.md) for C2 (`d4308de`).

**This project is clean.** No zmig change has been informed by it. From here on, the only
changes allowed on it are crash and misleading-output fixes, listed in §5.

## 1. Setup

**Project.** CANnectivity v1.1.0 → v1.2.0: Zephyr `v4.1.0` (`7823374`) → v4.2-branch
(`30de5e8`). zmig is given the pre-bump v1.1.0 tree, with modules `cmsis hal_stm32` from
its allowlist, SDK 0.16.8.

| Config | Board / args | USB stack | Wall clock (builds of the scanned tree cold, workspaces warm) |
|---|---|---|---|
| **C1** | `usb2canfdv1`, `-DFILE_SUFFIX=usbd_next` | device_next | 85 s |
| **C2** | `candlelight` | legacy | 42 s |

**Deviation from the plan.** C2 was meant to be `frdm_k64f` (NXP), for a second SoC
family. Its `hal_nxp` download ran at about 200 KB/s on this network, so it was replaced
by `candlelight`, a different STM32 and USB stack.

**Target builds.** In both configurations the target first failed: no `cmsis_6`. zmig's
module recovery added it, and the rebuilt target then failed again, at link:
`led.c:225: undefined reference to 'smf_set_handled'`. That's oracle item N1b, so:

- `initorder` and `sbom` were skipped (no target ELF);
- the verdict is **direct-ok, low**: "the target build failed", confidence capped (M11).

All reports pass `invariant_violations`.

`checked` records (C1):

- libclang parsed 5 TUs at each side, 0 errors;
- pahole: 13 of 22 field offsets checked at the source, 0 disagree;
- binary boundaries: none;
- the project's own syscalls: none;
- module set: needed cmsis_6.

## 2. Recall (in scope only)

| Item | C1 | C2 | Findings, and why |
|---|---|---|---|
| **N1** SMF API | **partly (d)** | **partly (d)** | **N1b caught:** `API-FUNCTION-REMOVED-*` (`smf_set_handled`, breaking) and guide MG-4.2 "smf_set_handled has been removed" (breaking), both at exactly `led.c:225, 263, 271, 275`, the lines the maintainers changed. **N1a partly (c):** `API-CALLBACK-TYPE-CHANGED-*` (breaking) names the new expected type, `const state_execution` = `enum smf_state_result (*)(void *)`, and `BUILD-NEW-WARNING-*` gives the same cause. But both cite the `SMF_CREATE_STATE` initializers (`led.c:348–364`), which weren't changed, not the run-function definitions (`:192, 219, 245, 293`), which were |
| **N2** `cmsis` → `cmsis_6` | **caught** | **caught** | `MANIFEST-MODULE-REQUIRED-*` (breaking): module, where it's required, and `west.yml` line |
| **N3** `UDC_ENABLE_SOF` | missed | missed | no artifact (as the oracle said) |

| Config | Items | Strict | Lenient | Actionable (§7.1 of DOGFOOD-3) |
|---|---|---|---|---|
| C1 | 3 | 1 (33%) | 2 (67%) | 0: no action names `led.c` or `west.yml` |
| C2 | 3 | 1 (33%) | 2 (67%) | 0 |

**Reachable ceiling:** 2/3. N1a is the only reachable item not fully caught. A finding at
the run functions' definitions would catch it, and there's no other way.

## 3. Precision (every breaking/risky finding checked against both refs)

| Finding (C1) | Verdict | Why |
|---|---|---|
| `API-CALLBACK-TYPE-CHANGED-4b10a6e3` | confirmed (N1a) | the run functions no longer match `state_execution` |
| `API-FUNCTION-REMOVED-32468a0d` | confirmed (N1b) | `smf_set_handled` is gone from `smf.h` at the target |
| `GUIDE-ENTRY-MATCH-0681292e` | confirmed (N1b) | |
| `BUILD-NEW-WARNING-14baac28` | confirmed (N1a) | incompatible initializer |
| `BUILD-NEW-WARNING-7d28dbc8` | confirmed (N1b) | implicit declaration |
| `BUILD-TARGET-BUILD-FAILED-1f6e0e51` | confirmed | the build does fail. The finding shows no cause (M14) |
| `MANIFEST-MODULE-REQUIRED-f4a43627` | confirmed (N2) | |
| `DEVICETREE-NODE-CHANGED-39be8ba9` `&zephyr_udc0` clocks, maximum-speed (`src/usb.c:126`) | unverifiable | the app uses this USB controller; whether the clock or speed change alters behavior needs hardware |
| `DEVICETREE-NODE-CHANGED-64411e74` alias `led0`: phandle | harmless | phandle values are numbers dtc assigns; a renumbering changes nothing (M13) |
| `DEVICETREE-NODE-CHANGED-af0bc293` alias `mcuboot_led0`: phandle (`src/dfu.c:25`) | harmless | phandle renumbering (M13); `dfu.c` isn't compiled in C1 (M15) |
| `DEVICETREE-NODE-CHANGED-b4b32a89` `counter2`: phandle | harmless | phandle renumbering (M13); 4 of its 5 overlays are for other boards |

- **C1 precision: 7/11 (64%)**: 7 confirmed, 3 harmless, 1 unverifiable.
- **C2 precision: 7/8 (88%)**: the same 7 confirmed plus the `&zephyr_udc0` change
  (unverifiable). C2 has no phandle findings.

## 4. Compared with the other held-out projects (same zmig)

| Project | In scope | Strict | Lenient | Precision |
|---|---|---|---|---|
| Golioth + bms-firmware (7 configs, DOGFOOD-3 §8) | 18 | 9 (50%) | 11 (61%) | 43/70 (61%) |
| **CANnectivity (2 configs, clean)** | 6 | **2 (33%)** | **4 (67%)** | **14/19 (74%)** |

On a project nothing was tuned on, zmig catches the cmsis_6 requirement and the removed
API exactly, and gives the cause of the SMF type change at the wrong lines. Every miss is
either the no-artifact Kconfig item (N3), or that location.

## 5. Crashes and misleading output found by this baseline (to fix, listed separately; no tuning)

No crashes: both runs exited 0, and every report passes the verdict invariants.

- **M13.** Devicetree "node changed" findings count `phandle` changes. Phandle values are
  assigned by dtc, so a renumbering is noise: 3 risky findings in C1. Fix: never compare
  `phandle` (or `linux,phandle`) values.
- **M14.** A link error with a source location (`led.c:225: undefined reference to
  'smf_set_handled'`) isn't parsed. The linker pattern expects an `ld:` prefix, so the
  finding falls back to "1 error (step)" with no evidence. Fix: parse
  `<file>:<line>: undefined reference to …` as a linker error with that location.
- **M15.** Devicetree findings cite evidence in source files the build doesn't compile
  (`src/dfu.c:25`, an MCUboot DFU file not in C1's build). Fix: like the SBOM fix in
  DOGFOOD-3, cite only files the build compiled (ninja dependency records); a finding with
  no such evidence becomes info.
