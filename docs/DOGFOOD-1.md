# Dogfood 1: ZMK v3.2 → v3.5, and smp_svr v4.2.2 → v4.3.0

Date: 2026-10-27. zmig at the commit that adds this file. Everything below was run, and the
numbers come from the reports in the run directory (`scratchpad/dogfood/out/`, not kept).

**Short version.** On a real project, the zmig I started with stopped at the first
Kconfig error and analyzed almost nothing. With the fixes made during this run:

- **Recall:** zmig flags in advance 8 of the 19 in-build changes the ZMK maintainers had to
  make, and partly flags another 6 (74% caught or partly caught).
- **Precision:** 17 of its 22 breaking/risky findings are confirmed hazards, 77%. One of them
  is a hazard the maintainers still haven't fixed.
- **What's left:** the misses cluster in two classes, init-order changes and API changes that
  only show at run time.
- **smp_svr:** the P1 field-upgrade verdict found the real MCUboot mode hazard, after two false
  positives were fixed.

---

## 1. Setup

### ZMK (primary)

- **Project:** zmkfirmware/zmk, cloned read-only into a scratch directory. ZMK's app is
  `app/` and its manifest is `app/west.yml`: an application manifest that imports Zephyr
  from **zmkfirmware/zephyr**, a fork pinned by **branch name**
  (`v3.2.0+zmk-fixes`, …), with a 17-module blocklist (MCUboot is blocked).
- **Upgrade chosen: v3.2 → v3.5** (candidate A of three; see the conversation). The ZMK
  commits `f4fce9e1`…`f72f415c` were committed together on 2024-02-09 (35 commits), with
  fix-ups through 02-20.
- **"Before the bump":** ZMK `b8846cf6`, the parent of the series.
- **Zephyr refs**, reconstructed as the fork branch head before the bump date (the branches
  have moved since; their reflog isn't public, so a force-push would go unnoticed):
  - from `1ae0eb5ce8ad` = v3.2.0 + 34 fork commits
  - to `339570e2393b` = v3.5.0 + 21 fork commits

  zmig can't resolve a fork branch name, so both were passed as local checkouts (git
  worktrees of a scratch repository that borrows zmig's mirror objects; fetching the fork
  history took 17 s this way, against more than 10 minutes cold).
- **Builds:** `nice_nano_v2` with shield `corne_left` ("corne"), and the same with the
  nice!view display shield (`corne_left nice_view_adapter nice_view`, "niceview") to bring
  ZMK's display code into the build. Modules: hal_nordic, cmsis, tinycrypt, lvgl.
- **SDK:** 0.16.8 (installed for this run, ARM toolchain only, sha256-verified, not
  registered). See §6 for why not 0.17.4.

### smp_svr (secondary)

- Zephyr's `samples/subsys/mgmt/mcumgr/smp_svr` as of v4.2.2, with `overlay-bt.conf` (MCUmgr
  DFU over BLE), sysbuild and MCUboot, on `nrf52840dk/nrf52840`, v4.2.2 → v4.3.0, SDK 0.17.4.
- The sample's own changes between those tags are overlay renames and new transports. The
  field-upgrade oracle is the v4.3 migration guide's MCUboot note
  (`migration-guide-4.3.rst:485-492`).

---

## 2. Recall: ZMK maintainers' changes vs. what zmig flagged

**Oracle:** the 35-commit series plus five later fix-ups that are plausibly upgrade-related.
**In this build** means the commit touches a file the source build compiled
(`compile_commands.json`, ninja deps) or a Kconfig/conf/devicetree file it parsed. Docs, CI,
test snapshots and a pure clean-up (`ccf03801`) are left out.

**Before** is zmig as it was at the start (first scan, SDK 0.17.4). **After** is with the
fixes in §5 (final scans, SDK 0.16.8).

### 2.1 Changes in this build (19)

| # | Commit | What the maintainers changed | Before | After | zmig finding (after) |
|---|---|---|---|---|---|
| 1 | `f4fce9e1` | `LV_Z_DPI` → `LV_DPI_DEF`; `SSD1306_REVERSE_MODE` → DT `inversion-on` (all shields) | partly (guide) | **caught** | `BUILD-KCONFIG-OVERRIDE-ORPHANED-c7ef6f3a`, `-06da6565`; `GUIDE-ENTRY-MATCH-b3668791` points at `corne.dtsi:68` (the DT replacement) |
| 2 | `bf4008da` | `SYS_INIT` functions take no arguments | missed | partly | `BUILD-NEW-WARNING-adbded30` (info): the incompatible-pointer warning is attributed to `zephyr/include/zephyr/init.h`, not to the app's call sites |
| 3 | `0b5afbf9` | `zephyr/bluetooth/hci_err.h` → `hci_types.h` | missed | **caught** | `BUILD-TARGET-BUILD-FAILED-f5607720`: `src/ble.c:22` no such file (no replacement named) |
| 4 | `bd9c71ab` | ZMK's `MAX17048` collides with Zephyr 3.5's new fuel-gauge driver | partly (generic "configure fails") | **caught** | `BUILD-KCONFIG-NAME-COLLISION-7c2cfcbc` (app `module/drivers/sensor/max17048/Kconfig:6` vs Zephyr `drivers/fuel_gauge/max17048/Kconfig:7`) |
| 5 | `d06e90e7` | syscall headers need `zephyr_syscall_header()` + `APPLICATION_DEFINED_SYSCALL` | missed | partly | `BUILD-TARGET-BUILD-FAILED-f5607720`: `syscalls/behavior.h` and `syscalls/ext_power.h` missing (the symptom; the cause isn't named) |
| 6 | `ba1a6c08` | `void main` → `int main` | missed | missed | the guide says so (`migration-guide-3.5.rst:71`); the matcher didn't link it to `src/main.c` |
| 7 | `d6de8a3a` | behaviors: `APPLICATION` → `POST_KERNEL` init level | partly | partly | `GUIDE-ENTRY-MATCH-39073cd9`: the right entry, but it matched the literal `POST_KERNEL` in drivers that already comply, not ZMK's `BEHAVIOR_DT_INST_DEFINE(…, APPLICATION, …)` |
| 8 | `b6d9f3c9` | `setting_name[15]` → `[17]` in `src/ble.c` | missed | missed | no diagnostic in this build |
| 9 | `98e3b8b4` | `pm_state_force(SOFT_OFF)` → `sys_poweroff()` | caught | **caught** | `GUIDE-ENTRY-MATCH-9754923b` (breaking, low confidence) at `src/activity.c:73` |
| 10 | `f4e6d704` | `CONTAINER_OF` with delayable work | missed | missed | no diagnostic at v3.5 |
| 11 | `413820fc` | nice!view: `char text[9]` → `[10]`, arc 359 → 360, Kconfig | missed | partly (niceview) | `BUILD-NEW-WARNING-f768b8ba`: the `sprintf` overflow at `status.c:185` is exactly the `text[10]` fix; the arc change is missed |
| 12 | `8e9b4fa5` | `NRF_STORE_REBOOT_TYPE_GPREGRET` for `&bootloader` | caught | **caught** | `GUIDE-ENTRY-MATCH-f6e35e8e` at `src/behaviors/behavior_reset.c:34` (3.4 release notes: GPREGRET reset reason dropped) |
| 13 | `a6c03994` | drop the `BT_LL_SW_LLCP_IMPL` choice override | missed | **caught** | `BUILD-KCONFIG-OVERRIDE-ORPHANED-4440a398` (`Kconfig:175`) |
| 14 | `b2a0a357` | default LVGL memory size | missed | **caught** | `BUILD-KCONFIG-OVERRIDE-ORPHANED-c4fb7621`, `-8baa1c47` (`LV_Z_MEM_POOL_MIN/MAX_SIZE`, `src/display/Kconfig:32/35`) |
| 15 | `4bcecd98` | display memory Kconfig tweaks | missed | **caught** | same as #14 |
| 16 | `1d83f279` | `PM_DEVICE` default back on | missed | partly | `KCONFIG-NO-LONGER-SET-…` (info, bulk list) includes `CONFIG_PM` |
| 17 | `50a303b8` | re-implement device suspension, since `sys_poweroff` no longer does it | partly | partly | `GUIDE-ENTRY-MATCH-9754923b` names the API change; the consequence (devices no longer suspended) isn't stated |
| 18 | `0f49fa9a` | `ext_power` init priority | missed | missed | runtime init order |
| 19 | `104c73d3` | enum conversion warning in `src/endpoints.c` | missed | missed | the warning didn't appear in this build (it depends on configuration) |

**Totals, in-build:**

| | Caught | Partly caught | Missed | Caught or partly |
|---|---|---|---|---|
| Before | 2 | 4 | 13 | 32% |
| **After** | **8** | **6** | **5** | **74%** |

Caught alone went from 11% to 42%. Commit `cd6f8a68` (a BLE security test updated for 3.5)
corresponds to `BT_GATT_AUTO_SEC_REQ` changing default, which zmig lists only inside the bulk
info finding `KCONFIG-DEFAULT-CHANGED-52fc981b`.

### 2.2 Changes outside this build (11)

Most touch other keyboards' board files, which a `nice_nano_v2` build never sees. zmig scans
the whole app tree for references, so it still caught three of them:

- `802881b6` PINMUX references: `KCONFIG-READ-SYMBOL-REMOVED-3f9ca014`
- `d9c0ded4` `rand32.h`: `GUIDE-ENTRY-MATCH-14a743b0`
- `2d6c9f79` `spi_is_ready`: `GUIDE-ENTRY-MATCH-213c7c7b`

Missed or out of reach:

- **Init level or priority for other boards' drivers:** `552347b8`, `aeb5bed3`, `c082f8d8`.
- **Board-specific fixes:** `324ada1d`, `fda6a5f1`.
- **`west flash` runners:** `3968d9fe`.
- **A test driver include:** `f7e7c9c1`.
- **A warning in a file this build doesn't compile:** `58413ca8`.

---

## 3. Precision: zmig's breaking/risky findings vs. the source

Final corne run: 14 breaking and 7 risky. The niceview run adds 1 risky. Each was checked
against the source at both refs.

| Verdict | Count | Findings |
|---|---|---|
| **Confirmed hazard, in the oracle** | 16 | `KCONFIG-NAME-COLLISION-7c2cfcbc`; 5× `KCONFIG-OVERRIDE-ORPHANED`; `TARGET-BUILD-FAILED-f5607720`; `READ-SYMBOL-REMOVED-3f9ca014` (PINMUX); guide `b3668791` (SSD1306 via DT), `f6e35e8e` (GPREGRET), `9754923b` (`pm_state_force`), `14a743b0` (`rand32.h`), `213c7c7b` (`spi_is_ready`), `39073cd9` and `04e4cb9c` (init levels: right entry, imprecise evidence); niceview `NEW-WARNING-f768b8ba` (`sprintf` overflow) |
| **Confirmed hazard, not in the oracle** | 1 | `GUIDE-ENTRY-MATCH-fca177c5`. v3.5's guide says GPIO drivers without interrupt support must set the ops to `NULL`. ZMK's `module/drivers/gpio/gpio_max7318.c:283-295` still installs a stub that returns `-ENOTSUP`, unchanged at ZMK HEAD (2026). |
| **Harmless** | 4 | `9f30bf3e` (bq274xx capacity/power units ×1000: ZMK reads only `STATE_OF_CHARGE`, `src/battery.c:40-47`); `106b2d8e` and `3f730cc9` (`fcntl.h`/`sys/stat.h` deprecated in the minimal libc: only in the native BLE test harness `tests/ble/central`); `2c28c79f` (the guide's note that something does *not* apply, matched as an entry) |
| **Unverifiable** | 1 | `a7776724` (picolibc heap default). The libc really did switch from minimal to picolibc at v3.5 (`KCONFIG-DEFAULT-CHANGED`), but the cited line is a test-harness `#ifdef`. Whether ZMK's firmware allocates from the libc heap wasn't established. |

**Precision after the fixes: 17 of 22 confirmed (77%), 4 harmless, 1 unverifiable.** Before
the devicetree fixes in §5, the same run had **54** breaking/risky findings. 33 of them were
devicetree noise:

- 28 "binding changed" findings, from zmig's own per-build app copy path;
- 8 properties newly *added* by Zephyr's nRF SoC `.dtsi`, reported as changes.

That was 61% noise.

For the "before" column in §8: the first scan, with zmig as it was at the start (target stuck
at configure, SDK 0.17.4), had 7 breaking and 14 risky findings. 9 were confirmed hazards
(the generic configure failure, guide `b3668791`, `f6e35e8e`, `9754923b`, `fca177c5`,
`04e4cb9c`, `39073cd9`, `14a743b0`, `213c7c7b`): **43%**. The rest were 7 `easydma-maxcnt-bits`
additions, 4 harmless guide matches and 1 unverifiable.

---

## 4. Misses: what would have revealed them, and can a deterministic analyzer catch them?

| Miss | Revealing artifact | Deterministic? |
|---|---|---|
| `int main` (#6) | the guide entry's code literal `int main(void)` vs. the app's `void main(void)` definition | **yes**: match signature literals in guide entries against app definitions |
| `SYS_INIT` signature (#2, partly) | gcc's "in expansion of macro 'SYS_INIT'" notes, which carry the app `file:line` | **yes**: attribute warnings raised inside Zephyr headers to the app call site |
| syscall headers (#5, partly) | a `fatal error: syscalls/<x>.h` for an app header that declares `__syscall` | **yes**: name the cause (an app header with `__syscall` but no `zephyr_syscall_header()`); also the matching guide entry |
| init priorities (#18, `552347b8`, `aeb5bed3`, `c082f8d8`) | device init order in `zephyr.elf` (the `__init_*` sections); Zephyr ships `scripts/build/check_init_priorities.py` | **yes**: diff the init order between the two builds. This miss class is the biggest: 5 of ZMK's commits. |
| `PM`/`PM_DEVICE`, `BT_GATT_AUTO_SEC_REQ` (#16, `cd6f8a68`) | they are in the Kconfig diff, but only in bulk info lists | **yes**: rank Kconfig changes of symbols the app's *own Kconfig* selects, depends on or defaults as risky |
| `setting_name` length (#8) | runtime: setting key names grew | no, not without source-level size analysis |
| `CONTAINER_OF` (#10) | none at v3.5 (preventive hardening) | no |
| arc 359 → 360 (#11, partly) | LVGL behavior, runtime | no |
| enum warning (#19) | a configuration this build doesn't use | maybe: build more configurations |

---

## 5. What this run fixed (each with a regression test)

| Problem as seen | Fix | Test |
|---|---|---|
| ZMK's first configure failed as "could not confirm the toolchain", with no log, after a 56-minute first run | the app uses `find_package(Zephyr HINTS ../zephyr)`, and SDK pinning had disabled the package registry. zmig now passes `-DZephyr_DIR=<workspace>/zephyr/share/zephyr-package/cmake`, checks the SDK only after a successful configure, and names the log path | `test_dogfood_regressions.py::test_app_that_finds_zephyr_by_west_layout` (fixture `hints-app`) |
| "The target configure fails" with a log tail: no symbol, no file, no salvage | new diagnostics `kconfig-definition` and `kconfig-loop`; findings `kconfig-override-orphaned` and `kconfig-name-collision` (collision = defined by the app and newly by Zephyr, checked against the source build's snapshot); salvage comments out the app-owned Kconfig *block* | `test_dogfood_units.py` (real ZMK log excerpts) |
| ninja stopped at the first failed step, hiding `hci_err.h` | build with `-- -k 0` (recipe 12) | covered by the ZMK runs |
| "6 errors (step)" with no compiler message | the compiler-error pattern accepts `fatal error:` | `test_fatal_error_is_a_compiler_error` |
| 28 risky "binding changed" findings | compare binding paths with the app-copy prefix (`apps/<hash>/<app>/`) normalized | `test_app_binding_paths_compare_without_the_copy_dir` |
| 8 risky "node changed" findings for properties only the target has | new info kind `node-properties-added`; `node-changed` keeps properties that existed at the source | covered by the ZMK runs |
| `CONFIG_LV_USE_THEME_MONO` "changed from n to n" | unset and `n` compare equal for code that reads the symbol | — (one-line condition) |
| wire "clean", "11 entries not found" while the target had no `.config` | category `unknown` when an image pair is unconfigured | — |
| storage `unknown` because ZMS and secure-storage trees don't exist before v4.0/v4.1 | a backend tree missing at a ref, or a seed missing at both refs, isn't a gap | — |
| BLE key extraction gave up at v3.x (no `BT_SETTINGS_DEFINE`, no `bt_settings_store`) | a missing seed drops only that source of key names | — |
| absolute scratch paths in every title | `Ref.label` = `<dir>@<sha12>` for local checkouts | — |
| `plan` said nothing about the refs being forks, and suggested upstream `v3.4.0` as a hop | `hops` reports `ref-is-fork` (nearest tag + commits no upstream ref contains: 34 and 21, matching an independent count) and caveats the hop | `test_fork_detection_counts_commits_no_upstream_ref_contains` |
| smp_svr: 4 identical `app-bootloader-mismatch` findings for the firmware-updater pair (`CONFIG_REBOOT`) | only pairs whose sysbuild trigger is `y` at the target apply, one per trigger | `test_dogfood_regressions.py::test_smp_svr_p1_verdict` |
| smp_svr: FUSE/virtiofs symbols `None → 0` as "storage parameters changed", with a config pin | parameters must exist at both refs | same test |
| smp_svr: `needs-changes` read as "these changes suffice" while storage was unchecked | `upgrade_path.unchecked`; the verdict text and basis call the remedies a lower bound | `test_verdict_with_unchecked_categories_is_a_lower_bound` |
| salvage crashed on a diagnostic naming a missing file | guard | `test_salvage_comments_out_app_kconfig_blocks` |

---

## 6. Noise sources, confusing errors, timing

### Noise sources (largest first)
1. **zmig's own per-build app copy path leaking into comparisons** (28 findings). Fixed. The
   same class appeared before (P1: the per-ref signing-key path).
2. **Upstream additions reported as changes** (8). Fixed.
3. **Evidence from files the build never uses.** ZMK is a monorepo of about 100 keyboards, and
   usage is scanned over the whole app tree. That helped recall (3 out-of-build catches), but
   evidence lists are dominated by unbuilt boards. For example, the `SSD1306_REVERSE_MODE`
   finding lists 44 locations, and salvage commented out 102 lines across 26 shields. Open.
4. **A test harness inside the app tree** (`tests/ble/central`) produced 3 harmless guide
   matches. Open.
5. **The SDK choice.** With 0.17.4, Zephyr 3.5 failed in `libc-hooks.c` (picolibc lock types,
   fixed upstream in v4.2), an error ZMK's maintainers never saw. One SDK has to serve both
   refs. 0.16.8 builds both (the refs need ≥0.15 and ≥0.16).

### Confusing errors
- **"could not confirm the toolchain"** instead of the configure error, with no log path. Fixed.
- **"The target configure fails and zmig can't compare builds"** plus 12 log lines. Fixed for
  Kconfig definition errors; still the fallback for anything unparsed.
- **smp_svr without `--module littlefs`:** "build-failed" at both refs with
  `fatal error: lfs.h`, and no hint that a module is missing. Open: zmig could name the module
  from `CONFIG_FILE_SYSTEM_LITTLEFS=y` and the manifest.
- **`plan`'s forks analyzer is "skipped (no --workspace)"** even when the refs are forks.
  Fork detection now works without a workspace, but the forks analyzer itself still needs one.

### Timing (wall clock)
| Run | Time |
|---|---|
| first ZMK scan (fork refs; cloning modules at the fork's pins, then failing) | 56 min 12 s |
| `zmig plan` | 1.1 s |
| ZMK scan, both refs built (salvage rounds included) | 40–54 s |
| ZMK scan, cached builds | 6.5–6.7 s |
| smp_svr scan, sysbuild + MCUboot, both refs | 2 min 25 s |

The first run is network-bound and silent for most of that hour. It needs progress output.

### Verdict honesty
- **ZMK: `unknown` (medium).** Layout clean (no bootloader, same partitions); MCUboot and
  sysbuild not-applicable; storage and BLE `unknown` because the target build failed (no SBOM
  at the target); wire clean (9 of 12 entries checked; 3 not defined at these refs). Nothing
  overstated.
- **smp_svr: `needs-changes` (medium).**
  - **The hazard:** `MCUBOOT-APP-BOOTLOADER-MISMATCH-1f62e338`. The app is built for
    swap-using-offset while the deployed bootloader runs swap-using-move. 8 compiled app source
    lines (the image manager behind MCUmgr DFU) depend on it.
  - **The remedy:** `config-pin` to `SB_CONFIG_MCUBOOT_MODE_SWAP_USING_MOVE`, which exists at
    v4.3.0. That matches the migration guide.
  - **What's unchecked:** storage stays `unknown`, because littlefs mounts through a
    devicetree `fstab`, which zmig's partition mapping doesn't read. The verdict now says the
    remedies are a lower bound.
  - **Before the fixes**, the verdict had 4 spurious `unknown` remedies and a spurious storage
    config pin.

### Known limitations: which ones mattered
| Limitation (from the P1 "not checked" list or earlier docs) | Mattered here? |
|---|---|
| salvage only edits conf/overlay lines | **most of all**: it blocked all analysis for ZMK until salvage learned Kconfig blocks |
| fork refs / application manifests (refs resolved against upstream; module pins only from Zephyr's manifest) | **yes**: needed manual checkouts; pins happened to match for v3.x but wouldn't at ZMK's v4.1 (ZMK pins its own `lvgl` fork) |
| one SDK per run | **yes**: needed a second SDK |
| `#if` conditions not evaluated (srcdiff) | no |
| MCUboot / layout / BLE seeds | no for ZMK (no MCUboot; the target didn't build for BLE); MCUboot yes for smp_svr, and it worked |
| wire allowlist incomplete | marginal: `BT_GATT_AUTO_SEC_REQ` is peer-visible and not in the allowlist |
| storage partitions via devicetree (`fstab`) | yes for smp_svr (storage unknown) |

---

## 7. Remaining issues, ranked by how much they hurt usability

1. **No init-order analysis.** It's the largest miss class: 5 ZMK commits. It's deterministic
   (diff the device init order of the two builds; Zephyr ships `check_init_priorities.py`).
2. **Fork and application-manifest projects need manual setup.**
   - **Today:** the fork's refs must be checked out by hand, and module pins come from
     Zephyr's manifest, not the project's.
   - **Proposed:** accept `--from <url>@<branch>[@date]`, and read module pins from the
     project's own `west.yml`.
3. **Kconfig changes that matter are buried in bulk info lists** (`PM`,
   `BT_GATT_AUTO_SEC_REQ`). Rank changes of symbols the app's own Kconfig references as risky.
4. **Errors and warnings land in Zephyr headers instead of app call sites** (`SYS_INIT`,
   syscalls). Use gcc's macro-expansion notes.
5. **Guide matching:**
   - it misses entries whose key is a code signature (`int main`);
   - it matches literals in code that already complies (`POST_KERNEL`).
6. **Evidence from unbuilt files dominates monorepo reports.** Show files the build compiled
   first; collapse the rest.
7. **A missing module isn't named** (smp_svr's `lfs.h`).
8. **Storage partition mapping ignores devicetree `fstab`.**
9. **The first run is silent and network-bound** (56 minutes).

## 8. Recommendation

The measured numbers:

| | Before this run | After |
|---|---|---|
| In-build recall, caught | 11% | 42% |
| In-build recall, caught or partly | 32% | 74% |
| Precision | 43% | 77% |

Most of the gain came from fixing how zmig handles an app whose target doesn't configure.
That is the normal state of a real upgrade, and the fixtures never exercised it.

What should come next is **fixes 1, 3 and 4 above**. Each is deterministic, each addresses a
measured miss class, and together they would move ZMK's five remaining in-build misses and
four partial catches. Fix 2 matters for adoption, since ZMK-style forks are common, but it
doesn't change recall.

I don't have the definitions of P2–P4 in this session's notes, so I can't map these to them.
If one of those phases is init-order or fork/manifest support, it should go first. Whatever
comes next should be dogfooded again before going further, preferably on a second project
with a different shape (a vendor SDK fork, or a single-board product).
