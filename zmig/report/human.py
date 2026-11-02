"""Compact terminal summary. The full checklist is MIGRATION.md (--out or --format markdown)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zmig.findings import Finding, Report, Severity

MARK = {Severity.BREAKING: "BREAKING", Severity.RISKY: "risky   ", Severity.INFO: "info    "}


Ctx = dict[str, Any]


def effective_contexts(contexts: list[Ctx]) -> tuple[Ctx | None, Ctx | None, Ctx | None]:
    """(source, raw target, salvaged target): the contexts a scan analyzed. Module recovery
    adds superseded contexts and salvage adds one, so position alone doesn't say which is
    which. Schema 1.6 contexts carry `side`; for 1.5 the source is the first context that
    wasn't superseded, and the targets follow it."""
    live = [c for c in contexts if "superseded_by_module_recovery" not in c]
    if any("side" in c for c in contexts):
        source = next((c for c in live if c.get("side") == "source"), None)
        targets = [c for c in live if c.get("side") == "target"]
    else:
        source = live[0] if live else None
        targets = live[1:]
    raw = next((c for c in targets if "salvage_of" not in c), None)
    salvaged = next((c for c in targets if "salvage_of" in c), None)
    return source, raw, salvaged


def target_build(contexts: list[Ctx]) -> str:
    """'target build: ok', or how it failed and whether a salvaged build was compared."""
    _, raw, salv = effective_contexts(contexts)
    if raw is None:
        return "target build: not run"
    out = f"target build: {raw.get('status')}"
    if raw.get("modules_added"):
        out += f" (with {', '.join(map(str, raw['modules_added']))} added)"
    if salv is not None:
        n = len(salv.get("salvage_edits") or [])
        out += f"; salvaged build {salv.get('status')} ({n} line(s) disabled)"
    return out


def _where(f: Finding) -> str:
    for e in f.evidence:
        if e.get("type") == "file_line" and not str(e["path"]).startswith("<"):
            return f"  [{e['path']}:{e['line']}]"
    return ""


def render_human(report: Report) -> str:
    inp, c = report.inputs, report.counts()

    def label(r: dict[str, object]) -> str:
        spec = str(r["spec"])
        return f"{Path(spec).name}@{str(r['sha'])[:12]}" if r.get("kind") == "path" else spec

    lines = [f"zmig {report.command}: {label(inp['from'])} → {label(inp['to'])}"]
    if board := inp.get("board"):
        lines[0] += f"  ({board['from']} → {board['to']})"
    lines.append(
        f"Build migration: {c['breaking']} breaking, {c['risky']} risky, {c['info']} info"
        + (f"; {target_build(report.contexts)}" if report.command == "scan" else "")
    )
    if u := report.upgrade_path:
        lines.append(
            f"Field-upgrade verdict: {u['verdict']} ({u['confidence']} confidence)"
            + (
                " for reflashing (no bootloader: no app-OTA path)"
                if u.get("update_path") == "reflash"
                else ""
            )
            + (
                "; remedies: " + ", ".join(f"{r} x{len(ids)}" for r, ids in u["remedies"].items())
                if u.get("remedies")
                else ""
            )
        )
        unknown = [n for n, cat in u["categories"].items() if cat["status"] == "unknown"]
        if unknown:
            lines.append("  unknown: " + ", ".join(unknown))
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
    if report.checked or report.not_checked:
        clean = sum(1 for c in report.checked if c.result == "clean")
        lines.append(
            f"(checked: {clean} clean, {len(report.checked) - clean} with findings or gaps; "
            f"{len(report.not_checked)} not checked: see MIGRATION.md)"
        )
    bad = [a for a in report.analyzers if a.status != "ok"]
    for a in bad:
        lines.append(f"! analyzer {a.name} {a.status}: {a.reason}")
    return "\n".join(lines) + "\n"


# Most actionable first; "unchanged" items are only counted.
_COMPARE_GROUPS = (
    ("new", "new (not in BEFORE)"),
    ("escalated", "escalated (higher severity)"),
    ("new-use-site", "new use sites"),
    ("revealed", "revealed (BEFORE could not see these; hidden, or caused by the edit)"),
    ("unseen", "unseen (gone, but AFTER could not check; still open)"),
    ("fixed", "fixed"),
    ("updated", "updated (same finding, changed value or fewer sites)"),
)


def render_compare(result: dict[str, Any]) -> str:
    """`zmig compare` for the terminal, from its compare.json."""
    lines = [f"zmig compare: {result['summary']}"]
    if n := result.get("unseen_source_build_broke"):
        lines.append(
            f"  {n} finding(s) unseen because the edited app no longer builds at the source ref"
        )
    for g in result.get("gaps", []):
        if g["state"] != "accepted":
            lines.append(f"  gap: {g['detail']} ({g['state']})")
    items = result.get("items", [])
    for state, heading in _COMPARE_GROUPS:
        group = [i for i in items if i["state"] == state]
        if not group:
            continue
        lines += ["", f"{heading}: {len(group)}"]
        for i in group:
            ident = ", ".join(i.get("ids") or []) or i["fingerprint"]
            where = (
                f"  [{i['path']}{':' + str(i['line']) if i.get('line') else ''}]"
                if i["path"]
                else ""
            )
            sites = (
                f" ({i['sites_before']} -> {i['sites_after']} sites)" if "sites_before" in i else ""
            )
            lines.append(f"  {ident} {i['severity']} {i['title']}{sites}{where}")
    same = sum(1 for i in items if i["state"] == "unchanged")
    if same:
        lines += ["", f"unchanged: {same}"]
    return "\n".join(lines) + "\n"
