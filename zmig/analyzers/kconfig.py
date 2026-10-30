"""Resolved Kconfig diff, scoped to the symbols the app sets and reads.

Sources, all from the two builds: the final .config of each, each tree's symbol
metadata via its own kconfiglib, and the Kconfig diagnostics Zephyr printed when the
target configure rejected the app's assignments.
"""

from __future__ import annotations

import os
import re
from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact, project_path, project_roots
from zmig.diagnostics import Diagnostic, app_relative, preexisting
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


_USE_INSTEAD = re.compile(
    r"\b(?:use|in favou?r of|replaced (?:by|with)|superseded by)\s+(?:the\s+)?(?:CONFIG_)?"
    r"([A-Z][A-Z0-9_]{2,})"
)


def deprecation_replacements(
    sym: str, info: dict[str, Any], syms: Symbols
) -> list[tuple[str, str, Confidence]]:
    """Replacements a deprecated symbol's own help text names ("Deprecated, use
    UART_NATIVE_PTY_0_ON_STDINOUT instead."), if they exist in `syms`. Zephyr's deprecated
    choice members select nothing, so this is the Kconfig-level evidence they carry."""
    if not info.get("deprecated"):
        return []
    found = dict.fromkeys(m[1] for m in _USE_INSTEAD.finditer(info.get("help") or ""))
    return [
        (n, f"the deprecated {sym}'s help text says to use {n}", Confidence.MEDIUM)
        for n in found
        if n != sym and n in syms
    ]


def rename_candidates(sym: str, old: Symbols, new: Symbols) -> list[tuple[str, str, Confidence]]:
    """Likely replacements for a symbol that exists in `old` but not `new`."""
    o = old[sym]
    out: dict[str, tuple[str, Confidence]] = {}
    if o.get("deprecated"):
        for s in o.get("selects", []):
            if s != "DEPRECATED" and s in new:
                out.setdefault(s, (f"the deprecated {sym} selected {s}", Confidence.MEDIUM))
    for name, why, conf in deprecation_replacements(sym, o, new):
        out.setdefault(name, (why, conf))
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


_SYMBOL = re.compile(r"\b[A-Z_][A-Z0-9_]*\b")


def references(info: dict[str, Any], known: Symbols) -> dict[str, str]:
    """Symbols one symbol's definition leans on, with how: what it depends on, selects or
    implies, and what its defaults name (values and conditions)."""
    out: dict[str, str] = {}
    for s in _SYMBOL.findall(info.get("depends") or ""):
        out.setdefault(s, "depends on")
    for s in info.get("selects", []):
        out.setdefault(s, "selects")
    for s in info.get("implies", []):
        out.setdefault(s, "implies")
    for d in info.get("defaults", []):
        for s in _SYMBOL.findall(d):
            out.setdefault(s, "takes its default from")
    return {s: how for s, how in out.items() if s in known}


EXPRESSIONS = ("depends", "selects", "implies")


def expression_changes(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[Any]]:
    """{"depends"|"selects"|"implies": [old, new]} for those that differ between one
    symbol's definitions at the two refs."""
    return {k: [old.get(k), new.get(k)] for k in EXPRESSIONS if old.get(k) != new.get(k)}


def expression_note(old: Symbols, new: Symbols, sym: str, spec: str) -> str:
    """ " Its definition changed in <spec>: ..." for a finding's action, or "" (AUDIT.md
    §6 item 5: the value findings say why)."""
    if sym not in old or sym not in new:
        return ""
    ch = expression_changes(old[sym], new[sym])
    if not ch:
        return ""

    def show(v: Any) -> str:
        return (", ".join(v) if isinstance(v, list) else v) or "nothing"

    how = {"depends": "depends on", "selects": "selects", "implies": "implies"}
    return (
        f" Its definition changed in {spec}: "
        + "; ".join(f"{how[k]} {show(o)} -> {show(n)}" for k, (o, n) in ch.items())
        + "."
    )


def dependency_chains(
    roots: dict[str, Any], changed: set[str], old: Symbols
) -> dict[str, list[tuple[str, str, str]]]:
    """For each symbol in `changed` that an app root leans on, the shortest chain of
    (symbol, how, next symbol) links from a root to it. One hop, or two when the
    intermediate symbol changed too (so the change visibly propagates through it); deeper
    chains reach the SoC and arch symbols every config depends on."""
    out: dict[str, list[tuple[str, str, str]]] = {}
    for root in sorted(roots):
        if root not in old:
            continue
        first = references(old[root], old)
        for mid, how in sorted(first.items()):
            if mid in changed and mid != root:
                out.setdefault(mid, [(root, how, mid)])
        for mid, how in sorted(first.items()):
            if mid not in changed or mid == root:
                continue
            for x, how2 in sorted(references(old[mid], old).items()):
                if x in changed and x not in (root, mid) and x not in out:
                    out[x] = [(root, how, mid), (mid, how2, x)]
    return out


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
        source_diags = ctx.old_facts.diagnostics
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
            introduced: bool = True,
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
                    introduced_by_migration=introduced,
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
            # The source configure printing the same diagnostic makes it a condition of
            # the app, not of the migration (docs/DESIGN-P1.md §2.3).
            pre = d is not None and preexisting(d, source_diags)
            if d is not None and d.kind == "kconfig-not-taken" and pre:
                # Never took effect, so nothing the app relies on changes: info, unlike
                # §2.3's pre-existing defects (AUDIT.md F1, the settings sample's
                # CONFIG_STDOUT_CONSOLE on native_sim).
                add(
                    "value-not-taken",
                    sym,
                    f"CONFIG_{sym}={value} has no effect (it gets {d.got}); already not taken "
                    f"at {ctx.old_spec}",
                    Severity.INFO,
                    Confidence.HIGH,
                    f"The source configure printed the same warning, so the migration doesn't "
                    f"change it. Drop the assignment, or meet its dependencies: "
                    f"{d.deps or 'see the evidence'}.",
                    ev + dev,
                    old=ov.get(sym),
                    new={"assigned": value, "got": d.got, "unsatisfied": d.deps},
                    heuristics=("kconfig.preexisting-diagnostic",),
                    introduced=False,
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
                    f"Enable them, or drop the assignment if the feature moved."
                    + expression_note(ok, nk, sym, ctx.new_spec),
                    ev + dev,
                    old=ov.get(sym),
                    new={"assigned": value, "got": d.got, "unsatisfied": d.deps},
                )
                continue
            if d is not None and d.kind == "kconfig-promptless":
                add(
                    "not-configurable",
                    sym,
                    f"CONFIG_{sym} can no longer be set directly in {ctx.new_spec}"
                    if not pre
                    else f"CONFIG_{sym} can't be set directly (pre-existing: the source "
                    f"configure printed the same error)",
                    Severity.BREAKING,
                    Confidence.HIGH,
                    "It now gets its value from other symbols; enable those instead.",
                    ev + dev,
                    introduced=not pre,
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
                    f"Check the dependencies of CONFIG_{sym}: {nk[sym]['depends']}."
                    + expression_note(ok, nk, sym, ctx.new_spec),
                    (*ev, diff_ev(sym)),
                    old=ov.get(sym),
                    new=nv.get(sym),
                )
            repl = deprecation_replacements(sym, nk[sym], nk)
            instead = (
                {
                    "candidates": [
                        {"symbol": c[0], "why": c[1], "confidence": str(c[2])} for c in repl
                    ]
                }
                if repl
                else None
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
                    new=instead,
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
                    new=instead,
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
            elif (ov.get(sym) or "n") != (nv.get(sym) or "n"):
                # Unset and "n" read the same in code (regression: ZMK's
                # CONFIG_LV_USE_THEME_MONO was reported as "changed from n to n").
                add(
                    "read-value-changed",
                    sym,
                    f"CONFIG_{sym}, which your code reads, changed from {ov.get(sym, 'n')} "
                    f"to {nv.get(sym, 'n')}",
                    Severity.RISKY,
                    Confidence.HIGH,
                    "Check the code paths that depend on it."
                    + expression_note(ok, nk, sym, ctx.new_spec),
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
        self._promote_dependencies(ctx, others, add, diff_ev)
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
        findings += self._expression_changes(ctx)
        if ctx.salvage and ctx.is_app:
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

    @staticmethod
    def _expression_changes(ctx: ScanContext) -> list[Finding]:
        """One info finding for the depends/selects/implies changes of symbols this build's
        inputs set (applied fragments) or read (compiled files). Any that took effect in
        this build is also a value change, reported on its own; the rest matter for other
        boards and configurations (AUDIT.md §6 item 5: as risky, echo_server would get 18,
        mostly SoC lists and overlays the build doesn't apply)."""
        ok, nk = ctx.old_kconfig, ctx.new_kconfig
        applied = set(app_relative(ctx.old_facts.configs, ctx.old.src))
        built = ctx.build_files
        used: dict[str, list[Loc]] = {}
        for sym, assigns in ctx.usage.kconfig_set.items():
            if locs := [loc for loc, _ in assigns if loc.path in applied]:
                used[sym] = locs
        for sym, locs in ctx.usage.kconfig_read.items():
            if keep := [loc for loc in locs if built is None or loc.path in built]:
                used.setdefault(sym, keep)
        changes = {
            sym: ch
            for sym in sorted(used)
            if sym in ok and sym in nk and (ch := expression_changes(ok[sym], nk[sym]))
        }
        if not changes:
            return []
        return [
            Finding(
                category=Category.KCONFIG,
                kind="dependency-expression-changed",
                subject="dependency-expressions",
                title=f"{plural(len(changes), 'Kconfig symbol')} your build sets or reads "
                f"changed their depends/selects/implies in {ctx.new_spec}",
                severity=Severity.INFO,
                confidence=Confidence.HIGH,
                action="Value changes they caused in this build are reported as their own "
                "findings; the new expressions can matter for other boards or configurations.",
                analyzer=KconfigAnalyzer.name,
                scope=ctx.scope,
                evidence=_locs([used[s][0] for s in changes][:10]),
                new=changes,
            )
        ]

    @staticmethod
    def _promote_dependencies(
        ctx: ScanContext, others: dict[str, dict[str, Any]], add: Any, diff_ev: Any
    ) -> None:
        """Take the symbols the app's own configuration leans on out of the bulk lists and
        report each as risky, with the chain from the app's line to the changed symbol.

        Roots: the symbols set in a configuration fragment this build applied, and the
        symbols the project's own Kconfig files define or override (the app and the
        project's modules outside zmig's workspace)."""
        ok = ctx.old_kconfig
        applied = set(app_relative(ctx.old_facts.configs, ctx.old.src))
        roots: dict[str, tuple[JSON, ...]] = {}
        for sym, assigns in ctx.usage.kconfig_set.items():
            locs = [loc for loc, _ in assigns if loc.path in applied]
            if locs:
                roots[sym] = _locs(locs)
        proj = project_roots(ctx.old, ctx.old_img.dir)
        ov, nv = ctx.old_config, ctx.new_config
        for sym, info in ok.items():
            # Only definitions that take effect in this build: a shield's Kconfig is parsed
            # by every build of the app, but its symbols stay off without the shield.
            if ov.get(sym, "n") in ("n", ""):
                continue
            ev: list[JSON] = []
            for loc in info.get("locations", []):
                path, _, line = loc.rpartition(":")
                full = os.path.normpath(os.path.join(ctx.old.workspace.zephyr, path))
                if (own := project_path(full, proj)) and line.isdigit():
                    fl = file_line(own, int(line), f"config {sym}")
                    if fl not in ev:  # a Kconfig file sourced twice lists its node twice
                        ev.append(fl)
            if ev:
                roots.setdefault(sym, tuple(ev))
        changed = {s for items in others.values() for s in items}
        for x, chain in sorted(dependency_chains(roots, changed, ok).items()):
            root = chain[0][0]
            text = "; ".join(f"CONFIG_{a} {how} CONFIG_{b}" for a, how, b in chain)
            for items in others.values():
                items.pop(x, None)
            add(
                "dependency-changed",
                x,
                f"CONFIG_{x}, which your configuration of CONFIG_{root} relies on, changed "
                f"from {ov.get(x, 'unset')} to {nv.get(x, 'unset')} in {ctx.new_spec}",
                Severity.RISKY,
                Confidence.MEDIUM,
                f"{text}. Check that CONFIG_{root} still does what your app expects."
                + expression_note(ok, ctx.new_kconfig, root, ctx.new_spec),
                (*roots[root], tool_output("Kconfig at the source", text), diff_ev(x)),
                old=ov.get(x),
                new={"value": nv.get(x), "chain": [list(link) for link in chain]},
                heuristics=("kconfig.dependency-chain",),
            )
