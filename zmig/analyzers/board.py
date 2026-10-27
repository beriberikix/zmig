"""Board name changes and the app's board-specific input files."""

from __future__ import annotations

from typing import ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact
from zmig.diagnostics import app_relative
from zmig.findings import (
    JSON,
    Category,
    Confidence,
    Finding,
    Severity,
    artifact_diff,
    file_line,
    tool_output,
)
from zmig.scan import ScanContext


class BoardAnalyzer:
    name: ClassVar[str] = "board"
    kind: ClassVar[Literal["build"]] = "build"
    requires: ClassVar[frozenset[Artifact]] = frozenset()

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        findings: list[Finding] = []

        def add(
            kind: str,
            subject: str,
            title: str,
            sev: Severity,
            action: str,
            ev: tuple[JSON, ...],
            old: object = None,
            new: object = None,
        ) -> None:
            findings.append(
                Finding(
                    category=Category.BOARD,
                    kind=kind,
                    subject=subject,
                    title=title,
                    severity=sev,
                    confidence=Confidence.HIGH,
                    action=action,
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=ev,
                    old=old,
                    new=new,
                )
            )

        m = ctx.board_mapping
        if m is not None and m.how == "deprecated.cmake":
            ev: list[JSON] = [
                file_line(
                    f"<zephyr {m.tag}>/boards/deprecated.cmake",
                    m.line,
                    f"set({m.source.split('@')[0]}_DEPRECATED {m.target})",
                )
            ]
            if ctx.new.has(Artifact.CONFIG) and (t := ctx.new_config.get("BOARD_TARGET")):
                ev.append(
                    artifact_diff(
                        "zephyr/.config",
                        ctx.old_spec,
                        ctx.new_spec,
                        ctx.old_config.get("BOARD_TARGET"),
                        t,
                    )
                )
            if m.tag == ctx.new_spec:
                add(
                    "board-renamed",
                    m.source,
                    f"Board {m.source} is a deprecated name in {ctx.new_spec}; it is now "
                    f"{m.target}",
                    Severity.RISKY,
                    f"Use `-b {m.target}` in build scripts and CI. The old name still works "
                    f"through Zephyr's deprecation alias, which upstream removes later.",
                    tuple(ev),
                    m.source,
                    m.target,
                )
            else:
                add(
                    "board-renamed",
                    m.source,
                    f"Board {m.source} no longer exists in {ctx.new_spec}; it was renamed "
                    f"{m.target}",
                    Severity.BREAKING,
                    f"Use `-b {m.target}`. The deprecation alias was last listed in {m.tag} "
                    f"and is gone in {ctx.new_spec}; zmig built the target as {m.target}.",
                    tuple(ev),
                    m.source,
                    m.target,
                )

        for d in ctx.new_raw_facts.diagnostics:
            if d.kind == "board-invalid":
                add(
                    "board-unknown",
                    d.symbol,
                    f"Board {d.symbol} doesn't exist in {ctx.new_spec}",
                    Severity.BREAKING,
                    "Pass the board's name in the target version with --to-board "
                    "(`west boards` at the target lists them).",
                    (tool_output("target configure", ctx.runner.redact(d.message)),),
                )

        # Compare the app files each build applied, only when both configures got far
        # enough to report them (a failed configure lists nothing, which proves nothing).
        if "configure-failed" in (ctx.old.status, ctx.new.status):
            return AnalyzerResult(findings)
        app = ctx.usage.app
        old_inputs = set(app_relative(ctx.old_facts.overlays, app)) | set(
            app_relative(ctx.old_facts.configs, app)
        )
        new_inputs = set(app_relative(ctx.new_facts.overlays, ctx.new.spec.app)) | set(
            app_relative(ctx.new_facts.configs, ctx.new.spec.app)
        )
        applied = artifact_diff(
            "build log: applied app files",
            ctx.old_spec,
            ctx.new_spec,
            sorted(old_inputs),
            sorted(new_inputs),
        )
        for rel in sorted(old_inputs - new_inputs):
            add(
                "input-dropped",
                rel,
                f"{rel} is no longer applied at {ctx.new_spec}",
                Severity.BREAKING,
                f"The {ctx.new_spec} build for {ctx.new.spec.board} doesn't pick up this file. "
                f"Rename it to match the board target (HWMv2 file names use the board target "
                f"with '/' replaced by '_').",
                (file_line(rel, 1), applied),
            )
        for rel in sorted(new_inputs - old_inputs):
            add(
                "input-added",
                rel,
                f"{rel} is applied at {ctx.new_spec} but wasn't at {ctx.old_spec}",
                Severity.RISKY,
                "Check this file's contents are meant for the target build.",
                (file_line(rel, 1), applied),
            )
        return AnalyzerResult(findings)
