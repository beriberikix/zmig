"""MIGRATION.md: a risk-ranked checklist, severity first."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from zmig.findings import Category, Finding, Report, Severity


def _ref(r: dict[str, Any]) -> str:
    dirty = " (dirty)" if r.get("dirty") else ""
    # A local checkout: its directory name, not the absolute path (the full path stays in
    # report.json).
    spec = Path(r["spec"]).name if r.get("kind") == "path" else r["spec"]
    return f"`{spec}` ({r['sha'][:12]}){dirty}"


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
    "images": "Images",
    "crossimage": "Consistency between images",
    "init": "Init order",
    "api": "Zephyr API used by your code",
    "sysbuild": "Sysbuild configuration",
    "layout": "Flash layout",
    "mcuboot": "MCUboot and image format",
    "storage": "Persistent storage",
    "ble": "Bluetooth bonds and identity",
    "wire": "Wire formats",
}
VERDICT_TEXT = {
    "direct-ok": "a device running the source build can take the target build by app OTA",
    "needs-changes": "app OTA works after the changes listed under remedies",
    "blocked": "not by app OTA alone: some hazard needs the bootloader changed or a reflash",
    "unknown": "not established: some category couldn't be checked (see below)",
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
    if f.remedy is not None:
        lines.append(f"  - Remedy: {f.remedy}")
    if (details := _details(f)) is not None:
        lines.append(f"  - Details: {details}")
    if f.evidence:
        lines.append("  - Evidence: " + "; ".join(_evidence(e) for e in f.evidence))
    if f.heuristics:
        lines.append("  - Heuristics: " + ", ".join(sorted(f.heuristics)))
    return lines


def _categories(findings: list[Finding], level: str) -> list[str]:
    """Findings grouped by category; sections by their worst finding, then name."""
    by_cat: dict[Category, list[Finding]] = {}
    for f in findings:
        by_cat.setdefault(f.category, []).append(f)
    out: list[str] = []
    for cat in sorted(
        by_cat, key=lambda c: (min(_SEV_RANK[f.severity] for f in by_cat[c]), str(c))
    ):
        out += [f"{level} {SECTION_TITLES.get(str(cat), str(cat).capitalize())}", ""]
        for f in by_cat[cat]:
            out += _item(f)
        out.append("")
    return out


def _image_roles(report: Report) -> dict[str, str]:
    roles: dict[str, str] = {}
    for c in report.contexts:
        for i in c.get("images", []):
            roles[i["name"]] = i["role"]
    return roles


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
    if report.upgrade_path:
        out += _field_upgrade(report.upgrade_path)
    introduced = [f for f in report.sorted_findings() if f.introduced_by_migration]
    pre_existing = [f for f in report.sorted_findings() if not f.introduced_by_migration]
    if report.findings:
        out += ["## Do these first", ""]
        for i, f in enumerate(report.top(), 1):
            where = f"[{f.image}] " if f.image else ""
            out.append(f"{i}. **{f.severity}**: {where}{f.title} (`{f.id}`)")
        out.append("")
        groups: dict[str | None, list[Finding]] = {}
        for f in introduced:
            groups.setdefault(f.image, []).append(f)
        if set(groups) == {None}:
            out += _categories(groups[None], "##")
        else:
            # One section per image: the app (which also holds build-wide findings)
            # first, then the others in name order.
            roles = _image_roles(report)
            app = next((n for n, r in roles.items() if r == "app"), "app")
            for image in [None, *sorted(k for k in groups if k is not None)]:
                if image not in groups:
                    continue
                name = app if image is None else image
                out += [f"## Image: {name} ({roles.get(name, 'image')})", ""]
                out += _categories(groups[image], "###")
        if pre_existing:
            out += [
                "## Already true of the source build",
                "",
                "Not introduced by the migration: the source build has these too. They "
                "affect what a deployed device is running (see the verdict's assumptions).",
                "",
            ]
            for f in pre_existing:
                out += _item(f)
            out.append("")
        low = [f for f in report.sorted_findings() if f.heuristics]
        if low:
            out += ["## Heuristic and low-confidence findings", ""]
            out += [f"- `{f.id}`: {', '.join(sorted(f.heuristics))}" for f in low]
            out.append("")
    else:
        out += ["No findings.", ""]
    if report.not_checked:
        out += ["## Not checked", ""]
        out += [
            f"- **{n.area}** ({n.analyzer}): {n.reason}"
            for n in sorted(report.not_checked, key=lambda n: (n.analyzer, n.area))
        ]
        out.append("")
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


def _field_upgrade(u: dict[str, Any]) -> list[str]:
    """The upgrade-path verdict: can deployed devices take the target build by app OTA?"""
    out = [
        "## Field upgrade",
        "",
        f"**Verdict: {u['verdict']}** ({u['confidence']} confidence): "
        f"{VERDICT_TEXT.get(u['verdict'], '')}"
        + (
            f" (at least; not established for: {', '.join(u['unchecked'])})"
            if u.get("unchecked") and u["verdict"] in ("needs-changes", "blocked")
            else ""
        )
        + ".",
        "",
        "| Category | Status | Confidence | Notes |",
        "|---|---|---|---|",
    ]
    for name, c in u["categories"].items():
        notes = c.get("reason") or (
            f"{len(c['findings'])} hazard finding(s)"
            if c.get("findings")
            else "; ".join(c.get("checked", [])[:1])
        )
        notes = str(notes).replace("|", "\\|")
        title = SECTION_TITLES.get(name, name)
        out.append(f"| {title} | {c['status']} | {c.get('confidence', '')} | {notes} |")
    out.append("")
    if u.get("remedies"):
        out += ["Remedies:", ""]
        out += [
            f"- **{r}**: " + ", ".join(f"`{i}`" for i in ids) for r, ids in u["remedies"].items()
        ]
        out.append("")
    if u.get("assumptions"):
        out += ["Assumptions:", ""] + [f"- {a}" for a in u["assumptions"]] + [""]
    if u.get("basis"):
        out += ["Basis:", ""] + [f"- {b}" for b in u["basis"]] + [""]
    return out
