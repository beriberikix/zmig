"""Release boundaries crossed by the upgrade, and suggested intermediate hops.

Release versions come from each ref's VERSION file and the mirror's release tags.
LTS releases and contract-breaking boundaries come from zmig/data/releases.json,
which is hand-maintained, so those findings carry the `hops.release-data` heuristic.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.findings import Category, Confidence, Finding, Severity
from zmig.plan import PlanContext
from zmig.refs import Mirror

Minor = tuple[int, int]
HEURISTIC = "hops.release-data"


def release_data(override: Path | None = None) -> dict[str, Any]:
    """The release data file: --release-data, else $ZMIG_RELEASE_DATA, else the bundled
    one. It holds the contract-breaking boundaries, the step size, and LTS facts used
    only when Zephyr's own docs don't state them."""
    path = override or (Path(p) if (p := os.environ.get("ZMIG_RELEASE_DATA")) else None)
    text = path.read_text() if path else files("zmig").joinpath("data/releases.json").read_text()
    data: dict[str, Any] = json.loads(text)
    data["_source"] = str(path) if path else "zmig/data/releases.json"
    return data


@dataclass(frozen=True)
class Lts:
    label: str  # "LTS3"
    source: str  # where this was read
    planned: bool = False


_INDEX_RELEASED = re.compile(r"Zephyr (\d+)\.(\d+)\.\d+ \(LTS(\d+)\)")
_INDEX_PROSE = re.compile(r"\bLTS(\d+) \((\d+)\.(\d+)\.x\)")
_INDEX_PLANNED = re.compile(r"^\|\s*(\d+)\.(\d+)\s*\|[^|\n]*\|\s*LTS(\d+)", re.M)
_NOTES_LTS = re.compile(
    r"\b(?:will be|is)\b(?: the next| an?)?\s+(?::ref:`)?Long Term Support", re.I
)


def derive_lts(
    mirror: Mirror,
    target: str,
    minors: list[Minor],
    tags: dict[Minor, list[tuple[int, str]]],
    data: dict[str, Any],
) -> dict[Minor, Lts]:
    """LTS releases, from Zephyr's own docs where possible:
    doc/releases/index.rst at the target ref (released and planned LTS), and each crossed
    release's own release notes ("... will be the next Long Term Support release").
    The data file fills gaps and is reported as the source when used."""

    def show(rev: str, path: str) -> str:
        proc = mirror.runner.run(
            [mirror.git, "--git-dir", mirror.path, "show", f"{rev}:{path}"], check=False
        )
        return proc.stdout if proc.returncode == 0 else ""

    out: dict[Minor, Lts] = {}
    index = show(target, "doc/releases/index.rst")
    where = f"doc/releases/index.rst@{target[:12]}"
    for m in _INDEX_RELEASED.finditer(index):
        out.setdefault((int(m[1]), int(m[2])), Lts(f"LTS{m[3]}", where))
    for m in _INDEX_PROSE.finditer(index):
        out.setdefault((int(m[2]), int(m[3])), Lts(f"LTS{m[1]}", where))
    for m in _INDEX_PLANNED.finditer(index):
        out.setdefault((int(m[1]), int(m[2])), Lts(f"LTS{m[3]}", where, planned=True))
    for minor in minors:
        if minor in out or minor not in tags:
            continue
        tag = tags[minor][0][1]
        notes = show(tag, f"doc/releases/release-notes-{minor[0]}.{minor[1]}.rst")
        intro = " ".join(notes.splitlines()[:40])
        if _NOTES_LTS.search(" ".join(intro.split())):
            out[minor] = Lts("LTS", f"doc/releases/release-notes-{minor[0]}.{minor[1]}.rst@{tag}")
    for k, label in data.get("lts", {}).items():
        out.setdefault(_minor(k), Lts(label, data["_source"]))
    return out


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


def fork_of(mirror: Mirror, sha: str) -> tuple[str, int, list[str]] | None:
    """(nearest upstream tag, commits on top of it that no upstream tag or branch contains,
    a few of their subjects) if `sha` isn't upstream; None if it is. Local checkouts are
    fetched under refs/zmig/local, which is excluded from "upstream"."""
    g: list[str | Path] = [mirror.git, "--git-dir", mirror.path]
    own = mirror.runner.run(
        [*g, "rev-list", "--count", sha, "--not", "--tags", "--branches"], check=False
    ).stdout.strip()
    if not own.isdigit() or int(own) == 0:
        return None
    tag = mirror.runner.run(
        [*g, "describe", "--tags", "--abbrev=0", "--match", "v*", sha], check=False
    ).stdout.strip()
    subjects = mirror.runner.run(
        [*g, "log", "--format=%h %s", "-5", sha, "--not", "--tags", "--branches"], check=False
    ).stdout.splitlines()
    return tag, int(own), subjects


class HopsAnalyzer:
    name: ClassVar[str] = "hops"
    kind: ClassVar[Literal["workspace"]] = "workspace"

    def analyze(self, ctx: PlanContext) -> AnalyzerResult:
        data = release_data(ctx.release_data_path)
        mirror = ctx.old.resolved.mirror
        old_v, _ = ref_version(mirror, ctx.old.resolved.ref.sha)
        # A development snapshot (PATCHLEVEL 99) of X.Y has crossed releases up to X.Y.
        new_v, _ = ref_version(mirror, ctx.new.resolved.ref.sha)
        tags = release_tags(mirror)
        crossed = sorted(m for m in tags if old_v < m <= new_v and any(p == 0 for p, _ in tags[m]))
        lts_info = derive_lts(mirror, ctx.new.resolved.ref.sha, crossed, tags, data)
        lts = {m for m, info in lts_info.items() if not info.planned}
        boundaries = {_minor(k): v for k, v in data["boundaries"].items()}
        findings: list[Finding] = []
        old_spec, new_spec = ctx.old.resolved.ref.label, ctx.new.resolved.ref.label
        forks = {}
        for label, sha in (
            (old_spec, ctx.old.resolved.ref.sha),
            (new_spec, ctx.new.resolved.ref.sha),
        ):
            if (f := fork_of(mirror, sha)) is not None:
                forks[label] = f
                findings.append(
                    Finding(
                        category=Category.HOP,
                        kind="ref-is-fork",
                        subject=label,
                        title=f"{label} is not an upstream Zephyr commit: {f[1]} commits on top "
                        f"of {f[0] or 'an upstream commit'} that no upstream tag or branch "
                        "contains",
                        severity=Severity.INFO,
                        confidence=Confidence.HIGH,
                        action="Release notes and migration guides describe upstream; review the "
                        "fork's own commits too, and carry them to the target.",
                        analyzer=self.name,
                        scope=ctx.scope,
                        new={"base_tag": f[0], "own_commits": f[1], "latest": f[2]},
                    )
                )

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
                    why.append(
                        f"{lts_info[m].label} long-term support release (per {lts_info[m].source})"
                    )
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
                    "passing tests before the next, so every step is a bisectable state."
                    + (
                        " The steps name upstream tags, but this upgrade is between fork "
                        f"commits ({', '.join(forks)}): use your fork's equivalent of each step, "
                        "if it has one."
                        if forks
                        else ""
                    ),
                    analyzer=self.name,
                    scope=ctx.scope,
                    new={
                        "path": path,
                        "reasons": reasons,
                        "crossed": [_fmt(m) for m in crossed],
                        "release_data": data["_source"],
                    },
                    heuristics=tuple(
                        sorted(
                            {
                                HEURISTIC
                                if lts_info[m].source == data["_source"]
                                else "hops.lts-from-release-docs"
                                for m in hops
                                if m in lts
                            }
                            | ({HEURISTIC} if any(m in boundaries for m in hops) else set())
                            | (
                                {"hops.step-size"}
                                if any(m not in lts and m not in boundaries for m in hops)
                                else set()
                            )
                        )
                    ),
                )
            )
        return AnalyzerResult(findings)
