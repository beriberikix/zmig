"""The target build itself: configure or compile failures, new warnings, and ROM/RAM.

Failures quote Zephyr's and the compiler's own messages. Warnings are compared by
identity (source, path relative to the zephyr tree / app / build dir, message), not by
line, so a warning that only moved isn't new. Sizes come from the two ELFs.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact, BuildContext, is_host_board, project_path, project_roots
from zmig.diagnostics import BuildWarning, app_relative, compiler_sites, parse_log, parse_warnings
from zmig.errors import ArtifactMissing
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
from zmig.snapshots import kconfig_symbols

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
    # The project code behind an error reported inside a header (see BuildWarning.sites).
    sites: tuple[tuple[str, int], ...] = ()


_LINKER = re.compile(
    r"^.*?\bld(?:\.bfd|\.lld)?: (?P<msg>.*(?:overflowed|undefined reference|"
    r"cannot find|multiple definition).*)$",
    re.M,
)
# A link error the linker attributes to a source line, printed with or without the "ld:"
# prefix (regression: CANnectivity's "…/led.c:225: undefined reference to
# `smf_set_handled'" became "1 error (step)", docs/DOGFOOD-4-baseline.md M14).
_LINKER_AT = re.compile(
    r"^(?:.*?\bld(?:\.bfd|\.lld)?: )?(?P<file>[^\s:]+\.(?:c|cc|cpp|S|s|h)):(?P<line>\d+): "
    r"(?P<msg>(?:undefined reference|multiple definition).*)$",
    re.M,
)
_FAILED = re.compile(r"^FAILED: (?:\[code=\d+\] )?(?P<target>\S+)", re.M)
_IMAGE = re.compile(r"/builds/[0-9a-f]+/(?P<image>[\w.-]+)/zephyr/")


# The first real configure error, not sysbuild's "CMake configure failed for Zephyr
# project: app" wrapper (regression: OpenDeck's target hid "devicetree error: ... undefined
# node label 'eth_rxd0_pc4'" behind it, DOGFOOD-5 M18).
_CONFIGURE_ERROR = re.compile(
    r"^(?:devicetree error: |.*kconfig(?:\.py)?: .*error: |error: |CMake Error at (?!.*sysbuild))"
)
_SYSBUILD_IMAGE = re.compile(r"CMake configure failed for Zephyr project: (\S+)")


def configure_error(log: str) -> tuple[str | None, list[str]] | None:
    """(image, the first error's lines) of a failed configure; image from sysbuild's
    wrapper message when there is one."""
    lines = log.splitlines()
    image = next((m[1] for ln in lines if (m := _SYSBUILD_IMAGE.search(ln))), None)
    for i, ln in enumerate(lines):
        if _CONFIGURE_ERROR.match(ln):
            block = [ln.strip()]
            for nxt in lines[i + 1 : i + 6]:
                if not nxt.strip() or nxt.startswith(("--", "CMake Error", "Call Stack")):
                    break
                block.append(nxt.strip())
            return image, block
    return None


def build_errors(log: str) -> list[BuildError]:
    """Compiler and linker errors in a build log; ninja's FAILED steps if neither."""
    out: list[BuildError] = []
    lines = log.splitlines()
    for m in _COMPILER_ERROR.finditer(log):
        img = _IMAGE.search(m["file"])
        sites = compiler_sites(lines, log.count("\n", 0, m.start()))
        out.append(
            BuildError(
                "compiler", m["msg"], m["file"], int(m["line"]), img["image"] if img else "", sites
            )
        )
    failed = [m for m in _FAILED.finditer(log)]
    located = {m.start(): m for m in _LINKER_AT.finditer(log)}
    for m in [*_LINKER.finditer(log), *located.values()]:
        if m.re is _LINKER and m.start() in located:
            continue  # the located form of the same line is taken below
        # The linker doesn't name the image; the nearest preceding FAILED step does.
        prev = [f for f in failed if f.start() < m.start()]
        img = _IMAGE.search(log[prev[-1].start() : prev[-1].end() + 300]) if prev else None
        image = img["image"] if img else ""
        if m.re is _LINKER_AT:
            out.append(BuildError("linker", m["msg"].strip(), m["file"], int(m["line"]), image))
        else:
            out.append(BuildError("linker", m["msg"].strip(), image=image))
    if not out:
        for m in failed:
            out.append(BuildError("step", f"FAILED: {m['target']}"))
    return list(dict.fromkeys(out))


# add(kind, subject, title, severity, action, evidence, old, new)
Add = Callable[..., None]
_COMPILER_ERROR = re.compile(
    r"^(?P<file>[^\s:]+):(?P<line>\d+):(?:\d+:)? (?:fatal )?error: (?P<msg>.*)$", re.M
)


def _roots(b: BuildContext, img_dir: Path) -> dict[str, str]:
    return {
        str(b.workspace.zephyr): "<zephyr>",
        str(b.src): "<app>",
        str(b.dir): "<build>",
        str(b.workspace.root): "<workspace>",
        **{r: f"<module:{p}>" for r, p in project_roots(b, img_dir).items() if p},
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

        findings += self._kconfig_definitions(ctx)
        salvaged = " (after salvage)" if ctx.salvage else ""
        log = ctx.new_img.log.read_text()
        if ctx.new_img.status == "configure-failed":
            tail = [
                ln
                for ln in log.splitlines()
                if ln.strip() and not ln.startswith(("$ ", "--", "FATAL ERROR"))
            ][-12:]
            first = configure_error(log)
            cause = ""
            ev_first: tuple[JSON, ...] = ()
            if first is not None:
                image, lines = first
                where = f"{image} image: " if image else ""
                cause = f": {where}{ctx.runner.redact(lines[0])[:160]}"
                ev_first = (
                    tool_output("first configure error", ctx.runner.redact("\n".join(lines))),
                )
            add(
                "target-configure-failed",
                ctx.new_spec,
                f"The target configure fails{salvaged}{cause}",
                Severity.BREAKING,
                "Fix the error in the evidence (often a board or toolchain change), then "
                "rerun; Kconfig and devicetree analysis need a configured target.",
                (*ev_first, tool_output("target configure", ctx.runner.redact("\n".join(tail)))),
            )
            return AnalyzerResult(findings)

        if ctx.new_img.status == "build-failed":
            proj = project_roots(ctx.new, ctx.new_img.dir)
            errors = build_errors(log)
            ev: list[JSON] = []
            for e in errors[:MAX_ERRORS]:
                where = f"{e.image} image: " if e.image else ""
                if own := project_path(e.file, proj):
                    ev.append(file_line(own, e.line, e.message))
                elif site := next(
                    (
                        (rel, ln)
                        for f, ln in e.sites
                        if ln and (rel := project_path(f, proj)) is not None
                    ),
                    None,
                ):
                    # Raised inside a Zephyr header: show the project line that led there
                    # (regression: A1-native's '__device_dts_ord_20' undeclared in
                    # device.h:96, docs/DOGFOOD-3-baseline.md M9).
                    raised = ctx.runner.redact(f"{e.file}:{e.line}")
                    ev.append(file_line(site[0], site[1], f"{e.message} (raised in {raised})"))
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
        old_roots, new_roots = _roots(ctx.old, ctx.old_img.dir), _roots(ctx.new, ctx.new_img.dir)
        old_keys = {w.key(old_roots) for w in old_w}
        new_keys = {w.key(new_roots) for w in new_w}
        # One finding per new warning; a warning raised in a header once per call site
        # (SYS_INIT in every file) keeps all of them.
        fresh: dict[tuple[str, str, str], list[BuildWarning]] = {}
        for w in new_w:
            k = w.key(new_roots)
            if k not in old_keys:
                fresh.setdefault(k, []).append(w)
        proj = project_roots(ctx.new, ctx.new_img.dir)
        # Kconfig's "Deprecated symbol X is enabled." is the kconfig analyzer's
        # symbol-deprecated finding, which has the assignment's file:line (regression:
        # reported twice on A1-native, docs/DOGFOOD-3-baseline.md M10).
        deprecated = {
            f.subject.removeprefix("CONFIG_")
            for f in ctx.prior
            if f.category == Category.KCONFIG and f.kind == "symbol-deprecated"
        }
        for (source, path, msg), ws in sorted(fresh.items()):
            dep = re.match(r"Deprecated symbol (\w+) is enabled", msg)
            if source == "kconfig" and dep and dep[1] in deprecated:
                continue
            lines: dict[tuple[str, int], None] = {}  # project file:line sites, in order
            files: dict[str, None] = {}  # project files without a line (enclosing function)
            for w in ws:
                own = project_path(w.file, proj) if w.file else None
                if own:
                    lines[(own, w.line)] = None
                    continue
                for f, ln in w.sites:
                    rel = project_path(f, proj)
                    if rel and ln:
                        lines[(rel, ln)] = None
                        break
                    if rel and not ln:
                        files[rel] = None
                        break
            raised = ctx.runner.redact(f"{path}: {msg}" if path else msg)
            ev: tuple[JSON, ...] = tuple(file_line(f, ln, msg) for f, ln in lines) + tuple(
                tool_output(f"target build ({source})", f"in {f}: {raised}") for f in files
            )
            attributed = bool(lines or files)
            if not ev:
                ev = (tool_output(f"target build ({source})", raised),)
            elif not any(project_path(w.file, proj) for w in ws if w.file):
                # Reported inside a Zephyr or module header: say where it was raised.
                ev += (tool_output(f"target build ({source}), raised at", raised),)
            deprecation = "deprecat" in msg.lower()
            new: dict[str, object] = {"file": path}
            if ws[0].flag:
                new["flag"] = ws[0].flag
            if attributed:
                new["sites"] = [f"{f}:{ln}" for f, ln in lines] + list(files)
            add(
                "new-warning",
                f"{source}:{path}:{msg}"[:200],
                f"New {source} warning at {ctx.new_spec}: {msg[:140]}",
                Severity.RISKY if attributed or deprecation else Severity.INFO,
                "Look at it before it becomes an error in a later release."
                if deprecation
                else "Check whether it points at a real problem.",
                ev,
                None,
                new,
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

    def _kconfig_definitions(self, ctx: ScanContext) -> list[Finding]:
        """Kconfig entries in the app's own files that the target tree rejects (from the
        target configure as the user wrote it; reported whether or not salvage worked).

        kconfig-definition   an app override of a symbol or choice the target no longer
                             defines ("defined without a type", a choice default that isn't
                             in the choice)
        kconfig-loop         a dependency loop through a symbol the app defines and the
                             target tree now defines too: a name collision, when the source
                             build's Kconfig had it from the app only
        """
        raw = ctx.new_raw_img or ctx.new_img
        if raw is None or raw.status != "configure-failed" or not raw.log.is_file():
            return []
        # Locations name the app copy each configure used: the raw build's or, for later
        # salvage rounds, the salvaged one.
        copies = [ctx.new_raw.src, ctx.new.src]

        # Each salvage round builds a new copy (apps/<content hash>/<app>), so intermediate
        # rounds name copies that are neither of these: match any copy of this app.
        copy_re = re.compile(rf"/apps/[0-9a-f]+/{re.escape(ctx.new_raw.src.name)}/(?P<rel>.+)$")

        def rel(path: str) -> tuple[str, ...]:
            for c in copies:
                if r := app_relative((path,), c):
                    return r
            m = copy_re.search(path)
            return (m["rel"],) if m else ()

        # The first configure stops at the first error class (e.g. a dependency loop);
        # later salvage rounds find the rest, and each salvage edit keeps its message.
        text = raw.log.read_text() + "\n\n" + "\n\n".join(e.reason for e in ctx.salvage)
        diags = parse_log(text).diagnostics
        out: list[Finding] = []
        seen: set[tuple[str, str]] = set()
        old_syms: dict[str, Any] | None = None
        if ctx.old_img is not None and ctx.old_img.has(Artifact.CONFIG):
            try:
                old_syms = kconfig_symbols(ctx.old_img, ctx.old.workspace, ctx.runner)
            except ArtifactMissing:
                old_syms = None
        for d in diags:
            if d.kind not in ("kconfig-definition", "kconfig-loop") or (d.kind, d.symbol) in seen:
                continue
            seen.add((d.kind, d.symbol))
            mine = [x for x in d.locations if rel(x.rpartition(":")[0])]
            if not mine:
                continue
            ev = tuple(
                file_line(
                    rel(x.rpartition(":")[0])[0],
                    int(x.rpartition(":")[2]),
                    f"{'choice' if 'choice' in d.message else 'config'} {d.symbol}",
                )
                for x in mine[:6]
            )
            where = ", ".join(
                f"{rel(x.rpartition(':')[0])[0]}:{x.rpartition(':')[2]}" for x in mine[:3]
            ) + (f" (+{len(mine) - 3})" if len(mine) > 3 else "")
            if d.kind == "kconfig-definition":
                gone = d.value or d.symbol
                out.append(
                    Finding(
                        category=Category.BUILD,
                        kind="kconfig-override-orphaned",
                        subject=f"CONFIG_{d.symbol}",
                        title=f"The app overrides CONFIG_{d.symbol} ({where}), but "
                        f"{'its default ' + d.value if d.value else 'the symbol'} is not "
                        f"defined at {ctx.new_spec}; Kconfig rejects the configure",
                        severity=Severity.BREAKING,
                        confidence=Confidence.HIGH,
                        action=f"Find what replaced CONFIG_{gone} at the target (Kconfig "
                        "findings and migration guide entries name candidates) and update or "
                        "remove the override.",
                        analyzer=self.name,
                        scope=ctx.scope,
                        evidence=ev,
                        new={"message": d.message, "locations": list(mine)},
                    )
                )
                continue
            # Loop: is this the app's name newly defined upstream?
            upstream = [x for x in d.locations if x not in mine]
            if not upstream:
                continue
            heur: tuple[str, ...]
            if old_syms is not None:
                before = [
                    loc
                    for loc in (old_syms.get(d.symbol) or {}).get("locations", [])
                    if not app_relative(
                        ((ctx.old.workspace.zephyr / loc.rpartition(":")[0]).as_posix(),),
                        ctx.old.src,
                    )
                ]
                if before:
                    continue  # the tree defined it at the source too: a normal override
                conf, heur = Confidence.HIGH, ()
            else:
                conf, heur = Confidence.MEDIUM, ("buildlog.collision-without-source-snapshot",)
            out.append(
                Finding(
                    category=Category.BUILD,
                    kind="kconfig-name-collision",
                    subject=f"CONFIG_{d.symbol}",
                    title=f"CONFIG_{d.symbol} is defined by the app ({where}) and, from "
                    f"{ctx.new_spec} on, by Zephyr ({', '.join(upstream[:2])}); the two "
                    "definitions form a dependency loop",
                    severity=Severity.BREAKING,
                    confidence=conf,
                    action="Rename the app's symbol (and the devicetree compatible if it has "
                    "one), or switch to the upstream driver.",
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=ev,
                    heuristics=heur,
                    new={"app": list(mine), "upstream": upstream, "message": d.message},
                )
            )
        return out
