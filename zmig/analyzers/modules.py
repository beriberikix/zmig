"""The Zephyr modules the build needs: modules the target build turned out to require that
the scan's module set lacked (zmig.modulecheck, diagnosed by the scan before analyzers
run), and how the modules the source build used changed in Zephyr's manifest.

From the Zephyr tree alone (the change database, zmig.modulecheck.tree_needs), modules
the target needs although the target build never got far enough to say so:

  missing from the scan's module set, target has no .config   module-required (same ID)
  in the scan's module set, missing from the user's allowlist module-not-in-manifest

(DESIGN.md, "Change database": ardep A6 tf-psa-crypto; RIT FSW F2 cmsis_6.)"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact, project_path, project_roots
from zmig.errors import ZmigError
from zmig.findings import (
    JSON,
    Category,
    Checked,
    Confidence,
    Finding,
    NotChecked,
    Severity,
    file_line,
    tool_output,
)
from zmig.manifest import load_manifest
from zmig.modulecheck import (
    ModuleNeed,
    manifest_changes,
    manifest_line,
    tree_needs,
    user_allowlist,
    user_manifest,
)
from zmig.scan import ScanContext
from zmig.snapshots import build_dependencies


class ModulesAnalyzer:
    name: ClassVar[str] = "modules"
    kind: ClassVar[Literal["build"]] = "build"
    requires: ClassVar[frozenset[Artifact]] = frozenset()
    per_image: ClassVar[bool] = False

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        findings: list[Finding] = []

        def add(kind: str, subject: str, title: str, sev: Severity, action: str,
                ev: tuple[JSON, ...], old: object = None, new: object = None,
                introduced: bool = True, conf: Confidence = Confidence.HIGH,
                heuristics: tuple[str, ...] = ()) -> None:  # fmt: skip
            findings.append(
                Finding(
                    category=Category.MANIFEST,
                    kind=kind,
                    subject=subject,
                    title=title,
                    severity=sev,
                    confidence=conf,
                    heuristics=heuristics,
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
        mine = _user_manifest(ctx)

        def required(
            need: ModuleNeed,
            source: str = "target build",
            conf: Confidence = Confidence.HIGH,
            heuristics: tuple[str, ...] = (),
        ) -> None:
            added = need.module in ctx.modules_added
            # Needed at both refs: the scan's module set lacked it, the migration didn't
            # change anything (eval/AUDIT.md §5.2 run 1; DESIGN.md, "Pre-existing problems").
            at_source = need.module in ctx.modules_at_source
            site = need.site.replace(zephyr, "zephyr/")
            ev: list[JSON] = [
                tool_output(
                    source,
                    ctx.runner.redact(f"{site}: {need.why}" if site else need.why),
                )
            ]
            if need.declared:
                ev.append(
                    tool_output("target manifest", f"zephyr/{need.declared} declares {need.module}")
                )
            in_manifest = ""
            if mine is not None:
                rel, line, what, text, _ = mine
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
                conf=conf,
                heuristics=heuristics,
            )

        for need in ctx.module_needs:
            required(need)

        new_manifest = load_manifest(ctx.new.workspace.root)
        have = {m.name for m in ctx.new.workspace.modules(ctx.runner)}
        diagnosed = {n.module for n in ctx.module_needs}
        allow = user_allowlist(mine[4]) if mine is not None else None
        old_manifest = load_manifest(ctx.old.workspace.root)
        tree, db_error = self._tree_needs(ctx, new_manifest, old_manifest)
        not_checked = (
            [
                NotChecked(
                    "change database",
                    f"modules the Zephyr tree says the target needs weren't checked: {db_error}",
                    self.name,
                )
            ]
            if db_error
            else []
        )
        for need in tree:
            if need.module in diagnosed:
                continue
            need = replace(need, declared=manifest_line(ctx.new.workspace.zephyr, need.module))
            if need.module not in have:
                # A target that configured without it didn't need it (or failed for a
                # reason the build itself names, diagnosed above). Configured means
                # build.ninja, not .config: proves' target wrote .config, then failed in
                # the Mbed TLS module for want of tf-psa-crypto (DOGFOOD-12 E1).
                if not ctx.new_img.has(Artifact.BUILD_NINJA):
                    required(need, "Zephyr tree at the target", Confidence.MEDIUM,
                             ("changes.lexical",))  # fmt: skip
            elif mine is not None and allow is not None and need.module not in allow:
                rel, line, what, text, _ = mine
                declared = (
                    [
                        tool_output(
                            "target manifest", f"zephyr/{need.declared} declares {need.module}"
                        )
                    ]
                    if need.declared
                    else []
                )
                add(
                    "module-not-in-manifest",
                    need.module,
                    f"The target needs module {need.module}, which your manifest's "
                    f"name-allowlist doesn't list",
                    Severity.BREAKING,
                    f"Add {need.module} to {rel}:{line} ({what}) and run `west update`. "
                    f"zmig was given it, so the target build had it; `west update` with "
                    f"your manifest won't fetch it.",
                    (
                        file_line(rel, line, text),
                        tool_output(
                            "Zephyr tree at the target",
                            ctx.runner.redact(f"{need.site}: {need.why}"),
                        ),
                        *declared,
                    ),
                    new={"module": need.module, "why": need.why, "site": need.site},
                    conf=Confidence.MEDIUM,
                    heuristics=("changes.lexical",),
                )

        used = [m.name for m in ctx.old.workspace.modules(ctx.runner)]
        changes = manifest_changes(
            used,
            old_manifest,
            new_manifest,
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
        return AnalyzerResult(findings, checked=[checked], not_checked=not_checked)

    @staticmethod
    def _tree_needs(
        ctx: ScanContext, new_manifest: dict[str, Any], old_manifest: dict[str, Any]
    ) -> tuple[list[ModuleNeed], str | None]:
        """Modules the Zephyr tree at the target says the source build's configuration
        needs; none without the source build's dependency records or the database (then
        with the database's error, reported under "not checked")."""
        from zmig.analyzers.changes import database

        if not ctx.old_img.has(Artifact.CONFIG):
            return [], None
        deps = build_dependencies(ctx.old_img, ctx.runner)
        modules = ctx.old_img.dir / Artifact.MODULES.value
        if deps is None or not modules.is_file():
            return [], None
        try:
            db = database(ctx).get("modules", {})
        except ZmigError as e:
            return [], str(e)
        paths = {}
        for ln in modules.read_text().splitlines():
            parts = [p.strip('"') for p in ln.split('":"')]
            if len(parts) >= 2:
                paths[parts[0]] = parts[1]
        used = {f for fs in deps.values() for f in fs}
        return tree_needs(db, ctx.old_config, used, paths, new_manifest, old_manifest), None


def _user_manifest(ctx: ScanContext) -> tuple[str, int, str, str, Path] | None:
    """The user's manifest (zmig.modulecheck.user_manifest) as (path shown, line, what the
    line is, its text, file): above the app, else in one of the project's own modules (a
    project whose manifest repository is an extra module; the app a copy elsewhere,
    regression: RIT FSW's patched app copy)."""
    app = ctx.old.spec.app.resolve()
    found = user_manifest(app)
    shown = None
    if found is None:
        roots = project_roots(ctx.old, ctx.old_img.dir)
        for root, prefix in sorted(roots.items()):
            if prefix and (found := user_manifest(Path(root))) is not None:
                shown = project_path(str(found[0]), roots)
                break
    if found is None:
        return None
    path, line, what = found
    rel = shown or os.path.relpath(path, app).replace(os.sep, "/")
    text = path.read_text(errors="replace").splitlines()[line - 1].strip()
    return rel, line, what, text, path
