"""The set of images, and how much changed in each.

image-added / image-removed  an image only one of the builds has (e.g. a bootloader the
                             target's sysbuild adds)
role-changed                 the same image name with a different role
images-summary               per image: build status at both refs, and how many compiled
                             files changed (from that image's SBOM)
mcuboot-revision             MCUboot's revision at both refs (module SHA and `git
                             describe`), and the compiled files that changed in the
                             bootloader image
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact, ImageContext
from zmig.findings import Category, Confidence, Finding, Remedy, Severity, plural
from zmig.hazards import BootloaderRole, bootloader_role, provenance
from zmig.sbom import APP_PACKAGE, compiled_sources, read_spdx_dir
from zmig.scan import ScanContext
from zmig.workspace import Module

MCUBOOT_MODULE = "mcuboot"


def _sources(img: ImageContext) -> dict[str, dict[str, str]] | None:
    if not img.has(Artifact.SPDX):
        return None
    return {
        k.replace("_", "-"): v
        for k, v in compiled_sources(read_spdx_dir(img.artifact(Artifact.SPDX).parent)).items()
    }


def _changed(
    old: dict[str, dict[str, str]], new: dict[str, dict[str, str]], package: str | None = None
) -> tuple[int, int, int]:
    """(changed, added, removed) compiled files, over all packages or one."""
    keys = [package] if package else sorted(old.keys() | new.keys())
    ch = ad = rm = 0
    for k in keys:
        o, n = old.get(k, {}), new.get(k, {})
        ch += sum(1 for f in o.keys() & n.keys() if o[f] != n[f])
        ad += len(n.keys() - o.keys())
        rm += len(o.keys() - n.keys())
    return ch, ad, rm


class ImagesAnalyzer:
    name: ClassVar[str] = "images"
    kind: ClassVar[Literal["build"]] = "build"
    per_image: ClassVar[bool] = False
    requires: ClassVar[frozenset[Artifact]] = frozenset()

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        findings: list[Finding] = []

        def add(
            kind: str,
            subject: str,
            title: str,
            sev: Severity,
            action: str,
            old: object = None,
            new: object = None,
        ) -> None:
            findings.append(
                Finding(
                    category=Category.IMAGES,
                    kind=kind,
                    subject=subject,
                    title=title,
                    severity=sev,
                    confidence=Confidence.HIGH,
                    action=action,
                    analyzer=self.name,
                    scope=ctx.scope,
                    old=old,
                    new=new,
                )
            )

        old = {i.name: i for i in ctx.old.images}
        new = {i.name: i for i in ctx.new.images}
        # The bootloader is matched by role: added, removed or renamed is one hazard.
        ob, nb = bootloader_role(ctx.old), bootloader_role(ctx.new)
        identity = self._identity(ctx, ob, nb)
        if identity is not None:
            findings.append(identity)
        boot_names = {r.image.name for r in (ob, nb) if r.image is not None}
        for n in sorted(old.keys() - new.keys() - boot_names):
            add(
                "image-removed",
                n,
                f"Image {n} ({old[n].role}) is not built at {ctx.new_spec}",
                Severity.RISKY,
                "Check whether your sysbuild configuration still enables it.",
                old=old[n].to_json(),
            )
        for n in sorted(new.keys() - old.keys() - boot_names):
            add(
                "image-added",
                n,
                f"Image {n} ({new[n].role}) is newly built at {ctx.new_spec}",
                Severity.RISKY,
                "Review what enabled it (SB_CONFIG defaults at the target).",
                new=new[n].to_json(),
            )
        rows: list[dict[str, Any]] = []
        for n in [i.name for i in ctx.old.images if i.name in new]:
            o, t = old[n], new[n]
            if o.role != t.role:
                add(
                    "role-changed",
                    n,
                    f"Image {n} was {o.role}, is {t.role}",
                    Severity.RISKY,
                    "Check the image's purpose at the target.",
                    o.role,
                    t.role,
                )
            so, sn = _sources(o), _sources(t)
            ch = _changed(so, sn) if so is not None and sn is not None else None
            rows.append(
                {
                    "image": n,
                    "role": t.role,
                    "status": [o.status, t.status],
                    "compiled_files": None
                    if ch is None
                    else {"changed": ch[0], "added": ch[1], "removed": ch[2]},
                }
            )
        add(
            "images-summary",
            "images",
            f"{plural(len(new), 'image')} at {ctx.new_spec}: "
            + ", ".join(
                f"{r['image']} ({r['role']}"
                + (
                    f", {r['compiled_files']['changed']} compiled files changed"
                    if r["compiled_files"]
                    else ""
                )
                + ")"
                for r in rows
            ),
            Severity.INFO,
            "Per-image findings follow, grouped by image.",
            new=rows,
        )
        findings += self._mcuboot(ctx, old, new)
        return AnalyzerResult(findings)

    def _identity(self, ctx: ScanContext, ob: BootloaderRole, nb: BootloaderRole) -> Finding | None:
        """One finding when the bootloader image appears, disappears or is renamed."""
        o, n = ob.image, nb.image
        if (o is None and n is None) or (o is not None and n is not None and o.name == n.name):
            return None
        if o is not None and n is not None:
            kind, sev, remedy = "renamed", Severity.RISKY, Remedy.UNKNOWN
            title = (
                f"The bootloader image is named {o.name} at {ctx.old_spec} and {n.name} "
                f"at {ctx.new_spec}"
            )
            action = (
                "It is compared by role, so its config and code are still analyzed; "
                "check scripts that refer to it by name (flashing, signing, CI)."
            )
        elif o is not None:
            kind, sev, remedy = "removed", Severity.BREAKING, Remedy.REFLASH
            title = f"The bootloader image {o.name} is no longer built at {ctx.new_spec}"
            action = (
                "Field devices keep the old bootloader, which expects a signed image in "
                "its slot layout; an app built without it can't be installed by OTA."
            )
        else:
            assert n is not None
            kind, sev, remedy = "added", Severity.BREAKING, Remedy.REFLASH
            title = f"A bootloader image {n.name} is built at {ctx.new_spec}, not at {ctx.old_spec}"
            action = (
                "Field devices have no bootloader to install the new app from its slot; "
                "adding one requires reflashing."
            )
        return Finding(
            category=Category.IMAGES,
            kind="bootloader-identity-changed",
            subject=kind,
            title=title,
            severity=sev,
            confidence=Confidence.HIGH,
            action=action,
            analyzer=self.name,
            scope=ctx.scope,
            old=o.to_json() if o else None,
            new=n.to_json() if n else None,
            remedy=remedy,
            provenance=provenance(
                f"domains.yaml and image .config at {ctx.old_spec} and {ctx.new_spec}",
                nb if n else ob,
            ),
        )

    def _mcuboot(
        self, ctx: ScanContext, old: dict[str, ImageContext], new: dict[str, ImageContext]
    ) -> list[Finding]:
        mo = _module(ctx.old.workspace.modules(ctx.runner))
        mn = _module(ctx.new.workspace.modules(ctx.runner))
        if mo is None or mn is None:
            return []
        boot_old = next((i for i in old.values() if i.role == "bootloader"), None)
        boot_new = next((i for i in new.values() if i.role == "bootloader"), None)
        desc_o, desc_n = _describe(ctx, mo), _describe(ctx, mn)
        data: dict[str, object] = {}
        detail, sev = "the bootloader image's SBOM isn't available", Severity.INFO
        if boot_old and boot_new and boot_old.name == boot_new.name:
            so, sn = _sources(boot_old), _sources(boot_new)
            if so is not None and sn is not None:
                # MCUboot's code in its image: the image's own sources (boot/zephyr, in
                # app.spdx) plus the mcuboot module package (boot/bootutil, ...).
                mc = tuple(
                    a + b
                    for a, b in zip(
                        _changed(so, sn, MCUBOOT_MODULE), _changed(so, sn, APP_PACKAGE), strict=True
                    )
                )
                total = _changed(so, sn)
                detail = (
                    f"{mc[0]} MCUboot source files compiled into {boot_new.name} changed "
                    f"({mc[1]} added, {mc[2]} removed); {total[0]} compiled files "
                    f"changed in that image overall"
                )
                sev = Severity.RISKY if mo.sha != mn.sha and any(mc) else Severity.INFO
                data = {
                    "mcuboot_files": dict(zip(("changed", "added", "removed"), mc, strict=True)),
                    "image_files": dict(zip(("changed", "added", "removed"), total, strict=True)),
                }
        same = mo.sha == mn.sha
        return [
            Finding(
                category=Category.IMAGES,
                kind="mcuboot-revision",
                subject=MCUBOOT_MODULE,
                title=(
                    f"MCUboot stays at {mo.sha[:12]} ({desc_o})"
                    if same
                    else f"MCUboot moves from {mo.sha[:12]} ({desc_o}) to {mn.sha[:12]} ({desc_n})"
                )
                + f"; {detail}",
                severity=sev,
                confidence=Confidence.HIGH,
                action="Read MCUboot's release notes for that range; image format, swap and "
                "signature changes need the bootloader and app updated together.",
                analyzer=self.name,
                scope=ctx.scope,
                old={"sha": mo.sha, "describe": desc_o},
                new={"sha": mn.sha, "describe": desc_n, **data},
            )
        ]


def _module(mods: list[Module]) -> Module | None:
    return next((m for m in mods if m.name == MCUBOOT_MODULE), None)


def _describe(ctx: ScanContext, m: Module) -> str:
    """`git describe` of a module checkout (tags may be missing in a narrow clone)."""
    proc = ctx.runner.run(
        ["git", "-C", str(m.path), "describe", "--tags", "--always", m.sha], check=False
    )
    return proc.stdout.strip() or m.sha[:12]
