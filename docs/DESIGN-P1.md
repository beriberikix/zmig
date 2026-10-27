# zmig P1 design: field-upgrade hazard analyzer

Status: approved with amendments (2026-10-27 review). Amended: §2.4, §5.3, §5.5, §5.6, §5.7, §8, §9, §10, §11, §13. Nothing is implemented yet.

**Goal:** answer one question with evidence. Can a device that runs the `--from` build
today receive the `--to` build over the air and keep working? If not, what has to happen
in between? "Safe" has to be earned category by category. A category that wasn't
positively checked is **unknown**, and an unknown category makes the whole verdict
unknown.

P1 extends the existing model rather than redesigning it: `ImageContext`/`BuildContext`
(docs/DESIGN.md §4.3a), the analyzer protocol, `Finding`, `zmig/sysbuild_rules.py`, and
the `images` and `crossimage` analyzers. Nothing blocks the work.

---

## 1. Ground facts this design rests on

Each fact was read from the source at the named ref (mirror of zephyrproject-rtos/zephyr,
plus the MCUboot clone at its west pin). The implementation re-derives them at run time;
nothing below is hard-coded unless §10 says so.

| Fact | Where (ref:file:line) |
|---|---|
| Tag SHAs the tests will pin | v4.0.0 `8469084d`, v4.1.0 `7823374e`, v4.2.2 `dbb53632`, v4.3.0 `3568e1b6`, v4.4.0 `684c9e8f`, v4.5.0-rc3 `76280bce` |
| MCUboot mode default changes to swap-using-offset, except STM32/ESP32 | v4.3.0:`share/sysbuild/images/bootloader/Kconfig:36-37` (upstream commit 7a76a62); v4.2.2 same file `:35` (swap-using-move for all) |
| …documented, with the slot-size note | v4.3.0 `doc/releases/migration-guide-4.3.rst:485-492` |
| nucleo_h753zi layout reordered | v4.2.2 vs v4.3.0 `boards/st/nucleo_h753zi/nucleo_h753zi.dts`: slot0 0x40000→0x20000, slot1 0x80000→0x60000, storage 0x20000→0xa0000, scratch (0xc0000) removed |
| …documented | `migration-guide-4.3.rst:71-73` |
| `SB_CONFIG_MCUBOOT_MODE_SWAP_WITHOUT_SCRATCH` deprecated | v4.1.0:`share/sysbuild/images/bootloader/Kconfig:61-65` (`select DEPRECATED`; help: "use MCUBOOT_MODE_SWAP_USING_MOVE"); documented `migration-guide-4.1.rst:31-33` |
| ITS settings store stops enabling NVS | v4.0.0:`subsys/secure_storage/Kconfig.its_store:13-15` (`imply FLASH_MAP`, `imply NVS`, `select SETTINGS`) → v4.1.0 `:27-34` (`depends on SETTINGS`, no imply); documented `migration-guide-4.1.rst:725-729` |
| Storage backend sources | v4.0.0–v4.3.0: `subsys/settings/src/settings_nvs.c`, `settings_zms.c` (from v4.2), `subsys/fs/nvs/nvs_priv.h`, `subsys/fs/zms/zms_priv.h` |
| BLE persistent state | `subsys/bluetooth/host/{settings.c,settings.h,keys.c,gatt.c,classic/keys_br.c}` (files that register settings handlers, found by grepping for handler macros) |
| MCUboot image format | MCUboot `boot/bootutil/include/bootutil/image.h` (`IMAGE_MAGIC`, `struct image_header`, `IMAGE_TLV_INFO_MAGIC`); trailer and boot-state headers under `boot/bootutil/include/bootutil/` |
| How the app was signed | the app image's `build.ninja` holds the exact `imgtool.py sign` command, e.g. at v4.5.0-rc3 xapp: `--header-size 0x200 --slot-size 483328 --align 4 --key …/root-rsa-2048.pem` |

---

## 2. Prerequisites, inside P1

### 2.1 Sysbuild-level config diff (`sysbuild` analyzer, build-wide)

**Inputs, at each ref:**
- the user's `sysbuild.conf` (and `sysbuild/*.conf`), read as text with file and line;
- sysbuild's **resolved** config (`<build>/zephyr/.config` at v4.4; the path is verified per ref and recorded);
- sysbuild's Kconfig metadata, extracted with that ref's kconfiglib using the environment from the top-level `build.ninja` (the same method as images; the target name is verified per ref).

**Output findings:**
- `sb-option-changed`: an `SB_CONFIG_*` whose resolved value differs.
- `sb-option-renamed`: a symbol that is deprecated or gone, and the replacement it points at. The rename itself is only medium confidence, from help text or the migration guide.
- `sb-option-default-changed`: the user didn't set it, and the resolved value differs.

**Propagation:** for each changed `SB_CONFIG_*`, the directives `zmig.sysbuild_rules` derives at both refs (which image option sysbuild sets, with file and line), next to the actual value of that option in each image's resolved `.config`. Where a rule names no image option, the finding says "propagation not derived".

**Behavior vs. option.** A changed or renamed `SB_CONFIG` option is an *option change*. Whether *behavior* changed is decided only from the resolved image `.config`s: the bootloader's mode, signature, and encryption options, and the app's `MCUBOOT_BOOTLOADER_MODE_*`. If they are identical at both refs, the finding says "renamed, behavior unchanged" with both `.config`s as evidence. That's fixture C's oracle. The rename's help text alone is never proof.

### 2.2 Image identity (`images` analyzer, extended)

- Images are matched across refs by **role** first (bootloader to bootloader, app to app), then by name.
- **One hazard finding** replaces the removed+added pair: `bootloader-identity-changed`, with kind `added`, `removed`, or `renamed`. Its evidence is both refs' `domains.yaml` and the role evidence.
- A bootloader appearing or disappearing is breaking for field devices, and the verdict category "mcuboot" becomes unknown or hazard (§8).

### 2.3 `introduced_by_migration` (schema 1.2)

- **Finding field:** `introduced_by_migration: bool`. It's optional in the schema, so 1.1 consumers keep working, and 1.2 producers always emit it.
- **Diff findings** (something changed between refs) are `true` by construction.
- **Condition findings** (a check that can be evaluated on a single build: cross-image consistency, slot-size requirements, partition overlaps) are evaluated on **both** builds. Present in the target and absent in the source means `true`. Present in both and **unchanged** means `false`, and the title says "pre-existing". Present in both but different (e.g. a mismatch that grew) means `true`, with both states in old/new.
- **Severity is not lowered** for pre-existing problems, as you specified.
- **Reports:** Markdown separates "Introduced by this migration" from "Pre-existing"; top-5 prefers introduced.

### 2.4 Provenance and remedy on every hazard finding

New optional `provenance` object (schema 1.2):

```json
"provenance": {
  "evidence_source": "resolved .config of image mcuboot @ v4.3.0; share/sysbuild/images/bootloader/Kconfig:36 @ v4.3.0",
  "role_source": "CONFIG_MCUBOOT=y in the image's .config",
  "role_evidence": "strong"
}
```

| `role_evidence` | When |
|---|---|
| `strong` | the image's own `.config` identifies it, e.g. `CONFIG_MCUBOOT=y`, or it is `domains.yaml`'s default image |
| `weak` | inferred indirectly, e.g. from the app's `CONFIG_BOOTLOADER_MCUBOOT=y` without a bootloader image in the build |
| `unknown` | no evidence, e.g. a plain build with no sysbuild: zmig can't know what bootloader is deployed |

Hazard findings that depend on a weak or unknown role are capped at **low** confidence, and say why. Every hazard finding also carries `remedy` (§8.1).

### 2.5 "Not checked" section

**Report:** a new top-level field, `not_checked: [{area, reason, analyzer}]`. Analyzers add to it through `AnalyzerResult.not_checked`, and Markdown renders it as its own section.

**Always listed:**
- sysbuild pair rules outside `MAIN`/`BOOTLOADER` (`FIRMWARE_LOADER`, vendor sysbuild CMake);
- non-MCUboot bootloaders (role `other`, or unknown);
- salvage limits for files the app doesn't own, such as MCUboot's own `prj.conf` and board files;
- `--deployed-image` not given, so acceptance was checked against the source *build's* bootloader configuration, not a real device image;
- sysbuild configuration for images zmig doesn't know (e.g. TF-M non-secure/secure splits).

**Added per run:** every category that ended unknown (§8), with its reason.

---

## 3. Shared inputs and machinery

- **Per image, at both refs:** `.config` and kconfiglib metadata, extended to also record each symbol's choice group and defining file (needed in §5); final EDT, including raw DTS text; SPDX; the CMake codemodel; and the `imgtool sign` arguments, parsed from the app image's `build.ninja`.
- **Source at each ref:**
  - the Zephyr mirror;
  - module sources by **pinned SHA**, from cache mirrors of each module's upstream, cloned fresh like Zephyr: `Mirror(url)` from `refs.py`. Workspace clones are narrow and may lack the other ref's objects.
  - Reads use `git show <sha>:<path>` and `git grep <sha>`, never a checkout.
- **Source diff helper** (`zmig/srcdiff.py`): for a file and its two SHAs, it extracts
  - `#define NAME value` (macros, values normalized),
  - `struct`/`union` member lists, from a brace-matching C parser for declarations only, which handles the headers in §1;
  - Kconfig `select`/`imply`/`depends`/`default` lines, via kconfiglib metadata rather than text.

  Results cite `file:line` at both SHAs. A construct it can't parse is reported as "changed, not interpreted" (low confidence), never as unchanged.
- **Seed identifiers:** some checks need a starting point, such as which macro is the image magic. These are listed in a shipped data file (`zmig/data/hazard_seeds.yaml`), each with a short reason. **Every seed is verified at each ref.** A seed that isn't found means that check is unknown at that ref, not clean. This is the only knowledge zmig brings in, and it's reported per run.

---

## 4. Flash partition layout (`layout` category)

- **Inputs:** each image's final EDT at both refs. Both representations are read: children of `fixed-partitions`/`fixed-subpartitions`, and `*-partition` nodes such as `zephyr,mapped-partition` (`crossimage.partitions()`, already covered by a regression test). For each partition: label, node path, `reg` (offset, size), and the flash device it's on.
- **Roles:**
  - slot0, slot1, scratch, and storage are resolved from **source at each ref, not names**: the partition labels or chosen nodes MCUboot's flash-map backend uses (found by grepping MCUboot's Zephyr port at the pin for `FIXED_PARTITION_*`/`DT_NODELABEL` uses, citing lines), and those the storage backends use (§6);
  - the chosen `zephyr,code-partition` of each image;
  - a label with no role source is reported with role `unknown`.
- **Erase granularity:** the flash node's `erase-block-size`/`write-block-size` from the EDT, needed for sector arithmetic.
- **Findings per partition** (old→new, per image and across images):

| Change | Severity | Notes |
|---|---|---|
| moved | breaking | |
| resized (and the change in usable image size) | breaking | usable size comes from the `imgtool` `--slot-size`, never derived from guessed trailer sizes |
| removed, e.g. scratch | breaking | flagged with the modes that need it, from §5 |
| added | info | |
| reordered | breaking | role order changes, even if each partition alone looks fine |
| moved to another flash device | breaking | |
| storage partition moved or resized | breaking | "stranding": data at the old address isn't found by the new image; §6 consumes this |

- **Clean:** every image's EDT is available at both refs, every partition resolves to a role or is listed as unknown-role, and there are no deltas.
- **Unknown:** any image's EDT is missing, a role source can't be found at a ref, or the partition representation isn't recognized (no partition nodes at all, while `CONFIG_FLASH_MAP` is set).
- **Introduced or pre-existing:** layout deltas are diff findings, so always introduced. Cross-image disagreements (`crossimage`) are condition findings (§2.3).

---

## 5. MCUboot and image format (`mcuboot` category)

### 5.1 Option vs. behavior
- **Options:** the `SB_CONFIG` diff (§2.1), plus a diff of the bootloader image's resolved `.config` restricted to **symbols defined in MCUboot's own Kconfig at that pin**. That restriction comes from the defining file in the kconfiglib snapshot, not from a list of names.
- **Grouping:** symbols are grouped by their Kconfig **choice** (the choice prompt as written at that ref), so "upgrade mode", "signature type", and "encryption" come from MCUboot's Kconfig, not from zmig. A non-choice symbol is reported under its menu.
- **Behavior:** the active member of each choice group, plus the non-choice values, in the resolved `.config` at both refs. Same active members means "behavior unchanged", whatever happened to `SB_CONFIG` names.

### 5.2 Format-affecting or not
- **Signing parameters:** `imgtool sign` arguments from `build.ninja` at both refs (header size, slot size, alignment, key type and size from the key file, pad, `--overwrite-only`, max sectors, encryption key). Any difference is format-affecting. These are facts about the actual image.
- **Format definitions** at the two MCUboot pins, via `srcdiff`: the image header struct and magic, TLV area magic and type IDs, trailer and boot-magic layout. Seeds: `IMAGE_MAGIC`, `struct image_header`, `IMAGE_TLV_INFO_MAGIC`, `IMAGE_TLV_*`, and the trailer and magic identifiers, verified per pin. "N files changed" is replaced by "format definitions changed: X; unchanged: Y; not interpreted: Z", each with `file:line` at both pins.
- **SBOM:** files compiled into the bootloader whose diffs touch none of those definitions are summarized as "implementation changes, format definitions unchanged". That's medium confidence, because behavior can change without the declarations changing.

### 5.3 Format checks, by direction

The two directions are reported separately, each with its own status.

**(a) The old bootloader accepts the new app image.** This always applies, because a field device keeps its bootloader unless the bootloader is updated.
- **The deployed bootloader** is the source build's MCUboot. That is an `assumptions` entry (§8), refined by `--deployed-image`.
- **Measured checks:** the target's signed app image (§5.5) is compared with what the source bootloader requires:
  - header size and magic;
  - signature TLV type, and key (key-file hash, plus the key TLV/hash in the image);
  - encryption TLVs and flags;
  - alignment and padding;
  - image size ≤ the source slot size;
  - upgrade mode: the source bootloader's mode vs. the target app's `MCUBOOT_BOOTLOADER_MODE_*`;
  - the source bootloader's max-sectors limit vs. the target slot geometry.
- **Results:** a mismatch is breaking; a check with missing inputs is unknown.

**(b) The new bootloader reads old images and swap state.** This applies only if the bootloader is updated: either the remedy of some hazard is `bootloader-update`, or the build evidences an updatable bootloader. Otherwise the direction is reported as `not-applicable`, with that reason.
- **Checks:** `srcdiff` of the trailer and boot-state definitions between the MCUboot pins (magic, swap-status layout, image-OK and copy-done fields), plus header and TLV parsing compatibility for the *source* signed image (measured, §5.5).
- **Results:** a definition change is breaking, with remedy `reflash` when it means a swap in progress at the time of the bootloader update could be misread.

**Slot-size requirements per mode,** e.g. swap-using-offset wanting a secondary slot one sector larger (fixture B):
- derived from MCUboot source at the target pin where it is checked in code (the mode's size or sector-count checks, cited);
- otherwise from the migration guide line (medium);
- otherwise unknown.
- The check uses EDT slot sizes and the flash erase-block size.

**Is the bootloader itself updatable?** It's unknown unless the build shows a mechanism:
- an image with a bootloader-updating role (e.g. a first-stage bootloader in `domains.yaml`);
- MCUboot configured to update itself, a group in MCUboot's own Kconfig, found by choice/menu.

Unknown updatability makes `bootloader-update` remedies resolve to `reflash` in the verdict (§8.1), with the reason stated.

### 5.4 Clean / unknown
- **Clean:** both refs have a bootloader image with a strong role; options, behavior, signing parameters, and format definitions were all compared with no format-affecting delta; and all acceptance checks passed.
- **Unknown:**
  - no bootloader image (plain build);
  - a weak or unknown role;
  - a missing MCUboot source pin or seeds not found;
  - `imgtool` arguments not found in `build.ninja`;
  - a bootloader that isn't MCUboot.

### 5.5 Signed images, measured (default) and `--deployed-image`

- **Default:** zmig always parses the **source build's own signed app image** and the **target build's**, so header, TLV and flag checks are measured, not inferred from configuration. The signed images are located from the `imgtool sign` output path in the app image's `build.ninja`, not by guessing a file name.
- **The parser:** the header and TLV layout are read from MCUboot's `image.h` at the matching pin: struct member order and sizes via `srcdiff`, magic values and TLV type IDs from the `#define`s. Fields read: magic, header size, image size, protected TLV size, version, flags, and the protected and unprotected TLV areas (type, length, and a hash of each value).
- **Diff:** source signed image vs. target signed image, field by field. Expected differences such as image size and digest are reported as info. Header layout, flags, and the set of TLV types and their lengths are format evidence.
- **`--deployed-image <path>`** replaces the source build's image as the "old" side, and removes the "deployed == source build" assumption for the image (not for the bootloader configuration).
- **Parse failures** name the exact field, and the direction-(a) checks that need it become unknown. zmig doesn't guess offsets.

### 5.6 imgtool, at each MCUboot pin

The image is produced by `scripts/imgtool` at the MCUboot pin, so the tool is diffed too:
- **Format constants:** imgtool is Python, so its module-level constants and literal tables (magic values, the TLV type map, flag values) are extracted with Python's `ast` from `scripts/imgtool/*.py` at both pins. No regexes and no execution.
- **The `sign` arguments** each ref's build passes (from `build.ninja`), as before.
- **Ground truth for both:** the measured signed-image diff of §5.5. A constants change that doesn't show up in the measured output is reported as "tool changed, output unchanged for this build" (info). A measured change is reported whether or not a constant explains it.

### 5.7 Implementation-only changes

Files compiled into the bootloader, or used by an enabled storage backend, whose diffs touch none of the format definitions are reported as "implementation changed, format definitions unchanged" at **medium** confidence. Behavior can change without the declarations changing, so a `direct-ok` verdict that rests on such files names them (§8.2).

---

### 5.8 Implementation notes (from running §5 on fixtures A, B, C and xapp)

- **Seeds are located by `git grep` at the pin**, not by file path, because definitions move between pins (e.g. `BOOT_STATUS_*` moved from `bootutil_priv.h` to `bootutil_area.h`). A struct may be declared through a macro (`STRUCT_PACKED image_header {` at the v4.0–v4.2 pins). The declaration prefix is recorded, so a packing change shows even when the members are identical.
- **`#define`s are compared as value multisets without evaluating `#if` conditions.** When every old definition survives and new conditional variants are added (MCUboot v4.0→v4.1 `BOOT_STATUS_OP_SWAP` {2} → {1, 2}, for swap-using-offset), the status is `extended`: reported, never cleared as unchanged, and never called a change.
- **Signed images:** the struct layout, magic values and TLV IDs come from `image.h` at the pin. The byte order is imgtool's `--endian` default, read with `ast` from `main.py`. Which `sign` options are flags also comes from imgtool's `click.option(is_flag=True)` at the pin, so `--pad <infile>` isn't misread. The oracle is imgtool's own `dumpinfo` at the same pin (`tests/test_p1_fixture.py`).
- **Direction (a) agreement** uses the app/bootloader pairs from `share/sysbuild/image_configurations` (`sysbuild_rules.pairs`). A mismatch is breaking only when compiled target-app code depends on the app-side option, found by git-grepping the target tree and filtering through the app's SBOM. Fixture B now enables `CONFIG_IMG_MANAGER`, and `subsys/dfu/img_util/flash_img.c:202-215` @ v4.3.0 starts uploads at the secondary slot's second sector under swap-using-offset. Otherwise a mismatch is risky (medium). `config-pin` requires the pin's SB symbol to exist, not deprecated, at the target, and its app option to be `y` in the source app.
- **Slot geometry:** the `if (...)` conditions in `boot_slots_compatible()` of the `swap_*.c` compiled into the target bootloader, kept if they mention sectors and are followed by a `BOOT_LOG`. They are evaluated by an AST-whitelisted evaluator over sector counts from the EDT. Uniform sectors of `erase-block-size` are assumed (heuristic `mcuboot.uniform-sectors`). Macros are resolved through MCUboot's `#define` chain in `boot/bootutil` and `boot/zephyr` down to a `CONFIG_` value. `BOOT_MAX_IMG_SECTORS` stays unevaluated, because `mcuboot_config.h:404/408/411` has three conditional definitions.
- **Bootloader updatability** is `unknown` for every fixture: one bootloader image in `domains.yaml`, and no enabled MCUboot option whose prompt mentions updating the bootloader (a prompt regex, heuristic). Direction (b) is therefore `not-applicable` everywhere. The boot-state definition diffs are still reported.

## 6. Persistent storage (`storage` category)

- **Which backends are enabled:** symbols that are `y` in the app's resolved `.config` and are **defined in Kconfig files under the backend source trees** (`subsys/settings`, `subsys/fs/nvs`, `subsys/fs/zms`, `subsys/secure_storage`, `subsys/fs` for filesystems, and module filesystems such as littlefs and fatfs). The trees are seeds, verified per ref; membership comes from each symbol's defining file in the snapshot. Each enabled backend lists the symbols that enable it.
- **Which partition each backend uses:** from source at each ref, by grepping the backend implementation for its partition references (`DT_CHOSEN`, `FIXED_PARTITION_*`, Kconfig-derived partition paths such as v4.1's `DT_SETTINGS_PARTITIION`/`DT_STORAGE_PARTITION`). Resolved against the EDT, then combined with the §4 deltas: if that partition moved or resized, storage is stranded (breaking).
- **Implicit-enable changes:** for every backend symbol whose value changed while the user didn't set it, follow the kconfiglib metadata at both refs to the cause: which symbol's `select`/`imply`/`default` appeared or disappeared, with file and line. Fixture C at v4.0.0 is `imply NVS` at `Kconfig.its_store:14`, gone at v4.1.0. If the change makes the target configure fail (`depends on SETTINGS` unmet), the existing Kconfig diagnostics and salvage report it, and this category links to them.
- **Format-affecting source changes:** for each enabled backend, `srcdiff` its private headers and on-media structures between refs. Seeds: NVS allocation-table-entry struct and its `#define`s in `nvs_priv.h`; the ZMS equivalents in `zms_priv.h`; settings key and value encoding constants in the backend file. Results: "new code can't read old data" when an on-media struct, magic, version, or CRC definition changed; "one-way migration on first boot" when the target source contains a version-upgrade path keyed on the old format version (found by `srcdiff` of the version `#define` plus grep for its comparisons, cited); otherwise "implementation changed, on-media definitions unchanged" (medium).
- **Clean:** all enabled backends identified, partition mapping resolved at both refs, no implicit backend switch, and no on-media definition change.
- **Unknown:** a backend enabled whose sources aren't in a recognized tree, a partition reference not resolved, or seeds missing at a ref.

---

### 6.1 Implementation notes (from running §6 on fixtures A, B, C and xapp)

- **Backends:** the trees and seeds are in `zmig/data/hazard_seeds.yaml` (`storage.backends`). Each tree is checked to exist at each ref. Membership comes from each symbol's Kconfig location. The `filesystem` tree (`subsys/fs`) excludes `fs/nvs` and `fs/zms`.
- **Causes:** the kconfig snapshot now records `implies` as well as `selects` (snapshot v6). The exact `select`/`imply`/`depends on` line is found by scanning the defining Kconfig entry at that ref's SHA. Fixture C:
  - `Kconfig.its_store:15` `select SETTINGS` and `:14` `imply NVS` at v4.0.0;
  - `:34` `depends on SETTINGS` at v4.1.0, for the user's `CONFIG_SECURE_STORAGE_ITS_STORE_IMPLEMENTATION_SETTINGS=y`, so the choice falls back to `..._NONE`.
  - Configure doesn't fail, so salvage never sees it.
- **Remedy, verified:**
  - Candidates are the settable symbols the causes name (lost `select`/`imply` targets, unmet `depends on`), plus their unmet dependencies over up to four rounds.
  - `zmig/extract/kconfig_try.py` re-resolves the target image's Kconfig from the fragments its configure log lists ("Loaded/Merged configuration"), with the candidates merged last.
  - `config-pin` is claimed only if every backend symbol enabled at the source, and every switched choice's source member, comes back `y`. Otherwise the remedy is `unknown`, and the finding names what didn't come back.
  - For C this yields `FLASH, FLASH_MAP, FLASH_PAGE_LAYOUT, NVS, SETTINGS`. A real build with them added (`tests/test_p1_fixture.py::test_c_storage_remedy_builds`) keeps the source's backends, which agrees with `migration-guide-4.1.rst:725-729`.
- **Partitions:** references to EDT partition labels and chosen nodes in the compiled files of each backend tree (app SBOM; headers count). NVS and ZMS are libraries that get their flash area from a caller (settings), so the category is unknown only when *no* enabled backend references a partition. A layout hazard on a referenced partition (fixture A: `storage_partition`, referenced by settings) is linked into the category and not duplicated as a storage finding.
- **On-media definitions:** `srcdiff` of the backend seeds. They are unchanged for A (NVS, settings, v4.2.2→v4.3.0), so the changed compiled backend files are reported as implementation-only (medium).

## 7. BLE and security state (`ble` category), and wire format (`wire`)

### BLE
- **Active when** the app's resolved `.config` enables a symbol defined under `subsys/bluetooth` that persists state: a symbol whose Kconfig `depends on`/`select` includes the settings subsystem, found from the snapshot.
- **What persists** is derived from the host files that register settings handlers (grep for the handler macros at each ref):
  - the set of settings key names they read and write;
  - the persisted structs (e.g. the key-storage struct and its length `#define`s), via `srcdiff`;
  - identity-related symbols (`ID` count, privacy) from the snapshot.
- **Findings:**
  - a key name removed or renamed means bonds or identity are dropped (breaking);
  - a persisted struct layout changed means old bonds may be unreadable (breaking, medium);
  - an identity-affecting option changed means the identity address may change (risky).
- **Unknown** when the handler macros or persisted structs can't be found at a ref.

**Implementation notes (from running §7 BLE):**
- **Seeds** are in `hazard_seeds.yaml` (`ble`). Key names come from the compiled files under `subsys/bluetooth` (app SBOM), each tagged with its source:
  - `handler`: `BT_SETTINGS_DEFINE`, whose `"bt/"` prefix is read from the macro's definition at each ref, and `SETTINGS_STATIC_HANDLER_DEFINE*`;
  - `store`: the first argument of `bt_settings_store/delete`;
  - `root-leaf`: the `strncmp(name, "x")` leaves of the root handler in `host/settings.c`, prefixed with the path that file registers.
- **Oracle:** the settings handlers the build linked, read from `zephyr.elf` (the first field of `struct settings_handler_static` is the subtree name). These equal the `handler` keys at both refs for fixture D (`tests/fixtures/p1-ble`: a bonding peripheral, plain build, v4.2.2→v4.3.0).
- **Heuristic:** `store` and `root-leaf` keys come from compiled files, but the calls may sit under `#if` branches that aren't compiled. A removed key is therefore medium confidence.
- **Fixture D result:** 11 keys at each ref, the persisted structs (`bt_keys`, `BT_KEYS_STORAGE_LEN`, `bt_ltk`/`bt_irk`/`bt_csrk`) unchanged, and the identity options unchanged, so the category is clean (medium). Fixtures A, B and C and xapp are not-applicable.

### Wire format
- **Allowlist:** `zmig/data/wire_format.yaml`, shipped defaults plus `--wire-allowlist <path>` for user entries. Each entry has an id, `kconfig: SYMBOL` or `dt: compatible/property`, a reason, and a source link.
  - Shipped defaults are a **small, documented starting set**, and every entry is verified to exist at the refs being compared. Missing at a ref means "allowlist entry not found", info.
  - zmig makes no claim that the list is complete; "not checked" says so.
- **Findings:** any allowlisted symbol or property whose resolved value differs, in any image, referencing the entry id.
- **Clean** only means "no allowlisted item changed". The verdict counts this category as checked, with that caveat in the report.

---

## 8. Upgrade-path verdict (report section `upgrade_path`)

```json
"upgrade_path": {
  "verdict": "blocked",
  "confidence": "high",
  "categories": {
    "layout":   {"status": "hazard", "confidence": "high", "findings": ["…"], "checked": ["…"]},
    "mcuboot":  {"status": "clean", "confidence": "medium",
                 "directions": {"old_bootloader_accepts_new_image": "clean",
                                "new_bootloader_reads_old_state": "not-applicable"},
                 "implementation_only": ["bootloader: boot/bootutil/src/loader.c", "…"]},
    "storage":  {"status": "hazard", "confidence": "high", "findings": ["…"]},
    "ble":      {"status": "not-applicable", "checked": ["BT settings disabled in every image"]},
    "wire":     {"status": "clean", "checked": ["allowlist v1, 9 entries"]},
    "sysbuild": {"status": "clean", "checked": ["…"]}
  },
  "remedies": {"bootloader-update": ["LAYOUT-…"], "app-change": ["STORAGE-…"]},
  "assumptions": [
    "deployed bootloader == the source build's MCUboot (image mcuboot @ v4.2.2)",
    "deployed app == the source build's signed app image",
    "bootloader updatability: unknown (no mechanism in the build)"
  ],
  "basis": ["…what a direct-ok or a clean category rests on, incl. implementation-only files…"]
}
```

### 8.1 Remedies

Every hazard finding gets a `remedy`, derived from evidence:

| Remedy | When (evidence) |
|---|---|
| `config-pin` | the target ref still offers the old behavior through configuration: the old `SB_CONFIG`/Kconfig option (or its value) **exists in the target ref's Kconfig** (checked with the target snapshot) and selecting it restores the source's resolved image options. Example: fixture B, `SB_CONFIG_MCUBOOT_MODE_SWAP_USING_MOVE` defined at v4.3.0 `share/sysbuild/images/bootloader/Kconfig:55` |
| `app-change` | the app's own files can fix it: an overlay or code in images that reach the device by app OTA, e.g. explicitly enabling a storage backend that was implicitly enabled before |
| `intermediate-hop` | a one-time image must run in between, e.g. a storage format migration that new code can't do from the old format but an intermediate version can |
| `bootloader-update` | the bootloader must change, and the build evidences that the bootloader can be updated in the field |
| `reflash` | the bootloader must change and updatability is unknown or absent, or a change makes old in-progress state unreadable |
| `unknown` | none of the above can be established from evidence |

Rules that follow from ownership:
- **Layout:** moved, resized, removed, or reordered partitions that the bootloader uses (slots, scratch, its own) get `bootloader-update`, or `reflash` when updatability isn't evidenced, because the bootloader owns the layout. A partition only the app uses (e.g. storage) gets `app-change` or `intermediate-hop` by the storage rules.
- **Bootloader options:** an option change gets `config-pin` only after verifying that the old option exists at the target ref and that selecting it yields the source's resolved bootloader options. Otherwise it's `bootloader-update`/`reflash`.
- **Unproven remedy:** if zmig can't prove a remedy, it's `unknown`, and the finding says what evidence was missing.

### 8.2 Verdict

| Verdict | Meaning |
|---|---|
| `blocked` | at least one hazard isn't resolvable by an application OTA alone: remedy `bootloader-update` or `reflash` |
| `needs-changes` | at least one hazard, all resolvable by OTA: `config-pin`, `app-change`, `intermediate-hop`, or `unknown` (an unknown remedy counts as needs-changes, with the remedy marked unknown) |
| `unknown` | no hazard proven, and at least one category `unknown` |
| `direct-ok` | every category `clean` or evidenced `not-applicable`, and no hazard |

- **Precedence:** blocked > needs-changes > unknown > direct-ok. The verdict starts as `unknown` and is computed from categories; it is never defaulted to OK.
- **`confidence`** is the minimum over categories that are clean or hazardous; a category's confidence is the minimum over its findings and evidence.
- **`basis`:** when `direct-ok` (or a clean category) rests on implementation-only changes in format-bearing files (MCUboot, NVS/ZMS, settings backends, §5.7), confidence is at most medium, and each file is named in `basis` and in the verdict text.

### 8.3 Pre-existing breaking findings

A breaking finding with `introduced_by_migration: false` in a deployment-relevant category (layout, mcuboot, crossimage, storage) means the source build is itself inconsistent. Example: xapp-mismatch, where app and bootloader disagree on `slot1_partition`.

**Proposal:** both, scoped.
- **The touched category becomes `unknown`** (reason: "the source build is inconsistent, so the deployed state can't be the source build"), and **the verdict's confidence is capped at `low`**.
- **Other categories are evaluated normally,** so a proven `blocked` or `needs-changes` elsewhere still stands.

**Why:**
- **Capping confidence alone isn't enough:** it would still let `direct-ok` through, built on the very assumption the finding disproves.
- **Forcing the whole verdict to `unknown` throws away valid results:** a layout move proven between the two builds, say, holds whichever half of the inconsistent source is on the device.

The finding and the downgrade are both listed in `assumptions` ("deployed == source build: contradicted by …").

---

## 9. Schema 1.2 (additive)

| Location | Field | Notes |
|---|---|---|
| finding | `introduced_by_migration` | bool, optional; always emitted by 1.2 producers |
| finding | `provenance` | object with `evidence_source`, `role_source`, `role_evidence` |
| finding | `category` enum | adds `layout`, `mcuboot`, `storage`, `ble`, `wire`, `sysbuild` |
| report | `upgrade_path` | object (§8): verdict (4 values), confidence, categories (status, confidence, directions for mcuboot), remedies, assumptions, basis |
| finding | `remedy` | one of `config-pin`, `app-change`, `intermediate-hop`, `bootloader-update`, `reflash`, `unknown`; on hazard findings |
| report | `not_checked` | array (§2.5) |
| report | `environment.sdk` | `{path, version}`; and the SDK per build context |

`schema_version` becomes `1.2`. Older reports still validate: all new fields are optional.

---

## 10. SDK handling

- **Install:** SDK 0.17.4 (`arm-zephyr-eabi` toolchain and host tools only, from the official zephyrproject-rtos/sdk-ng release) goes into `~/.cache/zmig/sdks/zephyr-sdk-0.17.4`, owned by zmig. Your 1.0.1 isn't touched, and the new SDK is **not** registered in the CMake package registry.
- **Selection** (`--sdk <path>`, recorded per fixture). Verified at v4.0.0, v4.2.2 and v4.3.0 in `cmake/modules/FindZephyr-sdk.cmake:75-82`: with `ZEPHYR_SDK_INSTALL_DIR` defined, Zephyr calls `find_package(Zephyr-sdk <ver> REQUIRED CONFIG HINTS ${ZEPHYR_SDK_INSTALL_DIR})`. CMake searches HINTS before the user package registry (where 1.0.1 is registered), but after `<Pkg>_ROOT` and `CMAKE_PREFIX_PATH`, and moves on if the hinted SDK fails the version check. So with `--sdk`, zmig:
  1. sets `ZEPHYR_SDK_INSTALL_DIR` and `ZEPHYR_TOOLCHAIN_VARIANT=zephyr`;
  2. removes `Zephyr-sdk_ROOT`, `Zephyr-sdk_DIR` and `CMAKE_PREFIX_PATH` from the build environment;
  3. passes `-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF`, so the registry can't supply another SDK;
  4. after configure, checks every image's "Found toolchain: zephyr <version> (<path>)" line against the selected SDK, and fails with an actionable error if any image used another one.
- **Cache key and report:** the SDK path and version join the build key, and are recorded in `environment.sdk` and each context. One SDK per run is kept.

---

## 11. Fixtures and tests

All refs are asserted by **resolved SHA** (§1), so a retagged ref fails the test instead of changing it.

| Fixture | Pair, board | App contents | Expected (oracle) | Must not report |
|---|---|---|---|---|
| **A** `p1-layout` | nucleo_h753zi, v4.2.2→v4.3.0, sysbuild+MCUboot, SDK 0.17.4 | settings on NVS in the storage partition (symbol names verified at both refs before writing it) | layout: slot0 0x40000→0x20000, slot1 0x80000→0x60000, storage 0x20000→0xa0000, scratch removed, all with remedy `bootloader-update`/`reflash`; storage stranding; **verdict `blocked`**. Oracle: guide 4.3:71-73 + the DTS lines | any MCUboot mode-change finding (`Kconfig:36` keeps swap-using-move for STM32; resolved bootloader mode identical) |
| **B** `p1-mode` | nrf52840dk/nrf52840, v4.2.2→v4.3.0, sysbuild+MCUboot, SDK 0.17.4 | xapp-like | `sb-option-default-changed` for the MCUboot mode; behavior changed swap-using-move → swap-using-offset in the bootloader and app `.config`s; slot-size requirement evaluated with sizes; remedy `config-pin` with `SB_CONFIG_MCUBOOT_MODE_SWAP_USING_MOVE`, which the test asserts exists at v4.3.0 `Kconfig:55`; **verdict `needs-changes`**. Oracle: guide 4.3:485-492 + `Kconfig:36-37` | any layout finding (partitions identical) |
| **C** `p1-rename` | nrf52840dk/nrf52840, v4.0.0→v4.1.0, sysbuild+MCUboot, SDK 0.17.4 | `sysbuild.conf`: `SB_CONFIG_MCUBOOT_MODE_SWAP_WITHOUT_SCRATCH=y`; app: ITS settings store (verified at both refs) | `sb-option-renamed` with "behavior unchanged" (bootloader mode identical in both resolved `.config`s); storage implicit-enable change citing `Kconfig.its_store:14` (v4.0.0) and `:34` (v4.1.0). **The verdict isn't pre-asserted:** the test checks that it follows from the categories by the §8.2 rules. Oracle: guide 4.1:31-33, 4.1:725-729 | "mode changed" |
| **xapp** | regression, v4.4.0→v4.5.0-rc3 | | P0.5 results unchanged; signed images measured (§5.5); the MCUboot pin change classified as format-affecting or implementation-only | |
| **xapp-mismatch** | regression | | partition mismatch with `introduced_by_migration: false`; the layout category becomes `unknown`, and verdict confidence is capped at low (§8.3) | |

**Independent oracles, beyond the guide lines:**
- **Partitions:** read from the `dtc`-normalized `zephyr.dts` of each image (extending `tests/manual_diff.py`).
- **MCUboot options:** a manual diff of the bootloader `.config`s restricted to symbols `git grep`'d from MCUboot's Kconfig at each pin.
- **Source facts:** `git show <sha>:<path>` lines asserted in the tests, so a change in the source shows up as a test failure, not a silent pass.
- **Regressions:** every false positive or negative found during implementation gets one.

---

## 12. Implementation order

1. Schema 1.2, `not_checked`, `provenance`, `introduced_by_migration`; `--sdk` and the SDK install and verification.
2. Build fixtures A, B, C at their refs; confirm the builds before analyzing anything.
3. `sysbuild` analyzer (§2.1) and image identity (§2.2).
4. Categories one at a time, each run on A, B, C, and xapp, with output shown: layout → mcuboot (§5.1–5.4) → storage → BLE → wire → verdict → `--deployed-image`.

---

## 13. Risks and open questions

- **Q1 (resolved):** four verdicts plus `remedy` on every hazard (§8.1–8.2).
- **Q2 (resolved):** "deployed == source build" is an explicit `assumptions` entry, and signed images are measured by default (§5.5).
- **Pre-existing breaking findings** make the touched category unknown and cap verdict confidence at low (§8.3). If you'd rather force the whole verdict to unknown, that's a one-line change in the verdict rule.
- **Source parsing limits.** `srcdiff` reads declarations, not semantics. A behavior change with no declaration change, e.g. a different CRC algorithm with the same struct, shows up only as "implementation changed" at medium confidence, never as clean format.
- **Seeds.** The seed identifiers and directories are the one place zmig brings knowledge. They're few, cited, and verified per ref, and missing seeds turn a category to unknown. They will still need maintenance.
- **Build cost.** A, B, and C add six sysbuild builds, two images each, plus module mirrors (MCUboot, hal_stm32, hal_nordic, crypto) cloned fresh. A first run takes tens of minutes; cached reruns are fast.
- **SDK selection.** Precedence was verified in source (§10), and the registry is disabled per build. The post-configure toolchain check stays as the guarantee.
- **Fixture A's board.** STM32 sysbuild with MCUboot may need modules or Kconfig the fixture discovers by building. If nucleo_h753zi can't build MCUboot at v4.2.2 with SDK 0.17.4, I'll report that before substituting anything.
