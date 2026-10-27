"""Analyzer interface and registry.

Analyzers are pure functions of their inputs: they read from the two contexts
and return findings, and never run builds themselves. Built-in analyzers are
listed in BUILTIN_*; third-party ones register under the `zmig.analyzers`
entry-point group and declare `kind = "build"` or `kind = "workspace"`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol, runtime_checkable

from zmig.errors import ZmigError
from zmig.findings import AnalyzerStatus, Finding

if TYPE_CHECKING:
    from zmig.buildctx import Artifact
    from zmig.plan import PlanContext
    from zmig.scan import ScanContext

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
    """Used by `scan`: compares two builds of the same app (see zmig.scan.ScanContext)."""

    name: ClassVar[str]
    kind: ClassVar[Literal["build"]]
    requires: ClassVar[frozenset[Artifact]]

    def analyze(self, ctx: ScanContext) -> AnalyzerResult: ...


@runtime_checkable
class WorkspaceAnalyzer(Protocol):
    """Used by `plan`: compares upstream at two refs against the user's workspace."""

    name: ClassVar[str]
    kind: ClassVar[Literal["workspace"]]

    def analyze(self, ctx: PlanContext) -> AnalyzerResult: ...


def _builtin(kind: str) -> list[type[Any]]:
    """Built-in analyzers, in run order. Imported lazily: they import this module."""
    if kind == "workspace":
        from zmig.analyzers.forks import ForksAnalyzer
        from zmig.analyzers.hops import HopsAnalyzer
        from zmig.analyzers.manifest import ManifestAnalyzer

        return [ManifestAnalyzer, ForksAnalyzer, HopsAnalyzer]
    from zmig.analyzers.board import BoardAnalyzer
    from zmig.analyzers.buildlog import BuildlogAnalyzer
    from zmig.analyzers.devicetree import DevicetreeAnalyzer
    from zmig.analyzers.guide import GuideAnalyzer
    from zmig.analyzers.kconfig import KconfigAnalyzer
    from zmig.analyzers.sbom import SbomAnalyzer

    # The guide analyzer runs last: it links its findings to the ones before it.
    return [
        BoardAnalyzer,
        KconfigAnalyzer,
        DevicetreeAnalyzer,
        BuildlogAnalyzer,
        SbomAnalyzer,
        GuideAnalyzer,
    ]


def _plugins(kind: str) -> list[type[Any]]:
    found = []
    for ep in entry_points(group="zmig.analyzers"):
        cls = ep.load()
        if getattr(cls, "kind", None) == kind:
            found.append(cls)
    return found


def select(kind: Literal["build", "workspace"], names: Iterable[str] | None) -> list[type[Any]]:
    """Analyzer classes of `kind`, filtered to `names` (None = all, empty = none)."""
    available = {c.name: c for c in _builtin(kind) + _plugins(kind)}
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
    classes: list[type[Any]], ctx: ScanContext
) -> tuple[list[Finding], list[AnalyzerStatus]]:
    """Run analyzers in order, turning missing artifacts and exceptions into a reported
    status, then make `related` links symmetric."""
    findings: list[Finding] = []
    statuses: list[AnalyzerStatus] = []
    for cls in classes:
        missing = [
            f"{side} ({spec}): {b.missing_reason(art)}"
            for art in sorted(cls.requires, key=lambda x: x.value)
            for side, b, spec in (
                ("source", ctx.old, ctx.old_spec),
                ("target", ctx.new, ctx.new_spec),
            )
            if not b.has(art)
        ]
        if missing:
            statuses.append(AnalyzerStatus(cls.name, "skipped", "; ".join(missing)))
            continue
        ctx.prior = list(findings)
        try:
            res: AnalyzerResult = cls().analyze(ctx)
        except ZmigError as e:
            statuses.append(AnalyzerStatus(cls.name, "error", str(e)))
            continue
        findings += res.findings
        statuses.append(AnalyzerStatus(cls.name, res.status, res.reason))
    return link_related(findings), statuses


def link_related(findings: list[Finding]) -> list[Finding]:
    """If A lists B as related, B lists A too."""
    back: dict[str, set[str]] = {}
    for f in findings:
        for r in f.related:
            back.setdefault(r, set()).add(f.id)
    return [
        replace(f, related=tuple(sorted(set(f.related) | back.get(f.id, set())))) for f in findings
    ]


def run_workspace_analyzers(
    classes: list[type[Any]], ctx: PlanContext
) -> tuple[list[Finding], list[AnalyzerStatus]]:
    findings: list[Finding] = []
    statuses: list[AnalyzerStatus] = []
    for cls in classes:
        try:
            res: AnalyzerResult = cls().analyze(ctx)
        except ZmigError as e:
            statuses.append(AnalyzerStatus(cls.name, "error", str(e)))
            continue
        findings += res.findings
        statuses.append(AnalyzerStatus(cls.name, res.status, res.reason))
    return findings, statuses
