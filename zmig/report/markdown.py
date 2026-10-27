"""MIGRATION.md: a risk-ranked checklist, severity first."""

from __future__ import annotations

import json
import re
from typing import Any

from zmig.findings import Category, Finding, Report, Severity


def _ref(r: dict[str, Any]) -> str:
    dirty = " (dirty)" if r.get("dirty") else ""
    return f"`{r['spec']}` ({r['sha'][:12]}){dirty}"


def _short(v: Any) -> str:
    """Compact display of an evidence value: abbreviate SHAs, JSON for structures."""
    if isinstance(v, str) and re.fullmatch(r"[0-9a-f]{40}", v):
        return v[:12]
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True)
    return "none" if v is None else str(v)


def _evidence(ev: dict[str, Any]) -> str:
    t = ev["type"]
    if t == "file_line":
        return f"`{ev['path']}:{ev['line']}`"
    if t == "doc_entry":
        return f"`{ev['doc']}:{ev['line']}` @ {ev['ref']}"
    if t == "artifact_diff":
        old, new = ev["old"], ev["new"]
        if isinstance(old, dict) and isinstance(new, dict):
            keys = sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
            return f"{ev['artifact']}: " + ", ".join(
                f"{k} `{_short(old.get(k))}` → `{_short(new.get(k))}`" for k in keys
            )
        return f"{ev['artifact']}: `{_short(old)}` → `{_short(new)}`"
    if t == "git_commit":
        return f"{ev['repo']}@{ev['sha'][:12]}"
    if t == "git_conflict":
        return "conflicts in " + ", ".join(f"`{f}`" for f in ev["files"])
    if t == "tool_output":
        excerpt = " ".join(str(ev["excerpt"]).split())
        return f"{ev['invocation']}: `{excerpt[:200]}`"
    return str(t)


SECTION_TITLES = {
    "manifest": "Manifest",
    "fork": "Forks and local patches",
    "hop": "Upgrade path",
    "board": "Board",
    "kconfig": "Kconfig",
    "devicetree": "Devicetree",
    "build": "Build",
    "guide": "Migration guide",
    "sbom": "SBOM",
}
_SEV_RANK = {Severity.BREAKING: 0, Severity.RISKY: 1, Severity.INFO: 2}
PREVIEW = 8
LEGEND = (
    "Severity: **breaking** = the build fails or something you rely on is gone; "
    "**risky** = builds but behavior may differ; **info** = for awareness. "
    "Confidence says how the finding was derived: high = read from build artifacts or git; "
    "medium/low = matched by name or wording (see Heuristics)."
)


def _preview(items: list[str]) -> str:
    more = len(items) - PREVIEW
    return ", ".join(f"`{i}`" for i in items[:PREVIEW]) + (f" … (+{more})" if more > 0 else "")


def _details(f: Finding) -> str | None:
    """A short preview of aggregated data so info findings aren't empty in Markdown."""
    new = f.new
    if isinstance(new, dict):
        if f.kind in ("default-changed",):
            return _preview([f"{k}: {v[0]} → {v[1]}" for k, v in new.items()])
        if f.kind in ("newly-set", "no-longer-set"):
            return _preview([f"{k}={v}" for k, v in new.items()])
        if f.kind == "footprint":
            return "Largest changes: " + _preview(
                [f"{s['symbol']} {s['delta']:+}" for s in new.get("symbols", [])]
            )
        if f.kind == "zephyr-sources":
            parts = []
            if new.get("busiest"):
                parts.append(
                    "most-changed upstream: "
                    + _preview([f"{b['file']} ({b['commits']})" for b in new["busiest"]])
                )
            for key in ("added", "removed"):
                if new.get(key):
                    parts.append(f"{key}: " + _preview(new[key]))
            return "; ".join(parts) or None
        if f.kind == "other-nodes":
            return "; ".join(f"{k}: " + _preview(v) for k, v in new.items() if v) or None
        if f.kind == "module-repinned" and new.get("changed"):
            return "changed: " + _preview(new["changed"])
    if isinstance(new, list) and new and all(isinstance(x, str) for x in new):
        return _preview(new)
    return None


def _item(f: Finding) -> list[str]:
    lines = [
        f"- [ ] **{f.severity}** / {f.confidence}: {f.title} (`{f.id}`)",
        f"  - Action: {f.action}",
    ]
    if (details := _details(f)) is not None:
        lines.append(f"  - Details: {details}")
    if f.evidence:
        lines.append("  - Evidence: " + "; ".join(_evidence(e) for e in f.evidence))
    if f.heuristics:
        lines.append("  - Heuristics: " + ", ".join(sorted(f.heuristics)))
    return lines


def render_markdown(report: Report) -> str:
    inp = report.inputs
    counts = report.counts()
    out = [
        "# Zephyr migration report",
        "",
        f"- From: {_ref(inp['from'])}",
        f"- To: {_ref(inp['to'])}",
    ]
    if board := inp.get("board"):
        out.append(f"- Board: `{board['from']}` → `{board['to']}`")
    if mode := inp.get("build_mode"):
        out.append(f"- Build mode: {mode}")
    out += [
        f"- Findings: {counts['breaking']} breaking, {counts['risky']} risky, "
        f"{counts['info']} info",
        "",
        LEGEND,
        "",
    ]
    if report.findings:
        out += ["## Do these first", ""]
        for i, f in enumerate(report.top(), 1):
            out.append(f"{i}. **{f.severity}**: {f.title} (`{f.id}`)")
        out.append("")
        by_cat: dict[Category, list[Finding]] = {}
        for f in report.sorted_findings():
            by_cat.setdefault(f.category, []).append(f)
        # Sections by their worst finding (breaking, then risky, then info), then name.
        for cat in sorted(
            by_cat,
            key=lambda c: (min(_SEV_RANK[f.severity] for f in by_cat[c]), str(c)),
        ):
            out += [f"## {SECTION_TITLES.get(str(cat), str(cat).capitalize())}", ""]
            for f in by_cat[cat]:
                out += _item(f)
            out.append("")
        low = [f for f in report.sorted_findings() if f.heuristics]
        if low:
            out += ["## Heuristic and low-confidence findings", ""]
            out += [f"- `{f.id}`: {', '.join(sorted(f.heuristics))}" for f in low]
            out.append("")
    else:
        out += ["No findings.", ""]
    not_ok = [a for a in report.analyzers if a.status != "ok"]
    out += ["## Analyzers", ""]
    if not report.analyzers:
        out.append("No analyzers ran.")
    for a in sorted(report.analyzers, key=lambda a: a.name):
        out.append(f"- {a.name}: {a.status}" + (f" ({a.reason})" if a.reason else ""))
    if not_ok:
        out += ["", "Skipped or failed analyzers mean the report may be incomplete."]
    if report.contexts:
        out += ["", "## Builds", ""]
        for c in report.contexts:
            line = f"- `{c['ref']}` / `{c['board']}` ({c['mode']}): {c['status']}"
            if edits := c.get("salvage_edits"):
                line += (
                    " (salvaged: disabled "
                    + ", ".join(f"`{e['file']}:{e['line']}`" for e in edits)
                    + ")"
                )
            out.append(line)
    out.append("")
    return "\n".join(out)
