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
    EXIT_USAGE,
    SARIF_FINGERPRINT_KEY,
    Acceptance,
    Baseline,
    FingerprintContext,
    Policy,
    classify_gaps,
    compare,
    decide,
    fingerprint,
    gaps,
    inconclusive_reasons,
    make_baseline,
    rebase_baseline,
    render_comment,
    render_watch_sarif,
    zmig_build,
)

FIX = Path(__file__).parent / "fixtures" / "watch"
TODAY = dt.date(2026, 10, 28)
ACC = Acceptance("test", "tester", "2026-10-28T00:00:00Z", None)
JSON = dict[str, Any]


def load(name: str) -> JSON:
    data: JSON = json.loads((FIX / f"{name}.json").read_text())
    return data


def baseline_of(report: JSON, until: str | None = None, accept_gaps: bool = True) -> Baseline:
    acc = Acceptance(ACC.reason, ACC.by, ACC.at, ACC.commit, until)
    to = {"spec": "x", "resolved": "x", "sha": report["inputs"]["to"]["sha"]}
    data = make_baseline(report, {}, to, acc, "test")
    if not accept_gaps:
        data["accepted_gaps"] = []
    return Baseline(Path("b.json"), data)


def gap_states(report: JSON, b: Baseline, today: dt.date = TODAY) -> list[Any]:
    return classify_gaps(gaps(report, b.data), b, today)


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
    b = baseline_of(r, accept_gaps=False)
    cmp = compare(r, b, TODAY)
    assert {i.state for i in cmp.items} == {"unchanged"}
    reasons = inconclusive_reasons(r, b.data)
    assert any("target build is build-failed" in x for x in reasons)
    assert all(r["inputs"]["to"]["sha"] not in x for x in reasons)
    assert {g.id for g in gaps(r)} == {"target-build", "analyzer:initorder", "analyzer:sbom"}
    states = gap_states(r, b)
    assert {g.state for g in states} == {"unaccepted"}
    assert decide(cmp, states, Policy()).code == EXIT_INCONCLUSIVE
    assert decide(cmp, states, Policy(allow_inconclusive=True)).code == EXIT_OK

    g = load("golioth-b")
    gb = baseline_of(g)
    out = decide(compare(g, gb, TODAY), gap_states(g, gb), Policy())
    assert (out.code, out.gaps) == (EXIT_OK, [])


def test_accepted_gaps_pass_with_a_warning_and_expire() -> None:
    r = load("cannectivity-c1")
    b = baseline_of(r)
    for e in b.data["accepted_gaps"]:
        e["until"] = "2026-11-30"
    assert [e["id"] for e in b.data["accepted_gaps"]] == [
        "analyzer:initorder",
        "analyzer:sbom",
        "target-build",
    ]
    assert all(
        e["accepted_by"] and e["accepted_at"] and e["reason"] for e in b.data["accepted_gaps"]
    )
    cmp = compare(r, b, TODAY)
    states = gap_states(r, b)
    out = decide(cmp, states, Policy())
    assert out.code == EXIT_OK and not out.blocking_gaps and len(out.gaps) == 3
    text = render_comment(r, b, cmp, out, "usb2canfdv1", dt.date(2026, 11, 7))
    assert (
        text.count(
            "analysis incomplete: new breaking findings cannot be seen while this gap exists"
        )
        == 3
    )
    assert "age 10 days" in text
    # Past `until`: back to exit 4.
    late = dt.date(2026, 12, 1)
    states = gap_states(r, b, late)
    assert {g.state for g in states} == {"expired"}
    assert decide(compare(r, b, late), states, Policy()).code == EXIT_INCONCLUSIVE


def test_new_or_worse_gaps_are_not_covered_by_an_acceptance() -> None:
    g = load("golioth-b")
    b = baseline_of(g)
    worse = copy.deepcopy(g)
    worse["analyzers"] = [
        {**a, "status": "skipped", "reason": "no ELF"} if a["name"] == "api" else a
        for a in worse["analyzers"]
    ]
    states = gap_states(worse, b)
    assert [(s.gap.id, s.state) for s in states] == [("analyzer:api", "unaccepted")]
    # Accept api *partial*; skipped is worse than that.
    partial = copy.deepcopy(g)
    partial["analyzers"] = [
        {**a, "status": "partial", "reason": "1 TU"} if a["name"] == "api" else a
        for a in partial["analyzers"]
    ]
    bp = baseline_of(partial)
    assert [(s.gap.id, s.state) for s in gap_states(partial, bp)] == [("analyzer:api", "accepted")]
    states = gap_states(worse, bp)
    assert [(s.gap.id, s.state) for s in states] == [("analyzer:api", "worse")]
    out = decide(compare(worse, bp, TODAY), states, Policy())
    assert out.code == EXIT_INCONCLUSIVE
    text = render_comment(worse, bp, compare(worse, bp, TODAY), out, "b", TODAY)
    assert "**worse than accepted** (partial accepted, now skipped)" in text
    # A gap that improves stays accepted (partial accepted at skipped's level is fine).
    assert [s.state for s in gap_states(partial, baseline_of(worse))] == ["accepted"]


def test_rebase_maps_by_category_kind_subject() -> None:
    """The two CANnectivity reports are real output of two zmig builds (before and after
    the M13-M15 fixes): a baseline from the older build, rebased onto the newer one."""
    old = baseline_of(load("cannectivity-c1-prefix"))
    new = load("cannectivity-c1")
    rb = rebase_baseline(old, new, ACC, "0.1.0", "newbuild")
    assert len(rb.remapped) == 8
    assert sorted(e["subject"] for e, _ in rb.unmapped) == [
        "&timers2/counter2:/soc/timers@40000000/counter2",
        "alias led0:/leds/led_ready",
        "alias mcuboot_led0:/leds/led_ready",
    ]
    assert all("no finding" in why for _, why in rb.unmapped)
    assert rb.not_accepted == []
    moved = [(o, n) for o, n in rb.remapped if o["fingerprint"] != n["fingerprint"]]
    assert [n["kind"] for _, n in moved] == ["target-build-failed"]  # gained a file (M14)
    assert all(n["reason"] == "test" and n["rebased_from"] for _, n in rb.remapped)
    assert rb.data["zmig_build"] == "newbuild" and rb.data["rebases"][-1]["dropped"] == 3
    after = Baseline(Path("b.json"), rb.data)
    assert {i.state for i in compare(new, after, TODAY).items} == {"unchanged"}


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
        assert "in this file" in render_comment(dup, b, cmp, decide(cmp, [], Policy()), "b", TODAY)


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
    out = decide(cmp, gap_states(pre, b), Policy())
    sarif = json.loads(render_watch_sarif(pre, cmp, "0", "app"))
    results = sarif["runs"][0]["results"]
    assert {r["baselineState"] for r in results} == {"new", "unchanged", "absent"}
    assert all(SARIF_FINGERPRINT_KEY in r["partialFingerprints"] for r in results)
    absent = [r for r in results if r["baselineState"] == "absent"]
    assert absent[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/led.c"
    assert "region" not in absent[0]["locations"][0]["physicalLocation"]
    text = render_comment(pre, b, cmp, out, "usb2canfdv1", TODAY, ("pins a branch",))
    assert text.startswith(COMMENT_MARKER)
    assert "breaking/risky in this file" in text and "analysis incomplete" in text
    assert "> **Warning:** pins a branch" in text


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
    bfile = tmp_path / ".zmig" / "baseline.json"
    data = json.loads(bfile.read_text())
    assert {e["reason"] for e in data["accepted"]} == {"v4.2 port pending"}
    assert all(e["accepted_by"] and e["accepted_at"] for e in data["accepted"])
    assert len(data["accepted_gaps"]) == 3 and data["zmig_build"] == zmig_build()
    assert data["config"]["app"] == "."
    capsys.readouterr()
    assert _cli(tmp_path, "cannectivity-c1", "--init", "--reason", "again") == 2
    # Gaps accepted at --init: exit 0, with the "analysis incomplete" lines.
    assert _cli(tmp_path, "cannectivity-c1") == EXIT_OK
    assert "analysis incomplete" in capsys.readouterr().out
    assert _cli(tmp_path, "cannectivity-c1", "--today", "2026-10-29") == EXIT_OK
    assert _cli(tmp_path, "cannectivity-c1-prefix") == EXIT_NEW_FINDING
    # Updating keeps who/why of entries that remain; new ones get the new reason.
    assert (
        _cli(tmp_path, "cannectivity-c1-prefix", "--update-baseline", "--reason", "phandles") == 0
    )
    data = json.loads(bfile.read_text())
    reasons = sorted(e["reason"] for e in data["accepted"])
    assert reasons.count("phandles") == 4 and reasons.count("v4.2 port pending") == 7
    assert {e["reason"] for e in data["accepted_gaps"]} == {"v4.2 port pending"}
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
            str(bfile),
            "--report",
            str(FIX / "cannectivity-c1.json"),
        ]
    )
    assert rc == 2 and "board: baseline 'usb2canfdv1'" in capsys.readouterr().err


def test_cli_version_skew_and_rebase(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A baseline from another zmig build: exit 2, not a flood of new findings; then
    --rebase-baseline re-keys it (needs --reason) and the comparison is clean."""
    assert _cli(tmp_path, "cannectivity-c1-prefix", "--init", "--reason", "old zmig") == 0
    bfile = tmp_path / ".zmig" / "baseline.json"
    data = json.loads(bfile.read_text())
    data["zmig_build"] = "0123456789abcdef"  # made by another build
    bfile.write_text(json.dumps(data))
    capsys.readouterr()
    assert _cli(tmp_path, "cannectivity-c1") == EXIT_USAGE
    err = capsys.readouterr().err
    assert "build 0123456789abcdef" in err and "--rebase-baseline" in err
    assert _cli(tmp_path, "cannectivity-c1", "--update-baseline", "--reason", "x") == EXIT_USAGE
    assert _cli(tmp_path, "cannectivity-c1", "--rebase-baseline") == EXIT_USAGE
    capsys.readouterr()
    assert _cli(tmp_path, "cannectivity-c1", "--rebase-baseline", "--reason", "zmig 2aca9a9") == 0
    out = capsys.readouterr().out
    assert "Re-mapped (8):" in out and "Could not re-map, dropped from the baseline (3):" in out
    assert "(fingerprint changed)" in out
    data = json.loads(bfile.read_text())
    assert data["zmig_build"] == zmig_build() and data["rebases"][-1]["reason"] == "zmig 2aca9a9"
    assert _cli(tmp_path, "cannectivity-c1") == EXIT_OK
    # Nothing to rebase once it matches.
    assert _cli(tmp_path, "cannectivity-c1", "--rebase-baseline", "--reason", "y") == EXIT_USAGE


def test_from_manifest_branch_is_warned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CANnectivity v1.1.0's west.yml pins `v4.1-branch`: resolving it gives today's tip,
    which may not be what the project was tested with."""
    import argparse

    from zmig import watchcmd
    from zmig.refs import Ref

    (tmp_path / "app").mkdir()
    (tmp_path / "west.yml").write_text(
        "manifest:\n  projects:\n    - name: zephyr\n"
        "      url: https://github.com/zephyrproject-rtos/zephyr\n"
        "      revision: v4.1-branch\n      import: true\n"
    )

    class Resolved:
        def __init__(self, spec: str, kind: str) -> None:
            self.ref = Ref(spec=spec, sha="a" * 40, kind=kind, url="u")  # type: ignore[arg-type]

    kind = "branch"
    monkeypatch.setattr(watchcmd, "resolve_ref", lambda spec, *a, **k: Resolved(spec, kind))
    args = argparse.Namespace(from_ref="manifest", manifest=None, app=tmp_path / "app")
    rev, _, sha, rec = watchcmd._resolve_from(args, None, None)  # type: ignore[arg-type]
    assert (rev, sha, rec and rec["kind"]) == ("v4.1-branch", "a" * 40, "branch")
    assert rec and "may differ from the Zephyr commit" in rec["warning"]
    kind = "tag"
    _, _, _, rec = watchcmd._resolve_from(args, None, None)  # type: ignore[arg-type]
    assert rec and "warning" not in rec


def test_unseen_is_not_fixed_when_a_gap_blocks() -> None:
    """The first CI nightly of the CANnectivity test repo (run 40764423331): at v4.4.2
    the target configure failed, and the 7 accepted findings the run couldn't look for
    were reported as fixed (and as SARIF `absent`, which would close their alerts)."""
    report = load("ci-nightly-v4.4.2")
    pinned = load("ci-nightly-pinned")
    b = Baseline(Path("b.json"), load("ci-baseline"))
    states = gap_states(report, b)
    assert {s.gap.id: s.state for s in states}["target-build"] == "worse"
    cmp = compare(report, b, TODAY, pinned, blind=any(s.blocking for s in states))
    assert len(cmp.of("unseen")) == 7 and not cmp.of("fixed")
    assert {i.cause for i in cmp.of("new")} == {"target-movement"}
    out = decide(cmp, states, Policy())
    assert out.code == EXIT_NEW_FINDING
    sarif = json.loads(render_watch_sarif(report, cmp, "0", "app"))
    assert "absent" not in {r["baselineState"] for r in sarif["runs"][0]["results"]}
    text = render_comment(report, b, cmp, out, "usb2canfdv1", TODAY)
    assert "Accepted, not seen in this run (analysis incomplete: not counted as fixed) (7)" in text
    assert "#### Fixed" not in text
    # Without a blocking gap, the same absences are "fixed".
    assert len(compare(report, b, TODAY, pinned).of("fixed")) == 7


def test_update_baseline_keeps_rebase_provenance() -> None:
    """CI test repo PR #2: --update-baseline after a rebase dropped `rebased_from`."""
    old = baseline_of(load("cannectivity-c1-prefix"))
    rb = rebase_baseline(old, load("cannectivity-c1"), ACC, "0.1.0", "newbuild")
    rebased = Baseline(Path("b.json"), rb.data)
    to = {"spec": "x", "resolved": "x", "sha": load("cannectivity-c1")["inputs"]["to"]["sha"]}
    acc = Acceptance("later", "other", "2026-11-01T00:00:00Z", None)
    data = make_baseline(load("cannectivity-c1"), {}, to, acc, "0.1.0", rebased, "newbuild")
    assert all(e.get("rebased_from") for e in data["accepted"])
    assert {e["reason"] for e in data["accepted"]} == {"test"}
