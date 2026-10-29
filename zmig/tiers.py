"""Presentation tiers: act-now / review / fyi (docs/PROPOSAL-TIERS.md).

An additive label on each finding, for grouping output. Nothing else reads it: the
verdict, exit codes, watch fingerprints and every other field are computed without it.

Inputs are only what the run itself has: severity, confidence, the evidence items (their
kind and project file:line), and which project files the scanned build used. Evaluation
labels (confirmed, harmless, oracle items) are never inputs.

  act-now  breaking, high confidence, with a project file:line the scanned build used
  review   any other breaking finding (confidence only moves breaking findings between
           act-now and review); risky with a project file:line the build used
  fyi      everything else: risky without such a location, and all info

One exception (review decision, 2026-10-29): a breaking, high-confidence
`module-required` finding whose evidence includes the target build's own failure
(tool_output from "target build" or "target configure") is act-now even without a
project file:line. The failure shows it blocks the upgrade. No other kind uses it.

"Used by the scanned build" is decided per file kind, from the build's own records:
compiled sources and headers from ninja's dependency records, overlays from the overlays
the image applied, Kconfig fragments from the fragments it merged. A file of another kind
(Kconfig, CMake, YAML), or a build without those records, counts as used: a finding is
only demoted when the build shows the file wasn't used.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

Tier = Literal["act-now", "review", "fyi"]
TIERS: tuple[Tier, ...] = ("act-now", "review", "fyi")

_SOURCE = (".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".S", ".s", ".inc")
_OVERLAY = (".overlay", ".dts", ".dtsi")


@dataclass(frozen=True)
class BuildUse:
    """What one image's scanned build used, as evidence shows paths (app-relative, or
    "<module>/..."). None for a kind means no record of it."""

    sources: frozenset[str] | None = None
    overlays: frozenset[str] | None = None
    configs: frozenset[str] | None = None

    def used(self, path: str) -> bool:
        if path.endswith(_SOURCE):
            known = self.sources
        elif path.endswith(_OVERLAY):
            known = self.overlays
        elif path.endswith(".conf") or path.endswith("_defconfig"):
            known = self.configs
        else:
            known = None
        return known is None or path in known


def project_locations(evidence: Iterable[Mapping[str, Any]]) -> list[str]:
    """Paths of `file_line` evidence in the project's files (the same rule as SARIF's
    locations: Zephyr's and generated files are shown as `<...>`, absolute or `(...)`)."""
    return [
        str(e["path"])
        for e in evidence
        if e.get("type") == "file_line" and not str(e["path"]).startswith(("<", "/", "("))
    ]


TARGET_FAILURE = ("target build", "target configure")
TARGET_FAILURE_KINDS = frozenset({"module-required"})


def tier(
    severity: str,
    confidence: str,
    evidence: Iterable[Mapping[str, Any]],
    use: BuildUse | None,
    kind: str = "",
) -> Tier:
    """A pure function of its arguments."""
    evidence = list(evidence)
    use = use or BuildUse()
    located = any(use.used(p) for p in project_locations(evidence))
    failed = any(
        e.get("type") == "tool_output" and str(e.get("invocation", "")).startswith(TARGET_FAILURE)
        for e in evidence
    )
    if severity == "breaking" and confidence == "high" and kind in TARGET_FAILURE_KINDS and failed:
        return "act-now"
    if severity == "breaking":
        return "act-now" if located and confidence == "high" else "review"
    if severity == "risky" and located:
        return "review"
    return "fyi"


def assign(findings: list[Any], use_by_image: Mapping[str | None, BuildUse | None]) -> list[Any]:
    """The findings with `tier` set, in the same order. A finding's image is `image`
    (None: the app image, or the build as a whole)."""
    from dataclasses import replace

    return [
        replace(
            f,
            tier=tier(
                str(f.severity),
                str(f.confidence),
                f.evidence,
                use_by_image.get(f.image),
                f.kind,
            ),
        )
        for f in findings
    ]
