"""Diff of upstream Zephyr's west.yml between the two refs, scoped to the user's workspace."""

from __future__ import annotations

import re
from typing import ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.findings import JSON, Category, Confidence, Finding, Severity, artifact_diff, file_line
from zmig.manifest import ProjectInfo, UserProject
from zmig.plan import PlanContext

FIELDS = ("revision", "url", "path", "groups")


def _declaration_line(ctx: PlanContext, name: str) -> JSON | None:
    """file_line evidence for `- name: <name>` in the user's manifest file, if found."""
    if ctx.user is None:
        return None
    repo = ctx.user.manifest_repo.path
    path = repo / "west.yml"
    if not path.is_file():
        return None
    pat = re.compile(rf"^\s*-\s*name:\s*{re.escape(name)}\s*$")
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if pat.match(line):
            return file_line(ctx.rel(path), i, line.strip())
    return None


def _present(up: UserProject | None) -> bool:
    return up is not None and up.presence != "missing"


def _diverged(up: UserProject, old: ProjectInfo) -> bool:
    """The user's checkout differs from what the old manifest pins."""
    if up.presence == "tree":
        return True  # unknowable without hashing; the forks analyzer checks content
    if up.dirty:
        return True
    return bool(re.fullmatch(r"[0-9a-f]{40}", old.revision)) and up.head != old.revision


class ManifestAnalyzer:
    name: ClassVar[str] = "manifest"
    kind: ClassVar[Literal["workspace"]] = "workspace"

    def analyze(self, ctx: PlanContext) -> AnalyzerResult:
        old, new = ctx.old_manifest, ctx.new_manifest
        old_ref, new_ref = ctx.old.resolved.ref.spec, ctx.new.resolved.ref.spec
        user = ctx.user.projects if ctx.user else {}
        findings: list[Finding] = []
        others: dict[str, list[str]] = {"added": [], "removed": [], "changed": []}

        def emit(
            kind: str,
            name: str,
            title: str,
            sev: Severity,
            action: str,
            o: ProjectInfo | None,
            n: ProjectInfo | None,
        ) -> None:
            ev: list[JSON] = [
                artifact_diff(
                    "west.yml",
                    old_ref,
                    new_ref,
                    o.to_json() if o else None,
                    n.to_json() if n else None,
                )
            ]
            if (decl := _declaration_line(ctx, name)) is not None:
                ev.insert(0, decl)
            findings.append(
                Finding(
                    category=Category.MANIFEST,
                    kind=kind,
                    subject=name,
                    title=title,
                    severity=sev,
                    confidence=Confidence.HIGH,
                    action=action,
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=tuple(ev),
                    old=o.to_json() if o else None,
                    new=n.to_json() if n else None,
                )
            )

        for name in sorted(old.keys() | new.keys()):
            o, n = old.get(name), new.get(name)
            up = user.get(name)
            scoped = ctx.user is None or _present(up)
            if o is None and n is not None:
                if not scoped:
                    others["added"].append(name)
                    continue
                emit(
                    "project-added",
                    name,
                    f"Module {name} is new in {new_ref}",
                    Severity.INFO,
                    f"Clone it with `west update {name}` after upgrading.",
                    None,
                    n,
                )
            elif n is None and o is not None:
                if not scoped:
                    others["removed"].append(name)
                    continue
                sev = Severity.RISKY if _present(up) else Severity.INFO
                emit(
                    "project-removed",
                    name,
                    f"Module {name} was removed from upstream's manifest in {new_ref}",
                    sev,
                    f"Find out what replaces {name} (see the migration guides) or "
                    f"pin it in your own manifest if you still need it.",
                    o,
                    None,
                )
            elif o is not None and n is not None and o != n:
                changed = [f for f in FIELDS if getattr(o, f) != getattr(n, f)]
                if not scoped:
                    others["changed"].append(name)
                    continue
                diverged = up is not None and _present(up) and _diverged(up, o)
                sev = Severity.RISKY if diverged else Severity.INFO
                what = ", ".join(changed)
                action = (
                    f"Your {name} checkout differs from the pinned {o.revision[:12]}; "
                    f"see the fork findings before moving it to {n.revision[:12]}."
                    if diverged
                    else f"`west update {name}` moves it to the new pin."
                )
                emit("project-changed", name, f"Module {name}: {what} changed", sev, action, o, n)

        for kind, names in others.items():
            if names:
                findings.append(
                    Finding(
                        category=Category.MANIFEST,
                        kind=f"other-projects-{kind}",
                        subject=f"{len(names)} projects",
                        severity=Severity.INFO,
                        confidence=Confidence.HIGH,
                        analyzer=self.name,
                        scope=ctx.scope,
                        title=f"{len(names)} projects not in your workspace were {kind}",
                        action="None needed unless you start using them.",
                        new=sorted(names),
                    )
                )
        return AnalyzerResult(findings)
