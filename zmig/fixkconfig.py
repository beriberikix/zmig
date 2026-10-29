"""`zmig fix kconfig`: rename Kconfig assignments the target removed, where Kconfig itself
says what replaced them.

Scope: assignments in the project's own configuration fragments (prj.conf, board .conf
files, per-image files such as sysbuild/mcuboot.conf, fragments passed with
EXTRA_CONF_FILE or OVERLAY_CONFIG, and SB_CONFIG_* entries in sysbuild.conf). Nothing else
is edited: no includes, boards, devicetree or code.

An assignment is classified with the Kconfig trees of the image that reads it:

  rename       CONFIG_OLD is absent from the target's tree, and the source tree shows
               what replaced it (`evidence`): OLD was deprecated and selected NEW, or one
               of the two was a pure alias for the other (`default OLD` / `default NEW`).
               Also required: NEW exists at the target with OLD's type and a prompt, the
               target's own kconfiglib gives NEW the assigned value when it is merged
               after the build's fragments, and no migration-guide entry about either
               symbol mentions a value or unit change. Only these are applied.
  needs-human  absent from the target's tree, without all of the above: a rename only
               the migration guide or help text names, a type or dependency mismatch, a
               value/unit change, or no replacement at all.
  deps-unmet   present at the target, but the assigned value no longer takes effect.
               A different problem; never edited.

Dry run by default. `--apply` refuses a dirty tree, commits each evidence kind separately
on a new branch, rebuilds the target and reruns the analyzers (see `compare`).
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

Symbols = dict[str, dict[str, Any]]
Category = Literal["rename", "needs-human", "deps-unmet"]

# `CONFIG_FOO=value  # comment` and `# CONFIG_FOO is not set` (which assigns n).
_SET = re.compile(r"^(?P<lead>\s*)(?P<prefix>SB_CONFIG_|CONFIG_)(?P<sym>\w+)(?P<rest>\s*=.*)$")
_UNSET = re.compile(
    r"^(?P<lead>\s*#\s*)(?P<prefix>SB_CONFIG_|CONFIG_)(?P<sym>\w+)(?P<rest> is not set.*)$"
)
_VALUE = re.compile(r"^\s*=\s*(?P<v>\"(?:[^\"\\]|\\.)*\"|[^#\s]*)")

# A migration-guide entry that mentions either symbol and one of these is a value or unit
# change, which a rename would carry over silently.
_VALUE_CHANGE = re.compile(
    r"\b(?:values?|units?|scal(?:e|ed|ing)|interpret\w*|semantics?|meaning|inverted|"
    r"milli-?seconds?|micro-?seconds?|nano-?seconds?|seconds?|ms|us|ns|ticks?|"
    r"bytes?|[kKM]i?B|[kKM]?Hz|percent|multipl\w*|divided)\b"
)


@dataclass(frozen=True)
class Assignment:
    path: str  # as shown: relative to the project root
    file: Path  # the real file
    line: int
    text: str
    prefix: str  # CONFIG_ or SB_CONFIG_
    symbol: str  # without the prefix
    value: str  # "n" for `# CONFIG_X is not set`
    image: str  # the image whose Kconfig reads it ("sysbuild" for SB_CONFIG_)


@dataclass(frozen=True)
class Proposal:
    assignment: Assignment
    category: Category
    new_symbol: str | None = None
    evidence_kind: str = ""  # rename only: "deprecation select" or "alias"
    reasons: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        a = self.assignment
        return {
            "file": a.path,
            "line": a.line,
            "text": a.text,
            "image": a.image,
            "symbol": a.prefix + a.symbol,
            "category": self.category,
            **({"new_symbol": a.prefix + self.new_symbol} if self.new_symbol else {}),
            **({"evidence_kind": self.evidence_kind} if self.evidence_kind else {}),
            "reasons": list(self.reasons),
        }


def parse_conf(text: str) -> list[tuple[int, str, str, str]]:
    """(line, prefix, symbol, value) for each assignment in a Kconfig fragment."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if m := _UNSET.match(line):
            out.append((i, m["prefix"], m["sym"], "n"))
        elif m := _SET.match(line):
            v = _VALUE.match(m["rest"])
            out.append((i, m["prefix"], m["sym"], v["v"] if v else ""))
    return out


def rewrite(text: str, edits: dict[int, tuple[str, str]]) -> str:
    """Rename the symbol on each edited line (line -> (old symbol, new symbol)), keeping
    the value, spacing, comments and line endings. A line whose symbol isn't the expected
    one is left alone (the file changed since it was read)."""
    lines = text.splitlines(keepends=True)
    for n, (old, new) in edits.items():
        if not 1 <= n <= len(lines):
            continue
        line = lines[n - 1]
        body = line.rstrip("\r\n")
        m = _UNSET.match(body) or _SET.match(body)
        if m is None or m["sym"] != old:
            continue
        lines[n - 1] = body[: m.start("sym")] + new + body[m.end("sym") :] + line[len(body) :]
    return "".join(lines)


def _default_names(defaults: Iterable[str], sym: str) -> bool:
    """A default whose value is `sym` itself (`default sym`): an alias. kconfiglib folds
    enclosing `if` blocks into the condition (`sym if FOO`). `y if sym` is not counted:
    that form is also how a menu's condition is folded into a plain `default y`."""
    rx = re.compile(rf"^{re.escape(sym)}(?:\s+if\s+.+)?$")
    return any(rx.match(d.strip()) for d in defaults)


def kconfig_replacements(sym: str, old: Symbols) -> list[tuple[str, str, str]]:
    """(NEW, evidence kind, why) from the source tree's own definitions."""
    o = old.get(sym, {})
    out: dict[str, tuple[str, str]] = {}
    if o.get("deprecated"):
        for s in o.get("selects", []):
            if s != "DEPRECATED":
                out.setdefault(s, ("deprecation select", f"{sym} is deprecated and selects {s}"))
    for name, info in old.items():
        if name != sym and name not in out and _default_names(info.get("defaults", []), sym):
            out[name] = ("alias", f"{name} takes its default from {sym}")
    for d in o.get("defaults", []):
        for name in old:
            if name != sym and name not in out and _default_names([d], name):
                out[name] = ("alias", f"{sym} takes its default from {name}")
    return sorted((n, k, w) for n, (k, w) in out.items())


def guide_hits(entries: Iterable[Any], *syms: str) -> list[Any]:
    """Migration-guide entries whose text names any of `syms`."""
    rxs = [re.compile(rf"\b(?:SB_)?(?:CONFIG_)?{re.escape(s)}\b") for s in syms]
    return [e for e in entries if any(rx.search(e.full) for rx in rxs)]


def _loc(info: dict[str, Any]) -> str:
    locs = info.get("locations") or []
    return f" ({locs[0]})" if locs else ""


def classify(
    a: Assignment,
    old: Symbols,
    new: Symbols,
    new_value: str | None,
    entries: list[Any],
    try_value: Callable[[str, str], str | None],
    old_spec: str,
    new_spec: str,
) -> Proposal | None:
    """`new_value` is the symbol's value in the target build (None if unset there);
    `try_value(NEW, value)` re-resolves the target's Kconfig with CONFIG_NEW=value merged
    after the build's fragments and returns what NEW gets (None if that can't be run).
    None: nothing to do (fine, or already invalid at the source)."""
    sym, value = a.symbol, a.value
    if sym not in old:
        return None  # already invalid at the source: not a migration issue
    if sym in new:
        got = new_value if new_value is not None else "n"
        if _same(value, got):
            return None
        deps = new[sym].get("depends") or "y"
        return Proposal(
            a,
            "deps-unmet",
            reasons=(
                f"{a.prefix}{sym} is still defined at {new_spec}{_loc(new[sym])}, but the "
                f"target build gives it {got}; it depends on: {deps}. Not a rename: "
                "enable its dependencies, or drop the assignment",
            ),
        )
    reasons = [f"{a.prefix}{sym} is not defined in the {new_spec} Kconfig tree of {a.image}"]
    cands = kconfig_replacements(sym, old)
    guide_named = sorted(
        {
            r.new
            for e in entries
            for r in getattr(e, "renames", [])
            if getattr(r, "kind", "") == "kconfig" and r.old == sym
        }
    )
    if not cands:
        for g in guide_named:
            where = "is defined" if g in new else "is not defined in this image's tree"
            reasons.append(
                f"the migration guide renames it to {a.prefix}{g}, which {where} at "
                f"{new_spec}; guide text alone is not enough evidence to rename"
            )
        if not guide_named:
            reasons.append(f"no replacement is evidenced in the {old_spec} Kconfig tree")
        reasons.extend(_value_changes(entries, sym, *guide_named))
        return Proposal(a, "needs-human", reasons=tuple(reasons))
    present = [(n, k, w) for n, k, w in cands if n in new]
    if len(present) != 1:
        found = ", ".join(f"{a.prefix}{n} ({w})" for n, _, w in cands)
        reasons.append(
            f"candidates from the {old_spec} Kconfig tree: {found}; "
            + ("none is defined at the target" if not present else "more than one remains")
        )
        return Proposal(a, "needs-human", reasons=tuple(reasons))
    target, kind, why = present[0]
    reasons.append(f"{old_spec}{_loc(old[sym])}: {why}")
    blockers = []
    t_old, t_new = old[sym].get("type"), new[target].get("type")
    if t_old != t_new:
        blockers.append(f"types differ ({sym}: {t_old}, {target}: {t_new})")
    if not new[target].get("prompt"):
        blockers.append(f"{a.prefix}{target} has no prompt at {new_spec}: it can't be set")
    blockers.extend(_value_changes(entries, sym, target))
    if not blockers:
        resolved = try_value(target, value)
        if resolved is None:
            blockers.append(f"could not re-resolve the {new_spec} Kconfig to check {target}")
        elif not _same(value, resolved):
            deps = new[target].get("depends") or "y"
            blockers.append(
                f"at {new_spec}, {a.prefix}{target}={value} resolves to {resolved} (dependencies "
                f"unmet: {deps})"
            )
        else:
            reasons.append(
                f"{a.prefix}{target}{_loc(new[target])}: type {t_new} at both; at "
                f"{new_spec} {a.prefix}{target}={value} resolves to {resolved} (the target's "
                "kconfiglib, after the build's fragments)"
            )
    if blockers:
        reasons.extend(f"not applied: {b}" for b in blockers)
        return Proposal(a, "needs-human", target, kind, tuple(reasons))
    return Proposal(a, "rename", target, kind, tuple(reasons))


def _value_changes(entries: list[Any], *syms: str) -> list[str]:
    out = []
    for e in guide_hits(entries, *syms):
        if m := _VALUE_CHANGE.search(e.full):
            out.append(
                f"the {e.version} migration guide entry ({e.id}) mentions {m[0]!r}: a value "
                "or unit change would carry over silently"
            )
    return out


def _same(a: str, b: str) -> bool:
    def norm(v: str) -> str:
        v = v.strip()
        if re.fullmatch(r"-?\d+|0[xX][0-9a-fA-F]+", v):
            return str(int(v, 0))
        return v.strip('"')

    return norm(a) == norm(b)


@dataclass
class FilePlan:
    file: Path
    path: str
    edits: dict[int, tuple[str, str]] = field(default_factory=dict)


def plan_edits(props: Iterable[Proposal], kind: str) -> dict[Path, FilePlan]:
    """The rename edits of one evidence kind, by file. A line read by several images is
    edited once; if images disagree about it, it is not edited."""
    by_line: dict[tuple[Path, int], set[tuple[str, str] | None]] = {}
    paths: dict[Path, str] = {}
    for p in props:
        a = p.assignment
        key = (a.file, a.line)
        ok = p.category == "rename" and p.evidence_kind == kind and p.new_symbol
        by_line.setdefault(key, set()).add(
            (a.symbol, p.new_symbol) if ok and p.new_symbol else None
        )
        paths[a.file] = a.path
    out: dict[Path, FilePlan] = {}
    for (f, line), edits in sorted(by_line.items()):
        only = next(iter(edits))
        if len(edits) == 1 and only is not None:
            out.setdefault(f, FilePlan(f, paths[f])).edits[line] = only
    return out


def unified_diff(plans: Iterable[FilePlan]) -> str:
    out: list[str] = []
    for fp in plans:
        before = fp.file.read_text()
        after = rewrite(before, fp.edits)
        out.extend(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                f"a/{fp.path}",
                f"b/{fp.path}",
                n=1,
            )
        )
    return "".join(out)


def drop_conflicts(props: list[Proposal]) -> list[Proposal]:
    """A rename into a symbol the same file already assigns is left to a human."""
    out = []
    for p in props:
        if p.category == "rename" and p.new_symbol:
            text = p.assignment.file.read_text()
            if any(s == p.new_symbol for _, _, s, _ in parse_conf(text)):
                p = replace(
                    p,
                    category="needs-human",
                    reasons=(
                        *p.reasons,
                        f"not applied: {p.assignment.path} already assigns "
                        f"{p.assignment.prefix}{p.new_symbol}",
                    ),
                )
        out.append(p)
    return out
