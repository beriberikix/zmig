"""The target build itself: configure or compile failures, new warnings, and ROM/RAM.

Failures quote Zephyr's and the compiler's own messages. Warnings are compared by
identity (source, path relative to the zephyr tree / app / build dir, message), not by
line, so a warning that only moved isn't new. Sizes come from the two ELFs.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact, BuildContext, is_host_board
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


@dataclass(frozen=True)
class BuildError:
    kind: str  # "compiler" | "linker" | "step"
    message: str
    file: str = ""
    line: int = 0
    image: str = ""  # sysbuild image the error came from, when known


_LINKER = re.compile(
    r"^.*?\bld(?:\.bfd|\.lld)?: (?P<msg>.*(?:overflowed|undefined reference|"
    r"cannot find|multiple definition).*)$",
    re.M,
)
_FAILED = re.compile(r"^FAILED: (?:\[code=\d+\] )?(?P<target>\S+)", re.M)
_IMAGE = re.compile(r"/builds/[0-9a-f]+/(?P<image>[\w.-]+)/zephyr/")


def build_errors(log: str) -> list[BuildError]:
    """Compiler and linker errors in a build log; ninja's FAILED steps if neither."""
    out: list[BuildError] = []
    for m in _COMPILER_ERROR.finditer(log):
        img = _IMAGE.search(m["file"])
        out.append(
            BuildError("compiler", m["msg"], m["file"], int(m["line"]), img["image"] if img else "")
        )
    failed = [m for m in _FAILED.finditer(log)]
    for m in _LINKER.finditer(log):
        # The linker doesn't name the image; the nearest preceding FAILED step does.
        prev = [f for f in failed if f.start() < m.start()]
        img = _IMAGE.search(log[prev[-1].start() : prev[-1].end() + 300]) if prev else None
        out.append(BuildError("linker", m["msg"].strip(), image=img["image"] if img else ""))
    if not out:
        for m in failed:
            out.append(BuildError("step", f"FAILED: {m['target']}"))
    return list(dict.fromkeys(out))


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
            conf: Confidence = Confidence.HIGH,
            heuristics: tuple[str, ...] = (),
        ) -> None:
            findings.append(
                Finding(
                    category=Category.BUILD,
                    kind=kind,
                    subject=subject,
                    title=title,
                    severity=sev,
                    confidence=conf,
                    action=action,
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=ev,
                    old=old,
                    new=new,
                    heuristics=heuristics,
                )
            )

        salvaged = " (after salvage)" if ctx.salvage else ""
        log = ctx.new_img.log.read_text()
        if ctx.new_img.status == "configure-failed":
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

        if ctx.new_img.status == "build-failed":
            app = str(ctx.new.src)
            errors = build_errors(log)
            ev: list[JSON] = []
            for e in errors[:MAX_ERRORS]:
                where = f"{e.image} image: " if e.image else ""
                if e.file.startswith(app + "/"):
                    ev.append(file_line(e.file[len(app) + 1 :], e.line, e.message))
                else:
                    loc = f"{e.file}:{e.line}: " if e.file else ""
                    ev.append(
                        tool_output(
                            f"target build ({e.kind})",
                            ctx.runner.redact(f"{where}{loc}{e.message}"),
                        )
                    )
            kinds = sorted({e.kind for e in errors})
            images = sorted({e.image for e in errors if e.image})
            add(
                "target-build-failed",
                ctx.new_spec,
                f"The target build fails{salvaged}: {plural(len(errors), 'error')}"
                + (f" ({', '.join(kinds)})" if kinds else "")
                + (f" in the {', '.join(images)} image" if images else ""),
                Severity.BREAKING,
                "The Kconfig and devicetree findings often explain these errors; a linker "
                "region overflow means the image no longer fits the board's memory.",
                tuple(ev),
                new={
                    "errors": [
                        {
                            "kind": e.kind,
                            "image": e.image,
                            "file": ctx.runner.redact(e.file),
                            "line": e.line,
                            "message": ctx.runner.redact(e.message),
                        }
                        for e in errors
                    ]
                },
            )

        if ctx.old_img.status == "ok":
            self._warnings(ctx, add)
        if ctx.old_img.has(Artifact.ELF) and ctx.new_img.has(Artifact.ELF):
            self._size(ctx, add)
        return AnalyzerResult(findings)

    @staticmethod
    def _warnings(ctx: ScanContext, add: Add) -> None:
        old_w = parse_warnings(ctx.old_img.log.read_text())
        new_w = parse_warnings(ctx.new_img.log.read_text())
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
        o = footprint(ctx.old_img.artifact(Artifact.ELF))
        n = footprint(ctx.new_img.artifact(Artifact.ELF))
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
        # From the build itself (POSIX arch = runs on the host), not the board's name.
        host = (
            ctx.new_config.get("ARCH_POSIX") == "y"
            if ctx.new_img.has(Artifact.CONFIG)
            else is_host_board(ctx.new.spec.board)
        )
        add(
            "footprint",
            ctx.new.spec.board,
            f"ROM {o.rom} → {n.rom} bytes ({rom_d:+}), RAM {o.ram} → {n.ram} bytes ({ram_d:+})"
            + (" [host-runtime-inflated]" if host else ""),
            Severity.RISKY if risky(o.rom, n.rom) or risky(o.ram, n.ram) else Severity.INFO,
            "The symbols that grew or shrank most are listed; check them against your "
            "memory budget."
            + (
                " This board runs on the host: both ELFs include host-side runtime code and "
                "data, so totals overstate what a device would use. Deltas are still "
                "comparable; absolute numbers are not."
                if host
                else ""
            ),
            (),
            {"rom": o.rom, "ram": o.ram},
            {
                "rom": n.rom,
                "ram": n.ram,
                "host_runtime_inflated": host,
                "symbols": [{"symbol": s, "delta": d} for s, d in movers[:TOP_SYMBOLS]],
            },
            Confidence.MEDIUM if host else Confidence.HIGH,
            ("build.host-runtime-inflated",) if host else (),
        )
