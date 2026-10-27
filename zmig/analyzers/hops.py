"""Release boundaries crossed by the upgrade, and suggested intermediate hops.

Release versions come from each ref's VERSION file and the mirror's release tags.
LTS releases and contract-breaking boundaries come from zmig/data/releases.json,
which is hand-maintained, so those findings carry the `hops.release-data` heuristic.
"""

from __future__ import annotations

import json
import re
from importlib.resources import files
from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.findings import Category, Confidence, Finding, Severity
from zmig.plan import PlanContext
from zmig.refs import Mirror

Minor = tuple[int, int]
HEURISTIC = "hops.release-data"


def release_data() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(files("zmig").joinpath("data/releases.json").read_text())
    return data


def _minor(s: str) -> Minor:
    a, b = s.split(".")[:2]
    return int(a), int(b)


def _fmt(m: Minor) -> str:
    return f"{m[0]}.{m[1]}"


def ref_version(mirror: Mirror, sha: str) -> tuple[Minor, int]:
    """(major.minor, patchlevel) from VERSION at `sha`."""
    text = mirror.runner.run(
        [mirror.git, "--git-dir", mirror.path, "show", f"{sha}:VERSION"]
    ).stdout
    kv = dict(re.findall(r"^(\w+)\s*=\s*(\S*)", text, re.M))
    return (int(kv["VERSION_MAJOR"]), int(kv["VERSION_MINOR"])), int(kv["PATCHLEVEL"])


def release_tags(mirror: Mirror) -> dict[Minor, list[tuple[int, str]]]:
    """Final release tags (no -rc), grouped by minor: {(3, 7): [(0, 'v3.7.0'), ...]}."""
    out: dict[Minor, list[tuple[int, str]]] = {}
    tags = mirror.runner.run([mirror.git, "--git-dir", mirror.path, "tag", "-l", "v*"]).stdout
    for tag in tags.split():
        if m := re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag):
            out.setdefault((int(m[1]), int(m[2])), []).append((int(m[3]), tag))
    for v in out.values():
        v.sort()
    return out


def plan_hops(crossed: list[Minor], anchors: set[Minor], max_gap: int) -> list[Minor]:
    """Intermediate minors to stop at: every anchor (LTS or boundary) before the target,
    plus fillers so that no step crosses more than `max_gap` minor releases."""
    if len(crossed) <= max_gap:
        return []
    target = crossed[-1]
    stops = sorted(a for a in anchors if a in crossed and a != target)
    hops: list[Minor] = []
    prev_idx = -1  # index in `crossed` of the last stop (-1 = the starting ref)
    for stop in [*stops, target]:
        idx = crossed.index(stop)
        while idx - prev_idx > max_gap:
            prev_idx += max_gap
            hops.append(crossed[prev_idx])
        if stop != target:
            hops.append(stop)
        prev_idx = idx
    return hops


class HopsAnalyzer:
    name: ClassVar[str] = "hops"
    kind: ClassVar[Literal["workspace"]] = "workspace"

    def analyze(self, ctx: PlanContext) -> AnalyzerResult:
        data = release_data()
        mirror = ctx.old.resolved.mirror
        old_v, _ = ref_version(mirror, ctx.old.resolved.ref.sha)
        # A development snapshot (PATCHLEVEL 99) of X.Y has crossed releases up to X.Y.
        new_v, _ = ref_version(mirror, ctx.new.resolved.ref.sha)
        tags = release_tags(mirror)
        crossed = sorted(m for m in tags if old_v < m <= new_v and any(p == 0 for p, _ in tags[m]))
        lts = {_minor(k) for k in data["lts"]}
        boundaries = {_minor(k): v for k, v in data["boundaries"].items()}
        findings: list[Finding] = []
        old_spec, new_spec = ctx.old.resolved.ref.spec, ctx.new.resolved.ref.spec

        for m in crossed:
            if m in boundaries:
                b = boundaries[m]
                findings.append(
                    Finding(
                        category=Category.HOP,
                        kind="boundary-crossed",
                        subject=_fmt(m),
                        title=f"The upgrade crosses {_fmt(m)}: {b['title']}",
                        severity=Severity.INFO,
                        confidence=Confidence.MEDIUM,
                        action=f"Read the {_fmt(m)} migration guide section first. {b['detail']}",
                        analyzer=self.name,
                        scope=ctx.scope,
                        new=b,
                        heuristics=(HEURISTIC,),
                    )
                )

        hops = plan_hops(crossed, lts | set(boundaries), int(data["max_minor_gap"]))
        if hops:
            path = [old_spec, *(tags[m][-1][1] for m in hops), new_spec]
            reasons = {}
            for m in hops:
                why = []
                if m in lts:
                    why.append(f"{data['lts'][_fmt(m)]} long-term support release")
                if m in boundaries:
                    why.append(f"lands the {boundaries[m]['title']} change on its own")
                if not why:
                    why.append(f"keeps each step to at most {data['max_minor_gap']} minor releases")
                reasons[tags[m][-1][1]] = "; ".join(why)
            findings.append(
                Finding(
                    category=Category.HOP,
                    kind="suggested-path",
                    subject=f"{old_spec}..{new_spec}",
                    title=f"{len(crossed)} minor releases apart; upgrade in {len(hops) + 1} steps: "
                    + " → ".join(path),
                    severity=Severity.RISKY,
                    confidence=Confidence.MEDIUM,
                    action="Run `zmig plan`/`zmig scan` for each step and get it building and "
                    "passing tests before the next, so every step is a bisectable state.",
                    analyzer=self.name,
                    scope=ctx.scope,
                    new={"path": path, "reasons": reasons, "crossed": [_fmt(m) for m in crossed]},
                    heuristics=(HEURISTIC,),
                )
            )
        return AnalyzerResult(findings)
