"""Migration-guide and release-note entries that touch what the app actually uses.

Documents are read for every release crossed (from, to], from the target tree when it
still has them, else from that release's tag. Entries are kept only when one of their
mentions matches the app: a Kconfig symbol it sets or reads, a compatible it names or
whose nodes it references, a C identifier it uses, a header it includes, or a
devicetree property / board name it uses. Each match points at the guide line and the
app lines.

Confidence is the lower of how the guide named the item (a Sphinx role: medium; a plain
literal: low) and how the app uses it (exact Kconfig/DT/include reference, or an
identifier confirmed by `nm` on the app's objects: high; an identifier seen only by
token scan: low).
"""

from __future__ import annotations

import re
from typing import ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact
from zmig.findings import (
    JSON,
    Category,
    Confidence,
    Finding,
    Severity,
    doc_entry,
    file_line,
    plural,
)
from zmig.guides import Document, Entry, ItemKind, Mention
from zmig.scan import ScanContext
from zmig.usage import Loc, dt_token, resolve

MAX_APP_LOCS = 10
GENERIC_DT_PROPERTIES = frozenset(
    {
        "reg",
        "status",
        "compatible",
        "label",
        "ranges",
        "interrupts",
        "interrupt-parent",
        "clocks",
        "pinctrl-0",
        "pinctrl-names",
        "#address-cells",
        "#size-cells",
        "reg-names",
    }
)
_SEV = {"breaking": Severity.BREAKING, "risky": Severity.RISKY, "info": Severity.INFO}
_GATE_WORDS = re.compile(r"\b(when|if|only|unless)\b", re.I)
_SEV_RANK = {"breaking": 0, "risky": 1, "info": 2}
_CONF_RANK = {Confidence.HIGH: 0, Confidence.MEDIUM: 1, Confidence.LOW: 2}


def _min_conf(*cs: Confidence) -> Confidence:
    return max(cs, key=lambda c: _CONF_RANK[c])


class GuideAnalyzer:
    name: ClassVar[str] = "guide"
    per_image: ClassVar[bool] = False  # matches the whole app against the guides
    kind: ClassVar[Literal["build"]] = "build"
    requires: ClassVar[frozenset[Artifact]] = frozenset()

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        u = ctx.app_usage
        linked = ctx.app_linked_symbols
        # Compatibles of the nodes the app references, and which of them are enabled in
        # either build (a guide entry about a driver matters if its node is enabled).
        node_compats: dict[str, list[Loc]] = {}
        enabled_compats: set[str] = set()
        sides = []  # old first: node_compats records the app's references at the source
        if ctx.old_img.has(Artifact.EDT):
            sides.append(ctx.old_edt)
        if ctx.new_img.has(Artifact.EDT):
            sides.append(ctx.new_edt)
        for i, side in enumerate(sides):
            for ref, loc in u.dt_nodes:
                if (p := resolve(ref, side)) is None:
                    continue
                node = side["nodes"][p]
                for c in node["compats"]:
                    if i == 0 and ctx.old_img.has(Artifact.EDT):
                        node_compats.setdefault(dt_token(c), []).append(loc)
                    if node["status"] in ("okay", "ok"):
                        enabled_compats.add(dt_token(c))
        have_config = ctx.old_img.has(Artifact.CONFIG) and ctx.new_img.has(Artifact.CONFIG)

        def enabled(sym: str) -> bool:
            return any(
                cfg.get(sym, "n") not in ("n", "") for cfg in (ctx.old_config, ctx.new_config)
            )

        def gate(entry: Entry, kind: ItemKind, value: str, mentions: list[Mention]) -> str | None:
            """Why a match probably doesn't apply to this build, from its resolved
            configuration and devicetree; None if it may apply."""
            tok = dt_token(value)
            if kind == "compat" and not u.dt_compats.get(tok) and tok not in enabled_compats:
                return f"your nodes with {value} are disabled in both builds"
            if not have_config:
                return None
            partners = {r.new for r in entry.renames if r.old == value} | {
                r.old for r in entry.renames if r.new == value
            }
            reasons = []
            for m in mentions:
                gates = [
                    x.value
                    for x in entry.mentions
                    if x.kind == "kconfig"
                    and x.sentence == m.sentence
                    and x.value not in (value, *partners)
                ]
                if not (
                    _GATE_WORDS.search(m.sentence) and gates and not any(enabled(g) for g in gates)
                ):
                    return None
                reasons.append(gates[0])
            return (
                f"it applies only with CONFIG_{reasons[0]}, which neither build enables"
                if reasons
                else None
            )

        props_set: dict[str, list[Loc]] = {}
        for _, prop, loc in u.dt_props_set:
            props_set.setdefault(prop, []).append(loc)
        props_read: dict[str, list[Loc]] = {}
        for _, token, loc, _ in u.dt_props_read:
            props_read.setdefault(token, []).append(loc)
        boards = {ctx.old.spec.board.split("@")[0].split("/")[0], ctx.old.spec.board}

        def hits(kind: ItemKind, value: str) -> tuple[list[Loc], Confidence, list[str]]:
            """App locations matching an item, the usage confidence, and heuristics."""
            if kind == "kconfig":
                locs = [loc for loc, _ in u.kconfig_set.get(value, [])]
                return locs + u.kconfig_read.get(value, []), Confidence.HIGH, []
            if kind == "compat":
                tok = dt_token(value)
                return (u.dt_compats.get(tok, []) + node_compats.get(tok, []), Confidence.HIGH, [])
            if kind == "ident":
                locs = u.identifiers.get(value, [])
                # Syscall APIs link against z_impl_<name> when userspace is off.
                if linked is not None and (value in linked or f"z_impl_{value}" in linked):
                    return locs, Confidence.HIGH, []
                return locs, Confidence.LOW, ["guide.identifier-token-scan"] if locs else []
            if kind == "header":
                want = value.removeprefix("include/")
                exact = [loc for inc, ls in u.includes.items() if inc == want for loc in ls]
                if exact or "/" in want:
                    return exact, Confidence.HIGH, []
                base = [
                    loc
                    for inc, ls in u.includes.items()
                    if inc.rsplit("/", 1)[-1] == want
                    for loc in ls
                ]
                return base, Confidence.LOW, ["guide.header-basename"] if base else []
            # A plain literal: DT property names, underscore identifiers, board names.
            # Properties every node has are too generic to tie an entry to the app.
            if value in GENERIC_DT_PROPERTIES:
                return [], Confidence.HIGH, []
            locs = props_set.get(value, []) + props_read.get(dt_token(value), [])
            if "_" in value and len(value) >= 4:
                locs += u.identifiers.get(value, [])
            if value in boards:
                locs.append(Loc("(--board)", 1, value))
            return locs, Confidence.HIGH, []

        findings: list[Finding] = []
        docs = ctx.guide_documents
        entries = ctx.guide_entries

        matched_entries: set[str] = set()
        for doc, entry in entries:
            items: dict[tuple[ItemKind, str], list[Mention]] = {}
            for m in entry.mentions:
                items.setdefault((m.kind, m.value), []).append(m)
            for (kind, value), mentions in sorted(items.items()):
                locs, usage_conf, heur = hits(kind, value)
                locs = [loc for loc in locs if not loc.path.startswith("(")] or locs
                if not locs:
                    continue
                matched_entries.add(entry.id)
                findings.append(
                    self._finding(
                        gate(entry, kind, value, mentions),
                        ctx,
                        doc,
                        entry,
                        kind,
                        value,
                        mentions,
                        locs,
                        usage_conf,
                        heur,
                    )
                )
            if ctx.all_guide_entries and entry.id not in matched_entries:
                findings.append(
                    Finding(
                        category=Category.GUIDE,
                        kind="entry-unmatched",
                        subject=entry.id,
                        title=f"{doc.version} guide: {entry.lead[:140]}",
                        severity=Severity.INFO,
                        confidence=Confidence.LOW,
                        action="Not matched to your app; listed because of --all-guide-entries.",
                        analyzer=self.name,
                        scope=ctx.scope,
                        evidence=(doc_entry(doc.path, entry.line, doc.ref, entry.id),),
                    )
                )

        findings.append(
            Finding(
                category=Category.GUIDE,
                kind="coverage",
                subject="documents",
                title=f"Read {len(entries)} entries from {plural(len(docs), 'document')}; "
                f"{len(matched_entries)} touch your app",
                severity=Severity.INFO,
                confidence=Confidence.HIGH,
                action="Unmatched entries don't reference anything zmig found in your app; rerun "
                "with --all-guide-entries to list them.",
                analyzer=self.name,
                scope=ctx.scope,
                new={
                    "documents": [
                        {"doc": d.path, "ref": d.ref, "version": d.version} for d in docs
                    ],
                    "entries": len(entries),
                    "matched": sorted(matched_entries),
                },
            )
        )
        return AnalyzerResult(findings)

    def _finding(
        self,
        gated: str | None,
        ctx: ScanContext,
        doc: Document,
        entry: Entry,
        kind: ItemKind,
        value: str,
        mentions: list[Mention],
        locs: list[Loc],
        usage_conf: Confidence,
        heur: list[str],
    ) -> Finding:
        worst = min(mentions, key=lambda m: _SEV_RANK[m.severity])
        lead_clause = entry.lead.split(":")[0] if ":" in entry.lead[:120] else entry.lead
        headline = worst.sentence if len(worst.sentence) >= 40 else f"{lead_clause} [{value}]"
        strength = (
            Confidence.MEDIUM if any(m.strength == "role" for m in mentions) else Confidence.LOW
        )
        conf = _min_conf(strength, usage_conf)
        heuristics = {
            "guide.severity-from-wording",
            "guide.role-match" if strength is Confidence.MEDIUM else "guide.literal-match",
            *heur,
        }
        rename = next((r for r in entry.renames if r.old == value and r.kind == kind), None)
        if rename:
            heuristics.add("guide.rename-pattern")
        severity = _SEV[worst.severity]
        if gated:
            heuristics.add("guide.gated-by-build")
            severity, conf = Severity.INFO, Confidence.LOW
        shown = f"CONFIG_{value}" if kind == "kconfig" else value
        action = (
            f"The guide says {shown} was renamed to "
            f'{"CONFIG_" if kind == "kconfig" else ""}{rename.new}: "{rename.sentence}"'
            if rename
            else f"Read {entry.id} and update the lines listed."
        )
        if gated:
            action = f"Probably not relevant: {gated}. " + action
        ev: list[JSON] = []
        for line in sorted({m.line for m in mentions}):
            sentence = next(m.sentence for m in mentions if m.line == line)
            ev.append(doc_entry(doc.path, line, doc.ref, entry.id, sentence[:300]))
        ev += [file_line(loc.path, loc.line, loc.text) for loc in locs[:MAX_APP_LOCS]]
        return Finding(
            category=Category.GUIDE,
            kind="entry-match",
            subject=f"{entry.id}:{kind}:{value}",
            title=f"{doc.version} {'migration guide' if doc.kind == 'guide' else 'release notes'}"
            f" ({' / '.join(entry.headings[-1:]) or 'general'}): {headline[:160]}",
            severity=severity,
            confidence=conf,
            action=action,
            analyzer=self.name,
            scope=ctx.scope,
            evidence=tuple(ev),
            new={
                "entry": entry.id,
                "doc": doc.path,
                "ref": doc.ref,
                "item": {"kind": kind, "value": value},
                "headings": list(entry.headings),
                **({"renamed_to": rename.new} if rename else {}),
            },
            related=tuple(self._related(ctx.prior, kind, value)),
            heuristics=tuple(sorted(heuristics)),
        )

    @staticmethod
    def _related(prior: list[Finding], kind: ItemKind, value: str) -> list[str]:
        out = []
        for f in prior:
            s = f.subject
            if (
                (kind == "kconfig" and s == f"CONFIG_{value}")
                or (
                    f.category is Category.DEVICETREE
                    and (
                        s.endswith(f":{value}")
                        or s == value
                        or dt_token(s.rsplit(":", 1)[-1]) == dt_token(value)
                    )
                )
                or (f.category is Category.BOARD and s == value)
            ):
                out.append(f.id)
        return sorted(set(out))
