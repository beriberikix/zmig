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


def test_overlap_is_risky_only_inside_the_patch_hunks(
    near_repo: tuple[SimpleNamespace, str, str, str],
) -> None:
    """Post-baseline fix (DOGFOOD-5 plan precision): 7 OpenDeck patches that apply were
    flagged risky because upstream changed the same files, 1-176 lines away. Overlap is
    risky only inside a hunk's window: its changed lines plus the context lines the patch
    carries. The watchdog patch's one hunk ends at the end of board.cmake (1 trailing
    context line); upstream appended after it, outside the window (it was risky under the
    earlier fixed 3-line rule)."""
    from zmig.analyzers.forks import ForksAnalyzer
    from zmig.findings import Severity

    mirror, head, base, target = near_repo
    texts = {p: (FIX / "patches" / p).read_text() for p in NEAR}
    for patch_text in (None, texts.get):
        res = _by_patch(classify(mirror, head, base, target, patch_text=patch_text).commits)  # type: ignore[arg-type]
        midi = res["usb_midi2_01_configurable_buffer_size.patch"]
        wdt = res["esp32s3_reset_with_watchdog_after_flash.patch"]
        assert midi.state == wdt.state == "applies"
        assert midi.overlap and midi.overlap_near == ()
        assert wdt.overlap == ("boards/espressif/esp32s3_devkitc/board.cmake",)
        assert wdt.overlap_near == ()
        fw = ForksAnalyzer._commit_finding(wdt, "zephyr", ("s",))
        assert fw.severity == Severity.INFO
        assert "upstream changed other parts of" in fw.title


def test_near_window_comes_from_the_patch_files_own_context(tmp_path: Path) -> None:
    """A patch made with 5 context lines reads lines 5-15 around its change at line 10;
    upstream changing line 14 is inside that window (risky), though outside git's default
    3. Under 3way the two changes merge cleanly, so only the window says it is risky."""
    from zmig.analyzers.forks import ForksAnalyzer
    from zmig.findings import Severity

    w = tmp_path
    f = w / "f.txt"
    lines = [f"line {i}" for i in range(1, 21)]
    _git(w, "init", "-q", "-b", "main")
    f.write_text("\n".join(lines) + "\n")
    _git(w, "add", "-A")
    _git(w, "commit", "-q", "-m", "base")
    base = _git(w, "rev-parse", "HEAD")
    _git(w, "checkout", "-q", "-b", "fork")
    f.write_text("\n".join([*lines[:9], "line 10 patched", *lines[10:]]) + "\n")
    _git(w, "commit", "-q", "-am", "wide.patch")
    head = _git(w, "rev-parse", "HEAD")
    patch = _git(w, "diff", "-U5", "HEAD^", "HEAD") + "\n"
    _git(w, "checkout", "-q", "main")
    f.write_text("\n".join([*lines[:13], "line 14 upstream", *lines[14:]]) + "\n")
    _git(w, "commit", "-q", "-am", "upstream")
    target = _git(w, "rev-parse", "HEAD")
    _git(w, "branch", "-D", "fork")
    _git(w, "update-ref", "refs/fork/head", head)
    mirror = SimpleNamespace(path=w / ".git", git=shutil.which("git"), runner=Runner())

    def run(patch_text: object) -> CommitResult:
        (c,) = classify(
            mirror, head, base, target, methods=("3way",), patch_text=patch_text
        ).commits  # type: ignore[arg-type]
        return c

    default, wide = run(None), run({"wide.patch": patch}.get)
    assert default.state == wide.state == "applies"
    assert default.overlap_near == () and wide.overlap_near == ("f.txt",)
    fw = ForksAnalyzer._commit_finding(wide, "zephyr", ("s",))
    assert fw.severity == Severity.RISKY
    assert "upstream changed lines inside its hunks" in fw.title
