# Oracle: zephyr-linux-ble-copro, Zephyr v4.3.0 → v4.4.2 (PR #3, `2a9919bc17`)

Written 2026-10-30, **before zmig was run on this project**. Only `--analyzers none`
verification builds were run, and they produce no findings. The rules are in
[../ORACLE-RULES.md](../ORACLE-RULES.md). This file isn't edited after the run;
corrections go at the end, dated.

This is a held-out project. It's unseen: no zmig change has been informed by it.

## Upgrade and scanned build

**Project.** zephyr-linux-ble-copro (https://github.com/lucasdietrich/zephyr-linux-ble-copro,
Apache-2.0), a BLE co-processor for a Linux host: an nRF52840 that scans and serves BLE
devices and streams their data over TCP to the host, through a USB CDC-ECM network link.

- It's a single Zephyr application at the repository root (`CMakeLists.txt`, `prj.conf`,
  `Kconfig`, `src/`, `include/`). There's no `zephyr/module.yml`.
- `CMakeLists.txt` defaults `BOARD` to `nrf52840dk/nrf52840` and sets `BOARD_ROOT` to the
  app directory (there are no board definitions in it, only `boards/*.overlay`).
- It also contains a Rust host-side crate (`ble-copro-stream-server-rs/`), a C example
  server (`examples/`) and docs. None of those is part of the firmware build.
- Its `west.yml` imports upstream Zephyr by tag with `import: true`, **without a
  name-allowlist**: every module of Zephyr's manifest is part of the workspace. There are no
  other projects.

**Commits.** The upgrade is PR #3, "feat: upgrade to zephyr 4.2.0" (the title is wrong: its
`west.yml` change is `v4.3.0` → `v4.4.2`), merged with a merge commit:

| Role | Commit | Date | Zephyr pin |
|---|---|---|---|
| Source tree (the PR's base, the merge's first parent) | `edee97c18006802ce387229c9c49ab2f9e1a18d7` | 2026-04-25 | `revision: v4.3.0` |
| The bump, "feat: upgrade to zephyr 4.2.0" | `2aaf0a1c2cb478c92260cdfebf7aae336614d20e` | 2026-08-11 | `revision: v4.4.2` |
| Rest of the PR | `3c1aa59`, `cf133c3` (docs, rust toolchain), `c4be5d7` (Rust example), `6a07dbc` (code style) | 2026-08-13 | |
| Merge into `develop` | `2a9919bc17b4e72de498dd40160078d540c44f19` | 2026-08-13 | `v4.4.2` |
| After the merge (branch tip) | `8536b5b` "chore: format code" | 2026-08-13 | `v4.4.2` |

**Zephyr refs:**

- from `v4.3.0` = `3568e1b6d5cdd51a6b964a2a1d6d29200fea2056`;
- to `v4.4.2` = `dccb09599635bdff17633fa7e9dab014b91dce90`.

**Configuration** (`eval/configs.toml`, configuration `ble-copro`):

- **App:** the repository root on board `nrf52840dk/nrf52840` (the `CMakeLists.txt` default,
  and the Makefile's default `west build` target). No extra conf or overlay arguments.
- **Modules:** `hal_nordic cmsis_6 mbedtls tf-psa-crypto`. The project's manifest has no
  allowlist, so all of Zephyr's modules are available at both refs; these are the ones this
  build uses:
  - `hal_nordic` (nrfx, the BLE controller's HAL) and `cmsis_6` (Cortex-M4) at both refs;
  - `mbedtls` at both refs (`CONFIG_MBEDTLS=y`, `CONFIG_PSA_CRYPTO=y`: the BLE host's
    SMP/privacy crypto);
  - `tf-psa-crypto` at v4.4.2 only (it isn't in v4.3.0's manifest, and zmig skips it there:
    "not in v4.3.0's manifest, skipped: tf-psa-crypto"). At the target,
    `CONFIG_ZEPHYR_TF_PSA_CRYPTO_MODULE=y`.
  - Because the project takes every module, the new module isn't a project change (unlike
    ardep's A6): nothing in the project's manifest changed but the Zephyr revision.
- **CMake args:** `-DCMAKE_PREFIX_PATH={sdk:0.17.4};{sdk:1.0.1}`. No `--sdk`, as for ardep:
  v4.3.0 rejects SDK 1.x and v4.4.x needs ≥ 1.0.0 (`migration-guide-4.4.rst:26`). Configure
  logs: "Found toolchain: zephyr 0.17.4" at v4.3.0 and "zephyr 1.0.1" at v4.4.2.
- **Sysbuild:** not used (the project doesn't use it).
- **No patch:** the app builds unmodified.

**Scope evidence** comes from the verification builds
(`eval/run.py scan --analyzers none --out ~/.cache/zmig/eval/runs/verify-newheldout ble-copro`,
zmig `4e97c2e`):

- **Source at v4.3.0: ok** (build `a5f1f352b304d0cb`).
- **Target at v4.4.2: build-failed** (build `8f84b457b5ff246d`). Configure succeeded (Kconfig,
  devicetree), and compilation failed in one file, `src/stream_client.c` ("FAILED: …
  CMakeFiles/app.dir/src/stream_client.c.obj"; then "ninja: build stopped: cannot make
  progress due to previous errors"). No other project or Zephyr file failed. The errors are
  quoted under B1 and B2.
- **Project files compiled, the same 9 at both refs** (`compile_commands.json`):
  `src/{ble_observer,ble_server,device_control,led,linky,main,stream_client,usb_net,xiaomi}.c`.
  `src/button.c` isn't compiled (`CONFIG_COPRO_BUTTON` is not set).
- **Parsed at both refs:** the app `Kconfig` ("Parsing …/Kconfig"), the merged fragment
  `prj.conf`, the devicetree overlay `boards/nrf52840dk_nrf52840.overlay` (empty) on top of
  Zephyr's `nrf52840dk_nrf52840.dts`. `CMakeLists.txt` is the app's CMake entry point.
- **Config, both refs:** `CONFIG_COPRO_STREAM_CLIENT=y`, `CONFIG_COPRO_STREAM_CHANNEL_RX=y`,
  `CONFIG_COPRO_DEVICE_CONTROL=y`, `CONFIG_COPRO_BLE_SERVER=y`, `CONFIG_COPRO_USB_NETWORK=y`;
  `CONFIG_NET_SOCKETS=y`, `CONFIG_POSIX_API=y`, `CONFIG_LOG=y`, `CONFIG_POLL=y`;
  `CONFIG_USB_DEVICE_STACK=y`, `CONFIG_USB_DEVICE_NETWORK_ECM=y`,
  `CONFIG_USB_DEVICE_MANUFACTURER="ZEPHYR"`; `CONFIG_DEPRECATED=y`,
  `CONFIG_WARN_DEPRECATED=y`; `CONFIG_DEPRECATION_TEST` and
  `CONFIG_COMPILER_WARNINGS_AS_ERRORS` not set.
- **Config, target only:** `CONFIG_NET_NAMESPACE_COMPAT_MODE=y` (new at v4.4, default y).
- **Include chain of `stream_client.c` at the source** (`ninja -t deps`): it includes
  `zephyr/net/socket.h`, `zephyr/net/net_core.h`, `zephyr/logging/log.h`,
  `zephyr/posix/sys/socket.h` and `zephyr/posix/unistd.h`, none of the last three directly.
- **Deprecation warnings:** both logs have the Kconfig warnings "Deprecated symbol
  USB_DEVICE_DRIVER is enabled" and "Deprecated symbol USB_DEVICE_STACK is enabled", and 84
  compiler deprecation warnings each (the legacy USB stack's own files, and
  `src/main.c:59:9: warning: 'usb_enable' is deprecated`). The count and the app's warning
  are the same at both refs.

## Items

The bump `2aaf0a1` changes 8 files: `CMakeLists.txt`, `Makefile`, `README.md`, `prj.conf`,
`src/main.c`, `src/stream_client.c`, `west.yml`, and nothing else. The rest of the PR is docs,
a Rust toolchain/example change and a code-style commit; `8536b5b` after the merge is
formatting. The commits before the PR back to the previous upgrade (`5ad5ccb`, v4.3.0) were
checked. There are two upgrade items, both in `src/stream_client.c`. The other changes are
classified at the end.

### B1. Networking headers no longer include the POSIX headers (`src/stream_client.c`): in scope

**Zephyr's change:**

- `354e117192e` "net: socket: Network should not include any Posix header" (in v4.4.0, not
  in v4.3.0) removes the block at the end of `include/zephyr/net/socket.h` (v4.3.0 lines
  1273-1297) that, under `CONFIG_POSIX_API`, included `zephyr/posix/arpa/inet.h`,
  `netdb.h`, `unistd.h`, `poll.h` and `sys/socket.h`.
- It's part of the network namespacing (`a63dbfb6cc4` "net: Namespace network symbols to
  avoid conflicts with Posix/libc"): `migration-guide-4.4.rst:1221-1235` (Networking) says
  network APIs get `net_`/`NET_`/`ZSOCK_` prefixes, a compatibility header
  (`net_compat.h`, `CONFIG_NET_NAMESPACE_COMPAT_MODE`) keeps the old constant and type
  names, and "External network applications can continue to use POSIX defined network
  symbols and include relevant POSIX header files like `sys/socket.h` to get the POSIX
  symbols as Zephyr networking header files will no longer include those."
- So at v4.4.2, `AF_INET`, `SOCK_STREAM`, `IPPROTO_TCP`, `struct sockaddr_in` and `htons`
  still resolve through compat mode, but the POSIX functions `socket`, `connect`, `send`,
  `recv` (`zephyr/posix/sys/socket.h`) and `close` (`zephyr/posix/unistd.h`) are no longer
  declared. GCC 14 (SDK 1.0.1) makes an implicit function declaration an error.

**Parts:**

| Part | Where (`edee97c`) | What changed |
|---|---|---|
| B1 | `src/stream_client.c:4-5` (after `#include <zephyr/net/socket.h>` and `<zephyr/sys/byteorder.h>`) | adds `#include <zephyr/posix/sys/socket.h>` and `#include <zephyr/posix/unistd.h>` |

The calls that need the headers, unchanged by the maintainers: `socket` `:188`, `connect`
`:194`, `close` `:197, :236`, `send` `:304, :311`, `recv` `:391`.

**Scope:** in. `stream_client.c` is compiled at both refs (`CONFIG_COPRO_STREAM_CLIENT=y`),
with `CONFIG_POSIX_API=y`.

**Revealing artifacts.** **These were seen** in the verification build log of the target
(`zmig-logs/zephyr-linux-ble-copro.log`, build `8f84b457b5ff246d`):

- `src/stream_client.c:188:16: error: implicit declaration of function 'socket'; did you
  mean 'socklen_t'? [-Wimplicit-function-declaration]`
- `src/stream_client.c:194:15: error: implicit declaration of function 'connect'; did you
  mean 'try_connect'?`
- `src/stream_client.c:197:17: error: implicit declaration of function 'close'; did you
  mean 'fclose'?`
- `src/stream_client.c:304:15: error: implicit declaration of function 'send'`
- `src/stream_client.c:391:27: error: implicit declaration of function 'recv'`

Also available: the header diff of `include/zephyr/net/socket.h` (the POSIX include block
is gone), the source build's include chain (the two POSIX headers reached only through
`socket.h`), and the guide entry `:1221-1235`.

The errors are at the call sites; the maintainers' edit is the include block of the same
file. The errors name the undeclared functions but not the header that used to declare
them.

### B2. `net_core.h` no longer includes `logging/log.h` (`src/stream_client.c`): in scope

**Zephyr's change** (no migration-guide entry):

- `086be2b85b3` "net: Split private net logging helper macros in own header" moves the
  `NET_DBG`/`NET_ERR`/… macros and the `#include <zephyr/logging/log.h>` from
  `include/zephyr/net/net_core.h` (v4.3.0 line 19) into a new `net_log.h`, still included
  from `net_core.h`;
- `d74c1ab7c83` "net: Stop including net_log.h from net_core.h" then drops that include, "so
  we do not leak these macros and its includes into users of net_core.h". Both are in
  v4.4.0 and not in v4.3.0.
- At v4.3.0, `stream_client.c` got `log.h` only through `net_core.h` (it's the only app file
  that uses `LOG_*` without including `log.h`). At v4.4.2 nothing in its include chain
  provides it.

**Parts:**

| Part | Where (`edee97c`) | What changed |
|---|---|---|
| B2 | `src/stream_client.c:1` (top of the include block) | adds `#include <zephyr/logging/log.h>` |

The uses that need it, unchanged: `LOG_MODULE_REGISTER` `:10`, and every
`LOG_ERR`/`LOG_INF`/`LOG_WRN`/`LOG_DBG` in the file.

**Scope:** in. The file is compiled at both refs, with `CONFIG_LOG=y`.

**Revealing artifacts.** **These were seen** in the same target build log:

- `src/stream_client.c:10:1: warning: data definition has no type or storage class`
- `src/stream_client.c:10:1: error: type defaults to 'int' in declaration of
  'LOG_MODULE_REGISTER' [-Wimplicit-int]`
- `src/stream_client.c:10:1: error: parameter names (without types) in function
  declaration [-Wdeclaration-missing-parameter-type]`
- `src/stream_client.c:190:17: error: implicit declaration of function 'LOG_ERR'`, and the
  same for `LOG_INF` (`:210`), `LOG_DBG` (`:414`), `LOG_WRN` (`:457`).

Also available: the header diff of `net_core.h`, and the source build's include chain. The
errors don't name the missing header.

## Not upgrade items (with reasons)

Every file of the PR and of `8536b5b` is accounted for below or in B1–B2.

| Commit / file | Change | Why it's not an item |
|---|---|---|
| `2aaf0a1` `west.yml:17` | `revision: v4.3.0` → `v4.4.2` | The bump itself |
| `2aaf0a1` `prj.conf:33` | adds `CONFIG_DEPRECATION_TEST=y` | It silences compiler deprecation warnings (`-Wno-deprecated-declarations`, `__DEPRECATED_MACRO`; `CMakeLists.txt:172`, `toolchain/gcc.h:360` at v4.4.2). The warnings it hides are from the legacy USB device stack (`USB_DEVICE_STACK` `select DEPRECATED` at both refs) and `usb_enable` (`src/main.c:59`), and both verification builds have the same 84 of them. No warning is new at the target, and none is an error (`COMPILER_WARNINGS_AS_ERRORS` is off). The symbol and its effect exist at v4.3.0 too. So the upgrade doesn't cause it; it's warning clean-up made during the upgrade. Kconfig's "Deprecated symbol … is enabled" warnings aren't affected by it |
| `2aaf0a1` `prj.conf:39` | adds `CONFIG_USB_DEVICE_MANUFACTURER="HA Copro"` | The symbol and its default `"ZEPHYR"` are the same at both refs (`subsys/usb/device/Kconfig`; the source and target `.config` both have `"ZEPHYR"`). The legacy stack's only changes between the refs are network-namespace renames inside five class files (`function_ecm.c`: `ntohs` → `net_ntohs`, `AF_UNSPEC` → `NET_AF_UNSPEC`). A product-string choice |
| `2aaf0a1` `prj.conf` | `CONFIG_POSIX_API=y` moved from `:58` (`edee97c`) to `:51`, next to `CONFIG_NET_SOCKETS=y` | Reordering; the value is the same |
| `2aaf0a1` `CMakeLists.txt:9` | adds `set(BOARD_FLASH_RUNNER openocd)` | A flashing workflow choice ("use system openocd", commit message). The board's runners are the same at both refs (`boards/nordic/nrf52840dk/board.cmake` is unchanged; `runners.yaml` gives `flash-runner: nrfutil` at both). The guide's OpenOCD-runner entry (`:62-80`, `--use-elf` etc. deprecated) doesn't touch it. See "Uncertain" below |
| `2aaf0a1` `Makefile` | `--runner=$(RUNNER)` (jlink) → `--openocd /usr/bin/openocd`; drops the `rust:` target | Same workflow choice; the Makefile isn't parsed by any build |
| `2aaf0a1` `src/main.c:16, :62` (post-bump lines) | blank line before `usb_device.h`; log message typo `"red %d"` → `"ret %d"` | Formatting and a typo |
| `2aaf0a1` `README.md`; `3c1aa59`/`cf133c3` `docs/usb_net.md`, `rust-toolchain.toml` | docs; Rust toolchain version | Docs and host tooling |
| `c4be5d7` `ble-copro-stream-server-rs/examples/server.rs` | firmware version in the Rust example | Host-side Rust, not firmware |
| `6a07dbc` `.clang-format`, `src/stream_client.c`, `src/xiaomi.c` | `AllowShortIfStatementsOnASingleLine`; brace removal, include sorting (moves the B1/B2 includes into one sorted block), early `continue`, and a `get_poll_events_count()` helper replacing the `#if` around `k_poll` | Code style and a behaviour-neutral refactor. The re-sorted includes are still B1/B2 |
| `8536b5b` `src/ble_server.c`, `src/main.c` | clang-format | Formatting |
| `fa2b1d2` (2026-04-14, on v4.3.0) `prj.conf` | adds `CONFIG_USB_DEVICE_STACK_NEXT=n`; drops `CONFIG_COPRO_STREAM_CHANNEL_RX=n` | Before the PR, while on v4.3.0, and the source build is unaffected (`USB_DEVICE_STACK_NEXT` isn't set either way). The PR's commit message ("still use legacy USB stack as CDC/ECM is not ported yet") explains it; no change of the PR follows from it |
| `edee97c`, `bbf9387`, `25ebac0`, `ecac8ed`, `883b270`, `95a7f78`, … back to `5ad5ccb` | log levels, button support, firmware version, bonding, multi-connection BLE | Features made on v4.3.0 |
| `5ad5ccb` (2026-03-10), `0bc1de1` | the v4.2 → v4.3.0 upgrade; an earlier v4.2.0 fork | Other upgrade pairs |

**Not changed, and not hazards in this build** (for precision checking):

- `migration-guide-4.4.rst:1156-1158`: the Bluetooth host no longer selects `CONFIG_POLL`,
  and `stream_client.c` uses `k_poll`. `NETWORKING` still selects `POLL`
  (`subsys/net/Kconfig:11` at v4.4.2), and the target `.config` has `CONFIG_POLL=y`.
- The legacy USB stack (`USB_DEVICE_STACK`, `usb_enable`) is deprecated at both refs, not
  newly.

### Uncertain: the OpenOCD runner switch

The PR's first bullet is "use system openocd". In this machine's SDKs, OpenOCD is found at
v4.3.0 (`OPENOCD:FILEPATH=…/zephyr-sdk-0.17.4/sysroots/x86_64-pokysdk-linux/usr/bin/openocd`)
and not at v4.4.2 (`OPENOCD-NOTFOUND`). That's because SDK 1.0.1 keeps host tools in a
separate `hosttools/` installer that wasn't run here; both SDKs ship the same host-tools
bundle (`zephyr-sdk-x86_64-hosttools-standalone-0.10.sh`). So the SDK move (`:26`) may be
why the maintainer points west at `/usr/bin/openocd`, but it doesn't explain switching the
runner away from jlink, and the only place the path is set is the Makefile, which no build
reads. It isn't an item. If it were counted, it would be out of scope (Makefile), and the
`CMakeLists.txt` runner line would still be a preference.

## Counts

- **Items:** 2 (B1, B2), both in scope, both one part, both in `src/stream_client.c`.
- **Out of scope:** none.
- **What the scanned target build shows:** it configures and fails compiling
  `stream_client.c`, with errors at the right file for both items. Neither error names the
  header to include, nor the Zephyr change. B1 also has a migration-guide entry
  (`:1221-1235`); B2 has none, only the `net_core.h` header diff.
- **Reachable ceiling:** strict recall 2/2. Both items need a finding that ties the
  undeclared functions (B1) or `LOG_*` macros (B2) in `stream_client.c` to the header that
  stopped providing them.
