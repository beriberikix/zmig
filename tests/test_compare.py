"""`zmig compare`: two scans of the same app, before and after an edit.

The reports are real scans (tests/fixtures/watch), edited the way an assistant's edit
would change them. Nothing builds."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from zmig.cli import main
from zmig.compare import Result, classify, compare_json, mismatches
from zmig.report.human import effective_contexts, target_build

FIX = Path(__file__).parent / "fixtures" / "watch"
JSON = dict[str, Any]


def load(name: str) -> JSON:
    data: JSON = json.loads((FIX / f"{name}.json").read_text())
    return data


def run(before: JSON, after: JSON, fail_on: str = "breaking", allow: bool = False) -> Result:
    return classify(before, after, Path("before.json"), fail_on, allow)


def states(res: Result) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for i in res.cmp.items:
        kind = i.finding["kind"] if i.finding else (i.entry or {}).get("kind", "")
        out.setdefault(i.state, []).append(kind)
    return {k: sorted(v) for k, v in out.items()}


def finding(r: JSON, kind: str) -> JSON:
    f: JSON = next(x for x in r["findings"] if x["kind"] == kind)
    return f


def drop(r: JSON, kind: str) -> JSON:
    r = copy.deepcopy(r)
    r["findings"] = [f for f in r["findings"] if f["kind"] != kind]
    return r


def skip(r: JSON, analyzer: str) -> JSON:
    r = copy.deepcopy(r)
    for a in r["analyzers"]:
        if a["name"] == analyzer:
            a["status"], a["reason"] = "skipped", "source (v1): no build"
    return r


def add_new(r: JSON, kind: str, subject: str, severity: str = "breaking") -> JSON:
    """A copy of a finding of `kind`, about something else: a finding BEFORE didn't have."""
    r = copy.deepcopy(r)
    f = copy.deepcopy(finding(r, kind))
    f["subject"], f["severity"] = subject, severity
    f["id"] = f["id"][:-8] + "deadbeef"
    f["title"] = f"{subject}: {f['title']}"
    r["findings"].append(f)
    return r


def sites_of(r: JSON, kind: str, files: list[tuple[str, int]]) -> JSON:
    r = copy.deepcopy(r)
    f = finding(r, kind)
    f["evidence"] = [e for e in f["evidence"] if e.get("type") != "file_line"] + [
        {"type": "file_line", "path": p, "line": n, "text": f["subject"]} for p, n in files
    ]
    return r


# --- States and exit codes --------------------------------------------------------------


def test_same_report_is_unchanged_and_exits_0() -> None:
    r = load("cannectivity-c1")
    res = run(r, copy.deepcopy(r))
    assert set(states(res)) == {"unchanged"}
    assert res.outcome.code == 0


def test_fixed_when_no_new_gap() -> None:
    before = load("cannectivity-c1")
    res = run(before, drop(before, "node-changed"))
    assert states(res)["fixed"] == ["node-changed"]
    assert res.outcome.code == 0


def test_unseen_when_analyzer_skipped_in_after() -> None:
    before = load("golioth-b")
    res = run(before, skip(drop(before, "typedef-changed"), "api"))
    assert states(res)["unseen"] == ["typedef-changed"]
    assert "fixed" not in states(res)
    assert res.outcome.code == 4
    assert res.unseen_source_build_broke == 0  # the source build is still ok


def test_unseen_counts_source_build_broke() -> None:
    before = load("golioth-b")
    after = skip(drop(before, "typedef-changed"), "api")
    after["contexts"][0]["status"] = "build-failed"
    res = run(before, after)
    assert res.unseen_source_build_broke == 1
    assert res.outcome.code == 4


def test_new_exits_3() -> None:
    before = load("golioth-b")
    res = run(before, add_new(before, "typedef-changed", "other_handler_t"))
    assert states(res)["new"] == ["typedef-changed"]
    assert res.outcome.code == 3


def test_new_below_fail_on_exits_0() -> None:
    before = load("golioth-b")
    after = add_new(before, "field-changed", "other.field", severity="risky")
    assert run(before, after).outcome.code == 0
    assert run(before, after, fail_on="risky").outcome.code == 3


def test_revealed_when_target_build_starts_working() -> None:
    before = load("cannectivity-c1")  # the target build failed
    after = drop(before, "target-build-failed")
    after["contexts"][-1]["status"] = "ok"
    after = add_new(after, "node-changed", "&other_node")  # a build-only analyzer's finding
    res = run(before, after)
    assert states(res)["revealed"] == ["node-changed"]
    assert states(res)["fixed"] == ["target-build-failed"]
    assert res.outcome.code == 4
    assert "revealed" in res.outcome.summary


def test_revealed_allowed_exits_0() -> None:
    before = load("cannectivity-c1")
    after = drop(before, "target-build-failed")
    after["contexts"][-1]["status"] = "ok"
    after = add_new(after, "node-changed", "&other_node")
    res = run(before, after, allow=True)
    assert res.outcome.code == 0 and "WARNING" in res.outcome.summary


def test_revealed_when_analyzer_gap_closes() -> None:
    after = load("golioth-b")
    before = skip(after, "sbom")
    before["findings"] = [f for f in before["findings"] if f["analyzer"] != "sbom"]
    res = run(before, after)
    assert states(res)["revealed"] == sorted(
        f["kind"] for f in after["findings"] if f["analyzer"] == "sbom" and f["severity"] != "info"
    )
    assert res.outcome.code == 4


def test_new_takes_precedence_over_revealed() -> None:
    after = add_new(load("golioth-b"), "typedef-changed", "other_handler_t")
    before = skip(load("golioth-b"), "sbom")
    before["findings"] = [f for f in before["findings"] if f["analyzer"] != "sbom"]
    res = run(before, after)
    assert "revealed" in states(res) and states(res)["new"] == ["typedef-changed"]
    assert res.outcome.code == 3


def test_new_use_site_exits_3() -> None:
    before = load("cannectivity-c1")
    f = finding(before, "function-removed")
    files = [(e["path"], e["line"]) for e in f["evidence"] if e.get("type") == "file_line"]
    res = run(before, sites_of(before, "function-removed", [*files, ("src/main.c", 46)]))
    assert states(res)["new-use-site"] == ["function-removed"]
    assert res.outcome.code == 3


def test_inconclusive_exits_4_and_allowed_exits_0() -> None:
    before = load("golioth-b")
    after = skip(before, "api")
    assert run(before, after).outcome.code == 4
    assert run(before, after, allow=True).outcome.code == 0


def test_verdict_confidence_drop_exits_4() -> None:
    before = load("golioth-b")
    after = copy.deepcopy(before)
    after["upgrade_path"]["confidence"] = "low"
    res = run(before, after)
    assert res.outcome.code == 4
    assert any(g.gap.id == "verdict-confidence" for g in res.outcome.gaps)


# --- Pairing: one finding under a moved fingerprint -------------------------------------


def test_pairs_finding_whose_first_file_was_cleared() -> None:
    """Every use site in the first-cited file rewritten: watch's fingerprint moves (it
    includes that file), which alone would read as one fixed and one new (exit 3)."""
    r = load("cannectivity-c1")
    f = finding(r, "function-removed")
    led = [(e["path"], e["line"]) for e in f["evidence"] if e.get("type") == "file_line"]
    before = sites_of(r, "function-removed", [*led, ("src/main.c", 46)])
    after = sites_of(r, "function-removed", [("src/main.c", 46)])
    res = run(before, after)
    item = next(i for i in res.cmp.items if i.finding and i.finding["kind"] == "function-removed")
    assert item.state == "updated"
    assert res.site_counts[id(item)] == (len(led) + 1, 1)
    assert "new" not in states(res) and "fixed" not in states(res)
    assert res.outcome.code == 0


def test_fewer_sites_same_digest_is_updated() -> None:
    r = load("cannectivity-c1")
    assert finding(r, "function-removed")["new"] is None  # value digest can't change
    after = sites_of(r, "function-removed", [("src/led.c", 225), ("src/led.c", 263)])
    res = run(r, after)
    item = next(i for i in res.cmp.items if i.finding and i.finding["kind"] == "function-removed")
    assert item.state == "updated"
    assert res.site_counts[id(item)] == (4, 2)
    assert res.outcome.code == 0


def test_more_build_errors_same_file_is_new_use_site() -> None:
    before = load("cannectivity-c1")
    after = copy.deepcopy(before)
    errors = finding(after, "target-build-failed")["new"]["errors"]
    errors.append({**errors[0], "line": 300, "message": "undefined reference to `k_poll'"})
    res = run(before, after)
    assert states(res)["new-use-site"] == ["target-build-failed"]
    assert res.outcome.code == 3


def test_updated_when_fewer_build_errors() -> None:
    before = load("cannectivity-c1")
    after = copy.deepcopy(before)
    f = finding(after, "target-build-failed")
    f["new"]["errors"] = f["new"]["errors"][:1]
    f["evidence"] = [e for e in f["evidence"] if e.get("line") != 275]
    res = run(before, after)
    assert states(res)["updated"] == ["target-build-failed"]
    assert res.outcome.code == 0


# --- Output ------------------------------------------------------------------------------


def test_fixed_and_unseen_items_carry_ids_and_no_cause() -> None:
    before = load("cannectivity-c1")
    res = run(before, drop(before, "node-changed"))
    doc = compare_json(res, before, before, Path("a/report.json"), Path("/abs/b/report.json"))
    fixed = [i for i in doc["items"] if i["state"] == "fixed"]
    assert fixed and fixed[0]["ids"] == [finding(before, "node-changed")["id"]]
    assert all("cause" not in i and "expired" not in i for i in doc["items"])
    assert doc["before"]["path"] == "a/report.json"
    assert doc["after"]["path"] == "report.json"  # absolute: the file name only


def _write(tmp: Path, name: str, r: JSON) -> Path:
    p = tmp / name
    p.write_text(json.dumps(r))
    return p


def test_compare_json_shape_and_determinism(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = load("cannectivity-c1")
    a = _write(tmp_path, "before.json", before)
    b = _write(tmp_path, "after.json", drop(before, "node-changed"))
    out = tmp_path / "cmp"
    assert main(["compare", str(a), str(b), "--format", "json", "--out", str(out)]) == 0
    first = capsys.readouterr().out
    doc = json.loads((out / "compare.json").read_text())
    assert json.loads(first) == doc
    assert set(doc) == {
        "exit_code",
        "summary",
        "before",
        "after",
        "from",
        "to",
        "pinned",
        "unseen_source_build_broke",
        "inconclusive",
        "gaps",
        "items",
    }
    assert not (out / "logs").exists()
    assert main(["compare", str(a), str(b), "--format", "json"]) == 0
    assert capsys.readouterr().out == first
    assert main(["compare", str(a), str(b)]) == 0
    assert "fixed: 1" in capsys.readouterr().out


def test_compare_dispatches_without_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv("ZMIG_CACHE", str(cache))
    r = load("golioth-b")
    a, b = _write(tmp_path, "a.json", r), _write(tmp_path, "b.json", r)
    assert main(["compare", str(a), str(b)]) == 0
    assert not cache.exists()


# --- Refusals ----------------------------------------------------------------------------


def _set(r: JSON, path: str, value: object) -> JSON:
    r = copy.deepcopy(r)
    *head, last = path.split(".")
    d = r
    for k in head:
        d = d[k]
    d[last] = value
    return r


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("inputs.app", "/elsewhere"),
        ("inputs.from.sha", "f" * 40),
        ("inputs.to.sha", "e" * 40),
        ("inputs.board", {"from": "other", "to": "other", "mapping": "same"}),
        ("inputs.build_mode", "fast"),
        ("inputs.sysbuild", True),
        ("zmig_version", "9.9.9"),
    ],
)
def test_refuses_each_mismatch(path: str, value: object) -> None:
    r = load("golioth-b")
    assert mismatches(r, _set(r, path, value))


def test_refuses_different_analyzer_selection() -> None:
    r = load("golioth-b")
    fewer = copy.deepcopy(r)
    fewer["analyzers"] = [a for a in r["analyzers"] if a["name"] != "sbom"]
    assert any(m.startswith("analyzers:") for m in mismatches(r, fewer))
    # An analyzer label's @image doesn't count.
    tagged = copy.deepcopy(r)
    tagged["analyzers"][0]["name"] += "@app"
    assert not mismatches(r, tagged)


def test_zmig_build_decides_when_both_have_it() -> None:
    r = load("golioth-b")  # 1.3: no zmig_build
    a = _set(r, "environment.zmig_build", "1" * 16)
    assert mismatches(a, _set(a, "environment.zmig_build", "2" * 16))
    # Same build, different version string (a dev build): comparable.
    assert not mismatches(a, _set(a, "zmig_version", "0.1.1"))
    # Only one has it: falls back to zmig_version.
    assert not mismatches(r, a)


def test_refusals_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    r = load("golioth-b")
    a = _write(tmp_path, "a.json", r)
    assert main(["compare", str(a), str(a)]) == 2
    assert main(["compare", str(a), str(tmp_path / "missing.json")]) == 2
    plan = _write(tmp_path, "plan.json", _set(r, "command", "plan"))
    assert main(["compare", str(a), str(plan)]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert main(["compare", str(a), str(bad)]) == 2
    other = _write(tmp_path, "o.json", _set(r, "inputs.to.sha", "e" * 40))
    assert main(["compare", str(a), str(other)]) == 2
    assert "inputs.to.sha" in capsys.readouterr().err


# --- Contexts ----------------------------------------------------------------------------


def test_effective_contexts_with_module_recovery_and_salvage() -> None:
    src_old = {"status": "build-failed", "superseded_by_module_recovery": ["m"]}
    src = {"status": "ok", "modules_added": ["m"]}
    tgt_old = {"status": "configure-failed", "superseded_by_module_recovery": ["m"]}
    tgt = {"status": "configure-failed"}
    salv = {"status": "ok", "salvage_of": "k", "salvage_edits": [{}]}
    v15 = [src_old, src, tgt_old, tgt, salv]
    assert effective_contexts(v15) == (src, tgt, salv)
    sides = ["source", "source", "target", "target", "target"]
    v16 = [{**c, "side": s} for c, s in zip(v15, sides, strict=True)]
    assert effective_contexts(v16) == (v16[1], v16[3], v16[4])
    # The old contexts[1:] read named the source build as the target here.
    assert target_build(v15) == (
        "target build: configure-failed; salvaged build ok (1 line(s) disabled)"
    )
    assert target_build([{"status": "ok"}]) == "target build: not run"


def test_target_failure_whose_error_changed_says_so() -> None:
    """A target failure keeps its id whatever the error: when the edit fixed one error and
    the build now stops at another, compare shows the old error, not a site count of two
    different errors (regression: ardep's board.yml error, then a Kconfig abort, "1 -> 0
    sites")."""
    from zmig.report.human import render_compare

    before = load("cannectivity-c1")
    after = copy.deepcopy(before)
    f = finding(after, "target-build-failed")
    old_title = f["title"]
    f["title"] = "The target build fails: 1 error (compiler)"
    f["evidence"] = [e for e in f["evidence"] if e["type"] != "file_line"]
    res = run(before, after)
    out = compare_json(res, before, after, Path("b.json"), Path("a.json"))
    (item,) = [i for i in out["items"] if f["id"] in i["ids"]]
    assert item["error_before"] == old_title
    assert "sites_before" not in item
    assert f"(was: {old_title})" in render_compare(out)
    # The same error with fewer sites still counts sites.
    after2 = copy.deepcopy(before)
    g = finding(after2, "target-build-failed")
    g["evidence"] = [e for e in g["evidence"] if e["type"] != "file_line"][:1] + [
        e for e in g["evidence"] if e["type"] == "file_line"
    ][:1]
    out2 = compare_json(run(before, after2), before, after2, Path("b.json"), Path("a.json"))
    (item2,) = [i for i in out2["items"] if g["id"] in i["ids"]]
    assert "error_before" not in item2


def test_summary_speaks_of_two_scans_not_a_baseline() -> None:
    """compare's summary and gap labels are its own: BEFORE isn't a baseline, and a gap
    both scans have isn't "accepted" (regression: watch's "no new finding outside the
    baseline; analysis incomplete (accepted gaps)" on every ardep dogfood run)."""
    from zmig.report.human import render_compare

    def text(before: JSON, after: JSON, allow: bool = False) -> tuple[str, str]:
        res = run(before, after, allow=allow)
        out = compare_json(res, before, after, Path("b.json"), Path("a.json"))
        return res.outcome.summary, render_compare(out)

    r = load("cannectivity-c1")
    same, _ = text(r, copy.deepcopy(r))
    assert same in ("nothing new", "nothing new; some analyses were incomplete in both scans")
    new, _ = text(r, add_new(r, "node-changed", "/new", "breaking"))
    assert new == "new in AFTER: 1 breaking"
    g = load("golioth-b")
    gap, shown = text(g, skip(g, "api"))
    assert gap.startswith("inconclusive: nothing new, but AFTER couldn't check everything")
    assert "(new in AFTER)" in shown or "(worse in AFTER)" in shown
    allowed, _ = text(g, skip(g, "api"), allow=True)
    assert allowed.startswith("nothing new; WARNING:")
    for s in (same, new, gap, allowed, shown):
        assert "baseline" not in s and "accepted" not in s


@pytest.mark.parametrize("to", ["risky", "info"])
def test_a_less_severe_finding_is_de_escalated_not_unchanged_or_fixed(to: str) -> None:
    """golioth: NET_EVENT_IPV4_ADDR_ADD went from breaking (stored in a narrower type) to
    risky once the edit widened the variable, and compare said "unchanged". A drop to info
    left the compared set and read "fixed". Both are "de-escalated", with BEFORE's
    severity; it doesn't gate."""
    from zmig.report.human import render_compare

    before = load("golioth-b")
    breaking = next(f for f in before["findings"] if f["severity"] == "breaking")
    after = copy.deepcopy(before)
    next(f for f in after["findings"] if f["id"] == breaking["id"])["severity"] = to
    res = run(before, after)
    assert res.outcome.code == 0
    out = compare_json(res, before, after, Path("b.json"), Path("a.json"))
    (item,) = [i for i in out["items"] if breaking["id"] in i["ids"]]
    assert item["state"] == "de-escalated"
    assert item["severity_before"] == "breaking" and item["severity"] == to
    assert "(was breaking)" in render_compare(out)
