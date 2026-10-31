# Fourth unseen project: candidates (waiting for a pick)

Gathered 2026-10-28 from public GitHub, read-only (`gh api`), and the local Zephyr mirror.

- **Excluded by the brief:** ZMK, Golioth, bms-firmware, smp_svr, example-application,
  and CANnectivity (DOGFOOD-4).
- **Nothing has been built or run** on any candidate.
- **After the pick:** verification builds (`--analyzers none`) decide the board and the
  scope; then the oracle is committed **before** zmig runs.

## Candidates

| # | Project | License | Zephyr source | Upgrade pair | Why it's interesting | Risks |
|---|---|---|---|---|---|---|
| 1 | [shanteacontrols/OpenDeck](https://github.com/shanteacontrols/OpenDeck) (MIDI/control-surface firmware, 960★) | Apache-2.0 | upstream, pinned by SHA in `west.yml` | **(a)** bump commit `ab8f6af` (2026-09-23): Zephyr `769c4f2` → `70be2ff`, about 3 months of `main` (both report 4.4.99). **(b)** releases v8.3.0 → v8.4.0: `bcf31c2` → `2f9f440`, about 2 weeks of `main` | Upstream refs, so no fork handling. It uses MCUboot (`9084925 refactor: use mcuboot`), which exercises the P1 field-upgrade analyzers. Real, frequently maintained product firmware | Large project with its own module set (`zlibs`, `zenv`). The supported boards need checking against SDK 0.16.8's toolchains; some may be custom boards in its tree |
| 2 | [arduino/ArduinoCore-zephyr](https://github.com/arduino/ArduinoCore-zephyr) | Apache-2.0 | **Arduino's Zephyr fork**: tags `zephyr-arduino-v4.2.0` → `zephyr-arduino-v4.4.2` | `tools/zephyr-sketch-tool/0.4.1` (2026-08-12) → `0.4.2` (2026-09-29): a 4.2 → 4.4 jump across two minors | Widely used. A two-minor jump exercises hops and guides. Vendor boards (Giga, Nano, UNO Q) | **Fork:** the refs aren't in upstream's mirror, so zmig must scan local checkouts of the fork (path refs), and fork patches mix with upstream changes. The "app" is the per-board loader |
| 3 | [Allegra42/ZEReader](https://github.com/Allegra42/ZEReader) (e-paper e-book reader) | MPL-2.0 | upstream, `revision: main` (floating) | bump commit `567104c` (2025-06-14, "updated zephyr, new cmsis and lvgl version") | LVGL, display, e-paper, filesystem: different subsystems from all earlier projects | **No pinned Zephyr:** both sides are "main at a date", so the pair has to be reconstructed by date (like CANnectivity's branch tips) and is less certain. No release tags |
| 4 | [tenstorrent/tt-system-firmware](https://github.com/tenstorrent/tt-system-firmware) | Apache-2.0 | **Tenstorrent's Zephyr fork** (`20721a1` isn't in upstream's mirror) | releases v80.x (97 tags) | Frequent releases, real product firmware | Fork (as #2); custom SoC boards, likely needing toolchains not installed here (SDK installs require telling you first) |

## Looked at and excluded

| Project | Why |
|---|---|
| ZSWatch/ZSWatch | nRF Connect SDK (`sdk-nrf v3.3.0-preview1`), not upstream Zephyr |
| boaks/zephyr-coaps-client | nRF Connect SDK (`nrf v3.1.0`) |
| brilliantlabsAR/halo-firmware | vendor Zephyr fork for Alif (`halo-zephyr-alif`) |
| Keychron/zgm | the repository is a website; no firmware source |
| LibreSolar/charge-controller-firmware | last tag 2021 (Zephyr 2.x era); same ecosystem as bms-firmware |
| zephyrproject-rtos/ArduinoCore-zephyr | tags from 2022–2023, Zephyr at a floating `main` |

## Recommendation

**#1 OpenDeck, pair (a).** Its refs are upstream and pinned. It's a real product with
MCUboot, and the 3-month `main` window gives more upgrade surface than (b).

**#2** is the strongest test of the fork handling, but it measures fork support as much
as upgrade analysis.
