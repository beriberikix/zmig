"""Removed Kconfig symbols the project uses, when the target never reached Kconfig.

The kconfig analyzer compares both builds' .config and symbol dumps, so it is skipped when
the target configure fails before Kconfig. The two Zephyr trees still say which symbols
were removed and what replaced them (zmig.changes, docs/DESIGN-CHANGE-DB.md); this
analyzer looks those facts up for what the source build shows the project uses:

  a symbol a fragment the build merged sets   symbol-removed            (kconfig's kind)
  a symbol the project's Kconfig re-declares  kconfig-override-orphaned (buildlog's kind)
  a symbol the project's code reads           read-symbol-removed       (kconfig's kind)

Same category, kind and subject as those analyzers, so a finding has the same ID whether
or not the target configured. It runs only when the target has no .config, and skips a
subject an earlier analyzer already reported. Candidates are ranked by the conditions the
source build satisfies (zmig.changes.choose).
"""

from __future__ import annotations

import os
from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact, project_path, project_roots
from zmig.changes import choose, load_or_build
from zmig.findings import JSON, Category, Confidence, Finding, Severity, file_line, tool_output
from zmig.scan import ScanContext


def _database(ctx: ScanContext) -> dict[str, Any]:
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
    # Only the source build is needed; the point is the target's missing .config.
    requires: ClassVar[frozenset[Artifact]] = frozenset()

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        if not ctx.is_app or ctx.new_img.has(Artifact.CONFIG):
            return AnalyzerResult([])  # the kconfig analyzer covers a configured target
        if not ctx.old_img.has(Artifact.CONFIG):
            return AnalyzerResult.skipped("the source build has no .config")
        removed: dict[str, Any] = _database(ctx)["kconfig"]["removed"]
        reported = {(f.kind, f.subject) for f in ctx.prior}
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
        return AnalyzerResult(findings)


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
