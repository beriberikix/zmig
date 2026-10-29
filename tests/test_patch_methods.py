"""Patch-application methods in fork triage, on real OpenDeck cases (DOGFOOD-5 §4).

OpenDeck's build tool applies its zephyr/patch/*.patch files with plain `git apply`
(zenv's scripts/patch.sh). Three of its patches merge cleanly three-way onto Zephyr
70be2ff but fail `git apply`; fork triage, which modelled only `git rebase`, said
"applies" for all three. This was a misleading-output fix made after seeing the OpenDeck
results; the fixture is generic Zephyr content plus those patches.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from zmig.forks import CommitResult, classify
from zmig.tools import Runner

FIX = Path(__file__).parent / "fixtures" / "patch-methods"
PATCHES = (
    "esp32_eth_enable_rmii_output_clock.patch",
    "esp32_eth_pass_multicast_frames.patch",
    "kconfig_dt_node_prop_len.patch",
)


def _git(cwd: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "PATH": "/usr/bin:/bin",
        "HOME": str(cwd),
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> tuple[SimpleNamespace, str, str, str]:
    """A repo with upstream base and target commits (the real files) and a fork branch:
    base plus the three patches, one commit each, subject = the patch file name."""
    w = tmp_path_factory.mktemp("w")
    _git(w, "init", "-q", "-b", "main")
    shutil.copytree(FIX / "base", w, dirs_exist_ok=True)
    _git(w, "add", "-A")
    _git(w, "commit", "-q", "-m", "upstream base 769c4f2")
    base = _git(w, "rev-parse", "HEAD")
    _git(w, "checkout", "-q", "-b", "fork")
    for p in PATCHES:
        _git(w, "apply", str(FIX / "patches" / p))
        _git(w, "add", "-A")
        _git(w, "commit", "-q", "-m", p)
    head = _git(w, "rev-parse", "HEAD")
    _git(w, "checkout", "-q", "main")
    shutil.copytree(FIX / "target", w, dirs_exist_ok=True)
    _git(w, "add", "-A")
    _git(w, "commit", "-q", "-m", "upstream target 70be2ff")
    target = _git(w, "rev-parse", "HEAD")
    # Only `main` is upstream; the fork's commits are local (classify uses --branches).
    _git(w, "branch", "-D", "fork")
    _git(w, "update-ref", "refs/fork/head", head)
    mirror = SimpleNamespace(path=w / ".git", git=shutil.which("git"), runner=Runner())
    return mirror, head, base, target


def _by_patch(commits: tuple[CommitResult, ...]) -> dict[str, CommitResult]:
    return {c.subject: c for c in commits}


def test_default_runs_git_apply_and_3way_and_takes_the_stricter(
    repo: tuple[SimpleNamespace, str, str, str],
) -> None:
    mirror, head, base, target = repo
    res = _by_patch(classify(mirror, head, base, target).commits)  # type: ignore[arg-type]
    rmii = res["esp32_eth_enable_rmii_output_clock.patch"]
    kcfg = res["kconfig_dt_node_prop_len.patch"]
    mcast = res["esp32_eth_pass_multicast_frames.patch"]
    for c in (rmii, kcfg):
        assert {m.method: m.state for m in c.methods} == {
            "git-apply": "conflicts",
            "3way": "applies",
        }
        assert c.disagreement and c.state == "conflicts"
    # The commit's own diff is applied, so the hunk line includes the offset at which the
    # patch landed on the base (the patch file itself says 707).
    assert rmii.failed_hunks == ("drivers/ethernet/eth_esp32.c:709",)
    assert kcfg.failed_hunks == ("scripts/kconfig/kconfigfunctions.py:663",)
    # Upstream added the same call (with a comment): absorbed, so the class is upstreamed.
    assert mcast.absorbed and (mcast.state, mcast.match) == ("upstreamed", "absorbed")
    assert {m.method: m.state for m in mcast.methods}["git-apply"] == "conflicts"


def test_3way_alone_reproduces_the_old_answer(
    repo: tuple[SimpleNamespace, str, str, str],
) -> None:
    mirror, head, base, target = repo
    res = _by_patch(classify(mirror, head, base, target, methods=("3way",)).commits)  # type: ignore[arg-type]
    assert res["esp32_eth_enable_rmii_output_clock.patch"].state == "applies"
    assert res["kconfig_dt_node_prop_len.patch"].state == "applies"
    assert not any(c.disagreement for c in res.values())


def test_git_apply_alone_and_git_am_agree(repo: tuple[SimpleNamespace, str, str, str]) -> None:
    mirror, head, base, target = repo
    a = classify(mirror, head, base, target, methods=("git-apply",)).commits  # type: ignore[arg-type]
    m = classify(mirror, head, base, target, methods=("git-am",)).commits  # type: ignore[arg-type]
    assert [c.state for c in a] == [c.state for c in m] == ["conflicts", "upstreamed", "conflicts"]


def test_findings_name_the_patch_file_and_the_failing_hunk(
    repo: tuple[SimpleNamespace, str, str, str],
) -> None:
    from zmig.analyzers.forks import ForksAnalyzer

    mirror, head, base, target = repo
    c = _by_patch(classify(mirror, head, base, target).commits)["kconfig_dt_node_prop_len.patch"]  # type: ignore[arg-type]
    f = ForksAnalyzer._commit_finding(
        c, "zephyr", ("s",), "zephyr/patch/zephyr/kconfig_dt_node_prop_len.patch"
    )
    assert f.kind == "conflicts"
    assert "(fails under git-apply; applies under 3way)" in f.title
    assert f.action == (
        "Refresh the project's patch zephyr/patch/zephyr/kconfig_dt_node_prop_len.patch "
        "against the target; failing hunk(s): scripts/kconfig/kconfigfunctions.py:663."
    )
    mc = _by_patch(classify(mirror, head, base, target).commits)[  # type: ignore[arg-type]
        "esp32_eth_pass_multicast_frames.patch"
    ]
    g = ForksAnalyzer._commit_finding(
        mc, "zephyr", ("s",), "p/esp32_eth_pass_multicast_frames.patch"
    )
    assert g.kind == "upstreamed" and "upstream already absorbed the change" in g.action
    assert "Drop the project's patch p/esp32_eth_pass_multicast_frames.patch" in g.action


NEAR = (
    "esp32s3_reset_with_watchdog_after_flash.patch",
    "usb_midi2_01_configurable_buffer_size.patch",
)


@pytest.fixture(scope="module")
def near_repo(tmp_path_factory: pytest.TempPathFactory) -> tuple[SimpleNamespace, str, str, str]:
    w = tmp_path_factory.mktemp("near")
    _git(w, "init", "-q", "-b", "main")
    shutil.copytree(FIX / "base", w, dirs_exist_ok=True)
    _git(w, "add", "-A")
    _git(w, "commit", "-q", "-m", "upstream base 769c4f2")
    base = _git(w, "rev-parse", "HEAD")
    _git(w, "checkout", "-q", "-b", "fork")
    for p in NEAR:
        _git(w, "apply", str(FIX / "patches" / p))
        _git(w, "add", "-A")
        _git(w, "commit", "-q", "-m", p)
    head = _git(w, "rev-parse", "HEAD")
    _git(w, "checkout", "-q", "main")
    shutil.copytree(FIX / "target", w, dirs_exist_ok=True)
    _git(w, "add", "-A")
    _git(w, "commit", "-q", "-m", "upstream target 70be2ff")
    target = _git(w, "rev-parse", "HEAD")
    _git(w, "branch", "-D", "fork")
    _git(w, "update-ref", "refs/fork/head", head)
    mirror = SimpleNamespace(path=w / ".git", git=shutil.which("git"), runner=Runner())
    return mirror, head, base, target


def test_overlap_is_risky_only_next_to_the_patch_lines(
    near_repo: tuple[SimpleNamespace, str, str, str],
) -> None:
    """Post-baseline fix (DOGFOOD-5 plan precision): 7 OpenDeck patches that apply were
    flagged risky because upstream changed the same files, 5-176 lines away. Only a change
    within 3 lines (git's diff context) of the patch's own lines stays risky."""
    from zmig.analyzers.forks import ForksAnalyzer
    from zmig.findings import Severity

    mirror, head, base, target = near_repo
    res = _by_patch(classify(mirror, head, base, target).commits)  # type: ignore[arg-type]
    midi = res["usb_midi2_01_configurable_buffer_size.patch"]
    wdt = res["esp32s3_reset_with_watchdog_after_flash.patch"]
    assert midi.state == wdt.state == "applies"
    assert midi.overlap and midi.overlap_near == ()
    assert wdt.overlap_near == ("boards/espressif/esp32s3_devkitc/board.cmake",)
    fm = ForksAnalyzer._commit_finding(midi, "zephyr", ("s",))
    fw = ForksAnalyzer._commit_finding(wdt, "zephyr", ("s",))
    assert (fm.severity, fw.severity) == (Severity.INFO, Severity.RISKY)
    assert "upstream changed other parts of" in fm.title
    assert "upstream changed lines next to it" in fw.title
