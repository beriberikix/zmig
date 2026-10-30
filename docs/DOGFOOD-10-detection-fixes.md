# DOGFOOD-10: the DOGFOOD-8 detection defects, fixed and measured

2026-10-30. Commits `3a9d791`..`9dc1591` (on `5348979`). Each was measured by
re-scanning all 14 harness configurations and diffing every finding against the previous
run.

**These fixes were informed by RIT FSW and CoffeeCaller, so both are tuned.** ardep
informed none of them and is still **clean**. B3's `va_start` half, found on ardep, is
deferred for that reason.

## 1. What each fix changed (every change, all 14 configurations)

| Commit | Defect | Every change in the re-scan |
|---|---|---|
| `3a9d791` | **B1:** `outside_build` didn't recognize fragments outside the app directory in their app-relative form (`../../boards/…`) | CoffeeCaller: 5 findings info → breaking, all on `native_sim.conf`: `symbol-removed NATIVE_UART_0_ON_STDINOUT` (C2) and `value-not-taken` PWM, GPIO, LED_STRIP, REBOOT |
| `e86d249` | **B2:** devicetree values compared as rendered text, so wrapping counted as a change | RIT: 3 risky findings gone (`rtc` clocks, `imu`/`magnetometer` irq-gpios). CANnectivity C1, C2: risky `&zephyr_udc0 changed: clocks, maximum-speed` → info "new property: maximum-speed". Golioth B and CANnectivity C1: one info finding each changed or gone |
| `3e480f3` | **B4:** a configure failure from a Kconfig abort didn't name the assignment that caused it | RIT: the configure failure now reads "…undefined symbol TMP116", at `sensor_module_defconfig:33` |
| `7db8ba0` | (Phase 0) the location prefix repeated a location the action already named | 6 actions shorter; no other change |
| `9dc1591` | **B3** (half): a function was reported removed when a target parse with errors missed the call | CoffeeCaller: `led_strip_update_rgb` "removed" is gone. CANnectivity still reports `smf_set_handled` removed (N1b) |

**B5** (apps that reach outside their directory by relative path) is a design limitation,
not a quick fix. It is unchanged.

## 2. Held-out projects now

| Project | Clean? | Strict | Lenient | Precision | Actionable | Fix-actionable |
|---|---|---|---|---|---|---|
| ardep (6) | **clean** (unchanged by these fixes) | 0 | 3 | 4/5 | 2 | 0 |
| RIT FSW (3) | tuned | 0 | 1 | **3/3** (was 3/6) | **1** (F1: `sensor_module_defconfig:33`) | 0 |
| CoffeeCaller (4) | tuned | **1** (C2 caught) | 2 | **8/8** (was 3/4) | **2** (C1: `native_sim.conf`; C2) | 1 (C2) |

**How the precision numbers moved:**
- **CoffeeCaller 8/8:** the five findings B1 restored are confirmed (true at the target,
  where the extension `.conf` isn't merged), and B3 removed the one harmless finding.
- **RIT FSW 3/3:** B2 removed its three harmless findings.

**CoffeeCaller C2 is caught.** It's breaking, at the exact line, with the cause stated, and
its fix is the maintainers' edit. C1 stays partly (b): the breaking `value-not-taken`
findings sit in the file the maintainers changed, but the cause (the extension `.conf` is no
longer applied) isn't stated.

## 3. The tuned set

- **CANnectivity C1, C2:** precision 7/8 → **7/7**. B2 moved the `&zephyr_udc0` finding,
  scored unverifiable (DOGFOOD-4-baseline), to info.
- **No other scored finding changed** on the original configurations.

## 4. Observed, not fixed: a fragment the target no longer merges

CoffeeCaller's restored `value-not-taken` findings say "check the dependencies of
CONFIG_REBOOT: y". `REBOOT` has no unmet dependency. The assignment isn't taken because
`boards/extensions/native_sim/native_sim.conf` isn't merged at the target at all, and that
is oracle C1's cause (Zephyr `c02c6add101`).

zmig already has what it needs to say so: the source build's "Merged configuration" list
has the fragment, and the target's doesn't. A finding such as "native_sim.conf, merged at
v4.2.0, isn't applied at v4.4.0" would state C1's cause at the file the maintainers moved.
It would be informed by CoffeeCaller, which is tuned already.
