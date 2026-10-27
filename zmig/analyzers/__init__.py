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
from zmig.findings import AnalyzerStatus, Finding, NotChecked

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
    not_checked: list[NotChecked] = field(default_factory=list)

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
    from zmig.analyzers.crossimage import CrossImageAnalyzer
    from zmig.analyzers.devicetree import DevicetreeAnalyzer
    from zmig.analyzers.guide import GuideAnalyzer
    from zmig.analyzers.images import ImagesAnalyzer
    from zmig.analyzers.kconfig import KconfigAnalyzer
    from zmig.analyzers.layout import LayoutAnalyzer
    from zmig.analyzers.mcuboot import McubootAnalyzer
    from zmig.analyzers.sbom import SbomAnalyzer
    from zmig.analyzers.storage import StorageAnalyzer
    from zmig.analyzers.sysbuild import SysbuildAnalyzer

    # The guide analyzer runs last: it links its findings to the ones before it.
    return [
        BoardAnalyzer,
        KconfigAnalyzer,
        DevicetreeAnalyzer,
        BuildlogAnalyzer,
        SbomAnalyzer,
        ImagesAnalyzer,
        CrossImageAnalyzer,
        SysbuildAnalyzer,
        LayoutAnalyzer,
        McubootAnalyzer,
        StorageAnalyzer,
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
    classes: list[type[Any]], base: ScanContext
) -> tuple[list[Finding], list[AnalyzerStatus], list[NotChecked]]:
    """Run analyzers in order. Per-image analyzers (the default; a class can set
    `per_image = False`) run once for every image both builds have, and their findings
    carry that image unless it is the app. Missing artifacts and errors become a reported
    status. `related` links are made symmetric at the end."""
    from zmig.buildctx import pair_images
    from zmig.scan import ScanContext as _Ctx

    pairs = pair_images(base.old, base.new)
    multi = len(pairs) > 1
    findings: list[Finding] = []
    statuses: list[AnalyzerStatus] = []
    not_checked: list[NotChecked] = []
    for cls in classes:
        per_image = getattr(cls, "per_image", True)
        ctxs = [_Ctx.for_pair(base, o, n) for o, n in pairs] if per_image else [base]
        for ctx in ctxs:
            label = f"{cls.name}@{ctx.image}" if per_image and multi else cls.name
            missing = [
                f"{side} ({spec}): {img.missing_reason(art)}"
                for art in sorted(cls.requires, key=lambda x: x.value)
                for side, img, spec in (
                    ("source", ctx.old_img, ctx.old_spec),
                    ("target", ctx.new_img, ctx.new_spec),
                )
                if not img.has(art)
            ]
            if missing:
                statuses.append(AnalyzerStatus(label, "skipped", "; ".join(missing)))
                continue
            ctx.prior = list(findings)
            try:
                res: AnalyzerResult = cls().analyze(ctx)
            except ZmigError as e:
                statuses.append(AnalyzerStatus(label, "error", str(e)))
                continue
            tag = None if (not per_image or ctx.is_app) else ctx.image
            findings += [replace(f, image=tag) if tag else f for f in res.findings]
            not_checked += res.not_checked
            statuses.append(AnalyzerStatus(label, res.status, res.reason))
            ctx.shared.setdefault("findings", []).extend(res.findings)
    return link_related(findings), statuses, not_checked


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
