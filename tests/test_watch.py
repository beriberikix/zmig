"""zmig watch: fingerprints, the baseline comparison and the exit policy, on real reports
(CANnectivity C1, DOGFOOD-4; Golioth B, DOGFOOD-2), plus synthetic edits of them."""

from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from zmig.cli import main
from zmig.watch import (
    COMMENT_MARKER,
    EXIT_INCONCLUSIVE,
    EXIT_NEW_FINDING,
    EXIT_OK,
    SARIF_FINGERPRINT_KEY,
    Acceptance,
    Baseline,
    FingerprintContext,
    Policy,
    compare,
    decide,
    fingerprint,
    inconclusive_reasons,
    make_baseline,
    render_comment,
    render_watch_sarif,
)

FIX = Path(__file__).parent / "fixtures" / "watch"
TODAY = dt.date(2026, 10, 28)
ACC = Acceptance("test", "tester", "2026-10-28T00:00:00Z", None)
JSON = dict[str, Any]


def load(name: str) -> JSON:
    data: JSON = json.loads((FIX / f"{name}.json").read_text())
    return data


def baseline_of(report: JSON, until: str | None = None) -> Baseline:
    acc = Acceptance(ACC.reason, ACC.by, ACC.at, ACC.commit, until)
    to = {"spec": "x", "resolved": "x", "sha": report["inputs"]["to"]["sha"]}
    return Baseline(Path("b.json"), make_baseline(report, {}, to, acc, "test"))


def fps(report: JSON) -> list[str]:
    ctx = FingerprintContext.of(report)
    return sorted(
        fingerprint(f, ctx) for f in report["findings"] if f["severity"] in ("breaking", "risky")
    )


def retarget(report: JSON, sha: str, spec: str | None = None) -> JSON:
    """The same report as if the target were another commit: every mention of the old
    target SHA (subjects, titles, evidence) replaced."""
    old = report["inputs"]["to"]["sha"]
    text = json.dumps(report).replace(old, sha).replace(old[:12], sha[:12])
    r: JSON = json.loads(text)
    if spec:
        r["inputs"]["to"]["spec"] = spec
    return r


def shift_lines(report: JSON, path: str, by: int) -> JSON:
    r = copy.deepcopy(report)
    for f in r["findings"]:
        for e in f["evidence"]:
            if e.get("type") == "file_line" and e["path"] == path:
                e["line"] += by
    return r


def rename(report: JSON, old: str, new: str) -> JSON:
    r: JSON = json.loads(json.dumps(report).replace(old, new))
    return r


@pytest.mark.parametrize("name", ["cannectivity-c1", "golioth-b"])
def test_fingerprints_ignore_the_target_ref_and_line_shifts(name: str) -> None:
    r = load(name)
    moved = retarget(r, "1" * 40, "v9.9.9")
    assert fps(moved) == fps(r)
    for f in {e["path"] for x in r["findings"] for e in x["evidence"] if e["type"] == "file_line"}:
        r = shift_lines(r, f, 7)
    assert fps(r) == fps(load(name))


def test_line_shifts_inside_values_are_not_value_changes() -> None:
    """The c-shift stability run: errors' `line` and compiler `sites` moved by 5."""
    r = load("cannectivity-c1")
    b = baseline_of(r)
    shifted = json.loads(
        json.dumps(r)
        .replace('"line": 225', '"line": 230')
        .replace("src/led.c:349", "src/led.c:354")
        .replace("src/led.c:225", "src/led.c:230")
    )
    assert {i.state for i in compare(shifted, b, TODAY).items} == {"unchanged"}
    changed = copy.deepcopy(r)
    f = next(x for x in changed["findings"] if x["kind"] == "target-build-failed")
    f["new"]["errors"][0]["message"] = "undefined reference to `something_else'"
    assert [i.state for i in compare(changed, b, TODAY).items].count("updated") == 1


def test_target_build_failed_subject_is_the_target_sha() -> None:
    """Regression guard: this subject *is* the target SHA, so without normalization every
    target movement would make it new."""
    r = load("cannectivity-c1")
    f = next(x for x in r["findings"] if x["kind"] == "target-build-failed")
    assert f["subject"] == r["inputs"]["to"]["sha"]


def test_renamed_file_is_new_and_fixed_not_matched() -> None:
    r = load("cannectivity-c1")
    b = baseline_of(r)
    cmp = compare(rename(r, "src/led.c", "src/led_ctrl.c"), b, TODAY)
    new = {(i.path, i.severity) for i in cmp.of("new")}
    fixed = {(i.path, i.severity) for i in cmp.of("fixed")}
    assert new and {p for p, _ in new} == {"src/led_ctrl.c"}
    assert {p for p, _ in fixed} == {"src/led.c"}
    assert len(cmp.of("new")) == len(cmp.of("fixed")) == 6
    assert {i.path for i in cmp.of("unchanged")} == {"", "src/usb.c"}


def test_same_report_is_unchanged_and_conclusiveness_decides_the_exit() -> None:
    r = load("cannectivity-c1")
    b = baseline_of(r)
    cmp = compare(r, b, TODAY)
    assert {i.state for i in cmp.items} == {"unchanged"}
    reasons = inconclusive_reasons(r, b.data)
    assert any("target build is build-failed" in x for x in reasons)
    assert all(r["inputs"]["to"]["sha"] not in x for x in reasons)
    assert decide(cmp, reasons, Policy()).code == EXIT_INCONCLUSIVE
    assert decide(cmp, reasons, Policy(allow_inconclusive=True)).code == EXIT_OK

    g = load("golioth-b")
    gb = baseline_of(g)
    out = decide(compare(g, gb, TODAY), inconclusive_reasons(g, gb.data), Policy())
    assert (out.code, out.inconclusive) == (EXIT_OK, [])


def test_new_findings_fail_by_severity_policy() -> None:
    """The baseline from the fixed CANnectivity report, against the report before the
    M13-M15 fixes: three phandle findings are new risky; the build failure lost its
    location, so it is new breaking (and the located one is fixed)."""
    b = baseline_of(load("cannectivity-c1"))
    pre = load("cannectivity-c1-prefix")
    cmp = compare(pre, b, TODAY)
    new = cmp.of("new")
    assert sorted(i.severity for i in new) == ["breaking", "risky", "risky", "risky"]
    assert [i.title for i in cmp.of("fixed")] == ["The target build fails: 2 errors (linker)"]
    out = decide(cmp, [], Policy())
    assert out.code == EXIT_NEW_FINDING and [i.severity for i in out.failing] == ["breaking"]
    out = decide(cmp, [], Policy(fail_on=("breaking", "risky")))
    assert len(out.failing) == 4


def test_multiset_counts_one_extra_copy_as_new() -> None:
    r = load("golioth-b")
    b = baseline_of(r)
    dup = copy.deepcopy(r)
    f = next(x for x in dup["findings"] if x["severity"] == "risky")
    dup["findings"].append(copy.deepcopy(f))
    cmp = compare(dup, b, TODAY)
    assert len(cmp.of("new")) == 1 and cmp.of("new")[0].severity == "risky"
    path = cmp.of("new")[0].path
    if path:
        fp = cmp.of("new")[0].fingerprint
        assert cmp.file_counts[path][fp] >= 2
        assert "in this file" in render_comment(dup, b, cmp, decide(cmp, [], Policy()), "b")


def test_expired_acceptance_makes_the_finding_new_again() -> None:
    r = load("golioth-b")
    b = baseline_of(r, until="2026-10-01")
    cmp = compare(r, b, TODAY)
    assert cmp.of("new") and all(i.expired for i in cmp.of("new"))
    assert not cmp.of("fixed")
    assert decide(cmp, [], Policy()).code == EXIT_NEW_FINDING
    assert not compare(r, b, dt.date(2026, 9, 30)).of("new")


def test_escalation_and_value_changes() -> None:
    r = load("golioth-b")
    b = baseline_of(r)
    up = copy.deepcopy(r)
    risky = next(x for x in up["findings"] if x["severity"] == "risky")
    risky["severity"] = "breaking"
    cmp = compare(up, b, TODAY)
    assert [i.state for i in cmp.items if i.gating] == ["escalated"]
    assert decide(cmp, [], Policy()).code == EXIT_NEW_FINDING

    val = copy.deepcopy(r)
    f = next(x for x in val["findings"] if x["severity"] in ("breaking", "risky"))
    f["new"] = {"changed": "value"}
    cmp = compare(val, b, TODAY)
    assert [i.state for i in cmp.items if i.state != "unchanged"] == ["updated"]
    assert decide(cmp, [], Policy()).code == EXIT_OK


def test_target_movement_is_attributed_with_a_pinned_report() -> None:
    """Baseline at the old target. This run's target moved, and the project gained a
    finding; another finding appears only at the new target. The pinned report (this
    project at the baseline's target) separates them."""
    base = load("cannectivity-c1")
    b = baseline_of(base)
    pinned = load("cannectivity-c1-prefix")  # stands in for "the project changed"
    moved = retarget(pinned, "2" * 40)
    extra = copy.deepcopy(next(x for x in moved["findings"] if x["kind"] == "module-required"))
    extra["subject"] = "hal_new"
    moved["findings"].append(extra)
    cmp = compare(moved, b, TODAY, pinned=pinned)
    causes = {(i.title[:40], i.cause) for i in cmp.of("new")}
    assert cmp.moved and cmp.attributed
    assert {c for t, c in causes if "hal_new" not in t and "cmsis_6" not in t} == {"change"}
    assert [i.cause for i in cmp.of("new") if (i.finding or {})["subject"] == "hal_new"] == [
        "target-movement"
    ]
    assert decide(cmp, [], Policy()).code == EXIT_NEW_FINDING
    warn = decide(cmp, [], Policy(on_target_movement="warn"))
    assert all(i.cause == "change" for i in warn.failing)
    assert [(i.finding or {})["subject"] for i in warn.warned] == ["hal_new"]
    assert {i.cause for i in compare(moved, b, TODAY).of("new")} == {"unattributed"}


def test_sarif_and_comment() -> None:
    b = baseline_of(load("cannectivity-c1"))
    pre = load("cannectivity-c1-prefix")
    cmp = compare(pre, b, TODAY)
    out = decide(cmp, inconclusive_reasons(pre, b.data), Policy())
    sarif = json.loads(render_watch_sarif(pre, cmp, "0", "app"))
    results = sarif["runs"][0]["results"]
    assert {r["baselineState"] for r in results} == {"new", "unchanged", "absent"}
    assert all(SARIF_FINGERPRINT_KEY in r["partialFingerprints"] for r in results)
    absent = [r for r in results if r["baselineState"] == "absent"]
    assert absent[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/led.c"
    assert "region" not in absent[0]["locations"][0]["physicalLocation"]
    text = render_comment(pre, b, cmp, out, "usb2canfdv1")
    assert text.startswith(COMMENT_MARKER)
    assert "breaking/risky in this file" in text and "(also in the baseline)" in text


def _cli(tmp: Path, report: str, *extra: str) -> int:
    return main(
        [
            "watch",
            "--cache-dir",
            str(tmp / "cache"),
            "--app",
            str(tmp),
            "--board",
            "usb2canfdv1",
            "--baseline",
            str(tmp / ".zmig" / "baseline.json"),
            "--report",
            str(FIX / f"{report}.json"),
            "--today",
            "2026-10-28",
            *extra,
        ]
    )


def test_cli_baseline_lifecycle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _cli(tmp_path, "cannectivity-c1") == 2
    assert "no baseline at" in capsys.readouterr().err
    assert _cli(tmp_path, "cannectivity-c1", "--init") == 2
    assert "need --reason" in capsys.readouterr().err
    assert _cli(tmp_path, "cannectivity-c1", "--init", "--reason", "v4.2 port pending") == 0
    data = json.loads((tmp_path / ".zmig" / "baseline.json").read_text())
    assert {e["reason"] for e in data["accepted"]} == {"v4.2 port pending"}
    assert all(e["accepted_by"] and e["accepted_at"] for e in data["accepted"])
    assert data["config"]["app"] == "."
    capsys.readouterr()
    assert _cli(tmp_path, "cannectivity-c1", "--init", "--reason", "again") == 2
    assert _cli(tmp_path, "cannectivity-c1") == EXIT_INCONCLUSIVE
    assert _cli(tmp_path, "cannectivity-c1", "--allow-inconclusive") == EXIT_OK
    assert _cli(tmp_path, "cannectivity-c1-prefix", "--allow-inconclusive") == EXIT_NEW_FINDING
    # Updating keeps who/why of entries that remain; new ones get the new reason.
    assert (
        _cli(tmp_path, "cannectivity-c1-prefix", "--update-baseline", "--reason", "phandles") == 0
    )
    data = json.loads((tmp_path / ".zmig" / "baseline.json").read_text())
    reasons = sorted(e["reason"] for e in data["accepted"])
    assert reasons.count("phandles") == 4 and reasons.count("v4.2 port pending") == 7
    capsys.readouterr()
    # A different board is a config mismatch: exit 2, not a comparison.
    rc = main(
        [
            "watch",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--app",
            str(tmp_path),
            "--board",
            "candlelight",
            "--baseline",
            str(tmp_path / ".zmig" / "baseline.json"),
            "--report",
            str(FIX / "cannectivity-c1.json"),
        ]
    )
    assert rc == 2 and "board: baseline 'usb2canfdv1'" in capsys.readouterr().err
