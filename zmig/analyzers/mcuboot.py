"""MCUboot and the signed image format across the migration (docs/DESIGN-P1.md §5).

A field device keeps its bootloader. So the main question (direction a) is whether the
*deployed* bootloader, assumed to be the source build's MCUboot, accepts the *target*
build's signed app image. Direction b (a newer bootloader reading state an older one
wrote) only applies when the bootloader is updated.

Everything comes from the two builds and the MCUboot source at each pin:
  options         the bootloader image's resolved .config, restricted to symbols defined
                  in MCUboot's own Kconfig (by location), grouped by Kconfig choice/menu
  definitions     zmig.srcdiff over the seeds in zmig/data/hazard_seeds.yaml
  imgtool         its literal constants and `sign` options at each pin (ast), and the
                  `imgtool sign` command in each app image's build.ninja
  measured        both signed images, parsed with their own pin's image.h layout
  agreement       app/bootloader option pairs from share/sysbuild/image_configurations
                  (zmig.sysbuild_rules.pairs): the target app's option must match the
                  deployed bootloader's
  slot geometry   the `if (...)` conditions of boot_slots_compatible() in the swap file
                  compiled into the target bootloader (from its SBOM), evaluated with the
                  build's slot sizes and flash erase-block size
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.analyzers.crossimage import partitions
from zmig.buildctx import Artifact, BuildContext, ImageContext
from zmig.findings import (
    JSON,
    Category,
    Confidence,
    Finding,
    NotChecked,
    Remedy,
    Severity,
    file_line,
)
from zmig.hazards import CategoryStatus, bootloader_role, mcuboot_module, provenance, set_category
from zmig.sbom import compiled_sources, read_spdx_dir
from zmig.scan import ScanContext
from zmig.signed_image import (
    ImageParseError,
    Layout,
    c_int,
    imgtool_cli,
    imgtool_constants,
    layout_at,
    parse,
    sign_command,
)
from zmig.snapshots import config_values, edt, kconfig_symbols, sysbuild_config, sysbuild_kconfig
from zmig.srcdiff import Seed, SeedDiff, Source, compare, load_seeds, seeds_of
from zmig.sysbuild_rules import IMAGE_FILES, pairs
from zmig.sysbuild_rules import parse as parse_rules
from zmig.workspace import Module

# Kconfig prompts that would evidence a bootloader able to update itself (heuristic,
# reported as such; none exists in upstream MCUboot's Zephyr Kconfig at the pins seen).
SELF_UPDATE = re.compile(
    r"(update|upgrade)\w*\s+(the\s+)?bootloader|bootloader\s+(self[- ]?)?(update|upgrade)", re.I
)

Check = dict[str, Any]


def _compiled(img: ImageContext) -> dict[str, dict[str, str]] | None:
    if not img.has(Artifact.SPDX):
        return None
    return compiled_sources(read_spdx_dir(img.artifact(Artifact.SPDX).parent))


class McubootAnalyzer:
    name: ClassVar[str] = "mcuboot"
    kind: ClassVar[Literal["build"]] = "build"
    per_image: ClassVar[bool] = False
    requires: ClassVar[frozenset[Artifact]] = frozenset()

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        ob, nb = bootloader_role(ctx.old), bootloader_role(ctx.new)
        nc: list[NotChecked] = []

        def unknown(reason: str) -> AnalyzerResult:
            set_category(
                ctx.shared,
                "mcuboot",
                "unknown",
                "low",
                [],
                reason=reason,
                extra={"assumptions": [], "directions": {}},
            )
            return AnalyzerResult(
                not_checked=[NotChecked("MCUboot / image format", reason, self.name)]
            )

        if ob.image is None or nb.image is None:
            if ob.evidence == "unknown" and nb.evidence == "unknown":
                set_category(
                    ctx.shared,
                    "mcuboot",
                    "not-applicable",
                    "high",
                    ["no bootloader image, and the app isn't built for MCUboot, at either ref"],
                )
                return AnalyzerResult()
            return unknown(
                "no bootloader image at "
                + " and ".join(
                    s for s, r in ((ctx.old_spec, ob), (ctx.new_spec, nb)) if r.image is None
                )
                + f" ({(ob if ob.image is None else nb).source}); a bootloader that isn't "
                "built here (or isn't MCUboot) is not checked"
            )
        mo, mn = (
            mcuboot_module(ctx.old.workspace, ctx.runner),
            mcuboot_module(ctx.new.workspace, ctx.runner),
        )
        if mo is None or mn is None:
            return unknown("the mcuboot module isn't cloned at one of the refs")
        boot_o, boot_n = ob.image, nb.image
        app_o, app_n = ctx.old.app, ctx.new.app
        for img in (boot_o, boot_n, app_o, app_n):
            if not img.has(Artifact.CONFIG):
                return unknown(f"image {img.name} at {img.ref} has no .config ({img.status})")

        seeds = load_seeds()
        paths = seeds["mcuboot"]["paths"]
        so, sn = (
            Source(Path(mo.path), mo.sha, ctx.runner),
            Source(Path(mn.path), mn.sha, ctx.runner),
        )
        pin_o, pin_n = f"{mo.sha[:12]} ({ctx.old_spec})", f"{mn.sha[:12]} ({ctx.new_spec})"
        role = nb
        findings: list[Finding] = []
        checked: list[str] = []
        confidence = "high"
        unknowns: list[str] = []

        def add(
            kind: str,
            subject: str,
            title: str,
            sev: Severity,
            conf: Confidence,
            action: str,
            *,
            old: Any = None,
            new: Any = None,
            remedy: Remedy | None = None,
            ev: list[JSON] | None = None,
            heuristics: tuple[str, ...] = (),
            source: str = "",
        ) -> None:
            findings.append(
                Finding(
                    category=Category.MCUBOOT,
                    kind=kind,
                    subject=subject,
                    title=title,
                    severity=sev,
                    confidence=conf,
                    action=action,
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=tuple(ev or ()),
                    old=old,
                    new=new,
                    remedy=remedy,
                    heuristics=heuristics,
                    provenance=provenance(
                        source
                        or f"MCUboot source at {pin_o} and {pin_n}; "
                        f"bootloader images {boot_o.name}/{boot_n.name}",
                        role,
                    ),
                )
            )

        # --- 5.1 options vs behavior --------------------------------------------------------
        cv_bo, cv_bn = config_values(boot_o), config_values(boot_n)
        ks_bo = kconfig_symbols(boot_o, ctx.old.workspace, ctx.runner)
        ks_bn = kconfig_symbols(boot_n, ctx.new.workspace, ctx.runner)
        self._options(ctx, add, mo, mn, cv_bo, cv_bn, ks_bo, ks_bn)
        checked.append(
            f"MCUboot-defined options of {boot_o.name} at both refs (by Kconfig location)"
        )

        # --- 5.2 format definitions --------------------------------------------------------
        diffs: dict[str, list[SeedDiff]] = {}
        for gname, group in seeds["mcuboot"]["groups"].items():
            diffs[gname] = [
                compare(s, so.locate(s, paths), sn.locate(s, paths), pin_o, pin_n)
                for s in seeds_of(group)
            ]
        not_found = [d.seed.label for ds in diffs.values() for d in ds if d.status == "not-found"]
        if not_found:
            unknowns.append(f"seeds not found at a pin: {', '.join(not_found)}")
        self._definitions(add, diffs, pin_o, pin_n)
        checked.append(
            f"format definitions ({sum(len(v) for v in diffs.values())} seeds) at MCUboot "
            f"{mo.sha[:12]} and {mn.sha[:12]}"
        )

        # imgtool at each pin
        cli_o = imgtool_cli(so, seeds["imgtool"]["endian_option"]["file"])
        cli_n = imgtool_cli(sn, seeds["imgtool"]["endian_option"]["file"])
        consts_o = imgtool_constants(so, seeds["imgtool"]["files"])
        consts_n = imgtool_constants(sn, seeds["imgtool"]["files"])
        const_changes = {
            k: {"old": consts_o.get(k), "new": consts_n.get(k)}
            for k in sorted(consts_o.keys() | consts_n.keys())
            if (consts_o.get(k) or {}).get("value") != (consts_n.get(k) or {}).get("value")
        }

        # --- 5.5 measured signed images ------------------------------------------------------
        sign_o = sign_command(app_o, cli_o.flags if cli_o else frozenset())
        sign_n = sign_command(app_n, cli_n.flags if cli_n else frozenset())
        lay_o: Layout = layout_at(so, paths, pin_o)
        lay_n: Layout = layout_at(sn, paths, pin_n)
        measured: dict[str, Any] = {}
        old_path: Path | None = ctx.deployed_image or (Path(sign_o["outfile"]) if sign_o else None)
        new_path = Path(sign_n["outfile"]) if sign_n else None
        endian_o = (sign_o or {}).get("options", {}).get("endian") or (
            cli_o.endian_default if cli_o else None
        )
        endian_n = (sign_n or {}).get("options", {}).get("endian") or (
            cli_n.endian_default if cli_n else None
        )
        for side, path, lay, endian in (
            ("old", old_path, lay_o, endian_o),
            ("new", new_path, lay_n, endian_n),
        ):
            if path is None or not path.is_file() or endian is None:
                measured[side] = {"error": "signed image or byte order not found"}
                continue
            try:
                measured[side] = parse(path, lay, endian)
            except ImageParseError as e:
                measured[side] = {"error": str(e), "field": e.field}
        if any("error" in m for m in measured.values()):
            unknowns.append(
                "signed images not parsed: "
                + "; ".join(f"{s}: {m['error']}" for s, m in measured.items() if "error" in m)
            )
        else:
            checked.append(
                "signed app images parsed with image.h at each pin"
                + (" (old side: --deployed-image)" if ctx.deployed_image else "")
            )
            self._measured_diff(add, measured, const_changes, pin_o, pin_n, ctx)

        # --- 5.3 direction (a): the deployed bootloader accepts the new image ------------------
        checks_a = self._direction_a(
            ctx, add, measured, lay_o, sign_o, sign_n, cv_bo, ks_bo, app_n, boot_o, pin_o, endian_n
        )
        st_a = (
            "hazard"
            if any(c["status"] == "mismatch" for c in checks_a)
            else "unknown"
            if any(c["status"] == "unknown" for c in checks_a)
            else "clean"
        )
        checked.append(
            f"direction a: {len(checks_a)} checks against {boot_o.name} @ {ctx.old_spec}"
        )
        if any(c["status"] == "unknown" for c in checks_a):
            unknowns += [
                f"direction a: {c['check']}: {c['detail']}"
                for c in checks_a
                if c["status"] == "unknown"
            ]

        # Slot geometry required by the target bootloader's swap mode.
        self._slot_geometry(ctx, add, sn, boot_n, cv_bn, pin_n)

        # --- updatability and direction (b) ---------------------------------------------------
        upd, upd_reason = self._updatable(ctx, ks_bo, cv_bo, mo)
        prior = ctx.shared.get("findings", [])
        needs_update = [f for f in prior + findings if f.remedy is Remedy.BOOTLOADER_UPDATE]
        if needs_update or upd == "yes":
            changed_state = [
                d
                for d in diffs["boot-state"]
                if d.status in ("changed", "not-interpreted", "not-found")
            ]
            st_b = "hazard" if changed_state else "clean"
            if changed_state:
                add(
                    "boot-state-definitions-changed",
                    "boot-state",
                    "Trailer/swap-state definitions changed between MCUboot pins: "
                    + ", ".join(d.seed.label for d in changed_state),
                    Severity.BREAKING,
                    Confidence.MEDIUM,
                    "Don't update the bootloader while a swap may be in progress; "
                    "complete or revert pending updates first.",
                    old=[d.to_json() for d in changed_state],
                    remedy=Remedy.REFLASH,
                )
        else:
            st_b = "not-applicable"

        # --- 5.7 implementation-only ------------------------------------------------------------
        impl = self._implementation_only(boot_o, boot_n, diffs)
        if impl:
            confidence = "medium"
            add(
                "implementation-changed",
                "bootloader",
                f"{len(impl)} MCUboot source files compiled into {boot_n.name} changed; "
                "format definitions they would touch are unchanged",
                Severity.INFO,
                Confidence.MEDIUM,
                "Behavior can change without the declarations changing; these files are "
                "named in the verdict basis.",
                new={"files": impl},
            )

        # --- category ---------------------------------------------------------------------
        hazards = [
            f.id for f in findings if f.remedy is not None and f.severity is not Severity.INFO
        ]
        status: CategoryStatus = (
            "hazard" if hazards else ("unknown" if unknowns or st_a == "unknown" else "clean")
        )
        if unknowns:
            confidence = "low" if status != "hazard" else "medium"
        assumptions = [
            "deployed bootloader == the source build's MCUboot "
            f"(image {boot_o.name} @ {ctx.old_spec}, "
            f"MCUboot {mo.sha[:12]})",
            "deployed app == "
            + (
                "the --deployed-image file"
                if ctx.deployed_image
                else "the source build's signed app image"
            ),
            f"bootloader updatability: {upd} ({upd_reason})",
        ]
        set_category(
            ctx.shared,
            "mcuboot",
            status,
            confidence,
            checked,
            findings=hazards,
            reason="; ".join(unknowns) or None,
            extra={
                "directions": {
                    "old_bootloader_accepts_new_image": st_a,
                    "new_bootloader_reads_old_state": st_b,
                },
                "direction_a_checks": checks_a,
                "implementation_only": [f"{boot_n.name}: {f}" for f in impl],
                "assumptions": assumptions,
                "updatable": upd,
            },
        )
        others = sorted({i.name for i in ctx.old.images + ctx.new.images if i.role == "other"})
        nc.append(
            NotChecked(
                "bootloaders other than MCUboot",
                "only an MCUboot image built here is checked (role from CONFIG_MCUBOOT=y); a "
                "first-stage bootloader, ROM bootloader or vendor updater isn't. Other images "
                "built: " + (", ".join(others) if others else "none"),
                self.name,
            )
        )
        if any(d.status == "extended" for ds in diffs.values() for d in ds) or any(
            f.kind == "slot-geometry" for f in findings
        ):
            nc.append(
                NotChecked(
                    "preprocessor conditions in MCUboot definitions",
                    "#if branches aren't evaluated: conditional #define variants are reported "
                    "as 'extended', and slot conditions whose macros have several conditional "
                    "definitions are left unevaluated",
                    self.name,
                )
            )
        if st_b == "not-applicable":
            nc.append(
                NotChecked(
                    "MCUboot direction b (new bootloader reads old state)",
                    "no hazard requires a bootloader update and updatability isn't evidenced; "
                    "trailer/swap-state definition diffs are reported for reference only",
                    self.name,
                )
            )
        return AnalyzerResult(findings, not_checked=nc)

    # --------------------------------------------------------------------------------------

    @staticmethod
    def _mcuboot_syms(ks: dict[str, dict[str, Any]], ws_zephyr: Path, mod: Module) -> set[str]:
        root = Path(mod.path).resolve()
        out = set()
        for name, info in ks.items():
            for loc in info.get("locations", []):
                p = (ws_zephyr / loc.rsplit(":", 1)[0]).resolve()
                if p.is_relative_to(root):
                    out.add(name)
                    break
        return out

    def _options(
        self,
        ctx: ScanContext,
        add: Any,
        mo: Module,
        mn: Module,
        cv_o: dict[str, str],
        cv_n: dict[str, str],
        ks_o: dict[str, dict[str, Any]],
        ks_n: dict[str, dict[str, Any]],
    ) -> None:
        mine = self._mcuboot_syms(ks_o, ctx.old.workspace.zephyr, mo) | self._mcuboot_syms(
            ks_n, ctx.new.workspace.zephyr, mn
        )
        choices: dict[str, dict[str, Any]] = {}
        others: dict[str, list[dict[str, Any]]] = {}
        for sym in sorted(mine):
            info = ks_n.get(sym) or ks_o.get(sym) or {}
            ch = info.get("choice")
            if ch:
                c = choices.setdefault(
                    ch, {"prompt": info.get("choice_prompt") or ch, "old": None, "new": None}
                )
                if cv_o.get(sym) == "y":
                    c["old"] = sym
                if cv_n.get(sym) == "y":
                    c["new"] = sym
            elif cv_o.get(sym) != cv_n.get(sym):
                others.setdefault(info.get("menu") or "(top level)", []).append(
                    {"symbol": f"CONFIG_{sym}", "old": cv_o.get(sym), "new": cv_n.get(sym)}
                )
        for ch, c in sorted(choices.items()):
            if c["old"] == c["new"]:
                continue
            ev = []
            for sym, ks, ref in ((c["old"], ks_o, ctx.old_spec), (c["new"], ks_n, ctx.new_spec)):
                for loc in ((ks.get(sym) or {}).get("locations") or [])[:1]:
                    path, _, line = loc.rpartition(":")
                    ev.append(file_line(f"<zephyr {ref}>/{path}", int(line or 1), f"config {sym}"))
            add(
                "bootloader-choice-changed",
                ch,
                f'MCUboot "{c["prompt"]}": {c["old"] or "(none)"} at {ctx.old_spec} → '
                f"{c['new'] or '(none)'} at {ctx.new_spec}",
                Severity.RISKY,
                Confidence.HIGH,
                "A device keeps its deployed bootloader's behavior unless the bootloader is "
                "reflashed; whether the new app still works with it is checked separately "
                "(direction a).",
                old={"active": c["old"]},
                new={"active": c["new"]},
                ev=ev,
                source=f"resolved .config and Kconfig (choice {ch}) of the bootloader image "
                "at both refs",
            )
        if others:
            n = sum(len(v) for v in others.values())
            add(
                "bootloader-options-changed",
                "options",
                f"{n} other MCUboot option values changed in the bootloader image: "
                + "; ".join(
                    f"{m}: {', '.join(x['symbol'] for x in v[:4])}{'…' if len(v) > 4 else ''}"
                    for m, v in sorted(others.items())
                ),
                Severity.INFO,
                Confidence.HIGH,
                "Relevant only to a reflashed or updated bootloader.",
                new=others,
                source="resolved .config of the bootloader image at both refs; menus from Kconfig",
            )

    @staticmethod
    def _definitions(add: Any, diffs: dict[str, list[SeedDiff]], pin_o: str, pin_n: str) -> None:
        for gname, ds in diffs.items():
            by: dict[str, list[str]] = {}
            for d in ds:
                by.setdefault(d.status, []).append(d.seed.label)
            changed = [d for d in ds if d.status in ("changed", "not-interpreted")]
            summary = "; ".join(f"{k}: {len(v)}" for k, v in sorted(by.items()))
            sev = Severity.RISKY if changed else Severity.INFO
            ev = []
            for d in ds:
                for e in d.names:
                    if e["status"] in ("unchanged",):
                        continue
                    for side in ("old", "new"):
                        for s in e.get(side, [])[:1]:
                            ev.append(
                                file_line(
                                    f"<mcuboot {s['ref']}>/{s['file']}",
                                    s["line"],
                                    f"{e['name']} {s['value']}"[:160],
                                )
                            )
            add(
                "format-definitions",
                gname,
                f"MCUboot {gname} definitions, {pin_o} → {pin_n}: {summary}"
                + (
                    " (changed: " + ", ".join(d.seed.label for d in changed) + ")"
                    if changed
                    else ""
                ),
                sev,
                Confidence.HIGH
                if not any(d.status == "not-interpreted" for d in ds)
                else Confidence.LOW,
                "Changed definitions are checked against the measured images (direction a) and "
                "the trailer state (direction b); added/extended ones are new variants only "
                "(conditions are not evaluated).",
                new=[d.to_json() for d in ds if d.status != "unchanged"],
                ev=ev[:12],
            )

    @staticmethod
    def _measured_diff(
        add: Any,
        m: dict[str, Any],
        const_changes: dict[str, Any],
        pin_o: str,
        pin_n: str,
        ctx: ScanContext,
    ) -> None:
        o, n = m["old"], m["new"]
        diffs: dict[str, Any] = {}
        for k in sorted(o["header"].keys() | n["header"].keys()):
            if k in ("ih_img_size",):
                continue
            if o["header"].get(k) != n["header"].get(k):
                diffs[f"header.{k}"] = [o["header"].get(k), n["header"].get(k)]
        if o["flags"] != n["flags"]:
            diffs["flags"] = [o["flags"], n["flags"]]
        to = [(t["area"], t["name"], t["len"]) for t in o["tlvs"]]
        tn = [(t["area"], t["name"], t["len"]) for t in n["tlvs"]]
        if to != tn:
            diffs["tlvs"] = [to, tn]
        tool = (
            "imgtool constants changed: " + ", ".join(const_changes)
            if const_changes
            else "imgtool constants unchanged"
        )
        add(
            "signed-image-diff",
            "app image",
            (
                "Signed app images differ in format fields: " + ", ".join(diffs)
                if diffs
                else "Signed app images have the same header layout, flags and TLV set"
            )
            + f" (size {o['size']} → {n['size']} bytes); {tool}"
            + (
                "; tool changed, output unchanged for this build"
                if const_changes and not diffs
                else ""
            ),
            Severity.INFO,
            Confidence.HIGH,
            "Format differences are judged against the deployed bootloader in direction a.",
            old={
                k: o[k] for k in ("path", "size", "magic", "header", "flags", "tlv_areas", "tlvs")
            },
            new={k: n[k] for k in ("path", "size", "magic", "header", "flags", "tlv_areas", "tlvs")}
            | {"format_differences": diffs, "imgtool_constant_changes": const_changes},
            source=f"signed .bin files parsed with image.h at {pin_o} and {pin_n}",
        )

    def _direction_a(
        self,
        ctx: ScanContext,
        add: Any,
        m: dict[str, Any],
        lay_o: Layout,
        sign_o: dict[str, Any] | None,
        sign_n: dict[str, Any] | None,
        cv_bo: dict[str, str],
        ks_bo: dict[str, dict[str, Any]],
        app_n: ImageContext,
        boot_o: ImageContext,
        pin_o: str,
        endian_n: str | None,
    ) -> list[Check]:
        checks: list[Check] = []

        def check(name: str, status: str, detail: str, **kw: Any) -> None:
            checks.append({"check": name, "status": status, "detail": detail, **kw})

        o, n = m.get("old", {}), m.get("new", {})
        parsed = "error" not in o and "error" not in n
        if not parsed:
            for name in (
                "header magic",
                "header layout",
                "TLV set",
                "signing key",
                "encryption",
                "image size",
            ):
                check(name, "unknown", "signed images not parsed")
        else:
            # Read the new image with the deployed bootloader's definitions.
            try:
                as_old = parse(Path(n["path"]), lay_o, endian_n or n["endian"])
                same = as_old["header"] == n["header"] and [
                    (t["type"], t["len"]) for t in as_old["tlvs"]
                ] == [(t["type"], t["len"]) for t in n["tlvs"]]
                check(
                    "header layout",
                    "ok" if same else "mismatch",
                    f"new image read with image.h at {pin_o}: "
                    + ("same header fields and TLVs" if same else "fields differ"),
                    site=lay_o.sites.get("struct image_header"),
                )
                if not same:
                    add(
                        "old-bootloader-rejects-header",
                        "image header",
                        f"The deployed bootloader (MCUboot {pin_o}) reads the new image's header "
                        "differently",
                        Severity.BREAKING,
                        Confidence.HIGH,
                        "Sign the new app with the deployed MCUboot's imgtool, or reflash.",
                        old=as_old["header"],
                        new=n["header"],
                        remedy=Remedy.UNKNOWN,
                    )
            except ImageParseError as e:
                check(
                    "header layout",
                    "mismatch",
                    f"the deployed bootloader's definitions can't read it: {e}",
                )
                add(
                    "old-bootloader-rejects-header",
                    "image header",
                    f"The new signed image can't be read with MCUboot {pin_o}'s image.h: {e}",
                    Severity.BREAKING,
                    Confidence.HIGH,
                    "Sign the new app with the deployed MCUboot's imgtool, or reflash.",
                    remedy=Remedy.UNKNOWN,
                )
            magic_ok = n["magic"]["value"] in {
                v for k, v in lay_o.defines.items() if k.startswith("IMAGE_MAGIC")
            }
            check(
                "header magic",
                "ok" if magic_ok else "mismatch",
                f"0x{n['magic']['value']:08x} vs IMAGE_MAGIC* at {pin_o}",
                site=lay_o.sites.get("IMAGE_MAGIC"),
            )
            # TLVs: the deployed bootloader accepted the source image's set (assumption).
            ts_o = {(t["area"], t["name"], t["len"]) for t in o["tlvs"]}
            ts_n = {(t["area"], t["name"], t["len"]) for t in n["tlvs"]}
            gone = sorted(ts_o - ts_n)
            extra = sorted(ts_n - ts_o)
            unknown_types = sorted({t["type"] for t in n["tlvs"]} - set(lay_o.tlv_names))
            if gone:
                check(
                    "TLV set", "mismatch", f"TLVs of the deployed image missing or resized: {gone}"
                )
                add(
                    "tlv-set-changed",
                    "TLVs",
                    f"The new image lacks or resizes TLVs the deployed image has: {gone}",
                    Severity.BREAKING,
                    Confidence.HIGH,
                    "Match the source's signing configuration (hash, signature type).",
                    old=sorted(ts_o),
                    new=sorted(ts_n),
                    remedy=Remedy.APP_CHANGE,
                )
            elif extra or unknown_types:
                check(
                    "TLV set",
                    "unknown",
                    f"new TLVs {extra}"
                    + (f"; types unknown to {pin_o}: {unknown_types}" if unknown_types else "")
                    + "; how the deployed bootloader treats them isn't evaluated",
                )
            else:
                check("TLV set", "ok", "same TLV types and lengths as the deployed image")
            if not _key_hashes(o) or not _key_hashes(n):
                check("signing key", "unknown", "no key hash/public key TLV in an image")
            elif _key_hashes(o) == _key_hashes(n):
                check("signing key", "ok", "key hash TLV identical")
            else:
                check("signing key", "mismatch", "key hash TLV differs")
                add(
                    "signing-key-changed",
                    "key",
                    "The new image is signed with a different key than the deployed image",
                    Severity.BREAKING,
                    Confidence.HIGH,
                    "Sign with the deployed key (SB_CONFIG_BOOT_SIGNATURE_KEY_FILE).",
                    remedy=Remedy.APP_CHANGE,
                )
            enc_o = (
                sorted(f for f in o["flags"] if "ENCRYPTED" in f),
                sorted(t[1] for t in ts_o if "_ENC_" in t[1]),
            )
            enc_n = (
                sorted(f for f in n["flags"] if "ENCRYPTED" in f),
                sorted(t[1] for t in ts_n if "_ENC_" in t[1]),
            )
            check("encryption", "ok" if enc_o == enc_n else "mismatch", f"{enc_o} → {enc_n}")
            if enc_o != enc_n:
                add(
                    "encryption-changed",
                    "encryption",
                    f"Image encryption differs from the deployed image: {enc_o} → {enc_n}",
                    Severity.BREAKING,
                    Confidence.HIGH,
                    "Match the deployed encryption setup.",
                    remedy=Remedy.APP_CHANGE,
                )
            slot_o = (sign_o or {}).get("options", {}).get("slot-size")
            if slot_o is None:
                check("image size", "unknown", "source --slot-size not found")
            else:
                fits = n["size"] <= int(slot_o, 0)
                check(
                    "image size",
                    "ok" if fits else "mismatch",
                    f"{n['size']} bytes vs the deployed slot size {int(slot_o, 0)} "
                    "(imgtool --slot-size "
                    "of the source build; trailer overhead not subtracted)",
                )
                if not fits:
                    add(
                        "image-too-large",
                        "size",
                        f"The new signed image ({n['size']} bytes) exceeds the deployed "
                        f"slot ({slot_o})",
                        Severity.BREAKING,
                        Confidence.HIGH,
                        "Reduce the image size.",
                        remedy=Remedy.APP_CHANGE,
                    )
        # Alignment / padding parameters.
        if sign_o and sign_n:
            for opt in ("align", "header-size", "max-sectors", "overwrite-only", "pad", "encrypt"):
                a, b = sign_o["options"].get(opt), sign_n["options"].get(opt)
                if a != b:
                    check(
                        f"imgtool --{opt}",
                        "unknown",
                        f"{a} → {b}; its effect on the deployed bootloader isn't evaluated",
                    )
        else:
            check(
                "imgtool sign arguments",
                "unknown",
                "no `imgtool sign` producing a .bin in an app build.ninja",
            )
        # App/bootloader agreement (e.g. the upgrade mode).
        checks += self._agreement(ctx, add, cv_bo, ks_bo, app_n, boot_o)
        return checks

    def _agreement(
        self,
        ctx: ScanContext,
        add: Any,
        cv_bo: dict[str, str],
        ks_bo: dict[str, dict[str, Any]],
        app_n: ImageContext,
        boot_o: ImageContext,
    ) -> list[Check]:
        rules = {r: _rules(b) for r, b in (("old", ctx.old), ("new", ctx.new))}
        tp = pairs(*[[d for d in rules["new"] if d.role == role] for role in ("app", "bootloader")])
        if not tp:
            return [
                {
                    "check": "app/bootloader agreement",
                    "status": "unknown",
                    "detail": "no app/bootloader option pairs found in share/sysbuild "
                    "at the target",
                }
            ]
        cv_an = config_values(app_n)
        cv_ao = config_values(ctx.old.app)
        out: list[Check] = []
        sb_new = (
            sysbuild_kconfig(ctx.new.dir, ctx.new.workspace, ctx.runner)
            if sysbuild_config(ctx.new.dir) is not None
            else {}
        )
        for p in tp:
            a_opt, b_opt = (
                p.app.option.removeprefix("CONFIG_"),
                p.bootloader.option.removeprefix("CONFIG_"),
            )
            if cv_an.get(a_opt) != "y":
                continue
            if b_opt not in ks_bo:
                out.append(
                    {
                        "check": f"agreement {p.trigger}",
                        "status": "unknown",
                        "detail": f"CONFIG_{b_opt} isn't defined in the deployed bootloader",
                    }
                )
                continue
            if cv_bo.get(b_opt) == "y":
                out.append(
                    {
                        "check": f"agreement {p.trigger}",
                        "status": "ok",
                        "detail": f"app CONFIG_{a_opt}=y and deployed bootloader CONFIG_{b_opt}=y",
                    }
                )
                continue
            # Mismatch: what does the deployed bootloader do instead?
            choice = (ks_bo.get(b_opt) or {}).get("choice")
            deployed = next(
                (
                    s
                    for s, i in ks_bo.items()
                    if choice and i.get("choice") == choice and cv_bo.get(s) == "y"
                ),
                None,
            )
            pin = (
                next((q for q in tp if q.bootloader.option == f"CONFIG_{deployed}"), None)
                if deployed
                else None
            )
            pin_ok = (
                pin is not None
                and pin.trigger.removeprefix("SB_CONFIG_") in sb_new
                and not sb_new[pin.trigger.removeprefix("SB_CONFIG_")].get("deprecated")
                # Selecting it restores the source's resolved app option (§8.1).
                and cv_ao.get(pin.app.option.removeprefix("CONFIG_")) == "y"
            )
            # Consequence: compiled target-app code conditioned on the app option.
            hits = self._compiled_uses(ctx, app_n, f"CONFIG_{a_opt}")
            ev = [
                file_line(
                    f"<zephyr {ctx.new_spec}>/{p.app.file}",
                    p.app.line,
                    f"{p.trigger} → {p.app.option}",
                ),
                file_line(
                    f"<zephyr {ctx.new_spec}>/{p.bootloader.file}",
                    p.bootloader.line,
                    f"{p.trigger} → {p.bootloader.option}",
                ),
            ]
            ev += [file_line(f"<zephyr {ctx.new_spec}>/{f}", ln, t) for f, ln, t in hits[:5]]
            if pin is not None:
                sym = pin.trigger.removeprefix("SB_CONFIG_")
                for loc in (sb_new.get(sym) or {}).get("locations", [])[:1]:
                    path, _, line = loc.rpartition(":")
                    ev.append(
                        file_line(
                            f"<zephyr {ctx.new_spec}>/{path}", int(line or 1), f"config {sym}"
                        )
                    )
            out.append(
                {
                    "check": f"agreement {p.trigger}",
                    "status": "mismatch",
                    "detail": f"target app CONFIG_{a_opt}=y, deployed bootloader has "
                    f"CONFIG_{deployed or b_opt}",
                }
            )
            add(
                "app-bootloader-mismatch",
                p.trigger,
                f"The new app is built for {p.trigger} (CONFIG_{a_opt}) but the deployed "
                f"bootloader {boot_o.name} @ {ctx.old_spec} runs CONFIG_{deployed or '(other)'}"
                + (
                    f"; {len(hits)} compiled app source lines depend on it"
                    if hits
                    else "; no compiled app code references the app option"
                ),
                Severity.BREAKING if hits else Severity.RISKY,
                Confidence.HIGH if hits else Confidence.MEDIUM,
                (
                    f"Set {pin.trigger}=y in sysbuild.conf to build the app for the deployed "
                    "bootloader's mode"
                    if pin_ok and pin
                    else "Build the app for the deployed bootloader's mode, or reflash the "
                    "bootloader."
                ),
                old={"deployed_bootloader": f"CONFIG_{deployed}" if deployed else None},
                new={
                    "app": f"CONFIG_{a_opt}",
                    "requires": f"CONFIG_{b_opt}",
                    "compiled_uses": [f"{f}:{ln}" for f, ln, _ in hits],
                },
                remedy=Remedy.CONFIG_PIN if pin_ok else Remedy.UNKNOWN,
                ev=ev,
                source="share/sysbuild image_configurations pairs at the target; deployed "
                "bootloader .config; compiled app files from the target SBOM",
            )
        return out

    @staticmethod
    def _compiled_uses(
        ctx: ScanContext, app: ImageContext, option: str
    ) -> list[tuple[str, int, str]]:
        sb = _compiled(app)
        if sb is None:
            return []
        files = {f.removeprefix("zephyr/") for f in sb.get("zephyr", {})}
        src = Source(
            ctx.new.workspace.resolved.mirror.path,
            ctx.new.workspace.resolved.ref.sha,
            ctx.runner,
            bare=True,
        )
        return [
            (f, ln, t.strip()[:120])
            for f, ln, t in src.grep(rf"\b{option}\b", ["subsys", "lib", "drivers"])
            if f in files
        ]

    def _slot_geometry(
        self,
        ctx: ScanContext,
        add: Any,
        sn: Source,
        boot_n: ImageContext,
        cv_bn: dict[str, str],
        pin_n: str,
    ) -> None:
        sb = _compiled(boot_n)
        if sb is None or not boot_n.has(Artifact.EDT):
            return
        swap = [f for f in sb.get("mcuboot", {}) if re.search(r"boot/bootutil/src/swap_\w+\.c$", f)]
        tree = edt(boot_n, ctx.new.workspace, ctx.runner)
        parts = partitions(tree)
        if "slot0_partition" not in parts or "slot1_partition" not in parts:
            return
        sector = _erase_block(tree, parts["slot0_partition"][0])
        if sector is None:
            return
        n_pri = parts["slot0_partition"][1][1] // sector
        n_sec = parts["slot1_partition"][1][1] // sector
        for f in swap:
            lines = sn.show(f) or []
            start = next(
                (
                    i
                    for i, ln in enumerate(lines)
                    if re.match(r"^\w.*\bboot_slots_compatible\s*\(", ln)
                ),
                None,
            )
            if start is None:
                continue
            conds = []
            depth = 0
            for i in range(start, len(lines)):
                depth += lines[i].count("{") - lines[i].count("}")
                m = re.match(r"^\s*(?:\}\s*else\s+)?if\s*\((.*)$", lines[i])
                if m:
                    text, j = m[1], i
                    while text.count("(") >= text.count(")") and j + 1 < len(lines):
                        j += 1
                        text += " " + lines[j].strip()
                    cond = text.rsplit(")", 1)[0]
                    msg = next(
                        (
                            q[1]
                            for k in range(j + 1, min(j + 4, len(lines)))
                            if "BOOT_LOG" in lines[k] and (q := re.search(r'"([^"]+)"', lines[k]))
                        ),
                        None,
                    )
                    if msg and "sector" in (cond + msg).lower():
                        conds.append((i + 1, cond, re.sub(r"\s*%\w+", "", msg).strip(" ,:")))
                if depth == 0 and i > start and "}" in lines[i]:
                    break
            # Uniform sectors of erase-block-size (an assumption, stated on the finding).
            env = {
                "num_sectors_pri": n_pri,
                "num_sectors_sec": n_sec,
                "sector_sz_pri": sector,
                "sector_sz_sec": sector,
            }
            resolved: dict[str, str] = {}
            for cond in {c for _, c, _ in conds}:
                for name in set(re.findall(r"\b[A-Z][A-Z0-9_]+\b", cond)) - env.keys():
                    value, chain = _resolve_macro(sn, name, cv_bn)
                    resolved[name] = chain
                    if value is not None:
                        env[name] = value
            results: list[dict[str, Any]] = []
            for line, cond, msg in conds:
                value, why = _c_condition(cond, env)
                results.append(
                    {"line": line, "condition": cond.strip(), "log": msg, "result": value}
                    | ({"why": why} if why else {})
                )
            if not results:
                continue
            fails = [r for r in results if r["result"] and "Cannot" in r["log"]]
            notes = [r for r in results if r["result"] and "Cannot" not in r["log"]]
            unevaluated = [r for r in results if r["result"] is None]
            mode = Path(f).stem
            add(
                "slot-geometry",
                mode,
                f"{mode} (MCUboot {pin_n}, {f}) slot requirement: primary {n_pri}, secondary "
                f"{n_sec} sectors of 0x{sector:x}"
                + (
                    "; FAILS: " + "; ".join(r["log"] for r in fails)
                    if fails
                    else "; compatible"
                    + ("; " + "; ".join(r["log"] for r in notes) if notes else "")
                )
                + (f" ({len(unevaluated)} condition(s) not evaluated)" if unevaluated else ""),
                Severity.BREAKING if fails else (Severity.RISKY if notes else Severity.INFO),
                Confidence.HIGH if not unevaluated else Confidence.MEDIUM,
                "Applies to a device running this MCUboot build (reflashed or updated "
                "bootloader); the migration guide describes the recommended slot sizes.",
                new={
                    "sectors": {"primary": n_pri, "secondary": n_sec, "erase_block": sector},
                    "conditions": results,
                    "macros": resolved,
                },
                heuristics=("mcuboot.uniform-sectors",),
                remedy=Remedy.REFLASH if fails else None,
                ev=[
                    file_line(
                        f"<mcuboot {pin_n}>/{f}", r["line"], f"if ({r['condition']}) → {r['log']}"
                    )
                    for r in results
                ],
                source=f"boot_slots_compatible() in {f} (compiled into {boot_n.name} per its "
                "SBOM); slot sizes and erase-block-size from its final devicetree",
            )

    @staticmethod
    def _updatable(
        ctx: ScanContext, ks: dict[str, dict[str, Any]], cv: dict[str, str], mod: Module
    ) -> tuple[str, str]:
        boots = [i.name for i in ctx.old.images if i.role == "bootloader"]
        if len(boots) > 1:
            return "yes", f"more than one bootloader-stage image in domains.yaml: {boots}"
        hits = [
            s
            for s, i in ks.items()
            if i.get("prompt") and SELF_UPDATE.search(i["prompt"]) and cv.get(s) == "y"
        ]
        if hits:
            return "yes", f"enabled MCUboot options {hits} (prompt match; heuristic)"
        return "unknown", (
            "one bootloader image in domains.yaml, and no enabled option of the deployed "
            "bootloader has a prompt about updating the bootloader"
        )

    @staticmethod
    def _implementation_only(
        boot_o: ImageContext, boot_n: ImageContext, diffs: dict[str, list[SeedDiff]]
    ) -> list[str]:
        if any(d.status in ("changed", "not-interpreted") for ds in diffs.values() for d in ds):
            return []
        so, sn = _compiled(boot_o), _compiled(boot_n)
        if so is None or sn is None:
            return []
        out = []
        for pkg, prefix in (("mcuboot", ""), ("app", "boot/zephyr/")):
            o, n = so.get(pkg, {}), sn.get(pkg, {})
            out += [prefix + f for f in sorted(o.keys() & n.keys()) if o[f] != n[f]]
        return out


def _rules(b: BuildContext) -> list[Any]:
    z = b.workspace.zephyr
    out: list[Any] = []
    for role, f in IMAGE_FILES.items():
        if (z / f).is_file():
            out += parse_rules((z / f).read_text(), role, f)
    return out


def _erase_block(tree: dict[str, Any], path: str) -> int | None:
    nodes = tree.get("nodes", {})
    p = path
    while p and p != "/":
        props = (nodes.get(p) or {}).get("props", {})
        if isinstance(props.get("erase-block-size"), int):
            return int(props["erase-block-size"])
        p = p.rsplit("/", 1)[0] or "/"
    return None


def _key_hashes(img: dict[str, Any]) -> list[str]:
    return sorted(
        t["sha256"] for t in img["tlvs"] if t["name"] in ("IMAGE_TLV_KEYHASH", "IMAGE_TLV_PUBKEY")
    )


_C_OPS = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.USub,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Name,
    ast.Load,
    ast.Constant,
)


def _c_condition(cond: str, env: dict[str, int]) -> tuple[bool | None, str | None]:
    """Evaluate a C `if` condition over integer variables, or say why it can't be.

    Only comparisons, boolean operators, + - * and the given names are accepted; anything
    else (calls, casts, macros not in `env`) is left unevaluated."""
    expr = cond.replace("&&", " and ").replace("||", " or ")
    expr = re.sub(r"!(?!=)", " not ", expr)
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError:
        return None, "not a simple expression"
    for node in ast.walk(tree):
        if not isinstance(node, _C_OPS):
            return None, f"unsupported construct {type(node).__name__}"
        if isinstance(node, ast.Name) and node.id not in env:
            return None, f"{node.id} unknown"
        if isinstance(node, ast.Constant) and not isinstance(node.value, int):
            return None, "non-integer constant"
    return bool(eval(compile(tree, "<condition>", "eval"), {"__builtins__": {}}, dict(env))), None


def _resolve_macro(src: Source, name: str, cv: dict[str, str]) -> tuple[int | None, str]:
    """An integer for a macro, following `#define A B` in MCUboot's tree at the pin down to
    a literal or a CONFIG_ value of the bootloader image. Ambiguous (several different
    definitions) or non-integer chains give None."""
    chain = [name]
    cur = name
    for _ in range(5):
        if cur.startswith("CONFIG_"):
            v = c_int(cv.get(cur.removeprefix("CONFIG_"), ""))
            return v, " → ".join(chain) + f" = {v}"
        # MCUboot's common code and its Zephyr port (other ports define these too).
        sites = src.locate(Seed("define", cur, ""), ["boot/bootutil", "boot/zephyr"])
        defs = {d.value for d in sites}
        if len(defs) != 1:
            where = ", ".join(sorted(f"{d.file}:{d.line}" for d in sites))
            return None, " → ".join(chain) + (
                f" ({len(defs)} conditional definitions: {where}; conditions not evaluated)"
                if defs
                else " (not defined)"
            )
        value = defs.pop()
        if (v := c_int(value)) is not None:
            return v, " → ".join(chain) + f" = {v}"
        if not re.fullmatch(r"\(?\s*[A-Za-z_]\w*\s*\)?", value):
            return None, " → ".join(chain) + f" = {value} (not interpreted)"
        cur = value.strip("() ")
        chain.append(cur)
    return None, " → ".join(chain) + " (too deep)"
