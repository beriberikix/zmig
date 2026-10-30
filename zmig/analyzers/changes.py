"""Removed Kconfig symbols and compatibles the project uses, when the target build never
got that far.

The kconfig analyzer compares both builds' .config and symbol dumps, so it is skipped when
the target configure fails before Kconfig. The two Zephyr trees still say which symbols
were removed and what replaced them (zmig.changes, docs/DESIGN-CHANGE-DB.md); this
analyzer looks those facts up for what the source build shows the project uses:

  a symbol a fragment the build merged sets   symbol-removed            (kconfig's kind)
  a symbol the project's Kconfig re-declares  kconfig-override-orphaned (buildlog's kind)
  a symbol the project's code reads           read-symbol-removed       (kconfig's kind)
  a symbol newly deprecated that the source   symbol-deprecated         (kconfig's kind)
  build enables, assigned in a fragment or
  referenced in the project's own Kconfig

Likewise the devicetree analyzer's `compatible-unbound`, when the target has no
devicetree: a compatible the app or the project's own DTS uses (zmig.usage, project_dts),
bound by a binding at the source, whose binding the Zephyr tree no longer has.

The database covers Zephyr's tree only. A symbol or compatible that a module of the target
workspace defines (it moved into a module) isn't removed, and isn't reported
(DESIGN-CHANGE-DB §2.1, `module_definitions`).

Same category, kind and subject as those analyzers, so a finding has the same ID whether
or not the target configured. Each part runs only when the target lacks the artifact its
analyzer needs, and skips a subject an earlier analyzer already reported. Kconfig
candidates are ranked by the conditions the source build satisfies (zmig.changes.choose).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact, project_path, project_roots
from zmig.changes import (
    _COMPAT_GREP,
    _DEF,
    _DEF_GREP,
    COMPAT,
    choose,
    kconfig_references,
    load_or_build,
    parse_table,
)
from zmig.findings import JSON, Category, Confidence, Finding, Severity, file_line, tool_output
from zmig.scan import ScanContext
from zmig.usage import Loc, dt_token


def module_definitions(ctx: ScanContext) -> tuple[set[str], set[str]]:
    """(Kconfig symbols, binding compatibles) the modules of the target workspace define:
    Zephyr's west projects, not the project's own extra modules (whose re-declarations,
    ardep A1, are what's being reported). One `git grep` per module, cached per scan."""
    if "module_definitions" not in ctx.shared:
        symbols: set[str] = set()
        compats: set[str] = set()
        for m in ctx.new.workspace.modules(ctx.runner):
            for pattern, grep, paths, into in (
                (_DEF, _DEF_GREP, ["*Kconfig*"], symbols),
                (COMPAT, _COMPAT_GREP, [":(glob)**/dts/bindings/**/*.yaml"], compats),
            ):
                proc = ctx.runner.run(
                    ["git", "-C", str(m.path), "grep", "-n", "-E", grep, "HEAD", "--", *paths],
                    check=False,
                )
                into.update(parse_table(str(proc.stdout), pattern))
        ctx.shared["module_definitions"] = (symbols, compats)
    found: tuple[set[str], set[str]] = ctx.shared["module_definitions"]
    return found


def database(ctx: ScanContext) -> dict[str, Any]:
    mirror = ctx.new.workspace.resolved.mirror
    return load_or_build(
        mirror,
        ctx.runner,
        mirror.cache_root,
        ctx.old.workspace.resolved.ref.sha,
        ctx.new.workspace.resolved.ref.sha,
    )


class ChangesAnalyzer:
    name: ClassVar[str] = "changes"
    kind: ClassVar[Literal["build"]] = "build"
    # Only the source build is needed; the point is what the target build lacks.
    requires: ClassVar[frozenset[Artifact]] = frozenset()

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        # The kconfig and devicetree analyzers cover what the target build generated.
        # A .config without build.ninja is a configure that failed after Kconfig: the
        # kconfig analyzer can't snapshot it (DOGFOOD-12 E1).
        need_kconfig = not (
            ctx.new_img.has(Artifact.CONFIG) and ctx.new_img.has(Artifact.BUILD_NINJA)
        )
        need_dts = not ctx.new_img.has(Artifact.EDT)
        if not ctx.is_app or not (need_kconfig or need_dts):
            return AnalyzerResult([])
        kconfig = need_kconfig and ctx.old_img.has(Artifact.CONFIG)
        dts = need_dts and ctx.old_img.has(Artifact.EDT)
        if not (kconfig or dts):
            what = ".config" if need_kconfig else "devicetree"
            return AnalyzerResult.skipped(f"the source build has no {what}")
        db = database(ctx)
        reported = {(f.kind, f.subject) for f in ctx.prior}
        in_modules, compats_in_modules = module_definitions(ctx)
        findings: list[Finding] = []
        if kconfig:
            removed = {s: v for s, v in db["kconfig"]["removed"].items() if s not in in_modules}
            findings += self._kconfig(ctx, removed, reported)
            findings += self._deprecated(ctx, db["kconfig"].get("deprecated", {}), reported)
        if dts:
            gone = {
                c: v for c, v in db["devicetree"]["removed"].items() if c not in compats_in_modules
            }
            for token, locs in sorted(ctx.usage.dt_compats.items()):
                compat = bound_compatible(ctx.old_edt, token)
                if compat and compat in gone and ("compatible-unbound", token) not in reported:
                    lexical = ("changes.lexical",)
                    findings.append(
                        unbound_compatible(ctx, token, compat, locs, gone, self.name, lexical)
                    )
        return AnalyzerResult(findings)

    def _deprecated(
        self, ctx: ScanContext, deprecated: dict[str, Any], reported: set[tuple[str, str]]
    ) -> list[Finding]:
        """Newly deprecated symbols the source build enables, where the project assigns or
        references them (ardep A2: `CONFIG_STD_C11=y` in prj.conf, `default STD_C11` in
        the board's Kconfig.uds)."""
        enabled = {s for s in deprecated if ctx.old_config.get(s) == "y"}
        if not enabled:
            return []
        refs = project_kconfig_references(ctx, enabled)
        findings = []
        for sym in sorted(enabled):
            if ("symbol-deprecated", f"CONFIG_{sym}") in reported:
                continue
            ev: list[JSON] = [
                file_line(loc.path, loc.line, loc.text)
                for loc, _ in ctx.usage.kconfig_set.get(sym, [])
            ]
            ev += [file_line(path, line, text) for path, line, text in refs.get(sym, [])]
            if not ev:
                continue
            entry = deprecated[sym]
            facts = f"{entry['defined_at']} selects DEPRECATED at the target, not at the source"
            ev.append(tool_output("Kconfig trees at both refs", ctx.runner.redact(facts)))
            instead = entry.get("instead") or []
            findings.append(
                Finding(
                    category=Category.KCONFIG,
                    kind="symbol-deprecated",
                    subject=f"CONFIG_{sym}",
                    title=f"CONFIG_{sym} is deprecated in {ctx.new_spec}",
                    severity=Severity.RISKY,
                    confidence=Confidence.HIGH,
                    action="Move off it before the release that removes it."
                    + (f" Its help text names CONFIG_{instead[0]}." if instead else "")
                    + " (The target configure didn't reach Kconfig; this comes from the "
                    "Kconfig trees at both refs: zmig changes.)",
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=tuple(ev),
                    old=ctx.old_config.get(sym),
                    new={
                        "candidates": [
                            {
                                "symbol": n,
                                "why": "the deprecated symbol's help text names it",
                                "confidence": "medium",
                            }
                            for n in instead
                        ]
                    }
                    if instead
                    else None,
                    heuristics=("changes.lexical",),
                )
            )
        return findings

    def _kconfig(
        self, ctx: ScanContext, removed: dict[str, Any], reported: set[tuple[str, str]]
    ) -> list[Finding]:
        ok, ov = ctx.old_kconfig, ctx.old_config
        findings: list[Finding] = []
        handled: set[str] = set()

        def add(cat: Category, kind: str, sym: str, title: str, sev: Severity,
                ev: tuple[JSON, ...], cands: list[dict[str, Any]], what: str) -> None:  # fmt: skip
            handled.add(sym)
            if (kind, f"CONFIG_{sym}") in reported:
                return
            best = cands[0] if cands else None
            if best is None:
                action = "Find its replacement in the migration guides, or remove it."
            else:
                action = f"{what} CONFIG_{best['symbol']} ({_why(best)})."
                if len(cands) > 1:
                    others = ", ".join("CONFIG_" + c["symbol"] for c in cands[1:])
                    action += f" Other candidates: {others}."
            action += (
                " (The target configure didn't reach Kconfig; this comes from the Kconfig "
                "trees at both refs: zmig changes.)"
            )
            facts = (
                f"CONFIG_{sym} is defined at {removed[sym]['defined_at']} at the source and "
                "nowhere at the target"
            )
            payload = [
                {"symbol": c["symbol"], "why": _why(c), "confidence": c["confidence"]}
                for c in cands
            ]
            findings.append(
                Finding(
                    category=cat,
                    kind=kind,
                    subject=f"CONFIG_{sym}",
                    title=title,
                    severity=sev,
                    confidence=Confidence.HIGH,
                    action=action,
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=(
                        *ev,
                        tool_output("Kconfig trees at both refs", ctx.runner.redact(facts)),
                    ),
                    old=ov.get(sym),
                    new={"candidates": payload} if payload else None,
                    heuristics=("changes.lexical",),
                )
            )

        for sym, assigns in sorted(ctx.usage.kconfig_set.items()):
            if sym not in removed or sym not in ok:
                continue
            last_loc, _ = assigns[-1]
            ev = tuple(file_line(loc.path, loc.line, loc.text) for loc, _ in assigns)
            title = f"CONFIG_{sym}, set in {last_loc.path}, no longer exists in {ctx.new_spec}"
            cands = choose(removed[sym]["candidates"], ov)
            add(Category.KCONFIG, "symbol-removed", sym, title, Severity.BREAKING, ev, cands,
                "Replace it with")  # fmt: skip

        roots = project_roots(ctx.old, ctx.old_img.dir)
        for sym, info in sorted(ok.items()):
            if sym in handled or sym not in removed:
                continue
            own = []
            for loc in info.get("locations", []):
                path, _, line = loc.rpartition(":")
                full = os.path.normpath(os.path.join(ctx.old.workspace.zephyr, path))
                if (rel := project_path(full, roots)) and line.isdigit():
                    own.append(file_line(rel, int(line), f"config {sym}"))
            if own:
                title = f"Your Kconfig re-declares CONFIG_{sym}, gone in {ctx.new_spec}"
                cands = choose(removed[sym]["candidates"], ov)
                add(Category.BUILD, "kconfig-override-orphaned", sym, title, Severity.BREAKING,
                    tuple(own), cands, "Rename the override to")  # fmt: skip

        for sym, locs in sorted(ctx.usage.kconfig_read.items()):
            if sym in handled or sym not in removed or sym not in ok:
                continue
            title = f"Your code reads CONFIG_{sym}, which no longer exists in {ctx.new_spec}"
            ev = tuple(file_line(loc.path, loc.line, loc.text) for loc in locs)
            cands = choose(removed[sym]["candidates"], ov)
            add(Category.KCONFIG, "read-symbol-removed", sym, title, Severity.BREAKING, ev, cands,
                "#ifdef/IS_ENABLED() on it is now always false; use")  # fmt: skip
        return findings


def project_kconfig_references(
    ctx: ScanContext, symbols: set[str]
) -> dict[str, list[tuple[str, int, str]]]:
    """{symbol: [(path shown, line, text)]} of references to `symbols` in the project's own
    Kconfig files that the source build parsed (zephyr/kconfig/sources.txt): the app's and
    the project's modules', not Zephyr's."""
    sources = ctx.old_img.dir / "zephyr" / "kconfig" / "sources.txt"
    if not sources.is_file():
        return {}
    roots = project_roots(ctx.old, ctx.old_img.dir)
    out: dict[str, list[tuple[str, int, str]]] = {}
    for raw in dict.fromkeys(sources.read_text().split()):
        path = os.path.normpath(raw)
        shown = project_path(path, roots)
        if shown is None:
            continue
        try:
            text = Path(path).read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for line, sym, code in kconfig_references(text, symbols):
            out.setdefault(sym, []).append((shown, line, code))
    return out


def bound_compatible(edt: dict[str, Any], token: str) -> str | None:
    """The compatible, spelled as written, through which a binding matches a node in
    `edt` for this dt_token, or None if no node is bound through it."""
    return next(
        (
            n["matching_compat"]
            for n in edt["nodes"].values()
            if n["matching_compat"] and dt_token(n["matching_compat"]) == token
        ),
        None,
    )


def unbound_compatible(
    ctx: ScanContext,
    token: str,
    compat: str,
    locs: list[Loc],
    removed: dict[str, Any],
    analyzer: str,
    heuristics: tuple[str, ...] = (),
) -> Finding:
    """The devicetree analyzer's `compatible-unbound`, with the binding's replacement when
    the change database names one (`removed`: its devicetree part)."""
    cands = removed.get(compat, {}).get("candidates", [])
    payload = [
        {"symbol": c["symbol"], "why": _why(c),
         "confidence": "medium" if len(cands) == 1 and c["how"] != "same commit" else "low"}
        for c in cands
    ]  # fmt: skip
    action = "The compatible was renamed or its binding removed; check the migration guide."
    if payload:
        action = f"Use {payload[0]['symbol']} ({payload[0]['why']})."
        if len(payload) > 1:
            action += f" Other candidates: {', '.join(c['symbol'] for c in payload[1:])}."
    evidence: tuple[JSON, ...] = tuple(file_line(loc.path, loc.line, loc.text) for loc in locs)
    if compat in removed:
        facts = (
            f"{compat} is bound by {removed[compat]['defined_at']} at the source; no binding "
            "in the Zephyr tree has it at the target"
        )
        evidence += (tool_output("Bindings at both refs", ctx.runner.redact(facts)),)
    return Finding(
        category=Category.DEVICETREE,
        kind="compatible-unbound",
        subject=token,
        title=f"No node matches compatible {token} through a binding in {ctx.new_spec}",
        severity=Severity.BREAKING,
        confidence=Confidence.MEDIUM,
        action=action,
        analyzer=analyzer,
        scope=ctx.scope,
        evidence=evidence,
        old=compat,
        new={"candidates": payload} if payload else None,
        heuristics=("devicetree.compat-by-matching-node", *heuristics),
    )


def _why(c: dict[str, Any]) -> str:
    how = {
        "same hunk": "renamed in",
        "same commit": "added by the same commit,",
        "help text": "named by",
    }
    src = (
        f"{how.get(c['how'], c['how'])} {c['commit'][:12]}"
        if c.get("commit")
        else "named by its help text"
    )
    if c["how"] == "help text":
        src = "the deprecated symbol's help text names it"
    via = f", via {' -> '.join(c['via'])}" if c.get("via") else ""
    cond = f"; defined if {' && '.join(c['conditions'])}" if c.get("conditions") else ""
    return f"{src}{via}{cond}"
