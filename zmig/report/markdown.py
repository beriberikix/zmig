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


def _item(f: Finding) -> list[str]:
    lines = [
        f"- [ ] **{f.severity}** / {f.confidence}: {f.title} (`{f.id}`)",
        f"  - Action: {f.action}",
    ]
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
    ]
    if report.findings:
        out += ["## Do these first", ""]
        for i, f in enumerate(report.top(), 1):
            out.append(f"{i}. **{f.severity}**: {f.title} (`{f.id}`)")
        out.append("")
        by_cat: dict[Category, list[Finding]] = {}
        for f in report.sorted_findings():
            by_cat.setdefault(f.category, []).append(f)
        # Categories with breaking findings first, then by name.
        for cat in sorted(
            by_cat,
            key=lambda c: (
                min(0 if f.severity is Severity.BREAKING else 1 for f in by_cat[c]),
                str(c),
            ),
        ):
            out += [f"## {str(cat).capitalize()}", ""]
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
            out.append(f"- `{c['ref']}` / `{c['board']}` ({c['mode']}): {c['status']}")
    out.append("")
    return "\n".join(out)
