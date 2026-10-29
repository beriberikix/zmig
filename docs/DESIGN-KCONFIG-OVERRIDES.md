# Design note: app Kconfig overrides of removed symbols

Status: design note only. `zmig fix kconfig` reports these as **needs a human** and never
edits them (2026-10-29).

## What they are

An app can re-declare a Zephyr symbol in its own Kconfig files, usually to change its
default:

```
# app/boards/shields/corne/Kconfig.defconfig:34
config LV_Z_DPI
    default 77
```

When Zephyr removes the symbol, the app's block becomes the only definition, and it has no
type. Kconfig warns "LV_Z_DPI (defined at …) defined without a type", and Zephyr aborts
the configure.

zmig already sees these from the target build's own Kconfig warning:

- the scan reports them as `kconfig-override-orphaned`;
- salvage comments them out in its build copy, to get further;
- `zmig fix kconfig` lists every location the warning names, mapped back to the app.

## ZMK's five (v3.2 → v3.5; tuned project), and what the maintainers did

| Symbol | Locations (from the warning) | Maintainers' fix | A Kconfig-level replacement? |
|---|---|---|---|
| `LV_Z_DPI` | 21 `Kconfig.defconfig` files | `f4fce9e1`: moved the defaults to LVGL's `LV_DPI_DEF` | no: LVGL's own Kconfig defines `LV_DPI_DEF`; nothing links the two symbols |
| `SSD1306_REVERSE_MODE` | 22 `Kconfig.defconfig` files | `f4fce9e1`: replaced by the devicetree property `inversion-on` | no: the replacement isn't in Kconfig at all |
| `BT_LL_SW_LLCP_IMPL` (a choice) | `app/Kconfig:175` | `a6c03994`: dropped the override | no: the choice was removed |
| `LV_Z_MEM_POOL_MIN_SIZE`, `LV_Z_MEM_POOL_MAX_SIZE` | `src/display/Kconfig:32, 35` | `b2a0a357`: replaced both by `LV_Z_MEM_POOL_SIZE` | no Kconfig link; two values became one, so the meaning changed |

`MAX17048` is also reported (`module/drivers/sensor/max17048/Kconfig:6`). It isn't a
removed symbol but a name collision: Zephyr added a symbol with the app's name. It's out
of this note's scope.

## Could they ever be auto-fixed safely?

There are three candidate edits.

1. **Rename the block to the replacement.** This is the only edit that keeps the
   override's intent. It would be safe only if all of the following hold, and each can be
   checked from the build:
   - **Kconfig evidence** of the replacement, the same as for assignments: in the source
     tree, the old symbol was deprecated and selected the new one, or one was `default`
     the other. Guide text alone is not enough.
   - **The block only sets defaults.** No type, prompt, `select`, `depends on` or help.
     This is read from kconfiglib's menu nodes for that file and line (`MenuNode.filename`,
     `linenr`), not by parsing text.
   - **The replacement is Zephyr's or a module's symbol**, with the old symbol's type, and
     the app doesn't already override it.
   - **No guide entry about either symbol mentions a value or unit change.**
   - **The value is preserved:** with the renamed block merged into the target's Kconfig
     (`kconfig_try`, as for assignments), the replacement takes, in each scanned build,
     the value the old override gave the old symbol at the source.
   - The edit, the rebuild, and the revealed/introduced comparison work as for assignments.
2. **Delete or comment out the block.** It removes the configure failure, but the default
   it set is lost silently. For ZMK, `LV_Z_DPI 77` would become LVGL's own default. **Never
   automatic.**
3. **Move the default to a non-Kconfig replacement** (devicetree, an API). Out of scope:
   no codemods outside Kconfig.

**On ZMK, none of the five would qualify for (1).** None has a Kconfig-level link to its
replacement, and the memory-pool pair changed meaning. So even a careful implementation
of (1) would leave all five for a human, as now.

**Recommendation.** Keep them needs-human. Implement (1) only when a measured project
shows a case that passes every check above, and add that case as a regression test first.
