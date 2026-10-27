"""Consistency between the images of one sysbuild build.

Checked in the target build, and in the source build to tell a new disagreement from
an existing one:

  partitions  flash partitions (children of a `fixed-partitions` node) must have the same
              address and size in every image's final devicetree; MCUboot and the app
              locate each other through them
  settings    board target, SoC and flash geometry must match
  pairs       app/bootloader option pairs that sysbuild switches on together (derived from
              that ref's share/sysbuild/image_configurations, see zmig.sysbuild_rules)
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact, BuildContext, ImageContext
from zmig.findings import JSON, Category, Confidence, Finding, Severity, artifact_diff, file_line
from zmig.scan import ScanContext
from zmig.snapshots import ConfigValues, config_values, edt, parse_dotconfig
from zmig.sysbuild_rules import IMAGE_FILES, Pair, pairs, parse

SETTINGS = ("BOARD_TARGET", "SOC", "FLASH_SIZE", "FLASH_BASE_ADDRESS")


def partitions(tree: dict[str, Any]) -> dict[str, tuple[str, Any]]:
    """label (or node name) -> (path, reg) for every flash partition. Two representations
    exist: children of a `fixed-partitions` node (older trees), and nodes that are
    themselves partitions (`zephyr,mapped-partition` from v4.4, under a `partitions` node
    without a compatible)."""
    nodes = tree["nodes"]
    out = {}
    for path, n in nodes.items():
        parent = path.rsplit("/", 1)[0] or "/"
        in_fixed = parent in nodes and any(
            c in ("fixed-partitions", "fixed-subpartitions") for c in nodes[parent]["compats"]
        )
        is_partition = any(c.endswith("-partition") for c in n["compats"])
        if in_fixed or is_partition:
            out[(n["labels"] or [n["name"]])[0]] = (path, n["props"].get("reg"))
    return out


def _dts_line(img: ImageContext, label: str) -> int:
    dts = img.dir / Artifact.DTS.value
    if dts.is_file():
        for i, line in enumerate(dts.read_text().splitlines(), 1):
            if re.match(rf"^\s*{re.escape(label)}:\s", line):
                return i
    return 1


class CrossImageAnalyzer:
    name: ClassVar[str] = "crossimage"
    kind: ClassVar[Literal["build"]] = "build"
    per_image: ClassVar[bool] = False
    requires: ClassVar[frozenset[Artifact]] = frozenset()

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        if len(ctx.new.images) < 2:
            return AnalyzerResult()
        findings: list[Finding] = []
        target = self._check(ctx, ctx.new)
        source = {k for k, *_ in self._check(ctx, ctx.old)} if len(ctx.old.images) > 1 else set()
        for key, title, action, ev, data in target:
            existing = key in source
            findings.append(
                Finding(
                    category=Category.CROSSIMAGE,
                    kind=key.split(":")[0],
                    subject=key,
                    title=title + (" (already so in the source build)" if existing else ""),
                    # A disagreement between images is a defect even if the source build
                    # already had it; the title says so instead of lowering the severity.
                    severity=Severity.BREAKING,
                    confidence=Confidence.HIGH,
                    action=action,
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=tuple(ev),
                    new=data,
                    heuristics=("crossimage.sysbuild-pairs",) if key.startswith("pair") else (),
                )
            )
        return AnalyzerResult(findings)

    def _check(
        self, ctx: ScanContext, b: BuildContext
    ) -> list[tuple[str, str, str, list[JSON], Any]]:
        """(key, title, action, evidence, data) for each disagreement in build `b`."""
        out: list[tuple[str, str, str, list[JSON], Any]] = []
        ref = b.workspace.resolved.ref.spec
        imgs = [i for i in b.images if i.has(Artifact.CONFIG)]
        boot = next((i for i in imgs if i.role == "bootloader"), None)
        anchor = boot or next((i for i in imgs if i.role == "app"), None)
        if anchor is None:
            return out
        cfg: dict[str, ConfigValues] = {i.name: config_values(i) for i in imgs}
        trees = {i.name: edt(i, b.workspace, ctx.runner) for i in imgs if i.has(Artifact.EDT)}

        def where(i: ImageContext) -> str:
            return f"<{ref} {i.name}>/{Artifact.DTS.value}"

        for other in imgs:
            if other is anchor:
                continue
            if anchor.name in trees and other.name in trees:
                pa, po = partitions(trees[anchor.name]), partitions(trees[other.name])
                for label in sorted(pa.keys() | po.keys()):
                    ra, ro = pa.get(label, (None, None))[1], po.get(label, (None, None))[1]
                    if ra == ro:
                        continue
                    ev: list[JSON] = []
                    for img, has in ((anchor, label in pa), (other, label in po)):
                        if has:
                            ev.append(
                                file_line(
                                    where(img),
                                    _dts_line(img, label),
                                    f"{label} reg = {(pa if img is anchor else po)[label][1]}",
                                )
                            )
                    ev += self._overlay_lines(ctx, label)
                    out.append(
                        (
                            f"partition:{label}:{anchor.name}:{other.name}",
                            f"Partition {label} differs between images {anchor.name} "
                            f"({_fmt_reg(ra)}) and {other.name} ({_fmt_reg(ro)}) in {ref}",
                            "Both images must use the same flash layout; put partition changes in "
                            "a board overlay shared by every image (or sysbuild/<image>.overlay "
                            "for each), not only the app's overlay.",
                            ev,
                            {anchor.name: ra, other.name: ro},
                        )
                    )
            for opt in SETTINGS:
                va, vo = cfg[anchor.name].get(opt), cfg[other.name].get(opt)
                if va != vo:
                    out.append(
                        (
                            f"setting:{opt}:{anchor.name}:{other.name}",
                            f"CONFIG_{opt} differs between images {anchor.name} ({va}) and "
                            f"{other.name} ({vo}) in {ref}",
                            "The images are built for different hardware or flash geometry.",
                            [
                                artifact_diff(f"{i.name}/zephyr/.config", ref, ref, va, vo)
                                for i in (other,)
                            ],
                            {anchor.name: va, other.name: vo},
                        )
                    )

        app = next((i for i in imgs if i.role == "app"), None)
        if boot is not None and app is not None:
            out += self._pairs(b, app, boot, cfg[app.name], cfg[boot.name])
        return out

    @staticmethod
    def _overlay_lines(ctx: ScanContext, label: str) -> list[JSON]:
        """The user's overlay lines that set this partition, in any image."""
        return [
            file_line(loc.path, loc.line, loc.text)
            for ref, prop, loc in ctx.app_usage.dt_props_set
            if ref.kind == "label" and ref.value == label and prop == "reg"
        ]

    @staticmethod
    def _pairs(
        b: BuildContext, app: ImageContext, boot: ImageContext, ca: ConfigValues, cb: ConfigValues
    ) -> list[tuple[str, str, str, list[JSON], Any]]:
        z = b.workspace.zephyr
        if not all((z / f).is_file() for f in IMAGE_FILES.values()):
            return []
        rules = pairs(
            parse((z / IMAGE_FILES["app"]).read_text(), "app", IMAGE_FILES["app"]),
            parse(
                (z / IMAGE_FILES["bootloader"]).read_text(), "bootloader", IMAGE_FILES["bootloader"]
            ),
        )
        sb_cfg = b.dir / "zephyr" / ".config"
        sb = parse_dotconfig(sb_cfg.read_text()) if sb_cfg.is_file() else {}
        ref = b.workspace.resolved.ref.spec
        out: list[tuple[str, str, str, list[JSON], Any]] = []

        def on(c: ConfigValues, opt: str) -> bool:
            # Pairs are about switches: only a bool set to y counts (a string option
            # like a key file path is '""' when unset, which isn't "enabled").
            return c.get(opt.removeprefix("CONFIG_"), "n") == "y"

        def ev(p: Pair) -> list[JSON]:
            return [
                file_line(f"<zephyr {ref}>/{p.app.file}", p.app.line, p.app.option),
                file_line(
                    f"<zephyr {ref}>/{p.bootloader.file}", p.bootloader.line, p.bootloader.option
                ),
                artifact_diff(
                    f"{app.name} / {boot.name} .config",
                    ref,
                    ref,
                    f"{p.app.option}={ca.get(p.app.option[7:], 'n')}",
                    f"{p.bootloader.option}={cb.get(p.bootloader.option[7:], 'n')}",
                ),
            ]

        # 1. sysbuild enabled the trigger: both sides must have their option.
        for p in rules:
            if sb.get(p.trigger.removeprefix("SB_"), "n") == "y" and not (
                on(ca, p.app.option) and on(cb, p.bootloader.option)
            ):
                out.append(
                    (
                        f"pair:{p.trigger}:{p.app.option}:{p.bootloader.option}",
                        f"{p.trigger} is set, but {app.name} has {p.app.option}="
                        f"{ca.get(p.app.option[7:], 'n')} and {boot.name} has "
                        f"{p.bootloader.option}={cb.get(p.bootloader.option[7:], 'n')}",
                        "An image-specific override contradicts sysbuild's setting; remove "
                        "it, or change the SB_CONFIG option instead.",
                        ev(p),
                        {"trigger": p.trigger},
                    )
                )
        # 2. Whatever sysbuild says: an MCUboot-integration option the app is built with
        # needs one of its partners in the bootloader, and the other way round.
        mc = [p for p in rules if p.app.option.startswith("CONFIG_MCUBOOT_")]
        for opt in sorted({p.app.option for p in mc}):
            partners = [p for p in mc if p.app.option == opt]
            if on(ca, opt) and not any(on(cb, p.bootloader.option) for p in partners):
                out.append(
                    (
                        f"pair:app:{opt}",
                        f"{app.name} is built with {opt}, but {boot.name} enables none of "
                        f"{', '.join(sorted({p.bootloader.option for p in partners}))}",
                        "The app expects a bootloader mode the bootloader doesn't "
                        "implement; align both images (normally via SB_CONFIG).",
                        ev(partners[0]),
                        {"app_option": opt},
                    )
                )
        for opt in sorted({p.bootloader.option for p in mc}):
            partners = [p for p in mc if p.bootloader.option == opt]
            if on(cb, opt) and not any(on(ca, p.app.option) for p in partners):
                out.append(
                    (
                        f"pair:boot:{opt}",
                        f"{boot.name} is built with {opt}, but {app.name} enables none of "
                        f"{', '.join(sorted({p.app.option for p in partners}))}",
                        "The bootloader runs a mode the app wasn't built for; align both "
                        "images (normally via SB_CONFIG).",
                        ev(partners[0]),
                        {"bootloader_option": opt},
                    )
                )
        return out


def _fmt_reg(reg: Any) -> str:
    if isinstance(reg, list) and len(reg) == 2 and all(isinstance(x, int) for x in reg):
        return f"0x{reg[0]:x}+0x{reg[1]:x}"
    return "absent" if reg is None else str(reg)
