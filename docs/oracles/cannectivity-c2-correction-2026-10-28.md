# Correction (2026-10-28): CANnectivity C2 scope, re-derived for candlelight

This is a dated correction to [cannectivity-c2.md](cannectivity-c2.md); the original isn't
edited.

**Why it exists.** C2 was planned as `frdm_k64f` (NXP Kinetis) and scanned as
`candlelight` (STM32F072), the deviation recorded in cannectivity-c2.md and in
DOGFOOD-4-baseline.md §1. Here the scope for the board actually scanned is re-derived
from the artifacts of its own verification builds, and the existing candlelight results
are re-scored under it.

## Scope evidence (candlelight)

Verification builds, `--analyzers none` (scratch: `~/.cache/zmig/dogfood/d4/verify/C2-{source,target}`):

| | Source: v1.1.0 tree @ `7823374` | Target: v1.2.0 tree @ `30de5e8` |
|---|---|---|
| Build | ok (build `b2fa6af63f983732`) | ok (build `f1ffa0d761c3cdb1`) |
| Project files compiled (`compile_commands.json`) | `app/src/{led,main,timestamp,usb}.c`, `subsys/usb/device/class/gs_usb.c` | the same five |
| `.config` | `CONFIG_CANNECTIVITY_LED=y`, `CONFIG_SMF=y`, `CONFIG_SMF_ANCESTOR_SUPPORT=y`, `CONFIG_USB_DEVICE_STACK=y`, `# CONFIG_USB_DEVICE_STACK_NEXT is not set`, `CONFIG_USB_DEVICE_GS_USB=y`, `# CONFIG_USB_DEVICE_GS_USB_TIMESTAMP_SOF is not set`, `CONFIG_SOC="stm32f072xb"` | the same; no `USBD_GS_USB*` and no `UDC_ENABLE_SOF` symbol at all |
| Kconfig files parsed (`zephyr/kconfig/sources.txt`) | — | includes `cannectivity/subsys/usb/device_next/class/Kconfig.gs_usb` (line 18) |
| Modules (`CMakeCache.txt` `ZEPHYR_MODULES`) | cmsis, hal_stm32 | includes `modules/hal/cmsis_6` |

## Scope per item (unchanged from cannectivity-c2.md, now with this evidence)

| Item | C2 (candlelight) | Reason |
|---|---|---|
| **N1** SMF API (`led.c`: N1a run functions, N1b `smf_set_handled`) | **in** | `led.c` compiled at both refs; `CONFIG_CANNECTIVITY_LED=y`, `CONFIG_SMF=y` |
| **N2** `cmsis` → `cmsis_6` | **in** | the target's `ZEPHYR_MODULES` includes cmsis_6 (Cortex-M0) |
| **N3** `select UDC_ENABLE_SOF` in `device_next/class/Kconfig.gs_usb` | **in** (parsed-file rule), **no artifact** | parsed at the target (`sources.txt` line 18), but the legacy stack is in use, so no `USBD_GS_USB*` or `UDC_ENABLE_SOF` symbol exists in `.config` |

Ceiling: 2/3 strict (N3 has no artifact).

## Re-scored candlelight results

The rules are [ORACLE-RULES.md](../ORACLE-RULES.md). The same refs and board throughout.

| Run | zmig | N1 | N2 | N3 | Strict | Lenient | Precision |
|---|---|---|---|---|---|---|---|
| Baseline (DOGFOOD-4 §2) | `d4308de` | partly (d): N1b caught at `led.c:225, 263, 271, 275`; N1a only at the `SMF_CREATE_STATE` lines 348–363 | caught | missed (no artifact) | 1/3 | 2/3 | 7/8 (88%) |
| After M13–M15 (DOGFOOD-4 §6) | `50970f6` | partly (d), as above (the target-build finding gained `led.c:225, 275`) | caught | missed | 1/3 | 2/3 | 7/8 (88%) |
| After definition-site citations | `477d419` (run with `e058b56`, same analyzer code) | **caught, not a clean measurement**: N1a now also cites the run functions' definitions `led.c:192, 219, 245, 293`, the lines the maintainers changed | caught | missed | **2/3 (not clean)** | 2/3 | 7/8 (88%) |

- **Precision** is unchanged in all three: 7 confirmed, plus `&zephyr_udc0`, which is
  unverifiable. No finding was added or removed; only evidence was added to the callback
  finding.
- **Not a clean measurement.** The definition-site change (`477d419`) was made in
  review, with CANnectivity N1a as its example. From that commit on, N1 in C1 and C2 is
  tuned, so the 2/3 strict figure isn't held-out evidence. The clean figures remain the
  first two rows.

## frdm_k64f (the planned board): not run

- **What happened:** its verification builds started with `--shallow`
  (`west update --narrow -o=--depth=1 cmsis hal_nxp`) and were stopped after 25 minutes,
  with 76 MB of hal_nxp downloaded at about 50 KB/s and the total unknown.
- **Why:** the reviewer had asked for the estimated download size before running it, and
  that hadn't been given.
- **Consequences:** no oracle was written for it and zmig wasn't run on it. **Status: not
  run.**
