# DESIGN-P2: public API/ABI diff, intersected with the project's call sites

Status: **reviewed 2026-10-28** (decisions in §10, which take precedence over earlier sections
where they differ). Not implemented yet. Module-set detection and the new held-out
configurations come first (DOGFOOD-3).

P2 answers one question: *which Zephyr declarations that this project's compiled code uses
changed between the two refs, and where does the project use them?* Every finding names a
project `file:line`. A changed declaration the project doesn't use produces nothing.

---

## 1. What was checked before writing this (2026-10-28)

| Fact | How it was checked |
|---|---|
| Clang can parse a real Zephyr translation unit with the build's own flags, after dropping GCC-only options and adding `--target=arm-none-eabi` | `clang -fsyntax-only` on Golioth's `net_connect.c` with flags from v4.2.0's `compile_commands.json`. It produced the AST and diagnosed both G1 changes at `net_connect.c:36` (`-Wincompatible-function-pointer-types`) and `:69` (`-Wconstant-conversion`, "changes value from 16154411863377969153 to 1") |
| GCC-only flags that had to be dropped for that TU | `-mfp16-format`, `--param=…`, `-fno-reorder-functions`, `-fno-defer-pop`, `-mabi=aapcs`, `-fno-printf-return-value`, `--specs=…`, `-fmacro-prefix-map=…`, `-ftls-model=…`, plus `-o/-c/-MD/-MT/-MF`. `--sysroot` (picolibc headers) and `-imacros autoconf.h` were kept |
| libclang on this machine | system `libclang-21` (`/usr/lib/llvm-21`) and `/usr/bin/clang`. No Python bindings are in zmig's venv. The PyPI wheel `libclang` (18.1.1, which bundles its own `libclang.so`) is available |
| `abidiff`, `pahole` | **not installed** on this machine (`which` finds neither) |
| Generated syscall artifacts, v4.2.0 build | `zephyr/misc/generated/syscalls.json` holds 588 entries of `[[ "ret name", "params" ], header, is_syscall]`. `syscalls_file_list.txt` lists the headers scanned, and `zephyr/include/generated/zephyr/syscall_list.h` exists |
| `zephyr_syscall_header()` | defined in `cmake/modules/extensions.cmake` at v3.5.0 (line 1523), absent at v3.2.0. This is exactly ZMK #5's cause |

---

## 2. Inputs

Per image, at each ref:

- **`compile_commands.json`**: flags for each project TU. The project's TUs are the files
  under the app and the project's own modules (`project_roots`, introduced in Step 2).
- **The ref's `include/zephyr/**`**: declarations come from there, as the project's TUs see
  them (same Kconfig, same devicetree, same `-D`s).
- **`syscalls.json`, `syscalls_file_list.txt`**: the generated syscall list.
- **Optional, when present:** both ELFs (for `pahole`), and project-provided static
  libraries or LLEXT objects (for `abidiff`).

The **source app tree is parsed at both refs**, as everywhere in zmig. The question is what
breaks in the code as the user has it today.

---

## 3. Extraction (libclang)

### 3.1 Flags

The flags are taken from each TU's `compile_commands.json` entry, with three changes:

- **Dropped:** a fixed list of GCC-only options (§1, kept in one table with a test per
  toolchain). Unknown `-f…`/`-m…` options make clang fail, so the list is tested against
  every SDK zmig supports.
- **Added:** `--target` from the toolchain triple. That's `arm-none-eabi` (with the
  build's `-mcpu`/`-mfloat-abi`), `riscv32-unknown-elf` / `riscv64-unknown-elf`, or the
  host triple for native_sim (`-m32` kept when present).
- **Added:** `-Wno-everything`, because P2 reads declarations, not warnings.

**Xtensa** (ESP32 classic, some NXP DSPs) isn't an upstream clang target. Those images are
parsed with a stand-in 32-bit little-endian triple (`i686-unknown-none`). Declarations are
then still right, but layouts aren't checked; that's recorded as not-checked.

### 3.2 Parsing

Each project TU is parsed once per ref with `PARSE_DETAILED_PROCESSING_RECORD`, so macro
definitions and instantiations are visible.

It also uses `CXTranslationUnit_KeepGoing`, so a fatal missing header doesn't stop the
parse. This matters: in ZMK's v3.5 build, `behavior.c`, `ble.c`, `combo.c` and `sensors.c`
never reached their `SYS_INIT` because GCC stopped at a missing `syscalls/*.h`
(DOGFOOD-2 §2).

### 3.3 Declaration table

Per ref, built only from declarations whose location is under that ref's
`include/zephyr/`. Keyed by name (and, for fields, `struct.field`):

| Kind | Recorded |
|---|---|
| function | canonical return and parameter types, variadic, `static inline` vs extern, `__syscall`, availability (deprecated) |
| typedef | canonical underlying type; for function-pointer typedefs, the signature |
| struct / union | fields (name, canonical type, offset, bit-field width), size, alignment, for the image's target |
| enum | constants and their values |
| macro | object-like vs function-like, parameter count, tokens. For object-like macros whose tokens form a constant expression: the value and type clang evaluates for it (from a synthesized `__auto_type` probe in a scratch TU with the same flags) |
| deprecation | `__deprecated` / `[[deprecated]]` attributes (cursor availability), and `__DEPRECATED_MACRO` / `__WARN("…deprecated…")` inside a macro body |

### 3.4 Project references

The project TUs' ASTs, at the **source** ref, are walked for references whose target is in
the table:

- call expressions, declaration references, type references, member references (field
  accesses);
- `sizeof` / `offsetof` operands;
- initializers of struct fields, which covers designated initializers of driver API
  structs;
- macro instantiations.

Function-pointer flows are tracked one step. If a project function is assigned to a field,
variable or parameter whose type is a Zephyr typedef, the function is recorded as
"implements `<typedef>`" at the assignment's `file:line`. That's how G1a is found from the
declaration, not the warning: `wait.cb.handler = event_cb_handler`.

The table and the references are cached per build in a snapshot (`zmig-api.json`, keyed like
the other snapshots), so a rerun with cached builds costs only the diff.

---

## 4. Changes reported, and their severity

A finding is created **only for a changed declaration with at least one project
reference**. It lists every reference as `file:line` evidence (the first one in the title),
plus an `artifact_diff` of the declaration (`header:line` at both refs).

| Change | Severity | Notes |
|---|---|---|
| Function / typedef / enum constant / macro **removed** (no declaration of that name at the target) | breaking | Replacement candidates come from the migration guides' renames (as the Kconfig rename does). Otherwise from a target declaration with the same signature that's referenced in the source declaration's `__deprecated` message |
| Function **signature changed** (parameter count, or canonical types of params/return) | breaking | Typedef-only changes that are canonically identical aren't reported |
| Function-pointer **typedef signature changed**, and the project implements it (§3.4) | breaking | Reported at the implementing function's definition and at the assignment |
| **Became a macro / became a function** | risky | Taking its address or `#undef` breaks |
| Struct/union **field removed or renamed**, with a project access | breaking | |
| Field **type changed**, with a project access | risky | breaking if it's a narrowing change in an assignment the project makes |
| Struct **size/offset change only** | info | risky when a binary boundary exists (§6). Static firmware is rebuilt from source, so pure layout changes don't break it |
| Enum constant **value changed**, with a project use | risky | Values persisted or put on the wire are what break. That link isn't made here (wire is P1's) |
| Object-like macro **value or type changed**, with a project use | risky | breaking when the new value no longer fits the type it's converted to at a project use. That's G1b: `NET_EVENT_IPV4_ADDR_ADD` → `uint32_t` |
| Function-like macro **arity changed** | breaking | |
| **Newly deprecated**, with a project use | risky | "Already deprecated at the source" is info |
| **Header removed** (an `#include` the project has, no file at the target) | breaking | Overlaps today's build-failure finding; that finding is linked as `related` |

**Macro token changes alone aren't reported.** Zephyr refactors macro bodies constantly,
and a body change with the same value/arity says nothing about breakage.
`CONTAINER_OF`-style semantic changes (#10) stay with the migration-guide matcher.

---

## 5. Syscalls and devicetree macros

### 5.1 Syscall list

Diff of the `is_syscall` entries of `syscalls.json` at both refs, intersected with the
project's calls:

- a syscall the project calls was removed, or its signature changed: breaking;
- a function became a syscall, or stopped being one: risky (user-mode callers are
  affected).

**The project's own `__syscall` declarations.** Any project header containing `__syscall`
that's in the source build's `syscalls_file_list.txt` but not the target's is breaking,
with the cause stated. At v3.5, project headers need `zephyr_syscall_header()` (or
`APPLICATION_DEFINED_SYSCALL`, depending on the ref) to be scanned. Both mechanisms are
looked up in the target's `cmake/modules/extensions.cmake` and Kconfig, not assumed. This
is ZMK #5.

### 5.2 Devicetree macros

The project's `DT_*` macro instantiations (§3.4) are resolved to the node/property/compatible
they name, through the existing devicetree snapshot at each ref. Reported when the target
has no such node/property/compat, at the instantiation's `file:line`.

This overlaps the devicetree analyzer's `node-gone` and property findings, which work from a
regex scan. P2 adds exact locations, for macros reached through project wrapper macros.
Where both report the same thing, the devicetree finding gets the P2 `file:line`s as
evidence instead of a second finding.

---

## 6. Optional tools (degrade if missing)

| Tool | Used for | If missing |
|---|---|---|
| `pahole` | Cross-checks the layout of structs the project accesses against the GCC-built ELF's DWARF at both refs: the toolchain that actually built it, not clang's model of it. A disagreement between clang and pahole is reported as a zmig limitation, not a finding | not-checked entry: "layouts from clang's model only" |
| `abidiff` | Only when there's a binary boundary: a project-provided static library (`.a` in the project tree linked into the image) or LLEXT extension objects. Compares the ABI that boundary sees between the two ELFs. Layout changes on that boundary become risky | not-checked entry naming the boundary |

Neither is installed on this machine. Testing the "present" path needs them installed
(`apt install abigail-tools dwarves`), so I'd like your go-ahead before installing them.

---

## 7. Dependency, packaging, failure modes

- **libclang:** an optional extra, `zmig[api]`, depending on the PyPI `libclang` wheel. It
  bundles `libclang.so`, so it works without a system LLVM, and its version is pinned.
  Without it, the analyzer is skipped with "install zmig[api]".
- **Parse failures:** a TU that libclang can't parse is listed as not checked, with its
  first error. The analyzer still reports from the TUs that did parse.
- **Cost:** about 50–300 project TUs × 2 refs. The measured single-TU parse of
  `net_connect.c` took under a second. Cached in the snapshot.
- **Category:** a new `api` category, plus `SECTION_TITLES` and schema entries. It isn't
  part of the field-upgrade verdict, which covers P1's persistent-state categories.

---

## 8. Expected effect on the oracles (to be measured, not assumed)

| Item | Today (after Step 2) | What P2 should add |
|---|---|---|
| B G1a, G1b | caught, via compiler warnings | the cause: "`net_mgmt_event_handler_t` changed `uint32_t` → `uint64_t`", "`NET_EVENT_IPV4_ADDR_ADD` no longer fits `uint32_t`" |
| ZMK #2 `SYS_INIT` | partly (d): 9 of 14 parts | all compiled parts, because KeepGoing parses past the missing syscall headers; `hog.h`'s declaration too |
| ZMK #5 syscall headers | partly (b) | caught, by §5.1 |
| ZMK #6 `void main` | missed | caught **if** v3.5 declares `main` in a public header (to be verified at the ref) |
| ZMK #3 `hci_err.h` | caught | linked header-removed evidence |
| ZMK #10 `CONTAINER_OF` | partly (c) | nothing (macro body change; §4) |
| A1 B1a, B2; B G2 | partly / missed / missed | nothing: not API changes |

At best, that's +1 to +2 strict on ZMK (#5, maybe #6, and #2 if all its parts are reached)
and no change on the held-out pairs.

The main precision risk is declaration churn that the project uses but isn't broken by,
such as risky value changes of macros it passes along. §4 keeps body-only changes out, and
makes pure layout changes info.

---

## 9. Questions for review (answered; see §10)

1. libclang: PyPI wheel or system libclang?
2. Header scope: `include/zephyr/**` only, or module headers too?
3. Severity of value changes.
4. Install `abigail-tools` and `dwarves`?
5. Order of implementation.

## 10. Review decisions (2026-10-28)

### 10.1 libclang

- **Default:** the PyPI `libclang` wheel, as the optional extra `zmig[api]`.
- **Override:** `ZMIG_LIBCLANG=/path/to/libclang.so` uses a system libclang instead, through
  `clang.cindex.Config.set_library_file`.
- **Version:** the libclang version actually loaded (`clang_getClangVersion`) is recorded in
  the report's `environment`.
- **Flags:** compile flags clang doesn't accept are stripped or translated (§3.1).
  - The list is data, with a test per supported SDK (0.16.x, 0.17.x, 1.0.x).
  - An unknown flag that makes a parse fail is reported (see §10.4), not silently dropped.

### 10.2 Scope: by usage, from the SOURCE build

The headers analyzed are those **included by the project's own translation units** in the
**source** build: `compile_commands.json` plus each object's dependency file (`ninja -t
deps`). That includes module public headers (HALs, mbedtls, …) whenever a project TU
includes them. Headers no project TU includes are never scanned.

A header in that set that has no counterpart at the target is reported as **header
removed**. Each header is resolved to its include root at the source and looked up under the
same root at the target.

### 10.3 Severity

| Change | Severity |
|---|---|
| Removal (function, type, field, enum constant, macro, header); signature change; function-like macro arity change | **breaking** |
| Changed value of an enum constant or object-like macro the project uses | **risky** |
| Type-width change (e.g. `uint32_t` → `uint64_t`) where the project stores, passes or compares the value at a narrower type (truncation), or implements a callback with the old width | **breaking** |
| Type-width change otherwise | **risky** |
| Newly deprecated, used | **risky** |
| Added symbol | **info** |
| Any change to a symbol | **info only when it is provably unused** in the compiled project TUs, i.e. no reference in any TU that parsed. If any project TU failed to parse, "unused" can't be proven, and the change is reported as not interpreted for those TUs instead (§10.4) |

### 10.4 Requirements confirmed

1. **Call sites come from the source build's TUs** (flags, sources, dependency files). P2
   therefore still finds call sites when the target build fails.
   - Target declarations come from parsing the same source TUs with the target's flags and
     include paths. That needs the target to be configured (its `autoconf.h` and
     `devicetree_generated.h`).
   - If the raw target configure fails, the salvaged target configure is used and the
     report says so.
   - If no target configuration exists at all, the target side is **not interpreted**, and
     no change is claimed.
2. **Macro use is matched at preprocessor instantiation sites.** These are libclang's
   `MACRO_INSTANTIATION` cursors in a detailed-processing-record parse, resolved to the
   macro definition they expand, including nested expansions inside project macros.
   - It is never a text match. A token that happens to equal a macro name in a comment,
     string or `#ifdef`-ed-out block doesn't count.
3. **Zero findings for symbols the project doesn't use** is an explicit test. A fixture
   changes a function, a struct, an enum and a macro the project doesn't reference, plus
   one it does, and only the used one may be reported.
4. **Every parse gap is reported as "not interpreted"**, never as clean:
   - a TU that fails to parse;
   - a TU parsed with errors (the errors listed);
   - a stripped flag that changed preprocessing (e.g. a `-D` lost);
   - an unsupported target such as Xtensa (no layout);
   - a header outside the scanned set.

   These are `not_checked` entries with the TU or header and the first error. The `api`
   status is `partial` whenever any exist.

### 10.5 Optional tools

`abigail-tools` and `dwarves` are approved for installation, and tests cover both paths:

- **Tools present:** `pahole` cross-check and `abidiff` on a binary boundary.
- **Tools absent:** simulated by an empty `PATH` override. The result must be a `not_checked`
  entry, with no finding and no error.

### 10.6 Order of work

1. Module-set change detection.
2. Bigger held-out denominators (new configurations, oracles first).
3. DOGFOOD-3 baseline.
4. P2, in the §9.5 order: syscall-header check, then functions / typedefs / function-pointer
   flow, then macros / enums / structs, then DT macros, then the optional tools.

Per-side SDK support stays queued after P2.
