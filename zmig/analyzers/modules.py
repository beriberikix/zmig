"""The Zephyr modules the build needs: modules the target build turned out to require that
the scan's module set lacked (zmig.modulecheck, diagnosed by the scan before analyzers
run), and how the modules the source build used changed in Zephyr's manifest."""

from __future__ import annotations

import os
from typing import ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact
from zmig.findings import (
    JSON,
    Category,
    Checked,
    Confidence,
    Finding,
    Severity,
    file_line,
    tool_output,
)
from zmig.manifest import load_manifest
from zmig.modulecheck import manifest_changes, user_manifest
from zmig.scan import ScanContext


class ModulesAnalyzer:
    name: ClassVar[str] = "modules"
    kind: ClassVar[Literal["build"]] = "build"
    requires: ClassVar[frozenset[Artifact]] = frozenset()
    per_image: ClassVar[bool] = False

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        findings: list[Finding] = []

        def add(kind: str, subject: str, title: str, sev: Severity, action: str,
                ev: tuple[JSON, ...], old: object = None, new: object = None,
                introduced: bool = True) -> None:  # fmt: skip
            findings.append(
                Finding(
                    category=Category.MANIFEST,
                    kind=kind,
                    subject=subject,
                    title=title,
                    severity=sev,
                    confidence=Confidence.HIGH,
                    action=action,
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=ev,
                    old=old,
                    new=new,
                    introduced_by_migration=introduced,
                )
            )

        zephyr = str(ctx.new.workspace.zephyr) + "/"
        for need in ctx.module_needs:
            added = need.module in ctx.modules_added
            # Needed at both refs: the scan's module set lacked it, the migration didn't
            # change anything (AUDIT.md §5.2 run 1; docs/DESIGN-P1.md §2.3).
            at_source = need.module in ctx.modules_at_source
            site = need.site.replace(zephyr, "zephyr/")
            ev: list[JSON] = [
                tool_output(
                    "target build",
                    ctx.runner.redact(f"{site}: {need.why}" if site else need.why),
                )
            ]
            if need.declared:
                ev.append(
                    tool_output("target manifest", f"zephyr/{need.declared} declares {need.module}")
                )
            app = ctx.old.spec.app.resolve()
            mine = user_manifest(app)
            in_manifest = ""
            if mine is not None:
                path, line, what = mine
                rel = os.path.relpath(path, app).replace(os.sep, "/")
                text = path.read_text(errors="replace").splitlines()[line - 1].strip()
                ev.insert(0, file_line(rel, line, text))
                in_manifest = f" in {rel}:{line} ({what})"
            add(
                "module-required",
                need.module,
                f"The target build needs module {need.module}, which the build's module set "
                f"doesn't include" + (f"; already required at {ctx.old_spec}" if at_source else ""),
                Severity.INFO if at_source else Severity.BREAKING,
                (
                    f"The source build failed without {need.module} too, so zmig added it at "
                    f"both versions. If your workspace has it, pass --module {need.module}. "
                    if at_source
                    else ""
                )
                + (
                    f"Add {need.module} to your manifest{in_manifest} and run `west update`."
                    if in_manifest
                    else f"Add {need.module} to your workspace (the name-allowlist or project "
                    f"list that imports Zephyr) and run `west update`; no west manifest was "
                    f"found above the app."
                )
                + (
                    " zmig added it to analyze the target; the rest of this report "
                    "compares against that build."
                    if added
                    else ""
                ),
                tuple(ev),
                new={
                    "module": need.module,
                    "why": need.why,
                    "site": ctx.runner.redact(site),
                    "declared": need.declared,
                    "alternatives": list(need.alternatives),
                    "added_by_zmig": added,
                    "required_at_source": at_source,
                },
                introduced=not at_source,
            )

        used = [m.name for m in ctx.old.workspace.modules(ctx.runner)]
        changes = manifest_changes(
            used,
            load_manifest(ctx.old.workspace.root),
            load_manifest(ctx.new.workspace.root),
            ctx.old.workspace.zephyr,
            ctx.new.workspace.zephyr,
        )
        sbom_covers = ctx.old_img.has(Artifact.SPDX) and ctx.new_img.has(Artifact.SPDX)
        for c in changes:
            where = (tool_output("manifest", f"zephyr/{c.declared}"),) if c.declared else ()
            if c.kind == "removed":
                add(
                    "module-removed",
                    c.module,
                    f"Module {c.module}, which the source build uses, is no longer in Zephyr's "
                    f"manifest at {ctx.new_spec}",
                    Severity.BREAKING,
                    "Find what replaced it in the migration guides, or keep it in your own "
                    "manifest at a revision that still works.",
                    where,
                    old=c.old,
                )
            elif not sbom_covers:
                # The SBOM analyzer reports re-pins (with the compiled files that changed)
                # whenever both builds have an SBOM; only fill in when they don't.
                add(
                    "module-repinned",
                    c.module,
                    f"Module {c.module}, which the build uses, moves from {c.old[:12]} to "
                    f"{c.new[:12]}",
                    Severity.INFO,
                    "The SBOM findings list what changed in the files the build compiles.",
                    where,
                    old=c.old,
                    new=c.new,
                )
        needs = ", ".join(n.module for n in ctx.module_needs)
        removed = [c.module for c in changes if c.kind == "removed"]
        detail = (
            f"{len(used)} module(s) the source build used compared in both Zephyr manifests "
            f"({len(changes)} changed); "
            + (
                f"the target build needed {needs}"
                if needs
                else "the target build needed no other module"
            )
        )
        checked = Checked("module set", "found" if needs or removed else "clean", detail, self.name)
        return AnalyzerResult(findings, checked=[checked])
