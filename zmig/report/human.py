"""Compact terminal summary. The full checklist is MIGRATION.md (--out or --format markdown)."""

from __future__ import annotations

from zmig.findings import Finding, Report, Severity

MARK = {Severity.BREAKING: "BREAKING", Severity.RISKY: "risky   ", Severity.INFO: "info    "}


def _where(f: Finding) -> str:
    for e in f.evidence:
        if e.get("type") == "file_line" and not str(e["path"]).startswith("<"):
            return f"  [{e['path']}:{e['line']}]"
    return ""


def render_human(report: Report) -> str:
    inp, c = report.inputs, report.counts()
    lines = [f"zmig {report.command}: {inp['from']['spec']} → {inp['to']['spec']}"]
    if board := inp.get("board"):
        lines[0] += f"  ({board['from']} → {board['to']})"
    lines.append(f"{c['breaking']} breaking, {c['risky']} risky, {c['info']} info")
    if report.top():
        lines += ["", "Do these first:"]
        lines += [
            f"  {i}. {'[' + f.image + '] ' if f.image else ''}{f.title}{_where(f)}"
            for i, f in enumerate(report.top(), 1)
        ]
    lines.append("")
    for f in report.sorted_findings():
        if f.severity is not Severity.INFO:
            lines.append(f"{MARK[f.severity]} {f.confidence:<6} {f.category}: {f.title}{_where(f)}")
    info = sum(1 for f in report.findings if f.severity is Severity.INFO)
    if info:
        lines.append(f"(+{info} info findings in MIGRATION.md / report.json)")
    bad = [a for a in report.analyzers if a.status != "ok"]
    for a in bad:
        lines.append(f"! analyzer {a.name} {a.status}: {a.reason}")
    return "\n".join(lines) + "\n"
