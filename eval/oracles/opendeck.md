# Oracle: OpenDeck, Zephyr bump `ab8f6af` (DOGFOOD-5)

Written 2026-10-29, **before zmig was run on this project**.

- **Runs so far:** only `--analyzers none` verification builds, which produce no
  findings.
- **Rules:** [../ORACLE-RULES.md](../ORACLE-RULES.md).
- **Edits:** this file isn't edited after the run; corrections go in a separate, dated
  file.
- **Status:** fourth held-out project, unseen. No zmig change has been informed by it.

## Project and pair

- **Project.** [shanteacontrols/OpenDeck](https://github.com/shanteacontrols/OpenDeck),
  Apache-2.0: MIDI/control-surface firmware, mostly C++.
- **Build tool.** It builds with the `zenv` task runner (paradajz/zenv): a `west build
  --sysbuild`, after zenv's `patch.sh` has applied OpenDeck's `zephyr/patch/` patches to
  the workspace.
- **Pair.** The bump commit `ab8f6af` ("zephyr: bump to latest revision", 2026-09-23) and
  its parent `8db08c0`.
  - Zephyr `769c4f2` → `70be2ff`: both `main`, `VERSION` 4.4.99, both pinned by SHA in
    `west.yml`, both ask for SDK 1.0.1.
  - The project's own `zlibs`: `3499a36` → `13c8abf`.
  - `u8g2`, `vl53l4cx` and `vl53l5cx` are unchanged.
- **Follow-up fix commits:** none. The only later commits are `7f87679` (devcontainer)
  and `3f333bd` (removes an unused file), and none touch `app/`, `zephyr/patch/` or
  `west.yml`.

## Board: `nucleo_h563zi` (Zephyr `nucleo_h563zi/stm32h563xx`)

It was chosen because it's **one of OpenDeck's own CI targets**:

- its `app/boards/opendeck/nucleo_h563zi/firmware.overlay` has an `opendeck,bulk-build`
  node with `app`, `host-test` and `lint`, so `scripts/bulk_build.sh` puts it in CI's app
  matrix;
- on `3f333bd`, which differs from `ab8f6af` only in `.devcontainer/` and one workflow
  file, `App / App (nucleo_h563zi)`, `Tests (nucleo_h563zi)` and
  `Lint (nucleo_h563zi)` all succeeded.

It wasn't chosen for what the bump changed.

## Both sides are "upstream Zephyr plus OpenDeck's patches"

The patch-application rule, fixed before any zmig run:

1. **Source side:** Zephyr at `769c4f2` plus OpenDeck's **source-commit** patch set
   (`zephyr/patch/zephyr/*.patch` at `8db08c0`).
2. **Target side:** Zephyr at `70be2ff` plus the **same source-commit** patch set (not
   the maintainers' refreshed patches), applied one patch at a time.
   - Each patch is recorded as clean, offset, already applied, or failed.
   - A failed patch is a finding. It is skipped, never hand-fixed. If it blocks
     configure, the target build is inconclusive, and zmig must say so.
3. **How the checkouts are made:** local clones of zmig's Zephyr mirror, with **each
   patch as its own commit** on top of the upstream SHA.
   - Subject: the patch file name, as zenv's `patch.sh` writes it.
   - Body: its path, the OpenDeck commit, and its SHA-256.
   - Application uses `git apply` in byte-sorted order, as zenv does: offsets are
     accepted, fuzz isn't.
   - The checkouts are passed to zmig as local checkouts (`--from`/`--to` paths), and
     fork triage classifies the 31 commits.
   - OpenDeck's own tree is only read.

**Result of applying the source set** (`dogfood/d6/patched.sh`, 31 patches):

| Side | Clean | Offset | Already applied | Fails | HEAD |
|---|---|---|---|---|---|
| source, `769c4f2` | 15 | 16 | 0 | 0 | `b51212c` |
| target, `70be2ff` | 9 | 14 | 0 | **8** | `ace4550` |

Failing at the target, per `git apply --check`:

- `adc_stm32_polled_read`
- `arduino_nano_33_ble_drop_led4_board_init`
- `bossac_runner_handle_no_flash_load_offset`
- `esp32_eth_enable_rmii_output_clock`
- `esp32_eth_pass_multicast_frames`
- `esp32s3_devkitc_esp32s3_reset_once`
- `esp32s3_devkitc_use_wroom_n16r8`
- `kconfig_dt_node_prop_len`

## Scanned build

```
TARGET=nucleo_h563zi ZENV_PROJECT_ROOT=<OpenDeck tree> ZEPHYR_WS=<dir with modules/{u8g2,vl53l4cx,vl53l5cx}>
zmig scan --app <tree>/app --board nucleo_h563zi/stm32h563xx --sysbuild --shallow \
  --sdk ~/.cache/zmig/sdks/zephyr-sdk-1.0.1 \
  --module cmsis_6 --module hal_stm32 --module mcuboot --module mbedtls --module tf-psa-crypto \
  --cmake-arg=-DZENV_PRESET_NAME=default --cmake-arg=-DZENV_BUILD_TYPE=release \
  "--cmake-arg=-DCONF_FILE=<app>/common.conf;<app>/release.conf" \
  --cmake-arg=-DZEPHYR_EXTRA_MODULES=<zlibs> \
  --from <patched source checkout> --to <patched target checkout>
```

This reproduces zenv's `gen_build_config.sh` (preset `default`, build type `release`).

- **Modules.** `zlibs` is the only project module that's a Zephyr module. `u8g2` and the
  VL53 libraries are read as `$ZEPHYR_WS/modules/<name>`.
- **zmig is given the pre-bump tree** (`8db08c0`), as a developer starting the upgrade
  has it.

## Scope evidence (verification builds, `--analyzers none`)

| | Source: pre tree, patched `769c4f2` | Target: post tree (`ab8f6af`), `70be2ff` + source patch set |
|---|---|---|
| Result | **ok** (260 s; build `695256308365fdb9`) | **configure-failed** (build `35c0ea210593364a`): `app/firmware/Kconfig:185: couldn't parse 'default $(dt_node_prop_len,…)': macro expanded to blank string`, because the Kconfig function comes from the failed `kconfig_dt_node_prop_len` patch. mcuboot and the OpenDeck bootloader images had passed devicetree; the app image passed devicetree and stopped at Kconfig |
| Sysbuild images | `mcuboot`, `opendeck_bootloader`, `app` | the same three, configured in that order |
| Project TUs compiled | `app`: 92 (80 C++); `opendeck_bootloader`: 20 (all C++); `mcuboot`: 0 | — |
| Overlays applied to `app` | `app/boards/zephyr/nucleo_h563zi_stm32h563xx/{common,firmware}.overlay`, `app/boards/opendeck/nucleo_h563zi/{common,firmware}.overlay` | the same |
| `.config` (`app`) | `CONFIG_NET_L2_ETHERNET=y`, `CONFIG_ETH_DRIVER=y`, `CONFIG_ETH_STM32_HAL=y`, `CONFIG_ADC_STM32=y`, `CONFIG_BOOTLOADER_MCUBOOT=y` | — |
| `webusb_hw.cpp` | **not compiled** in any image | — |
| Zephyr facts at the target | — | hal_stm32 defines **no** `eth_rxd0_pc4`-style labels (0 files, against 31 at the source; `eth_rmii_*` exist). The H5 dtsi has an `adc12_common` node (absent at the source). `ETH_DRIVER` lost `default y if NET_L2_ETHERNET` (`drivers/ethernet/Kconfig:6`). Zephyr's `cmake_minimum_required` is 3.28 (3.20 at the source) |

## Items

### App, devicetree, Kconfig and CMake items (scored by ORACLE-RULES)

| Item | Maintainers' change (`ab8f6af`) | Scope | Reason | Revealing artifact |
|---|---|---|---|---|
| **D1** Ethernet pinctrl labels | `app/boards/zephyr/nucleo_h563zi_stm32h563xx/firmware.overlay`: `&mac` `pinctrl-0` `eth_*_pXn` → `eth_rmii_*_pXn` (7 labels) | **in** | the overlay is applied to `app`; Ethernet is on (`NET_L2_ETHERNET`, `ETH_STM32_HAL`) | the target's hal_stm32 no longer defines the old labels, so the pre-bump overlay fails devicetree compilation at those lines (dtc: undefined label) |
| **D2** ADC clock source / prescaler | the same overlay: `st,adc-clock-source` and `st,adc-prescaler` move from `&adc1`/`&adc2` to the new `&adc12_common` | **in** | `adc1` and `adc2` are enabled, and `ADC_STM32=y` | uncertain: the properties are still in `st,stm32-adc.yaml` at the target, so maybe no build error. A devicetree diff of `adc12_common` / `adc1` would show it |
| **K1** `CONFIG_ETH_DRIVER=y` | `app/common/network.conf` gains it | **in** | Ethernet drivers are on in this build. At the target, `ETH_DRIVER` no longer defaults to `y` with `NET_L2_ETHERNET`, so the pre-bump config silently loses `ETH_DRIVER` and `ETH_STM32_HAL` | a Kconfig diff (`ETH_DRIVER`, `ETH_STM32_HAL` no longer set) needs a target configure that gets past Kconfig. **Not reachable here** if `kconfig_dt_node_prop_len` blocks Kconfig, as in the verification build |
| **C1** `cmake_minimum_required` 3.27 → 3.28 | `app/CMakeLists.txt`, `app/bootloader/CMakeLists.txt` | **in**, **no artifact** | both files are parsed; Zephyr's minimum rose to 3.28 | none on this host (CMake 4.2.3 satisfies both) |
| **M1** `zlibs` `3499a36` → `13c8abf` | `west.yml` | **in**, **no artifact** | `zlibs` is a module of this build | the module's change is CMake minimums only |

### Not items for this build (with reasons)

| Change in `ab8f6af` | Why it's out of scope |
|---|---|
| `app/bootloader/src/protocols/webusb/hwa/hw/webusb_hw.cpp`: the USB vendor-request callback now returns `net_buf *` and allocates with `usbd_ep_ctrl_data_in_alloc` | a real API change, but the file isn't compiled for `nucleo_h563zi` in any image |
| board files of other targets (`app/boards/opendeck/{lilygo_t_eth_elite,metro_*,olimex_esp32_poe,ws_esp32_s3_eth}/…`, `app/boards/zephyr/{blackpill_f411ce,esp32s3_devkitc,nrf54h20dk,nucleo_f767zi,nucleo_h753zi,stm32f4_disco,w5500_evb_*,w6100_evb_*,weact_stm32f405}/…`) | other boards: not parsed in this build |
| `tests/src/**/CMakeLists.txt` | tests aren't part of the app build |

### Patch items (scored against `zmig plan`'s fork triage)

**How they're scored.** Fork triage classifies each of the 31 patch commits as
`upstreamed`, `applies` or `conflicts` against `70be2ff`. The expected class comes from
two things: the mechanical result at the target (above), and what the maintainers did.

**Every patch the maintainers added, removed or edited is an item:** 7 edited, 2
removed, 0 added.

| Item | Patch | Maintainers | Target, `git apply` | Expected class | Reason |
|---|---|---|---|---|---|
| P1 | `adc_stm32_polled_read` | edited | fails | **conflicts** | refreshed for upstream `drivers/adc/adc_stm32.c` changes |
| P2 | `arduino_nano_33_ble_drop_led4_board_init` | edited | fails | **conflicts** | refreshed for an upstream `board.c` change |
| P3 | `bossac_reset_cap` | edited | **applies** (offset 33) | **applies** | the edit only reorders the `-R` flag. That's the maintainers' own choice; the old patch still applies |
| P4 | `bossac_runner_handle_no_flash_load_offset` | edited | fails | **conflicts** | refreshed for upstream `bossac.py` changes |
| P5 | `esp32_eth_enable_rmii_output_clock` | edited | fails | **conflicts** | refreshed for upstream `eth_esp32.c` changes |
| P6 | `esp32s3_devkitc_esp32s3_reset_once` | edited | fails | **conflicts** | refreshed for an upstream `board.yml` change |
| P7 | `kconfig_dt_node_prop_len` | edited | fails | **conflicts** | refreshed for upstream `kconfigfunctions.py` changes. **Blocks the target configure** |
| P8 | `esp32_eth_pass_multicast_frames` | **removed** | fails | **upstreamed** | the target's `drivers/ethernet/eth_esp32.c:740` has the same `emac_ll_pass_all_multicast_enable(…, true)` with a comment, i.e. upstream took it |
| P9 | `esp32s3_devkitc_use_wroom_n16r8` | **removed** | fails | **conflicts** | not upstreamed: the board no longer has this module-variant selection. The maintainers dropped the patch, and the bump also changes `app/boards/zephyr/esp32s3_devkitc_esp32s3_procpu/common.overlay` |
| — | the 22 unchanged patches | kept as they were | 9 clean, 13 offset | **applies** | the maintainers kept them unchanged. A `conflicts` or `upstreamed` for any of them is a precision error |

- **Scored by fork triage regardless of board:** zenv applies every patch on every
  board, so the ESP32, nRF and Arduino patches are triaged too.
- **Patches that touch this board's build:** P1 (`adc_stm32.c` is compiled), P7 (the
  Kconfig function), and `nucleo_h563zi_openocd_default_runner` (unchanged).

## Counts

| Group | Items | Reachable ceiling |
|---|---|---|
| Scan items | **5** (D1, D2, K1, C1, M1) | 1–2 of 5 (D1; D2 if a devicetree diff runs). C1 and M1 have no artifact; K1 is unreachable while P7 blocks Kconfig |
| Patch items | **9** (P1–P9), plus the 22 unchanged patches for precision | 9/9: every class is derivable from the trees |

The target build is expected to be **inconclusive**. D1 should fail devicetree for the
pre-bump overlay. With D1's lines salvaged, P7 then blocks Kconfig.
