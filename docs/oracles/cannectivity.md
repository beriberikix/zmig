# Oracle: CANnectivity v1.1.0 → v1.2.0 (Zephyr v4.1.0 → v4.2-branch)

Written 2026-10-28, **before zmig was run on this project**. Only `--analyzers none`
verification builds were run, and they produce no findings. The rules are in
[../ORACLE-RULES.md](../ORACLE-RULES.md). This file isn't edited after the run;
corrections go at the end, dated.

This is the third held-out project (DOGFOOD-4). It's unseen: no zmig change has been
informed by it.

## Upgrade and scanned build

**Project.** CANnectivity (https://github.com/CANnectivity/cannectivity), USB-to-CAN adapter
firmware.

- It's a Zephyr module (`zephyr/module.yml`, name `cannectivity`) with the app in `app/`,
  its own `gs_usb` USB class in `subsys/usb/device_next/class/`, Kconfig and devicetree
  bindings.
- Its `west.yml` imports upstream Zephyr by **release branch**, with a name-allowlist.

**Releases.** The pair is two consecutive releases, each pinned to a Zephyr release
branch:

| Release | Commit | Date | Zephyr pin |
|---|---|---|---|
| v1.1.0 (source tree) | `80612ee` | 2025-03-09 | `revision: v4.1-branch` |
| v1.2.0 (the maintainers' state after the upgrade) | `f3edac1` | 2025-08-08 | `revision: v4.2-branch` |

**Zephyr refs** are the branch tips on each release date:

- from `v4.1-branch` @ 2025-03-09 = `7823374e8721` (= `v4.1.0`);
- to `v4.2-branch` @ 2025-08-08 = `30de5e8a2cee`.

**Configuration C1** (this file):

- `app/` on `usb2canfdv1` (STM32G0B1, FDCAN), with `-DFILE_SUFFIX=usbd_next`: the CI's
  `app.cannectivity.usbd_next` test, whose integration platforms include `usb2canfdv1`.
- `ZEPHYR_EXTRA_MODULES=<cannectivity>`.
- Modules `cmsis hal_stm32`: the subset of the v1.1.0 allowlist (`cmsis hal_atmel hal_nxp
  hal_stm32 mcuboot segger`) this board uses.
- SDK 0.16.8.

**Scope evidence** comes from the verification builds, both **ok**:

- **Sources:** the pre tree at `v4.1.0` and the post tree at `30de5e8` (with `cmsis_6`).
- **Project files compiled at both:** `app/src/{led,main,timestamp,usb}.c` and
  `subsys/usb/device_next/class/gs_usb.c`.
- **Config at both:**
  - `CONFIG_CANNECTIVITY_LED=y`, `CONFIG_SMF=y`, `CONFIG_SMF_ANCESTOR_SUPPORT=y`;
  - `CONFIG_USB_DEVICE_STACK_NEXT=y`, `CONFIG_USBD_GS_USB=y`, `CONFIG_USBD_GS_USB_TIMESTAMP=y`;
  - `CONFIG_USBD_GS_USB_TIMESTAMP_SOF` **not set**.
- **Merged fragments:** `prj_usbd_next.conf`. **Overlay:**
  `boards/usb2canfdv1_stm32g0b1xx.overlay`.
- **Target:** compiles against `cmsis_6`.

## Items

The range `v1.1.0..v1.2.0` has 25 commits. Three are upgrade items; the rest are classified
at the end.

### N1. Zephyr 4.2 state machine framework API (`f7379ce`): in scope

**Zephyr's change** (`migration-guide-4.2.rst:770-775`):

- `smf_set_handled()` was removed;
- state run actions return `enum smf_state_result` (`SMF_EVENT_HANDLED` /
  `SMF_EVENT_PROPAGATE`) instead of `void`.

**Parts**, all in `app/src/led.c` (compiled; `CONFIG_CANNECTIVITY_LED=y`):

| Part | Where (v1.1.0) | What changed |
|---|---|---|
| N1a | `:192` `led_state_normal_run`, `:219` `led_state_normal_stopped_run`, `:245` `led_state_normal_started_run`, `:293` `led_state_identify_run` | `static void …(void *obj)` → `static enum smf_state_result …(void *obj)`, plus `return` statements |
| N1b | `:225, :263, :271, :275` | `smf_set_handled(SMF_CTX(lctx));` removed (the handled/propagate result is now the return value) |

**Revealing artifacts**, expected from the code (not checked before the run):

- **N1a:** at the target, the run functions are passed to `SMF_CREATE_STATE(...)` (`led.c`
  `:348-363`), whose run member now has type `enum smf_state_result (*)(void *)`. That's an
  incompatible-function-pointer diagnostic at those lines, and a changed declaration of
  `state_execution` / `struct smf_state`'s `run` member.
- **N1b:** `smf_set_handled` is no longer declared at the target: an implicit declaration
  at `led.c:225…275` and an undefined reference at link time. The declaration diff shows
  the function removed from `include/zephyr/smf.h`.

### N2. `cmsis` → `cmsis_6` in the manifest (`24a6d34`): in scope

- **What changed:** `west.yml` name-allowlist, `cmsis` → `cmsis_6`.
- **Why:** "which is now required for Cortex-M" (commit message;
  `migration-guide-4.2.rst:795-802`).
- **Scope:** in, because the target build compiles against `cmsis_6` (STM32G0, Cortex-M0+).
- **Revealing artifact:** the target build without `cmsis_6` fails, a missing header from
  the cmsis_6 glue or a Kconfig `&& 0` dependency (the forms seen on bms-firmware and
  nrf52840dk). The target `west.yml` declares `cmsis_6`.

### N3. `select UDC_ENABLE_SOF` for SOF timestamping (`09351bf`): in scope (by the parsed-file rule)

- **Zephyr's change:** `UDC_ENABLE_SOF` is new at the target
  (`drivers/usb/udc/Kconfig:45`; absent at `v4.1.0`). Start-of-frame events reach classes
  only when it's enabled.
- **What changed:** `subsys/usb/device_next/class/Kconfig.gs_usb`,
  `config USBD_GS_USB_TIMESTAMP_SOF` gains `select UDC_ENABLE_SOF`.
- **Scope:** in, because `Kconfig.gs_usb` is parsed (`CONFIG_USBD_GS_USB=y`). But
  `USBD_GS_USB_TIMESTAMP_SOF` isn't set, so the select has no effect in this build.
- **Revealing artifact:** none in this build. With `USBD_GS_USB_TIMESTAMP_SOF=y`, SOF
  timestamps would silently stop at the target. That's run-time only, with no build-time
  artifact, unless a tool flags a symbol whose new dependency isn't selected.

## Not upgrade items (with reasons)

| Commit(s) | Change | Why it's not an item |
|---|---|---|
| `73454f6` | `gs_usb.c`: no `net_buf_unref` after a successful `usbd_ep_enqueue()` (double free) | A project bug fix. `usbd_ep_enqueue()`'s documented contract is unchanged between the refs (`include/zephyr/usb/usbd.h`, the same "@return 0 on success" text), so the upgrade isn't shown to cause it. The file is compiled in C1, so if zmig flags it, that's scored as precision (§3), not recall |
| `8a041fb`, `86274a3` | use `USBD_SUPPORTS_HIGH_SPEED` to drop high-speed descriptors on FS-only builds | Optional adoption of a new v4.2 macro for footprint. Nothing breaks without it |
| `e4e5452` | LED handling on the system work queue | Footprint (1 KiB stack), per the commit message |
| `652b2a6`, `a36439a` | fewer CAN RX filters on bxCAN boards; release-config hardening | Project configuration choices |
| `fc0965c` | `CANNECTIVITY_LED` depends on `cannectivity` instead of `cannectivity-channel` | The project's own devicetree compatible |
| `89931f0` | new `candlelightfd_stm32g0b1xx_dual_usbd_next*.conf` | A project board config for another board, not upgrade-related |
| `399cb04`, `f6d9a77`, `db83ee5`, `e5c6538` | new board; channel-count Kconfig; LED conditional compilation | Features |
| `59c649f`, `79dd962`, `0dac8bf`, `f3edac1` | manifest tracking `main` / `v4.2-branch`; version bumps | The bump itself |
| `fcf106c`, `5f20dcc`, `d6ceb69`, `381c70f` | udev note, README, CI Python, pytest typing | Docs, CI, tests |

## Counts (C1)

- **In scope: 3 items:** N1 (2 parts), N2, N3.
- **Out of scope: 0** of the upgrade items (all three touch this build).
- **Reachable ceiling:** N3 has no artifact in this build, so the best possible strict
  recall is 2/3.
