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

**What this says.** On ZMK the fixer is safe but not useful. The conf-level changes this
bump needed were a deletion and an addition, not renames. The only auto-applied rename so
far is the fixture's `CPLUSPLUS` → `CPP`.
