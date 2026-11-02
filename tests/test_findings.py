from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from zmig.findings import (
    Category,
    Confidence,
    Finding,
    Report,
    Severity,
    artifact_diff,
    file_line,
    make_id,
)
from zmig.report import render

SCHEMA = json.loads((Path(__file__).parents[1] / "zmig/schema/report-1.json").read_text())
SCOPE = ("a" * 40, "b" * 40, "native_sim")


def finding(sev: Severity, subject: str, in_app: bool = False, **kw: object) -> Finding:
    ev = (file_line("prj.conf", 3, "CONFIG_X=y"),) if in_app else ()
    ev += (artifact_diff("zephyr/.config", "v1", "v2", "y", None),)
    return Finding(
        category=kw.pop("category", Category.KCONFIG),  # type: ignore[arg-type]
        kind="symbol-removed",
        subject=subject,
        title=f"{subject} removed",
        severity=sev,
        confidence=kw.pop("confidence", Confidence.HIGH),  # type: ignore[arg-type]
        action="do something",
        analyzer="kconfig",
        scope=SCOPE,
        evidence=ev,
        **kw,  # type: ignore[arg-type]
    )


def report(findings: list[Finding]) -> Report:
    ref = {"spec": "v1", "sha": "a" * 40, "kind": "tag", "dirty": False}
    return Report(
        command="scan",
        zmig_version="0.1.0",
        inputs={"from": ref, "to": {**ref, "spec": "v2", "sha": "b" * 40}},
        environment={"python": "3.14"},
        findings=findings,
    )


def test_ids_are_stable_and_scoped() -> None:
    a = make_id(Category.KCONFIG, "symbol-removed", "CONFIG_X", SCOPE)
    assert a == make_id(Category.KCONFIG, "symbol-removed", "CONFIG_X", SCOPE)
    assert a.startswith("KCONFIG-SYMBOL-REMOVED-") and len(a.rsplit("-", 1)[1]) == 8
    assert a != make_id(Category.KCONFIG, "symbol-removed", "CONFIG_X", ("c" * 40, *SCOPE[1:]))


def test_sorting_and_top() -> None:
    info = finding(Severity.INFO, "CONFIG_I")
    risky = finding(Severity.RISKY, "CONFIG_R")
    brk = finding(Severity.BREAKING, "CONFIG_B")
    brk_app = finding(Severity.BREAKING, "CONFIG_A", in_app=True)
    brk_low = finding(Severity.BREAKING, "CONFIG_L", confidence=Confidence.LOW)
    r = report([info, risky, brk_low, brk, brk_app])
    assert [f.severity for f in r.sorted_findings()] == [
        Severity.BREAKING,
        Severity.BREAKING,
        Severity.BREAKING,
        Severity.RISKY,
        Severity.INFO,
    ]
    assert r.sorted_findings()[-3] is brk_low  # low confidence after high, same severity
    assert r.top()[0] is brk_app  # breaking + points into the app ranks first
    assert r.counts() == {"breaking": 3, "risky": 1, "info": 1}


def test_report_validates_and_is_deterministic() -> None:
    fs = [finding(Severity.RISKY, "CONFIG_R"), finding(Severity.BREAKING, "CONFIG_B", True)]
    a = render(report(fs), "json")
    b = render(report(list(reversed(fs))), "json")
    assert a == b
    jsonschema.validate(json.loads(a), SCHEMA)


def test_markdown_lists_breaking_first() -> None:
    md = render(
        report([finding(Severity.INFO, "CONFIG_I"), finding(Severity.BREAKING, "CONFIG_B", True)]),
        "markdown",
    )
    assert md.index("CONFIG_B removed") < md.index("CONFIG_I removed")
    assert "`prj.conf:3`" in md and "## Do these first" in md


def _doc(**top: object) -> dict[str, object]:
    """A rendered report with fields set, for schema tests (schema 1.6)."""
    fs = [finding(Severity.BREAKING, "CONFIG_B", True)]
    doc: dict[str, object] = json.loads(render(report(fs), "json"))
    doc.update(top)
    return doc


def _path(remedies: object) -> dict[str, object]:
    return {
        "verdict": "needs-changes",
        "confidence": "medium",
        "categories": {},
        "assumptions": [],
        "remedies": remedies,
    }


def _ctx(**kw: object) -> dict[str, object]:
    base = {"ref": "a" * 40, "board": "native_sim", "mode": "full", "status": "ok", "key": "k"}
    return {**base, **kw}


def _invalid(doc: dict[str, object]) -> bool:
    try:
        jsonschema.validate(doc, SCHEMA)
    except jsonschema.ValidationError:
        return True
    return False


def test_schema_1_6_fields_validate() -> None:
    fid = make_id(Category.KCONFIG, "symbol-removed", "CONFIG_B", SCOPE)
    doc = _doc(
        environment={"python": "3.14", "zmig_build": "0123456789abcdef"},
        contexts=[_ctx(side="source"), _ctx(side="target")],
        upgrade_path=_path({"reflash": [fid], "unknown": ["category:layout"]}),
    )
    assert doc["schema_version"] == "1.6"
    jsonschema.validate(doc, SCHEMA)


def test_schema_accepts_1_5_reports() -> None:
    # No zmig_build, no side: both are optional.
    jsonschema.validate(_doc(schema_version="1.5", contexts=[_ctx(), _ctx()]), SCHEMA)


def test_schema_rejects_unknown_remedy() -> None:
    assert _invalid(_doc(upgrade_path=_path({"rewrite-it": ["category:layout"]})))


def test_schema_rejects_remedy_value_that_is_not_an_id() -> None:
    assert _invalid(_doc(upgrade_path=_path({"reflash": ["CONFIG_B"]})))
    assert _invalid(_doc(upgrade_path=_path({"reflash": "category:layout"})))


def test_schema_rejects_non_string_zmig_build() -> None:
    assert _invalid(_doc(environment={"python": "3.14", "zmig_build": None}))


def test_schema_rejects_unknown_context_side() -> None:
    assert _invalid(_doc(contexts=[_ctx(side="both")]))
