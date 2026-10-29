# zmig audit (design and implementation)

Read-only audit performed 2026-10-29 against commit `ddf6b2b` (main). No code was modified.
Evidence is cited as `path:line` (repo-relative) or as a command with its output. Tool and
paper names from the brief were verified where marked; items I could not verify are flagged.

Environment used: Linux, Python 3.14.4 (`.venv`), git 2.53, CMake 4.2.3, ninja 1.13.2,
dtc 1.7.2, gcc 15.2 (multilib), clang 21.1 (libclang), pahole 1.31, abidiff 2.9.0; a warm
`~/.cache/zmig` (66 GB: Zephyr mirror with every tag v2.7.0..v4.5.0-rc3, ~1300 build
directories). Coccinelle, Comby, clang-query and `west` on PATH were absent.

---

## 1. Executive summary

**What it is.** zmig builds a Zephyr application at two refs, extracts facts from the real
artifacts of both builds (`.config`, `edt.pickle`, ELF, SPDX, `compile_commands.json`, the
configure log) using each ref's own `kconfiglib`/`edtlib` in a subprocess, intersects the
differences with a regex index of what the app references, cross-checks against the crossed
migration guides, and (with libclang) diffs the declarations the project actually uses. No
LLM is involved anywhere (the 21 "llm" grep hits are all `re.fullmatch`). ~20k lines of
tool code, 7k of tests, 6k of design/evaluation docs, written 2026-10-27..29.

**Effectiveness: real but narrower than the README implies.** On the tuned fixture it
catches 9/9 planted changes. On two *un-tuned* Zephyr samples (v4.1.0 → v4.2.0) it caught
the only two forced source changes upstream itself made (one via libclang at high
confidence, one via the guide at low), and it correctly recovered a missing module. But its
own honest evaluation (`docs/EVALUATION.md:70-76`) shows clean-run strict recall of
0–40 %, precision 39–74 %, and *"actionable was 0 on every clean measurement"*. My runs
reproduce the precision problem: a `breaking/high` finding that is a pre-existing
condition at both refs (§5.2), `risky/high` "header changed" findings that are byte diffs,
and a `breaking/medium` guide match on an enum the app never uses.

**Robustness: mixed, leaning structurally sound.** Zero `if version ==` branches; facts
are mechanical; failure is reported, not hidden. The fragility is concentrated in (a) a
hand-rolled RST reader whose file names, section titles and severity vocabulary are
hard-coded, (b) regexes over Zephyr's CMake/Kconfig log text and `build.ninja`, (c) private
`edtlib` attributes read with silent `getattr` fallbacks, and (d) a regex C/DTS lexer.

**Single biggest risk: trust erosion from high-confidence false positives.** Confidence
is assigned by *evidence source*, not by *claim validity*: `value-not-taken` is HIGH
because it quotes a Kconfig warning, even when the source build printed the same warning
(`zmig/analyzers/kconfig.py:186,222-236`); `header-changed` is HIGH because git says the
file changed (`zmig/analyzers/sbom.py:214-227`); API findings are HIGH even when the TU
was parsed with stub headers (`zmig/analyzers/api.py:375`). A user who sees two wrong
"act-now" items stops reading the report.

---

## 2. Findings table (Part 3 checklist)

Legend — considered: **Y** (in code), **D** (docs/design only), **N** (no trace).
Value: H/M/L/n-a for this tool.

| Item | Considered? | Integrated? | Value here | Smallest next step |
|---|---|---|---|---|
| Migration guides as parseable ground truth | **Y** `zmig/guides.py`, `zmig/analyzers/guide.py` | Core feature. Hand-rolled RST reader (`guides.py:20-23`); every crossed `migration-guide-X.Y.rst` + "Removed/Deprecated" sections of release notes (`guides.py:425,452-457`). Admonition/directive bodies are discarded (`guides.py:398-403`); `:ref:`/`:github:` roles not parsed; severity from a 14-word regex (`guides.py:147-153`); rename direction not checked (`guides.py:284-302`). | H | Glob `migration-guide-X.Y*.rst` and emit `NotChecked` for a crossed release with no guide; keep admonition bodies as nested segments; add a per-entry unit test corpus from real guides (there is one for 3.7 only: `tests/test_guides.py`). |
| `__deprecated` attributes / compiler deprecation warnings | partly **Y** | libclang reads `availability == DEPRECATED` on referenced functions/types (`api.py:480-484`; macros never, `apidiff.py:339`). Buildlog only bumps severity when a new warning message contains "deprecat" (`buildlog.py:379`); `-Wdeprecated-declarations` is never named; `-Werror` is not neutralised, so a deprecation becomes an opaque "build fails" (`buildlog.py:169-171`). Verified: 11 files under `include/zephyr` use `__deprecated` at v4.2.0. | M | Parse `[-Wdeprecated-declarations]` into its own kind; when the target build fails, re-run the failing TUs with `-Wno-error` to harvest warnings. |
| `Kconfig.deprecated` / renamed-removed symbol mechanisms | partly **Y** | Verified in the tree: no central `Kconfig.deprecated`; `config DEPRECATED` in `Kconfig.zephyr:1011` plus per-subsystem files (`lib/posix/options/Kconfig.deprecated`, `modules/mbedtls/Kconfig.deprecated`, `lib/cpp/Kconfig.deprecated`). zmig detects "deprecated" only as `select DEPRECATED` or `DEPRECATED` as a whitespace token of `direct_dep` (`extract/kconfig_dump.py:60-63`, misses `(B \|\| DEPRECATED)`); uses the old symbol's `select` list as a rename source (`kconfig.py:52-55`). `[DEPRECATED]` prompts and `imply` are not used; deprecated symbols the app only *reads* get no finding (`kconfig.py:263-282` is inside the `kconfig_set` loop). | H | Tokenise `direct_dep` properly; also scan prompt text; run the deprecation check for `kconfig_read` symbols. |
| Binding `deprecated:` property | partly **Y** | Only for properties the app's own overlay *sets* (`devicetree.py:265-284`); not for properties C reads, not for node-level/compatible deprecation (e.g. 4.2's `zephyr,native-posix-cpu`). Verified: 7 bindings at v4.2.0 carry `deprecated: true`. `required:` flips are recorded (`extract/edt_dump.py:57`) but never compared (grep `required` in `devicetree.py`: 0). | M | Compare `specs[prop].required/deprecated/type` for every node the app references, at both refs. |
| Helper scripts (`migrate_includes.py`, HWMv2 helpers) | **N** (0 mentions in code, docs, git log) | Verified present in the mirror: `scripts/utils/migrate_includes.py` (v3.7.0+), `board_v1_to_v2.py` (v3.7.0–v4.2.0, gone by v4.4.2), `migrate_sys_init.py`, `migrate_mcumgr_kconfigs.py`, `migrate_posix_kconfigs.py`, `pinctrl_nrf_migrate.py`, `migrate_bindings_style.py` (v4.2.0+). | M | In `zmig fix`, when a guide entry names a `scripts/utils/migrate_*.py`, print the exact invocation against the app copy and offer `--apply` through it. |
| `edtlib` / python-devicetree | **Y** | Used from each ref's tree in a subprocess (`snapshots.py:98-108`, DESIGN §4.3) — the right isolation. But reads private `node._binding.prop2specs` and `node._node` with `getattr(…, None)` fallbacks that silently degrade findings (`extract/edt_dump.py:52-53,69,81-84`). | H | Fail the snapshot loudly (`ArtifactMissing`) when a private attribute is absent; pin an "edtlib shape" probe per ref. |
| `kconfiglib` | **Y** | Same subprocess isolation (`extract/kconfig_dump.py`). Dumps `depends/selects/implies/defaults` (`:77-83`) but the analyzer never compares them (§4 F6). `user_value` promised in DESIGN §5.2 is never used (grep: 0); values come from parsing `.config` text (`snapshots.py:34-48`). Kconfig env is recovered by parsing the `menuconfig` rule in `build.ninja` with an unguarded `argv.index("env")` (`snapshots.py:61`). | H | Diff dependency expressions per app-set/read symbol; use `kconf.syms[x].user_value`; wrap `menuconfig_env` in a `ZmigError` with a `CMakeCache.txt` fallback. |
| Build artifacts (`compile_commands.json`, `.config`, `autoconf.h`, `devicetree_generated.h`, `edt.pickle`) | **Y** except `autoconf.h`/`devicetree_generated.h` (0 code hits) | `.config`, `edt.pickle`, `compile_commands.json`, CMake file API, `domains.yaml`, `zephyr_modules.txt`, ELF/map all consumed (`buildctx.py:43-52`). | n-a (covered) | Nothing; `autoconf.h` adds no information over `.config`. |
| Coccinelle / SmPL, Spinfer | **N** (grep of code, docs and `git log` for coccinelle/spatch: 0; the 16 doc hits were `workflow_dispatch`) | Not a reasoned rejection; simply absent. `zmig fix` is Kconfig-only by approved scope (`docs/P3-FIX-KCONFIG.md:1-4`). | M | Not a prerequisite. If codemods for C are ever in scope, emit a SmPL stub per `function-changed` finding (old/new signatures are already captured). |
| Comby, tree-sitter | **N** | Usage index is single-line regex (`usage.py:80-97`); misses multi-line macros, `DT_INST_*`, `DT_CHOSEN`, strings unstripped for `CONFIG_` tokens (`usage.py:123,130`). | M | tree-sitter-c for the *usage* pass only (not for diffing): resolves the multi-line and string-literal false positives in one step. |
| clang-query / LibTooling via `compile_commands.json` | **Y** (libclang `clang.cindex`, optional extra `zmig[api]`) | `apidiff.py:222-266`: parses every project TU with both refs' flags, GCC→clang flag translation (`:159-204`), stub headers on miss, declaration references filtered to tokens written in project source (`:555-581`). Detects removed/changed functions, typedefs, macros (object-like value/size; function-like arity only), enum values, struct fields, callback type mismatches, deprecations (`api.py:410-517`). Empirically the strongest analyzer (§5.3). Weak spots: always HIGH confidence (`api.py:375`), xtensa triple faked as i686 (`apidiff.py:98`), only target-configure-reachable TUs. | H (done) | Lower confidence per TU when `errors` non-empty or the triple is a stand-in. |
| `scripts/diffconfig`, `olddefconfig` baseline | **N** (0 hits; `scripts/kconfig/diffconfig` exists at every tag) | zmig does its own `.config` diff with scoping by app usage, which is strictly more useful than `diffconfig`. `olddefconfig` as a baseline is not used. | L | None needed; optionally show `diffconfig`-style output in `--all-guide-entries`-like verbose mode. |
| kmax/kclause, KconfigReader/TypeChef, Kismet (unmet-dependency reasoning) | **N** | **The specific question — a symbol that still exists but whose `depends on`/`select` changed meaning — is not detected.** The data is in both snapshots (`extract/kconfig_dump.py:77-79`) but `kconfig.py` never compares `ok[sym]["depends"]` with `nk[sym]["depends"]` (only used as action text, `:258`). `dependency_chains` walks the *old* tree only (`kconfig.py:119,126`), so a dependency the new tree *added* is invisible unless it already changed a resolved value. What exists: the target's own "assigned y but got n" warning (`diagnostics.py:36-42`) and a resolved-value compare (`kconfig.py:248-262`). | H | Quick win: string-diff `depends/selects/implies` for app-set/read symbols → `dependency-expression-changed` (medium). Structural: feed both trees' expressions to a SAT check (kclause-style) restricted to the app's roots. |
| libabigail (`abidiff`), abi-compliance-checker, `pahole`/DWARF for settings/NVS/retained structs | partly **Y** | `abidiff` only when a prebuilt `.a` from the project tree is linked or `CONFIG_LLEXT=y` (`abitools.py:56-71`; LLEXT always NotChecked, `api.py:601-610`). `pahole -C` cross-checks offsets of fields the project accesses against clang's model; a disagreement becomes a NotChecked, never a finding (`api.py:577-587`). **Persisted-struct layout (settings/NVS/retained) is not DWARF-based**: it is a text diff of seeded `struct`/`#define` definitions via `git grep` (`zmig/srcdiff.py`, `zmig/data/hazard_seeds.yaml`). DWARF is never read directly. abi-compliance-checker: 0 hits. | M | For every struct passed to `settings_save_one`/`nvs_write`/`zms_write`/retained APIs (already findable through the libclang references), run `pahole -C` on both ELFs and diff member offsets/sizes; that converts the text heuristic into a measured check. |
| Footprint deltas (`ram_report`, `rom_report`, `zephyr.map`) | **Y** (own implementation) | pyelftools over section flags with per-symbol deltas (`size.py:24-47`); risky above +1 KiB **and** +5 % (`buildlog.py:38-39,440-443`); host boards flagged `host-runtime-inflated`. `ram_report`/`rom_report` not used (0 hits). | L (adequate) | Attribute deltas to files via the map file so the top symbols say *which Kconfig/module* grew. |
| Devicetree validation (`dtc` warnings, `dt-validate`/`dtbs_check`, binding-YAML diff) | partly **Y** | Zephyr's own edtlib errors are parsed (`diagnostics.py:48-51,67`), but only the undeclared-property case is mapped to an overlay line (`buildctx.py:737-744`); any other `devicetree error:` stalls salvage after round 1. Binding YAML is never diffed directly; everything comes through the pickle (`extract/edt_dump.py:13,65-66`). `dt-validate`/`dtbs_check` are Linux-kernel tools, not applicable to Zephyr bindings (verified: not in the Zephyr tree). | M | Diff `dts/bindings/**/*.yaml` between refs for compatibles the app's nodes use: property removed/renamed/type/required/deprecated — cheap, versionless, and covers nodes the board DTS sets. |
| Dig & Johnson 2006 (most breaking changes are refactorings) | **N** | Design implicitly agrees (rename detection in Kconfig, guide, API) but there is no refactoring taxonomy; renames beyond one hop are not composed (`kconfig.py:68-78`). | M | Represent every detected rename as `(kind, old, new, release)` and close transitively against the target symbol table. |
| Padioleau/Lawall et al. (collateral evolutions, Coccinelle) | **N** | See Coccinelle row. | L | — |
| Brito et al. (APIDiff), Raemaekers et al. (semver vs breaking) | **N** | The API analyzer's change kinds are an ad-hoc version of APIDiff's catalogue; no explicit mapping, no "breaking vs non-breaking" classification beyond title wording. Zephyr has no semver guarantee, so Raemaekers is low value. | L | Adopt APIDiff's kind names in `api.py` so results are comparable with literature. |
| API-mapping mining (MAM, SemDiff: infer old→new from in-tree callers) | **N** | Rename candidates come from guide text, `select` chains, help-text mentions and prompt equality (`kconfig.py:52-82`); there is no mining of how samples/tests/drivers adapted between refs even though the mirror has full history and `plan` already uses `git patch-id`/`merge-tree`. | **H** | For each `function-removed`/`symbol-removed` finding, `git log -S<old> --diff-filter=M old..new -- samples tests drivers` and extract the most frequent replacement token from those diffs; attach as a medium-confidence candidate. |
| RefactoringMiner-style rename/move detection on the Zephyr tree | **N** | `git diff -M` renames are not used for headers/bindings/boards; board renames come from `deprecated.cmake` (`zmig/boards.py`), header moves only from guide text. | M | `git diff -M --name-status old..new -- include dts/bindings boards` as a mechanical rename source for headers, bindings and board dirs. |
| LLM version-aware benchmarks (VersiCode, CodeUpdateArena, MigrationBench) | **N** (no LLM in the tool) | Development used Claude (every commit is `Co-Authored-By`), but the product is deterministic; README states "never from a model". | n-a | If an LLM is ever added for *explanations*, keep `report.json` as the only input and never let it emit findings. |

### Architecture-pattern comparison (change database → project facts → match)

| Pipeline stage | zmig today | Divergence |
|---|---|---|
| 1a. Parsed migration guides | Yes, at run time per scan, from the target tree (`guides.py:437-472`). | Not persisted or versioned; re-parsed on each run; no way to correct a mis-parse without editing Zephyr's RST (§4 F9). |
| 1b. Mechanical diffs: Kconfig symbol table | Yes, implicitly, via both builds' resolved `.config` + metadata (`kconfig.py`). | Diff is of *this build's* resolution, not of the symbol table; symbols the app doesn't touch are bulk INFO. Dependency expressions not diffed. |
| 1b. Binding YAML | No direct diff (pickle only). | Missed `required`/type changes. |
| 1b. Public headers (Doxygen XML / clang) | Yes, clang, but *use-scoped*: only declarations the project references are compared (`apidiff.py:298-315`). | There is no standalone "what changed in the public API between 4.1 and 4.2" database; nothing is reusable across projects (per-TU cache keyed by file hashes, `api.py:989-1008`). |
| 1b. Board/SoC renames | `boards/deprecated.cmake` walk across intermediate tags (`boards.py:56-61`). | Good; mechanical. |
| 1c. Mined in-tree adaptations | **Absent.** | Biggest structural gap (see MAM row). |
| 2. Project facts | `compile_commands.json`, `.config`, `edt.pickle`, ELF `nm -u`, ninja deps, libclang references. DWARF: no. | Strong. The regex usage index is the weak link. |
| 3. Match with confidence | Yes; `confidence` + `heuristics[]` + additive `tier` (`zmig/tiers.py`). | Confidence reflects evidence *source*, not claim *validity* (§1 risk). |

Verdict on the pattern: zmig implements stages 2 and 3 well and stage 1 as a transient,
per-run, use-scoped computation instead of a persistent change database. Making stage 1 a
versioned artifact (per release pair, in the repo, diffable, correctable) is the single
structural change that would address data-vs-code, rule contribution without Python,
chained renames, and cross-project reuse at once.

---

## 3. Coverage analysis (migration-guide sampling)

Source: `doc/releases/migration-guide-4.2.rst` at v4.2.0 (817 lines, read in full) plus three
entries from `migration-guide-3.7.rst`. Classification is against the code as of `ddf6b2b`,
assuming a successful target configure (the API analyzer needs the target's
`compile_commands.json`; `docs/EVALUATION.md:118` notes a failed target configure "blinds
most analyzers", which is the common case in real upgrades).

**D** = detected today (cite the mechanism); **M** = detectable with modest work;
**N** = fundamentally not detectable statically.

| # | Guide entry (abridged) | Class | Basis |
|---|---|---|---|
| 1 | HWMv1 board support removed (`mg-4.2:23`) | M | Target prints "No board named" → `board-invalid` diag (`diagnostics.py:68`), but the finding does not say *why*; a check for `board.yml` under `BOARD_ROOT` would. |
| 2 | Nordic boards default to `nrfutil` runner (`:33`) | N | Flash-tooling/host behaviour; no build artifact. |
| 3 | nRF54L default `--erase-mode` (`:43`) | N | Runtime of `west flash`. |
| 4 | `CONFIG_NATIVE_POSIX_SLOWDOWN_TO_REAL_TIME` deprecated → `_SIM_` (`:52`) | D | `symbol-deprecated` (`kconfig.py:263-272`) if the app sets it; guide role match otherwise. |
| 5 | `zephyr,native-posix-cpu` binding deprecated (`:55`) | M | Node/compatible-level deprecation is not checked; only overlay-set property deprecation (`devicetree.py:265-284`). |
| 6 | `neorv32` → `neorv32/neorv32/up5kdemo` (`:61`) | D | Board analyzer via `deprecated.cmake`, else `list_boards.py` fallback at medium (`board.py:77-102`). |
| 7 | DAI/DMA/eSPI/SPI DT properties `foo_bar` → `foo-bar` (`:123-137,174-181,336-337`) | D | Overlay sets old name → Zephyr `not declared in 'properties:'` → `property-undeclared` BREAKING/HIGH (`devicetree.py:100-117`); C reads → `read-property-gone`. |
| 8 | `#include <common/x.dtsi>` moved (`:142`) | M | Generic `dt-error` is reported but not mapped to the overlay line (`devicetree.py:118-127`); salvage stalls. |
| 9 | `DT_ENUM_HAS_VALUE` now checks all array values (`:149`) | N | Function-like macro body semantics; only arity is compared (`apidiff.py:724-730`). |
| 10 | `ETH_STM32_HAL_MII` removed; use `phy-connection-type` (`:196`) | D | `symbol-removed` + guide rename link. |
| 11 | `st,stm32-ethernet` now **requires** `phy-handle` (`:199`) | M | `required` is in the snapshot (`edt_dump.py:57`) and never compared. Also surfaces as a configure error, but without the cause. |
| 12 | `ETHERNET_DSA_MASTER_PORT` enum renamed (`:214`) | D (with FP) | `enum-removed` if referenced. Observed FP: a `breaking/medium` guide match because the app merely includes `ethernet.h` (§5.3). |
| 13 | `LINK_HALF_10BASE_T` … enum renames (`:218`) | D | `enum-removed` via libclang when referenced. |
| 14 | `phy_configure_link` gained `flags` (`:238`) | D | `function-changed` (canonical type differs, `api.py:792-796`). |
| 15 | `struct net_linkaddr` pointer → array; `addr == NULL` checks break (`:564`) | D/partial | `field-changed` on type if the field is referenced; the semantic "your NULL check is now always false" is not stated. |
| 16 | `TLS_CREDENTIAL_SERVER_CERTIFICATE` → `PUBLIC_CERTIFICATE` (`:575`) | D | Observed: guide match at `src/echo-server.c:156`, low confidence; `enum-removed` HIGH if the TLS code is compiled. |
| 17 | `mqtt_disconnect` gained `param` (`:579`) | D | `function-changed`. |
| 18 | `AF_PACKET/SOCK_RAW/IPPROTO_RAW` no longer supported (`:584`) | N | Runtime `socket()` failure; argument constants are not an API shape change. |
| 19 | `CONFIG_NET_ZPERF` no longer includes the server (`:594`) | M | Shows only in the `newly-set`/`no-longer-set` INFO bulk unless the app reads the symbol; a "default of a symbol the app *enables a parent of*" rule would promote it. |
| 20 | `http_response_cb_t` now returns `int` (`:618`) | D | `callback-type-changed` from clang's incompatible-pointer diagnostic (`api.py:486-511`). |
| 21 | `net_mgmt_event_handler_t` event type `uint32_t` → `uint64_t` (`:625`) | **D (observed)** | `callback-type-changed` + `function-changed`, both HIGH, at `src/echo-server.c:84,190` in the echo_server run. |
| 22 | `dns_resolve_reconfigure` requires a source argument (`:646`) | D | `function-changed`. |
| 23 | `smf_set_handled` removed; run actions return `smf_state_result` (`:770`) | D | `function-removed`; CANnectivity evaluation shows the link error is reported (`docs/EVALUATION.md:38`). |
| 24 | hawkBit `device_id` no longer prepends `CONFIG_BOARD` (`:779`) | N | Runtime behaviour; guide gating correctly demotes it (`analyzers/guide.py:100-129`). |
| 25 | Cortex-M needs the `CMSIS_6` module (`:790`) | D | `module-required` with manifest line (`zmig/modulecheck.py`), observed in the evaluation. |
| 26 | `CONFIG_DEBUG_INFO` → `CONFIG_X86_DEBUG_INFO` (`:815`) | D | `symbol-removed`; the fixer correctly refuses the rename on ARM (`docs/P3-FIX-KCONFIG.md`). |
| 27 | 3.7: `version.h` → `<zephyr/version.h>` | D (observed) | Guide header match, low, `src/main.c:4`. |
| 28 | 3.7: CAN `bus-speed` deprecated | D (observed) | `property-deprecated` HIGH + guide match. |
| 29 | 3.7: `CAN_AUTO_BUS_OFF_RECOVERY` renamed **and inverted** | D/partial (observed) | `symbol-removed` with the right replacement, but the inversion of meaning is only in the quoted guide sentence; `zmig fix` correctly refuses to auto-apply it. |

**Tally (29 sampled):** detected today 18 (62 %), of which 4 are partial or have an
observed false-positive twin; detectable with modest work 6 (21 %); not statically
detectable 5 (17 %). Caveats: the "D" API items require (a) the `zmig[api]` extra and
libclang, (b) a target configure that succeeds (or is salvaged) so `compile_commands.json`
exists, and (c) the symbol to be referenced in project code, not only in a `#if` or a
string. The sample is biased toward the Networking section because that is what my runs
exercised; board/SoC-vendor entries are mostly "D if the app sets it in an overlay",
which out-of-tree boards often do not.

---

## 4. Fragility register

Severity: **H** = can silently remove or falsify findings; **M** = wrong severity/advice or
noisy; **L** = maintenance cost only.

| # | Fragility | Evidence | Sev | Mitigation |
|---|---|---|---|---|
| F1 | `value-not-taken` is BREAKING/HIGH from the *target* configure warning alone; the source's identical warning is never consulted. | `kconfig.py:182` checks only `sym in ok`; `:186` `d = diags.get(sym)` is from `new_raw_facts`; `:222-236`. Observed in both settings runs (§5.2); source log `~/.cache/zmig/builds/cb3aab5716941570/zmig-logs/settings.log:20` has the same warning. | **H** | Require the diagnostic to be *new* at the target (diff against the source configure's diagnostics, which `parse_log` already produces). |
| F2 | Private `edtlib`/`dtlib` attributes with silent `getattr` fallbacks. | `extract/edt_dump.py:52-53` (`_binding`), `:69,81-84` (`_node`). Missing → `raw_values={}` → written-property changes degrade to INFO `node-binding-defaults` (`devicetree.py:198-207,247-259`); `property-deprecated` never fires. No status, no NotChecked. | **H** | Raise `ArtifactMissing` or set a `degraded` flag the analyzer reports; add a one-line probe per ref. |
| F3 | Kconfig environment recovered by parsing `build.ninja`'s `menuconfig` rule. | `snapshots.py:51-71`: `split(" && ")`, `shlex.split`, unguarded `argv.index("env")`, `argv[-1]` as Kconfig root, no ninja `$$` un-escaping. A `ValueError` is not a `ZmigError` and escapes `run_build_analyzers` (`analyzers/__init__.py:170-174`). | **H** | Catch and convert; fall back to `CMakeCache.txt`; verify the parsed root exists. |
| F4 | Guide reader: hard-coded file names, section titles, and directive skipping. | `guides.py:452-457` (`migration-guide-{v}.rst`), `:425` (`^(removed\|deprecated)`), `:398-403` (every top-level directive body dropped, incl. `.. note::`). A format change yields "Read N entries from 0 documents" (`analyzers/guide.py:221-242`), status still `ok`. | **H** | Glob; `NotChecked` per crossed release with no guide; keep admonitions. |
| F5 | Log-text regexes with no "configure failed but nothing parsed" detection. | `diagnostics.py:29-71`, `buildctx.py:313` (sysbuild banner). Non-matching error → zero diagnostics → salvage breaks after round 1 (`buildctx.py:879`) → `.config` missing → kconfig/devicetree "skipped". | M | When configure fails and zero diagnostics parse, emit a BREAKING finding with the last error lines. |
| F6 | Dependency-expression changes not compared; choices ignored. | `kconfig.py` never reads `ok[sym]["depends"]`/`selects`/`implies` (only `:258` as text); grep `choice` in `kconfig.py`: 0. `dependency_chains` walks the old tree only (`:119,126`). | M | Diff the three expression strings per app symbol; add choice-selection comparison. |
| F7 | `read-symbol-removed` BREAKING/HIGH is not filtered by `build_files`; `outside_build` ignores `.c/.h`. | `kconfig.py:284-300` vs the DT path which filters (`devicetree.py:131-138`); `analyzers/__init__.py:199` suffix list. | M | Apply the same filter; extend suffixes. |
| F8 | `header-changed` is a byte diff of the header. | `sbom.py:214-227` (`git diff --numstat` non-empty → RISKY/HIGH). The author's own data: bms A1 precision 50 % → 11 % (`docs/EVALUATION.md:91`). Observed: 3 of 10 risky findings in the fixture, 1 of 2 in settings. | M | Downgrade to INFO when the API analyzer parsed the including TUs and found no change in referenced declarations. |
| F9 | Migration knowledge is not data. | No rule file. Renames exist only as transient parse results (`guides.py:50-56`); `kconfig.py:68-78` does not chain A→B→C. Overridable data is limited to `zmig/data/releases.json` (`hops.py:27-35`); `hazard_seeds.yaml`/`wire_format.yaml` are bundled. | M | Persist a per-pair `changes.json` (guide + mechanical + mined) under `zmig/data/pairs/` with a schema; load user overlays from `--changes`. |
| F10 | Confidence is per evidence source, never degraded by parse quality. | `api.py:375` HIGH unconditionally; stubbed headers recorded as errors (`apidiff.py:258-266`) but findings still ship; pahole disagreement → NotChecked only (`api.py:577-587`); xtensa triple faked (`apidiff.py:98`). | M | Per-TU confidence cap. |
| F11 | Regex C/DTS usage lexer. | `usage.py:80-97`; strings not stripped for `CONFIG_`/`DT_` passes (`:123,130`); no `DT_INST_*`, `DT_CHOSEN`, multi-line properties (`:146,183`), `/delete-node/`. | M | tree-sitter or libclang token stream for the usage pass. |
| F12 | Rename recommendation ranking is alphabetical after guide hits; help-text match is a bare word match over all new symbols. | `kconfig.py:56-65,189-196`. | M | Rank by confidence, then prompt/type similarity. |
| F13 | Salvage only for the target and only for the app's own files; `-Werror` not handled. | `cli.py:437` (target only); `buildctx.py:726-734` (fragment outside app → no edit); `buildlog.py:169-171`. | M | Salvage the source too (so "already broken at source" is distinguishable from "broken by the migration"); re-run failing TUs with `-Wno-error`. |
| F14 | Unconditional per-version workarounds (not branches, but drift risk). | 11 sites: `extract/kconfig_dump.py:14-16,25-26`; `edt_dump.py:52-58,69,73,88`; `snapshots.py:185-189` (`class ZephyrInitLevels` probe); `boards.py:56-61`; `buildctx.py:38,476-484,487-494,893-903,104-111`; `diagnostics.py:34-42,98-100`. All feature-probed or harmless if the condition vanishes. | L | Keep; add a test matrix job that runs the extractors against every release tag's scripts. |
| F15 | Stale public contract. | `README.md:210-211` says `watch`, API/ABI diffing and MCUboot checks are "not implemented yet"; all three exist (`zmig/watch.py`, `zmig/analyzers/api.py`, `zmig/analyzers/mcuboot.py`) and are in `zmig --help`. | L | Fix the README; add a doc test that `--help` subcommands appear in README. |

**Per-version special cases (counted).** Explicit `if version == …`/ref comparisons in
`zmig/`: **0** (the only version tuple compares are `MIN_PYTHON`, `doctor.py:28`, and the
report's own `schema_version`, `verdict.py:343`). Release knowledge outside code:
`zmig/data/releases.json` (LTS list 1.14/2.7/3.7, one boundary "3.7 HWMv2",
`max_minor_gap=2`), `hazard_seeds.yaml` (31 seeds), `wire_format.yaml` (12 entries).
Trend: the design does not grow a branch per release; what grows per release is the
*regex vocabulary* when Zephyr changes a log line or RST idiom (F4, F5), which is harder to
see coming than a version branch.

**Hand-curated vs mechanical.** Facts (which symbols/headers/declarations changed, what
the app uses, sizes, warnings) are ~90 % mechanical. Interpretation (what is breaking,
rename detection, gating words, stoplists, triple table, thresholds) is ~100 % hand-written:
116 compiled regexes in `zmig/`, ~10 numeric thresholds, 2 bundled data files. The
maintenance cost per Zephyr release is paid by the zmig author, in Python, and is only
visible when a dogfood run goes wrong.

**Version-pair scaling.** Guides are composed per crossed minor (`guides.py:446-448`),
Kconfig/DT/API/SBOM are a single old-vs-new diff (correct for facts, since the build at
the target is ground truth). Chained renames are **not** composed: A→B (3.7) then B→C (4.1)
reports "renamed to B" (`kconfig.py:68-78`). Large gaps get a hop plan (`hops.py:123-140`).

**Failure modes (observed).** Nonexistent ref → clear error, exit 1. Nonexistent app →
clear error, exit 1. Nonexistent board → runs, reports configure-failed, verdict
`unknown`, exit 0 (arguably should be a usage error). Missing module at *both* refs →
only the target is recovered and the gap is reported as a migration finding (§5.2).
A source build that fails leaves the kconfig/devicetree analyzers running on configure
outputs but `initorder`/`sbom` skipped with reasons — reported, not hidden.

**Bus factor and extensibility.** Analyzer protocol + entry-point registry are clean
(`analyzers/__init__.py:47-67,118-124`); `AnalyzerResult` carries `not_checked`/`checked`,
and `run_build_analyzers` turns missing artifacts into `skipped` with reasons (`:157-168`).
Adding a fact extractor means adding an `Artifact` and a `snapshots.py` accessor; adding a
rule type means a new `kind` string with no schema registry (kinds are free-form strings in
`findings.py`). The design docs are unusually complete (DESIGN, P1, P2, WATCH, TIERS,
ORACLE-RULES), which offsets the three-day authorship. Bus factor is 1.

### Verdict on robustness

**Mixed.** The *architecture* (build both, extract with the tree's own libraries, diff
artifacts, scope by use, report what wasn't checked) is the right one and is not
per-version. The *implementation* has three structural weaknesses that will each cause
silent quality loss as Zephyr evolves: (1) migration knowledge is computed per run from
regex over prose and never persisted, so it cannot be corrected, reviewed, or composed
(F4, F9); (2) confidence is a property of the evidence channel rather than of the
claim, so wrong findings arrive labelled HIGH (F1, F8, F10); (3) the text/regex layers
(RST reader, log parser, C/DTS lexer, `build.ninja` parser) fail by returning less
rather than by failing (F2–F5, F11).

The two or three structural changes with the most leverage:

1. **Make the change database a first-class, versioned artifact** per release pair
   (guide entries + mechanical diffs of symbol table/bindings/headers/boards + mined
   in-tree adaptations), generated by a `zmig build-changes v4.1.0 v4.2.0` command and
   checked in with golden tests. Scans then *match* rather than *parse*.
2. **Re-derive confidence from claim validity**: a finding is HIGH only if the same
   check at the source ref says "fine" and the target says "broken" (F1), the API parse
   had no errors (F10), and the evidence is in a file the build used (F7).
3. **Fail loudly in every text layer**: no `getattr(…, None)` on private attributes,
   no `skipped` without a BREAKING "could not analyze" finding when the cause was a
   parse miss (F2–F5).

---

## 5. Empirical results

All runs with `.venv/bin/zmig` at `ddf6b2b`, outputs under `$CLAUDE_JOB_DIR/tmp/run-*`
(`report.json`, `MIGRATION.md`, `logs/`). Unit tests: `pytest -m "not build and not
network"` → **229 passed in 5.93 s**; 57 build/network/SDK tests deselected by default
(`pyproject.toml:[tool.pytest.ini_options]`).

### 5.1 Bundled fixture, v3.6.0 → v3.7.0, `native_sim_64` (tuned by construction)

4 s (cached builds). 29 findings: 7 breaking, 10 risky, 12 info. All 9 planted changes
(`tests/fixtures/app/README.md`) were found, with correct board mapping
(`native_sim_64` → `native_sim/native/64`) and salvage of the two fatal `prj.conf` lines.

Noise: 3 of 10 risky findings are `header-changed` byte diffs (`kernel.h`, `can.h`,
`devicetree.h`); 2 `dependency-changed` findings (`CONFIG_DEPRECATED`, `CONFIG_CPP`) restate
the already-reported removal of `CPLUSPLUS`; 3 guide findings duplicate Kconfig/DT findings
(they are cross-linked, which helps, but they count separately in the headline numbers).

### 5.2 Zephyr `samples/subsys/settings` (from the v4.1.0 tree), v4.1.0 → v4.2.0, `native_sim` (un-tuned)

Oracle (upstream's own diff of the sample, `git diff v4.1.0 v4.2.0 -- samples/subsys/settings`):
no forced change for `native_sim`; upstream switched the sample's native_sim storage from
littlefs-on-file to NVS as a simplification.

*Run 1* (without `--module littlefs`, my error): both source and target builds failed on
`lfs.h` (`logs/cmd-011.log:31`, `cmd-014.log:31`). zmig recovered **only the target** by
adding `littlefs` (`zmig/modulecheck.py`), then reported "The target build needs module
littlefs" as **breaking/act-now** and compared a failed source against a repaired target.
The human summary's header said "target build: ok (with littlefs added)"; that the source
build had failed was visible only in two `!` lines at the bottom. **Mis-attribution**: an
invocation gap at both refs was reported as a migration requirement.

*Run 2* (with `--module littlefs`): 13 s. 15 findings: 1 breaking, 2 risky, 12 info.

| Finding | Verdict | Evidence |
|---|---|---|
| `CONFIG_STDOUT_CONSOLE=y no longer takes effect in v4.2.0 (it gets n)` breaking/high/act-now | **False positive** | Kconfig definition identical at both refs (`lib/libc/Kconfig:156` vs `:160`); source configure emitted the same warning (`builds/cb3aab5716941570/zmig-logs/settings.log:20`); `.config` lacks the symbol at both refs. Root cause F1. |
| `<zephyr/settings/settings.h> changed upstream (+51/-9 lines in 4 commits)` risky/high | Unverifiable/noise | Byte diff (F8); the API analyzer parsed the TU and reported no changed declaration. |
| `Module littlefs moves … 1 file compiled changed` risky/high | True but harmless | Module repin. |

Precision on breaking/risky: **0/3**. Recall: n-a (no forced items). The storage analyzer
correctly produced two INFO `backend-options-changed` findings.

### 5.3 Zephyr `samples/net/sockets/echo_server` (from the v4.1.0 tree), v4.1.0 → v4.2.0, `native_sim` (un-tuned)

Oracle (upstream diff of `src/` and `prj.conf`): two forced changes — the net_mgmt handler
parameter `uint32_t` → `uint64_t` (`src/echo-server.c`) and
`TLS_CREDENTIAL_SERVER_CERTIFICATE` → `PUBLIC_CERTIFICATE`; the rest is sample
restructuring (tunnel.c/vlan.c moved to a shared helper).

23 s. Target build failed at v4.2.0 (expected). 19 findings: 7 breaking, 3 risky, 9 info.

| Finding | Verdict |
|---|---|
| `callback-type-changed` net_mgmt handler, breaking/high, `src/echo-server.c:84` | **True, caught, exact line** (libclang) |
| `function-changed net_mgmt_init_event_callback`, breaking/high, `:190` | True (same root cause) |
| `target-build-failed`, breaking/high, `:191` with the compiler message | True |
| `macro-changed NET_EVENT_L4_CONNECTED/_DISCONNECTED` (4→8 bytes), risky/high ×2 | True (same root cause; 4 findings for one change) |
| Guide `net_mgmt_event_handler_t` signature, breaking/low, `src/tunnel.c:144,156` | Duplicate of the above, pointing at a file that is **not compiled** in this build (tunnel.c needs `CONFIG_NET_L2_IPIP`) |
| Guide/RN `TLS_CREDENTIAL_SERVER_CERTIFICATE` ×2, breaking/low, `src/echo-server.c:156` | True rename; the code is inside `#if defined(CONFIG_NET_SOCKETS_SOCKOPT_TLS)`, not compiled in this build. Useful, but should not be "breaking" for this build |
| Guide `ETHERNET_DSA_MASTER_PORT` rename, **breaking/medium**, `src/tunnel.c:12, src/vlan.c:12` | **False positive**: matched because those files `#include <zephyr/net/ethernet.h>`; the enum is never used, and the files are not compiled |
| `dependency-changed CONFIG_POSIX_MESSAGE_PASSING` (via `POSIX_API`), risky/medium | Unverifiable; no observable effect on the sample |
| `ETH_NATIVE_POSIX` guide match in `overlay-nsos.conf` | Correctly demoted to INFO by `outside_build` |

Strict recall on the two forced items: **2/2** (one HIGH, one LOW). Precision on the 10
breaking/risky findings: 5 confirmed, 3 duplicates of confirmed, 1 unverifiable, 1 false
positive → **5/10 strict, 8/10 lenient**. The report's "Do these first" list leads with
the correct item.

### 5.4 `zmig plan` on the generated fork workspace, v3.6.0 → v3.7.0

`tests/fixtures/make_fork.py` (23 s) then `plan` (4 s). All five synthetic commits were
classified as `expected.json` specifies: 1 conflict (`kernel/Kconfig`), 2 apply cleanly
(one with an overlap note), 1 upstreamed by patch-id (high), 1 upstreamed by subject +
content similarity 0.50 (medium). Uncommitted `README.rst`, the untracked `segger` copy
differing in one file, and the unrecorded `tinycrypt` fork were all reported. No errors.
This is the most reliable part of the tool: it rests on git object identity.

### 5.5 Author's own evaluation (verified it exists; numbers not re-run)

`docs/EVALUATION.md` and five DOGFOOD reports with pre-registered oracles
(`docs/ORACLE-RULES.md` §1.4 freezes them before the run). Clean results: Golioth strict
3/8, bms 4/10, CANnectivity 2/6, OpenDeck scan 0/5, OpenDeck plan 6/9; precision 39–74 %;
actionable 0 on every clean measurement. The ceiling analysis (`EVALUATION.md:81-98`) is a
genuinely good practice. The evaluation harness itself (`tests/test_dogfood_regressions.py`,
`tests/test_opendeck_regressions.py` with a checked-in 47-finding report) requires SDKs and
network, so it does not run in the default `pytest`; detection quality can regress without
a default-suite failure.

---

## 6. Prioritized recommendations

Ordered by impact ÷ effort. **QW** = quick win (hours–day), **ST** = structural.

1. **QW — Diagnostics must be *new* at the target.** For `value-not-taken`,
   `not-configurable`, `property-undeclared`, require absence of the same diagnostic in the
   source configure log (`kconfig.py:186`, `devicetree.py:100-127`). Removes the observed
   HIGH false positive class (F1).
2. **QW — Salvage/recover the source build too**, and when a module or line is needed at
   *both* refs, report it as "already required at `--from`" (info), not as a migration
   finding (§5.2 run 1; `cli.py:437`, `modulecheck.py`).
3. **QW — Demote `header-changed` to INFO** when the API analyzer covered the including
   TUs without errors and found no changed referenced declaration (`sbom.py:214-227`).
   The author's own data shows this is the main precision sink (`EVALUATION.md:91`).
4. **QW — Apply the compiled-file filter to Kconfig reads and guide identifier matches**
   (`kconfig.py:284-300`, `analyzers/guide.py:147-152`), and extend `outside_build` to
   `.c/.h` (`analyzers/__init__.py:199`). Fixes the `tunnel.c`/`vlan.c` class in §5.3.
5. **QW — Diff `depends`/`selects`/`implies` strings** for every app-set/read symbol
   (`extract/kconfig_dump.py:77-79` already dumps them) → `dependency-expression-changed`
   medium. Answers the audit's Kismet question for the cheap 80 %.
6. **QW — Compare binding `required`/`deprecated`/`type`** for properties of referenced
   nodes (`extract/edt_dump.py:57`), and diff `dts/bindings/**/*.yaml` for the app's
   compatibles. Closes guide items #5, #11.
7. **ST — Persist the change database per release pair** (`zmig/data/pairs/<from>-<to>.json`
   or generated into the cache with a schema): guide entries with corrected severity, Kconfig
   symbol-table diff, binding diff, header rename list (`git diff -M`), board renames. Scans
   match against it; contributors fix a mis-parsed entry by editing JSON, not Python (F9);
   chained renames compose by construction.
8. **ST — Mine in-tree adaptations** for replacement candidates: for each removed function,
   macro, or Kconfig symbol, extract the dominant replacement token from upstream commits
   that touched callers in `samples/`, `tests/`, `drivers/` between the refs (the mirror and
   `git patch-id` plumbing already exist in `forks.py`). This is the MAM/SemDiff idea and the
   most direct route to raising "actionable" above zero.
9. **ST — Confidence from claim validity, with a per-TU cap** (`api.py:375`,
   `apidiff.py:258-266`) and a rendered glossary of heuristic tags in MIGRATION.md
   (`report/markdown.py:250-254` lists tags without definitions).
10. **ST — Replace the regex usage lexer** with a tree-sitter-c (or libclang token) pass for
    `CONFIG_`/`DT_` references: strips strings/comments correctly, handles multi-line calls,
    and enables `DT_INST_*`/`DT_CHOSEN` resolution through `matching_compat`/`chosen`
    (already dumped at `extract/edt_dump.py:87-89`, unused).

Housekeeping (not ranked): fix README's "not implemented yet" line (F15); make
"unknown board" a usage error instead of exit 0; add a CI job that runs
`extract/kconfig_dump.py`/`edt_dump.py` against every release tag's scripts (F14).

---

## 7. Open questions for the author

1. **Is a persistent change database out of scope by decision, or not yet reached?**
   DESIGN §12 records decisions on fixtures, worktrees, and build mode but says nothing
   about where migration knowledge should live; `--release-data` is the only override.
2. **Why is `value-not-taken` not compared against the source diagnostics**, given that
   `parse_log` already runs on the source log for warnings (`buildlog.py:329-340`)? Was the
   asymmetric salvage (target only, `cli.py:437`) a deliberate scope cut?
3. **What is the intended semantics of `confidence` for API findings** when a TU was parsed
   with stub headers or a stand-in triple? `DESIGN-P2.md` discusses parse gaps but the code
   ships HIGH unconditionally (`api.py:375`).
4. **Was `required:`/binding-YAML diffing dropped intentionally?** DESIGN §5.2 promises
   "newly `required`: breaking" and "`user_value` vs `str_value`"; neither is in the code.
5. **How should chained renames be reported** on a 3.5 → 4.2 jump — final target symbol
   only, or the chain? The hop planner suggests intermediate stops, but the Kconfig analyzer
   compares endpoints.
6. **Who runs the SDK/network regression suite, and when?** It is the only place detection
   quality is pinned to real projects, and it is excluded from the default `pytest`.
7. **Is the `plan` fork machinery meant to read a project's patch directory automatically?**
   `EVALUATION.md:122-123` lists it as a known limitation; OpenDeck needed hand-built
   checkouts.
8. **Is there an intended consumer for `report.json` beyond MIGRATION.md/SARIF** (e.g. an
   agent)? The README mentions "for scripts or an agent"; if so, the per-finding
   `old`/`new` payloads are undocumented per kind (DESIGN §3.1 says "each analyzer documents
   them"; none do in-code).

---

## Response (2026-10-29, branch `audit-fixes`)

Appended by the maintainer session; the audit text above is unchanged. Every item was
re-checked against `ddf6b2b` before acting.

### Verification

- **F1–F15:** 13 confirmed. Two are partly right:
  - **F3:** the mechanics are right, but the severity is not. It fails as an uncaught
    `ValueError` traceback, which is loud, not silent. Zephyr's menuconfig rule has the
    `cmake -E env` shape the parser expects. M, not H.
  - **F6:** dependency expressions are read, by `references()`, but never compared. Choice
    membership is dumped and used by sysbuild, storage and mcuboot, not by `kconfig.py`.
- **Citations:** most are within ±3 lines. Wrong locations: F15 is `README.md:219-220`;
  deprecation availability is read in `apidiff.py:303,434`, not `api.py:480-484`.
- **§2 "Kconfig.deprecated" row:** partly right. `split()` does find `B || DEPRECATED`; it
  misses only when `DEPRECATED` touches a parenthesis or `!`.
- **§5.2 and §5.3:** both runs reproduced exactly (15 and 19 findings, same severities).
- **Severity ordering:**
  - F7 should be H. It is the same class as F1: breaking/high findings on code the build
    doesn't compile.
  - F4 is worse than stated: no `migration-guide-*.rst` exists before v3.5, so a 2.7→3.7
    LTS scan reads no guide for 3.0–3.4 and reports `ok`.

### Fixed (one commit each, test first)

| Audit item | Commit | Effect on the §5 runs |
|---|---|---|
| F15 README | f82c644 | — |
| §6.1 / F1 diagnostics new at the target | d5e3561 | settings: `STDOUT_CONSOLE` breaking/act-now → info/fyi |
| §6.4 / F7 uncompiled sources | 54aa423 | echo_server: DSA and `tunnel.c` guide matches breaking → info |
| §6.3 / F8 `header-changed` | d0f9e03 | settings: `settings.h` risky → info; fixture: the 3 `header-changed` → info |
| (new) shared-build path mapping | 5753b7a | see below |
| §6.2 module at both refs (module half) | 49bc2d1 | settings without `--module`: `module-required` breaking/act-now → info; source rebuilt with littlefs |
| §6.6 binding checks (narrowed) | d4181ac | none (native_sim has no overlays) |
| §6.5 dependency expressions (as info) | 22a5405 | echo_server: +1 info; `dependency-changed POSIX_MESSAGE_PASSING` now names its cause |

**Net change** (breaking / risky / info):

| Run | Before | After |
|---|---|---|
| settings `--module littlefs` | 1 / 2 / 12 | 0 / 1 / 14 |
| settings without `--module` | 2 / 1 / 8 | 0 / 1 / 15 |
| echo_server | 7 / 3 / 9 | 5 / 3 / 12 |

echo_server's two forced changes are still caught as before (the net_mgmt callback
breaking/high; TLS rename breaking/low).

**Departures from the audit's proposals:**

- **Pre-existing diagnostics.** These follow `docs/DESIGN-P1.md` §2.3: severity is kept and
  `introduced_by_migration` is false. Two exceptions, now recorded in §2.3:
  - an assignment never taken at either ref is info;
  - a module missing at both refs is info.
- **`header-changed`.** Demoted per including TU: any API finding in an including TU keeps
  the header risky. API findings don't record the declaring header, so a per-header check
  isn't possible.
- **Dependency expressions.** One info finding plus action-text notes, not risky/medium per
  symbol. Within one build, every effective expression change is already a value change.
- **Schema.** No finding ID, fingerprint or `schema_version` changed.

### Found during the fixes, not in the audit

- **Shared builds across app copies.** The build key hashes the app's *content*
  (`buildctx.py` `key_inputs`), but the app copy is `apps/<hash>/<basename>`. Two apps with
  identical trees and different directory names share one cached build. Its records name the
  other copy, so:
  - at `ddf6b2b`, `build_files` came out as an empty set and devicetree findings were silently
    dropped (the fixture lost `node-gone DT_PATH(gpio_800)`);
  - commit 54aa423 widened this to kconfig, guide and API findings.

  5753b7a follows the build's recorded `APPLICATION_SOURCE_DIR` and treats an unmappable set
  as unknown. Still open: phantom `input-added` and `image-added`/`image-removed` findings and
  missing `dependency-changed` findings in that situation. The root fix (the basename in the
  build key) invalidates every cached build once, so it awaits a decision.
- **Buildlog warnings.** The buildlog analyzer keys warnings on their text, including the
  "defined at" line. The `STDOUT_CONSOLE` warning therefore still shows as an info "new
  warning" plus "1 resolved". This is the F1 class in another analyzer, and only info.

### Deferred (each needs a design doc)

- **§6.7** Persisted change database.
- **§6.8** Mining in-tree adaptations.
- **§6.9** Confidence derived from claim validity.
- **§6.10** Replacing the regex usage lexer. It also covers the TLS match inside an `#if`
  that is off, which item 4 cannot catch.
- **F13** Line-level salvage at the source.
- **§6.6, rest** Bindings-YAML diff and compatible-level deprecation.

### Evaluation status

`samples/subsys/settings` and `samples/net/sockets/echo_server` informed these fixes, so
they are **tuned** under the clean-vs-tuned rule in `docs/EVALUATION.md`. EVALUATION.md was
not edited. Rows that may be stale are named in each commit message, chiefly bms A1
precision (d0f9e03). None was re-measured.

### Follow-up (2026-10-29, same branch)

- **Shared builds across app copies:** closed. The build key now includes the app directory
  name (cb90465), and the `APPLICATION_SOURCE_DIR` alias from 5753b7a is removed as
  unreachable. Every cached build misses once.
- **Byte stability.** Cold and warm caches gave different `report.json` text: the libclang
  coverage line reported parse-cache counts. Exposed by the key change and fixed in 61cd355.
- **Buildlog warning noise:** closed in f284f92. A kconfig warning whose "defined at" line
  moved is no longer reported as new plus resolved.
- **F4, partly:** each crossed release with no migration guide (anything before 3.5) is now
  a "not checked" gap, and the guide analyzer is `partial` (1275465). In `watch` that gap
  blocks until it is accepted. Hard-coded file names and section titles, and dropped
  directive bodies, remain open.
