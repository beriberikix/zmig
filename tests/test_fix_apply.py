"""`zmig fix kconfig --apply` without builds: the scan is replaced by fixed reports, so the
branch, the per-category commit, and the revealed / introduced split are tested on their
own (the real rebuild is covered by tests/test_fix_fixture.py, -m build)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from zmig import fixcmd
from zmig.findings import Category, Confidence, Finding, Report, Severity, file_line
from zmig.fixkconfig import Assignment, classify

PINMUX = {
    "type": "bool",
    "prompt": "Pinmux driver",
    "deprecated": False,
    "selects": [],
    "defaults": [],
    "depends": "y",
    "locations": ["drivers/pinmux/Kconfig:9"],
}
IDENT = {
    f"GIT_{r}_{k}": v
    for r in ("AUTHOR", "COMMITTER")
    for k, v in (("NAME", "t"), ("EMAIL", "t@example.com"))
}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        env={**os.environ, **IDENT},
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _finding(kind: str, subject: str, ev: tuple[Any, ...] = ()) -> Finding:
    return Finding(
        Category.KCONFIG,
        kind,
        subject,
        f"{kind} {subject}",
        Severity.BREAKING,
        Confidence.HIGH,
        "act",
        "kconfig",
        ("s",),
        ev,
    )


def _report(findings: list[Finding], target_status: str) -> Report:
    return Report(
        "scan", "0", {}, {}, [{"status": "ok"}, {"status": target_status}], findings=findings
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for k, v in IDENT.items():
        monkeypatch.setenv(k, v)
    root = tmp_path / "zmk"
    d = root / "app" / "boards" / "arm" / "planck"
    d.mkdir(parents=True)
    (root / "app" / "CMakeLists.txt").write_text("project(x)\n")
    (d / "planck_rev6_defconfig").write_text("# enable pinmux\nCONFIG_PINMUX=y\nCONFIG_PINCTRL=y\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "pre-bump")
    return root


def _run(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    after: list[Finding],
    before_status: str = "configure-failed",
) -> tuple[int, str]:
    app = project / "app"
    f = app / "boards/arm/planck/planck_rev6_defconfig"
    removed = _finding("symbol-removed", "CONFIG_PINMUX")
    reports = iter([_report([removed], before_status), _report(after, "ok")])
    ctx = SimpleNamespace(old_spec="v3.2", new_spec="v3.5")

    def scan_report(args: Any, cache: Any, runner: Any, keep: list[Any] | None = None) -> Report:
        if keep is not None:
            keep.append(ctx)
        return next(reports)

    a = Assignment(
        "app/boards/arm/planck/planck_rev6_defconfig",
        f,
        2,
        "CONFIG_PINMUX=y",
        "CONFIG_",
        "PINMUX",
        "y",
        "app",
    )
    prop = classify(a, {"PINMUX": PINMUX}, {}, None, [], lambda s, v: None, "v3.2", "v3.5")
    monkeypatch.setattr("zmig.cli.scan_report", scan_report)
    monkeypatch.setattr(fixcmd, "collect", lambda *x: fixcmd.Collected([(a, None)], []))
    monkeypatch.setattr(fixcmd, "propose", lambda *x: [prop])
    args = SimpleNamespace(
        what="kconfig", app=app, apply=True, branch=None, out=None, sysbuild=False
    )
    rc = fixcmd.cmd_fix(args, SimpleNamespace(), fixcmd.Runner())  # type: ignore[arg-type]
    return rc, f.read_text()


def test_apply_commits_the_removal_on_its_own_branch(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    revealed = _finding("value-not-taken", "CONFIG_GPIO", (file_line("prj.conf", 4),))
    rc, text = _run(project, monkeypatch, [revealed])
    out = capsys.readouterr().out
    assert rc == 0
    assert text == (
        "# enable pinmux\n"
        "# zmig: CONFIG_PINMUX removed in v3.5: no replacement evidenced\n"
        "# CONFIG_PINMUX=y\n"
        "CONFIG_PINCTRL=y\n"
    )
    assert _git(project, "branch", "--show-current").strip() == "zmig/fix-kconfig-v3.5"
    assert _git(project, "log", "--format=%s", "main..HEAD").splitlines() == [
        "zmig fix kconfig: remove (no replacement evidenced)"
    ]
    assert "app/boards/arm/planck/planck_rev6_defconfig:2: CONFIG_PINMUX commented out" in _git(
        project, "log", "-1", "--format=%b"
    )
    assert _git(project, "status", "--porcelain") == ""
    # The target configure had failed: a new finding not tied to the edit was hidden.
    assert (
        "gone: 1" in out and "revealed (hidden before" in out and "introduced by the fix: 0" in out
    )


def test_a_finding_at_the_edited_line_is_introduced_and_stops(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    at_edit = _finding(
        "read-symbol-removed",
        "CONFIG_X",
        (file_line("boards/arm/planck/planck_rev6_defconfig", 2),),
    )
    rc, _ = _run(project, monkeypatch, [at_edit])
    assert rc == fixcmd.EXIT_INTRODUCED
    assert "introduced by the fix: 1" in capsys.readouterr().out


def test_nothing_was_hidden_so_every_new_finding_is_introduced(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _finding("value-not-taken", "CONFIG_GPIO", (file_line("prj.conf", 4),))
    rc, _ = _run(project, monkeypatch, [other], before_status="ok")
    assert rc == fixcmd.EXIT_INTRODUCED


def test_dry_run_edits_nothing(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    f = project / "app/boards/arm/planck/planck_rev6_defconfig"
    before = f.read_text()
    a = Assignment(
        "app/boards/arm/planck/planck_rev6_defconfig",
        f,
        2,
        "CONFIG_PINMUX=y",
        "CONFIG_",
        "PINMUX",
        "y",
        "app",
    )
    prop = classify(a, {"PINMUX": PINMUX}, {}, None, [], lambda s, v: None, "v3.2", "v3.5")
    ctx = SimpleNamespace(old_spec="v3.2", new_spec="v3.5")
    monkeypatch.setattr(
        "zmig.cli.scan_report",
        lambda a, c, r, keep=None: (
            (keep.append(ctx) if keep is not None else None) or _report([], "ok")
        ),
    )
    monkeypatch.setattr(fixcmd, "collect", lambda *x: fixcmd.Collected([(a, None)], []))
    monkeypatch.setattr(fixcmd, "propose", lambda *x: [prop])
    args = SimpleNamespace(
        what="kconfig", app=project / "app", apply=False, branch=None, out=None, sysbuild=False
    )
    assert fixcmd.cmd_fix(args, SimpleNamespace(), fixcmd.Runner()) == 0  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "Fix: remove (no replacement evidenced): comment out with a note [1]" in out
    assert "+# zmig: CONFIG_PINMUX removed in v3.5: no replacement evidenced" in out
    assert f.read_text() == before and _git(project, "branch", "--show-current").strip() == "main"


def test_kconfig_overrides_of_removed_symbols_are_listed_never_edited() -> None:
    """ZMK's five orphaned overrides, from the target build's own Kconfig warnings (the
    scan's kconfig-override-orphaned findings; the real messages, home directory replaced).
    Every location the warning names is listed, mapped back to the app."""
    import json

    from zmig.findings import Report as _R

    msgs = json.loads((Path(__file__).parent / "fixtures/zmk-overrides/messages.json").read_text())
    copy = Path(
        "/home/user/.cache/zmig/workspaces/339570e2393b108bf8f9241018ecaf5615c25469/apps/c81d0237f8e705d7/app"
    )
    fs = [
        Finding(
            Category.BUILD,
            "kconfig-override-orphaned",
            sym,
            sym,
            Severity.BREAKING,
            Confidence.HIGH,
            "act",
            "buildlog",
            ("s",),
            (),
            None,
            {"message": m},
        )
        for sym, m in sorted(msgs.items())
    ]
    # The warning names copy c81d…, from a build reused from an earlier run; this run's
    # copy is another one (regression: no location was mapped back to the app).
    build = SimpleNamespace(
        workspace=SimpleNamespace(root=copy.parents[2]),
        src=copy.parent.parent / "6b9e9f511eba0965" / "app",
    )
    got = {
        o.symbol: o
        for o in fixcmd.overrides(_R("scan", "0", {}, {}, findings=fs), Path("/x/app"), [build])
    }
    assert sorted(got) == [
        "CONFIG_BT_LL_SW_LLCP_IMPL",
        "CONFIG_LV_Z_DPI",
        "CONFIG_LV_Z_MEM_POOL_MAX_SIZE",
        "CONFIG_LV_Z_MEM_POOL_MIN_SIZE",
        "CONFIG_SSD1306_REVERSE_MODE",
    ]
    assert got["CONFIG_LV_Z_DPI"].locations[0] == "boards/shields/corne/Kconfig.defconfig:34"
    assert len(got["CONFIG_SSD1306_REVERSE_MODE"].locations) == 22
    assert len(got["CONFIG_LV_Z_DPI"].locations) == 21
    assert got["CONFIG_BT_LL_SW_LLCP_IMPL"].locations == ("Kconfig:175",)
    assert got["CONFIG_LV_Z_MEM_POOL_MIN_SIZE"].locations == ("src/display/Kconfig:32",)
    assert all(not loc.startswith("/") for o in got.values() for loc in o.locations)
    text = fixcmd.format_plan([], fixcmd.Collected([], []), "h", list(got.values()))
    assert (
        "Needs a human: Kconfig-file overrides of symbols the target removed [5 symbol(s)" in text
    )
    assert "Nothing to fix" not in text
