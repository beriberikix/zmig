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
  interpretation, not something the review said, and I didn't flag the deviation. Per
  this round's instruction ("no new fixers"), it stays unimplemented.

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
