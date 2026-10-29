"""SBOM diff: modules and source files compiled at each version, and the upstream changes
to them.

The file inventory is what `west spdx` reports each build compiled (zephyr.spdx). Upstream
changes are scoped to that inventory: only commits touching files the target build
compiles are counted. Headers are not in that inventory by default, so the headers the
app includes directly are checked against git separately.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, ClassVar, Literal

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
from zmig.sbom import APP_PACKAGE, compiled_sources, read_spdx_dir
from zmig.scan import ScanContext

TOP_FILES = 15


def _norm(module: str) -> str:
    """One spelling for module names: west uses `hal_nordic`, SPDX IDs can't contain '_',
    and package names vary across Zephyr versions."""
    return module.replace("_", "-")


def api_covers(sites: set[str], coverage: dict[str, Any] | None, api_sites: set[str]) -> bool:
    """True if libclang already answered "does this header change affect the project?":
    every file that includes the header is a TU the API analyzer compared cleanly at both
    refs (`coverage["clean_tus"]`) and has no API finding in it (`api_sites`). Includes from
    project headers aren't TUs, so they never qualify."""
    clean = set((coverage or {}).get("clean_tus", ()))
    return bool(sites) and all(s in clean and s not in api_sites for s in sites)


class SbomAnalyzer:
    name: ClassVar[str] = "sbom"
    kind: ClassVar[Literal["build"]] = "build"
    requires: ClassVar[frozenset[Artifact]] = frozenset({Artifact.SPDX})

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        old = {
            _norm(k): v
            for k, v in compiled_sources(
                read_spdx_dir(ctx.old_img.artifact(Artifact.SPDX).parent)
            ).items()
        }
        new = {
            _norm(k): v
            for k, v in compiled_sources(
                read_spdx_dir(ctx.new_img.artifact(Artifact.SPDX).parent)
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
            heuristics: tuple[str, ...] = (),
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
                    heuristics=heuristics,
                )
            )

        # API findings for this image: their use sites are where a header change mattered.
        image = None if ctx.is_app else ctx.image
        api_sites = {
            str(e["path"])
            for f in ctx.prior
            if f.analyzer == "api" and f.image == image and f.severity is not Severity.INFO
            for e in f.evidence
            if e.get("type") == "file_line"
        }
        coverage = ctx.shared.get("api_coverage", {}).get(ctx.image)

        old_mods = {_norm(m.name): m.sha for m in ctx.old.workspace.modules(ctx.runner)}
        new_mods = {_norm(m.name): m.sha for m in ctx.new.workspace.modules(ctx.runner)}
        not_modules = {"zephyr", APP_PACKAGE}
        # Only modules that compile files into this image: a module's package can be
        # present with no files (it's in the workspace, the image doesn't use it).
        old = {k: v for k, v in old.items() if v or k in not_modules}
        new = {k: v for k, v in new.items() if v or k in not_modules}
        for mod in sorted(old.keys() - new.keys() - not_modules):
            add(
                "module-dropped",
                mod,
                f"Module {mod} is no longer compiled into {ctx.image} at {ctx.new_spec}",
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
        for mod in sorted(new.keys() - old.keys() - not_modules):
            add(
                "module-added",
                mod,
                f"Module {mod} is newly compiled into {ctx.image} at {ctx.new_spec}",
                Severity.INFO,
                "Review its license and footprint.",
                new_v=new_mods.get(mod),
            )
        for mod in sorted((old.keys() & new.keys()) - not_modules):
            if old_mods.get(mod) != new_mods.get(mod):
                changed = sorted(
                    f for f in old[mod].keys() & new[mod].keys() if old[mod][f] != new[mod][f]
                )
                added = sorted(new[mod].keys() - old[mod].keys())
                removed = sorted(old[mod].keys() - new[mod].keys())
                n = len(changed) + len(added) + len(removed)
                add(
                    "module-repinned",
                    mod,
                    f"Module {mod} moves from {(old_mods.get(mod) or '?')[:12]} to "
                    f"{(new_mods.get(mod) or '?')[:12]}; {plural(n, 'file')} compiled into "
                    f"{ctx.image} changed ({len(changed)} modified, {len(added)} added, "
                    f"{len(removed)} removed)",
                    Severity.RISKY if n else Severity.INFO,
                    "Read the module's changelog for that range.",
                    old_v=old_mods.get(mod),
                    new_v={
                        "sha": new_mods.get(mod),
                        "changed": changed,
                        "added": added,
                        "removed": removed,
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
            # Only the includes this build compiles: an include in a file that's built for
            # another board or shield says nothing about this build (regression:
            # bms-firmware's oled.c, docs/DOGFOOD-3-baseline.md).
            built = ctx.build_files
            used = [loc for loc in locs if built is None or loc.path in built]
            outside = "" if used else " (only in files this build doesn't compile)"
            ev = tuple(file_line(loc.path, loc.line, loc.text) for loc in (used or locs))
            if kind == "removed":
                add(
                    "header-removed",
                    inc,
                    f"<{inc}>, which your code includes, no longer exists in {ctx.new_spec}"
                    + outside,
                    Severity.BREAKING if used else Severity.INFO,
                    "Find where its declarations moved (see the migration guides).",
                    ev=ev,
                )
            elif used and api_covers({loc.path for loc in used}, coverage, api_sites):
                # A byte diff isn't a risk when libclang compared what the project uses from
                # it and found nothing changed (AUDIT.md F8; bms A1 precision 50% -> 11%,
                # docs/EVALUATION.md).
                add(
                    "header-changed",
                    inc,
                    f"<{inc}>, which your code includes, changed upstream ({detail}); none of "
                    f"the declarations your code uses from it changed",
                    Severity.INFO,
                    "libclang compared the declarations your code uses at both versions; "
                    "review the header only for behavior changes behind unchanged signatures.",
                    ev=ev,
                    new_v=detail,
                    heuristics=("sbom.api-covered-no-change",),
                )
            else:
                add(
                    "header-changed",
                    inc,
                    f"<{inc}>, which your code includes, changed upstream ({detail})" + outside,
                    Severity.RISKY if used else Severity.INFO,
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
