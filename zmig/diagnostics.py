"""Facts parsed from a Zephyr build log: which app input files the build used, and the
Kconfig and devicetree errors that stopped a configure.

The patterns match messages printed by Zephyr's own scripts (kconfiglib via
scripts/kconfig/kconfig.py, edtlib via scripts/dts/gen_defines.py, and the
"Found devicetree overlay" / "Merged configuration" lines from cmake/modules).
They have been stable from v3.6 through v4.4; tests pin them against real logs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

DiagKind = Literal[
    "kconfig-undefined",  # assignment to a symbol that doesn't exist
    "kconfig-not-taken",  # assigned value differs from the resulting value
    "kconfig-promptless",  # assignment to a symbol without a prompt
    "dt-undeclared-property",  # property not declared in the node's binding
    "dt-error",  # any other devicetree error
    "board-invalid",  # the board name doesn't exist in this tree
]

_UNDEF = re.compile(
    r"^(?P<file>\S[^\n]*?):(?P<line>\d+): warning: attempt to assign the value "
    r"'(?P<value>[^']*)' to the undefined symbol (?P<sym>\w+)",
    re.M,
)
_NOT_TAKEN = re.compile(
    r"warning: (?:CONFIG_)?(?P<sym>\w+) \(defined at [^)]*\) was assigned the value "
    r"'(?P<value>[^']*)' but got the value '(?P<got>[^']*)'\.(?P<rest>.*?)(?:\n\n|\Z)",
    re.S,
)
_DEPS = re.compile(r"Check these unsatisfied dependencies: (?P<deps>.*?)\. ", re.S)
_PROMPTLESS = re.compile(
    r"error: (?:CONFIG_)?(?P<sym>\w+) \(defined at [^)]*\) is assigned in a configuration "
    r"file, but is not directly user-configurable",
    re.S,
)
_DT_UNDECLARED = re.compile(
    r"devicetree error: '(?P<prop>[^']+)' appears in (?P<node>\S+) in \S+, but is not "
    r"declared in 'properties:' in (?P<binding>\S+)"
)
_DT_ERROR = re.compile(r"^devicetree error: (?P<msg>.*)$", re.M)
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
    message: str = ""  # the original message, for evidence


@dataclass(frozen=True)
class LogFacts:
    overlays: tuple[str, ...] = ()
    configs: tuple[str, ...] = ()  # merged configuration fragments, in order
    base_config: str = ""  # the board defconfig Kconfig loaded first
    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)


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
            diags.append(Diagnostic("dt-error", message=m[0].strip()))
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
_W_PLAIN = re.compile(r"^warning: (?P<msg>.+)$", re.M)
_W_CMAKE = re.compile(
    r"^CMake (?:Deprecation )?Warning(?: \(dev\))? at (?P<file>\S+):(?P<line>\d+) \([\w]+\):\n"
    r"(?P<body>(?:(?:  .*)?\n)+)",
    re.M,
)


@dataclass(frozen=True)
class BuildWarning:
    source: str  # "compiler" | "kconfig" | "cmake"
    file: str  # as printed (absolute), or "" if none
    line: int
    message: str
    flag: str = ""

    def key(self, roots: dict[str, str]) -> tuple[str, str, str]:
        """Identity across builds: paths made relative to known roots (the zephyr tree,
        the app, the build dir) and line numbers dropped, since both shift between
        versions without the warning changing."""

        def norm(s: str) -> str:
            for prefix, token in sorted(roots.items(), key=lambda kv: -len(kv[0])):
                s = s.replace(prefix, token)
            return " ".join(s.split())

        return (self.source, norm(self.file), norm(self.message))


def parse_warnings(text: str) -> list[BuildWarning]:
    out = [
        BuildWarning("compiler", m["file"], int(m["line"]), m["msg"].strip(), m["flag"] or "")
        for m in _W_COMPILER.finditer(text)
    ]
    out += [BuildWarning("kconfig", "", 0, m["msg"].strip()) for m in _W_PLAIN.finditer(text)]
    out += [
        BuildWarning("cmake", m["file"], int(m["line"]), " ".join(m["body"].split()))
        for m in _W_CMAKE.finditer(text)
    ]
    return out
