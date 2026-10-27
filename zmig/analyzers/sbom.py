"""SBOM diff: modules and source files compiled at each version, and the upstream changes
to them.

The file inventory is what `west spdx` reports each build compiled (zephyr.spdx). Upstream
changes are scoped to that inventory: only commits touching files the target build
compiles are counted. Headers are not in that inventory by default, so the headers the
app includes directly are checked against git separately.
"""

from __future__ import annotations

from collections import Counter
from typing import ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact
from zmig.findings import (
    JSON,
    Category,
    Confidence,
    Finding,
    Severity,
    artifact_diff,
    file_line,
    plural,
)
from zmig.sbom import compiled_sources, read_spdx_dir
from zmig.scan import ScanContext

TOP_FILES = 15


def _norm(module: str) -> str:
    """One spelling for module names: west uses `hal_nordic`, SPDX IDs can't contain '_',
    and package names vary across Zephyr versions."""
    return module.replace("_", "-")


class SbomAnalyzer:
    name: ClassVar[str] = "sbom"
    kind: ClassVar[Literal["build"]] = "build"
    requires: ClassVar[frozenset[Artifact]] = frozenset({Artifact.SPDX})

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        old = {
            _norm(k): v
            for k, v in compiled_sources(
                read_spdx_dir(ctx.old.artifact(Artifact.SPDX).parent)
            ).items()
        }
        new = {
            _norm(k): v
            for k, v in compiled_sources(
                read_spdx_dir(ctx.new.artifact(Artifact.SPDX).parent)
            ).items()
        }
        findings: list[Finding] = []

        def add(
            kind: str,
            subject: str,
            title: str,
            sev: Severity,
            action: str,
            new_v: object = None,
            old_v: object = None,
            ev: tuple[JSON, ...] = (),
        ) -> None:
            findings.append(
                Finding(
                    category=Category.SBOM,
                    kind=kind,
                    subject=subject,
                    title=title,
                    severity=sev,
                    confidence=Confidence.HIGH,
                    action=action,
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=ev,
                    old=old_v,
                    new=new_v,
                )
            )

        old_mods = {_norm(m.name): m.sha for m in ctx.old.workspace.modules(ctx.runner)}
        new_mods = {_norm(m.name): m.sha for m in ctx.new.workspace.modules(ctx.runner)}
        for mod in sorted(old.keys() - new.keys() - {"zephyr"}):
            add(
                "module-dropped",
                mod,
                f"Module {mod} is no longer compiled at {ctx.new_spec}",
                Severity.RISKY,
                "Check whether its functionality moved or must be enabled "
                "differently (Kconfig) at the target.",
                old_v=old_mods.get(mod),
                ev=(
                    artifact_diff(
                        "spdx/zephyr.spdx packages", ctx.old_spec, ctx.new_spec, mod, None
                    ),
                ),
            )
        for mod in sorted(new.keys() - old.keys() - {"zephyr"}):
            add(
                "module-added",
                mod,
                f"Module {mod} is newly compiled at {ctx.new_spec}",
                Severity.INFO,
                "Review its license and footprint.",
                new_v=new_mods.get(mod),
            )
        for mod in sorted((old.keys() & new.keys()) - {"zephyr"}):
            if old_mods.get(mod) != new_mods.get(mod):
                changed = sorted(
                    f for f in old[mod].keys() & new[mod].keys() if old[mod][f] != new[mod][f]
                )
                add(
                    "module-repinned",
                    mod,
                    f"Module {mod} moves from {(old_mods.get(mod) or '?')[:12]} to "
                    f"{(new_mods.get(mod) or '?')[:12]}; "
                    f"{plural(len(changed), 'compiled file')} changed",
                    Severity.RISKY if changed else Severity.INFO,
                    "Read the module's changelog for that range.",
                    old_v=old_mods.get(mod),
                    new_v={
                        "sha": new_mods.get(mod),
                        "changed": changed,
                        "added": sorted(new[mod].keys() - old[mod].keys()),
                        "removed": sorted(old[mod].keys() - new[mod].keys()),
                    },
                )

        oz, nz = old.get("zephyr", {}), new.get("zephyr", {})
        changed = sorted(f for f in oz.keys() & nz.keys() if oz[f] != nz[f])
        added, removed = sorted(nz.keys() - oz.keys()), sorted(oz.keys() - nz.keys())
        commits = self._commits_touching(ctx, [f.removeprefix("zephyr/") for f in nz])
        add(
            "zephyr-sources",
            "zephyr",
            f"Zephyr files compiled: {len(oz)} → {len(nz)} ({len(added)} added, "
            f"{len(removed)} removed, {len(changed)} changed); "
            f"{plural(sum(commits.values()), 'upstream commit')} touch the files the target "
            f"compiles",
            Severity.INFO,
            "The busiest files are listed first; review those against your app's behavior.",
            new_v={
                "added": added,
                "removed": removed,
                "changed": changed,
                "busiest": [{"file": f, "commits": n} for f, n in commits.most_common(TOP_FILES)],
            },
        )

        for inc, locs in sorted(ctx.usage.includes.items()):
            path = f"include/{inc}"
            status = self._header_change(ctx, path)
            if status is None:
                continue
            kind, detail = status
            ev = tuple(file_line(loc.path, loc.line, loc.text) for loc in locs)
            if kind == "removed":
                add(
                    "header-removed",
                    inc,
                    f"<{inc}>, which your code includes, no longer exists in {ctx.new_spec}",
                    Severity.BREAKING,
                    "Find where its declarations moved (see the migration guides).",
                    ev=ev,
                )
            else:
                add(
                    "header-changed",
                    inc,
                    f"<{inc}>, which your code includes, changed upstream ({detail})",
                    Severity.RISKY,
                    "Review the API changes in that header against your calls.",
                    ev=ev,
                    new_v=detail,
                )
        return AnalyzerResult(findings)

    @staticmethod
    def _git(ctx: ScanContext, *args: str) -> tuple[int, str]:
        m = ctx.new.workspace.resolved.mirror
        proc = ctx.runner.run([m.git, "--git-dir", m.path, *args], check=False)
        return proc.returncode, proc.stdout

    def _commits_touching(self, ctx: ScanContext, files: list[str]) -> Counter[str]:
        if not files:
            return Counter()
        o, n = ctx.old.workspace.resolved.ref.sha, ctx.new.workspace.resolved.ref.sha
        _, out = self._git(
            ctx, "log", "--no-merges", "--format=", "--name-only", f"{o}..{n}", "--", *files
        )
        return Counter(line for line in out.splitlines() if line)

    def _header_change(self, ctx: ScanContext, path: str) -> tuple[str, str] | None:
        """('removed'|'changed', detail) for a zephyr header between the refs; None if it
        isn't a zephyr source header or didn't change."""
        o, n = ctx.old.workspace.resolved.ref.sha, ctx.new.workspace.resolved.ref.sha
        if self._git(ctx, "cat-file", "-e", f"{o}:{path}")[0] != 0:
            return None  # not in the zephyr tree at the source (generated, or a module's)
        if self._git(ctx, "cat-file", "-e", f"{n}:{path}")[0] != 0:
            return "removed", ""
        _, stat = self._git(ctx, "diff", "--numstat", o, n, "--", path)
        if not stat.strip():
            return None
        plus, minus, _ = stat.split("\t", 2)
        _, count = self._git(ctx, "rev-list", "--count", "--no-merges", f"{o}..{n}", "--", path)
        return "changed", f"+{plus}/-{minus} lines in {count.strip()} commits"
