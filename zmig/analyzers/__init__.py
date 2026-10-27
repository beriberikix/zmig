"""Analyzer interface and registry.

Analyzers are pure functions of their inputs: they read from the two contexts
and return findings, and never run builds themselves. Built-in analyzers are
listed in BUILTIN_*; third-party ones register under the `zmig.analyzers`
entry-point group and declare `kind = "build"` or `kind = "workspace"`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol, runtime_checkable

from zmig.errors import ZmigError
from zmig.findings import AnalyzerStatus, Finding

if TYPE_CHECKING:
    from zmig.buildctx import Artifact, BuildContext
    from zmig.workspace import RefWorkspace

Status = Literal["ok", "skipped", "error"]


@dataclass(frozen=True)
class AnalyzerResult:
    findings: list[Finding] = field(default_factory=list)
    status: Status = "ok"
    reason: str | None = None

    @classmethod
    def skipped(cls, reason: str) -> AnalyzerResult:
        return cls(status="skipped", reason=reason)


@runtime_checkable
class BuildAnalyzer(Protocol):
    """Used by `scan`: compares two builds of the same app."""

    name: ClassVar[str]
    kind: ClassVar[Literal["build"]]
    requires: ClassVar[frozenset[Artifact]]

    def analyze(self, old: BuildContext, new: BuildContext, usage: object) -> AnalyzerResult: ...


@runtime_checkable
class WorkspaceAnalyzer(Protocol):
    """Used by `plan`: compares two workspaces (manifests, forks, history)."""

    name: ClassVar[str]
    kind: ClassVar[Literal["workspace"]]

    def analyze(self, old: RefWorkspace, new: RefWorkspace, user: object) -> AnalyzerResult: ...


# Populated as milestones land (M2: manifest, forks, hops; M3: board, kconfig, ...).
BUILTIN_BUILD: list[type[BuildAnalyzer]] = []
BUILTIN_WORKSPACE: list[type[WorkspaceAnalyzer]] = []


def _plugins(kind: str) -> list[type[Any]]:
    found = []
    for ep in entry_points(group="zmig.analyzers"):
        cls = ep.load()
        if getattr(cls, "kind", None) == kind:
            found.append(cls)
    return found


def select(kind: Literal["build", "workspace"], names: Iterable[str] | None) -> list[type[Any]]:
    """Analyzer classes of `kind`, filtered to `names` (None = all, empty = none)."""
    builtin: list[type[Any]] = list(BUILTIN_BUILD if kind == "build" else BUILTIN_WORKSPACE)
    available = {c.name: c for c in builtin + _plugins(kind)}
    if names is None:
        return list(available.values())
    wanted = list(names)
    unknown = [n for n in wanted if n not in available]
    if unknown:
        raise ZmigError(
            f"unknown analyzer(s): {', '.join(unknown)}; "
            f"available: {', '.join(sorted(available)) or '(none yet)'}"
        )
    return [available[n] for n in wanted]


def run_build_analyzers(
    classes: list[type[Any]], old: BuildContext, new: BuildContext, usage: object
) -> tuple[list[Finding], list[AnalyzerStatus]]:
    """Run analyzers, turning missing artifacts and exceptions into a reported status."""
    findings: list[Finding] = []
    statuses: list[AnalyzerStatus] = []
    for cls in classes:
        missing = sorted(a.value for a in cls.requires if not (old.has(a) and new.has(a)))
        if missing:
            statuses.append(
                AnalyzerStatus(cls.name, "skipped", f"missing artifacts: {', '.join(missing)}")
            )
            continue
        try:
            res: AnalyzerResult = cls().analyze(old, new, usage)
        except ZmigError as e:
            statuses.append(AnalyzerStatus(cls.name, "error", str(e)))
            continue
        findings += res.findings
        statuses.append(AnalyzerStatus(cls.name, res.status, res.reason))
    return findings, statuses
