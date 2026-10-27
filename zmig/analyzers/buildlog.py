"""The target build itself: configure or compile failures, new warnings, and ROM/RAM.

Failures quote Zephyr's and the compiler's own messages. Warnings are compared by
identity (source, path relative to the zephyr tree / app / build dir, message), not by
line, so a warning that only moved isn't new. Sizes come from the two ELFs.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact, BuildContext
from zmig.diagnostics import BuildWarning, parse_warnings
from zmig.findings import (
    JSON,
    Category,
    Confidence,
    Finding,
    Severity,
    file_line,
    plural,
    tool_output,
)
from zmig.scan import ScanContext
from zmig.size import footprint

MAX_ERRORS = 5
TOP_SYMBOLS = 10
# A size change is risky when it exceeds both of these.
SIZE_RISKY_BYTES = 1024
SIZE_RISKY_FRACTION = 0.05
# add(kind, subject, title, severity, action, evidence, old, new)
Add = Callable[..., None]
_COMPILER_ERROR = re.compile(
    r"^(?P<file>[^\s:]+):(?P<line>\d+):(?:\d+:)? error: (?P<msg>.*)$", re.M
)


def _roots(b: BuildContext) -> dict[str, str]:
    return {
        str(b.workspace.zephyr): "<zephyr>",
        str(b.src): "<app>",
        str(b.dir): "<build>",
        str(b.workspace.root): "<workspace>",
    }


class BuildlogAnalyzer:
    name: ClassVar[str] = "buildlog"
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
            ev: tuple[JSON, ...] = (),
            old: object = None,
            new: object = None,
        ) -> None:
            findings.append(
                Finding(
                    category=Category.BUILD,
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

        salvaged = " (after salvage)" if ctx.salvage else ""
        log = ctx.new.log.read_text()
        if ctx.new.status == "configure-failed":
            tail = [
                ln
                for ln in log.splitlines()
                if ln.strip() and not ln.startswith(("$ ", "--", "FATAL ERROR"))
            ][-12:]
            add(
                "target-configure-failed",
                ctx.new_spec,
                f"The target configure fails{salvaged} and zmig can't compare builds",
                Severity.BREAKING,
                "Fix the error in the evidence (often a board or toolchain change), then "
                "rerun; Kconfig and devicetree analysis need a configured target.",
                (tool_output("target configure", ctx.runner.redact("\n".join(tail))),),
            )
            return AnalyzerResult(findings)

        if ctx.new.status == "build-failed":
            app = str(ctx.new.src)
            errors = list(
                dict.fromkeys(
                    (m["file"], int(m["line"]), m["msg"]) for m in _COMPILER_ERROR.finditer(log)
                )
            )
            ev: list[JSON] = []
            for f, line, msg in errors[:MAX_ERRORS]:
                if f.startswith(app + "/"):
                    ev.append(file_line(f[len(app) + 1 :], line, msg))
                else:
                    ev.append(tool_output("target build", ctx.runner.redact(f"{f}:{line}: {msg}")))
            add(
                "target-build-failed",
                ctx.new_spec,
                f"The target build fails{salvaged} with {plural(len(errors), 'compiler error')}",
                Severity.BREAKING,
                "The Kconfig and devicetree findings usually explain these errors; fix those "
                "first.",
                tuple(ev),
                new={
                    "errors": [
                        {"file": ctx.runner.redact(f), "line": ln, "message": msg}
                        for f, ln, msg in errors
                    ]
                },
            )

        if ctx.old.status == "ok":
            self._warnings(ctx, add)
        if ctx.old.has(Artifact.ELF) and ctx.new.has(Artifact.ELF):
            self._size(ctx, add)
        return AnalyzerResult(findings)

    @staticmethod
    def _warnings(ctx: ScanContext, add: Add) -> None:
        old_w = parse_warnings(ctx.old.log.read_text())
        new_w = parse_warnings(ctx.new.log.read_text())
        old_keys = {w.key(_roots(ctx.old)) for w in old_w}
        new_keys = {w.key(_roots(ctx.new)) for w in new_w}
        fresh: dict[tuple[str, str, str], BuildWarning] = {}
        for w in new_w:
            k = w.key(_roots(ctx.new))
            if k not in old_keys:
                fresh.setdefault(k, w)
        app = str(ctx.new.src) + "/"
        for (source, path, msg), w in sorted(fresh.items()):
            in_app = w.file.startswith(app)
            ev: tuple[JSON, ...] = (
                (file_line(w.file[len(app) :], w.line, msg),)
                if in_app
                else (tool_output(f"target build ({source})", f"{path}: {msg}" if path else msg),)
            )
            deprecation = "deprecat" in msg.lower()
            add(
                "new-warning",
                f"{source}:{path}:{msg}"[:200],
                f"New {source} warning at {ctx.new_spec}: {msg[:140]}",
                Severity.RISKY if in_app or deprecation else Severity.INFO,
                "Look at it before it becomes an error in a later release."
                if deprecation
                else "Check whether it points at a real problem.",
                ev,
                None,
                {"file": path, "flag": w.flag} if w.flag else {"file": path},
            )
        gone = old_keys - new_keys
        if gone:
            add(
                "warnings-resolved",
                "warnings",
                f"Warnings from {ctx.old_spec} that no longer appear: {len(gone)}",
                Severity.INFO,
                "None needed.",
                (),
                None,
                sorted(f"{s}: {m}" for s, _, m in gone),
            )

    @staticmethod
    def _size(ctx: ScanContext, add: Add) -> None:
        o = footprint(ctx.old.artifact(Artifact.ELF))
        n = footprint(ctx.new.artifact(Artifact.ELF))
        deltas = {
            s: n.symbols.get(s, 0) - o.symbols.get(s, 0)
            for s in o.symbols.keys() | n.symbols.keys()
        }
        movers = sorted((d for d in deltas.items() if d[1]), key=lambda kv: (-abs(kv[1]), kv[0]))

        def risky(a: int, b: int) -> bool:
            return abs(b - a) > SIZE_RISKY_BYTES and (
                a == 0 or abs(b - a) / a > SIZE_RISKY_FRACTION
            )

        rom_d, ram_d = n.rom - o.rom, n.ram - o.ram
        add(
            "footprint",
            ctx.new.spec.board,
            f"ROM {o.rom} → {n.rom} bytes ({rom_d:+}), RAM {o.ram} → {n.ram} bytes ({ram_d:+})",
            Severity.RISKY if risky(o.rom, n.rom) or risky(o.ram, n.ram) else Severity.INFO,
            "The symbols that grew or shrank most are listed; check them against your "
            "memory budget."
            + (
                " (Native boards: totals include host-side runtime.)"
                if ctx.new.spec.board.startswith("native")
                else ""
            ),
            (),
            {"rom": o.rom, "ram": o.ram},
            {
                "rom": n.rom,
                "ram": n.ram,
                "symbols": [{"symbol": s, "delta": d} for s, d in movers[:TOP_SYMBOLS]],
            },
        )
