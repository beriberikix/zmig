"""Unit regressions from dogfooding on ZMK v3.2 -> v3.5 (eval/reports/DOGFOOD-1.md). The log
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
    b = SimpleNamespace(src=app, workspace=SimpleNamespace(root=tmp_path / "ws"), images=(img,),
                        spec=SimpleNamespace(app=app))  # fmt: skip
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
    """eval/AUDIT.md F7/§5.3: echo_server's tunnel.c and vlan.c are only compiled with
    CONFIG_NET_L2_IPIP / CONFIG_NET_VLAN, yet guide matches on them were breaking."""
    from zmig.analyzers import outside_build
    from zmig.findings import Category, Confidence, Finding, Severity, file_line

    log = tmp_path / "log"
    log.write_text("")
    (tmp_path / "zephyr").mkdir()
    (tmp_path / "zephyr" / "zephyr.dts.d").write_text("")
    img = SimpleNamespace(dir=tmp_path, log=log)
    b = SimpleNamespace(
        src=tmp_path / "app",
        workspace=SimpleNamespace(root=tmp_path),
        images=(img,),
        spec=SimpleNamespace(app=tmp_path / "app"),
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
    """bms-firmware native_sim at v4.2 (eval/reports/DOGFOOD-3-baseline.md M9)."""
    (e,) = build_errors(DEVICE_ERROR_LOG)
    assert (e.file, e.line) == ("/ws/zephyr/include/zephyr/device.h", 96)
    project = [s for s in e.sites if s[0].startswith("/mod/")]
    assert project[0] == ("/mod/thingset-zephyr-sdk/src/serial.c", 28)


def test_guide_entry_full_text_keeps_the_remedy() -> None:
    """The second sentence held the remedy (eval/reports/DOGFOOD-3-baseline.md M12)."""
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
    reported as '1 error (step)' (eval/reports/DOGFOOD-4-baseline.md M14)."""
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
    """CANnectivity: led0 and counter2 'changed: phandle' (eval/reports/DOGFOOD-4-baseline.md M13)."""
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
        old=SimpleNamespace(src=Path("/app")),
        build_files=None,
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
    """eval/AUDIT.md F1/§5.2: samples/subsys/settings sets CONFIG_STDOUT_CONSOLE=y, which
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
    """A fatal Kconfig error at both refs is a defect, not a no-op: per DESIGN.md, "Pre-existing problems" it
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
    """DESIGN.md, "Pre-existing problems" for the devicetree configure errors: same error at both refs ->
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
    """eval/AUDIT.md §5.2 run 1: the settings sample built without littlefs fails at v4.1.0 and
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
            old=SimpleNamespace(workspace=ws, spec=SimpleNamespace(app=tmp_path), src=tmp_path),
            new=SimpleNamespace(workspace=ws),
            old_img=SimpleNamespace(has=lambda a: False, dir=tmp_path / "b"),
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


def test_binding_spec_changes() -> None:
    from zmig.analyzers.devicetree import spec_changes

    old = {"a": {"type": "int", "required": False, "deprecated": False}}
    assert spec_changes(old, old, "a") == []
    assert spec_changes(old, {}, "a") == []  # gone: other findings say so
    assert spec_changes(old, {"a": {**old["a"], "type": "array"}}, "a") == [
        ("type", "int", "array")
    ]
    assert spec_changes(old, {"a": {**old["a"], "deprecated": True}}, "a") == [
        ("deprecated", False, True)
    ]
    # Still deprecated is not news; newly required doesn't matter to a property that's set.
    dep = {"a": {**old["a"], "deprecated": True}}
    assert spec_changes(dep, dep, "a") == []
    assert spec_changes(old, {"a": {**old["a"], "required": True}}, "a") == []


def test_devicetree_binding_checks_for_read_properties_and_required_errors(
    tmp_path: Path,
) -> None:
    """eval/AUDIT.md §6 item 6: binding changes to properties the app's C code reads (type,
    deprecation), and the cause of a "marked as required" configure error."""
    from tests.test_diagnostics import REQUIRED_LOG
    from zmig.analyzers.devicetree import DevicetreeAnalyzer
    from zmig.diagnostics import parse_log
    from zmig.findings import Severity
    from zmig.usage import Loc, NodeRef

    def node(specs: dict[str, Any]) -> dict[str, Any]:
        return {"name": "can", "labels": ["can0"], "aliases": [], "compats": ["x,can"],
                "matching_compat": "x,can", "status": "okay", "ord": 5,
                "binding": "dts/bindings/can/x,can.yaml",
                "props": {"bitrate": 125000, "sjw": 1}, "raw_props": ["bitrate", "sjw"],
                "raw_values": {"bitrate": "<0x1e848>", "sjw": "<0x1>"}, "specs": specs}  # fmt: skip

    spec = {"type": "int", "required": False, "deprecated": False}
    old = {"nodes": {"/can": node({"bitrate": spec, "sjw": spec})}, "chosen": {}}
    new = {"nodes": {"/can": node({"bitrate": {**spec, "deprecated": True},
                                   "sjw": {**spec, "type": "array"}})}, "chosen": {}}  # fmt: skip
    ref = NodeRef("label", "can0")
    src = Loc("src/main.c", 9, "DT_PROP(DT_NODELABEL(can0), bitrate)")
    ctx = SimpleNamespace(
        old_edt=old,
        new_edt=new,
        old_facts=parse_log(""),
        new_raw_facts=parse_log(REQUIRED_LOG),
        new_raw=SimpleNamespace(src=tmp_path),
        usage=SimpleNamespace(
            app=tmp_path,
            dt_nodes=[(ref, src)],
            # The overlay sets bitrate too: one finding for it, not two with one ID.
            dt_props_set=[(ref, "bitrate", Loc("app.overlay", 3, "bitrate = <125000>;"))],
            dt_props_read=[
                (ref, "bitrate", src, "DT_PROP"),
                (ref, "sjw", Loc("src/main.c", 10, "DT_PROP(...)"), "DT_PROP"),
            ],
            dt_compats={},
        ),
        build_files={"src/main.c"},
        old_spec="v4.1.0",
        new_spec="v4.2.0",
        scope=("s",),
        runner=SimpleNamespace(redact=lambda s: s),
    )
    found = DevicetreeAnalyzer().analyze(ctx).findings  # type: ignore[arg-type]
    by_kind: dict[str, list[Any]] = {}
    for f in found:
        by_kind.setdefault(f.kind, []).append(f)
    assert len({f.id for f in found}) == len(found)

    (dep,) = by_kind["property-deprecated"]
    assert dep.subject == "/can:bitrate" and dep.severity is Severity.RISKY
    (typ,) = by_kind["property-type-changed"]
    assert typ.subject == "/can:sjw" and typ.severity is Severity.RISKY
    assert typ.old == "int" and typ.new == "array"
    assert any(e.get("path") == "src/main.c" for e in typ.evidence)

    (err,) = by_kind["dt-error"]
    assert err.subject == parse_log(REQUIRED_LOG).diagnostics[0].message[:80]  # ID unchanged
    assert "phy-handle" in err.title and "/soc/ethernet@40028000" in err.title
    assert "Add 'phy-handle'" in err.action
    assert err.new["property"] == "phy-handle"


def test_dependency_expression_changes_are_one_info_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """eval/AUDIT.md §6 item 5, as info: on echo_server v4.1.0 -> v4.2.0, 18 app-referenced
    symbols changed depends/selects/implies, mostly SoC lists and symbols set only in
    overlays the build doesn't use; any change that takes effect in this build already
    shows as a value change. One finding lists the ones this build's inputs use, and
    value findings name the expression change in their action."""
    from zmig.analyzers.kconfig import KconfigAnalyzer
    from zmig.diagnostics import parse_log
    from zmig.findings import Severity
    from zmig.usage import Loc

    monkeypatch.setattr(KconfigAnalyzer, "_promote_dependencies", staticmethod(lambda *a: None))

    def sym(depends: str, selects: tuple[str, ...] = ()) -> dict[str, Any]:
        return {"depends": depends, "selects": list(selects), "implies": [], "defaults": []}

    ok = {"POSIX_API": sym("!NATIVE_LIBC", ("POSIX_BASE",)), "NET_TCP": sym("NET_IPV4"),
          "OPENTHREAD_DEBUG": sym("NET_L2_OPENTHREAD"), "LOG": sym(""), "PM": sym("A || B")}  # fmt: skip
    nk = {"POSIX_API": sym("!NATIVE_LIBC", ("POSIX_BASE", "POSIX_MESSAGE_PASSING")),
          "NET_TCP": sym("NET_IPV4 || NET_IPV6"), "OPENTHREAD_DEBUG": sym("OPENTHREAD && 0"),
          "LOG": sym(""), "PM": sym("A || B || C")}  # fmt: skip
    app = tmp_path / "app"
    ctx = SimpleNamespace(
        old_config={"POSIX_API": "y", "NET_TCP": "y", "LOG": "y"},
        new_config={"POSIX_API": "y", "NET_TCP": "n", "LOG": "y"},
        old_kconfig=ok,
        new_kconfig=nk,
        old_facts=parse_log(f"Merged configuration '{app}/prj.conf'\n"),
        new_raw_facts=parse_log(""),
        old=SimpleNamespace(src=app),
        build_files={"src/main.c"},
        usage=SimpleNamespace(
            kconfig_set={
                "POSIX_API": [(Loc("prj.conf", 8, "CONFIG_POSIX_API=y"), "y")],
                "NET_TCP": [(Loc("prj.conf", 9, "CONFIG_NET_TCP=y"), "y")],
                # Only in an overlay this build doesn't apply.
                "OPENTHREAD_DEBUG": [(Loc("overlay-ot.conf", 3, "CONFIG_OPENTHREAD_DEBUG=y"), "y")],
                "LOG": [(Loc("prj.conf", 1, "CONFIG_LOG=y"), "y")],
            },
            # Read only in a file this build doesn't compile.
            kconfig_read={"PM": [Loc("src/pm.c", 4, "#ifdef CONFIG_PM")]},
        ),
        old_spec="v4.1.0",
        new_spec="v4.2.0",
        scope=("s",),
        salvage=(),
        is_app=True,
        runner=SimpleNamespace(redact=lambda s: s),
    )
    found = KconfigAnalyzer().analyze(ctx).findings  # type: ignore[arg-type]
    (agg,) = [f for f in found if f.kind == "dependency-expression-changed"]
    assert agg.severity is Severity.INFO and agg.subject == "dependency-expressions"
    assert sorted(agg.new) == ["NET_TCP", "POSIX_API"]
    assert agg.new["NET_TCP"] == {"depends": ["NET_IPV4", "NET_IPV4 || NET_IPV6"]}
    assert agg.new["POSIX_API"] == {
        "selects": [["POSIX_BASE"], ["POSIX_BASE", "POSIX_MESSAGE_PASSING"]]
    }
    (vnt,) = [f for f in found if f.kind == "value-not-taken"]
    assert "NET_IPV4 || NET_IPV6" in vnt.action and "NET_IPV4" in vnt.action


def test_fragments_outside_the_app_dir_count_as_used(tmp_path: Path) -> None:
    """DOGFOOD-8 B1, CoffeeCaller: the source build merged
    <project>/boards/extensions/native_sim/native_sim.conf. Kconfig usage cites it relative
    to the app (`../../boards/...`), so it must match that form, not be judged unused."""
    from dataclasses import replace

    from zmig.analyzers import outside_build
    from zmig.findings import Category, Confidence, Finding, Severity, file_line

    project = tmp_path / "CoffeeCaller"
    app = project / "applications" / "coffeecaller"
    frag = project / "boards" / "extensions" / "native_sim" / "native_sim.conf"
    frag.parent.mkdir(parents=True)
    frag.write_text("CONFIG_NATIVE_UART_0_ON_STDINOUT=y\n")
    app.mkdir(parents=True)
    img_dir = tmp_path / "build"
    (img_dir / "zephyr").mkdir(parents=True)
    log = img_dir / "log"
    log.write_text(
        f"Merged configuration '{frag}'\nMerged configuration '{tmp_path}/copy/prj.conf'\n"
    )
    (img_dir / "zephyr" / "zephyr.dts.d").write_text("")
    img = SimpleNamespace(dir=img_dir, log=log)
    b = SimpleNamespace(src=tmp_path / "copy", workspace=SimpleNamespace(root=tmp_path / "ws"),
                        images=(img,), spec=SimpleNamespace(app=app))  # fmt: skip
    base = SimpleNamespace(old=b, new=b, new_raw=b, build_files=None)
    f = Finding(Category.KCONFIG, "symbol-removed", "CONFIG_NATIVE_UART_0_ON_STDINOUT", "t",
                Severity.BREAKING, Confidence.HIGH, "a", "kconfig", ("s",),
                evidence=(file_line("../../boards/extensions/native_sim/native_sim.conf", 11, "x"),))  # fmt: skip
    other = replace(
        f,
        subject="CONFIG_B",
        evidence=(file_line("../../boards/extensions/other/other.conf", 1, "x"),),
    )
    out = outside_build([f, other], base)  # type: ignore[arg-type]
    assert out[0].severity is Severity.BREAKING
    assert out[1].severity is Severity.INFO  # a fragment no build merged is still judged unused


def test_dts_values_that_differ_only_in_wrapping_are_unchanged(tmp_path: Path) -> None:
    """DOGFOOD-8 B2, RIT FSW: v4.2's dtlib wraps long cell lists, so `clocks` and
    `irq-gpios` of three referenced nodes were reported as changed with equal values."""
    from zmig.analyzers.devicetree import DevicetreeAnalyzer
    from zmig.diagnostics import parse_log
    from zmig.usage import Loc, NodeRef

    def node(label: str, raw: dict[str, str]) -> dict[str, Any]:
        return {"name": label, "labels": [label], "aliases": [], "compats": ["st,stm32-rtc"],
                "matching_compat": "st,stm32-rtc", "status": "okay", "ord": 5,
                "binding": "dts/bindings/rtc/st,stm32-rtc.yaml", "props": {}, "raw_props": sorted(raw),
                "raw_values": raw, "specs": {}}  # fmt: skip

    wrapped = "< &rcc 0x40 0x10000000 >,\n         < &rcc 0x3 0x10c80070 >"
    flat = "< &rcc 0x40 0x10000000 >, < &rcc 0x3 0x10c80070 >"
    old = {
        "nodes": {
            "/rtc": node("rtc", {"clocks": flat}),
            "/gpio": node("gpio", {"ngpios": "<0x10>"}),
        },
        "chosen": {},
    }
    new = {
        "nodes": {
            "/rtc": node("rtc", {"clocks": wrapped}),
            "/gpio": node("gpio", {"ngpios": "<0x8>"}),
        },
        "chosen": {},
    }
    ctx = SimpleNamespace(
        old_edt=old, new_edt=new, old_facts=parse_log(""), new_raw_facts=parse_log(""),
        new_raw=SimpleNamespace(src=tmp_path),
        usage=SimpleNamespace(app=tmp_path, dt_props_set=[], dt_props_read=[], dt_compats={},
                              dt_nodes=[(NodeRef("label", "rtc"), Loc("src/a.c", 1, "x")),
                                        (NodeRef("label", "gpio"), Loc("src/a.c", 2, "y"))]),
        build_files={"src/a.c"}, old_spec="v4.1.0", new_spec="v4.2.0", scope=("s",),
        runner=SimpleNamespace(redact=lambda s: s),
        old_img=SimpleNamespace(has=lambda a: False), new_img=SimpleNamespace(has=lambda a: False),
    )  # fmt: skip
    found = DevicetreeAnalyzer().analyze(ctx).findings  # type: ignore[arg-type]
    changed = [f for f in found if f.kind == "node-changed"]
    assert len(changed) == 1 and changed[0].subject.endswith("/gpio")  # a real value change
    assert changed[0].new == {"ngpios": ["<0x10>", "<0x8>"]}


def test_build_sites_do_not_depend_on_the_order_a_parallel_build_printed_them(
    tmp_path: Path,
) -> None:
    """DOGFOOD-12 E8: after a rebuild, CoffeeCaller's build failure and ZMK's new warning
    listed different "first" sites. A parallel build prints files in any order, so errors
    (compiler before linker) and a warning's sites are ordered by file and line."""
    from types import SimpleNamespace

    from zmig.analyzers.buildlog import BuildlogAnalyzer

    app = tmp_path / "app"
    a = f"{app}/src/a.c:3:5: error: 'x' undeclared\n"
    b = f"{app}/src/b.c:1:2: error: 'y' undeclared\n"
    link = "/usr/bin/ld: undefined reference to `z'\n"
    one, other = build_errors(a + b + link), build_errors(link + b + a)
    assert one == other
    assert [(e.kind, e.file) for e in one] == [
        ("compiler", f"{app}/src/a.c"),
        ("compiler", f"{app}/src/b.c"),
        ("linker", ""),
    ]

    # One warning raised in a Zephyr header, reached from two project files (ZMK's shape).
    warn = "/ws/zephyr/include/zephyr/k.h:7:1: warning: 'k_foo' is deprecated [-Wdeprecated-declarations]\n"
    logs = {}
    for name, order in (("x", ("b", "a")), ("y", ("a", "b"))):
        p = tmp_path / f"{name}.log"
        p.write_text("".join(f"In file included from {app}/src/{f}.c:9:\n{warn}" for f in order))
        logs[name] = p
    empty = tmp_path / "empty.log"
    empty.write_text("")
    sites = []
    for name in ("x", "y"):
        b_ctx = SimpleNamespace(workspace=SimpleNamespace(zephyr=tmp_path / "z", root=tmp_path),
                                src=app, dir=tmp_path / "build")  # fmt: skip
        ctx = SimpleNamespace(
            old=b_ctx, new=b_ctx, prior=[], new_spec="v2",
            old_img=SimpleNamespace(log=empty, dir=tmp_path / "o"),
            new_img=SimpleNamespace(log=logs[name], dir=tmp_path / "n"),
            runner=SimpleNamespace(redact=lambda s: s),
        )  # fmt: skip
        found: list[Any] = []
        BuildlogAnalyzer._warnings(ctx, lambda *a, out=found: out.append(a))  # type: ignore[arg-type]
        sites.append(found[0][7]["sites"])  # add(kind, subject, title, sev, action, ev, old, new)
    assert sites[0] == sites[1] == ["src/a.c:9", "src/b.c:9"]


def test_repeated_build_errors_do_not_crowd_out_other_sites() -> None:
    """DOGFOOD-12 E8, ZMK: the same missing-header error is printed once per file that
    includes the header. Ordered by file, five copies of `behavior.h:276` filled the
    evidence and dropped `ext_power.h:104`. The evidence lists distinct sites."""
    from zmig.analyzers.buildlog import BuildError, error_evidence

    def err(f: str, ln: int, msg: str) -> BuildError:
        return BuildError("compiler", msg, f"/app/include/drivers/{f}", ln)

    errors = [err("behavior.h", 276, "syscalls/behavior.h: No such file or directory")] * 6
    errors.append(err("ext_power.h", 104, "syscalls/ext_power.h: No such file or directory"))
    ev = error_evidence(errors, {"/app": ""}, lambda s: s)
    assert [(e["path"], e["line"]) for e in ev] == [
        ("include/drivers/behavior.h", 276),
        ("include/drivers/ext_power.h", 104),
    ]


def _warn_ctx(tmp_path: Path, log: str, kconfig_set: dict[str, Any] | None = None) -> Any:
    from types import SimpleNamespace

    app = tmp_path / "app"
    empty, target = tmp_path / "old.log", tmp_path / "new.log"
    empty.write_text("")
    target.write_text(log)
    b = SimpleNamespace(workspace=SimpleNamespace(zephyr=tmp_path / "z", root=tmp_path),
                        src=app, dir=tmp_path / "build")  # fmt: skip
    return SimpleNamespace(
        old=b, new=b, prior=[], new_spec="v4.4.0",
        old_img=SimpleNamespace(log=empty, dir=tmp_path / "o"),
        new_img=SimpleNamespace(log=target, dir=tmp_path / "n"),
        runner=SimpleNamespace(redact=lambda s: s),
        usage=SimpleNamespace(kconfig_set=kconfig_set or {}),
    )  # fmt: skip


def _warned(ctx: Any) -> list[Any]:
    from zmig.analyzers.buildlog import BuildlogAnalyzer

    found: list[Any] = []
    BuildlogAnalyzer._warnings(ctx, lambda *a, out=found: out.append(a))  # type: ignore[arg-type]
    return found


def test_a_cmake_warning_about_a_kconfig_value_is_cited_at_its_assignment(tmp_path: Path) -> None:
    """DOGFOOD-13 F1, edgehog H8: Zephyr's lib/os/CMakeLists.txt warns "CONFIG_ZVFS_OPEN_MAX
    is less than requested minimum: 25 < 26". The project sets it at prj.conf:171, so
    that's where the warning is, and it's risky, not info."""
    from zmig.findings import Severity
    from zmig.usage import Loc

    log = (
        f"CMake Warning at {tmp_path}/z/lib/os/CMakeLists.txt:60 (message):\n\n\n"
        "            CONFIG_ZVFS_OPEN_MAX is less than requested minimum:\n"
        "              25 < 26\n            Setting the file descriptor size to 26\n\n\n"
    )
    loc = Loc("prj.conf", 171, "CONFIG_ZVFS_OPEN_MAX=25")
    (w,) = _warned(_warn_ctx(tmp_path, log, {"ZVFS_OPEN_MAX": [(loc, "25")]}))
    assert w[3] is Severity.RISKY  # add(kind, subject, title, sev, action, ev, old, new)
    assert [(e["path"], e["line"]) for e in w[5] if e["type"] == "file_line"] == [("prj.conf", 171)]


def test_a_deprecated_header_is_cited_where_the_project_includes_it(tmp_path: Path) -> None:
    """DOGFOOD-13 F2, edgehog H1: zephyr/fs/nvs.h warns from its own `__WARN`. GCC prints the
    include chain to toolchain/gcc.h (nvs.c:7 -> nvs.h:15 -> device.h -> ...), not to
    fs/nvs.h, so the warning was cited at nvs.c:7. The deprecated header is the last
    macro-expansion note; the sites are the project lines that include it."""
    z = tmp_path / "z" / "include" / "zephyr"
    lib = tmp_path / "mod" / "lib"
    (lib / "include").mkdir(parents=True)
    (lib / "include" / "nvs.h").write_text(
        "#pragma once\n#include <zephyr/kernel.h>\n#include <zephyr/fs/nvs.h>\n"
    )
    (lib / "nvs.c").write_text(
        '#include "nvs.h"\n#include <zephyr/drivers/flash.h>\n#include <zephyr/fs/nvs.h>\n'
    )
    log = (
        f"In file included from {z}/sys_clock.h:7,\n"
        f"                 from {lib}/include/nvs.h:2,\n"
        f"                 from {lib}/nvs.c:1:\n"
        f"{z}/toolchain/gcc.h:357:20: warning: This header file is deprecated, please use zephyr/kvss/nvs.h instead.\n"
        "  357 | #define __WARN1(s) _Pragma(#s)\n"
        f"{z}/toolchain/gcc.h:356:21: note: in expansion of macro '__WARN1'\n"
        f"{z}/fs/nvs.h:17:1: note: in expansion of macro '__WARN'\n"
    )
    ctx = _warn_ctx(tmp_path, log)
    from zmig.analyzers import buildlog

    roots = {str(tmp_path / "app"): "", str(tmp_path / "mod"): "mod/"}
    orig = buildlog.project_roots
    buildlog.project_roots = lambda b, d: roots  # type: ignore[assignment]
    try:
        (w,) = _warned(ctx)
    finally:
        buildlog.project_roots = orig  # type: ignore[assignment]
    assert [(e["path"], e["line"]) for e in w[5] if e["type"] == "file_line"] == [
        ("mod/lib/include/nvs.h", 3),
        ("mod/lib/nvs.c", 3),
    ]


def test_value_not_taken_names_a_new_dependency_that_is_off() -> None:
    """DOGFOOD-13 F3, edgehog H6: MBEDTLS_KEY_EXCHANGE_ECDHE_ECDSA_ENABLED=y isn't taken at
    v4.4.0; its unmet dependency is a long expression, and the one symbol to add is
    MBEDTLS_X509_CRT_PARSE_C, new at v4.4.0, settable, and off."""
    from zmig.analyzers.kconfig import new_dependencies_off

    deps = "(PSA_WANT_ALG_ECDH && PSA_WANT_ALG_ECDSA && MBEDTLS_X509_CRT_PARSE_C && MBEDTLS)"
    old = {"PSA_WANT_ALG_ECDH": {}, "PSA_WANT_ALG_ECDSA": {}, "MBEDTLS": {}}
    new = {"PSA_WANT_ALG_ECDH": {"prompt": "x"}, "PSA_WANT_ALG_ECDSA": {"prompt": "x"},
           "MBEDTLS": {"prompt": "x"}, "MBEDTLS_X509_CRT_PARSE_C": {"prompt": "X.509 parsing"},
           "NEW_HIDDEN": {"prompt": None}}  # fmt: skip
    config = {"PSA_WANT_ALG_ECDH": "y", "MBEDTLS": "y"}
    assert new_dependencies_off(deps + " || NEW_HIDDEN", old, new, config) == [
        "MBEDTLS_X509_CRT_PARSE_C"
    ]
