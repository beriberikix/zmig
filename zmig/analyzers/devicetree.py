"""Final devicetree diff, scoped to the nodes, properties and compatibles the app uses.

Sources: each build's edt.pickle read by that tree's own edtlib (nodes, labels,
property values, and the binding's property specs per node), the app's overlays and
C sources (zmig.usage), and devicetree errors from the target configure.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact, find_dt_property
from zmig.diagnostics import app_relative
from zmig.findings import (
    JSON,
    Category,
    Confidence,
    Finding,
    Severity,
    artifact_diff,
    file_line,
    plural,
    tool_output,
)
from zmig.scan import ScanContext
from zmig.usage import Loc, NodeRef, describe, dt_token, resolve


def _ev(locs: list[Loc]) -> tuple[JSON, ...]:
    return tuple(file_line(loc.path, loc.line, loc.text) for loc in locs)


def _dts_view(node: dict[str, Any]) -> tuple[Any, ...]:
    """What zephyr.dts shows for a node: labels and the properties written in the DTS.
    edtlib's `props` also holds binding defaults, which change when only a binding does."""
    raw = set(node["raw_props"])
    return (
        sorted(node["labels"]),
        sorted(raw),
        {k: v for k, v in node["props"].items() if k in raw},
    )


def _prop_by_token(node: dict[str, Any], token: str) -> str | None:
    return next((p for p in node["props"] if dt_token(p) == token), None)


class DevicetreeAnalyzer:
    name: ClassVar[str] = "devicetree"
    kind: ClassVar[Literal["build"]] = "build"
    requires: ClassVar[frozenset[Artifact]] = frozenset({Artifact.EDT})

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        old, new = ctx.old_edt, ctx.new_edt
        on, nn = old["nodes"], new["nodes"]
        findings: list[Finding] = []
        used_old_paths: set[str] = set()

        def add(
            kind: str,
            subject: str,
            title: str,
            sev: Severity,
            conf: Confidence,
            action: str,
            ev: tuple[JSON, ...],
            old_v: Any = None,
            new_v: Any = None,
            heuristics: tuple[str, ...] = (),
        ) -> None:
            findings.append(
                Finding(
                    category=Category.DEVICETREE,
                    kind=kind,
                    subject=subject,
                    title=title,
                    severity=sev,
                    confidence=conf,
                    action=action,
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=ev,
                    old=old_v,
                    new=new_v,
                    heuristics=heuristics,
                )
            )

        # Diagnostics: the target configure rejected the app's devicetree.
        overlays = list(app_relative(ctx.new_raw_facts.overlays, ctx.new_raw.src))
        for d in ctx.new_raw_facts.diagnostics:
            if d.kind == "dt-undeclared-property":
                hit = find_dt_property(ctx.usage.app, overlays, d.symbol)
                ev: tuple[JSON, ...] = ((file_line(hit[0], hit[1]),) if hit else ()) + (
                    tool_output("target configure", ctx.runner.redact(d.message)),
                )
                add(
                    "property-undeclared",
                    f"{d.node}:{d.symbol}",
                    f"Property '{d.symbol}' on {d.node} is no longer declared in its binding "
                    f"in {ctx.new_spec}",
                    Severity.BREAKING,
                    Confidence.HIGH if hit else Confidence.MEDIUM,
                    f"Remove it or move to its replacement (binding: {d.binding}). Zephyr "
                    f"aborts the target configure on it.",
                    ev,
                    heuristics=() if hit else ("devicetree.diag-unlocated",),
                )
            elif d.kind == "dt-error":
                add(
                    "dt-error",
                    d.message[:80],
                    "Devicetree error at the target",
                    Severity.BREAKING,
                    Confidence.HIGH,
                    "Fix the error shown in the evidence.",
                    (tool_output("target configure", ctx.runner.redact(d.message)),),
                )

        # Nodes the app references.
        by_ref: dict[NodeRef, list[Loc]] = {}
        for ref, loc in ctx.usage.dt_nodes:
            by_ref.setdefault(ref, []).append(loc)
        for ref, locs in sorted(by_ref.items(), key=lambda kv: describe(kv[0])):
            po, pn = resolve(ref, old), resolve(ref, new)
            if po:
                used_old_paths.add(po)
            if po and not pn:
                labels = on[po]["labels"]
                moved = next((p for p, n in nn.items() if set(labels) & set(n["labels"])), None)
                hint = (
                    f"The node is now {moved}; refer to it by label "
                    f"(DT_NODELABEL({labels[0]}) / &{labels[0]}) instead."
                    if moved
                    else "Find what replaced it in the target's board files."
                )
                add(
                    "node-gone",
                    describe(ref),
                    f"{describe(ref)} (was {po}) doesn't exist in {ctx.new_spec}",
                    Severity.BREAKING,
                    Confidence.HIGH,
                    hint + " DT_NODE_EXISTS() on it is now false and other macros fail.",
                    (
                        *_ev(locs),
                        artifact_diff("zephyr.dts", ctx.old_spec, ctx.new_spec, po, moved),
                    ),
                    old_v=po,
                    new_v=moved,
                )
                continue
            if not (po and pn):
                continue
            o, n = on[po], nn[pn]
            if po != pn:
                add(
                    "node-moved",
                    describe(ref),
                    f"{describe(ref)} moved from {po} to {pn}",
                    Severity.INFO,
                    Confidence.HIGH,
                    "Label-based references keep working; path-based ones don't.",
                    _ev(locs),
                    old_v=po,
                    new_v=pn,
                )
            if o["status"] in ("okay", "ok") and n["status"] not in ("okay", "ok"):
                add(
                    "node-disabled",
                    describe(ref),
                    f"{describe(ref)} is '{n['status']}' in {ctx.new_spec}",
                    Severity.BREAKING,
                    Confidence.HIGH,
                    'Enable it in an overlay: status = "okay";',
                    _ev(locs),
                    old_v=o["status"],
                    new_v=n["status"],
                )
            changed = {
                k: [o["props"].get(k), n["props"].get(k)]
                for k in sorted(o["props"].keys() | n["props"].keys())
                if o["props"].get(k) != n["props"].get(k)
            }
            if o["binding"] != n["binding"]:
                changed["(binding)"] = [o["binding"], n["binding"]]
            if changed:
                add(
                    "node-changed",
                    f"{describe(ref)}:{pn}",
                    f"{describe(ref)} ({pn}) changed: {', '.join(sorted(changed))}",
                    Severity.RISKY,
                    Confidence.HIGH,
                    "Check these property changes against how your code uses the node.",
                    _ev(locs),
                    new_v=changed,
                )

        # Properties the app's overlays set.
        undeclared = {
            d.symbol for d in ctx.new_raw_facts.diagnostics if d.kind == "dt-undeclared-property"
        }
        for ref, prop, loc in ctx.usage.dt_props_set:
            pn = resolve(ref, new)
            po = resolve(ref, old)
            if pn is None or prop in undeclared:
                continue
            ns, os_ = nn[pn]["specs"], on[po]["specs"] if po else {}
            spec = ns.get(prop)
            if spec and spec["deprecated"] and not os_.get(prop, {}).get("deprecated"):
                add(
                    "property-deprecated",
                    f"{pn}:{prop}",
                    f"Your overlay sets '{prop}' on {pn}, deprecated in {ctx.new_spec}",
                    Severity.RISKY,
                    Confidence.HIGH,
                    "Move to the replacement property named in the binding or migration "
                    "guide before it is removed.",
                    _ev([loc]),
                    old_v=os_.get(prop),
                    new_v=spec,
                )

        # Properties the app's C code reads.
        consequence = {
            "DT_PROP_OR": "DT_PROP_OR() now silently returns its default instead",
            "DT_PROP_LEN_OR": "DT_PROP_LEN_OR() now silently returns its default instead",
            "DT_NODE_HAS_PROP": "DT_NODE_HAS_PROP() is now false",
        }
        for ref, token, loc, macro in ctx.usage.dt_props_read:
            po, pn = resolve(ref, old), resolve(ref, new)
            if not (po and pn):
                continue
            pold = _prop_by_token(on[po], token)
            if pold and not _prop_by_token(nn[pn], token):
                add(
                    "read-property-gone",
                    f"{pn}:{pold}",
                    f"Your code reads '{pold}' of {describe(ref)}, which has no value in "
                    f"{ctx.new_spec}",
                    Severity.BREAKING,
                    Confidence.HIGH,
                    consequence.get(macro, f"{macro}() on it no longer compiles")
                    + "; set it in an overlay, or move to its replacement.",
                    (
                        *_ev([loc]),
                        artifact_diff(
                            "zephyr.dts", ctx.old_spec, ctx.new_spec, on[po]["props"][pold], None
                        ),
                    ),
                    old_v=on[po]["props"][pold],
                )

        # Compatibles the app names (overlays, DT_DRV_COMPAT).
        def has_binding(edt_nodes: dict[str, Any], token: str) -> bool:
            return any(
                n["matching_compat"] and dt_token(n["matching_compat"]) == token
                for n in edt_nodes.values()
            )

        for token, locs in sorted(ctx.usage.dt_compats.items()):
            if has_binding(on, token) and not has_binding(nn, token):
                add(
                    "compatible-unbound",
                    token,
                    f"No node matches compatible {token} through a binding in {ctx.new_spec}",
                    Severity.BREAKING,
                    Confidence.MEDIUM,
                    "The compatible was renamed or its binding removed; check the migration guide.",
                    _ev(locs),
                    heuristics=("devicetree.compat-by-matching-node",),
                )

        # Everything else, split by what changed: the node itself (as zephyr.dts shows
        # it) or only the binding that describes it.
        common = sorted(p for p in on.keys() & nn.keys() if p not in used_old_paths)
        other = [p for p in common if _dts_view(on[p]) != _dts_view(nn[p])]
        binding_only = [
            p
            for p in common
            if p not in other
            and (on[p]["binding"], on[p]["specs"]) != (nn[p]["binding"], nn[p]["specs"])
        ]
        gone = sorted(p for p in on.keys() - nn.keys() if p not in used_old_paths)
        added = sorted(nn.keys() - on.keys())
        if other or gone or added or binding_only:
            add(
                "other-nodes",
                "unreferenced",
                f"{plural(len(other) + len(gone) + len(added), 'node')} your app doesn't "
                f"reference changed, disappeared or appeared"
                + (
                    f"; {plural(len(binding_only), 'more node')} only changed binding"
                    if binding_only
                    else ""
                ),
                Severity.INFO,
                Confidence.HIGH,
                "None needed unless you start using them.",
                (),
                new_v={
                    "changed": other,
                    "removed": gone,
                    "added": added,
                    "binding_changed": binding_only,
                },
            )
        return AnalyzerResult(findings)
