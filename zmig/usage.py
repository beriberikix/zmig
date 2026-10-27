"""What the application references, with file:line for every reference.

M3 scope: Kconfig assignments and reads, and devicetree references from overlays and
C macros. Headers and APIs (from compile_commands / object files) come in M4.

Overlay parsing is a small brace-tracking scanner, not a DTS parser: it understands
`&label {`, `&{/path} {`, nested `name@addr {` nodes, and `prop = ...;` / `prop;`
statements on one line. That covers how overlays are written in practice; anything it
can't place is ignored rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SOURCE_SUFFIXES = (".c", ".h", ".cpp", ".hpp", ".cc", ".S")
CMAKE_NAMES = ("CMakeLists.txt",)
IGNORE_DIRS = {".git", "build", "__pycache__", ".venv"}


@dataclass(frozen=True)
class Loc:
    path: str  # relative to the app
    line: int
    text: str


@dataclass(frozen=True)
class NodeRef:
    """How a reference names a node: by label, by full path, by C path tokens, or alias."""

    kind: str  # "label" | "path" | "tokens" | "alias"
    value: str  # label / path / alias name; tokens joined with '/'
    child: str = ""  # for nodes nested under a label in an overlay: "name@addr/..."


@dataclass
class ProjectUsage:
    app: Path
    kconfig_set: dict[str, list[tuple[Loc, str]]] = field(default_factory=dict)
    kconfig_read: dict[str, list[Loc]] = field(default_factory=dict)
    dt_nodes: list[tuple[NodeRef, Loc]] = field(default_factory=list)
    dt_props_set: list[tuple[NodeRef, str, Loc]] = field(default_factory=list)
    dt_props_read: list[tuple[NodeRef, str, Loc]] = field(default_factory=list)
    dt_compats: dict[str, list[Loc]] = field(default_factory=dict)
    includes: dict[str, list[Loc]] = field(default_factory=dict)  # "zephyr/kernel.h" -> locs
    identifiers: dict[str, list[Loc]] = field(default_factory=dict)  # C identifiers used


def _files(app: Path) -> list[Path]:
    out = []
    for p in sorted(app.rglob("*")):
        rel = p.relative_to(app)
        if p.is_file() and not any(part in IGNORE_DIRS for part in rel.parts):
            out.append(p)
    return out


def _strip_c_comments(text: str) -> str:
    """Blank out /* */ and // comments, keeping newlines so line numbers survive."""

    def blank(m: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", m[0])

    return re.sub(r"/\*.*?\*/|//[^\n]*", blank, text, flags=re.S)


def dt_token(name: str) -> str:
    """Devicetree node or property name as it appears in C macros: lowercase, and every
    character other than letters and digits becomes '_' (e.g. gpio@800 -> gpio_800)."""
    return re.sub(r"[^a-z0-9]", "_", name.lower())


_C_NODE = re.compile(r"\b(DT_NODELABEL|DT_ALIAS|DT_PATH)\s*\(\s*([A-Za-z0-9_,\s]+?)\s*\)")
_C_PROP = re.compile(
    r"\b(?:DT_PROP|DT_PROP_OR|DT_PROP_LEN|DT_PROP_LEN_OR|DT_NODE_HAS_PROP|DT_PROP_BY_IDX|"
    r"DT_PHA|DT_PHANDLE|DT_ENUM_IDX|DT_STRING_TOKEN)\s*\(\s*"
    r"(?:(DT_NODELABEL|DT_ALIAS|DT_PATH)\s*\(\s*([A-Za-z0-9_,\s]+?)\s*\)|(\w+))\s*,\s*(\w+)"
)
# `#define NAME DT_NODELABEL(x)`: node identifiers are almost always named this way.
_C_NODE_DEFINE = re.compile(
    r"^\s*#\s*define\s+(\w+)\s+(DT_NODELABEL|DT_ALIAS|DT_PATH)\s*\(\s*([A-Za-z0-9_,\s]+?)\s*\)\s*$",
    re.M,
)
_C_COMPAT = re.compile(r"#\s*define\s+DT_DRV_COMPAT\s+(\w+)|\bDT_COMPAT_\w*?\(\s*(\w+)")
_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')
_C_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_STRING = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
MAX_IDENT_LOCS = 5
_KCONFIG_READ = re.compile(r"\bCONFIG_([A-Z0-9_]+)\b")
_CONF_SET = re.compile(r"^\s*CONFIG_(\w+)\s*=\s*(.*?)\s*$")


def _c_noderef(macro: str, args: str) -> NodeRef:
    parts = [a.strip() for a in args.split(",")]
    if macro == "DT_NODELABEL":
        return NodeRef("label", parts[0])
    if macro == "DT_ALIAS":
        return NodeRef("alias", parts[0])
    return NodeRef("tokens", "/".join(parts))


def _scan_c(rel: str, text: str, u: ProjectUsage) -> None:
    lines = text.splitlines()
    stripped = _strip_c_comments(text)
    defines = {m[1]: _c_noderef(m[2], m[3]) for m in _C_NODE_DEFINE.finditer(stripped)}
    for i, line in enumerate(stripped.splitlines(), 1):
        loc = Loc(rel, i, lines[i - 1].strip())
        for m in _C_NODE.finditer(line):
            u.dt_nodes.append((_c_noderef(m[1], m[2]), loc))
        for m in _C_PROP.finditer(line):
            node = _c_noderef(m[1], m[2]) if m[1] else defines.get(m[3])
            if node is not None:
                u.dt_props_read.append((node, m[4], loc))
        for m in _C_COMPAT.finditer(line):
            u.dt_compats.setdefault(m[1] or m[2], []).append(loc)
        for m in _KCONFIG_READ.finditer(line):
            u.kconfig_read.setdefault(m[1], []).append(loc)
        if inc := _INCLUDE.match(line):
            u.includes.setdefault(inc[1], []).append(loc)
            continue
        if line.lstrip().startswith("#"):
            continue  # other preprocessor lines: names there are macros, not uses
        for m in _C_IDENT.finditer(_STRING.sub('""', line)):
            locs = u.identifiers.setdefault(m[0], [])
            if len(locs) < MAX_IDENT_LOCS and (not locs or locs[-1] != loc):
                locs.append(loc)


def _scan_conf(rel: str, text: str, u: ProjectUsage) -> None:
    for i, line in enumerate(text.splitlines(), 1):
        if m := _CONF_SET.match(line):
            u.kconfig_set.setdefault(m[1], []).append((Loc(rel, i, line.strip()), m[2]))


_OPEN = re.compile(
    r"^(?:(?P<label>[\w-]+)\s*:\s*)?(?:&(?P<ref>[\w-]+)|&\{(?P<path>[^}]+)\}|"
    r"(?P<name>/|[\w,.+-]+(?:@[\w,.-]+)?))\s*\{"
)
_PROP = re.compile(r"^(?P<prop>[\w,.#+?-]+)\s*(?:=\s*(?P<val>.*?))?\s*;")


def _scan_overlay(rel: str, text: str, u: ProjectUsage) -> None:
    raw_lines = text.splitlines()
    stack: list[NodeRef | None] = []
    for i, line in enumerate(_strip_c_comments(text).splitlines(), 1):
        s = line.strip()
        loc = Loc(rel, i, raw_lines[i - 1].strip())
        while s:
            if s.startswith("}"):
                if stack:
                    stack.pop()
                s = s[1:].lstrip(" ;")
                continue
            if m := _OPEN.match(s):
                parent = stack[-1] if stack else None
                ref: NodeRef | None
                if m["ref"]:
                    ref = NodeRef("label", m["ref"])
                elif m["path"]:
                    ref = NodeRef("path", m["path"].strip())
                elif m["name"] == "/":
                    ref = NodeRef("path", "/")
                elif parent is None:
                    ref = None
                elif parent.kind == "path":
                    base = parent.value.rstrip("/")
                    ref = NodeRef("path", f"{base}/{m['name']}")
                else:
                    child = f"{parent.child}/{m['name']}" if parent.child else m["name"]
                    ref = NodeRef(parent.kind, parent.value, child)
                if ref is not None and not (ref.kind == "path" and ref.value == "/"):
                    u.dt_nodes.append((ref, loc))
                stack.append(ref)
                s = s[m.end() :].strip()
                continue
            if (m := _PROP.match(s)) and stack and stack[-1] is not None:
                u.dt_props_set.append((stack[-1], m["prop"], loc))
                if m["prop"] == "compatible" and m["val"]:
                    for c in re.findall(r'"([^"]+)"', m["val"]):
                        u.dt_compats.setdefault(dt_token(c), []).append(loc)
            break


def scan(app: Path) -> ProjectUsage:
    app = app.resolve()
    u = ProjectUsage(app)
    for p in _files(app):
        rel = p.relative_to(app).as_posix()
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        if p.suffix == ".conf":
            _scan_conf(rel, text, u)
        elif p.suffix in (".overlay", ".dts", ".dtsi"):
            _scan_overlay(rel, text, u)
        elif p.suffix in SOURCE_SUFFIXES:
            _scan_c(rel, text, u)
        elif p.name in CMAKE_NAMES or p.name.startswith("Kconfig"):
            for i, line in enumerate(text.splitlines(), 1):
                for m in _KCONFIG_READ.finditer(line):
                    u.kconfig_read.setdefault(m[1], []).append(Loc(rel, i, line.strip()))
    return u


# --- Resolution against an EDT snapshot -----------------------------------------------


def resolve(ref: NodeRef, edt: dict[str, Any]) -> str | None:
    """Node path a reference points at in a given devicetree, or None if it doesn't."""
    nodes: dict[str, dict[str, Any]] = edt["nodes"]
    base: str | None = None
    if ref.kind == "path":
        base = ref.value if ref.value in nodes else None
    elif ref.kind == "label":
        base = next((p for p, n in nodes.items() if ref.value in n["labels"]), None)
    elif ref.kind == "alias":
        tok = dt_token(ref.value)
        base = next(
            (p for p, n in nodes.items() if any(dt_token(a) == tok for a in n["aliases"])), None
        )
    elif ref.kind == "tokens":
        want = ref.value.split("/")
        base = next(
            (
                p
                for p in nodes
                if [dt_token(x) for x in p.strip("/").split("/")] == want and p != "/"
            ),
            None,
        )
    if base is None or not ref.child:
        return base
    path = f"{base.rstrip('/')}/{ref.child}"
    return path if path in nodes else None


def describe(ref: NodeRef) -> str:
    if ref.kind == "label":
        s = f"&{ref.value}"
    elif ref.kind == "alias":
        s = f"alias {ref.value}"
    elif ref.kind == "tokens":
        s = f"DT_PATH({ref.value.replace('/', ', ')})"
    else:
        s = ref.value
    return f"{s}/{ref.child}" if ref.child else s
