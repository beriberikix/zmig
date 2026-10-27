# zmig P1 design: field-upgrade hazard analyzer

Status: draft for review (2026-10-27). Nothing in this document is implemented yet.

**Goal:** answer one question with evidence. Can a device that runs the `--from` build
today receive the `--to` build over the air and keep working? If not, what has to happen
in between? "Safe" has to be earned category by category. A category that wasn't
positively checked is **unknown**, and an unknown category makes the whole verdict
unknown.

P1 extends the existing model rather than redesigning it: `ImageContext`/`BuildContext`
(docs/DESIGN.md §4.3a), the analyzer protocol, `Finding`, `zmig/sysbuild_rules.py`, and
the `images` and `crossimage` analyzers. Nothing blocks the work. One change needs your
call: the verdict values in §8 (see open question Q1).

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

### 2.4 Provenance on every hazard finding

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

Hazard findings that depend on a weak or unknown role are capped at **low** confidence, and say why.

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

### 5.3 Would a deployed bootloader accept the new image?
- **The deployed bootloader** is assumed to be the source build's MCUboot. It is reported as such, with `role_evidence`, and `--deployed-image` refines it (§5.5).
- **The new app image** is checked against it:
  - signature type and key match (key file hash at both refs);
  - header size;
  - slot size ≥ image size;
  - alignment;
  - upgrade mode compatibility (the source bootloader's mode vs. the target app's `MCUBOOT_BOOTLOADER_MODE_*`);
  - encryption;
  - the old bootloader's max-sectors limit vs. the new slot geometry.

  A mismatch is breaking. A check with missing inputs is unknown.
- **Slot-size requirements per mode**, e.g. swap-using-offset needing a secondary slot one sector larger (fixture B):
  - derived from MCUboot source at the target pin where it is checked in code (the size or sector-count checks in the mode's swap implementation, cited);
  - otherwise from the migration guide line (medium);
  - otherwise unknown.
  - The check uses the EDT slot sizes and erase-block size.
- **Is the bootloader itself updatable?** It's unknown unless the build shows a mechanism. "Unknown" is the honest default, and the finding says the hazard is "fatal unless the bootloader can be replaced in the field", rather than claiming either way. Mechanisms to look for, each derived from the build:
  - an image with a bootloader-updating role, such as a first-stage bootloader in `domains.yaml`;
  - MCUboot configured to update itself, a Kconfig group in MCUboot's own Kconfig, found by choice/menu, not by name.

### 5.4 Clean / unknown
- **Clean:** both refs have a bootloader image with a strong role; options, behavior, signing parameters, and format definitions were all compared with no format-affecting delta; and all acceptance checks passed.
- **Unknown:**
  - no bootloader image (plain build);
  - a weak or unknown role;
  - a missing MCUboot source pin or seeds not found;
  - `imgtool` arguments not found in `build.ninja`;
  - a bootloader that isn't MCUboot.

### 5.5 `--deployed-image <path>`
- **Parsing:** the header and TLV layout are **read from MCUboot's `image.h` at the source pin**: struct member order and sizes from `srcdiff`, magic values from the `#define`s. zmig then parses the given signed `.bin`: magic, header size, image size, version, flags, the protected and unprotected TLV areas, and the TLV types present.
- **Comparison against the target build:** the expected header size, key and signature TLV type, and encryption.
- **Parse failures** say exactly which field couldn't be read, and the category becomes unknown. zmig doesn't guess offsets.

---

## 6. Persistent storage (`storage` category)

- **Which backends are enabled:** symbols that are `y` in the app's resolved `.config` and are **defined in Kconfig files under the backend source trees** (`subsys/settings`, `subsys/fs/nvs`, `subsys/fs/zms`, `subsys/secure_storage`, `subsys/fs` for filesystems, and module filesystems such as littlefs and fatfs). The trees are seeds, verified per ref; membership comes from each symbol's defining file in the snapshot. Each enabled backend lists the symbols that enable it.
- **Which partition each backend uses:** from source at each ref, by grepping the backend implementation for its partition references (`DT_CHOSEN`, `FIXED_PARTITION_*`, Kconfig-derived partition paths such as v4.1's `DT_SETTINGS_PARTITIION`/`DT_STORAGE_PARTITION`). Resolved against the EDT, then combined with the §4 deltas: if that partition moved or resized, storage is stranded (breaking).
- **Implicit-enable changes:** for every backend symbol whose value changed while the user didn't set it, follow the kconfiglib metadata at both refs to the cause: which symbol's `select`/`imply`/`default` appeared or disappeared, with file and line. Fixture C at v4.0.0 is `imply NVS` at `Kconfig.its_store:14`, gone at v4.1.0. If the change makes the target configure fail (`depends on SETTINGS` unmet), the existing Kconfig diagnostics and salvage report it, and this category links to them.
- **Format-affecting source changes:** for each enabled backend, `srcdiff` its private headers and on-media structures between refs. Seeds: NVS allocation-table-entry struct and its `#define`s in `nvs_priv.h`; the ZMS equivalents in `zms_priv.h`; settings key and value encoding constants in the backend file. Results: "new code can't read old data" when an on-media struct, magic, version, or CRC definition changed; "one-way migration on first boot" when the target source contains a version-upgrade path keyed on the old format version (found by `srcdiff` of the version `#define` plus grep for its comparisons, cited); otherwise "implementation changed, on-media definitions unchanged" (medium).
- **Clean:** all enabled backends identified, partition mapping resolved at both refs, no implicit backend switch, and no on-media definition change.
- **Unknown:** a backend enabled whose sources aren't in a recognized tree, a partition reference not resolved, or seeds missing at a ref.

---

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
  "verdict": "needs-intermediate-hop",
  "categories": {
    "layout":  {"status": "hazard",  "findings": ["…"], "checked": ["…evidence…"]},
    "mcuboot": {"status": "clean",   "checked": ["…"]},
    "storage": {"status": "hazard",  "findings": ["…"]},
    "ble":     {"status": "not-applicable", "checked": ["BT settings not enabled in any image"]},
    "wire":    {"status": "clean", "checked": ["allowlist v1, 9 entries"]},
    "sysbuild": {"status": "unknown", "reason": "…"}
  },
  "blockers": ["…finding ids that no OTA image can resolve on its own…"]
}
```

- **Category status** is one of `clean`, `hazard`, `unknown`, or `not-applicable`. `not-applicable` needs positive evidence: e.g. BLE is n/a only if the resolved `.config` shows BLE persistence disabled in every image.
- **The verdict:**
  - **direct-OK** only if every category is `clean` or evidenced `not-applicable`, and there's no breaking finding with `introduced_by_migration: true` in any hazard category;
  - **needs-intermediate-hop** if at least one category has a proven hazard;
  - **unknown** if no hazard is proven and any category is `unknown`.
  - If both a hazard and an unknown exist, the verdict is `needs-intermediate-hop` and the unknown categories are listed.
- **Default:** the verdict starts as unknown and is never defaulted to OK.
- **`blockers`:** hazards an intermediate application image cannot fix by itself, such as a bootloader incompatibility while bootloader updatability is unknown. See Q1.

---

## 9. Schema 1.2 (additive)

| Location | Field | Notes |
|---|---|---|
| finding | `introduced_by_migration` | bool, optional; always emitted by 1.2 producers |
| finding | `provenance` | object with `evidence_source`, `role_source`, `role_evidence` |
| finding | `category` enum | adds `layout`, `mcuboot`, `storage`, `ble`, `wire`, `sysbuild` |
| report | `upgrade_path` | object (§8) |
| report | `not_checked` | array (§2.5) |
| report | `environment.sdk` | `{path, version}`; and the SDK per build context |

`schema_version` becomes `1.2`. Older reports still validate: all new fields are optional.

---

## 10. SDK handling

- **Install location:** SDK 0.17.4 (minimal bundle, `arm-zephyr-eabi` toolchain, host tools, from the official zephyrproject-rtos/sdk-ng release) goes into `~/.cache/zmig/sdks/zephyr-sdk-0.17.4`, owned by zmig. Your 1.0.1 isn't touched.
- **Selection:** a new `--sdk <path>` flag, also recorded per fixture. Builds set `ZEPHYR_SDK_INSTALL_DIR` and `ZEPHYR_TOOLCHAIN_VARIANT=zephyr`.
  - Before relying on it, zmig verifies at each ref that `FindZephyr-sdk.cmake` honors `ZEPHYR_SDK_INSTALL_DIR` ahead of the CMake package registry.
  - It then checks the configure log's "Found toolchain: zephyr X.Y.Z (path)" line. If the path isn't the selected SDK, the run fails with an actionable error instead of silently using another SDK.
- **Cache key and report:** the SDK path and version join the build key, and are recorded in `environment.sdk` and each context. One SDK per run is kept.

---

## 11. Fixtures and tests

All refs are asserted by **resolved SHA** (§1), so a retagged ref fails the test instead of changing it.

| Fixture | Pair, board | App contents | Expected (oracle) | Must not report |
|---|---|---|---|---|
| **A** `p1-layout` | nucleo_h753zi, v4.2.2→v4.3.0, sysbuild+MCUboot, SDK 0.17.4 | settings on NVS in the storage partition (symbol names verified at both refs before writing it) | layout: slot0 0x40000→0x20000, slot1 0x80000→0x60000, storage 0x20000→0xa0000, scratch removed; storage stranding; verdict needs-intermediate-hop. Oracle: guide 4.3:71-73 + the DTS lines | any MCUboot mode-change finding (`Kconfig:36` keeps swap-using-move for STM32; the resolved bootloader mode is identical) |
| **B** `p1-mode` | nrf52840dk/nrf52840, v4.2.2→v4.3.0, sysbuild+MCUboot, SDK 0.17.4 | xapp-like | `sb-option-default-changed` for the MCUboot mode; mcuboot behavior changed swap-using-move → swap-using-offset in the bootloader and app `.config`s; slot-size requirement evaluated with sizes; verdict needs-intermediate-hop. Oracle: guide 4.3:485-492 + `Kconfig:36-37` | any layout finding (partitions identical) |
| **C** `p1-rename` | nrf52840dk/nrf52840, v4.0.0→v4.1.0, sysbuild+MCUboot, SDK 0.17.4 | `sysbuild.conf`: `SB_CONFIG_MCUBOOT_MODE_SWAP_WITHOUT_SCRATCH=y`; app: ITS settings store (verified at both refs) | `sb-option-renamed` with "behavior unchanged" (bootloader mode identical in both resolved `.config`s); storage implicit-enable change citing `Kconfig.its_store:14` (v4.0.0) and `:34` (v4.1.0). Oracle: guide 4.1:31-33, 4.1:725-729 | "mode changed" |
| **xapp** | regression, v4.4.0→v4.5.0-rc3 | | P0.5 results unchanged; P1 categories evaluated; MCUboot pin change summarized as format-affecting or not | |
| **xapp-mismatch** | regression | | partition mismatch reported with `introduced_by_migration: false` (present and identical at both refs) | |

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

- **Q1, verdict values.** Some hazards can't be fixed by an intermediate *application* image, such as a bootloader mode change when the bootloader can't be updated in the field. Three values make those look like "needs-intermediate-hop". I propose keeping your three values and adding `blockers` (§8) for those cases. Alternatively, add a fourth verdict, `blocked`. Which do you want?
- **Q2, deployed bootloader.** Without `--deployed-image`, the "deployed bootloader" is the source build's MCUboot configuration. That's right for a device flashed from the source build, wrong for one that shipped differently. The report states the assumption every time.
- **Source parsing limits.** `srcdiff` reads declarations, not semantics. A behavior change with no declaration change, e.g. a different CRC algorithm with the same struct, shows up only as "implementation changed" at medium confidence, never as clean format.
- **Seeds.** The seed identifiers and directories are the one place zmig brings knowledge. They're few, cited, and verified per ref, and missing seeds turn a category to unknown. They will still need maintenance.
- **Build cost.** A, B, and C add six sysbuild builds, two images each, plus module mirrors (MCUboot, hal_stm32, hal_nordic, crypto) cloned fresh. A first run takes tens of minutes; cached reruns are fast.
- **SDK selection.** If `ZEPHYR_SDK_INSTALL_DIR` doesn't take precedence over the CMake package registry at some ref, zmig fails loudly. A fallback, registering a CMake package dir per run, would need your approval.
- **Fixture A's board.** STM32 sysbuild with MCUboot may need modules or Kconfig the fixture discovers by building. If nucleo_h753zi can't build MCUboot at v4.2.2 with SDK 0.17.4, I'll report that before substituting anything.
