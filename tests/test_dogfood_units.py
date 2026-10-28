"""Unit regressions from dogfooding on ZMK v3.2 -> v3.5 (docs/DOGFOOD-1.md). The log
excerpts are copied from the real target configure/build output (paths shortened)."""

# ruff: noqa: E501  (verbatim log lines)

from __future__ import annotations

from pathlib import Path

from zmig.analyzers.buildlog import build_errors
from zmig.buildctx import kconfig_block, locate_salvage_edits
from zmig.diagnostics import parse_log

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
