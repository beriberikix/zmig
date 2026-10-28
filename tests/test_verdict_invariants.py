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
