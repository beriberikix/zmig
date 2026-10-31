# Dogfood 3 baseline: module-set detection, and five more held-out configurations

Date: 2026-10-28. zmig `0400082`: its code is `9c8da4a`, i.e. Step 2 plus module-set
detection; P2 isn't implemented. Scoring follows [ORACLE-RULES.md](../ORACLE-RULES.md). The
oracles for the new configurations were committed before these runs (`0400082`):
[oracles/golioth-configs.md](../oracles/golioth-configs.md) and
[oracles/bms-firmware-configs.md](../oracles/bms-firmware-configs.md).

Every configuration is reported on its own. The original three (ZMK, Golioth B,
bms-firmware A1) were re-run on the same zmig, so the table in §5 compares like with like.

---

## 1. Module-set detection (work item 1)

**What it does.** When the target build fails, zmig reads the failure for modules the build
needs but wasn't given:

- **A missing header** that only an inactive module's glue ships.
- **A Kconfig warning** naming `ZEPHYR_<X>_MODULE`, or a symbol from
  `zephyr/modules/<X>/Kconfig` whose dependencies reduce to `0`.
- **A CMake error** naming the module.

It only proposes modules that the target's `west.yml` declares. It then rebuilds the target
with them and analyzes that build. Each one becomes a `MANIFEST-MODULE-REQUIRED` finding
(breaking), with the build's own message and the `west.yml` line. The superseded target
build stays in the report's contexts.

Modules the source build used are also compared between the two manifests:

- **Removed** from the target manifest: breaking.
- **Re-pinned:** info, but only when no SBOM covers it, so there's no duplicate of SBOM's
  finding (caught by `test_sbom_matches_independent_sources`).

Regression tests:

- `tests/test_modulecheck.py` (6 tests): the cmsis_6 header case, ambiguity, already
  active, Kconfig `ZEPHYR_ZCBOR_MODULE`, CMake `ZEPHYR_TRUSTED_FIRMWARE_M_MODULE_DIR`, the
  nRF `&& 0` glue case, TinyCrypt and mbedtls selects, and the manifest diff.
- An end-to-end SDK test: `test_target_needs_a_module_the_scan_lacks`, nrf52840dk
  v4.1.0 → v4.2.0 with `cmsis` only. Its output is `["ok", "configure-failed", "ok"]`, with
  cmsis_6 added.

Found while building it, and fixed (misleading output): SBOM "header changed/removed"
findings cited includes in files that this build doesn't compile (bms-firmware's
`oled.c`). They now cite only includes in files the source build compiled, using ninja's
dependency records. A finding with none becomes info.

## 2. The new configurations

| Config | Board / args | Source build | Target build (pre-bump tree) | Wall clock | In scope |
|---|---|---|---|---|---|
| **B-rak** | `rak5010/nrf52840` | ok | configure failed → salvaged → ok | 52 s | 3 |
| **B-esp** | `nrf52840dk/nrf52840` + `SHIELD=golioth_esp_at` | ok | ok | 46 s | 2 |
| **B-native** | `native_sim` | ok | ok | 38 s | 1 |
| **A1-native** | `native_sim` | ok | configure failed (mbedtls) → **+mbedtls** → build failed (`__device_dts_ord_20` undeclared, B5) | 31 s | 5 |
| **A1-oled** | `bms_8s50_ic@0.2` + `oled.conf` + `SHIELD=uext_oled` | ok | build failed (cmsis_6) → **+cmsis_6** → build failed (`CFB_DISPLAY_HEIGH`, B7) | 29 s | 3 |

The source builds were reused from the verification runs (same build key); the target
builds ran from scratch.

---

## 3. Golioth configurations

### B-rak (3 in scope)

| Item | Label | Finding |
|---|---|---|
| G1 (G1a, G1b) | **caught** | `BUILD-NEW-WARNING-60ef13a4` (risky, `…/common/net_connect.c:36`, incompatible handler type) and `-e1303537` (risky, `…:69`, `'uint32_t' changes value … to '1'`) |
| G2 | missed | no artifact (as the oracle said) |
| G3 (`hello/boards/rak5010_nrf52840.conf:27`) | **caught** | `KCONFIG-SYMBOL-REMOVED-8108c136` (breaking) and guide `c20dd67e` (breaking, names `MODEM_CMUX_WORK_BUFFER_SIZE`) at `rak5010_nrf52840.conf:27` |

Strict **2/3 (67%)**, lenient **2/3 (67%)**.

**Precision** covers 8 breaking/risky findings:

- 4 confirmed: the two warnings and the two G3 findings.
- 2 harmless: `log.h` and `kernel.h` changed, but not in what `main.c` uses (same as B).
- 2 unverifiable: mbedtls and hal_nordic re-pins.

**4/8 (50%).**

### B-esp (2 in scope)

| Item | Label | Finding |
|---|---|---|
| G1 (G1a, G1b, **G1c**) | **caught** | G1a `BUILD-NEW-WARNING-1bed801c` (`net_connect.c:36`). G1b `-97218003` (`net_connect.c:69`). G1c: `-4fd15663` (risky) at `common/wifi.c:564` and `:569`, "passing argument 2 of 'net_mgmt_init_event_callback' from incompatible pointer type", plus four risky "`uint32_t` changes value" warnings at `wifi.c:481, 485, 508, 523` (the `switch (mgmt_event)` case labels truncated) |
| G2 | missed | no artifact |

Strict **1/2 (50%)**, lenient **1/2 (50%)**.

**Precision** covers 11:

- 7 confirmed: the seven warnings above. The four case-label truncations are real: with a
  `uint32_t` event the `switch` never matches.
- 2 harmless: `log.h`, `kernel.h`.
- 2 unverifiable: re-pins.

**7/11 (64%).**

### B-native (1 in scope)

| Item | Label |
|---|---|
| G2 | missed (no artifact; `MCUMGR` is off) |

Strict **0/1**, lenient **0/1**.

**Precision** covers 5:

- 2 confirmed, not in the oracle: `KCONFIG-SYMBOL-DEPRECATED-edf190a1` and guide `e066ec25`.
  `socs/native.conf:3` sets the now-deprecated `CONFIG_ETH_NATIVE_POSIX` in a fragment this
  build merges; the maintainers didn't change it.
- 2 harmless: headers.
- 1 unverifiable: the mbedtls re-pin.

**2/5 (40%).**

A deprecation of a symbol assigned in an applied fragment is counted as **confirmed**. The
claim is true, and the build is affected (a warning now, an error at removal). The same
rule is applied to A1-native's B4/B5 below.

---

## 4. bms-firmware configurations

### A1-native (5 in scope)

| Item | Label | Finding |
|---|---|---|
| B1 (part B1b, mbedtls) | **caught** | `MANIFEST-MODULE-REQUIRED-559fd34e` (breaking): "Kconfig from zephyr/modules/mbedtls/ is used, but the module is absent", plus `west.yml` line. zmig then built the target with mbedtls |
| B2 | missed | no artifact |
| B4 (`native_sim.conf:8`) | **caught** | `KCONFIG-SYMBOL-DEPRECATED-cd199449` (risky) at `boards/native_sim.conf:8` |
| B5 (conf `:11` + overlay `&uart1`) | **partly (d)** | The conf part is caught: `KCONFIG-SYMBOL-DEPRECATED-41cb296f`, guide `082e0937`, and `KCONFIG-DEPENDENCY-CHANGED-3c231f14/-3d44a4c1` (`UART_NATIVE_POSIX` → n). The overlay part is missed. Its symptom, `'__device_dts_ord_20' undeclared` in `zephyr/include/zephyr/device.h:96`, isn't attributed to project code, and the guide finding quotes only "has been deprecated", not the entry's next line ("instantiate `zephyr,native-pty-uart` nodes") |
| B6 (`native_sim.conf:13`) | **partly (c)** | `KCONFIG-VALUE-NOT-TAKEN-48a2e9d2` (breaking) has the right cause, "`LOG_BACKEND_NATIVE_POSIX=y` … unsatisfied: `(!LOG_MODE_MINIMAL)`", but it's at `native_sim.conf:14`, not the changed line 13 |

Strict **2/5 (40%)**, lenient **4/5 (80%)**. Partly: (c)×1, (d)×1.

**Precision** covers 10 breaking/risky findings, **10/10 (100%)**:

- target build failed (B5 symptom);
- value not taken (B6);
- module required (B1b);
- 2 kconfig deprecation warnings;
- 2 deprecated-symbol findings;
- the guide entry;
- 2 dependency-chain findings: the **first real Kconfig-promotion findings**, both true.

The two `BUILD-NEW-WARNING` kconfig deprecation warnings duplicate the two
`KCONFIG-SYMBOL-DEPRECATED` findings; see M10.

### A1-oled (3 in scope)

| Item | Label | Finding |
|---|---|---|
| B1 (part B1a, cmsis_6) | **caught** | `MANIFEST-MODULE-REQUIRED-194d1ea7` |
| B2 | missed | no artifact |
| B7 (`oled.c:109`) | **caught** | `BUILD-TARGET-BUILD-FAILED-0b778d9a` (breaking) at `src/oled.c:109`: "'CFB_DISPLAY_HEIGH' undeclared …; did you mean 'CFB_DISPLAY_HEIGHT'?". This is reachable only because module recovery got past `cmsis_core.h` |

Strict **2/3 (67%)**, lenient **2/3 (67%)**. Precision: **2/2 (100%)**.

### A1 (original `bms_16s100_sc`), re-run on this zmig

| Item | Before (Step 2) | Now |
|---|---|---|
| B1a cmsis_6 | partly (b) | **caught**: `MANIFEST-MODULE-REQUIRED-cd65f61b`, `zephyr/include/zephyr/arch/arm/asm_inline_gcc.h:24: cmsis_core.h is missing; the target's zephyr/modules/cmsis_6/ provides it`, `zephyr/west.yml:130 declares cmsis_6` |
| B2 | missed | missed |

Strict **1/2 (50%)**, lenient **1/2 (50%)**.

**Precision** covers 9 findings, **1/9 (11%)**. The target now builds, so SBOM runs and adds
seven risky findings:

- 1 confirmed: module required.
- 6 harmless:
  - the OpenThread/NVS guide match;
  - 5 "header changed" findings: `gpio.h`, `device.h`, `hwinfo.h`, `crc.h` and `kernel.h`.
    No changed line touches an identifier the compiled app files use, apart from same-value
    rewrites (`GPIO_INPUT (1U << 16)` → `BIT(16)`) and comments.
- 2 unverifiable: zcbor and hal_stm32 re-pins.

Recovering the target exposes SBOM's noise, which a failed target used to hide.

### Golioth B (original `nucleo_h563zi`), re-run

Same findings as Step 2: G1 caught, G2 missed. Strict 1/2, lenient 1/2, precision 2/6
(33%).

### ZMK, re-run

Breaking/risky findings are identical to Step 2 (corne and niceview). Strict 33%, lenient
72%, precision 12/22 (55%).

---

## 5. Side by side (this zmig, before P2)

| Config | In scope | Strict | Lenient | Precision | Verdict |
|---|---|---|---|---|---|
| ZMK (corne ∪ niceview) | 18 | 6 = 33% | 13 = 72% | 12/22 = 55% | unknown, medium |
| Golioth B (nucleo_h563zi) | 2 | 1 = 50% | 1 = 50% | 2/6 = 33% | direct-ok, medium |
| Golioth B-rak | 3 | 2 = 67% | 2 = 67% | 4/8 = 50% | direct-ok, medium (see M8) |
| Golioth B-esp | 2 | 1 = 50% | 1 = 50% | 7/11 = 64% | direct-ok, medium (see M8) |
| Golioth B-native | 1 | 0 = 0% | 0 = 0% | 2/5 = 40% | direct-ok, medium |
| bms A1 (bms_16s100_sc) | 2 | 1 = 50% | 1 = 50% | 1/9 = 11% | unknown, medium |
| bms A1-native | 5 | 2 = 40% | 4 = 80% | 10/10 = 100% | direct-ok, medium (see M11) |
| bms A1-oled | 3 | 2 = 67% | 2 = 67% | 2/2 = 100% | direct-ok, medium (see M11) |
| **Held-out total** (B…A1-oled) | **18** | **9 = 50%** | **11 = 61%** | **28/51 = 55%** | |

The held-out denominator went from 4 items to 18. Seven of the 18 are G2 or B2 (the same
two changes, scoped into several configurations), and neither has any artifact in any
build, so the reachable ceiling without new evidence sources is 11/18.

## 6. Known limitations that affected these results

1. **G2 and B2 have no artifact in any scanned build.** They're dependency- or
   default-level changes whose effect needs MCUmgr, or a missing `thingset,eeprom` chosen
   node.
2. **Build errors raised in Zephyr headers aren't attributed to the project.** The Step 2
   attribution covers warnings, not errors. B5's overlay part was lost to that (M9).
3. **The guide entry text is cut to its first sentence.** B5's remedy was in the second
   (M12).
4. **SBOM "header changed" findings are coarse.** They report any upstream change to an
   included header. With the bms target now building, they're 5 of A1's 8 false
   positives. P2 replaces them with declaration-level changes the project uses.

## 7. Misleading-output bugs found by this baseline (to be fixed before P2's measurement, each with a test; not tuning)

- **M8.** The Step 2 outside-the-build rule also downgraded findings whose evidence is a
  generated artifact (`<v4.1.0 hello>/zephyr/zephyr.dts:398`). On B-rak and B-esp the two
  nRF partition findings became info, while the layout category still says **hazard** and
  the verdict says **direct-ok**. Fix: evidence paths that aren't project files (the `<…>`
  artifact paths) never make a finding "outside the build".
- **M9.** A build error raised inside a Zephyr header (`device.h:96`,
  `'__device_dts_ord_20' undeclared`) is reported against the header. Fix: give errors
  the same project-site attribution as warnings (GCC's expansion notes and include chain).
- **M10.** Kconfig "Deprecated symbol X is enabled" warnings are reported a second time as
  `BUILD-NEW-WARNING` findings, alongside the kconfig analyzer's `SYMBOL-DEPRECATED`. Fix:
  buildlog skips kconfig warnings that the kconfig analyzer reports with a location.
- **M11.** The field-upgrade verdict is **direct-ok** on A1-native and A1-oled, whose
  target builds fail. The verdict's categories come from configure-time data, so this is
  explainable, but the report doesn't say that the target didn't build. Fix: when the
  effective target build failed, the verdict's confidence is capped at low, and the basis
  says why.
- **M12.** Guide findings quote only an entry's first sentence. B5's entry continues
  "Instead users should instantiate as many `zephyr,native-pty-uart` nodes…". Fix: quote
  the whole entry, as already parsed.
