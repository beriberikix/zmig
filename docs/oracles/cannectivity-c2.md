# Oracle: CANnectivity v1.1.0 → v1.2.0, configuration C2 (candlelight)

Written 2026-10-28, **before zmig was run on this configuration**. Only `--analyzers none`
verification builds were run. The items N1–N3, the refs and the not-items are defined in
[cannectivity.md](cannectivity.md); this file re-scopes them. The rules are in
[../ORACLE-RULES.md](../ORACLE-RULES.md).

## Why candlelight, not frdm_k64f

The planned C2 was `frdm_k64f` (NXP Kinetis), to add a second SoC family.

- Its `hal_nxp` download ran at roughly 200 KB/s on this network.
- An earlier attempt had stalled when a proxy change cut the connection.
- It held the v4.1.0 workspace lock for the whole download.

I stopped it, and chose `candlelight` (STM32F072, CAN 2.0 bxCAN) from the project's CI
integration platforms instead. It adds a different MCU and the **legacy USB device stack**
(`CONFIG_USB_DEVICE_STACK`, `subsys/usb/device/class/gs_usb.c`), where C1 uses
`device_next`. It doesn't add another SoC family. That's a recorded deviation from the plan.

## Scanned build C2

- `app/` on `candlelight` (resolves to `candlelight/stm32f072xb`), no file suffix (`prj.conf`
  plus `boards/candlelight_stm32f072xb.conf`).
- `ZEPHYR_EXTRA_MODULES=<cannectivity>`, modules `cmsis hal_stm32` (from the v1.1.0
  allowlist), SDK 0.16.8.

**Verification builds:** pre tree at `v4.1.0` and post tree at `30de5e8` (with `cmsis_6`),
both **ok**.

- **Project files compiled at both:** `app/src/{led,main,timestamp,usb}.c` and
  `subsys/usb/device/class/gs_usb.c` (the legacy-stack class; the `device_next` one isn't
  compiled).
- **Config at both:**
  - `CONFIG_CANNECTIVITY_LED=y`, `CONFIG_SMF=y` (the board overlay gives the channel
    `activity-leds`);
  - `CONFIG_USB_DEVICE_STACK=y`, `CONFIG_USB_DEVICE_GS_USB=y`,
    `CONFIG_USB_DEVICE_GS_USB_TIMESTAMP=y`.
- **Target:** compiles against `cmsis_6`.

## Scope

| Item | C2 | Reason |
|---|---|---|
| **N1** SMF API (`led.c`: N1a run functions, N1b `smf_set_handled`) | **in** (both parts) | `led.c` is compiled, `CONFIG_CANNECTIVITY_LED=y` |
| **N2** `cmsis` → `cmsis_6` | **in** | Cortex-M0 STM32F072; the target compiles against `cmsis_6` |
| **N3** `select UDC_ENABLE_SOF` in `subsys/usb/device_next/class/Kconfig.gs_usb` | **in** (parsed-file rule), **no artifact** | The module's Kconfig sources `device_next/class/Kconfig.gs_usb` unconditionally (`subsys/usb/Kconfig:5` → `device_next/Kconfig:6` → `class/Kconfig:4`), so it's parsed. But this build uses the legacy stack, so `USBD_GS_USB*` isn't even visible |

Revealing artifacts are as in [cannectivity.md](cannectivity.md).

## Counts (C2)

- **In scope: 3.** **Reachable ceiling:** 2/3 strict (N3 has no artifact).
