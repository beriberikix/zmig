# DESIGN-P2: public API/ABI diff, intersected with the project's call sites

Status: **draft for review**. Nothing here is implemented yet.

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

## 9. Questions for review

1. **Dependency:** is the `libclang` PyPI wheel acceptable as an optional extra, or do you
   want system libclang through the `clang` bindings?
2. **Scope of headers:** `include/zephyr/**` only, or also the public headers of modules
   the build uses (`modules/hal/*`, mbedtls, …)? The brief says `include/zephyr/**`, and
   the design keeps to that.
3. **Severity of value changes** (§4) for enum constants and object-like macros the project
   uses: risky as proposed, or info unless narrowing?
4. **Installing `abigail-tools` and `dwarves`** to test §6: OK to install with apt?
5. **Order of implementation**, as proposed:
   1. flags and the parse snapshot;
   2. functions, typedefs and the function-pointer flow (G1, ZMK #2);
   3. syscalls (ZMK #5);
   4. macros, enums and structs;
   5. DT macros;
   6. the optional tools.

   Each step is measured on all three projects before the next.
