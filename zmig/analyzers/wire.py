"""Wire formats peers depend on (docs/DESIGN-P1.md §7): an allowlist, not inference.

Entries come from zmig/data/wire_format.yaml plus --wire-allowlist. For every image pair,
an entry whose resolved value differs is a finding naming the entry id. Each entry is
first checked to exist at both refs (Kconfig: the symbol is defined in some image's
Kconfig; dt: the binding file named by `source` exists); a missing entry is reported as
info and doesn't count as checked. "Clean" means only that no allowlisted item changed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal

import yaml

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact, ImageContext, pair_images
from zmig.findings import Category, Confidence, Finding, NotChecked, Remedy, Severity, file_line
from zmig.hazards import CategoryStatus, provenance, set_category
from zmig.sbom import compiled_sources, read_spdx_dir
from zmig.scan import ScanContext
from zmig.snapshots import config_values, edt, kconfig_symbols
from zmig.srcdiff import Source

SHIPPED = Path(__file__).parents[1] / "data" / "wire_format.yaml"


def load_entries(extra: Path | None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = list(yaml.safe_load(SHIPPED.read_text())["entries"])
    if extra is not None:
        for e in (yaml.safe_load(extra.read_text()) or {}).get("entries", []):
            entries.append({**e, "user": True})
    return entries


def _dt_values(tree: dict[str, Any], dt: dict[str, str]) -> dict[str, Any]:
    nodes = tree.get("nodes", {})
    prop = dt["property"]
    if "chosen" in dt:
        path = tree.get("chosen", {}).get(dt["chosen"])
        return {path: (nodes.get(path) or {}).get("props", {}).get(prop)} if path else {}
    return {
        p: n.get("props", {}).get(prop)
        for p, n in nodes.items()
        if dt.get("compatible") in n.get("compats", []) and n.get("status") == "okay"
    }


class WireAnalyzer:
    name: ClassVar[str] = "wire"
    kind: ClassVar[Literal["build"]] = "build"
    per_image: ClassVar[bool] = False
    requires: ClassVar[frozenset[Artifact]] = frozenset()

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        entries = load_entries(ctx.wire_allowlist)
        pairs = [
            (o, n)
            for o, n in pair_images(ctx.old, ctx.new)
            if o.has(Artifact.CONFIG) and n.has(Artifact.CONFIG)
        ]
        findings: list[Finding] = []
        missing: list[str] = []
        checked_ids: list[str] = []
        syms: dict[tuple[str, str], dict[str, Any]] = {}

        unused: list[str] = []
        compiled: dict[tuple[str, str], set[str] | None] = {}

        def used(img: ImageContext, side: str, sym: str) -> bool:
            """Whether a .c file compiled into the image references CONFIG_<sym> (true
            when the image has no SBOM: unproven use counts as use)."""
            key = (side, img.name)
            if key not in compiled:
                compiled[key] = (
                    {
                        f.removeprefix("zephyr/")
                        for f in compiled_sources(
                            read_spdx_dir(img.artifact(Artifact.SPDX).parent)
                        ).get("zephyr", {})
                    }
                    if img.has(Artifact.SPDX)
                    else None
                )
            files = compiled[key]
            if files is None:
                return True
            b = ctx.old if side == "old" else ctx.new
            src = Source(
                b.workspace.resolved.mirror.path,
                b.workspace.resolved.ref.sha,
                ctx.runner,
                bare=True,
            )
            return any(
                path in files and path.endswith(".c")
                for path, _, _ in src.grep(rf"\bCONFIG_{sym}\b", ["."])
            )

        def symbols(img: ImageContext, side: str) -> dict[str, Any]:
            key = (side, img.name)
            if key not in syms:
                b = ctx.old if side == "old" else ctx.new
                syms[key] = kconfig_symbols(img, b.workspace, ctx.runner)
            return syms[key]

        for e in entries:
            if "kconfig" in e:
                sym = e["kconfig"]
                defined = {
                    side: any(sym in symbols(img, side) for img in imgs)
                    for side, imgs in (
                        ("old", [o for o, _ in pairs]),
                        ("new", [n for _, n in pairs]),
                    )
                }
                if not all(defined.values()):
                    missing.append(
                        f"{e['id']} (CONFIG_{sym} not defined at "
                        + " and ".join(
                            s
                            for s, ok in (
                                (ctx.old_spec, defined["old"]),
                                (ctx.new_spec, defined["new"]),
                            )
                            if not ok
                        )
                        + ")"
                    )
                    continue
            else:
                src = e.get("source", "")
                if not all((b.workspace.zephyr / src).is_file() for b in (ctx.old, ctx.new)):
                    missing.append(f"{e['id']} ({src} missing at a ref)")
                    continue
            checked_ids.append(e["id"])
            for o, n in pairs:
                if "kconfig" in e:
                    sym = e["kconfig"]
                    a, b = config_values(o).get(sym), config_values(n).get(sym)
                    if a == b:
                        continue
                    # On the wire only if compiled code of the image reads it at some ref
                    # (USB_DEVICE_PRODUCT has a value in MCUboot without the USB stack).
                    if not (used(o, "old", sym) or used(n, "new", sym)):
                        unused.append(f"{e['id']} in {n.name}")
                        continue
                    info = symbols(n, "new").get(sym) or symbols(o, "old").get(sym) or {}
                    ev = [
                        file_line(
                            f"<zephyr {ref}>/{loc.rsplit(':', 1)[0]}",
                            int(loc.rsplit(":", 1)[1]),
                            f"config {sym}",
                        )
                        for ref, s in (
                            (ctx.old_spec, symbols(o, "old")),
                            (ctx.new_spec, symbols(n, "new")),
                        )
                        for loc in (s.get(sym) or {}).get("locations", [])[:1]
                    ]
                    user = sym in ctx.app_usage.kconfig_set
                    findings.append(
                        self._finding(
                            ctx,
                            e,
                            n,
                            f"CONFIG_{sym}",
                            a,
                            b,
                            Remedy.CONFIG_PIN if info.get("prompt") else Remedy.UNKNOWN,
                            "set by you" if user else "a default",
                            ev,
                        )
                    )
                elif o.has(Artifact.EDT) and n.has(Artifact.EDT):
                    va = _dt_values(edt(o, ctx.old.workspace, ctx.runner), e["dt"])
                    vb = _dt_values(edt(n, ctx.new.workspace, ctx.runner), e["dt"])
                    if va != vb:
                        findings.append(
                            self._finding(
                                ctx,
                                e,
                                n,
                                f"{e['dt'].get('chosen') or e['dt'].get('compatible')}"
                                f"/{e['dt']['property']}",
                                va,
                                vb,
                                Remedy.APP_CHANGE,
                                "devicetree",
                                [],
                            )
                        )
        nc = [
            NotChecked(
                "wire formats outside the allowlist",
                f"only {len(checked_ids)} allowlisted items are compared "
                "(zmig/data/wire_format.yaml"
                + (", --wire-allowlist" if ctx.wire_allowlist else "")
                + "); protocol payloads, custom GATT services and other peer-visible "
                "behavior aren't",
                self.name,
            )
        ]
        if missing:
            findings.append(
                Finding(
                    category=Category.WIRE,
                    kind="allowlist-entry-not-found",
                    subject="allowlist",
                    title=f"{len(missing)} allowlist entries not found at the compared refs: "
                    + "; ".join(missing),
                    severity=Severity.INFO,
                    confidence=Confidence.HIGH,
                    action="Update the entry (the option may have been renamed) or drop it.",
                    analyzer=self.name,
                    scope=ctx.scope,
                    new=missing,
                )
            )
        hz = [f.id for f in findings if f.remedy is not None]
        status: CategoryStatus = "hazard" if hz else "clean"
        set_category(
            ctx.shared,
            "wire",
            status,
            "high" if hz else "medium",
            [
                f"allowlist v1: {len(checked_ids)} of {len(entries)} entries checked in "
                f"{len(pairs)} image pairs; clean means only that none of them changed"
            ],
            findings=hz,
            extra={
                "entries_checked": checked_ids,
                "entries_missing": missing,
                "changed_but_unused": unused,
            },
        )
        return AnalyzerResult(findings, not_checked=nc)

    def _finding(
        self,
        ctx: ScanContext,
        e: dict[str, Any],
        img: ImageContext,
        subject: str,
        old: Any,
        new: Any,
        remedy: Remedy,
        origin: str,
        ev: list[Any],
    ) -> Finding:
        return Finding(
            category=Category.WIRE,
            kind="wire-value-changed",
            subject=subject,
            title=f"[{e['id']}] {subject} in {img.name}: {old if old is not None else 'unset'} → "
            f"{new if new is not None else 'unset'} ({origin}): {e['reason']}",
            severity=Severity.RISKY,
            confidence=Confidence.HIGH,
            action="Deployed peers may depend on the old value; pin it unless all peers "
            "are updated too.",
            analyzer=self.name,
            scope=ctx.scope,
            evidence=tuple(ev),
            old=old,
            new=new,
            image=None if img.role == "app" else img.name,
            remedy=remedy,
            provenance=provenance(
                f"allowlist entry {e['id']}"
                + (" (user)" if e.get("user") else " (shipped)")
                + f"; resolved values of image {img.name} at both refs"
            ),
        )
