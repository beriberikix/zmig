"""Verdict invariants (zmig.verdict.invariant_violations), property-style: random
combinations of findings, analyzer outcomes and recorded categories never produce a
verdict that breaks them. Real reports are checked in the fixture tests."""

from __future__ import annotations

import random
from typing import Any

from zmig.findings import AnalyzerStatus, Category, Confidence, Finding, Remedy, Severity
from zmig.verdict import CATEGORIES, DERIVED, invariant_violations, upgrade_path

KINDS = ("partition", "partition-moved", "x")


def _case(rng: random.Random) -> tuple[dict[str, Any], list[Finding], list[AnalyzerStatus], str]:
    findings = []
    for i in range(rng.randint(0, 6)):
        cat = rng.choice(list(Category))
        findings.append(
            Finding(
                category=cat,
                kind=rng.choice(KINDS),
                subject=f"s{i}",
                title="t",
                severity=rng.choice(list(Severity)),
                confidence=rng.choice(list(Confidence)),
                action="a",
                analyzer=cat.value,
                scope=("s",),
                remedy=rng.choice([None, *Remedy]),
                introduced_by_migration=rng.random() < 0.8,
            )
        )
    statuses = []
    for analyzer in CATEGORIES.values():
        r = rng.random()
        if r < 0.75:
            statuses.append(AnalyzerStatus(analyzer, "ok"))
        elif r < 0.9:
            statuses.append(AnalyzerStatus(analyzer, rng.choice(["skipped", "error", "partial"])))
    recorded = {}
    for name in CATEGORIES:
        if name in DERIVED and rng.random() < 0.5:
            continue
        r = rng.random()
        if r < 0.15:
            continue
        status = rng.choice(["clean", "clean", "hazard", "unknown"])
        recorded[name] = {
            "status": status,
            "confidence": rng.choice(["high", "medium", "low"]),
            "checked": [],
            **({"findings": [f.id for f in findings[:1]]} if status == "hazard" else {}),
        }
    return {"categories": recorded}, findings, statuses, rng.choice(["ok", "ok", "build-failed"])


def test_random_cases_never_break_the_invariants() -> None:
    rng = random.Random(20261028)
    for n in range(3000):
        shared, findings, statuses, target = _case(rng)
        up = upgrade_path(shared, findings, statuses, target)
        report = {
            "findings": [f.to_json() for f in findings],
            "analyzers": [s.to_json() for s in statuses],
            "upgrade_path": up,
        }
        assert invariant_violations(report) == [], (n, report)


def test_layout_hazard_never_reads_direct_ok() -> None:
    """B-rak at the DOGFOOD-3 baseline: the layout analyzer recorded a hazard, its findings
    had been downgraded to info, and the verdict said direct-ok (M8)."""
    ok = [AnalyzerStatus(a, "ok") for a in CATEGORIES.values()]
    shared = {
        "categories": {
            n: {"status": "clean", "confidence": "high", "checked": []}
            for n in CATEGORIES
            if n not in DERIVED
        }
    }
    shared["categories"]["layout"] = {
        "status": "hazard",
        "confidence": "medium",
        "checked": [],
        "findings": ["LAYOUT-PARTITION-RESIZED-d87bfe01"],
    }
    info = Finding(Category.LAYOUT, "partition-resized", "slot0_partition", "t", Severity.INFO,
                   Confidence.HIGH, "a", "layout", ("s",), remedy=Remedy.REFLASH)  # fmt: skip
    up = upgrade_path(shared, [info], ok)
    assert up["verdict"] == "needs-changes"
    assert up["remedies"] == {"unknown": ["category:layout"]}
    report = {
        "findings": [info.to_json()],
        "analyzers": [s.to_json() for s in ok],
        "upgrade_path": up,
    }
    assert invariant_violations(report) == []


def test_the_checker_catches_each_violation() -> None:
    base = {
        "analyzers": [{"name": a, "status": "ok"} for a in CATEGORIES.values()],
        "findings": [],
        "upgrade_path": {
            "verdict": "direct-ok",
            "confidence": "high",
            "unchecked": [],
            "categories": {n: {"status": "clean"} for n in CATEGORIES},
        },
    }
    f = {"id": "L1", "category": "layout", "kind": "partition-moved", "severity": "risky",
         "introduced_by_migration": True}  # fmt: skip
    assert invariant_violations({**base, "findings": [f]})
    pre = {**f, "severity": "breaking", "introduced_by_migration": False}
    up = {**base["upgrade_path"], "verdict": "unknown",
          "categories": {**base["upgrade_path"]["categories"], "layout": {"status": "unknown"}},
          "unchecked": ["layout"]}  # fmt: skip
    assert any(
        "confidence" in v
        for v in invariant_violations({**base, "findings": [pre], "upgrade_path": up})
    )
    skipped = {**base, "analyzers": [{"name": "ble", "status": "skipped"}]}
    assert any("didn't run" in v for v in invariant_violations(skipped))


def test_not_applicable_needs_positive_evidence() -> None:
    """A category with no positive evidence is unknown, never not-applicable; a build
    with no bootloader gets a verdict about reflashing, not an OTA path."""
    from zmig.findings import AnalyzerStatus
    from zmig.verdict import CATEGORIES, upgrade_path

    shared = {
        "categories": {
            "mcuboot": {"status": "not-applicable", "confidence": "high", "checked": ["x"],
                        "evidence": ["v1: CONFIG_BOOTLOADER_MCUBOOT=n"]},
            "storage": {"status": "not-applicable", "confidence": "high", "checked": ["y"]},
        }
    }  # fmt: skip
    statuses = [AnalyzerStatus(a, "ok") for a in set(CATEGORIES.values())]
    up = upgrade_path(shared, [], statuses)
    assert up["categories"]["storage"]["status"] == "unknown"
    assert up["categories"]["storage"]["reason"] == "not-applicable without positive evidence"
    assert up["categories"]["mcuboot"]["status"] == "not-applicable"
    assert up["update_path"] == "reflash"
    assert any("no app-OTA path" in b for b in up["basis"])


def test_invariant_not_applicable_without_evidence_from_schema_1_4() -> None:
    """On a real report (CANnectivity C1, schema 1.3: no evidence recorded yet)."""
    import json
    from pathlib import Path

    from zmig.verdict import invariant_violations

    rep = json.loads(
        (Path(__file__).parent / "fixtures" / "watch" / "cannectivity-c1.json").read_text()
    )
    assert not any("positive evidence" in v for v in invariant_violations(rep))
    rep["schema_version"] = "1.4"
    got = {v for v in invariant_violations(rep) if "positive evidence" in v}
    assert got == {
        f"category {n} is not-applicable without positive evidence"
        for n in ("mcuboot", "storage", "ble", "sysbuild")
    }


def test_build_migration_summary_line() -> None:
    from zmig.report.human import target_build

    ctxs = [
        {"status": "ok"},
        {"status": "configure-failed"},
        {"status": "ok", "salvage_of": "k", "salvage_edits": [{}, {}]},
    ]
    assert target_build(ctxs) == (
        "target build: configure-failed; salvaged build ok (2 line(s) disabled)"
    )
    assert target_build([{"status": "ok"}, {"status": "ok"}]) == "target build: ok"
