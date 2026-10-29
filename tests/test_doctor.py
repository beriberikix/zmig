"""`zmig doctor` checks, and the PYTHONPATH scrub in every command zmig runs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from zmig import doctor
from zmig.cache import Cache
from zmig.tools import Runner


def _git(cwd: Path, *args: str) -> None:
    ident = {f"GIT_{r}_{k}": v for r in ("AUTHOR", "COMMITTER") for k, v in
             (("NAME", "t"), ("EMAIL", "t@example.com"))}  # fmt: skip
    env = {**ident, "HOME": str(cwd), "PATH": "/usr/bin:/bin"}
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


@pytest.fixture
def cache(tmp_path: Path) -> Cache:
    """A cache with a Zephyr 'mirror' holding two board yamls and SDK_VERSION, and one
    SDK (1.0.1, arm only, the >= 1.0 gnu/ layout)."""
    c = Cache(tmp_path / "cache")
    c.ensure()
    w = tmp_path / "zephyr"
    (w / "boards/st/nucleo_h563zi").mkdir(parents=True)
    (w / "boards/st/nucleo_h563zi/nucleo_h563zi.yaml").write_text(
        "identifier: nucleo_h563zi\narch: arm\ntoolchain:\n  - zephyr\n  - gnuarmemb\n"
    )
    (w / "boards/espressif/esp32s3_devkitc").mkdir(parents=True)
    (w / "boards/espressif/esp32s3_devkitc/esp32s3_devkitc_procpu.yaml").write_text(
        "identifier: esp32s3_devkitc/esp32s3/procpu\narch: xtensa\ntoolchain:\n  - zephyr\n"
    )
    (w / "SDK_VERSION").write_text("1.0.1\n")
    _git(w, "init", "-q", "-b", "main")
    _git(w, "add", "-A")
    _git(w, "commit", "-q", "-m", "z")
    _git(tmp_path, "clone", "-q", "--bare", str(w), str(c.mirrors / doctor.ZEPHYR_MIRROR))
    sdk = c.root / "sdks" / "zephyr-sdk-1.0.1"
    gcc = sdk / "gnu/arm-zephyr-eabi/bin/arm-zephyr-eabi-gcc"
    gcc.parent.mkdir(parents=True)
    gcc.touch()
    (sdk / "sdk_version").write_text("1.0.1\n")
    return c


def _by_name(checks: list[doctor.Check]) -> dict[str, doctor.Check]:
    return {c.name: c for c in checks}


def test_arm_board_finds_the_sdk_with_its_toolchain(
    cache: Cache, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        doctor, "find_sdks", lambda explicit, c: [(c.root / "sdks/zephyr-sdk-1.0.1", "1.0.1")]
    )
    got = _by_name(doctor.check_sdk("nucleo_h563zi/stm32h563xx", None, "HEAD", cache))
    assert got["board"].status == "ok" and "arch arm" in got["board"].detail
    assert got["zephyr sdk"].status == "ok"
    assert "arm-zephyr-eabi in" in got["zephyr sdk"].detail
    assert "needs >= 1.0.1" in got["zephyr sdk"].detail


def test_missing_toolchain_and_old_sdk_fail_with_an_action(
    cache: Cache, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdk = cache.root / "sdks/zephyr-sdk-1.0.1"
    monkeypatch.setattr(doctor, "find_sdks", lambda explicit, c: [(sdk, "1.0.1")])
    x = _by_name(doctor.check_sdk("esp32s3_devkitc/esp32s3/procpu", None, "HEAD", cache))
    assert x["zephyr sdk"].status == "FAIL"
    assert "xtensa-*_esp32s3_zephyr-elf" in x["zephyr sdk"].detail
    assert "install the Zephyr SDK" in x["zephyr sdk"].action
    # An explicit --sdk is judged alone: too old for the ref's SDK_VERSION.
    monkeypatch.setattr(doctor, "find_sdks", lambda explicit, c: [(sdk, "0.16.8")])
    old = _by_name(doctor.check_sdk("nucleo_h563zi", sdk, "HEAD", cache))
    assert old["zephyr sdk"].status == "FAIL" and "needs SDK >= 1.0.1" in old["zephyr sdk"].detail


def test_unknown_board_and_host_board(cache: Cache) -> None:
    bad = _by_name(doctor.check_sdk("no_such_board", None, "HEAD", cache))
    assert bad["board"].status == "FAIL" and "west boards" in bad["board"].action
    host = _by_name(doctor.check_sdk("native_sim", None, "HEAD", cache))
    assert host["zephyr sdk"].status == "ok" and "not needed" in host["zephyr sdk"].detail


def test_no_mirror_yet_says_what_to_do(tmp_path: Path) -> None:
    c = Cache(tmp_path / "empty")
    c.ensure()
    (got,) = doctor.check_sdk("nucleo_h563zi", None, "HEAD", c)
    assert got.status == "warn" and "run any zmig scan or plan once" in got.action


def test_find_sdks_reads_the_cache_and_the_sdk_layouts(cache: Cache) -> None:
    sdk = cache.root / "sdks/zephyr-sdk-1.0.1"
    assert (sdk, "1.0.1") in doctor.find_sdks(None, cache)
    assert doctor.has_toolchain(sdk, "arm-zephyr-eabi")
    assert not doctor.has_toolchain(sdk, "riscv64-zephyr-elf")


def test_pythonpath_is_warned_and_tools_check_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/opt/ros/lyrical/lib/python3.14/site-packages")
    got = _by_name(doctor.check_python())
    assert got["PYTHONPATH"].status == "warn" and "env -u PYTHONPATH" in got["PYTHONPATH"].action
    assert doctor._versioned_tool("git", (999,)).status == "FAIL"
    monkeypatch.setenv("PATH", "/nonexistent")
    missing = doctor._versioned_tool("ninja", None)
    assert missing.status == "FAIL" and "ninja-build" in missing.action
    assert "1 check(s) failed" in doctor.format_checks([missing])


def test_runner_never_passes_pythonpath(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/opt/ros/lyrical/lib/python3.14/site-packages")
    out = Runner().run([sys.executable, "-c", "import os; print(os.environ.get('PYTHONPATH'))"])
    assert out.stdout.strip() == "None"
