"""Flash partition layout across the migration (field-upgrade hazard, docs/DESIGN-P1.md §4).

The bootloader owns the layout: a deployed bootloader keeps looking for its slots where
the source build put them. So the source and target layouts are compared from the
bootloader image's final devicetree (the app image's when there is no bootloader), with
both partition representations (fixed-partitions children, *-partition nodes).

Roles come from source at each ref, not from names:
  bootloader  the label is referenced by MCUboot's Zephyr port at its pin (boot/zephyr:
              CMake dt_nodelabel, Kconfig dt_nodelabel_*, overlays, C)
  storage     the label, or a chosen node resolving to it, is referenced by the storage
              subsystems (subsys/settings, fs/nvs, fs/zms, secure_storage)
  code        the image's chosen zephyr,code-partition
  unknown     none of the above

A source reference counts only if it is in code the build compiles: a .c/.S hit must be
in that ref's SBOM (MCUboot's for the bootloader role, any image's for storage); headers
count; Kconfig, CMake and board overlays don't (they are conditional on things the grep
can't see). Without an SBOM every hit counts, and the provenance says so. (Regression:
MCUboot's optional serial-recovery storage-erase command gave storage_partition the
bootloader role.)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.analyzers.crossimage import partitions
from zmig.buildctx import Artifact, ImageContext
from zmig.findings import (
    JSON,
    Category,
    Confidence,
    Finding,
    NotChecked,
    Remedy,
    Severity,
    artifact_diff,
    file_line,
)
from zmig.hazards import (
    BootloaderRole,
    bootloader_role,
    mcuboot_module,
    provenance,
    set_category,
    signing_args,
)
from zmig.sbom import compiled_sources, read_spdx_dir
from zmig.scan import ScanContext
from zmig.snapshots import edt


def _compiled(img: ImageContext) -> dict[str, dict[str, str]] | None:
    if not img.has(Artifact.SPDX):
        return None
    return compiled_sources(read_spdx_dir(img.artifact(Artifact.SPDX).parent))


def _counts(path: str, compiled: set[str] | None) -> bool:
    """Whether a grep hit is in code the build compiles (see the module docstring)."""
    if compiled is None or path.endswith(".h"):
        return True
    if path.endswith((".c", ".S")):
        return path in compiled
    return False


STORAGE_TREES = ("subsys/settings", "subsys/fs/nvs", "subsys/fs/zms", "subsys/secure_storage")


def _fmt(reg: Any) -> str:
    if isinstance(reg, list) and len(reg) == 2:
        return f"0x{reg[0]:x}+0x{reg[1]:x}"
    return str(reg)


class LayoutAnalyzer:
    name: ClassVar[str] = "layout"
    kind: ClassVar[Literal["build"]] = "build"
    per_image: ClassVar[bool] = False
    requires: ClassVar[frozenset[Artifact]] = frozenset()

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        ob, nb = bootloader_role(ctx.old), bootloader_role(ctx.new)
        o_img = ob.image or ctx.old.app
        n_img = nb.image or ctx.new.app
        not_checked: list[NotChecked] = []
        if not (o_img.has(Artifact.EDT) and n_img.has(Artifact.EDT)):
            reason = "the final devicetree of the layout-owning image is missing at a ref"
            set_category(ctx.shared, "layout", "unknown", "low", [], reason=reason)
            return AnalyzerResult(not_checked=[NotChecked("flash layout", reason, self.name)])
        o_tree = edt(o_img, ctx.old.workspace, ctx.runner)
        n_tree = edt(n_img, ctx.new.workspace, ctx.runner)
        po, pn = partitions(o_tree), partitions(n_tree)
        checked = [
            f"partitions of image {o_img.name} @ {ctx.old_spec} ({len(po)}) and "
            f"{n_img.name} @ {ctx.new_spec} ({len(pn)}) from the final devicetree"
        ]
        if not po or not pn:
            reason = (
                "no flash partitions found in the final devicetree (neither "
                "fixed-partitions children nor *-partition nodes)"
            )
            set_category(ctx.shared, "layout", "unknown", "low", checked, reason=reason)
            return AnalyzerResult(not_checked=[NotChecked("flash layout", reason, self.name)])

        roles = self._roles(ctx, set(po) | set(pn), (o_tree, n_tree), not_checked)
        checked.append(
            "partition roles from MCUboot's Zephyr port and the storage subsystems at both refs"
        )
        findings: list[Finding] = []
        role = nb if nb.image else ob

        def add(
            kind: str,
            label: str,
            title: str,
            sev: Severity,
            action: str,
            old: Any,
            new: Any,
            extra_ev: list[JSON] | None = None,
        ) -> None:
            r = roles.get(label, {"roles": ["unknown"], "refs": []})
            ev: list[JSON] = [
                *self._dts_ev(o_img, label, ctx.old_spec),
                *self._dts_ev(n_img, label, ctx.new_spec),
            ]
            ev += [file_line(x["path"], x["line"], x["text"]) for x in r["refs"][:4]]
            ev += extra_ev or []
            findings.append(
                Finding(
                    category=Category.LAYOUT,
                    kind=kind,
                    subject=label,
                    title=title + f" [role: {', '.join(r['roles'])}]",
                    severity=sev,
                    confidence=Confidence.HIGH,
                    action=action,
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=tuple(ev),
                    old=old,
                    new=new,
                    remedy=self._remedy(r["roles"], role) if sev is not Severity.INFO else None,
                    provenance=provenance(
                        f"final devicetree of image {o_img.name} @ "
                        f"{ctx.old_spec} and {n_img.name} @ {ctx.new_spec}; "
                        f"roles: {'; '.join(r['sources']) or 'none found'}",
                        role,
                    ),
                )
            )

        for label in sorted(po.keys() | pn.keys()):
            ro, rn = po.get(label, (None, None))[1], pn.get(label, (None, None))[1]
            if label not in pn:
                add(
                    "partition-removed",
                    label,
                    f"Partition {label} ({_fmt(ro)}) is removed at {ctx.new_spec}",
                    Severity.BREAKING,
                    "A deployed bootloader or app that uses it can't find it in the new layout.",
                    {"reg": ro},
                    None,
                )
                continue
            if label not in po:
                add(
                    "partition-added",
                    label,
                    f"Partition {label} ({_fmt(rn)}) is added at {ctx.new_spec}",
                    Severity.INFO,
                    "Nothing on a deployed device uses it yet.",
                    None,
                    {"reg": rn},
                )
                continue
            if ro == rn:
                if self._device(po[label][0]) != self._device(pn[label][0]):
                    add(
                        "partition-moved-device",
                        label,
                        f"Partition {label} moved to another flash device",
                        Severity.BREAKING,
                        "Data and slots on the old device are no longer where the new build looks.",
                        po[label][0],
                        pn[label][0],
                    )
                continue
            if not (isinstance(ro, list) and isinstance(rn, list) and len(ro) == len(rn) == 2):
                not_checked.append(
                    NotChecked(
                        f"partition {label}",
                        "its reg isn't a single (offset, size) pair at both refs",
                        self.name,
                    )
                )
                continue
            moved = ro[0] != rn[0]
            resized = ro[1] != rn[1]
            what = " and ".join(x for x, y in (("moved", moved), ("resized", resized)) if y)
            # A storage partition gets one finding, stated as what it means for the data.
            if "storage" in roles.get(label, {}).get("roles", []):
                add(
                    "storage-stranded",
                    label,
                    f"Storage partition {label} {what}: data written by the old firmware at "
                    f"{_fmt(ro)} isn't where the new firmware reads ({_fmt(rn)})",
                    Severity.BREAKING,
                    "Keep the storage partition where it was for this "
                    "device population, or migrate its contents before the layout changes.",
                    {"reg": ro},
                    {"reg": rn},
                    [artifact_diff("partition reg", ctx.old_spec, ctx.new_spec, ro, rn)],
                )
                continue
            add(
                f"partition-{'moved' if moved else 'resized'}",
                label,
                f"Partition {label} {what}: {_fmt(ro)} at {ctx.old_spec} → {_fmt(rn)} at "
                f"{ctx.new_spec}",
                Severity.BREAKING,
                "A deployed device keeps the old layout: its bootloader and data stay where "
                "the source build put them.",
                {"reg": ro},
                {"reg": rn},
                [artifact_diff("partition reg", ctx.old_spec, ctx.new_spec, ro, rn)],
            )

        common = [lb for lb in po if lb in pn]
        order_o = sorted(common, key=lambda lb: po[lb][1][0] if isinstance(po[lb][1], list) else 0)
        order_n = sorted(common, key=lambda lb: pn[lb][1][0] if isinstance(pn[lb][1], list) else 0)
        if order_o != order_n:
            findings.append(
                Finding(
                    category=Category.LAYOUT,
                    kind="partitions-reordered",
                    subject="order",
                    title=f"Partition order changed: {', '.join(order_o)} → {', '.join(order_n)}",
                    severity=Severity.BREAKING,
                    confidence=Confidence.HIGH,
                    action="The bootloader owns the layout; a reordered layout needs the "
                    "bootloader to change with it.",
                    analyzer=self.name,
                    scope=ctx.scope,
                    old=order_o,
                    new=order_n,
                    remedy=self._remedy(["bootloader"], role),
                    provenance=provenance("final devicetree at both refs", role),
                )
            )

        sa, sb = signing_args(ctx.old.app), signing_args(ctx.new.app)
        if sa and sb and sa.get("slot-size") != sb.get("slot-size"):
            a, b = int(sa["slot-size"], 0), int(sb["slot-size"], 0)
            findings.append(
                Finding(
                    category=Category.LAYOUT,
                    kind="usable-image-size-changed",
                    subject="slot-size",
                    title=f"Usable image size (imgtool --slot-size) {a} → {b} bytes ({b - a:+})",
                    severity=Severity.BREAKING if b < a else Severity.RISKY,
                    confidence=Confidence.HIGH,
                    action="Images signed for the new slot size may not fit, or be accepted by, "
                    "the deployed bootloader's slots.",
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=(
                        artifact_diff(
                            "imgtool sign --slot-size (app build.ninja)",
                            ctx.old_spec,
                            ctx.new_spec,
                            a,
                            b,
                        ),
                    ),
                    old=a,
                    new=b,
                    remedy=self._remedy(["bootloader"], role),
                    provenance=provenance("imgtool sign arguments in the app's build.ninja", role),
                )
            )
        elif not (sa and sb):
            not_checked.append(
                NotChecked(
                    "usable image size",
                    "no imgtool sign command in the app's build at both refs",
                    self.name,
                )
            )
        else:
            checked.append(f"imgtool --slot-size unchanged ({sa.get('slot-size')})")

        breaking = [f.id for f in findings if f.severity is Severity.BREAKING]
        set_category(
            ctx.shared,
            "layout",
            "hazard" if breaking else "clean",
            "high",
            checked,
            findings=[f.id for f in findings],
        )
        return AnalyzerResult(findings, not_checked=not_checked)

    @staticmethod
    def _remedy(roles: list[str], role: BootloaderRole) -> Remedy:
        """The bootloader owns the layout: a change to a partition it uses needs the
        bootloader updated (or the device reflashed when updatability isn't evidenced). A
        partition only the app's storage uses can be kept in place by the app."""
        if "bootloader" in roles or "code" in roles:
            return Remedy.REFLASH  # bootloader updatability is not evidenced (§5.3)
        if "storage" in roles:
            return Remedy.APP_CHANGE
        return Remedy.UNKNOWN

    @staticmethod
    def _device(path: str) -> str:
        return path.split("/partitions")[0]

    @staticmethod
    def _dts_ev(img: ImageContext, label: str, ref: str) -> list[JSON]:
        dts = img.dir / Artifact.DTS.value
        if dts.is_file():
            for i, line in enumerate(dts.read_text().splitlines(), 1):
                if re.match(rf"^\s*{re.escape(label)}:\s", line):
                    return [file_line(f"<{ref} {img.name}>/{Artifact.DTS.value}", i, line.strip())]
        return []

    def _roles(
        self,
        ctx: ScanContext,
        labels: set[str],
        trees: tuple[Any, Any],
        not_checked: list[NotChecked],
    ) -> dict[str, dict[str, Any]]:
        """label -> {roles, refs (path/line/text), sources} from source at both refs."""
        out: dict[str, dict[str, Any]] = {
            lb: {"roles": [], "refs": [], "sources": []} for lb in labels
        }

        def note(label: str, role: str, ref: dict[str, Any], source: str) -> None:
            e = out.setdefault(label, {"roles": [], "refs": [], "sources": []})
            if role not in e["roles"]:
                e["roles"].append(role)
            if source not in e["sources"]:
                e["sources"].append(source)
            e["refs"].append(ref)

        for b, tree in ((ctx.old, trees[0]), (ctx.new, trees[1])):
            ref = b.workspace.resolved.ref.spec
            sboms = {i.name: _compiled(i) for i in b.images}
            # MCUboot repo paths compiled into the bootloader image.
            boot = bootloader_role(b).image
            bs = sboms.get(boot.name) if boot is not None else None
            boot_files = (
                None
                if bs is None
                else {f"boot/zephyr/{f}" for f in bs.get("app", {})} | set(bs.get("mcuboot", {}))
            )
            # Zephyr repo paths compiled into any image.
            zephyr_files: set[str] | None = (
                None
                if any(v is None for v in sboms.values())
                else {
                    f.removeprefix("zephyr/")
                    for v in sboms.values()
                    if v is not None
                    for f in v.get("zephyr", {})
                }
            )
            sbom_note = "" if boot_files is not None else " (no SBOM: uncompiled hits counted)"
            mod = mcuboot_module(b.workspace, ctx.runner)
            if mod is None:
                not_checked.append(
                    NotChecked(
                        "bootloader partition roles",
                        f"no mcuboot module in the {ref} workspace",
                        self.name,
                    )
                )
            else:
                for path, line, text, label in _grep_labels(
                    ctx,
                    Path(mod.path),
                    "HEAD",
                    "boot/zephyr",
                    labels,
                ):
                    if not _counts(path, boot_files):
                        continue
                    note(
                        label,
                        "bootloader",
                        {"path": f"<mcuboot {ref}>/{path}", "line": line, "text": text},
                        f"MCUboot boot/zephyr @ {mod.sha[:12]}{sbom_note}",
                    )
            # Storage: labels and chosen nodes the storage subsystems reference.
            chosen = {
                k.replace(",", "_").replace("-", "_"): v for k, v in tree.get("chosen", {}).items()
            }
            by_path = {p: lb for lb, (p, _) in partitions(tree).items()}
            mirror = b.workspace.resolved.mirror
            names = labels | set(chosen)
            for path, line, text, name in _grep_labels(
                ctx,
                mirror.path,
                b.workspace.resolved.ref.sha,
                " ".join(STORAGE_TREES),
                names,
                bare=True,
            ):
                target = by_path.get(chosen[name]) if name in chosen else name
                if target and _counts(path, zephyr_files):
                    label = target
                    note(
                        label,
                        "storage",
                        {"path": f"<zephyr {ref}>/{path}", "line": line, "text": text},
                        f"storage subsystems @ {ref}",
                    )
            for img in b.images:
                if img.has(Artifact.EDT):
                    t = edt(img, b.workspace, ctx.runner)
                    code = t.get("chosen", {}).get("zephyr,code-partition")
                    lb = {p: lb for lb, (p, _) in partitions(t).items()}.get(code or "")
                    if lb:
                        note(
                            lb,
                            "code",
                            {
                                "path": f"<{ref} {img.name}>/zephyr/zephyr.dts",
                                "line": 1,
                                "text": f"zephyr,code-partition = &{lb}",
                            },
                            f"chosen zephyr,code-partition of {img.name} @ {ref}",
                        )
        for e in out.values():
            if not e["roles"]:
                e["roles"] = ["unknown"]
        return out


def _grep_labels(
    ctx: ScanContext, repo: Path, rev: str, paths: str, names: set[str], bare: bool = False
) -> list[tuple[str, int, str, str]]:
    """(path, line, text, name) for whole-word occurrences of `names` under `paths`."""
    if not names:
        return []
    args = [
        "git",
        f"--git-dir={repo}" if bare else f"--git-dir={repo}/.git",
        "grep",
        "-n",
        "-w",
        "-I",
    ]
    for n in sorted(names):
        args += ["-e", n]
    args += [rev, "--", *paths.split()]
    proc = ctx.runner.run(args, check=False)
    out = []
    for line in proc.stdout.splitlines():
        parts = line.split(":", 3)
        if len(parts) < 4:
            continue
        _, path, lineno, text = parts
        for n in names:
            if re.search(rf"\b{re.escape(n)}\b", text):
                out.append((path, int(lineno), text.strip()[:160], n))
    return out
