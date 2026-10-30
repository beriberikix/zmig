# DOGFOOD-13: clean baseline on a third new held-out project (edgehog)

2026-10-30. zmig `7c64974` (code as at `6227d2f`). This is the first zmig run on
[edgehog](oracles/edgehog.md). Its oracle was committed and pushed before the run
(`7c64974`); only `--analyzers none` verification builds ran before that.

Scoring follows [ORACLE-RULES.md](ORACLE-RULES.md), including §5.

**This result is clean.** No zmig change was informed by edgehog. It is the first clean
measurement after DOGFOOD-12's E1–E4 and E8 fixes, which were tuned on ble-copro and
proves. Several of its items match patterns that earlier, tuned fixes target:
- removed Kconfig symbols (the change database);
- `NATIVE_UART_0_ON_STDINOUT` (CoffeeCaller C2);
- the partition macros (proves P2).

So this run checks whether those fixes carry over to a project they weren't built on.

Run: `eval/run.py scan --out ~/.cache/zmig/eval/runs/edgehog-baseline edgehog` (41 s).

## 1. Result

| Project | Upgrade | In scope | Strict | Lenient | Precision | Actionable | Fix-actionable | Target build | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| edgehog `e2e` (native_sim) | v4.3.0 → v4.4.0 | 8 | **4 (50%)** | **7 (88%)** | **38/40 (95%)** | **7** | **2** | configure failed (Kconfig, 11 warnings); salvaged build failed compiling | unknown |

## 2. Per item

| Item | Label | Finding, and why | Actionable | Fix-actionable |
|---|---|---|---|---|
| H1 NVS header `fs/nvs.h` → `kvss/nvs.h` (`include/nvs.h:18`, `nvs.c:10`) | **partly (c)** | `BUILD-NEW-WARNING-b1b2ddf6` (risky): "This header file is deprecated, please use zephyr/kvss/nvs.h instead". That's the cause, with the replacement. But it's cited at `nvs.c:7` (`#include "nvs.h"`, the include that reaches it) and `storage_usage.c:9`, not at the lines that include `fs/nvs.h` | yes (`nvs.c`) | no |
| H2 `FIXED_PARTITION_*` → `PARTITION_*` (5 parts) | **partly (d)** | `BUILD-NEW-WARNING-659234c9` (risky), "Macro is deprecated", at `nvs.c:19`, `:20`, `ota.c:47`, `:523`: 4 of 5 parts, at their lines. `ota.c:46` is defined but never expanded, so there's no warning (oracle) | yes | no |
| H3 `MBEDTLS_PEM_CERTIFICATE_FORMAT` replaced (`prj.conf:74`) | **caught** | `KCONFIG-SYMBOL-REMOVED-09e2a297` (breaking) at `:74`, plus two guide matches | yes | **no**: the fix is `PEM_PARSE_C` only. The maintainers added `PEM_PARSE_C`, `PEM_WRITE_C` and `BASE64_C`, and the salvaged build shows `PEM_WRITE_C` is needed (`crypto.c:91`, `mbedtls_pk_write_key_pem`) |
| H4 `SERVER_NAME_INDICATION` → `SSL_SERVER_NAME_INDICATION` (`:77`) | **caught** | `KCONFIG-SYMBOL-REMOVED-a528dfe0` (breaking) at `:77`, with the fix | yes | **yes** |
| H5 removed Mbed TLS symbols (`:69, :82, :85-88`) | **caught** | Six `KCONFIG-SYMBOL-REMOVED` (breaking), one per line, plus guide matches | yes | no: the action offers "remove the assignment, or find its replacement". The maintainers removed them, but the action doesn't commit to it (a judgement call; compare ardep A5a, DOGFOOD-11) |
| H6 add `X509_USE_C`, `X509_CRT_PARSE_C` (after `:77`) | **partly (c)** | `KCONFIG-VALUE-NOT-TAKEN-836f1342`/`-92cd75b8` (breaking) at `:84`/`:83`: the ECDHE key exchanges no longer take effect, and the unmet dependency expression includes `MBEDTLS_X509_CRT_PARSE_C`. The cause is there, but at the dependent lines, and not singled out | yes | no |
| H7 `NATIVE_UART_0_ON_STDINOUT` → `UART_NATIVE_PTY_0_ON_STDINOUT` (`boards/native_sim.conf:6`) | **caught** | `KCONFIG-SYMBOL-REMOVED-52160972` (breaking) at `:6`, with the fix | yes | **yes** |
| H8 `ZVFS_OPEN_MAX` 25 → 26 (`prj.conf:171`) | missed | Only `BUILD-NEW-WARNING-7c6a29d5` (info), "CONFIG_ZVFS_OPEN_MAX is less than requested minimum: 25 < 26", from Zephyr's `lib/os/CMakeLists.txt`, with no project location | | |

**Precision 38/40.**
- **Confirmed (38):**
  - the target build failure (after salvage);
  - nine `symbol-removed` (H3, H4, H5, H7);
  - two `value-not-taken` (H6);
  - two new warnings (H1, H2);
  - seven `dependency-changed`: Mbed TLS dependencies that are gone or changed at the target, `DEPRECATED` behind the UART option, and `NET_SOCKETS_SOCKOPT_TLS` no longer implying its ciphers. All are true and evaluated in this build;
  - 16 Mbed TLS guide matches at the removed or renamed lines;
  - the `NET_SOCKETS_SOCKOPT_TLS` guide match at `:96` ("automatic selection of crypto Kconfigs has been removed": true, and the reason H6's key exchanges fail).
- **Harmless (2):**
  - `GUIDE-ENTRY-MATCH-e95a4298` (OpenThread) at `prj.conf:67`: matched on `CONFIG_PSA_CRYPTO`, and the project doesn't use OpenThread (F5);
  - `API-FIELD-CHANGED-9c7d7f50`, `zsock_addrinfo.ai_addr`, `struct sockaddr *` → `struct net_sockaddr *` at the same offset: the compatibility alias (F4).

## 3. What carried over from the tuned fixes

- **Removed and renamed Kconfig symbols (H3, H4, H5, H7): all caught at their exact
  lines.** H4 and H7 are fix-actionable: the help-text and guide rename sources from
  Phase 0, and the UART rename first fixed for CoffeeCaller C2.
- **E2 (compatibility macros):** of the 13 kinds of false finding on ble-copro, one
  remains here: a field's type spelled through an alias the project never wrote (F4).
- **E3 (guide sections):** the two NXP entries correctly went to info (vendor section,
  native_sim). The OpenThread entry (a subsystem section) did not (F5).
- **E4 (`include-missing`) didn't apply.** The salvaged target's compile errors are in
  Astarte's `crypto.c` (a module) and an Mbed TLS `#error`, not in edgehog's code.

## 4. zmig defects this run exposed (not fixed here)

| # | Defect | Effect here |
|---|---|---|
| F1 | **A CMake warning about a Kconfig value isn't tied to the project's assignment.** "CONFIG_ZVFS_OPEN_MAX is less than requested minimum: 25 < 26" names a symbol the project sets at `prj.conf:171` | H8 missed (only an info finding, no location) |
| F2 | **A deprecated-header warning is cited at the first include in the compiled file**, not at the project line that includes the deprecated header (`include/nvs.h:18`, `nvs.c:10`) | H1 partly (c) |
| F3 | **`value-not-taken` doesn't single out the dependency that's new at the target.** `MBEDTLS_X509_CRT_PARSE_C` is a new, default-off option; it sits in a long expression | H6 partly (c) |
| F4 | **E2's aliases are known only for names the project writes.** A field's type (`ai_addr`: `struct sockaddr *`) goes through an alias the project never named | 1 harmless risky |
| F5 | **Guide subsystem sections aren't gated** like vendor sections (OpenThread, with `CONFIG_OPENTHREAD` off) | 1 harmless breaking |
| F6 | **A "replaced by the underlying options" entry is parsed as a rename to the first option** | H3 not fix-actionable |
| F7 | **"Macro is deprecated" doesn't say which macro or its replacement.** The guide's table has both (DOGFOOD-12 E7) | H2 not fix-actionable |

## 5. What this means

- **Clean recall is the best so far:** strict 4/8 and lenient 7/8, with precision 38/40.
  For comparison, the clean baselines were DOGFOOD-8 (strict 0/13), DOGFOOD-12 (1/7)
  and, here, 4/8.
- **The change database and the Phase 0 rename sources carried over.** Every removed
  or renamed Kconfig item was caught at its line.
- **The remaining gaps are in the attribution of warnings** (F1, F2, F3): the cause is in
  the output, but at a Zephyr location or the wrong project line. That's the same class
  as ble-copro's E4 before it was fixed.
- **One caveat on cleanliness:** edgehog's items overlap with patterns the earlier fixes
  were built for (Mbed TLS 4 removals, the UART rename). It is clean by the rules (no
  change was informed by edgehog), but the overlap favours zmig here.
