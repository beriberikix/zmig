"""The target build itself: configure or compile failures, new warnings, and ROM/RAM.

Failures quote Zephyr's and the compiler's own messages. Warnings are compared by
identity (source, path relative to the zephyr tree / app / build dir, message), not by
line, so a warning that only moved isn't new. Sizes come from the two ELFs.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact, BuildContext, is_host_board, project_path, project_roots
from zmig.diagnostics import (
    BuildWarning,
    LogFacts,
    app_relative,
    compiler_sites,
    parse_log,
    parse_warnings,
)
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
from zmig.snapshots import build_dependencies, kconfig_symbols
from zmig.tools import find_tool

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


_AT_LINE = re.compile(r"(/[^\s:]+):(\d+)(?::\d+)?:")
_DT_UNDEFINED_LABEL = re.compile(r"devicetree error: (\S+): undefined node label '([^']+)'")
_DTC_ANNOTATION = re.compile(r"/\* (\S+?):(\d+):\d+-\S* \*/")


def configure_locations(
    lines: list[str], img_dir: Path, roots: dict[str, str], runner: Any
) -> list[tuple[str, int, str]]:
    """The project's own file and line behind a configure error, from the tools' own
    output, never by searching the sources:
    - errors that carry "<file>:<line>:" (Kconfig, CMake, conf warnings);
    - devicetree "undefined node label" errors, which name only the node: the devicetree
      compiler, run with source annotations (`dtc -T -T`) on the image's preprocessed
      zephyr.dts.pre, names the file and line of the property that references the label
      (regression: OpenDeck's firmware.overlay:20, DOGFOOD-5 D1)."""
    out: list[tuple[str, int, str]] = []
    for ln in lines:
        for m in _AT_LINE.finditer(ln):
            if (own := project_path(m[1], roots)) is not None:
                out.append((own, int(m[2]), ln.strip()[:200]))
    dt = next((d for ln in lines if (d := _DT_UNDEFINED_LABEL.search(ln))), None)
    pre = img_dir / "zephyr" / "zephyr.dts.pre"
    if dt is not None and pre.is_file():
        label = re.compile(rf"&{re.escape(dt[2])}\b")
        proc = runner.run(
            [find_tool("dtc"), "-f", "-q", "-T", "-T", "-I", "dts", "-O", "dts", str(pre)],
            check=False,
        )
        for line in proc.stdout.splitlines():
            a = _DTC_ANNOTATION.search(line) if label.search(line) else None
            own = project_path(a[1], roots) if a else None
            if a and own is not None:
                prop = line.strip().split(" = ", 1)[0]
                out.append((own, int(a[2]), f"{prop} references undefined label '{dt[2]}'"))
    unique: list[tuple[str, int, str]] = []
    for x in out:
        if all((x[0], x[1]) != (y[0], y[1]) for y in unique):
            unique.append(x)
    return unique


def build_errors(log: str) -> list[BuildError]:
    """Compiler and linker errors in a build log, compiler errors first, each kind by file
    and line; ninja's FAILED steps if neither. Not in log order: a parallel build prints
    files in any order, and findings cite the first few (regression: the cited sites
    changed after a rebuild, DOGFOOD-12 E8)."""
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
    rank = {"compiler": 0, "linker": 1, "step": 2}
    return sorted(dict.fromkeys(out), key=lambda e: (rank[e.kind], e.file, e.line, e.message))


def error_evidence(
    errors: list[BuildError], proj: dict[str, str], redact: Callable[[str], str]
) -> list[JSON]:
    """Evidence for the first MAX_ERRORS distinct build errors: at the project line, or the
    project line that led into a Zephyr header, else the tool's message. The same error
    printed once per file that includes a header is one site (regression: five copies of
    ZMK's `behavior.h:276` hid `ext_power.h:104` once errors were ordered by file,
    DOGFOOD-12 E8)."""
    ev: list[JSON] = []
    for e in errors:
        where = f"{e.image} image: " if e.image else ""
        if own := project_path(e.file, proj):
            item = file_line(own, e.line, e.message)
        elif site := next(
            ((rel, ln) for f, ln in e.sites if ln and (rel := project_path(f, proj)) is not None),
            None,
        ):
            # Raised inside a Zephyr header: show the project line that led there
            # (regression: A1-native's '__device_dts_ord_20' undeclared in device.h:96,
            # docs/DOGFOOD-3-baseline.md M9).
            raised = redact(f"{e.file}:{e.line}")
            item = file_line(site[0], site[1], f"{e.message} (raised in {raised})")
        else:
            loc = f"{e.file}:{e.line}: " if e.file else ""
            item = tool_output(f"target build ({e.kind})", redact(f"{where}{loc}{e.message}"))
        if item not in ev:
            ev.append(item)
        if len(ev) == MAX_ERRORS:
            break
    return ev


_UNDECLARED = re.compile(
    r"implicit declaration of function '(\w+)'|'(\w+)' undeclared|"
    r"type defaults to 'int' in declaration of '(\w+)'|unknown type name '(\w+)'"
)
_INCLUDE_LINE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')


@dataclass(frozen=True)
class MissingInclude:
    """A header a project file must now include itself: it declares symbols the target
    build reports undeclared, and the source build reached it only through other headers."""

    file: str  # as evidence shows it
    line: int  # the file's last #include line (0: none)
    text: str  # that line
    header: str  # include path, e.g. zephyr/logging/log.h
    symbols: tuple[str, ...]


def _declares(text: str, sym: str) -> bool:
    """Whether a header declares `sym`: a #define, or a line declaring or defining it with
    a type before it (not a call: `ret = sym(`, `return sym(`)."""
    s = re.escape(sym)
    return bool(
        re.search(rf"(?m)^\s*#\s*define\s+{s}\b", text)
        or re.search(rf"(?m)^[ \t]*(?!return\b)(?:[A-Za-z_]\w*[ \t*]+)+\**{s}\s*\(", text)
    )


def _include_name(path: str) -> str | None:
    """How a header is included: the part after its last include/ directory."""
    norm = path.replace(os.sep, "/")
    i = norm.rfind("/include/")
    return norm[i + len("/include/") :] if i >= 0 else None


def missing_includes(
    errors: list[BuildError],
    src_deps: dict[str, list[str]],
    old_roots: dict[str, str],
    new_roots: dict[str, str],
) -> list[MissingInclude]:
    """For a target build's "undeclared" errors in project files: the header, in that
    file's include chain at the source (ninja's records), that declares the symbols, when
    the file doesn't include it itself. It got them through another header, which no longer
    provides them (regression: ble-copro's socket()/LOG_ERR(), whose zephyr/posix and
    logging/log.h stopped coming through net/socket.h and net_core.h, DOGFOOD-12 E4)."""
    from zmig.usage import SOURCE_SUFFIXES

    wanted: dict[str, set[str]] = {}
    for e in errors:
        if e.kind != "compiler" or not (m := _UNDECLARED.search(e.message)):
            continue
        if shown := project_path(os.path.normpath(e.file), new_roots):
            wanted.setdefault(shown, set()).add(next(g for g in m.groups() if g))
    chains: dict[str, tuple[str, list[str]]] = {}
    for files in src_deps.values():
        src = next((f for f in files if f.endswith(SOURCE_SUFFIXES)), None)
        if src and (shown := project_path(os.path.normpath(src), old_roots)):
            chains.setdefault(shown, (src, [f for f in files if f != src]))
    texts: dict[str, str] = {}

    def read(path: str) -> str:
        if path not in texts:
            try:
                texts[path] = Path(path).read_text(errors="replace")
            except OSError:
                texts[path] = ""
        return texts[path]

    out = []
    for shown, syms in sorted(wanted.items()):
        if shown not in chains:
            continue
        src, headers = chains[shown]
        lines = read(src).splitlines()
        direct = {m[1] for ln in lines if (m := _INCLUDE_LINE.match(ln))}
        last = max((i for i, ln in enumerate(lines, 1) if _INCLUDE_LINE.match(ln)), default=0)
        providers: dict[str, set[str]] = {}
        for sym in sorted(syms):
            names = {n for h in headers if (n := _include_name(h)) and _declares(read(h), sym)}
            if names and not names & direct:
                providers[sym] = names
        by_header: dict[str, set[str]] = {}
        for sym, names in providers.items():
            # The header that provides the most of this file's missing symbols, then the
            # shortest include path.
            best = min(names, key=lambda n: (-sum(n in v for v in providers.values()), len(n), n))
            by_header.setdefault(best, set()).add(sym)
        text = lines[last - 1].strip() if last else ""
        out += [
            MissingInclude(shown, last, text, h, tuple(sorted(by_header[h])))
            for h in sorted(by_header)
        ]
    return out


def kconfig_abort_causes(log: str, roots: dict[str, str]) -> list[tuple[str, int, str]]:
    """The assignments to undefined symbols behind "Aborting due to Kconfig warnings", as
    (project file, line, message); ("", 0, message with its path) when the file isn't the
    project's. The abort line itself names none of them (regression: RIT FSW's
    `sensor_module_defconfig:33` TMP116, DOGFOOD-8 B4)."""
    if "Aborting due to Kconfig warnings" not in log:
        return []
    out = []
    for d in parse_log(log).diagnostics:
        if d.kind != "kconfig-undefined":
            continue
        msg = f"attempt to assign the value '{d.value}' to the undefined symbol {d.symbol}"
        own = project_path(os.path.normpath(d.file), roots)
        out.append((own, d.line, msg) if own else ("", 0, f"{d.file}:{d.line}: {msg}"))
    return out


_MALFORMED_BOARD = re.compile(r"Malformed board YAML file:\s*(?P<path>\S+?\.ya?ml)\b")
BOARD_SCHEMA = Path("scripts", "schemas", "board-schema.yaml")


def board_yaml_causes(
    log: str, new_zephyr: Path, old_zephyr: Path, roots: dict[str, str]
) -> list[tuple[str, int, str]]:
    """What's wrong with the board.yml behind "Malformed board YAML file", as (project
    file, line, message): the file validated against the target's board schema, as
    list_boards.py does. Zephyr prints only "is not valid under any of the given schemas",
    which doesn't name the missing property (regression: ardep's `full_name`, required for
    a new board at v4.4.0, DOGFOOD-8 A3)."""
    import yaml

    m = _MALFORMED_BOARD.search(log)
    if not m:
        return []
    try:
        import jsonschema  # the `zephyr` extra: Zephyr's list_boards.py needs it too
    except ImportError:
        return []
    path = Path(m["path"])
    try:
        text = path.read_text()
        data = yaml.safe_load(text)
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return []

    def errors(zephyr: Path) -> list[Any] | None:
        try:
            schema = yaml.safe_load((zephyr / BOARD_SCHEMA).read_text())
        except (OSError, yaml.YAMLError):
            return None
        return list(jsonschema.validators.validator_for(schema)(schema).iter_errors(data))

    new_errors, old_errors = errors(new_zephyr), errors(old_zephyr)
    if not new_errors:
        return []
    before = "; the file is valid at the source" if old_errors == [] else ""
    shown = project_path(os.path.normpath(str(path)), roots)
    out = []
    for e in new_errors:
        where = list(e.absolute_path)
        msg, key = _schema_error(e)
        line = _yaml_line(text, [*where, key] if key else where)
        jpath = "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in where)
        full = f"{msg} in {jpath} (the target's board schema{before})"
        out.append((shown, line, full) if shown else ("", 0, f"{path}:{line}: {full}"))
    return out


def _schema_error(e: Any) -> tuple[str, str | None]:
    """A schema error as a sentence, and the key whose line to cite. For a `oneOf` of
    required-key branches, the branch the entry already partly satisfies: `name` without
    `full_name` is a new board missing `full_name`, not one missing `extend`."""
    inst = e.instance if isinstance(e.instance, dict) else {}
    branches = [c for c in e.context or [] if c.validator == "required"]
    if e.validator == "oneOf" and branches:
        best = max(branches, key=lambda c: (len(set(c.validator_value) & inst.keys()),
                                            -len(c.validator_value)))  # fmt: skip
        have = [k for k in best.validator_value if k in inst]
        missing = [k for k in best.validator_value if k not in inst]
        if missing and have:
            names = ", ".join(f"`{k}`" for k in missing)
            return f"add {names}: required next to `{have[0]}`", have[0]
    return str(e.message)[:200], None


def _yaml_line(text: str, where: list[Any]) -> int:
    """1-based line of the YAML node at `where` (mapping keys, sequence indexes), or of the
    deepest one found."""
    import yaml

    try:
        node: Any = yaml.compose(text)
    except yaml.YAMLError:
        return 0
    line = 1
    for step in where:
        if isinstance(node, yaml.MappingNode):
            hit = next(((k, v) for k, v in node.value if k.value == step), None)
            if hit is None:
                break
            line, node = hit[0].start_mark.line + 1, hit[1]
        elif isinstance(node, yaml.SequenceNode) and isinstance(step, int):
            if step >= len(node.value):
                break
            node = node.value[step]
            line = node.start_mark.line + 1
        else:
            break
    return line


def inputs_not_applied(
    old: LogFacts,
    new: LogFacts,
    old_roots: dict[str, str],
    new_roots: dict[str, str],
    target_dts: bool,
) -> list[tuple[str, str]]:
    """The project's own configuration fragments and devicetree overlays the source build
    applied and the unmodified target build doesn't, as (path shown, what). Fragments are
    judged only if the target merged any (it reached Kconfig), overlays only if it
    generated a devicetree (regression: CoffeeCaller's boards/extensions/native_sim/ files,
    silently dropped at v4.4.0, DOGFOOD-10 §4)."""

    def shown(paths: tuple[str, ...], roots: dict[str, str]) -> set[str]:
        return {s for p in paths if (s := project_path(os.path.normpath(p), roots))}

    out = []
    for what, before, after, judged in (
        ("configuration fragment", old.configs, new.configs, bool(new.configs)),
        ("devicetree overlay", old.overlays, new.overlays, target_dts),
    ):
        if judged:
            gone = shown(before, old_roots) - shown(after, new_roots)
            out += [(path, what) for path in sorted(gone)]
    return out


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
        raw = ctx.new_raw_img
        if raw is not None:
            target_dts = raw.has(Artifact.EDT)
            for path, what in inputs_not_applied(
                ctx.old_facts,
                ctx.new_raw_facts,
                project_roots(ctx.old, ctx.old_img.dir),
                project_roots(ctx.new_raw, raw.dir),
                target_dts,
            ):
                verb = "merged" if what == "configuration fragment" else "applied"
                add(
                    "input-not-applied",
                    path,
                    f"{path}, a {what} the {ctx.old_spec} build {verb}, isn't {verb} at "
                    f"{ctx.new_spec}",
                    Severity.RISKY,
                    f"{ctx.new_spec} no longer picks this file up from where it is, so "
                    f"what it sets is silently lost. Apply it explicitly (EXTRA_CONF_FILE or "
                    f"EXTRA_DTC_OVERLAY_FILE), or move it to where {ctx.new_spec} looks for "
                    f"it (for a board's files: the board's directory, or a board variant).",
                    (
                        file_line(path, 1, f"{what} {verb} at {ctx.old_spec}"),
                        tool_output(
                            "configure logs",
                            f"{ctx.old_spec}: {verb} ({what}); {ctx.new_spec}: not {verb}",
                        ),
                    ),
                )
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
            locs: list[tuple[str, int, str]] = []
            if first is not None:
                image, lines = first
                where = f"{image} image: " if image else ""
                cause = f": {where}{ctx.runner.redact(lines[0])[:160]}"
                img_dir = ctx.new_img.dir
                if image and (ctx.new_img.dir.parent / image).is_dir():
                    img_dir = ctx.new_img.dir.parent / image
                locs = configure_locations(
                    lines, img_dir, project_roots(ctx.new, img_dir), ctx.runner
                )
                ev_first = (
                    *(file_line(p, ln, ctx.runner.redact(text)) for p, ln, text in locs),
                    tool_output("first configure error", ctx.runner.redact("\n".join(lines))),
                )
            roots = project_roots(ctx.new, ctx.new_img.dir)
            causes = kconfig_abort_causes(log, roots)
            board = (
                []
                if causes
                else board_yaml_causes(
                    log, ctx.new.workspace.zephyr, ctx.old.workspace.zephyr, roots
                )
            )
            causes = causes or board
            if causes:
                ev_first = (
                    *(
                        file_line(p, ln, msg)
                        if p
                        else tool_output("target configure (Kconfig)", ctx.runner.redact(msg))
                        for p, ln, msg in causes[:MAX_ERRORS]
                    ),
                    *ev_first,
                )
                cause = f": {ctx.runner.redact(causes[0][2])[:160]}"
                locs = [c for c in causes if c[0]] + locs
            at = f"{locs[0][0]}:{locs[0][1]}" if locs else ""
            add(
                "target-configure-failed",
                ctx.new_spec,
                f"The target configure fails{salvaged}{cause}",
                Severity.BREAKING,
                (
                    f"In {at}, {ctx.runner.redact(board[0][2])}, then rerun; "
                    if board and at
                    else f"Fix {at} (see the first configure error), then rerun; "
                    if at
                    else "Fix the error in the evidence (often a board or toolchain change), then "
                    "rerun; "
                )
                + "Kconfig and devicetree analysis need a configured target.",
                (*ev_first, tool_output("target configure", ctx.runner.redact("\n".join(tail)))),
            )
            return AnalyzerResult(findings)

        if ctx.new_img.status == "build-failed":
            proj = project_roots(ctx.new, ctx.new_img.dir)
            errors = build_errors(log)
            ev = error_evidence(errors, proj, ctx.runner.redact)
            # Symbols a header stopped providing: the include to add (DOGFOOD-12 E4).
            src_deps = build_dependencies(ctx.old_img, ctx.runner) or {}
            old_roots = project_roots(ctx.old, ctx.old_img.dir)
            for m in missing_includes(errors, src_deps, old_roots, proj):
                names = ", ".join(m.symbols)
                add(
                    "include-missing",
                    f"{m.file}:{m.header}",
                    f"{m.file} no longer gets {names} at {ctx.new_spec}: it needs "
                    f"#include <{m.header}>",
                    Severity.BREAKING,
                    f"Include <{m.header}> in {m.file} itself. At {ctx.old_spec} it came only "
                    f"through other headers, which no longer include it.",
                    (
                        file_line(m.file, m.line or 1, m.text),
                        tool_output(
                            "source build's include chain (ninja deps)",
                            f"at {ctx.old_spec}, {m.file} reached {m.header}, which declares "
                            f"{names}, without including it",
                        ),
                    ),
                    new={"header": m.header, "symbols": list(m.symbols)},
                    conf=Confidence.MEDIUM,
                    heuristics=("buildlog.source-include-chain",),
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
            lines: dict[tuple[str, int], None] = {}  # project file:line sites
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
            # By file and line, not log order (DOGFOOD-12 E8: a rebuild reordered them).
            lines = dict.fromkeys(sorted(lines))
            files = dict.fromkeys(sorted(files))
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
    def _warning_evidence(ctx: ScanContext, message: str, logs: list[Path]) -> JSON:
        """Kconfig's own warning, with the log and line it is in: the target configure's
        log, or the salvaged build's (later salvage rounds find the rest)."""
        first = message.strip().splitlines()[0].strip() if message.strip() else ""
        for log in logs:
            if not first or not log.is_file():
                continue
            for i, line in enumerate(log.read_text(errors="replace").splitlines(), 1):
                if first in line:
                    return tool_output(
                        "target configure (Kconfig)",
                        ctx.runner.redact(message),
                        ctx.runner.redact(str(log)),
                        i,
                    )
        return tool_output(
            "target configure (Kconfig, a salvage round)", ctx.runner.redact(message)
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
        # Where each message is: the first configure, each salvage round, the final build.
        logs = list(
            dict.fromkeys(
                [
                    raw.log,
                    *(Path(e.log) for e in ctx.salvage if e.log),
                    *([ctx.new_img.log] if ctx.new_img is not raw else []),
                ]
            )
        )
        for d in diags:
            if d.kind not in ("kconfig-definition", "kconfig-loop") or (d.kind, d.symbol) in seen:
                continue
            seen.add((d.kind, d.symbol))
            mine = [x for x in d.locations if rel(x.rpartition(":")[0])]
            if not mine:
                continue
            warning = self._warning_evidence(ctx, d.message, logs)
            ev = (
                *(
                    file_line(
                        rel(x.rpartition(":")[0])[0],
                        int(x.rpartition(":")[2]),
                        f"{'choice' if 'choice' in d.message else 'config'} {d.symbol}",
                    )
                    for x in mine[:6]
                ),
                warning,
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
