"""Final devicetree diff, scoped to the nodes, properties and compatibles the app uses.

Sources: each build's edt.pickle read by that tree's own edtlib (nodes, labels,
property values, and the binding's property specs per node), the app's overlays and
C sources (zmig.usage), and devicetree errors from the target configure.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.analyzers.changes import bound_compatible, unbound_compatible
from zmig.buildctx import Artifact, find_dt_property
from zmig.diagnostics import app_relative, preexisting
from zmig.errors import ZmigError
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
from zmig.usage import SOURCE_SUFFIXES, Loc, NodeRef, describe, dt_token, resolve


def _ev(locs: list[Loc]) -> tuple[JSON, ...]:
    return tuple(file_line(loc.path, loc.line, loc.text) for loc in locs)


# Numbers dtc assigns to referenced nodes: a renumbering between builds changes nothing
# (regression: CANnectivity's led0 and counter2 "changed: phandle",
# docs/DOGFOOD-4-baseline.md M13).
_ASSIGNED_BY_DTC = frozenset({"phandle", "linux,phandle"})


def _written(node: dict[str, Any]) -> dict[str, Any]:
    """The properties written in the DTS, without the ones dtc assigns. Whitespace is
    collapsed: dtlib's rendering wraps long cell lists differently between versions
    (regression: equal `clocks`/`irq-gpios` reported as changed, DOGFOOD-8 B2)."""
    return {
        k: " ".join(v.split()) if isinstance(v, str) else v
        for k, v in node["raw_values"].items()
        if k not in _ASSIGNED_BY_DTC
    }


def _dts_view(node: dict[str, Any]) -> tuple[Any, ...]:
    """What zephyr.dts shows for a node: labels and the properties written in the DTS.
    edtlib's `props` also holds binding defaults, which change when only a binding does."""
    return (sorted(node["labels"]), _written(node))


def _prop_by_token(node: dict[str, Any], token: str) -> str | None:
    return next((p for p in node["props"] if dt_token(p) == token), None)


def spec_changes(old: dict[str, Any], new: dict[str, Any], prop: str) -> list[tuple[str, Any, Any]]:
    """How the binding's spec of a property the app uses changed: ("type", old, new) and
    ("deprecated", False, True). A newly required property that's used is already present,
    and a gone one is reported by the analyzer's other checks."""
    o, n = old.get(prop), new.get(prop)
    if not o or not n:
        return []
    out: list[tuple[str, Any, Any]] = []
    if o["type"] != n["type"]:
        out.append(("type", o["type"], n["type"]))
    if n["deprecated"] and not o["deprecated"]:
        out.append(("deprecated", False, True))
    return out


def _compat_database(ctx: ScanContext) -> dict[str, Any]:
    """The change database, for a removed binding's replacement; without it (a ref the
    mirror can't build it for) the finding stands without one."""
    from zmig.analyzers.changes import database

    try:
        return database(ctx)
    except ZmigError:
        return {"devicetree": {"removed": {}}}


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
            introduced: bool = True,
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
                    introduced_by_migration=introduced,
                )
            )

        # Diagnostics: the target configure rejected the app's devicetree. The same error
        # at the source is pre-existing; it keeps its severity (docs/DESIGN-P1.md §2.3).
        overlays = list(app_relative(ctx.new_raw_facts.overlays, ctx.new_raw.src))
        for d in ctx.new_raw_facts.diagnostics:
            pre = preexisting(d, ctx.old_facts.diagnostics)
            also = " (pre-existing: the source configure printed the same error)" if pre else ""
            if d.kind == "dt-undeclared-property":
                hit = find_dt_property(ctx.usage.app, overlays, d.symbol)
                ev: tuple[JSON, ...] = ((file_line(hit[0], hit[1]),) if hit else ()) + (
                    tool_output("target configure", ctx.runner.redact(d.message)),
                )
                add(
                    "property-undeclared",
                    f"{d.node}:{d.symbol}",
                    f"Property '{d.symbol}' on {d.node} is no longer declared in its binding "
                    f"in {ctx.new_spec}" + also,
                    Severity.BREAKING,
                    Confidence.HIGH if hit else Confidence.MEDIUM,
                    f"Remove it or move to its replacement (binding: {d.binding}). Zephyr "
                    f"aborts the target configure on it.",
                    ev,
                    heuristics=() if hit else ("devicetree.diag-unlocated",),
                    introduced=not pre,
                )
            elif d.kind == "dt-error" and d.symbol:
                # A property the binding now requires (guide 4.2: st,stm32-ethernet needs
                # phy-handle). The target configure stops on it, so this error is the only
                # evidence; same kind and subject as any dt-error, so IDs don't change.
                add(
                    "dt-error",
                    d.message[:80],
                    f"{d.node} lacks '{d.symbol}', which its binding requires in "
                    f"{ctx.new_spec}" + also,
                    Severity.BREAKING,
                    Confidence.HIGH,
                    f"Add '{d.symbol}' to {d.node} in an overlay (binding: "
                    f"{ctx.runner.redact(d.binding)}); the migration guide names its value.",
                    (tool_output("target configure", ctx.runner.redact(d.message)),),
                    new_v={
                        "cause": "required-property-missing",
                        "property": d.symbol,
                        "node": d.node,
                        "binding": ctx.runner.redact(d.binding),
                    },
                    introduced=not pre,
                )
            elif d.kind == "dt-error":
                add(
                    "dt-error",
                    d.message[:80],
                    "Devicetree error at the target" + also,
                    Severity.BREAKING,
                    Confidence.HIGH,
                    "Fix the error shown in the evidence.",
                    (tool_output("target configure", ctx.runner.redact(d.message)),),
                    introduced=not pre,
                )

        # Nodes the app references.
        by_ref: dict[NodeRef, list[Loc]] = {}
        built = ctx.build_files
        for ref, loc in ctx.usage.dt_nodes:
            # A reference in a source file this build doesn't compile isn't this build's
            # (regression: CANnectivity's src/dfu.c, only built with MCUboot DFU,
            # docs/DOGFOOD-4-baseline.md M15). Overlays are judged by outside_build().
            if built is not None and loc.path.endswith(SOURCE_SUFFIXES) and loc.path not in built:
                continue
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
            # Properties written in the DTS (either side) vs. binding defaults that
            # appeared, disappeared or changed with the binding alone.
            # What the DTS writes (raw text) vs. binding defaults that appeared,
            # disappeared or changed with the binding alone.
            ro, rn = _written(o), _written(n)
            written = ro.keys() | rn.keys()
            changed = {k: [ro.get(k), rn.get(k)] for k in sorted(written) if ro.get(k) != rn.get(k)}
            defaults = {
                k: [o["props"].get(k), n["props"].get(k)]
                for k in sorted(o["props"].keys() | n["props"].keys())
                if k not in written
                and k not in _ASSIGNED_BY_DTC
                and o["props"].get(k) != n["props"].get(k)
            }
            if _binding(o["binding"]) != _binding(n["binding"]):
                changed["(binding)"] = [o["binding"], n["binding"]]
            # Properties only the target has (e.g. easydma-maxcnt-bits, new_props to nRF SoC
            # .dtsi files in v3.5) are reported apart: nothing the app relied on changed.
            new_props = {k: v for k, v in changed.items() if v[0] is None and k != "(binding)"}
            if new_props and len(new_props) == len(changed):
                add(
                    "node-properties-new_props",
                    f"{describe(ref)}:{pn}",
                    f"{describe(ref)} ({pn}) has new properties in {ctx.new_spec}: "
                    f"{', '.join(sorted(new_props))}",
                    Severity.INFO,
                    Confidence.HIGH,
                    "New hardware description, usually used by the driver; check it if you "
                    "configure this node's behavior yourself.",
                    _ev(locs),
                    new_v=new_props,
                )
                changed = {}
            if changed:
                # A node this image has no device for at either ref (its subsystem is off
                # here: OpenDeck's MCUboot has no Ethernet, its bootloader no ADC) can't
                # affect this image (DOGFOOD-5 M19). Unknown when either ELF is missing.
                unused = _no_device(ctx, o, n)
                add(
                    "node-changed",
                    f"{describe(ref)}:{pn}",
                    f"{describe(ref)} ({pn}) changed: {', '.join(sorted(changed))}"
                    + (f" (no device for it in {ctx.image or 'this'} image)" if unused else ""),
                    Severity.INFO if unused else Severity.RISKY,
                    Confidence.HIGH,
                    "Check these property changes against how your code uses the node."
                    if not unused
                    else "Nothing in this image instantiates the node; it matters only where "
                    "a driver for it is built.",
                    _ev(locs),
                    new_v={**changed, **({"binding_defaults": defaults} if defaults else {})},
                    heuristics=("devicetree.no-device-in-image",) if unused else (),
                )
            elif defaults:
                add(
                    "node-binding-defaults",
                    f"{describe(ref)}:{pn}",
                    f"{describe(ref)} ({pn}): binding defaults changed, not set in the DTS: "
                    f"{', '.join(sorted(defaults))}",
                    Severity.INFO,
                    Confidence.HIGH,
                    "Only the binding changed; set the property explicitly if the new default "
                    "matters to you.",
                    _ev(locs),
                    new_v=defaults,
                )

        # Properties the app's overlays set.
        undeclared = {
            d.symbol for d in ctx.new_raw_facts.diagnostics if d.kind == "dt-undeclared-property"
        }
        # Binding spec changes of properties the app uses, one finding per (kind, property)
        # whether the overlay sets it, the C code reads it, or both (AUDIT.md §6 item 6).
        spec_seen: set[tuple[str, str]] = set()

        def type_changed(pn: str, prop: str, was: str, now: str, locs: list[Loc]) -> None:
            if ("type", f"{pn}:{prop}") in spec_seen:
                return
            spec_seen.add(("type", f"{pn}:{prop}"))
            add(
                "property-type-changed",
                f"{pn}:{prop}",
                f"The binding of {pn} changed the type of '{prop}', which your app uses, "
                f"from {was} to {now} in {ctx.new_spec}",
                Severity.RISKY,
                Confidence.HIGH,
                f"Check how the value is written and read: DT_PROP() of a {now} expands "
                f"differently from a {was}.",
                _ev(locs),
                old_v=was,
                new_v=now,
            )

        for ref, prop, loc in ctx.usage.dt_props_set:
            pn = resolve(ref, new)
            po = resolve(ref, old)
            if pn is None or prop in undeclared:
                continue
            ns, os_ = nn[pn]["specs"], on[po]["specs"] if po else {}
            spec = ns.get(prop)
            for what, was, now in spec_changes(os_, ns, prop):
                if what == "type":
                    type_changed(pn, prop, was, now, [loc])
            if spec and spec["deprecated"] and not os_.get(prop, {}).get("deprecated"):
                spec_seen.add(("deprecated", f"{pn}:{prop}"))
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
            pnew = _prop_by_token(nn[pn], token)
            if pold and pnew:
                for what, was, now in spec_changes(on[po]["specs"], nn[pn]["specs"], pnew):
                    if what == "type":
                        type_changed(pn, pnew, was, now, [loc])
                    elif ("deprecated", f"{pn}:{pnew}") not in spec_seen:
                        spec_seen.add(("deprecated", f"{pn}:{pnew}"))
                        add(
                            "property-deprecated",
                            f"{pn}:{pnew}",
                            f"Your code reads '{pnew}' of {describe(ref)}, deprecated in "
                            f"{ctx.new_spec}",
                            Severity.RISKY,
                            Confidence.HIGH,
                            "Move to the replacement property named in the binding or "
                            "migration guide before it is removed.",
                            _ev([loc]),
                            old_v=on[po]["specs"].get(pnew),
                            new_v=nn[pn]["specs"].get(pnew),
                        )
            if pold and not pnew:
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

        # Compatibles the app names (overlays, DT_DRV_COMPAT) or the project's own DTS
        # outside the app does (a board of its own, RIT FSW F1a), bound at the source, that a
        # target node still lists with no binding matching it. A node gone at the target
        # isn't unbound (regression: CoffeeCaller's unapplied native_sim.overlay, whose
        # gpio-keys node was reported as if gpio-keys had lost its binding). The change
        # database names the binding's replacement, when there is one.
        listed = {dt_token(c) for n in nn.values() for c in n["compats"]}
        unbound = [
            (token, compat, locs)
            for token, locs in sorted(ctx.usage.dt_compats.items())
            if (compat := bound_compatible(old, token))
            and token in listed
            and not bound_compatible(new, token)
        ]
        removed = _compat_database(ctx)["devicetree"]["removed"] if unbound else {}
        for token, compat, locs in unbound:
            findings.append(unbound_compatible(ctx, token, compat, locs, removed, self.name))

        # Everything else, split by what changed: the node itself (as zephyr.dts shows
        # it) or only the binding that describes it.
        common = sorted(p for p in on.keys() & nn.keys() if p not in used_old_paths)
        other = [p for p in common if _dts_view(on[p]) != _dts_view(nn[p])]
        binding_only = [
            p
            for p in common
            if p not in other
            and (_binding(on[p]["binding"]), on[p]["specs"])
            != (_binding(nn[p]["binding"]), nn[p]["specs"])
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


_APP_COPY = re.compile(r"^(?:.*/)?apps/[0-9a-f]+/[^/]+/")


_DEVICE_SYM = re.compile(r"^__device_dts_ord_(\d+)$")
_device_ords: dict[str, frozenset[int]] = {}


def device_ords(elf: Path) -> frozenset[int]:
    """Devicetree ordinals that have a device object (`__device_dts_ord_<N>`) in an ELF."""
    key = str(elf)
    if key not in _device_ords:
        from elftools.elf.elffile import ELFFile
        from elftools.elf.sections import SymbolTableSection

        found: set[int] = set()
        with elf.open("rb") as f:
            for sec in ELFFile(f).iter_sections():
                if isinstance(sec, SymbolTableSection):
                    for sym in sec.iter_symbols():
                        if m := _DEVICE_SYM.match(sym.name):
                            found.add(int(m[1]))
        _device_ords[key] = frozenset(found)
    return _device_ords[key]


def _no_device(ctx: ScanContext, o: dict[str, Any], n: dict[str, Any]) -> bool:
    """Both images built, both define devices, the node has a compatible, and neither
    image has a device for it."""
    if not (ctx.old_img.has(Artifact.ELF) and ctx.new_img.has(Artifact.ELF)):
        return False
    if not (o.get("compats") or n.get("compats")) or o.get("ord") is None:
        return False
    old_d, new_d = (
        device_ords(ctx.old_img.artifact(Artifact.ELF)),
        device_ords(ctx.new_img.artifact(Artifact.ELF)),
    )
    if not old_d or not new_d:
        return False
    return o["ord"] not in old_d and n.get("ord") not in new_d


def _binding(path: str | None) -> str | None:
    """A binding path with zmig's per-build app copy (apps/<hash>/<app>/) normalized
    (regression: every app-provided binding in ZMK looked changed between refs)."""
    return _APP_COPY.sub("<app>/", path) if path else path
