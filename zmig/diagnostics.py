"""Facts parsed from a Zephyr build log: which app input files the build used, and the
Kconfig and devicetree errors that stopped a configure.

The patterns match messages printed by Zephyr's own scripts (kconfiglib via
scripts/kconfig/kconfig.py, edtlib via scripts/dts/gen_defines.py, and the
"Found devicetree overlay" / "Merged configuration" lines from cmake/modules).
They have been stable from v3.6 through v4.4; tests pin them against real logs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

DiagKind = Literal[
    "kconfig-undefined",  # assignment to a symbol that doesn't exist
    "kconfig-not-taken",  # assigned value differs from the resulting value
    "kconfig-promptless",  # assignment to a symbol without a prompt
    "kconfig-definition",  # a Kconfig entry that no longer makes sense (e.g. an override of
    # a symbol the tree no longer defines: "defined without a type")
    "kconfig-loop",  # kconfiglib's "Dependency loop" (e.g. a name the tree now defines too)
    "dt-undeclared-property",  # property not declared in the node's binding
    "dt-error",  # any other devicetree error
    "board-invalid",  # the board name doesn't exist in this tree
]

_UNDEF = re.compile(
    r"^(?P<file>\S[^\n]*?):(?P<line>\d+): warning: attempt to assign the value "
    r"'(?P<value>[^']*)' to the undefined symbol (?P<sym>\w+)",
    re.M,
)
# Kconfig wraps this warning at ~100 columns in some logs (sysbuild child images), so
# every space in the fixed wording may be a newline.
_NOT_TAKEN = re.compile(
    r"warning:\s+(?:CONFIG_)?(?P<sym>\w+)\s+\(defined\s+at\s+[^)]*\)\s+was\s+assigned\s+"
    r"the\s+value\s+'(?P<value>[^']*)'\s+but\s+got\s+the\s+value\s+'(?P<got>[^']*)'\."
    r"(?P<rest>.*?)(?:\n\n|\Z)",
    re.S,
)
_DEPS = re.compile(r"Check\s+these\s+unsatisfied\s+dependencies:\s+(?P<deps>.*?)\.\s", re.S)
_PROMPTLESS = re.compile(
    r"error: (?:CONFIG_)?(?P<sym>\w+) \(defined at [^)]*\) is assigned in a configuration "
    r"file, but is not directly user-configurable",
    re.S,
)
_DT_UNDECLARED = re.compile(
    r"devicetree error: '(?P<prop>[^']+)' appears in (?P<node>\S+) in \S+, but is not "
    r"declared in 'properties:' in (?P<binding>\S+)"
)
# kconfiglib's definition warnings (Kconfig "warnings are errors" in Zephyr's kconfig.py):
# an app's Kconfig override (`config X` + `default ...`) of a symbol the tree no longer
# defines has no type; an override of a choice that no longer exists has no type/prompt.
_DEFINITION = re.compile(
    r"^warning: (?P<sym><choice \w+>|\w+) \(defined at (?P<locs>[^)]*)\) "
    r"(?P<what>defined without a type|defined with type unknown|defined without a prompt)",
    re.M,
)
_CHOICE_DEFAULT = re.compile(
    r"^warning: the default selection (?P<default>\w+) \(undefined\) of <choice "
    r"(?P<sym>\w+)> \(defined at (?P<locs>[^)]*)\) is not contained in the choice",
    re.M,
)
_LOOP = re.compile(r"Dependency loop\n=+\n(?P<body>.*?)(?:\nCMake Error|\Z)", re.S)
_LOOP_SYM = re.compile(r"^(?:\.\.\.[a-z ]+? )?(?P<sym>\w+) \(defined at (?P<locs>[^)]*)\)", re.M)
_DT_ERROR = re.compile(r"^devicetree error: (?P<msg>.*)$", re.M)
# edtlib, v3.6 through v4.4: a property the binding now requires is missing from a node.
_DT_REQUIRED = re.compile(
    r"'(?P<prop>[^']+)' is marked as required in 'properties:' in (?P<binding>\S+), but "
    r"does not appear in <Node (?P<node>\S+) in"
)
_BOARD_INVALID = re.compile(r"No board named '(?P<board>[^']+)' found")
_OVERLAY = re.compile(r"^-- Found devicetree overlay: (?P<path>.+)$", re.M)
_MERGED = re.compile(r"^Merged configuration '(?P<path>[^']+)'", re.M)
_LOADED = re.compile(r"^Loaded configuration '(?P<path>[^']+)'", re.M)


@dataclass(frozen=True)
class Diagnostic:
    kind: DiagKind
    symbol: str = ""  # Kconfig symbol (no CONFIG_ prefix) or DT property
    value: str = ""  # assigned value
    got: str = ""  # resulting value (kconfig-not-taken)
    deps: str = ""  # unsatisfied dependencies, as Kconfig printed them
    node: str = ""  # DT node path
    binding: str = ""  # DT binding file
    file: str = ""  # source file, when the message names one
    line: int = 0
    # Every "defined at" location the message lists (path:line), in order.
    locations: tuple[str, ...] = ()
    message: str = ""  # the original message, for evidence


@dataclass(frozen=True)
class LogFacts:
    overlays: tuple[str, ...] = ()
    configs: tuple[str, ...] = ()  # merged configuration fragments, in order
    base_config: str = ""  # the board defconfig Kconfig loaded first
    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)


def _identity(d: Diagnostic) -> tuple[str, ...]:
    """What makes two configures' diagnostics the same one: not the "defined at" lines,
    which move between refs, nor build paths. A message without a subject (dt-error) is
    compared as text with its paths masked."""
    text = "" if d.symbol else re.sub(r"\S*/\S*", "<path>", d.message)
    return (d.kind, d.symbol, d.value, d.got, d.node, text)


def preexisting(d: Diagnostic, source: Iterable[Diagnostic]) -> bool:
    """True if the source configure printed the same diagnostic (eval/AUDIT.md F1: the
    settings sample's CONFIG_STDOUT_CONSOLE warning was reported as new at v4.2.0)."""
    return _identity(d) in {_identity(s) for s in source}


def _join_wrapped(text: str) -> str:
    """kconfig.py wraps its final error() at 100 columns; undo that for matching."""
    return re.sub(r"(?<=\S)\n(?=\S)", " ", text)


def parse_log(text: str) -> LogFacts:
    diags: list[Diagnostic] = []
    for m in _UNDEF.finditer(text):
        diags.append(
            Diagnostic(
                "kconfig-undefined",
                symbol=m["sym"],
                value=m["value"],
                file=m["file"],
                line=int(m["line"]),
                message=m[0],
            )
        )
    for m in _NOT_TAKEN.finditer(text):
        deps = _DEPS.search(m["rest"])
        diags.append(
            Diagnostic(
                "kconfig-not-taken",
                symbol=m["sym"],
                value=m["value"],
                got=m["got"],
                deps=" ".join(deps["deps"].split()) if deps else "",
                message=" ".join(m[0].split()),
            )
        )
    for m in _PROMPTLESS.finditer(_join_wrapped(text)):
        diags.append(
            Diagnostic("kconfig-promptless", symbol=m["sym"], message=" ".join(m[0].split()))
        )

    def locs(text: str) -> tuple[str, ...]:
        return tuple(x.strip() for x in text.split(", ") if re.search(r":\d+$", x.strip()))

    def first(loc: tuple[str, ...]) -> tuple[str, int]:
        if not loc:
            return "", 0
        path, _, line = loc[0].rpartition(":")
        return path, int(line)

    for m in _DEFINITION.finditer(text):
        ls = locs(m["locs"])
        diags.append(
            Diagnostic(
                "kconfig-definition",
                symbol=m["sym"].removeprefix("<choice ").removesuffix(">"),
                file=first(ls)[0],
                line=first(ls)[1],
                locations=ls,
                message=" ".join(m[0].split()),
            )
        )
    for m in _CHOICE_DEFAULT.finditer(text):
        ls = locs(m["locs"])
        diags.append(
            Diagnostic(
                "kconfig-definition",
                symbol=m["sym"],
                value=m["default"],
                file=first(ls)[0],
                line=first(ls)[1],
                locations=ls,
                message=" ".join(m[0].split()),
            )
        )
    for m in _LOOP.finditer(text):
        for step in _LOOP_SYM.finditer(m["body"]):
            ls = locs(step["locs"])
            diags.append(
                Diagnostic(
                    "kconfig-loop",
                    symbol=step["sym"],
                    file=first(ls)[0],
                    line=first(ls)[1],
                    locations=ls,
                    message=" ".join(step[0].split()),
                )
            )
    undeclared = set()
    for m in _DT_UNDECLARED.finditer(text):
        undeclared.add(m.start())
        diags.append(
            Diagnostic(
                "dt-undeclared-property",
                symbol=m["prop"],
                node=m["node"],
                binding=m["binding"],
                message=m[0],
            )
        )
    for m in _DT_ERROR.finditer(text):
        if m.start() not in undeclared:
            # Still a dt-error (finding IDs depend on the kind), with its subject when
            # edtlib named one (eval/AUDIT.md §6 item 6).
            r = _DT_REQUIRED.search(m["msg"])
            diags.append(
                Diagnostic(
                    "dt-error",
                    symbol=r["prop"] if r else "",
                    node=r["node"] if r else "",
                    binding=r["binding"] if r else "",
                    message=m[0].strip(),
                )
            )
    for m in _BOARD_INVALID.finditer(text):
        diags.append(Diagnostic("board-invalid", symbol=m["board"], message=m[0]))
    loaded = _LOADED.search(text)
    return LogFacts(
        overlays=tuple(dict.fromkeys(m["path"].strip() for m in _OVERLAY.finditer(text))),
        configs=tuple(dict.fromkeys(m["path"] for m in _MERGED.finditer(text))),
        base_config=loaded["path"] if loaded else "",
        diagnostics=tuple(diags),
    )


def app_relative(paths: tuple[str, ...], app: Path) -> tuple[str, ...]:
    """The subset of `paths` inside the app directory, relative to it."""
    root = app.resolve()
    out = []
    for p in paths:
        try:
            out.append(Path(p).resolve().relative_to(root).as_posix())
        except ValueError:
            continue
    return tuple(out)


def parse_deprecated_boards(text: str) -> dict[str, str]:
    """old board name -> new board target, from the text of boards/deprecated.cmake."""
    pat = re.compile(r"set\(\s*(\S+)_DEPRECATED\s+(\S+)\s*\)")
    return {m[1]: m[2] for m in pat.finditer(text)}


def deprecated_boards(zephyr: Path) -> dict[str, str]:
    """old board name -> new board target, from boards/deprecated.cmake (HWMv2+)."""
    f = zephyr / "boards" / "deprecated.cmake"
    return parse_deprecated_boards(f.read_text()) if f.is_file() else {}


def deprecated_board_line(text: str, old: str) -> int:
    for i, line in enumerate(text.splitlines(), 1):
        if re.match(rf"^set\(\s*{re.escape(old)}_DEPRECATED\b", line.strip()):
            return i
    return 0


# --- Warnings ---------------------------------------------------------------------------

_W_COMPILER = re.compile(
    r"^(?P<file>[^\s:]+):(?P<line>\d+):(?:\d+:)? warning: (?P<msg>.*?)"
    r"(?: \[(?P<flag>-W[^\]]+)\])?$",
    re.M,
)
# Kconfig wraps long warnings: a message continues on following non-blank lines until
# the next "warning:"/"error:" or a blank line.
_W_PLAIN = re.compile(r"^warning: (?P<msg>.+(?:\n(?!warning:|error:|-- |\[\d)[^\n]+)*)", re.M)
_W_CMAKE = re.compile(
    r"^CMake (?:Deprecation )?Warning(?: \(dev\))? at (?P<file>\S+):(?P<line>\d+) \([\w]+\):\n"
    r"(?P<body>(?:(?:  .*)?\n)+)",
    re.M,
)


# The context GCC prints around a diagnostic: the include chain and the enclosing function
# before it, the "in expansion of macro" notes after it (innermost first), and source
# excerpts (" 36 | code", "    |  ^~~") in between.
_W_INCLUDED = re.compile(r"^(?:In file included|\s+) from (?P<file>[^\s:]+):(?P<line>\d+)[:,]$")
_W_FUNCTION = re.compile(r"^(?P<file>[^\s:]+): In (?:function|member function) '[^']*':$")
_W_NOTE = re.compile(r"^(?P<file>[^\s:]+):(?P<line>\d+):(?:\d+:)? note: (?P<msg>.*)$")
_W_EXCERPT = re.compile(r"^\s+\d*\s*\|")
_DEFINED_AT = re.compile(r"\(defined\s+at\s+[^)]*\)")


@dataclass(frozen=True)
class BuildWarning:
    source: str  # "compiler" | "kconfig" | "cmake"
    file: str  # as printed (absolute), or "" if none
    line: int
    message: str
    flag: str = ""
    # Where the code that triggered it was written, when the warning itself is reported in
    # another file (a Zephyr header): the "in expansion of macro" sites, innermost first,
    # then the enclosing function's file (line 0), then the include chain's outermost file.
    sites: tuple[tuple[str, int], ...] = ()
    # The "in expansion of macro" sites alone, innermost first; and the whole include chain
    # printed before it, innermost first (DOGFOOD-13 F2: a header's own `__WARN` is about
    # including it, and the chain is to toolchain/gcc.h, not to that header).
    notes: tuple[tuple[str, int], ...] = ()
    includes: tuple[tuple[str, int], ...] = ()

    def key(self, roots: dict[str, str]) -> tuple[str, str, str]:
        """Identity across builds: paths made relative to known roots (the zephyr tree,
        the app, the build dir) and line numbers dropped, since both shift between
        versions without the warning changing."""

        def norm(s: str) -> str:
            for prefix, token in sorted(roots.items(), key=lambda kv: -len(kv[0])):
                s = s.replace(prefix, token)
            return " ".join(s.split())

        message = self.message
        if self.source == "kconfig":
            # Kconfig names definitions inline: "(defined at lib/libc/Kconfig:156)" moved
            # to :160 between v4.1 and v4.2 with the warning unchanged.
            message = _DEFINED_AT.sub(lambda m: re.sub(r":\d+\b", "", m[0]), message)
        return (self.source, norm(self.file), norm(message))


def compiler_sites(lines: list[str], i: int) -> tuple[tuple[str, int], ...]:
    """The code locations GCC prints around the diagnostic on lines[i] (see BuildWarning)."""
    notes, function, included = compiler_context(lines, i)
    # The include chain is printed innermost first; its last line is the compiled file.
    return tuple(dict.fromkeys([*notes, *function, *included[-1:]]))


def compiler_context(
    lines: list[str], i: int
) -> tuple[list[tuple[str, int]], list[tuple[str, int]], list[tuple[str, int]]]:
    """(macro-expansion notes, innermost first; the enclosing function's file, line 0; the
    include chain, innermost first) GCC prints around the diagnostic on lines[i]."""
    notes: list[tuple[str, int]] = []
    for ln in lines[i + 1 :]:
        if m := _W_NOTE.match(ln):
            if m["msg"].startswith("in expansion of macro"):
                notes.append((m["file"], int(m["line"])))
        elif not _W_EXCERPT.match(ln):
            break
    function: list[tuple[str, int]] = []
    included: list[tuple[str, int]] = []
    for ln in reversed(lines[max(0, i - 40) : i]):
        if m := _W_FUNCTION.match(ln):
            function = function or [(m["file"], 0)]
        elif m := _W_INCLUDED.match(ln):
            included.append((m["file"], int(m["line"])))
        else:
            break
    # Read upwards, so the chain was collected outermost first.
    return notes, function, included[::-1]


def parse_warnings(text: str) -> list[BuildWarning]:
    lines = text.splitlines()
    out = []
    for i, ln in enumerate(lines):
        if m := _W_COMPILER.match(ln):
            notes, _, included = compiler_context(lines, i)
            out.append(
                BuildWarning(
                    "compiler",
                    m["file"],
                    int(m["line"]),
                    m["msg"].strip(),
                    m["flag"] or "",
                    compiler_sites(lines, i),
                    tuple(notes),
                    tuple(included),
                )
            )
    out += [
        BuildWarning("kconfig", "", 0, " ".join(m["msg"].split())) for m in _W_PLAIN.finditer(text)
    ]
    out += [
        BuildWarning("cmake", m["file"], int(m["line"]), " ".join(m["body"].split()))
        for m in _W_CMAKE.finditer(text)
    ]
    return out
