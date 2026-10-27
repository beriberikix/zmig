"""Inputs shared by the `scan` analyzers."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from zmig.boards import BoardMapping
from zmig.buildctx import BuildContext, SalvageEdit
from zmig.diagnostics import LogFacts, parse_log
from zmig.findings import Finding
from zmig.guides import NOTES_SECTION, Document, Entry, load_documents, parse
from zmig.snapshots import (
    ConfigValues,
    app_undefined_symbols,
    config_values,
    edt,
    kconfig_symbols,
)
from zmig.tools import Runner
from zmig.usage import ProjectUsage


@dataclass
class ScanContext:
    runner: Runner
    old: BuildContext
    new: BuildContext  # what analyzers compare: the salvaged build if salvage was needed
    new_raw: BuildContext  # the target build of the app as the user wrote it
    usage: ProjectUsage
    salvage: tuple[SalvageEdit, ...] = ()
    board_mapping: BoardMapping | None = None
    # Findings of the analyzers that already ran (the guide analyzer links to them).
    prior: list[Finding] = field(default_factory=list)
    all_guide_entries: bool = False

    @property
    def scope(self) -> tuple[str, ...]:
        return (
            self.old.workspace.resolved.ref.sha,
            self.new.workspace.resolved.ref.sha,
            self.old.spec.board,
        )

    @property
    def old_spec(self) -> str:
        return self.old.workspace.resolved.ref.spec

    @property
    def new_spec(self) -> str:
        return self.new.workspace.resolved.ref.spec

    @cached_property
    def old_config(self) -> ConfigValues:
        return config_values(self.old)

    @cached_property
    def new_config(self) -> ConfigValues:
        return config_values(self.new)

    @cached_property
    def old_kconfig(self) -> dict[str, dict[str, Any]]:
        return kconfig_symbols(self.old, self.runner)

    @cached_property
    def new_kconfig(self) -> dict[str, dict[str, Any]]:
        return kconfig_symbols(self.new, self.runner)

    @cached_property
    def old_edt(self) -> dict[str, Any]:
        return edt(self.old, self.runner)

    @cached_property
    def new_edt(self) -> dict[str, Any]:
        return edt(self.new, self.runner)

    @cached_property
    def old_facts(self) -> LogFacts:
        return parse_log(self.old.log.read_text())

    @cached_property
    def new_facts(self) -> LogFacts:
        return parse_log(self.new.log.read_text())

    @cached_property
    def new_raw_facts(self) -> LogFacts:
        """Diagnostics from the target build of the unmodified app."""
        return parse_log(self.new_raw.log.read_text())

    @cached_property
    def app_linked_symbols(self) -> set[str] | None:
        """Undefined symbols of the app's objects in the source build (see nm)."""
        if self.old.status != "ok":
            return None
        return app_undefined_symbols(self.old, self.usage.app, self.runner)

    @cached_property
    def guide_documents(self) -> list[Document]:
        return load_documents(self.old, self.new)

    @cached_property
    def guide_entries(self) -> list[tuple[Document, Entry]]:
        """Parsed entries of every guide and release-notes document crossed."""
        out: list[tuple[Document, Entry]] = []
        for d in self.guide_documents:
            prefix, filt = ("MG", None) if d.kind == "guide" else ("RN", NOTES_SECTION)
            out += [(d, e) for e in parse(d.text, d.path, d.version, prefix, filt)]
        return out
