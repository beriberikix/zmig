# P3: `zmig fix kconfig`, results (2026-10-29)

Scope as approved: renames of Kconfig assignments in the project's own fragments, applied
only with Kconfig-level evidence. No codemods for includes, boards, devicetree or APIs.
The rules are in `zmig/fixkconfig.py`'s docstring.

## Tests

| Test | What it shows | Where |
|---|---|---|
| Conf parsing and rewriting | values, quoted `#`, `# CONFIG_X is not set`, CRLF, no final newline, a line that changed since it was read is left alone | `tests/test_fixkconfig.py` |
| Rules | deprecation select → rename; `default OLD` alias → rename; a folded menu condition (`y if CAN`) is **not** an alias; type mismatch, no prompt, unresolved dependencies, failed re-resolution and a guide value/unit change each block it; guide-only renames need a human | ″ |
| `CONFIG_DEBUG_INFO` on ARM (negative) | v4.2 renamed it to the x86-only `CONFIG_X86_DEBUG_INFO`, which isn't in an ARM build's tree: needs a human, and no edit is planned. With the symbol in the tree, Kconfig still refuses it (`depends on X86`) | ″ |
| Still defined, dependencies unmet | reported as `deps-unmet`, never edited | ″ |
| Conflicts | a file that already assigns the new symbol, or images that disagree about a line: not edited | ″ |
| Dirty tree | `--apply` exits 2 before any scan; the file is untouched | ″ |
| Fixture end to end (`-m build`) | on a git copy of `tests/fixtures/app`: one commit, `CONFIG_CPLUSPLUS` → `CONFIG_CPP` (v3.6.0 `lib/cpp/Kconfig.deprecated:6`: deprecated, selects CPP); the guide-only, inverted `CAN_AUTO_BUS_OFF_RECOVERY` → `CAN_MANUAL_RECOVERY_MODE` is reported, not edited; rebuild: 3 findings gone, 0 revealed, 0 introduced; a second run makes no commit | `tests/test_fix_fixture.py` |

## Real oracle: ZMK (a **tuned** project; this tests the fixer, not recall)

**Setup.**

- ZMK pre-bump tree `b8846cf6`, in a scratch worktree.
- Zephyr fork `1ae0eb5` (v3.2 + ZMK) → `339570e` (v3.5 + ZMK).
- SDK 0.16.8, scratch `~/.cache/zmig/dogfood/d9`.
- Two builds:
  - corne + nice!view on `nice_nano_v2`, the DOGFOOD-1 configuration;
  - `planck_rev6`, whose defconfig the maintainers edited.

**What the fixer checked.**

| Build | Assignments / files | Result |
|---|---|---|
| corne + nice!view | 19 in 3 (`nice_nano_v2_defconfig`, `nice_view.conf`, `nice_view_adapter.conf`; `prj.conf` and the corne confs have no active assignments) | nothing to fix: every assignment is defined at the target |
| planck_rev6 | 8 in 1 (`planck_rev6_defconfig`) | **needs a human:** `planck_rev6_defconfig:10 CONFIG_PINMUX=y`; not defined at the target (v3.2 had `menuconfig PINMUX` in `drivers/pinmux/Kconfig:9`); no replacement evidenced. Not edited |

**Against the maintainers' commits** that touched these builds' config files in the bump:

| Commit | Change | Fixer |
|---|---|---|
| `802881b6` | delete `CONFIG_PINMUX=y` from `planck_rev6_defconfig` (and `dz60rgb`) | same line flagged, no replacement claimed; deleting is left to a human, per the approved scope |
| `d06e90e7` | add `CONFIG_APPLICATION_DEFINED_SYSCALL=y` to `prj.conf` | out of scope (an addition, not a rename) |
| `f4fce9e1` | `LV_Z_DPI` → `LV_DPI_DEF` in shields' `Kconfig.defconfig` | out of scope (a Kconfig file, not an assignment) |

- **Edits compared:** the maintainers made 0 renames in these files, and the fixer made 0
  edits. Nothing wrong was written. The one removed-symbol line the maintainers fixed is
  reported at the exact line.
- **Does the target configure further?** No. With no edits applied, both targets are as
  before: configure failed, then the salvaged build failed. That's expected from ZMK's
  failures: a Kconfig name collision, `hci_err.h`, and syscall headers (DOGFOOD-1), none of
  which a rename fixes.

**What this says.** On these two builds the implemented fixer was safe but didn't help.
The conf-level changes this bump needed were deletions and an addition, not renames.

### Correction (2026-10-29): the "remove with no replacement" category

**It was never implemented.**

- My P3 proposal had two categories: "rename", and "remove with no replacement: comment
  out with a note". The approval changed the rename rules but didn't drop the second
  category, so it was in the approved scope.
- The implementation has no remove category. A removed symbol with no evidenced
  replacement is reported as **needs-human** and not edited (`classify` in
  `zmig/fixkconfig.py`; `plan_edits` only takes `rename`).
- I read "auto-apply only with Kconfig-level evidence" as covering every edit. That was my
  interpretation, not something the review said, and I didn't flag the deviation. It
  stayed unimplemented that round ("no new fixers"), and was implemented in the next one
  (below).

**Evidence: what the existing runs did with PINMUX and the other removed-symbol
overrides.**

- **`planck_rev6_defconfig:10 CONFIG_PINMUX=y`** is the only removed-symbol *assignment*
  in either scanned build. It was dry-run output only: `out-planck-dry/fix.json` has
  category `needs-human`, with reasons "not defined in the … Kconfig tree" and "no
  replacement is evidenced". No `--apply` was run on ZMK, and no file was edited.
- **The scan itself never reported that line.** Its only removed-symbol finding is
  `read-symbol-removed CONFIG_PINMUX`, citing other boards' `CMakeLists.txt:2`. The scan's
  usage index reads `*.conf` files, not `*_defconfig`. The fixer found the line only after
  base defconfigs were added to its fragments.
- **The other removed-symbol overrides are in Kconfig files, not assignments**, so neither
  category covers them. The scan's salvage commented them out in its own build copy
  (`before/report.json`, 103 salvage edits for planck):
  - `config SSD1306_REVERSE_MODE` (44 lines) and `config LV_Z_DPI` (42 lines) in the
    shields' `Kconfig.defconfig`;
  - `MAX17048` (8 lines);
  - `choice BT_LL_SW_LLCP_IMPL` in `app/Kconfig` (4 lines);
  - `LV_Z_MEM_POOL_MIN/MAX_SIZE` (2 + 2 lines).

### Dry-run diff for every removed-symbol assignment, against the maintainers' commits

This is a static sweep, not a build:

- every assignment in ZMK's 166 `*.conf` and `*_defconfig` files at `b8846cf6`;
- checked against the four existing builds' Kconfig symbol dumps (source and salvaged
  target, corne and planck);
- "removed" means in a source tree and in neither target tree;
- the diff is what the unimplemented comment-out rule would print;
- script: `$CLAUDE_JOB_DIR/tmp/p3/sweep.py`, reading zmig's `parse_conf` and
  `kconfig_replacements`; no file was edited.

Result: 954 assignments, **2 removed**, both with no evidenced replacement.

```diff
--- a/app/boards/arm/dz60rgb/dz60rgb_rev1_defconfig
+++ b/app/boards/arm/dz60rgb/dz60rgb_rev1_defconfig
@@ -8,3 +8,4 @@
 # enable pinmux
-CONFIG_PINMUX=y
+# zmig: CONFIG_PINMUX no longer exists at the target; no replacement evidenced
+# CONFIG_PINMUX=y

--- a/app/boards/arm/planck/planck_rev6_defconfig
+++ b/app/boards/arm/planck/planck_rev6_defconfig
@@ -9,3 +9,4 @@
 # enable pinmux
-CONFIG_PINMUX=y
+# zmig: CONFIG_PINMUX no longer exists at the target; no replacement evidenced
+# CONFIG_PINMUX=y
 CONFIG_PINCTRL=y
```

37 symbols are in neither scanned tree and weren't judged:

- 26 `BOARD_*` symbols, defined only when that board is built;
- 7 `CLOCK_STM32_*` in `ferris_rev02_defconfig`;
- 4 `CUSTOM_WIDGET_*` symbols of one shield.

The maintainers changed none of them in the bump.

**The maintainers' conf and defconfig changes in the series** (`b8846cf6..f72f415c`, read
with `GIT_NO_LAZY_FETCH=1`; `40adb385` later drops `CONFIG_ZMK_BLE=n`, a symbol that still
exists):

| Line | Maintainers | Comment-out rule (swept) | Implemented fixer (scanned builds) |
|---|---|---|---|
| `dz60rgb_rev1_defconfig:9` `CONFIG_PINMUX=y` | **deleted**, with its `# enable pinmux` comment and the blank line (`802881b6`) | comments it out and adds a note; leaves the old comment | not seen: dz60rgb wasn't a scanned build |
| `planck_rev6_defconfig:10` `CONFIG_PINMUX=y` | **deleted**, with comment and blank line (`802881b6`) | same as above | reported needs-human; **not edited** |
| `ferris_rev02_defconfig` `# enable PINMUX` → `# enable PINCTRL` | comment edited | not touched (not an assignment) | not touched |
| `corneish_zen_v{1,2}_{left,right}_defconfig` stack size 2048 → 4096 | value changed (4 lines) | not touched (symbol still exists) | not touched (not scanned) |
| `prj.conf` `+CONFIG_APPLICATION_DEFINED_SYSCALL=y` | added | not touched | not touched |

- **Deletions that match:** both removed-symbol assignment lines the maintainers deleted
  are exactly the two the sweep finds. That's 2 of 2, with no extra lines.
- **Lines the maintainers deleted that P3 didn't touch:** the two `# enable pinmux`
  comments and blank lines. The implemented fixer also left planck's assignment itself
  (needs-human).
- **Lines P3 touched that the maintainers didn't:** none.

**Was "edited nothing, correctly" checked?** **No, it was an assessment.**

- In the previous round I compared the fixer's output only with a `git log` of the
  maintainers' commits to the files the fixer had checked. I didn't compare it line by
  line, and I didn't sweep all removed-symbol assignments.
- "Correctly" also hid that the approved remove category wasn't implemented. Under that
  category, the planck line should have been a comment-out edit, not needs-human.
- The line-by-line comparison above is the check. It agrees with the maintainers on which
  lines are removed-symbol assignments (2/2). It differs on the edit: they deleted the
  lines, the rule would comment them out, and the implemented fixer didn't edit at all.

ZMK is a **tuned** project (the development project). This tests the fixer, not recall.

## Scan usage index from the build's own fragment list (generic post-baseline fix)

**The problem.** The scan's Kconfig usage index (`ScanContext.usage`) read assignments
only from files named `*.conf`, so ZMK's `CONFIG_PINMUX=y` in `planck_rev6_defconfig:10`
was never reported. Commit `16403a6` fixed this only for `zmig fix kconfig` (its own
fragment list), not for the scan.

**The fix.** The index now reads assignments from the fragments each image's configure
log says Kconfig loaded or merged (`ScanContext.config_files`):

- board defconfigs, board and shield `.conf` files;
- `EXTRA_CONF_FILE` and `OVERLAY_CONFIG` fragments, also outside the app;
- per-image sysbuild files.

App copies map back to the app. Zephyr's, modules' and generated files are dropped. The
sysbuild level (`sysbuild.conf`, `SB_CONF_FILE`) comes from the sysbuild segment's log
(`sysbuild_config_files`, used by the sysbuild analyzer).

`*.conf` names are only the fallback, when a configure never reached Kconfig. As a side
effect, an app `.conf` the build didn't read is no longer indexed. `zmig fix kconfig` now
uses the same lists. That also fixes its old rule, which dropped every path under zmig's
cache root, including a project that lives there.

**Regression test.** `tests/test_config_index.py`, with ZMK's real `planck_rev6_defconfig`
from `b8846cf6` (`tests/fixtures/zmk-defconfig`).

**Re-run on the cached ZMK planck configuration** (tuned project). The run used
`GIT_ALLOW_PROTOCOL=file`, so git could not use the network, and took 32 s with exit 0.
Comparing reports by finding id, 59 → 59 findings, one changed:

| Before | After |
|---|---|
| breaking `read-symbol-removed` CONFIG_PINMUX, citing `boards/arm/{mikoto,nrfmicro,puchi_ble}/CMakeLists.txt:2` (other boards, not in this build) | breaking `symbol-removed` "CONFIG_PINMUX, set in boards/arm/planck/planck_rev6_defconfig, no longer exists", at `planck_rev6_defconfig:10`, the line the maintainers deleted |

## Remove with no replacement (implemented, as approved)

**Rule** (`classify` in `zmig/fixkconfig.py`). A removed-symbol assignment is in the
`remove` category when all of these hold:

- the symbol is absent from the target's Kconfig tree of the image that reads it;
- no Kconfig-level replacement is evidenced in the source tree;
- the migration guide names no rename for it;
- **and no migration-guide entry mentions it at all.**

**Narrowing, flagged when it was made:** the last condition goes beyond the approval. A
guide entry can name a replacement outside Kconfig: ZMK's `SSD1306_REVERSE_MODE` became
the devicetree property `inversion-on`. In that case "no replacement evidenced" would be
false, and commenting the line out would drop behaviour silently. Such lines stay
**needs-human**.

**Edit** (`comment_out`):

- the line is kept whole after `# `, including its own trailing comment;
- the note `# zmig: CONFIG_X removed in <target ref>: no replacement evidenced` goes above
  it;
- comments above the line are kept;
- `# CONFIG_X is not set` becomes `# # CONFIG_X is not set`, so Kconfig no longer reads it;
- a second run finds nothing to do.

**Safety:**

- the remove category is its own commit: `zmig fix kconfig: remove (no replacement
  evidenced)`;
- dry run by default; `--apply` refuses a dirty tree and works on a new branch;
- after `--apply`, the target is rebuilt and the analyzers rerun, and findings are split
  into revealed and introduced. A new finding counts as introduced if it cites an edited
  line, is about a written symbol, or appears when nothing was hidden before.

**Tests:**

- `tests/test_fixkconfig.py`: the rule, the guide-mention case, comment-out (comments,
  values, CRLF, `is not set`, idempotence) and the separate edit kind;
- `tests/test_fix_apply.py`: `--apply` with the scan stubbed out, so no build is needed.
  It covers the branch, the commit and its message, the committed file, revealed versus
  introduced, exit 3 only for introduced, and that a dry run edits nothing;
- `tests/test_fix_fixture.py` (`-m build`, cached): unchanged, still passes.

**ZMK re-run** (tuned project), on the cached planck configuration with
`GIT_ALLOW_PROTOCOL=file`: exit 0, 34 s, dry run. The scratch tree is unchanged
(`b8846cf6`, clean).

```
Fix: remove (no replacement evidenced): comment out with a note [1]
  app/boards/arm/planck/planck_rev6_defconfig:10  CONFIG_PINMUX=y
      CONFIG_PINMUX is not defined in the tgt@339570e2393b Kconfig tree of app
      no replacement is evidenced in the src@1ae0eb5ce8ad Kconfig tree
      no migration-guide entry mentions it
```

**Next to the maintainers' `802881b6`.** The P3 side covers all of ZMK's fragments: the
static sweep, with zmig's real `comment_out` and note. dz60rgb wasn't a scanned build.
PINMUX's guide check is from the planck run, and it's the same symbol.

| File | P3 dry-run diff | Maintainers (`802881b6`) |
|---|---|---|
| `dz60rgb_rev1_defconfig:9` | `-CONFIG_PINMUX=y`<br>`+# zmig: CONFIG_PINMUX removed in tgt@339570e2393b: no replacement evidenced`<br>`+# CONFIG_PINMUX=y`<br>(keeps `# enable pinmux`) | `-# enable pinmux`<br>`-CONFIG_PINMUX=y`<br>`-` (blank line) |
| `planck_rev6_defconfig:10` | the same | `-` (blank line)<br>`-# enable pinmux`<br>`-CONFIG_PINMUX=y` |
| `ferris_rev02_defconfig` | nothing (no assignment of a removed symbol) | `# enable PINMUX` → `# enable PINCTRL` (comment only) |

- **Same lines:** both removed-symbol assignments the maintainers removed, and no others.
- **Style difference:** the maintainers deleted each line with its comment and a blank
  line. P3 comments the line out, adds its note, and keeps the comment.
- **The maintainers changed one line P3 doesn't touch:** ferris's comment.

### The 37 symbols the sweep didn't judge, and why

The sweep can only judge a symbol that is in one of the Kconfig trees it has: the four
existing builds, corne and planck at both refs. These 37 are in neither source tree nor
target tree:

- **26 `BOARD_*` symbols** (`BOARD_ADV360PRO_LEFT`, `BOARD_GLOVE80_LH`, …). Each is
  defined only when its own board is built; a nice_nano or planck build doesn't define
  them.
- **7 `CLOCK_STM32_*` symbols** in `ferris_rev02_defconfig` (an STM32F0 board). They exist
  only in an STM32F0 board's tree, or are gone already at v3.2. Neither scanned build can
  tell which.
- **4 `CUSTOM_WIDGET_*` symbols** of one shield that wasn't built.

Judging them would need a build of each of those boards at both refs. That wasn't done:
no network work this session, and those builds aren't cached. The maintainers changed
none of the 37 in this bump.

## Kconfig-file overrides of removed symbols (needs a human, never edited)

`zmig fix kconfig` lists them from the scan's `kconfig-override-orphaned` findings. Their
locations are taken from the target build's own Kconfig warning ("defined at …"), not
from the sources, and mapped back to the app. Re-run on the cached ZMK planck
configuration (tuned project), network off:

```
Needs a human: Kconfig-file overrides of symbols the target removed [5 symbol(s), 46 location(s)] (never edited)
  CONFIG_BT_LL_SW_LLCP_IMPL: 1 location(s)       Kconfig:175
  CONFIG_SSD1306_REVERSE_MODE: 22 location(s)    boards/shields/corne/Kconfig.defconfig:24, …
  CONFIG_LV_Z_DPI: 21 location(s)                boards/shields/corne/Kconfig.defconfig:34, …
  CONFIG_LV_Z_MEM_POOL_MIN_SIZE: 1 location(s)   src/display/Kconfig:32
  CONFIG_LV_Z_MEM_POOL_MAX_SIZE: 1 location(s)   src/display/Kconfig:35
```

**A bug found on the way, and fixed.** The warning came from a target build reused from
an earlier run, which was built from another copy of the app (`apps/c81d…/app`).
Locations were mapped only against this run's copies, so the first run printed absolute
cache paths.

- `scan.from_copy` now maps any `<workspace>/apps/<tree hash>/<app name>/` path back to
  the app.
- The same rule fixes `ScanContext.config_files`, which would otherwise drop fragments a
  reused build's log names.
- There's a regression test for each: `tests/test_fix_apply.py` and
  `tests/test_config_index.py`.

Whether they could ever be auto-fixed: [DESIGN-KCONFIG-OVERRIDES.md](DESIGN-KCONFIG-OVERRIDES.md).

