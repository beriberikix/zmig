"""Device and SYS_INIT init order: where the project's own init calls run relative to
everything else, at both refs.

Both orders come from the ELFs, read by each ref's own check_init_priorities.py (see
zmig.snapshots.init_levels). An init call is the project's when the function it calls is
defined in the app or in one of the project's modules (DWARF). A call is identified across
refs by its function and, for a devicetree device, the node path (ordinals renumber).

Reported, for each project init call that exists at both refs: a different init level,
and calls that swapped sides around it (ran before it and now run after, or the reverse).
Those are the changes that break an init that relied on a Zephyr service or device being
ready, as ZMK's behaviors and ext_power did at v3.5 (eval/reports/DOGFOOD-1.md #7, #18).
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact, project_path, project_roots
from zmig.errors import ArtifactMissing
from zmig.findings import (
    JSON,
    Category,
    Checked,
    Confidence,
    Finding,
    NotChecked,
    Severity,
    file_line,
)
from zmig.scan import ScanContext
from zmig.snapshots import init_levels

# Calls listed per swapped direction before the rest are only counted.
MAX_LISTED = 8

Key = tuple[str, str]


def _key(e: dict[str, Any]) -> Key:
    # A device by its node (or device object): from v4.2 the reader lists a device's init
    # function as NULL (it moved into the device), so the function doesn't match across refs.
    if e["node"]:
        return ("device", e["node"])
    if e["arg"].startswith("__device_"):
        return ("device", e["arg"])
    return (e["fn"], e["arg"])


def _label(e: dict[str, Any]) -> str:
    target = e["node"] or (e["arg"] if e["arg"] not in ("NULL", "") else "")
    if e["fn"] == "NULL":
        return f"device {target}"
    return f"{e['fn']}({target})" if target else f"{e['fn']}()"


def run_order(dump: dict[str, Any]) -> list[dict[str, Any]]:
    """Entries in the order the kernel runs them: by level, then position in the level."""
    rank = {lvl: i for i, lvl in enumerate(dump["levels"])}
    return sorted(dump["entries"], key=lambda e: (rank[e["level"]], e["index"]))


def swaps(old: list[Key], new: list[Key], key: Key) -> tuple[list[Key], list[Key]]:
    """Calls (present at both refs) that ran before `key` and now run after it, and the
    reverse."""
    common = set(old) & set(new)
    before_old = set(old[: old.index(key)]) & common
    before_new = set(new[: new.index(key)]) & common
    return sorted(before_old - before_new), sorted(before_new - before_old)


class InitOrderAnalyzer:
    name: ClassVar[str] = "initorder"
    kind: ClassVar[Literal["build"]] = "build"
    requires: ClassVar[frozenset[Artifact]] = frozenset({Artifact.ELF, Artifact.EDT})

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        try:
            od = init_levels(ctx.old_img, ctx.old.workspace, ctx.runner)
            nd = init_levels(ctx.new_img, ctx.new.workspace, ctx.runner)
        except ArtifactMissing as e:
            return AnalyzerResult(
                status="skipped",
                reason=str(e),
                not_checked=[NotChecked("device and SYS_INIT init order", str(e), self.name)],
            )
        old_entries, new_entries = run_order(od), run_order(nd)
        old_keys = [_key(e) for e in old_entries]
        new_keys = [_key(e) for e in new_entries]
        by_old = {_key(e): e for e in old_entries}
        by_new = {_key(e): e for e in new_entries}
        old_proj = project_roots(ctx.old, ctx.old_img.dir)
        new_proj = project_roots(ctx.new, ctx.new_img.dir)

        def site(dump: dict[str, Any], fn: str, roots: dict[str, str]) -> tuple[str, int] | None:
            s = dump["sites"].get(fn)
            if not s:
                return None
            own = project_path(s[0], roots)
            return (own, s[1]) if own else None

        findings: list[Finding] = []

        def add(kind: str, e: dict[str, Any], title: str, sev: Severity, action: str,
                ev: tuple[JSON, ...], old: Any = None, new: Any = None) -> None:  # fmt: skip
            findings.append(
                Finding(
                    category=Category.INIT,
                    kind=kind,
                    subject=_label(e),
                    title=title,
                    severity=sev,
                    confidence=Confidence.MEDIUM,
                    action=action,
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=ev,
                    old=old,
                    new=new,
                )
            )

        def describe(k: Key, side: dict[Key, dict[str, Any]]) -> str:
            e = side[k]
            return f"{_label(e)} [{e['level']}]"

        for k in old_keys:
            if k not in by_new:
                continue
            o, n = by_old[k], by_new[k]
            where = site(nd, n["fn"], new_proj) or site(od, o["fn"], old_proj)
            if where is None:
                continue  # not the project's code
            ev: tuple[JSON, ...] = (file_line(where[0], where[1], _label(o)),)
            if o["level"] != n["level"]:
                add(
                    "level-changed",
                    o,
                    f"{_label(o)} now runs at {n['level']} (was {o['level']})",
                    Severity.RISKY,
                    "Check that what it uses is initialized by then.",
                    ev,
                    old=o["level"],
                    new=n["level"],
                )
            later, earlier = swaps(old_keys, new_keys, k)
            if not later and not earlier:
                continue
            parts = []
            if later:
                parts.append(f"{len(later)} that ran before it now run after it")
            if earlier:
                parts.append(f"{len(earlier)} that ran after it now run before it")
            add(
                "order-changed",
                o,
                f"Init order around {_label(o)} ({n['level']}) changed: " + "; ".join(parts),
                Severity.RISKY,
                "If it uses one of the calls that now run after it, raise its priority or "
                "move it to a later level (see the evidence for the calls that moved).",
                ev,
                new={
                    "now_after": [describe(x, by_new) for x in later[:MAX_LISTED]],
                    "now_before": [describe(x, by_new) for x in earlier[:MAX_LISTED]],
                    "counts": {"now_after": len(later), "now_before": len(earlier)},
                },
            )
        project = sum(
            1
            for k in old_keys
            if k in by_new
            and (site(nd, by_new[k]["fn"], new_proj) or site(od, by_old[k]["fn"], old_proj))
        )
        checked = Checked(
            "device and SYS_INIT init order",
            "found" if findings else "clean",
            f"{project} project init call(s) present at both refs compared, among "
            f"{len(old_keys)} init calls at the source and {len(new_keys)} at the target",
            self.name,
        )
        return AnalyzerResult(findings, checked=[checked])
