"""Zephyr migration guides and release notes, parsed into discrete, matchable entries.

Sources (doc/releases/ in the Zephyr tree):
  migration-guide-X.Y.rst  every entry
  release-notes-X.Y.rst    only sections titled "Removed ..." or "Deprecated ..."

An entry is a top-level bullet (or paragraph) of a section, with its nested content.
Each mention inside it is typed and located to its own doc line:

  :kconfig:option:`CONFIG_X`                 kconfig   role (medium)
  :dtcompatible:`vnd,x`                      compat    role (medium)
  :c:func: :c:macro: :c:struct: :c:type: ... ident     role (medium)
  :zephyr_file:`include/zephyr/x.h`          header    role (medium)
  ``CONFIG_X`` / bare CONFIG_X               kconfig   literal (low)
  ``foo.h`` / ``<zephyr/foo.h>``             header    literal (low)
  ``vnd,name``                               compat    literal (low)
  ``anything_else``                          literal   literal (low): matched against DT
                                                       property names, identifiers, boards

This is a heuristic reader of reStructuredText, not docutils: it relies on how Zephyr's
guides are written (underlined headings, `*` bullets with indented continuation), which
has been stable from v3.5 through v4.5. Severity and rename pairs come from the wording
of the sentence that contains a mention, so they are always tagged as heuristics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from zmig.buildctx import BuildContext

ItemKind = Literal["kconfig", "compat", "ident", "header", "literal"]
Strength = Literal["role", "literal"]
GuideSeverity = Literal["breaking", "risky", "info"]


@dataclass(frozen=True)
class Mention:
    kind: ItemKind
    value: str
    strength: Strength
    line: int  # 1-based line in the document
    sentence: str
    severity: GuideSeverity


@dataclass(frozen=True)
class Rename:
    kind: ItemKind
    old: str
    new: str
    line: int
    sentence: str


@dataclass
class Entry:
    id: str
    doc: str  # e.g. doc/releases/migration-guide-3.7.rst
    version: str  # e.g. 3.7
    line: int
    headings: tuple[str, ...]
    text: str
    mentions: list[Mention] = field(default_factory=list)
    renames: list[Rename] = field(default_factory=list)

    @property
    def lead(self) -> str:
        """First sentence, without RST list markers or directives."""
        clean = " ".join(
            _LIST_MARKER.sub("", t).strip()
            for t in self.text.splitlines()
            if not _DIRECTIVE.match(t)
        )
        return _sentences(_plain(clean))[0] if clean.strip() else ""


# Directive lines (".. list-table::") and their options (":header-rows: 1").
_DIRECTIVE = re.compile(r"^\s*(?:\.\.\s|:[\w-]+:(?:\s|$))")
_LIST_MARKER = re.compile(r"^\s*(?:(?:[*-]|\d+\.|#\.)\s+)+")
_HEADING_CHARS = set("=-`:'\"~^_*+#<>")
_ROLE = re.compile(
    r":(?P<role>kconfig:option|dtcompatible|zephyr_file|c:[a-z]+|code):"
    r"`(?P<body>[^`]+)`"
)
_LITERAL = re.compile(r"``(?P<body>[^`]+)``")
_BARE_CONFIG = re.compile(r"(?<![`\w])CONFIG_[A-Z0-9_]+\b")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_C_KEYWORDS = {
    "auto",
    "bool",
    "break",
    "case",
    "char",
    "const",
    "continue",
    "default",
    "do",
    "double",
    "else",
    "enum",
    "extern",
    "false",
    "float",
    "for",
    "goto",
    "if",
    "inline",
    "int",
    "long",
    "main",
    "NULL",
    "register",
    "return",
    "short",
    "signed",
    "sizeof",
    "static",
    "struct",
    "switch",
    "true",
    "typedef",
    "union",
    "unsigned",
    "void",
    "volatile",
    "while",
    "y",
    "n",
}
_BREAKING = re.compile(
    r"\b(removed|renamed|replaced|dropped|no longer|must|has been moved|have been moved|"
    r"moved to|changed|now (?:returns|requires|takes|uses)|instead of|incompatible)\b",
    re.I,
)
_RISKY = re.compile(r"\bdeprecat\w*", re.I)
_FUTURE = re.compile(r"\b(will be|eventually|in the future|future release|later release)\b", re.I)
_RENAME_WORD = re.compile(r"\b(renamed|replaced|moved)\b", re.I)


def _plain(text: str) -> str:
    """Roles and literals reduced to their text, whitespace collapsed (for display)."""
    t = _ROLE.sub(lambda m: _role_display(m["body"]), text)
    t = _LITERAL.sub(lambda m: m["body"], t)
    t = re.sub(r"\(:github:`\d+`\)", "", t)
    return " ".join(t.split())


def _role_display(body: str) -> str:
    m = re.match(r"^(?:[^<]*)<([^>]+)>$", body.strip())
    return (m[1] if m else body).strip().lstrip("~!")


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z:`*(])", text)
    return [p.strip() for p in parts if p.strip()]


def _classify(sentence: str) -> GuideSeverity | None:
    """Breaking wording wins ("Removed the following deprecated ..."), unless the change
    is announced for the future ("deprecated and will be removed")."""
    if _RISKY.search(sentence) and _FUTURE.search(sentence):
        return "risky"
    if _BREAKING.search(sentence):
        return "breaking"
    if _RISKY.search(sentence):
        return "risky"
    return None


def _item(role: str | None, body: str) -> tuple[ItemKind, str] | None:
    body = _role_display(body)
    if role == "kconfig:option":
        return "kconfig", body.removeprefix("CONFIG_")
    if role == "dtcompatible":
        return "compat", body
    if role == "zephyr_file":
        return ("header", body.removeprefix("include/")) if body.endswith(".h") else None
    if role and role.startswith("c:"):
        name = re.sub(r"\(.*\)$", "", body).strip()
        name = name.split(".")[-1].split("::")[-1]
        return ("ident", name) if _IDENT.match(name) else None
    # A plain ``literal`` (or :code:).
    if re.fullmatch(r"CONFIG_[A-Z0-9_]+", body):
        return "kconfig", body.removeprefix("CONFIG_")
    m = re.fullmatch(r"#?\s*(?:include\s*)?<?([\w./-]+\.h)>?", body)
    if m:
        return "header", m[1].removeprefix("include/")
    if re.fullmatch(r"[a-z0-9-]+,[a-z0-9.,+-]+", body):
        return "compat", body
    if re.fullmatch(r"[A-Za-z_][\w-]*", body) and body not in _C_KEYWORDS and len(body) >= 3:
        return "literal", body
    return None


@dataclass
class _Segment:
    """A paragraph or list item: consecutive lines at one indentation."""

    first_line: int
    lines: list[tuple[int, str]]
    depth: int
    parent: _Segment | None

    @property
    def text(self) -> str:
        """Joined lines without RST list markers ("* ", "- ", "#. ", list-table "* - ")."""
        return " ".join(
            _LIST_MARKER.sub("", t).strip() for _, t in self.lines if not _DIRECTIVE.match(t)
        )


def _mentions(seg: _Segment) -> list[tuple[ItemKind, str, Strength, int]]:
    """Typed mentions in reading order (line, then column)."""
    found: list[tuple[int, int, ItemKind, str, Strength]] = []
    for lineno, raw in seg.lines:
        spans: list[tuple[int, int]] = []
        for m in _ROLE.finditer(raw):
            spans.append(m.span())
            role = None if m["role"] == "code" else m["role"]
            it = _item(role, m["body"])
            if it:
                found.append((lineno, m.start(), it[0], it[1], "role" if role else "literal"))
        for m in _LITERAL.finditer(raw):
            if any(a <= m.start() < b for a, b in spans):
                continue
            spans.append(m.span())
            it = _item(None, m["body"])
            if it:
                found.append((lineno, m.start(), it[0], it[1], "literal"))
        for m in _BARE_CONFIG.finditer(raw):
            if not any(a <= m.start() < b for a, b in spans):
                found.append(
                    (lineno, m.start(), "kconfig", m[0].removeprefix("CONFIG_"), "literal")
                )
    return [(k, v, st, ln) for ln, _, k, v, st in sorted(found, key=lambda x: (x[0], x[1]))]


def _sentence_for(seg: _Segment, value: str) -> str:
    plain = _plain(seg.text)
    for s in _sentences(plain):
        if value in s or f"CONFIG_{value}" in s:
            return s
    return plain


def _severity(seg: _Segment, sentence: str, entry_text: str) -> GuideSeverity:
    """From the mention's sentence, else the lead sentences of enclosing bullets, else the
    entry's wording as a whole."""
    sev = _classify(sentence)
    node: _Segment | None = seg.parent
    while sev is None and node is not None:
        sev = _classify(_sentences(_plain(node.text))[0] if node.text.strip() else "")
        node = node.parent
    return sev or _classify(_plain(entry_text)) or "info"


def _renames(seg: _Segment, found: list[tuple[ItemKind, str, Strength, int]]) -> list[Rename]:
    out: list[Rename] = []
    plain = _plain(seg.text)
    names = list(dict.fromkeys((k, v) for k, v, _, _ in found))
    # "A was renamed/replaced/moved ... to/by/with B" within one sentence.
    for s in _sentences(plain):
        m = _RENAME_WORD.search(s)
        if not m:
            continue
        located = [(p, k, v) for k, v in names if (p := _pos(s, v, k)) is not None]
        before = [x for x in located if x[0] < m.start()]
        after = [x for x in located if x[0] > m.end()]
        if before and after:
            _, k1, v1 = max(before)
            _, k2, v2 = min(after)
            if k1 == k2 != "literal" and v1 != v2:
                out.append(Rename(k1, v1, v2, seg.first_line, s))
    # List form under a lead that says renamed: "* ``old`` to :c:struct:`new`".
    list_form = re.fullmatch(r"\s*\S+\s+(?:to|->|→)\s+\S+?\.?\s*", plain)
    lead_renames = seg.parent is not None and _RENAME_WORD.search(_plain(seg.parent.text))
    if list_form and lead_renames and len(names) == 2:
        (k1, v1), (k2, v2) = names
        if k1 == k2 or k1 == "literal":
            out.append(Rename(k2, v1, v2, seg.first_line, plain))
    return out


def _pos(sentence: str, value: str, kind: ItemKind) -> int | None:
    for probe in (f"CONFIG_{value}", value) if kind == "kconfig" else (value,):
        i = sentence.find(probe)
        if i >= 0:
            return i
    return None


def _headings(lines: list[str]) -> dict[int, tuple[int, str]]:
    """line index -> (level, title) for underlined headings (title line index)."""
    order: list[str] = []
    out: dict[int, tuple[int, str]] = {}
    for i in range(len(lines) - 1):
        title, under = lines[i].rstrip(), lines[i + 1].rstrip()
        if (
            title.strip()
            and under
            and len(set(under)) == 1
            and under[0] in _HEADING_CHARS
            and len(under) >= max(3, len(title.strip()) - 2)
            and not title.startswith((" ", "*", "-", ".."))
        ):
            ch = under[0]
            if ch not in order:
                order.append(ch)
            out[i] = (order.index(ch), title.strip())
    return out


def parse(
    text: str, doc: str, version: str, id_prefix: str, section_filter: re.Pattern[str] | None = None
) -> list[Entry]:
    """Entries of one document. `section_filter` keeps only sections whose heading (at any
    level of the current path) matches."""
    lines = text.splitlines()
    heads = _headings(lines)
    entries: list[Entry] = []
    path: list[tuple[int, str]] = []
    i = 0
    seq = 0
    current: list[_Segment] = []

    def flush() -> None:
        nonlocal seq, current
        if not current:
            return
        top = current[0]
        if section_filter is None or any(section_filter.search(t) for _, t in path):
            seq += 1
            entry = Entry(
                id=f"{id_prefix}-{version}-{seq:04d}",
                doc=doc,
                version=version,
                line=top.first_line,
                headings=tuple(t for _, t in path[1:]),
                text="\n".join(t for s in current for _, t in s.lines),
            )
            seen: set[tuple[str, str, int]] = set()
            for seg in current:
                found = _mentions(seg)
                for kind, value, strength, lineno in found:
                    if (kind, value, lineno) in seen:
                        continue
                    seen.add((kind, value, lineno))
                    sentence = _sentence_for(seg, value)
                    entry.mentions.append(
                        Mention(
                            kind,
                            value,
                            strength,
                            lineno,
                            sentence,
                            _severity(seg, sentence, entry.text),
                        )
                    )
                entry.renames += _renames(seg, found)
            entries.append(entry)
        current = []

    while i < len(lines):
        if i in heads:
            flush()
            level, title = heads[i]
            path = [p for p in path if p[0] < level] + [(level, title)]
            i += 2
            continue
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0 and (stripped.startswith(("..", ":orphan:")) or stripped == "|"):
            flush()
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].startswith(" ")):
                i += 1
            continue
        bullet = re.match(r"^(\s*)([*-]|\d+\.|#\.)\s+", line)
        if indent == 0:
            flush()  # a new top-level bullet or paragraph starts a new entry
        # Collect this segment: the line plus its continuation lines.
        content_indent = len(bullet[0]) if bullet else indent
        seg_lines = [(i + 1, line)]
        j = i + 1
        while j < len(lines) and lines[j].strip() and j not in heads:
            nxt = lines[j]
            nind = len(nxt) - len(nxt.lstrip())
            if re.match(r"^\s*([*-]|\d+\.|#\.)\s+", nxt) or nind < content_indent:
                break
            seg_lines.append((j + 1, nxt))
            j += 1
        parent = next((s for s in reversed(current) if s.depth < indent), None)
        current.append(_Segment(i + 1, seg_lines, indent, parent))
        i = j
    flush()
    return entries


NOTES_SECTION = re.compile(r"^(removed|deprecated)\b", re.I)


@dataclass(frozen=True)
class Document:
    path: str  # doc/releases/...
    version: str  # "3.7"
    ref: str  # where it was read from
    text: str
    kind: Literal["guide", "notes"]


def load_documents(old: BuildContext, new: BuildContext) -> list[Document]:
    """Guide and release-notes documents for every release crossed by old -> new: from the
    target tree when it still has them, else from that release's tag."""
    from zmig.analyzers.hops import ref_version, release_tags

    mirror = new.workspace.resolved.mirror
    old_v, _ = ref_version(mirror, old.workspace.resolved.ref.sha)
    new_v, _ = ref_version(mirror, new.workspace.resolved.ref.sha)
    tags = release_tags(mirror)
    minors = sorted(m for m in tags if old_v < m <= new_v)
    if new_v not in minors and new_v > old_v:
        minors.append(new_v)  # target is a development snapshot of an unreleased version
    docs = []
    for minor in minors:
        version = f"{minor[0]}.{minor[1]}"
        sources: tuple[tuple[Literal["guide", "notes"], str], ...] = (
            ("guide", f"migration-guide-{version}.rst"),
            ("notes", f"release-notes-{version}.rst"),
        )
        for kind, name in sources:
            path = f"doc/releases/{name}"
            local = new.workspace.zephyr / path
            if local.is_file():
                docs.append(
                    Document(
                        path, version, new.workspace.resolved.ref.label, local.read_text(), kind
                    )
                )
                continue
            if minor in tags:
                tag = tags[minor][-1][1]
                proc = mirror.runner.run(
                    [mirror.git, "--git-dir", mirror.path, "show", f"{tag}:{path}"], check=False
                )
                if proc.returncode == 0:
                    docs.append(Document(path, version, tag, proc.stdout, kind))
    return docs
