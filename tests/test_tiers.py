"""Presentation tiers (zmig.tiers): the rule, and its invariants.

Invariants: nothing is hidden or dropped; a breaking finding is never FYI (so a breaking
finding in the scanned build never is); assignment is deterministic; the tier changes no
other field, no watch fingerprint, and isn't read by the verdict.
"""

from __future__ import annotations

import inspect
import json
import random
from pathlib import Path
from typing import Any

import pytest

from zmig import tiers
from zmig.findings import Category, Confidence, Finding, Report, Severity, file_line
from zmig.report.markdown import render_markdown
from zmig.tiers import TIERS, BuildUse, assign, tier
from zmig.watch import FingerprintContext, fingerprint

FIXTURES = Path(__file__).parent / "fixtures"
REPORTS = sorted(
    p
    for p in FIXTURES.rglob("*.json")
    if '"findings"' in p.read_text(errors="replace")[:200000]
    and "zmig_version" in p.read_text(errors="replace")
)

# CANnectivity C1 (DOGFOOD-4 baseline): led.c and usb.c compiled, dfu.c not.
C1 = BuildUse(
    sources=frozenset({"src/led.c", "src/usb.c", "src/main.c"}),
    overlays=frozenset({"boards/usb2canfdv1.overlay"}),
    configs=frozenset({"prj.conf"}),
)


def _ev(*locs: str) -> list[dict[str, Any]]:
    return [file_line(p.rsplit(":", 1)[0], int(p.rsplit(":", 1)[1])) for p in locs]


@pytest.mark.parametrize(
    ("severity", "confidence", "locs", "want"),
    [
        ("breaking", "high", ["src/led.c:225"], "act-now"),  # function-removed smf_set_handled
        ("breaking", "medium", ["src/led.c:225"], "review"),  # the guide entry: confidence
        ("breaking", "high", [], "review"),  # target build failed: no location
        ("breaking", "high", ["src/dfu.c:25"], "review"),  # not compiled: never FYI
        ("risky", "high", ["src/usb.c:126"], "review"),  # &zephyr_udc0 changed
        ("risky", "high", ["src/dfu.c:25"], "fyi"),  # mcuboot_led0: dfu.c not compiled
        ("risky", "low", ["src/led.c:79"], "review"),  # confidence never moves into FYI
        ("risky", "high", ["<zephyr>/include/zephyr/smf.h:10"], "fyi"),  # not a project file
        ("risky", "high", ["Kconfig:175"], "review"),  # no record for Kconfig files: used
        ("risky", "high", ["boards/other.overlay:3"], "fyi"),  # overlay the image didn't apply
        ("info", "high", ["src/led.c:225"], "fyi"),
    ],
)
def test_rule(severity: str, confidence: str, locs: list[str], want: str) -> None:
    assert tier(severity, confidence, _ev(*locs), C1) == want


def test_no_build_records_means_used() -> None:
    assert tier("risky", "high", _ev("src/dfu.c:25"), None) == "review"
    assert tier("risky", "high", _ev("src/dfu.c:25"), BuildUse()) == "review"


def test_inputs_are_run_time_evidence_only() -> None:
    """No evaluation data can reach the rule: its inputs are severity, confidence, the
    evidence items and the build's use of files."""
    assert list(inspect.signature(tier).parameters) == ["severity", "confidence", "evidence", "use"]
    assert "confirmed" not in inspect.getsource(tiers).split('"""', 2)[2]


def _every_finding() -> list[tuple[str, dict[str, Any]]]:
    out = []
    for p in REPORTS:
        for f in json.loads(p.read_text())["findings"]:
            out.append((p.name, f))
    return out


def test_fixtures_are_there() -> None:
    assert len(REPORTS) >= 5 and len(_every_finding()) > 50


@pytest.mark.parametrize("use", [None, BuildUse(frozenset(), frozenset(), frozenset())])
def test_invariants_on_every_stored_report(use: BuildUse | None) -> None:
    """With no build records, and with records saying the build used nothing (the most
    demoting case): every finding gets exactly one tier, and no breaking one is FYI."""
    for name, f in _every_finding():
        t = tier(f["severity"], f["confidence"], f["evidence"], use)
        assert t in TIERS, name
        if f["severity"] == "breaking":
            assert t != "fyi", (name, f["id"])


def test_assignment_is_deterministic() -> None:
    findings = _every_finding()
    want = {
        (n, f["id"]): tier(f["severity"], f["confidence"], f["evidence"], C1) for n, f in findings
    }
    for seed in range(5):
        shuffled = list(findings)
        random.Random(seed).shuffle(shuffled)
        got = {
            (n, f["id"]): tier(f["severity"], f["confidence"], f["evidence"], C1)
            for n, f in shuffled
        }
        assert got == want


def _finding(sev: Severity, conf: Confidence, *locs: str, image: str | None = None) -> Finding:
    return Finding(
        Category.API,
        f"k{len(locs)}{sev}{conf}",
        f"S{locs}",
        f"t {locs}",
        sev,
        conf,
        "act",
        "api",
        ("s",),
        tuple(_ev(*locs)),
        image=image,
    )


FINDINGS = [
    _finding(Severity.BREAKING, Confidence.HIGH, "src/led.c:225"),
    _finding(Severity.BREAKING, Confidence.MEDIUM, "src/led.c:263"),
    _finding(Severity.BREAKING, Confidence.HIGH),
    _finding(Severity.RISKY, Confidence.HIGH, "src/dfu.c:25"),
    _finding(Severity.RISKY, Confidence.HIGH, "src/usb.c:126", image="mcuboot"),
    _finding(Severity.INFO, Confidence.LOW, "src/main.c:1"),
]


def test_assign_changes_nothing_else() -> None:
    tiered = assign(FINDINGS, {None: C1, "mcuboot": None})
    assert [f.tier for f in tiered] == ["act-now", "review", "review", "fyi", "review", "fyi"]
    ctx = FingerprintContext(board="b", refs=("v1", "v2"))
    for before, after in zip(FINDINGS, tiered, strict=True):
        a, b = before.to_json(), after.to_json()
        assert b.pop("tier") in TIERS and "tier" not in a
        assert a == b  # every other field identical
        assert fingerprint(a, ctx) == fingerprint(after.to_json(), ctx)


def test_markdown_groups_every_finding_once_and_keeps_the_rest() -> None:
    tiered = assign(FINDINGS, {None: C1})
    inputs = {"from": {"spec": "v1", "sha": "a" * 40}, "to": {"spec": "v2", "sha": "b" * 40}}
    md = render_markdown(Report("scan", "0", inputs, {}, findings=tiered))
    by_tier = md.split("## By tier", 1)[1].split("\n## ", 1)[0]
    for f in tiered:
        assert by_tier.count(f"(`{f.id}`)") == 1
    assert "(1)" in by_tier and "(3)" in by_tier and "(2)" in by_tier
    # The untiered sections still list every finding.
    plain = render_markdown(Report("scan", "0", inputs, {}, findings=FINDINGS))
    assert "## By tier" not in plain
    rest = md.replace(by_tier, "")
    for f in tiered:
        assert f"(`{f.id}`)" in rest and f"(`{f.id}`)" in plain
