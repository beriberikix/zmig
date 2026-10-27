"""Outcome of the target build: configure or compile failures, with Zephyr's own errors.

M5 adds new warnings against the source build, and ROM/RAM deltas.
"""

from __future__ import annotations

import re
from typing import ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact
from zmig.findings import Category, Confidence, Finding, Severity, file_line, tool_output
from zmig.scan import ScanContext

MAX_ERRORS = 5
_COMPILER_ERROR = re.compile(
    r"^(?P<file>[^\s:]+):(?P<line>\d+):(?:\d+:)? error: (?P<msg>.*)$", re.M
)


class BuildlogAnalyzer:
    name: ClassVar[str] = "buildlog"
    kind: ClassVar[Literal["build"]] = "build"
    requires: ClassVar[frozenset[Artifact]] = frozenset()

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        findings: list[Finding] = []
        status = ctx.new.status
        if status == "ok":
            return AnalyzerResult(findings)
        log = ctx.new.log.read_text()
        salvaged = " (after salvage)" if ctx.salvage else ""
        if status == "configure-failed":
            tail = [
                ln
                for ln in log.splitlines()
                if ln.strip() and not ln.startswith(("$ ", "--", "FATAL ERROR"))
            ][-12:]
            findings.append(
                Finding(
                    category=Category.BUILD,
                    kind="target-configure-failed",
                    subject=ctx.new_spec,
                    title=f"The target configure fails{salvaged} and zmig can't compare builds",
                    severity=Severity.BREAKING,
                    confidence=Confidence.HIGH,
                    action="Fix the error in the evidence (often a board or toolchain change), "
                    "then rerun; Kconfig and devicetree analysis need a configured target.",
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=(tool_output("target configure", ctx.runner.redact("\n".join(tail))),),
                )
            )
            return AnalyzerResult(findings)
        app = str(ctx.new.spec.app.resolve())
        errors = list(
            dict.fromkeys(
                (m["file"], int(m["line"]), m["msg"]) for m in _COMPILER_ERROR.finditer(log)
            )
        )
        ev = []
        for f, line, msg in errors[:MAX_ERRORS]:
            if f.startswith(app + "/"):
                ev.append(file_line(f[len(app) + 1 :], line, msg))
            else:
                ev.append(tool_output("target build", ctx.runner.redact(f"{f}:{line}: {msg}")))
        findings.append(
            Finding(
                category=Category.BUILD,
                kind="target-build-failed",
                subject=ctx.new_spec,
                title=f"The target build fails{salvaged} with {len(errors)} compiler error"
                f"{'' if len(errors) == 1 else 's'}",
                severity=Severity.BREAKING,
                confidence=Confidence.HIGH,
                action="The Kconfig and devicetree findings usually explain these errors; fix "
                "those first.",
                analyzer=self.name,
                scope=ctx.scope,
                evidence=tuple(ev),
                new={
                    "errors": [
                        {"file": ctx.runner.redact(f), "line": ln, "message": msg}
                        for f, ln, msg in errors
                    ]
                },
            )
        )
        return AnalyzerResult(findings)
