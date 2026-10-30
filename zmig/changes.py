"""What changed between two Zephyr refs, from the tree alone (docs/DESIGN-CHANGE-DB.md).

Facts that need no build of either ref, so scans can use them when the target configure
fails before Kconfig or devicetree. Kconfig:

  symbol table   every `config`/`menuconfig` definition in the tree at each ref (lexical:
                 `git grep`; symbols Zephyr generates, and module trees, aren't in it)
  removed        defined at the source, not at the target
  candidates     what replaced a removed symbol: the `config` lines added by the commits
                 that removed it, same hunk first (a rename, `git log -M` follows moved
                 files), then the rest of the commit by name similarity; chained through
                 intermediate renames to a symbol the target defines (A -> B -> C)
  conditions     each candidate's `depends on` lines and enclosing `if` blocks at the
                 target, so a scan can pick the one its build enables

Devicetree bindings, the same way: every `compatible:` line under dts/bindings at each
ref, removed compatibles, and their replacements from the commits that removed them (a
binding file renamed with its compatible, `ti,tmp116.yaml` -> `ti,tmp11x.yaml`).

Computed once per (source SHA, target SHA) and cached as JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zmig.errors import ZmigError

if TYPE_CHECKING:
    from zmig.refs import Mirror
    from zmig.tools import Runner

CHANGES_VERSION = 3
_DEF = re.compile(r"^\s*(?:menu)?config\s+([A-Z0-9_]+)\s*$")
_DEF_GREP = r"^\s*(menu)?config\s+[A-Z0-9_]+\s*$"
_KCONFIG_PATHS = ("*Kconfig*",)
# A binding's `compatible: "vnd,name"` (quoted or not).
COMPAT = re.compile(r'^compatible:\s*"?([\w,.+-]+)"?\s*(?:#.*)?$')
_COMPAT_GREP = r"^compatible:"
_BINDING_PATHS = ("dts/bindings",)
# Symbols Zephyr generates at configure time; a lexical table can't speak for them.
_GENERATED = re.compile(r"^(DT_HAS_\w+_ENABLED|ZEPHYR_\w+_MODULE(_BLOBS)?)$")
_KEYWORD = re.compile(
    r"^(menuconfig|config|choice|endchoice|menu|endmenu|if|endif|source|rsource|osource|"
    r"orsource|comment|mainmenu)\b"
)
MAX_CHAIN = 5
MIN_SIMILARITY = 0.5
# A commit that removes or adds more `config` lines than this restructures rather than
# renames: its other symbols aren't candidates for each other (Mbed TLS 4, c42fefcb1f2e).
MAX_RENAME_COMMIT = 3
# "Deprecated, use X instead." and the like, in a deprecated symbol's own help text.
USE_INSTEAD = re.compile(
    r"\b(?:use|in favou?r of|replaced (?:by|with)|superseded by)\s+(?:the\s+)?(?:CONFIG_)?"
    r"([A-Z][A-Z0-9_]{2,})"
)


@dataclass
class Candidate:
    symbol: str
    commit: str
    how: str  # "same hunk" | "help text" | "same commit"
    similarity: float
    via: list[str] = field(default_factory=list)  # intermediate renames, oldest first
    defined_at: str = ""  # path:line at the target
    conditions: list[str] = field(default_factory=list)


# --- pure parsing ------------------------------------------------------------------------------


def parse_table(grep_out: str, pattern: re.Pattern[str] = _DEF) -> dict[str, str]:
    """`git grep -n` output ("ref:path:line:text") -> {name: "path:line"}, first wins; the
    name is `pattern`'s group 1 (a Kconfig symbol, or a binding's compatible)."""
    table: dict[str, str] = {}
    for line in grep_out.splitlines():
        parts = line.split(":", 3)
        if len(parts) < 4:
            continue
        m = pattern.match(parts[3])
        if m:
            table.setdefault(m[1], f"{parts[1]}:{parts[2]}")
    return table


def parse_pairs(log: str, pattern: re.Pattern[str] = _DEF) -> dict[str, list[tuple[str, str, str]]]:
    """`git log -M --format='commit %H' -p -U0` over Kconfig files -> {removed symbol:
    [(added symbol, commit, "same hunk" | "same commit")]}. A symbol whose `config` line a
    hunk removes and another `config` line the same hunk adds is a rename; anything else
    the same commit adds is a weaker candidate. Bindings pair the same way on their
    `compatible:` lines (pattern=COMPAT)."""
    out: dict[str, list[tuple[str, str, str]]] = {}

    def flush(commit: str, hunks: list[tuple[list[str], list[str]]]) -> None:
        added_all = [a for _, adds in hunks for a in adds]
        removed_all = [r for rems, _ in hunks for r in rems]
        small = len(added_all) <= MAX_RENAME_COMMIT and len(removed_all) <= MAX_RENAME_COMMIT
        for removed, added in hunks:
            for r in removed:
                same = [a for a in added if a != r]
                out.setdefault(r, []).extend((a, commit, "same hunk") for a in same)
                # The rest of the commit only for a small commit, and only when the symbol
                # had no rename in its own hunk (f2a6a191cf5f renamed STD and EXT side by
                # side: EXT isn't STD's replacement).
                if small and not same:
                    out[r].extend((a, commit, "same commit") for a in added_all if a != r)

    commit = ""
    hunks: list[tuple[list[str], list[str]]] = []
    for line in log.splitlines():
        if line.startswith("commit "):
            if commit:
                flush(commit, hunks)
            commit, hunks = line.split()[1], []
        elif line.startswith(("@@", "diff --git")):
            hunks.append(([], []))
        elif line.startswith(("---", "+++")):
            continue
        elif line[:1] in "+-" and hunks and (m := pattern.match(line[1:])):
            hunks[-1][0 if line[0] == "-" else 1].append(m[1])
    if commit:
        flush(commit, hunks)
    return out


def conditions(kconfig_text: str, symbol: str) -> list[str]:
    """The conditions on `symbol`'s definition in one Kconfig file: the enclosing
    `if EXPR` blocks (outermost first) and its own `depends on` lines."""
    stack: list[str] = []
    lines = kconfig_text.replace("\\\n", " ").splitlines()
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith("if "):
            stack.append(" ".join(line[3:].split("#")[0].split()))
        elif line == "endif" or line.startswith("endif "):
            if stack:
                stack.pop()
        elif (m := _DEF.match(raw)) and m[1] == symbol:
            deps = []
            for nxt in lines[i + 1 :]:
                s = nxt.strip()
                if _KEYWORD.match(nxt):  # the next top-level entry ends this one
                    break
                if s.startswith("depends on "):
                    deps.append(" ".join(s[len("depends on ") :].split("#")[0].split()))
            return [*stack, *deps]
    return []


def help_replacements(kconfig_text: str, symbol: str) -> list[str]:
    """Symbols a deprecated `symbol`'s own definition block tells users to move to
    ("Deprecated, use UART_NATIVE_PTY_0_ON_STDINOUT instead.")."""
    lines = kconfig_text.splitlines()
    for i, raw in enumerate(lines):
        if (m := _DEF.match(raw)) and m[1] == symbol:
            block = []
            for nxt in lines[i + 1 :]:
                if _KEYWORD.match(nxt):
                    break
                block.append(nxt)
            text = "\n".join(block)
            if "deprecat" not in text.lower():
                return []
            return [n for n in dict.fromkeys(USE_INSTEAD.findall(text)) if n != symbol]
    return []


def similarity(a: str, b: str) -> float:
    return round(SequenceMatcher(None, a, b).ratio(), 2)


def rank(
    removed: str,
    pairs: dict[str, list[tuple[str, str, str]]],
    target: dict[str, str],
) -> list[Candidate]:
    """Candidates for one removed symbol, followed through intermediate renames to symbols
    the target defines. Same-hunk ones always count; same-commit ones need a similar name."""
    found: dict[str, Candidate] = {}
    todo: list[tuple[str, list[str]]] = [(removed, [])]
    seen = {removed}
    while todo:
        sym, via = todo.pop(0)
        for new, commit, how in pairs.get(sym, []):
            sim = similarity(sym, new)
            if how == "same commit" and sim < MIN_SIMILARITY:
                continue
            if new in target:
                c = Candidate(new, commit, how, sim, via)
                old = found.get(new)
                if old is None or _order(c) < _order(old):
                    found[new] = c
            elif new not in seen and len(via) < MAX_CHAIN:
                seen.add(new)
                todo.append((new, [*via, new]))
    return sorted(found.values(), key=_order)


_HOW = {"same hunk": 0, "help text": 1, "same commit": 2}


def _order(c: Candidate) -> tuple[int, int, float, str]:
    """A direct rename before a chained one; a same-hunk rename, then the deprecated
    symbol's own help text, then the rest of a small commit; then the more similar name."""
    return (len(c.via), _HOW[c.how], -c.similarity, c.symbol)


def kconfig_changes(
    source: dict[str, str],
    target: dict[str, str],
    pairs: dict[str, list[tuple[str, str, str]]],
    read_target: Any,
    read_source: Any = None,
) -> dict[str, Any]:
    """The Kconfig part of the database. `read_target(path)` / `read_source(path)` return a
    file's text at the target / source ref (for conditions and help text), or None."""
    files: dict[str, str | None] = {}
    sources: dict[str, str | None] = {}
    removed: dict[str, Any] = {}
    for sym in sorted(source.keys() - target.keys()):
        if _GENERATED.match(sym):
            continue
        cands = rank(sym, pairs, target)
        if read_source is not None:
            spath = source[sym].rpartition(":")[0]
            if spath not in sources:
                sources[spath] = read_source(spath)
            text = sources[spath]
            have = {c.symbol for c in cands}
            for n in help_replacements(text, sym) if text else []:
                if n in target and n not in have:
                    cands.append(Candidate(n, "", "help text", similarity(sym, n)))
            cands.sort(key=_order)
        for c in cands:
            c.defined_at = target[c.symbol]
            path = c.defined_at.rpartition(":")[0]
            if path not in files:
                files[path] = read_target(path)
            text = files[path]
            c.conditions = conditions(text, c.symbol) if text else []
        removed[sym] = {"defined_at": source[sym], "candidates": [asdict(c) for c in cands]}
    return {
        "removed": removed,
        "added": len(target.keys() - source.keys()),
        "symbols": {"source": len(source), "target": len(target)},
    }


def compat_changes(
    source: dict[str, str],
    target: dict[str, str],
    pairs: dict[str, list[tuple[str, str, str]]],
) -> dict[str, Any]:
    """The devicetree part of the database: compatibles a binding in the Zephyr tree has
    at the source and none has at the target, with their ranked replacements."""
    removed: dict[str, Any] = {}
    for compat in sorted(source.keys() - target.keys()):
        cands = rank(compat, pairs, target)
        for c in cands:
            c.defined_at = target[c.symbol]
        removed[compat] = {"defined_at": source[compat], "candidates": [asdict(c) for c in cands]}
    return {
        "removed": removed,
        "added": len(target.keys() - source.keys()),
        "compatibles": {"source": len(source), "target": len(target)},
    }


# --- choosing a replacement for one build ------------------------------------------------------

_TOKEN = re.compile(r'\s*(&&|\|\||!=|<=|>=|[!()=<>]|"[^"]*"|[A-Za-z0-9_]+)')
_COMPARE = {"=", "!=", "<", ">", "<=", ">="}


def evaluate(expr: str, config: dict[str, str]) -> bool | None:
    """A Kconfig condition against a build's .config values: True, False, or None when it
    can't tell (a symbol the .config doesn't have, e.g. one new at the target; any
    comparison). `||`, `&&` and `!` follow three-valued logic."""
    toks = [m[1] for m in _TOKEN.finditer(expr)]
    pos = 0

    def peek() -> str | None:
        return toks[pos] if pos < len(toks) else None

    def take() -> str:
        nonlocal pos
        pos += 1
        return toks[pos - 1]

    def or_() -> bool | None:
        v = and_()
        while peek() == "||":
            take()
            w = and_()
            v = True if (v or w) else (None if v is None or w is None else False)
        return v

    def and_() -> bool | None:
        v = not_()
        while peek() == "&&":
            take()
            w = not_()
            v = False if (v is False or w is False) else (None if v is None or w is None else True)
        return v

    def not_() -> bool | None:
        if peek() == "!":
            take()
            v = not_()
            return None if v is None else not v
        return atom()

    def atom() -> bool | None:
        if peek() == "(":
            take()
            v = or_()
            take()  # ")"
            return v
        tok = take()
        if peek() in _COMPARE:
            take()
            take()
            return None
        value = config.get(tok)
        return {"y": True, "m": True, "n": False}.get(value) if value is not None else None

    try:
        return or_()
    except IndexError:
        return None


def choose(candidates: list[dict[str, Any]], config: dict[str, str]) -> list[dict[str, Any]]:
    """The candidates a build can use, best first, each with a `confidence`. A candidate
    whose conditions the build makes false is dropped. One whose conditions all hold comes
    first; it is medium when it is the only such one. With none that holds, a single
    remaining candidate is medium. Everything else is low, and a same-commit candidate is
    always low (DESIGN-CHANGE-DB §2.2)."""
    rated = []
    for c in candidates:
        vals = [evaluate(x, config) for x in c.get("conditions", [])]
        if False in vals:
            continue
        rated.append((True if all(v is True for v in vals) else None, c))
    holds = [c for v, c in rated if v is True]
    ordered = holds + [c for v, c in rated if v is not True]
    sure = holds[:1] if len(holds) == 1 else (ordered if len(ordered) == 1 else [])
    return [
        {**c, "confidence": "medium" if c in sure and c["how"] != "same commit" else "low"}
        for c in ordered
    ]


# --- git-backed ----------------------------------------------------------------------------------


def _git(mirror: Mirror, runner: Runner, *args: str) -> str:
    proc = runner.run([mirror.git, "--git-dir", str(mirror.path), *args], check=False)
    return str(proc.stdout)


def symbol_table(mirror: Mirror, runner: Runner, sha: str) -> dict[str, str]:
    return parse_table(
        _git(mirror, runner, "grep", "-n", "-E", _DEF_GREP, sha, "--", *_KCONFIG_PATHS)
    )


def binding_table(mirror: Mirror, runner: Runner, sha: str) -> dict[str, str]:
    return parse_table(
        _git(mirror, runner, "grep", "-n", "-E", _COMPAT_GREP, sha, "--", *_BINDING_PATHS),
        COMPAT,
    )


def build(mirror: Mirror, runner: Runner, old_sha: str, new_sha: str) -> dict[str, Any]:
    source = symbol_table(mirror, runner, old_sha)
    target = symbol_table(mirror, runner, new_sha)
    compats = binding_table(mirror, runner, old_sha), binding_table(mirror, runner, new_sha)
    for what, sha, table in (
        ("Kconfig symbols", old_sha, source),
        ("Kconfig symbols", new_sha, target),
        ("binding compatibles", old_sha, compats[0]),
        ("binding compatibles", new_sha, compats[1]),
    ):
        if not table:
            # Never let a failed grep read as "nothing changed" (AUDIT §4: text layers must
            # fail loudly, not return less).
            raise ZmigError(f"no {what} found at {sha[:12]} in {mirror.path}")

    def log(*paths: str) -> str:
        return _git(mirror, runner, "log", "-M", "--format=commit %H", "-p", "-U0",
                    f"{old_sha}..{new_sha}", "--", *paths)  # fmt: skip

    def reader(sha: str) -> Any:
        def read(path: str) -> str | None:
            proc = runner.run(
                [mirror.git, "--git-dir", str(mirror.path), "show", f"{sha}:{path}"], check=False
            )
            return str(proc.stdout) if proc.returncode == 0 else None

        return read

    return {
        "schema": CHANGES_VERSION,
        "from": old_sha,
        "to": new_sha,
        "kconfig": kconfig_changes(
            source, target, parse_pairs(log(*_KCONFIG_PATHS)), reader(new_sha), reader(old_sha)
        ),
        "devicetree": compat_changes(*compats, parse_pairs(log(*_BINDING_PATHS), COMPAT)),
    }


def load_or_build(
    mirror: Mirror, runner: Runner, cache_root: Path, old_sha: str, new_sha: str
) -> dict[str, Any]:
    """The database for (old, new), from ~/.cache/zmig/changes or built now."""
    path = cache_root / "changes" / f"{old_sha[:12]}-{new_sha[:12]}-v{CHANGES_VERSION}.json"
    if path.is_file():
        data: dict[str, Any] = json.loads(path.read_text())
        return data
    data = build(mirror, runner, old_sha, new_sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    tmp.replace(path)
    return data
