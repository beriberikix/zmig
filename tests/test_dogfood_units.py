"""Unit regressions from dogfooding on ZMK v3.2 -> v3.5 (docs/DOGFOOD-1.md). The log
excerpts are copied from the real target configure/build output (paths shortened)."""

# ruff: noqa: E501  (verbatim log lines)

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from zmig import tools
from zmig.analyzers import initorder
from zmig.analyzers.buildlog import build_errors
from zmig.analyzers.kconfig import dependency_chains
from zmig.buildctx import kconfig_block, locate_salvage_edits, project_path, project_roots
from zmig.diagnostics import parse_log, parse_warnings
from zmig.tools import Runner

APP = "/ws/apps/x/app"

DEFINITION_LOG = f"""\
Parsing /ws/zephyr/Kconfig
warning: LV_Z_MEM_POOL_MIN_SIZE (defined at {APP}/src/display/Kconfig:32) defined without a type

warning: SSD1306_REVERSE_MODE (defined at {APP}/boards/shields/corne/Kconfig.defconfig:24, {APP}/boards/shields/jorne/Kconfig.defconfig:25) defined without a type

warning: <choice BT_LL_SW_LLCP_IMPL> (defined at {APP}/Kconfig:175) defined with type unknown

warning: the default selection BT_LL_SW_LLCP_LEGACY (undefined) of <choice BT_LL_SW_LLCP_IMPL> (defined at {APP}/Kconfig:175) is not contained in the choice

error: Aborting due to Kconfig warnings
"""

LOOP_LOG = f"""\
/ws/zephyr/scripts/kconfig/kconfig.py: Dependency loop
===============

I2C (defined at {APP}/boards/shields/corne/Kconfig.defconfig:18, drivers/i2c/Kconfig:9), with definition...

config I2C
	bool

...depends on MAX17048 (defined at {APP}/module/drivers/sensor/max17048/Kconfig:6, drivers/fuel_gauge/max17048/Kconfig:7), with definition...

...depends again on I2C (defined at {APP}/boards/shields/corne/Kconfig.defconfig:18, drivers/i2c/Kconfig:9)
CMake Error at /ws/zephyr/cmake/modules/kconfig.cmake:355 (message):
"""


def test_kconfig_definition_and_loop_diagnostics() -> None:
    d = [x for x in parse_log(DEFINITION_LOG).diagnostics if x.kind == "kconfig-definition"]
    assert [(x.symbol, x.value, len(x.locations)) for x in d] == [
        ("LV_Z_MEM_POOL_MIN_SIZE", "", 1),
        ("SSD1306_REVERSE_MODE", "", 2),
        ("BT_LL_SW_LLCP_IMPL", "", 1),
        ("BT_LL_SW_LLCP_IMPL", "BT_LL_SW_LLCP_LEGACY", 1),
    ]
    loop = [x for x in parse_log(LOOP_LOG).diagnostics if x.kind == "kconfig-loop"]
    assert {x.symbol for x in loop} == {"I2C", "MAX17048"}
    m = next(x for x in loop if x.symbol == "MAX17048")
    assert m.locations == (
        f"{APP}/module/drivers/sensor/max17048/Kconfig:6",
        "drivers/fuel_gauge/max17048/Kconfig:7",
    )


def test_kconfig_block_extent() -> None:
    lines = [
        "config SSD1306_REVERSE_MODE",
        "    default y",
        "",
        "choice BT_LL_SW_LLCP_IMPL",
        "    default BT_LL_SW_LLCP_LEGACY",
        "",
        "endchoice",
        "menuconfig MAX17048",
        '    bool "MAX17048/9 I2C-based Fuel Gauge"',
        "    depends on I2C",
    ]
    assert kconfig_block(lines, 0) == range(0, 2)
    assert kconfig_block(lines, 3) == range(3, 7)
    assert kconfig_block(lines, 7) == range(7, 10)


def test_salvage_comments_out_app_kconfig_blocks(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "boards/shields/corne").mkdir(parents=True)
    (app / "module/drivers/sensor/max17048").mkdir(parents=True)
    (app / "boards/shields/corne/Kconfig.defconfig").write_text(
        "\n" * 17
        + "config I2C\n    default y\n\n"  # 18: a normal override of an upstream symbol
        + "\n" * 3
        + "config SSD1306_REVERSE_MODE\n    default y\n"  # 24
    )
    (app / "module/drivers/sensor/max17048/Kconfig").write_text(
        "\n" * 5 + 'menuconfig MAX17048\n    bool "MAX17048/9 I2C-based Fuel Gauge"\n'
    )
    log = (DEFINITION_LOG + LOOP_LOG).replace(APP, str(app))
    edits = {(e.file, e.line) for e in locate_salvage_edits(log, app)}
    assert ("boards/shields/corne/Kconfig.defconfig", 24) in edits
    assert ("boards/shields/corne/Kconfig.defconfig", 25) in edits
    assert ("module/drivers/sensor/max17048/Kconfig", 6) in edits
    assert ("module/drivers/sensor/max17048/Kconfig", 7) in edits
    # The I2C override has no prompt: it's how apps set defaults, not a collision.
    assert ("boards/shields/corne/Kconfig.defconfig", 18) not in edits


def test_fatal_error_is_a_compiler_error() -> None:
    log = (
        "FAILED: [code=1] CMakeFiles/app.dir/src/behavior.c.obj\n"
        f"{APP}/include/drivers/behavior.h:276:10: fatal error: syscalls/behavior.h: "
        "No such file or directory\n"
    )
    (e,) = build_errors(log)
    assert e.message.startswith("syscalls/behavior.h")
    assert e.line == 276


def test_fork_detection_counts_commits_no_upstream_ref_contains(tmp_path: Path) -> None:
    """`plan` on ZMK's fork refs said nothing about them being forks (34/21 commits on top
    of v3.2.0/v3.5.0) and suggested upstream tags as hops without a caveat."""
    import subprocess
    from types import SimpleNamespace

    from zmig.analyzers.hops import fork_of
    from zmig.tools import Runner

    repo = tmp_path / "m.git"
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(tmp_path / "w")]
    (tmp_path / "w").mkdir()
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "release"], check=True)
    subprocess.run([*git, "tag", "v3.2.0"], check=True)
    subprocess.run([*git, "checkout", "-q", "--detach"], check=True)
    for msg in ("fork fix 1", "fork fix 2"):
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", msg], check=True)
    fork = subprocess.run(
        [*git, "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "clone", "-q", "--bare", str(tmp_path / "w"), str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "--git-dir",
            str(repo),
            "fetch",
            "-q",
            str(tmp_path / "w"),
            f"+{fork}:refs/zmig/local/{fork}",
        ],
        check=True,
    )
    mirror = SimpleNamespace(git="git", path=repo, runner=Runner())
    tag = subprocess.run(
        ["git", "--git-dir", str(repo), "rev-parse", "v3.2.0"], capture_output=True, text=True
    ).stdout.strip()
    assert fork_of(mirror, tag) is None  # type: ignore[arg-type]
    base, own, subjects = fork_of(mirror, fork)  # type: ignore[arg-type,misc]
    assert (base, own) == ("v3.2.0", 2)
    assert subjects[0].endswith("fork fix 2")


def test_app_binding_paths_compare_without_the_copy_dir() -> None:
    """28 risky "node-changed: (binding)" findings in the ZMK report differed only in
    zmig's per-build app copy directory."""
    from zmig.analyzers.devicetree import _binding

    a = "apps/6b9e9f511eba0965/app/dts/bindings/macros/zmk,macro-control-tap-time.yaml"
    b = "apps/3733b8e4da428d6a/app/dts/bindings/macros/zmk,macro-control-tap-time.yaml"
    assert _binding(a) == _binding(b) == "<app>/dts/bindings/macros/zmk,macro-control-tap-time.yaml"
    assert _binding("zephyr/dts/bindings/i2c/nordic,nrf-twim.yaml") == (
        "zephyr/dts/bindings/i2c/nordic,nrf-twim.yaml"
    )


# --- Round 2: warnings raised in Zephyr headers, attributed to the project's call site ---

SYS_INIT_LOG = """\
[17/314] Building C object CMakeFiles/app.dir/src/activity.c.obj
In file included from /ws/zephyr/include/zephyr/device.h:13,
                 from /ws/apps/x/app/src/activity.c:7:
/ws/zephyr/include/zephyr/init.h:193:44: warning: initialization of 'int (*)(void)' from incompatible pointer type 'int (*)(const struct device *)' [-Wincompatible-pointer-types]
  193 |                         .init_fn = {.sys = (init_fn_)},                        \\
      |                                            ^
/ws/zephyr/include/zephyr/init.h:174:9: note: in expansion of macro 'SYS_INIT_NAMED'
  174 |         SYS_INIT_NAMED(init_fn, init_fn, level, prio)
      |         ^~~~~~~~~~~~~~
/ws/apps/x/app/src/activity.c:98:1: note: in expansion of macro 'SYS_INIT'
   98 | SYS_INIT(activity_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);
      | ^~~~~~~~
[18/314] Building C object CMakeFiles/app.dir/src/kscan.c.obj
"""

# Golioth hello at v4.2.0: the shared example code is in the project's own module, outside
# the app directory.
NET_MGMT_LOG = """\
[172/432] Building C object zephyr/CMakeFiles/zephyr.dir/mod/golioth-firmware-sdk/examples/zephyr/common/net_connect.c.obj
/mod/golioth-firmware-sdk/examples/zephyr/common/net_connect.c: In function 'wait_for_net_event':
/mod/golioth-firmware-sdk/examples/zephyr/common/net_connect.c:36:21: warning: assignment to 'net_mgmt_event_handler_t' {aka 'void (*)(struct net_mgmt_event_callback *, long long unsigned int,  struct net_if *)'} from incompatible pointer type 'void (*)(struct net_mgmt_event_callback *, uint32_t,  struct net_if *)' {aka 'void (*)(struct net_mgmt_event_callback *, unsigned int,  struct net_if *)'} [-Wincompatible-pointer-types]
   36 |     wait.cb.handler = event_cb_handler;
      |                     ^
In file included from /ws/zephyr/include/zephyr/net/net_mgmt.h:96,
                 from /ws/zephyr/include/zephyr/net/net_stats.h:19,
                 from /ws/zephyr/include/zephyr/net/net_if.h:32,
                 from /mod/golioth-firmware-sdk/examples/zephyr/common/net_connect.c:11:
/mod/golioth-firmware-sdk/examples/zephyr/common/net_connect.c: In function 'net_connect':
/ws/zephyr/include/zephyr/net/net_event.h:344:9: warning: conversion from 'long long unsigned int' to 'uint32_t' {aka 'unsigned int'} changes value from '16154411863377969153' to '1' [-Woverflow]
  344 |         (NET_EVENT_IPV4_BASE | NET_EVENT_IPV4_CMD_ADDR_ADD)
      |         ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
/mod/golioth-firmware-sdk/examples/zephyr/common/net_connect.c:69:31: note: in expansion of macro 'NET_EVENT_IPV4_ADDR_ADD'
   69 |     wait_for_net_event(iface, NET_EVENT_IPV4_ADDR_ADD);
      |                               ^~~~~~~~~~~~~~~~~~~~~~~
[173/432] Building C object zephyr/CMakeFiles/zephyr.dir/drivers/flash/flash_util.c.obj
"""


def test_header_warning_keeps_the_macro_call_site() -> None:
    (w,) = parse_warnings(SYS_INIT_LOG)
    assert (w.file, w.line) == ("/ws/zephyr/include/zephyr/init.h", 193)
    # Innermost expansion first; the include chain's compiled file last.
    assert w.sites == (
        ("/ws/zephyr/include/zephyr/init.h", 174),
        ("/ws/apps/x/app/src/activity.c", 98),
        ("/ws/apps/x/app/src/activity.c", 7),
    )


def test_net_mgmt_warnings_point_at_the_module_call_site(tmp_path: Path) -> None:
    handler, overflow = parse_warnings(NET_MGMT_LOG)
    common = "/mod/golioth-firmware-sdk/examples/zephyr/common/net_connect.c"
    assert (handler.file, handler.line) == (common, 36)
    assert handler.sites == ((common, 0),)
    assert overflow.file.endswith("net_event.h")
    assert overflow.sites[0] == (common, 69)

    # The project's own modules (outside zmig's workspace) count as project code.
    img = tmp_path / "build"
    img.mkdir()
    (img / "zephyr_modules.txt").write_text(
        '"golioth-firmware-sdk":"/mod/golioth-firmware-sdk":"/mod/golioth-firmware-sdk/port/zephyr"\n'
        '"hal_stm32":"/ws/modules/hal/stm32":"/ws/zephyr/modules/hal_stm32"\n'
    )
    b = SimpleNamespace(src=Path("/ws/apps/x/hello"), workspace=SimpleNamespace(root=Path("/ws")))
    roots = project_roots(b, img)  # type: ignore[arg-type]
    assert roots == {"/ws/apps/x/hello": "", "/mod/golioth-firmware-sdk": "golioth-firmware-sdk/"}
    assert (
        project_path(common, roots) == "golioth-firmware-sdk/examples/zephyr/common/net_connect.c"
    )
    assert project_path("/ws/apps/x/hello/src/main.c", roots) == "src/main.c"
    assert project_path("/ws/zephyr/include/zephyr/net/net_event.h", roots) is None


def test_long_steps_report_progress(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(tools, "HEARTBEAT_S", 0.2)
    with caplog.at_level(logging.WARNING, logger="zmig"):
        Runner().run(["sleep", "0.7"], progress="compiling app at v4.2.0")
    msgs = [r.getMessage() for r in caplog.records]
    assert msgs[0] == "  compiling app at v4.2.0..."
    assert any(m.startswith("  ... still compiling app at v4.2.0 (") for m in msgs[1:-1])
    assert msgs[-1].startswith("  compiling app at v4.2.0: done in ")
    # Steps without a label stay quiet.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="zmig"):
        Runner().run(["true"])
    assert caplog.records == []


def test_dependency_chains_one_hop_or_through_a_changed_symbol() -> None:
    old = {
        "APP_FEATURE": {"depends": "BT && !FOO", "selects": ["PM"], "defaults": ["y if BAR"]},
        "BT": {"depends": "HAS_BT", "selects": [], "defaults": []},
        "PM": {"depends": "HAS_PM", "selects": [], "defaults": []},
        "HAS_PM": {},
        "HAS_BT": {},
        "BAR": {},
        "FOO": {},
    }
    # PM: one hop (selected). HAS_PM: only through PM, which changed too. HAS_BT: BT didn't
    # change, so the chain stops (SoC capability churn every config depends on).
    chains = dependency_chains({"APP_FEATURE": ()}, {"PM", "HAS_PM", "HAS_BT", "BAR"}, old)
    assert chains == {
        "BAR": [("APP_FEATURE", "takes its default from", "BAR")],
        "PM": [("APP_FEATURE", "selects", "PM")],
        "HAS_PM": [("APP_FEATURE", "selects", "PM"), ("PM", "depends on", "HAS_PM")],
    }
    assert dependency_chains({"NOT_DEFINED": ()}, {"PM"}, old) == {}


def test_init_order_swaps_and_device_identity() -> None:
    def dump(entries: list[tuple[str, str, str, str | None]]) -> dict[str, Any]:
        levels = ["PRE_KERNEL_1", "POST_KERNEL", "APPLICATION"]
        idx: dict[str, int] = {}
        out = []
        for level, fn, arg, node in entries:
            out.append(
                {
                    "level": level,
                    "index": idx.setdefault(level, 0),
                    "fn": fn,
                    "arg": arg,
                    "node": node,
                }
            )
            idx[level] += 1
        return {"levels": levels, "entries": out, "sites": {}}

    old = dump(
        [
            ("PRE_KERNEL_1", "clock_init", "__device_dts_ord_3", "/soc/rcc"),
            ("POST_KERNEL", "settings_init", "NULL", None),
            ("POST_KERNEL", "app_ext_power_init", "__device_dts_ord_9", "/ext_power"),
            ("POST_KERNEL", "regulator_init", "__device_dts_ord_7", "/regulator"),
        ]
    )
    # v4.2: device init functions are listed as NULL and ordinals renumber.
    new = dump(
        [
            ("PRE_KERNEL_1", "NULL", "__device_dts_ord_2", "/soc/rcc"),
            ("POST_KERNEL", "NULL", "__device_dts_ord_11", "/regulator"),
            ("POST_KERNEL", "NULL", "__device_dts_ord_8", "/ext_power"),
            ("APPLICATION", "settings_init", "NULL", None),
        ]
    )
    o = [initorder._key(e) for e in initorder.run_order(old)]
    n = [initorder._key(e) for e in initorder.run_order(new)]
    assert ("device", "/ext_power") in n
    later, earlier = initorder.swaps(o, n, ("device", "/ext_power"))
    assert later == [("settings_init", "NULL")]
    assert earlier == [("device", "/regulator")]
    assert initorder._label(new["entries"][2]) == "device /ext_power"


def test_c_standard_type_names_are_not_guide_literals() -> None:
    from zmig.guides import _item

    assert _item(None, "uint32_t") is None
    assert _item(None, "size_t") is None
    assert _item(None, "net_mgmt_event_handler_t") == ("literal", "net_mgmt_event_handler_t")


def test_elf_named_after_kernel_bin_name(tmp_path: Path) -> None:
    from zmig.buildctx import Artifact, ImageContext

    (tmp_path / "zephyr").mkdir()
    (tmp_path / "zephyr" / ".config").write_text('CONFIG_KERNEL_BIN_NAME="zmk"\n')
    (tmp_path / "zephyr" / "zmk.elf").write_bytes(b"\x7fELF")
    img = ImageContext("app", "app", "", tmp_path, tmp_path / "log", "ok", "full", "b", "r")
    assert img.has(Artifact.ELF)
    assert img.artifact(Artifact.ELF) == tmp_path / "zephyr" / "zmk.elf"
    assert img.missing_reason(Artifact.MAP) == "zephyr/zmk.map missing"


def test_findings_only_in_unused_input_files_become_info(tmp_path: Path) -> None:
    from zmig.analyzers import outside_build
    from zmig.findings import Category, Confidence, Finding, Severity, file_line

    app = tmp_path / "ws" / "apps" / "x" / "hello"
    img_dir = tmp_path / "build"
    (img_dir / "zephyr").mkdir(parents=True)
    log = img_dir / "log"
    log.write_text(f"Merged configuration '{app}/prj.conf'\n")
    (img_dir / "zephyr" / "zephyr.dts.d").write_text(
        f"empty_file.o: \\\n /ws/zephyr/boards/st/nucleo_h563zi/nucleo_h563zi.dts \\\n {app}/app.overlay\n"
    )
    img = SimpleNamespace(dir=img_dir, log=log)
    b = SimpleNamespace(src=app, workspace=SimpleNamespace(root=tmp_path / "ws"), images=(img,))
    # The source build compiled src/main.c and the header it includes (ninja's records).
    base = SimpleNamespace(old=b, new=b, new_raw=b, build_files={"src/main.c", "src/app.h"})

    def finding(*paths: str, sev: Severity = Severity.BREAKING) -> Finding:
        return Finding(Category.KCONFIG, "symbol-removed", paths[0], "t", sev, Confidence.HIGH,
                       "a", "kconfig", ("s",),
                       evidence=tuple(file_line(p, 27, "x") for p in paths))  # fmt: skip

    rak, used, src = (
        finding("boards/rak5010_nrf52840.conf"),
        finding("app.overlay"),
        finding("src/main.c", sev=Severity.RISKY),
    )
    artifact = finding("<v4.1.0 hello>/zephyr/zephyr.dts", sev=Severity.RISKY)
    out = outside_build([rak, used, src, artifact], base)  # type: ignore[arg-type]
    assert out[3].severity is Severity.RISKY  # a generated file, never "outside the build"
    assert out[0].severity is Severity.INFO
    assert "only in files this build doesn't use: boards/rak5010_nrf52840.conf" in out[0].title
    assert "outside-build" in out[0].heuristics
    assert out[1].severity is Severity.BREAKING  # applied overlay
    assert out[2].severity is Severity.RISKY  # a compiled source


def test_findings_only_in_uncompiled_sources_become_info(tmp_path: Path) -> None:
    """AUDIT.md F7/§5.3: echo_server's tunnel.c and vlan.c are only compiled with
    CONFIG_NET_L2_IPIP / CONFIG_NET_VLAN, yet guide matches on them were breaking."""
    from zmig.analyzers import outside_build
    from zmig.findings import Category, Confidence, Finding, Severity, file_line

    log = tmp_path / "log"
    log.write_text("")
    (tmp_path / "zephyr").mkdir()
    (tmp_path / "zephyr" / "zephyr.dts.d").write_text("")
    img = SimpleNamespace(dir=tmp_path, log=log)
    b = SimpleNamespace(
        src=tmp_path / "app", workspace=SimpleNamespace(root=tmp_path), images=(img,)
    )

    def finding(*paths: str, image: str | None = None) -> Finding:
        return Finding(Category.GUIDE, "entry-match", paths[0], "t", Severity.BREAKING,
                       Confidence.MEDIUM, "a", "guide", ("s",), image=image,
                       evidence=tuple(file_line(p, 12, "x") for p in paths))  # fmt: skip

    unbuilt = finding("src/tunnel.c", "src/vlan.c")
    header = finding("src/tunnel.h")
    built = finding("src/echo-server.c")
    mixed = finding("src/echo-server.c", "src/tunnel.c")
    other_image = finding("src/tunnel.c", image="mcuboot")
    base = SimpleNamespace(old=b, new=b, new_raw=b, build_files={"src/echo-server.c"})
    out = outside_build([unbuilt, header, built, mixed, other_image], base)  # type: ignore[arg-type]
    assert out[0].severity is Severity.INFO
    assert "only in files this build doesn't compile: src/tunnel.c, src/vlan.c" in out[0].title
    assert "outside-build" in out[0].heuristics
    assert out[0].id == unbuilt.id
    assert out[1].severity is Severity.INFO  # a header no compiled file includes
    assert out[2].severity is Severity.BREAKING
    assert out[3].severity is Severity.BREAKING  # one compiled file is enough
    assert out[4].severity is Severity.BREAKING  # build_files describes the app image only

    # No ninja records: nothing is judged.
    base = SimpleNamespace(old=b, new=b, new_raw=b, build_files=None)
    assert outside_build([unbuilt], base)[0].severity is Severity.BREAKING  # type: ignore[arg-type]


DEVICE_ERROR_LOG = """\
[40/281] Building C object modules/thingset-zephyr-sdk/CMakeFiles/thingset.dir/src/serial.c.obj
In file included from /ws/zephyr/include/zephyr/device.h:12,
                 from /mod/thingset-zephyr-sdk/src/serial.c:7:
/ws/zephyr/include/zephyr/device.h:96:41: error: '__device_dts_ord_20' undeclared here (not in a function); did you mean '__device_dts_ord_21'?
   96 | #define DEVICE_NAME_GET(dev_id) _CONCAT(__device_, dev_id)
      |                                         ^~~~~~~~~
/ws/zephyr/include/zephyr/toolchain/common.h:168:26: note: in definition of macro '_DO_CONCAT'
  168 | #define _DO_CONCAT(x, y) x ## y
      |                          ^
/ws/zephyr/include/zephyr/device.h:96:33: note: in expansion of macro '_CONCAT'
   96 | #define DEVICE_NAME_GET(dev_id) _CONCAT(__device_, dev_id)
      |                                 ^~~~~~~
/mod/thingset-zephyr-sdk/src/serial.c:28:46: note: in expansion of macro 'DEVICE_DT_GET'
   28 | static const struct device *uart_dev = DEVICE_DT_GET(DT_CHOSEN(thingset_serial));
      |                                              ^~~~~~~~~~~~~
FAILED: modules/thingset-zephyr-sdk/CMakeFiles/thingset.dir/src/serial.c.obj
"""


def test_build_error_in_a_header_keeps_the_project_call_site() -> None:
    """bms-firmware native_sim at v4.2 (docs/DOGFOOD-3-baseline.md M9)."""
    (e,) = build_errors(DEVICE_ERROR_LOG)
    assert (e.file, e.line) == ("/ws/zephyr/include/zephyr/device.h", 96)
    project = [s for s in e.sites if s[0].startswith("/mod/")]
    assert project[0] == ("/mod/thingset-zephyr-sdk/src/serial.c", 28)


def test_guide_entry_full_text_keeps_the_remedy() -> None:
    """The second sentence held the remedy (docs/DOGFOOD-3-baseline.md M12)."""
    from zmig.guides import parse

    text = (
        "Migration guide\n###############\n\nSerial\n======\n\n"
        "* :kconfig:option:`CONFIG_UART_NATIVE_POSIX_PORT_1_ENABLE` has been deprecated. This option does\n"
        "  nothing now. Instead users should instantiate as many :dtcompatible:`zephyr,native-pty-uart` nodes\n"
        "  as native PTY UART instances they want. (:github:`86739`)\n"
    )
    (entry,) = parse(text, "doc/releases/migration-guide-4.2.rst", "4.2", "MG-4.2")
    assert entry.lead == "CONFIG_UART_NATIVE_POSIX_PORT_1_ENABLE has been deprecated."
    assert "instantiate as many zephyr,native-pty-uart nodes" in entry.full


def test_report_separates_checked_from_not_checked() -> None:
    """'Ran, found nothing' vs 'didn't run' (review of P2, item 1)."""
    import json as _json

    import jsonschema

    from zmig.findings import AnalyzerStatus, Checked, NotChecked, Report

    ref = {"spec": "v1", "sha": "a" * 40, "kind": "tag", "dirty": False}
    r = Report("scan", "0", {"from": ref, "to": ref}, {})
    r.analyzers = [
        AnalyzerStatus("initorder", "ok", None, 0, 0),
        AnalyzerStatus("sbom", "skipped", "no SBOM"),
    ]
    r.checked = [Checked("device and SYS_INIT init order", "clean", "2 compared", "initorder")]
    r.not_checked = [NotChecked("SBOM", "the build failed", "sbom")]
    j = r.to_json()
    schema = _json.loads((Path(__file__).parents[1] / "zmig/schema/report-1.json").read_text())
    jsonschema.validate(j, schema)
    assert j["checked"] == [
        {
            "area": "device and SYS_INIT init order",
            "result": "clean",
            "detail": "2 compared",
            "analyzer": "initorder",
        }
    ]
    statuses = {a["name"]: a for a in j["analyzers"]}
    assert statuses["initorder"] == {
        "name": "initorder",
        "status": "ok",
        "findings": 0,
        "hazards": 0,
    }
    assert "findings" not in statuses["sbom"]  # didn't run: no count


def test_located_link_errors_keep_their_source_line() -> None:
    """CANnectivity at v4.2: '…/led.c:225: undefined reference' with no 'ld:' prefix was
    reported as '1 error (step)' (docs/DOGFOOD-4-baseline.md M14)."""
    log = (
        "FAILED: [code=1] zephyr/zephyr_pre0.elf zephyr/zephyr_pre0.map\n"
        "/ws/apps/x/app/src/led.c:225: undefined reference to `smf_set_handled'\n"
        "collect2: error: ld returned 1 exit status\n"
        "/sdk/bin/ld.bfd: oled.c:33: undefined reference to `cfb_print'\n"
        "/sdk/bin/ld.bfd: region `FLASH' overflowed by 123 bytes\n"
    )
    errs = {(e.kind, e.file, e.line, e.message) for e in build_errors(log)}
    assert errs == {
        ("linker", "/ws/apps/x/app/src/led.c", 225, "undefined reference to `smf_set_handled'"),
        ("linker", "oled.c", 33, "undefined reference to `cfb_print'"),
        ("linker", "", 0, "region `FLASH' overflowed by 123 bytes"),
    }


def test_dtc_assigned_phandles_are_not_node_changes() -> None:
    """CANnectivity: led0 and counter2 'changed: phandle' (docs/DOGFOOD-4-baseline.md M13)."""
    from zmig.analyzers.devicetree import _dts_view

    old = {"labels": ["led_ready"], "raw_values": {"gpios": "<&gpioa 5 0>", "phandle": "<0x12>"}}
    new = {"labels": ["led_ready"], "raw_values": {"gpios": "<&gpioa 5 0>", "phandle": "<0x15>"}}
    assert _dts_view(old) == _dts_view(new)
    new["raw_values"]["gpios"] = "<&gpioa 6 0>"
    assert _dts_view(old) != _dts_view(new)


def _kconfig_ctx(old_log: str, new_log: str, assign: str = "STDOUT_CONSOLE") -> Any:
    """A ScanContext stand-in for KconfigAnalyzer: one assignment in prj.conf, the symbol
    defined at both refs and unset in both .configs, and each configure's log."""
    from zmig.diagnostics import parse_log
    from zmig.usage import Loc

    sym = {"depends": "!NATIVE_APPLICATION", "selects": [], "implies": [], "defaults": []}
    return SimpleNamespace(
        old_config={},
        new_config={},
        old_kconfig={assign: sym},
        new_kconfig={assign: sym},
        old_facts=parse_log(old_log),
        new_raw_facts=parse_log(new_log),
        usage=SimpleNamespace(
            kconfig_set={assign: [(Loc("prj.conf", 1, f"CONFIG_{assign}=y"), "y")]},
            kconfig_read={},
        ),
        old_spec="v4.1.0",
        new_spec="v4.2.0",
        scope=("s",),
        salvage=(),
        is_app=True,
        runner=SimpleNamespace(redact=lambda s: s),
    )


def test_value_not_taken_at_both_refs_is_not_a_migration_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUDIT.md F1/§5.2: samples/subsys/settings sets CONFIG_STDOUT_CONSOLE=y, which
    native_sim never honours; v4.1.0 printed the same warning as v4.2.0."""
    from tests.test_diagnostics import STDOUT_CONSOLE_LOG
    from zmig.analyzers.kconfig import KconfigAnalyzer
    from zmig.findings import Severity

    monkeypatch.setattr(KconfigAnalyzer, "_promote_dependencies", staticmethod(lambda *a: None))
    old, new = STDOUT_CONSOLE_LOG.format(line=156), STDOUT_CONSOLE_LOG.format(line=160)

    (f,) = KconfigAnalyzer().analyze(_kconfig_ctx(old, new)).findings
    assert f.kind == "value-not-taken" and f.subject == "CONFIG_STDOUT_CONSOLE"
    assert f.severity is Severity.INFO
    assert "already" in f.title and "v4.1.0" in f.title
    assert "kconfig.preexisting-diagnostic" in f.heuristics
    assert not f.introduced_by_migration

    # New at the target: still breaking, and the same finding ID either way.
    (g,) = KconfigAnalyzer().analyze(_kconfig_ctx("", new)).findings
    assert g.kind == "value-not-taken" and g.severity is Severity.BREAKING
    assert g.id == f.id


def test_preexisting_promptless_keeps_its_severity(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fatal Kconfig error at both refs is a defect, not a no-op: per DESIGN-P1 §2.3 it
    keeps its severity and is marked pre-existing (only value-not-taken drops to info)."""
    from zmig.analyzers.kconfig import KconfigAnalyzer
    from zmig.findings import Severity

    monkeypatch.setattr(KconfigAnalyzer, "_promote_dependencies", staticmethod(lambda *a: None))
    log = (
        "error: FOO (defined at drivers/Kconfig:{}) is assigned in a configuration file, "
        "but is not directly user-configurable (has no prompt).\n"
    )
    (f,) = KconfigAnalyzer().analyze(_kconfig_ctx(log.format(1), log.format(2), "FOO")).findings
    assert f.kind == "not-configurable" and f.severity is Severity.BREAKING
    assert not f.introduced_by_migration and "pre-existing" in f.title
    (g,) = KconfigAnalyzer().analyze(_kconfig_ctx("", log.format(2), "FOO")).findings
    assert g.introduced_by_migration and "pre-existing" not in g.title


def test_preexisting_devicetree_errors_keep_their_severity(tmp_path: Path) -> None:
    """DESIGN-P1 §2.3 for the devicetree configure errors: same error at both refs ->
    pre-existing, severity unchanged."""
    from zmig.analyzers.devicetree import DevicetreeAnalyzer
    from zmig.diagnostics import parse_log
    from zmig.findings import Severity

    undeclared = (
        "devicetree error: 'sjw' appears in /can in /b{}/zephyr/zephyr.dts.pre, but is not "
        "declared in 'properties:' in /z/dts/bindings/can/zephyr,native-linux-can.yaml\n"
        "devicetree error: /ws{}/zephyr/dts/common/x.dtsi:3 (column 1): parse error\n"
    )

    def run(old_log: str) -> list[Any]:
        ctx = SimpleNamespace(
            old_edt={"nodes": {}, "chosen": {}},
            new_edt={"nodes": {}, "chosen": {}},
            old_facts=parse_log(old_log),
            new_raw_facts=parse_log(undeclared.format(2, 2)),
            new_raw=SimpleNamespace(src=tmp_path),
            usage=SimpleNamespace(
                app=tmp_path, dt_nodes=[], dt_props_set=[], dt_props_read=[], dt_compats={}
            ),
            build_files=set(),
            new_spec="v4.2.0",
            scope=("s",),
            runner=SimpleNamespace(redact=lambda s: s),
        )
        return [
            f
            for f in DevicetreeAnalyzer().analyze(ctx).findings  # type: ignore[arg-type]
            if f.kind in ("property-undeclared", "dt-error")
        ]

    pre = run(undeclared.format(1, 1))
    assert len(pre) == 2
    for f in pre:
        assert f.severity is Severity.BREAKING
        assert not f.introduced_by_migration and "pre-existing" in f.title
    assert all(f.introduced_by_migration for f in run(""))


def test_module_required_at_both_refs_is_preexisting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AUDIT.md §5.2 run 1: the settings sample built without littlefs fails at v4.1.0 and
    v4.2.0 alike; "the target build needs module littlefs" was breaking/act-now."""
    from zmig.analyzers import modules
    from zmig.findings import Severity
    from zmig.modulecheck import ModuleNeed

    monkeypatch.setattr(modules, "load_manifest", lambda root: {})
    monkeypatch.setattr(modules, "manifest_changes", lambda *a: [])
    monkeypatch.setattr(modules, "user_manifest", lambda app: None)
    ws = SimpleNamespace(zephyr=tmp_path / "zephyr", root=tmp_path, modules=lambda runner: [])
    need = ModuleNeed("littlefs", "a Kconfig dependency on ZEPHYR_LITTLEFS_MODULE is unmet")

    def run(at_source: tuple[str, ...]) -> Any:
        ctx = SimpleNamespace(
            module_needs=(need,),
            modules_added=("littlefs",),
            modules_at_source=at_source,
            old=SimpleNamespace(workspace=ws, spec=SimpleNamespace(app=tmp_path)),
            new=SimpleNamespace(workspace=ws),
            old_img=SimpleNamespace(has=lambda a: False),
            new_img=SimpleNamespace(has=lambda a: False),
            runner=SimpleNamespace(redact=lambda s: s),
            old_spec="v4.1.0",
            new_spec="v4.2.0",
            scope=("s",),
        )
        (f,) = modules.ModulesAnalyzer().analyze(ctx).findings  # type: ignore[arg-type]
        return f

    pre = run(("littlefs",))
    assert pre.kind == "module-required" and pre.severity is Severity.INFO
    assert not pre.introduced_by_migration and "already required at v4.1.0" in pre.title
    assert pre.new["required_at_source"] is True
    new = run(())
    assert new.severity is Severity.BREAKING and new.introduced_by_migration
    assert new.new["required_at_source"] is False
    assert new.id == pre.id
