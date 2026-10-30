"""Where the fix goes, and what it is when a finding's own facts say so (schema 1.5).

A pass over every finding after the analyzers have run (docs/PROPOSAL-CHANGE-DB.md §3,
Phase 0). It sets `Finding.fix` and renders `action` from it:

  {"file", "line", "old", "new", "source", "confidence"}

`file`/`line` is the first project location in the evidence. `old`/`new` are set only when
the finding (or one it is linked to) already holds both sides of the edit:

  kconfig symbol-removed / -deprecated  a rename candidate with Kconfig-level evidence, or a
                                        related guide entry's parsed rename
  guide entry-match                     the entry's parsed rename
  api callback-type-changed             the definition site, its type and the expected type
  api enum-removed, target-build-failed the compiler's "did you mean"
  devicetree device-missing             the node's label, as an overlay snippet

Nothing else is changed: no finding is added, dropped or re-graded, so recall and precision
can't move (tested). `source` names the facts used, and `confidence` is never higher than
theirs: a guide rename or a compiler suggestion is at most medium.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

from zmig.findings import JSON, Category, Confidence, Finding

if TYPE_CHECKING:
    from zmig.scan import ScanContext

_RANK = {Confidence.HIGH: 0, Confidence.MEDIUM: 1, Confidence.LOW: 2}
_DID_YOU_MEAN = re.compile(r"'(?P<old>\w+)' undeclared.*?did you mean '(?P<new>\w+)'")
# clang: "initializing 'T' (aka 'canonical T')" / "with an expression of type 'U' (aka …)".
_INIT_TYPE = re.compile(r"initializing '(?P<t>[^']+)'(?: \(aka '(?P<aka>[^']+)'\))?")
_EXPR_TYPE = re.compile(r"with an expression of type '(?P<t>[^']+)'(?: \(aka '(?P<aka>[^']+)'\))?")
_FN_POINTER = re.compile(r"\s*\(\*\s*(?:const\s*|volatile\s*)*\)\s*")
# Actions that only say "go and find the replacement": dropped once the fix names it.
_GENERIC = re.compile(
    r"^(?:Find its replacement in the migration guides\.|Remove the assignment, or find its "
    r"replacement in the migration guides\.|Read [A-Z]+-[\d.]+-\d+ and update the lines listed\.)"
    r"\s*"
)
MAX_PLACES = 3


def _cap(c: Confidence, *caps: Confidence) -> Confidence:
    return max((c, *caps), key=lambda x: _RANK[x])


def project_places(f: Finding) -> list[tuple[str, int, str]]:
    """(path, line, text) of the evidence in the project's own files: not Zephyr's or
    generated ones (`<…>`), not pseudo-locations (`(--board)`), not absolute paths."""
    out = []
    for e in f.evidence:
        if e.get("type") != "file_line":
            continue
        path = str(e["path"])
        if path.startswith(("<", "(", "/", "$")):
            continue
        out.append((path, int(e.get("line") or 0), str(e.get("text") or "")))
    return out


def _fix(
    place: tuple[str, int, str],
    old: str | None,
    new: str | None,
    source: str,
    conf: Confidence,
) -> JSON:
    path, line, _ = place
    return {
        "file": path,
        "line": line or None,
        "old": old,
        "new": new,
        "source": source,
        "confidence": str(conf),
    }


def _renamed_line(text: str, old_sym: str, new_sym: str) -> tuple[str, str]:
    """The assignment as written, and the same line with the symbol renamed."""
    if text and re.search(rf"\bCONFIG_{re.escape(old_sym)}\b", text):
        return text.strip(), re.sub(
            rf"\bCONFIG_{re.escape(old_sym)}\b", f"CONFIG_{new_sym}", text
        ).strip()
    return f"CONFIG_{old_sym}", f"CONFIG_{new_sym}"


def _guide_rename(f: Finding) -> tuple[str, str, str] | None:
    """(old, new, entry) from a guide finding's parsed rename."""
    new = f.new if isinstance(f.new, dict) else {}
    to, item = new.get("renamed_to"), new.get("item") or {}
    if not to or not item.get("value"):
        return None
    pre = "CONFIG_" if item.get("kind") == "kconfig" else ""
    return f"{pre}{item['value']}", f"{pre}{to}", str(new.get("entry", ""))


def _kconfig(f: Finding, by_id: dict[str, Finding], place: tuple[str, int, str]) -> JSON | None:
    sym = f.subject.removeprefix("CONFIG_")
    cands = (f.new or {}).get("candidates", []) if isinstance(f.new, dict) else []
    kconfig_level = [c for c in cands if c.get("confidence") in ("high", "medium")]
    if kconfig_level:
        c = kconfig_level[0]
        old, new = _renamed_line(place[2], sym, c["symbol"])
        conf = _cap(f.confidence, Confidence(c["confidence"]))
        return _fix(place, old, new, f"kconfig: {c['why']}", conf)
    for rid in f.related:
        g = by_id.get(rid)
        r = _guide_rename(g) if g is not None and g.category is Category.GUIDE else None
        if r is not None and r[0] == f"CONFIG_{sym}":
            old, new = _renamed_line(place[2], sym, r[1].removeprefix("CONFIG_"))
            return _fix(place, old, new, f"guide: {r[2]}", _cap(f.confidence, Confidence.MEDIUM))
    if cands:
        c = cands[0]
        old, new = _renamed_line(place[2], sym, c["symbol"])
        return _fix(
            place, old, new, f"kconfig: {c['why']}", _cap(f.confidence, Confidence(c["confidence"]))
        )
    return None


def _function_type(t: str) -> str:
    """`R (*const)(A)` / `R (*)(A)` -> `R (A)`: the pointer's function type."""
    return " ".join(_FN_POINTER.sub(" ", t, count=1).split())


def _did_you_mean(
    errors: list[JSON], where: tuple[str, int] | None = None
) -> tuple[str, int, str, str] | None:
    for e in errors:
        m = _DID_YOU_MEAN.search(str(e.get("message", "")))
        if not m:
            continue
        path = str(e.get("file", "")).removeprefix("<app>/")
        if path.startswith(("<", "$", "/")):
            continue
        line = int(e.get("line") or 0)
        if where is None or where == (path, line):
            return path, line, m["old"], m["new"]
    return None


def _derive(f: Finding, by_id: dict[str, Finding], ctx: ScanContext) -> JSON | None:
    places = project_places(f)
    if not places:
        return None
    place = places[0]
    new = f.new if isinstance(f.new, dict) else {}
    if f.category is Category.KCONFIG and f.kind in ("symbol-removed", "symbol-deprecated"):
        if fx := _kconfig(f, by_id, place):
            return fx
    elif f.kind == "entry-match" and (r := _guide_rename(f)):
        # Only a line that contains the old name can take the edit (DOGFOOD-9 D1: RIT's
        # `DEVICE_DT_GET(DT_ALIAS(thermometer))` is where the node is used, not named).
        names = {r[0], str((new.get("item") or {}).get("value", ""))} - {""}
        named = re.compile("|".join(rf"(?<![\w,-]){re.escape(n)}(?![\w-])" for n in names))
        at = next((p for p in places if named.search(p[2])), None)
        if at is not None:
            return _fix(at, r[0], r[1], f"guide: {r[2]}", _cap(f.confidence, Confidence.MEDIUM))
    elif f.kind == "callback-type-changed":
        defs = [p for p in places if p[2].endswith(": definition to change")]
        msg = str(new.get("message", ""))
        want, have = _INIT_TYPE.search(msg), _EXPR_TYPE.search(msg)
        if defs and want and have:
            # The canonical type ("aka"), as a function type: a typedef name can't be given
            # to a function (DOGFOOD-9 D2: `const state_execution`).
            expected = _function_type(want["aka"] or want["t"])
            old = _function_type(have["aka"] or have["t"])
            return _fix(defs[0], old, expected, "api: the type Zephyr expects", f.confidence)
    elif f.kind == "enum-removed":
        for g in by_id.values():
            if g.kind == "target-build-failed" and isinstance(g.new, dict):
                hit = _did_you_mean(g.new.get("errors", []), (place[0], place[1]))
                if hit and hit[2] == f.subject:
                    return _fix(place, hit[2], hit[3], "compiler: did you mean",
                                _cap(f.confidence, Confidence.MEDIUM))  # fmt: skip
    elif f.kind == "target-build-failed":
        if hit := _did_you_mean(new.get("errors", [])):
            return _fix((hit[0], hit[1], ""), hit[2], hit[3], "compiler: did you mean",
                        _cap(f.confidence, Confidence.MEDIUM))  # fmt: skip
    elif f.kind == "device-missing" and (label := _label(ctx, str(new.get("node", "")))):
        # The fix goes in an overlay the build applies, not at the code that takes the
        # device (bms-firmware B5: app/boards/native_sim.overlay).
        overlay = _applied_overlay(ctx)
        snippet = f'&{label} {{ status = "okay"; }};'
        return _fix((overlay, 0, "") if overlay else place, None, snippet,
                    "devicetree: the node is disabled at the target", f.confidence)  # fmt: skip
    return _fix(place, None, None, "evidence", f.confidence)


def _label(ctx: ScanContext, node: str) -> str | None:
    """The node's first label at the target, else at the source."""
    from zmig.buildctx import Artifact

    for side, img in (("new_edt", ctx.new_img), ("old_edt", ctx.old_img)):
        if img is None or not img.has(Artifact.EDT):
            continue
        labels = getattr(ctx, side)["nodes"].get(node, {}).get("labels") or []
        if labels:
            return str(labels[0])
    return None


def _applied_overlay(ctx: ScanContext) -> str | None:
    """The first app overlay the source build applied, as evidence shows paths."""
    from zmig.diagnostics import app_relative

    facts, old = getattr(ctx, "old_facts", None), getattr(ctx, "old", None)
    if facts is None or old is None:
        return None
    return next(iter(app_relative(facts.overlays, old.src)), None)


def _places_text(f: Finding, first: tuple[str, int]) -> str:
    """The fix's place first, then the first line of each other project file."""
    seen: dict[str, str] = {}
    for path, line, _ in [(first[0], first[1], ""), *project_places(f)]:
        seen.setdefault(path, f"{path}:{line}" if line else path)
    places = list(seen.values())
    return ", ".join(places[:MAX_PLACES]) + (", …" if len(places) > MAX_PLACES else "")


def render(f: Finding, fix: JSON) -> str:
    """The action, led by where (and, when known, what) to change."""
    where = _places_text(f, (fix["file"], fix["line"] or 0))
    rest = f.action
    if fix["old"] is not None and fix["new"] is not None:
        what = f"change `{fix['old']}` to `{fix['new']}`"
        if f.kind == "callback-type-changed":
            name = next(
                (
                    p[2].split(":")[0]
                    for p in project_places(f)
                    if p[2].endswith("definition to change")
                ),
                "",
            )
            what = f"change {name or 'the definition'}'s type from `{fix['old']}` to `{fix['new']}`"
        rest = _GENERIC.sub("", rest)
        head = f"In {where}, {what} ({fix['source']}; {fix['confidence']} confidence)."
    elif fix["new"] is not None:
        head = f"In {where}, add `{fix['new']}` ({fix['source']}; {fix['confidence']} confidence)."
    else:
        loc = f"{fix['file']}:{fix['line']}" if fix["line"] else fix["file"]
        if loc in rest:
            return rest  # the action already says where
        if rest[:1].isupper() and rest[1:2].islower():
            rest = rest[0].lower() + rest[1:]
        return f"In {where}: {rest}"
    return f"{head} {rest}".strip()


def apply_fixes(findings: list[Finding], ctx: ScanContext) -> list[Finding]:
    """Every finding with a project location gets `fix` and an action that names it."""
    by_id = {f.id: f for f in findings}
    out = []
    for f in findings:
        fix = _derive(f, by_id, ctx)
        out.append(replace(f, fix=fix, action=render(f, fix)) if fix else f)
    return out
