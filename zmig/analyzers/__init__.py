"""Analyzer interface and registry.

Analyzers are pure functions of their inputs: they read from the two contexts
and return findings, and never run builds themselves. Built-in analyzers are
listed in BUILTIN_*; third-party ones register under the `zmig.analyzers`
entry-point group and declare `kind = "build"` or `kind = "workspace"`.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol, runtime_checkable

from zmig.errors import ZmigError
from zmig.findings import AnalyzerStatus, Checked, Finding, NotChecked

if TYPE_CHECKING:
    from zmig.buildctx import Artifact
    from zmig.plan import PlanContext
    from zmig.scan import ScanContext

# "partial": ran, but some inputs couldn't be interpreted (listed in not_checked).
Status = Literal["ok", "partial", "skipped", "error"]


@dataclass(frozen=True)
class AnalyzerResult:
    findings: list[Finding] = field(default_factory=list)
    status: Status = "ok"
    reason: str | None = None
    not_checked: list[NotChecked] = field(default_factory=list)
    # What it did examine (with the result), so "ran, found nothing" is visible.
    checked: list[Checked] = field(default_factory=list)

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
    from zmig.analyzers.api import ApiAnalyzer
    from zmig.analyzers.ble import BleAnalyzer
    from zmig.analyzers.board import BoardAnalyzer
    from zmig.analyzers.buildlog import BuildlogAnalyzer
    from zmig.analyzers.crossimage import CrossImageAnalyzer
    from zmig.analyzers.devicetree import DevicetreeAnalyzer
    from zmig.analyzers.guide import GuideAnalyzer
    from zmig.analyzers.images import ImagesAnalyzer
    from zmig.analyzers.initorder import InitOrderAnalyzer
    from zmig.analyzers.kconfig import KconfigAnalyzer
    from zmig.analyzers.layout import LayoutAnalyzer
    from zmig.analyzers.mcuboot import McubootAnalyzer
    from zmig.analyzers.modules import ModulesAnalyzer
    from zmig.analyzers.sbom import SbomAnalyzer
    from zmig.analyzers.storage import StorageAnalyzer
    from zmig.analyzers.sysbuild import SysbuildAnalyzer
    from zmig.analyzers.wire import WireAnalyzer

    # The guide analyzer runs last: it links its findings to the ones before it.
    return [
        ModulesAnalyzer,
        BoardAnalyzer,
        KconfigAnalyzer,
        DevicetreeAnalyzer,
        BuildlogAnalyzer,
        InitOrderAnalyzer,
        ApiAnalyzer,
        SbomAnalyzer,
        ImagesAnalyzer,
        CrossImageAnalyzer,
        SysbuildAnalyzer,
        LayoutAnalyzer,
        McubootAnalyzer,
        StorageAnalyzer,
        BleAnalyzer,
        WireAnalyzer,
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
    checked: list[Checked] = []
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
            checked += res.checked
            statuses.append(AnalyzerStatus(label, res.status, res.reason, 0, 0))
            ctx.shared.setdefault("findings", []).extend(res.findings)
    base.shared["checked"] = checked
    final = outside_build(findings, base)
    # Counts after outside_build's downgrades: what the report shows for each analyzer.
    counted = []
    for st in statuses:
        if st.findings is None:
            counted.append(st)
            continue
        name, _, image = st.name.partition("@")
        if image == base.old_img.name:
            image = ""  # the app image's findings carry no image tag
        mine = [f for f in final if f.analyzer == name and (f.image or "") == image]
        hazards = sum(1 for f in mine if f.severity.value != "info")
        counted.append(replace(st, findings=len(mine), hazards=hazards))
    return link_related(final), counted, not_checked


# App input files that a build may or may not use, depending on board, shield and SoC.
BUILD_INPUT_SUFFIXES = (".conf", ".overlay", ".dts", ".dtsi", ".keymap")


def build_inputs(base: ScanContext) -> set[str] | None:
    """The project's configuration and devicetree input files that any image of either
    build used (merged .conf fragments, and every file in zephyr.dts.d), as shown in
    evidence (app-relative, or "<module>/..."). None if no image recorded them."""
    from zmig.buildctx import project_path, project_roots
    from zmig.diagnostics import parse_log

    out: set[str] = set()
    found = False
    for b in (base.old, base.new, base.new_raw):
        for img in b.images:
            roots = project_roots(b, img.dir)
            paths: list[str] = []
            if img.log.is_file():
                facts = parse_log(img.log.read_text())
                paths += [*facts.configs, *facts.overlays]
            deps = img.dir / "zephyr" / "zephyr.dts.d"
            if deps.is_file():
                found = True
                paths += [t.rstrip(":") for t in deps.read_text().split() if t != "\\"]
            for p in paths:
                if rel := project_path(os.path.normpath(p), roots):
                    out.add(rel)
    return out if found else None


def outside_build(findings: list[Finding], base: ScanContext) -> list[Finding]:
    """Breaking/risky findings whose only evidence is in configuration or devicetree files
    no build used (another board's .conf, a shield's overlay) become info, saying so. They
    stay in the report as hints for those configurations (regression: Golioth's
    rak5010_nrf52840.conf and an STM32H5 USB node blamed on rak5010's overlay,
    docs/DOGFOOD-2-baseline.md M2/M3).

    Likewise for findings whose only evidence is in app source files the build doesn't
    compile (regression: echo_server's tunnel.c and vlan.c, built only with
    CONFIG_NET_L2_IPIP / CONFIG_NET_VLAN, AUDIT.md F7). Compiled files come from the app
    image's ninja records, so only app-image findings are judged."""
    from zmig.findings import Severity
    from zmig.usage import SOURCE_SUFFIXES

    inputs = build_inputs(base)
    compiled = base.build_files
    if inputs is None and compiled is None:
        return findings

    def unused(files: set[str], image: str | None) -> str | None:
        """How the build doesn't use these files, or None if it might."""
        if inputs is not None and all(
            p.endswith(BUILD_INPUT_SUFFIXES) and p not in inputs for p in files
        ):
            return "use"
        if (
            compiled is not None
            and image is None
            and all(p.endswith(SOURCE_SUFFIXES) and p not in compiled for p in files)
        ):
            return "compile"
        return None

    out = []
    for f in findings:
        files = {str(e["path"]) for e in f.evidence if e.get("type") == "file_line"}
        # "<v4.1.0 hello>/zephyr/zephyr.dts" is a build artifact, not a project input file
        # (regression: B-rak's partition findings were downgraded while the layout category
        # stayed a hazard, docs/DOGFOOD-3-baseline.md M8).
        if (
            f.severity is not Severity.INFO
            and files
            and not any(p.startswith("<") for p in files)
            and (how := unused(files, f.image))
        ):
            f = replace(
                f,
                severity=Severity.INFO,
                title=f"{f.title} (only in files this build doesn't {how}: "
                f"{', '.join(sorted(files)[:3])}{', ...' if len(files) > 3 else ''})",
                heuristics=(*f.heuristics, "outside-build"),
            )
        out.append(f)
    return out


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
