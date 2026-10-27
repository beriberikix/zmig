"""Resolved Kconfig diff, scoped to the symbols the app sets and reads.

Sources, all from the two builds: the final .config of each, each tree's symbol
metadata via its own kconfiglib, and the Kconfig diagnostics Zephyr printed when the
target configure rejected the app's assignments.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact
from zmig.diagnostics import Diagnostic
from zmig.findings import (
    JSON,
    Category,
    Confidence,
    Finding,
    Severity,
    artifact_diff,
    file_line,
    plural,
    tool_output,
)
from zmig.scan import ScanContext
from zmig.usage import Loc

Symbols = dict[str, dict[str, Any]]


def norm_value(v: str | None) -> str | None:
    """Comparable form of a Kconfig value: ints and hex compared numerically."""
    if v is None:
        return None
    v = v.strip()
    if re.fullmatch(r"-?\d+|0[xX][0-9a-fA-F]+", v):
        return str(int(v, 0))
    return v


def _strip_deprecated(prompt: str | None) -> str:
    return re.sub(r"^\s*\[DEPRECATED\]\s*", "", prompt or "", flags=re.I).strip().lower()


def rename_candidates(sym: str, old: Symbols, new: Symbols) -> list[tuple[str, str, Confidence]]:
    """Likely replacements for a symbol that exists in `old` but not `new`."""
    o = old[sym]
    out: dict[str, tuple[str, Confidence]] = {}
    if o.get("deprecated"):
        for s in o.get("selects", []):
            if s != "DEPRECATED" and s in new:
                out.setdefault(s, (f"the deprecated {sym} selected {s}", Confidence.MEDIUM))
    word = re.compile(rf"\b(CONFIG_)?{re.escape(sym)}\b")
    prompt = _strip_deprecated(o.get("prompt"))
    for name, info in new.items():
        if name in old or name in out:
            continue
        if word.search(info.get("help") or ""):
            out[name] = (f"the help text of {name} mentions {sym}", Confidence.MEDIUM)
        elif prompt and _strip_deprecated(info.get("prompt")) == prompt:
            out[name] = (f"{name} has the same prompt", Confidence.LOW)
    return sorted(((n, why, c) for n, (why, c) in out.items()), key=lambda x: x[0])


def guide_renames(ctx: ScanContext, sym: str, new: Symbols) -> list[tuple[str, str, Confidence]]:
    """Replacements the migration guides name for a removed symbol ("X was renamed to Y"),
    if Y exists at the target."""
    out = []
    for _, entry in ctx.guide_entries:
        for r in entry.renames:
            if r.kind == "kconfig" and r.old == sym and r.new in new:
                out.append(
                    (
                        r.new,
                        f"the {entry.version} migration guide says so ({entry.id})",
                        Confidence.MEDIUM,
                    )
                )
    return list({c[0]: c for c in out}.values())


def _locs(locs: list[Loc]) -> tuple[JSON, ...]:
    return tuple(file_line(loc.path, loc.line, loc.text) for loc in locs)


class KconfigAnalyzer:
    name: ClassVar[str] = "kconfig"
    kind: ClassVar[Literal["build"]] = "build"
    requires: ClassVar[frozenset[Artifact]] = frozenset({Artifact.CONFIG})

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        ov, nv = ctx.old_config, ctx.new_config
        ok, nk = ctx.old_kconfig, ctx.new_kconfig
        diags: dict[str, Diagnostic] = {
            d.symbol: d for d in ctx.new_raw_facts.diagnostics if d.kind.startswith("kconfig")
        }
        findings: list[Finding] = []
        handled: set[str] = set()

        def add(
            kind: str,
            sym: str,
            title: str,
            sev: Severity,
            conf: Confidence,
            action: str,
            ev: tuple[JSON, ...],
            old: Any = None,
            new: Any = None,
            heuristics: tuple[str, ...] = (),
        ) -> None:
            findings.append(
                Finding(
                    category=Category.KCONFIG,
                    kind=kind,
                    subject=f"CONFIG_{sym}",
                    title=title,
                    severity=sev,
                    confidence=conf,
                    action=action,
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=ev,
                    old=old,
                    new=new,
                    heuristics=heuristics,
                )
            )

        def diff_ev(s: str) -> JSON:
            return artifact_diff("zephyr/.config", ctx.old_spec, ctx.new_spec, ov.get(s), nv.get(s))

        for sym in sorted(ctx.usage.kconfig_set):
            assigns = ctx.usage.kconfig_set[sym]
            if sym not in ok:
                continue  # already invalid at the source ref; not a migration issue
            handled.add(sym)
            last_loc, value = assigns[-1]
            ev = _locs([loc for loc, _ in assigns])
            d = diags.get(sym)
            dev = (tool_output("target configure", ctx.runner.redact(d.message)),) if d else ()
            if sym not in nk:
                cands = guide_renames(ctx, sym, nk) + [
                    c
                    for c in rename_candidates(sym, ok, nk)
                    if c[0] not in {g[0] for g in guide_renames(ctx, sym, nk)}
                ]
                if cands:
                    best = cands[0]
                    hint = f"Replace it with CONFIG_{best[0]} ({best[1]})." + (
                        f" Other candidates: {', '.join('CONFIG_' + c[0] for c in cands[1:])}."
                        if len(cands) > 1
                        else ""
                    )
                else:
                    hint = "Remove the assignment, or find its replacement in the migration guides."
                add(
                    "symbol-removed",
                    sym,
                    f"CONFIG_{sym}, set in {last_loc.path}, no longer exists in {ctx.new_spec}",
                    Severity.BREAKING,
                    Confidence.HIGH,
                    hint + (" Zephyr aborts the target configure on this assignment." if d else ""),
                    ev + dev,
                    old=ov.get(sym),
                    new=None
                    if not cands
                    else {
                        "candidates": [
                            {"symbol": c[0], "why": c[1], "confidence": str(c[2])} for c in cands
                        ]
                    },
                    heuristics=tuple(sorted({f"kconfig.rename.{c[2]}" for c in cands})),
                )
                continue
            if d is not None and d.kind == "kconfig-not-taken":
                add(
                    "value-not-taken",
                    sym,
                    f"CONFIG_{sym}={value} no longer takes effect in {ctx.new_spec} "
                    f"(it gets {d.got})",
                    Severity.BREAKING,
                    Confidence.HIGH,
                    f"New or changed dependencies are unmet: {d.deps or 'see the evidence'}. "
                    f"Enable them, or drop the assignment if the feature moved.",
                    ev + dev,
                    old=ov.get(sym),
                    new={"assigned": value, "got": d.got, "unsatisfied": d.deps},
                )
                continue
            if d is not None and d.kind == "kconfig-promptless":
                add(
                    "not-configurable",
                    sym,
                    f"CONFIG_{sym} can no longer be set directly in {ctx.new_spec}",
                    Severity.BREAKING,
                    Confidence.HIGH,
                    "It now gets its value from other symbols; enable those instead.",
                    ev + dev,
                )
                continue
            if norm_value(value) == norm_value(ov.get(sym)) and norm_value(value) != norm_value(
                nv.get(sym, "n")
            ):
                add(
                    "value-not-taken",
                    sym,
                    f"CONFIG_{sym}={value} took effect in {ctx.old_spec} but not in "
                    f"{ctx.new_spec} (it is {nv.get(sym, 'n')})",
                    Severity.BREAKING,
                    Confidence.HIGH,
                    f"Check the dependencies of CONFIG_{sym}: {nk[sym]['depends']}.",
                    (*ev, diff_ev(sym)),
                    old=ov.get(sym),
                    new=nv.get(sym),
                )
            if nk[sym].get("deprecated") and not ok[sym].get("deprecated"):
                add(
                    "symbol-deprecated",
                    sym,
                    f"CONFIG_{sym} is deprecated in {ctx.new_spec}",
                    Severity.RISKY,
                    Confidence.HIGH,
                    "Move off it before the release that removes it.",
                    ev,
                )
            elif nk[sym].get("deprecated"):
                add(
                    "symbol-deprecated",
                    sym,
                    f"CONFIG_{sym} is still deprecated in {ctx.new_spec}",
                    Severity.RISKY,
                    Confidence.HIGH,
                    "It was already deprecated in the source version; removal is likely soon.",
                    ev,
                )

        for sym in sorted(ctx.usage.kconfig_read):
            if sym in handled or sym not in ok:
                continue
            handled.add(sym)
            locs = _locs(ctx.usage.kconfig_read[sym])
            if sym not in nk:
                add(
                    "read-symbol-removed",
                    sym,
                    f"Your code reads CONFIG_{sym}, which no longer exists in {ctx.new_spec}",
                    Severity.BREAKING,
                    Confidence.HIGH,
                    "#ifdef/IS_ENABLED() on it is now always false. Use its replacement "
                    "(see the migration guides).",
                    locs,
                    old=ov.get(sym),
                )
            elif ov.get(sym) != nv.get(sym):
                add(
                    "read-value-changed",
                    sym,
                    f"CONFIG_{sym}, which your code reads, changed from {ov.get(sym, 'n')} "
                    f"to {nv.get(sym, 'n')}",
                    Severity.RISKY,
                    Confidence.HIGH,
                    "Check the code paths that depend on it.",
                    (*locs, diff_ev(sym)),
                    old=ov.get(sym),
                    new=nv.get(sym),
                )

        others: dict[str, dict[str, Any]] = {
            "default-changed": {
                s: [ov[s], nv[s]]
                for s in sorted(ov.keys() & nv.keys())
                if ov[s] != nv[s] and s not in handled
            },
            "newly-set": {
                s: nv[s] for s in sorted(nv.keys() - ov.keys()) if nv[s] != "n" and s not in handled
            },
            "no-longer-set": {
                s: ov[s] for s in sorted(ov.keys() - nv.keys()) if ov[s] != "n" and s not in handled
            },
        }
        titles = {
            "default-changed": "other symbols resolve to a different value",
            "newly-set": "symbols are newly enabled or set",
            "no-longer-set": "symbols set before are absent",
        }
        for kind, items in others.items():
            if items:
                add(
                    kind,
                    f"{len(items)} symbols",
                    f"{plural(len(items), 'Kconfig symbol')}: {titles[kind]} in {ctx.new_spec}",
                    Severity.INFO,
                    Confidence.HIGH,
                    "Not set or read by your app; review the list for size or behavior "
                    "changes you care about (stack sizes, heap, logging).",
                    (),
                    new=items,
                )
        if ctx.salvage:
            findings.append(
                Finding(
                    category=Category.KCONFIG,
                    kind="salvaged",
                    subject="target-configure",
                    title=f"The target configure failed; zmig disabled "
                    f"{plural(len(ctx.salvage), 'line')} in a copy of your app to compare "
                    f"the rest",
                    severity=Severity.INFO,
                    confidence=Confidence.HIGH,
                    action="Everything else in this report compares against that salvaged "
                "build, so values that depended on the disabled lines (symbols they "
                "selected, for example) differ in it too. Fix the breaking findings and "
                "rerun.",
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=tuple(file_line(e.file, e.line, e.text) for e in ctx.salvage),
                    new=[e.to_json() for e in ctx.salvage],
                )
            )
        return AnalyzerResult(findings)
