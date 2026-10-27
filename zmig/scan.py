"""Inputs shared by the `scan` analyzers."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any

from zmig.boards import BoardMapping
from zmig.buildctx import BuildContext, SalvageEdit
from zmig.diagnostics import LogFacts, parse_log
from zmig.snapshots import ConfigValues, config_values, edt, kconfig_symbols
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
