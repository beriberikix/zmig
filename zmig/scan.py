"""Inputs shared by the `scan` analyzers.

A ScanContext compares one image of the source build with the same image of the target
build. A plain build has one image (the app); a sysbuild build is analyzed once per image
present on both sides (app, MCUboot, ...). Analyzers that look at the build as a whole
(the guide, image-set and cross-image analyzers) receive the app image's context and use
`old` / `new` (the BuildContexts) for the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import Path
from typing import Any

from zmig.boards import BoardMapping
from zmig.buildctx import BuildContext, ImageContext, SalvageEdit, project_path, project_roots
from zmig.diagnostics import LogFacts, app_relative, parse_log
from zmig.findings import Finding
from zmig.guides import NOTES_SECTION, Document, Entry, load_documents, parse
from zmig.modulecheck import ModuleNeed
from zmig.snapshots import (
    ConfigValues,
    app_undefined_symbols,
    build_dependencies,
    config_values,
    edt,
    kconfig_symbols,
)
from zmig.tools import Runner
from zmig.usage import ProjectUsage
from zmig.usage import scan as usage_scan

SYSBUILD_DIR = "sysbuild"  # per-image sysbuild configuration inside an app


@dataclass
class ScanContext:
    runner: Runner
    old: BuildContext
    new: BuildContext  # what analyzers compare: the salvaged build if salvage was needed
    new_raw: BuildContext  # the target build of the app as the user wrote it
    old_img: ImageContext
    new_img: ImageContext
    new_raw_img: ImageContext | None
    salvage: tuple[SalvageEdit, ...] = ()
    board_mapping: BoardMapping | None = None
    # Findings of the analyzers that already ran (the guide analyzer links to them).
    prior: list[Finding] = field(default_factory=list)
    all_guide_entries: bool = False
    # A signed app image read from a field device (--deployed-image): replaces the source
    # build's signed image as the "old" side of the measured image checks.
    deployed_image: Path | None = None
    # User wire-format allowlist entries (--wire-allowlist), added to the shipped ones.
    wire_allowlist: Path | None = None
    # Modules the target build turned out to need (zmig.modulecheck), and those of them
    # zmig added to rebuild the target so the analysis could continue.
    module_needs: tuple[ModuleNeed, ...] = ()
    modules_added: tuple[str, ...] = ()
    # Caches shared by the contexts of all images of one scan.
    shared: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def for_pair(cls, base: ScanContext, old: ImageContext, new: ImageContext) -> ScanContext:
        """The context comparing image `old` (source build) with `new` (target build)."""
        raw = base.new_raw.image(new.name)
        return replace(base, old_img=old, new_img=new, new_raw_img=raw, prior=base.prior)

    @property
    def image(self) -> str:
        return self.old_img.name

    @property
    def is_app(self) -> bool:
        return self.old_img.role == "app"

    @property
    def scope(self) -> tuple[str, ...]:
        """Identity of the comparison, for finding ids. The app image adds nothing, so
        ids of single-image builds are unchanged."""
        base = (
            self.old.workspace.resolved.ref.sha,
            self.new.workspace.resolved.ref.sha,
            self.old.spec.board,
        )
        return base if self.is_app else (*base, self.image)

    @property
    def old_spec(self) -> str:
        return self.old.workspace.resolved.ref.label

    @property
    def new_spec(self) -> str:
        return self.new.workspace.resolved.ref.label

    # --- what the user's app contributes to this image --------------------------------

    @cached_property
    def usage(self) -> ProjectUsage:
        """References from the app's files that apply to this image. For the app image:
        everything except per-image sysbuild configuration (sysbuild/, sysbuild.conf).
        For other images: the app files that image's build applied (from its log)."""
        app = self.old.spec.app.resolve()
        if self.is_app:

            def keep(rel: str) -> bool:
                return not (rel.startswith(SYSBUILD_DIR + "/") or rel == "sysbuild.conf")

            return usage_scan(app, include=keep)
        applied = set(app_relative(self.old_facts.overlays + self.old_facts.configs, self.old.src))
        return usage_scan(app, include=lambda rel: rel in applied)

    @cached_property
    def build_files(self) -> set[str] | None:
        """The project's files the source build of this image used: compiled sources and
        every header they included (ninja's dependency records), shown as in evidence
        (app-relative, or "<module>/..."). None when the build has no records."""
        deps = build_dependencies(self.old_img, self.runner)
        if deps is None:
            return None
        roots = project_roots(self.old, self.old_img.dir)
        return {rel for files in deps.values() for f in files if (rel := project_path(f, roots))}

    @property
    def app_usage(self) -> ProjectUsage:
        if "app_usage" not in self.shared:
            self.shared["app_usage"] = usage_scan(self.old.spec.app.resolve())
        usage: ProjectUsage = self.shared["app_usage"]
        return usage

    # --- snapshots of this image -------------------------------------------------------

    @cached_property
    def old_config(self) -> ConfigValues:
        return config_values(self.old_img)

    @cached_property
    def new_config(self) -> ConfigValues:
        return config_values(self.new_img)

    @cached_property
    def old_kconfig(self) -> dict[str, dict[str, Any]]:
        return kconfig_symbols(self.old_img, self.old.workspace, self.runner)

    @cached_property
    def new_kconfig(self) -> dict[str, dict[str, Any]]:
        return kconfig_symbols(self.new_img, self.new.workspace, self.runner)

    @cached_property
    def old_edt(self) -> dict[str, Any]:
        return edt(self.old_img, self.old.workspace, self.runner)

    @cached_property
    def new_edt(self) -> dict[str, Any]:
        return edt(self.new_img, self.new.workspace, self.runner)

    @cached_property
    def old_facts(self) -> LogFacts:
        return parse_log(self.old_img.log.read_text())

    @cached_property
    def new_facts(self) -> LogFacts:
        return parse_log(self.new_img.log.read_text())

    @cached_property
    def new_raw_facts(self) -> LogFacts:
        """Diagnostics from this image in the target build of the unmodified app."""
        if self.new_raw_img is None:
            return LogFacts()
        return parse_log(self.new_raw_img.log.read_text())

    @property
    def raw_src(self) -> Path:
        return self.new_raw.src

    @cached_property
    def app_linked_symbols(self) -> set[str] | None:
        """Undefined symbols of the app's objects in the source build (see nm)."""
        if self.old.app.status != "ok":
            return None
        return app_undefined_symbols(self.old.app, self.old.src, self.runner)

    # --- migration guides (shared by every image) -------------------------------------

    @property
    def guide_documents(self) -> list[Document]:
        if "guide_documents" not in self.shared:
            self.shared["guide_documents"] = load_documents(self.old, self.new)
        docs: list[Document] = self.shared["guide_documents"]
        return docs

    @property
    def guide_entries(self) -> list[tuple[Document, Entry]]:
        """Parsed entries of every guide and release-notes document crossed."""
        if "guide_entries" not in self.shared:
            out: list[tuple[Document, Entry]] = []
            for d in self.guide_documents:
                prefix, filt = ("MG", None) if d.kind == "guide" else ("RN", NOTES_SECTION)
                out += [(d, e) for e in parse(d.text, d.path, d.version, prefix, filt)]
            self.shared["guide_entries"] = out
        entries: list[tuple[Document, Entry]] = self.shared["guide_entries"]
        return entries
