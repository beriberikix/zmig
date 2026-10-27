"""SARIF 2.1.0 for code-scanning UIs. Only findings with evidence in the user's own files
(file_line evidence with a relative path) become results; paths are relative to the app
(scan) or workspace (plan), declared as the APPROOT base."""

from __future__ import annotations

import json
from typing import Any

from zmig import __version__
from zmig.findings import Finding, Report, Severity

LEVEL = {Severity.BREAKING: "error", Severity.RISKY: "warning", Severity.INFO: "note"}


def _locations(f: Finding) -> list[dict[str, Any]]:
    out = []
    for e in f.evidence:
        if e.get("type") == "file_line" and not str(e["path"]).startswith(("<", "/", "(")):
            out.append(
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": e["path"], "uriBaseId": "APPROOT"},
                        "region": {"startLine": e["line"]},
                    }
                }
            )
    return out


def render_sarif(report: Report) -> str:
    rules: dict[str, dict[str, Any]] = {}
    results = []
    for f in report.sorted_findings():
        locs = _locations(f)
        if not locs:
            continue
        rule_id = f"{f.category}/{f.kind}"
        rules.setdefault(
            rule_id,
            {
                "id": rule_id,
                "name": f.kind,
                "shortDescription": {"text": f"{f.category}: {f.kind}"},
            },
        )
        results.append(
            {
                "ruleId": rule_id,
                "level": LEVEL[f.severity],
                "message": {"text": f"{f.title}. {f.action}"},
                "locations": locs,
                "partialFingerprints": {"zmigFindingId": f.id},
                "properties": {
                    "severity": str(f.severity),
                    "confidence": str(f.confidence),
                    "heuristics": sorted(f.heuristics),
                },
            }
        )
    base = report.inputs.get("app") or report.inputs.get("workspace") or "."
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "zmig",
                        "version": __version__,
                        "rules": sorted(rules.values(), key=lambda r: r["id"]),
                    }
                },
                "originalUriBaseIds": {"APPROOT": {"description": {"text": str(base)}}},
                "results": results,
            }
        ],
    }
    return json.dumps(doc, indent=2) + "\n"
