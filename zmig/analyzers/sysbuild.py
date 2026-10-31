"""Sysbuild-level configuration (SB_CONFIG_*), and how it reaches the images.

Inputs at each ref: the user's sysbuild.conf, sysbuild's resolved config
(<build>/zephyr/.config), sysbuild's Kconfig metadata (that ref's kconfiglib), and the
propagation directives in share/sysbuild/image_configurations (zmig.sysbuild_rules).

An *option* change is reported from the SB_CONFIG diff. Whether *behavior* changed is
decided only from the resolved image options the SB_CONFIG symbol propagates to: if
they are identical at both refs, the finding says "behavior unchanged", whatever the
SB_CONFIG names did (DESIGN.md, "Field-upgrade verdict"). An image option that is itself renamed in
step (deprecated at the target, its help naming a replacement that takes its value) is
a rename, not a behavior change.

Values are compared with each ref's workspace and build directory replaced by
placeholders, so per-ref cache paths (e.g. the default signing key) don't show as changes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact, BuildContext, ImageContext
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
from zmig.hazards import bootloader_role, provenance, set_category
from zmig.scan import ScanContext
from zmig.snapshots import (
    ConfigValues,
    config_values,
    kconfig_symbols,
    sysbuild_config,
    sysbuild_kconfig,
)
from zmig.sysbuild_rules import IMAGE_FILES, Directive, parse

Symbols = dict[str, dict[str, Any]]

# Markers that follow from other options (a deprecated or experimental option in use);
# the option itself is reported.
DERIVED = frozenset({"DEPRECATED", "EXPERIMENTAL"})
UNSET = "(unset)"


def _off(v: str | None) -> bool:
    return v in (None, "n", "", '""')


def _same(a: str | None, b: str | None) -> bool:
    return (_off(a) and _off(b)) or a == b


def user_settings(
    app: Path, files: list[Path] | None = None
) -> dict[str, tuple[str, int, str, str]]:
    """SB_CONFIG_X -> (path, line, value, text) from the sysbuild-level fragments the build
    read (`ScanContext.sysbuild_config_files`: sysbuild.conf, SB_CONF_FILE), shown relative
    to the app; the app's sysbuild.conf when the log names none."""
    out: dict[str, tuple[str, int, str, str]] = {}
    for f in files if files is not None else [app / "sysbuild.conf"]:
        if not f.is_file():
            continue
        rel = os.path.relpath(f.resolve(), app).replace(os.sep, "/")
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if m := re.match(r"^\s*SB_CONFIG_(\w+)\s*=\s*(.*?)\s*$", line):
                out[m[1]] = (rel, i, m[2], line.strip())
    return out


@dataclass(frozen=True)
class Propagation:
    role: str
    image: str | None
    option: str
    rule: str  # "file:line"
    old: str | None
    new: str | None


class SysbuildAnalyzer:
    name: ClassVar[str] = "sysbuild"
    kind: ClassVar[Literal["build"]] = "build"
    per_image: ClassVar[bool] = False
    requires: ClassVar[frozenset[Artifact]] = frozenset()

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        ov, nv = sysbuild_config(ctx.old.dir), sysbuild_config(ctx.new.dir)
        if ov is not None and nv is not None:
            ov, nv = _normalized(ov, ctx.old), _normalized(nv, ctx.new)
        if ov is None or nv is None:
            set_category(
                ctx.shared,
                "sysbuild",
                "not-applicable",
                "high",
                ["not a sysbuild build"],
                extra={
                    "evidence": [
                        "no sysbuild configuration (zephyr/.config of _sysbuild) in "
                        "either build directory"
                    ]
                },
            )
            return AnalyzerResult(
                not_checked=[
                    NotChecked(
                        "sysbuild configuration", "not a sysbuild build at both refs", self.name
                    )
                ]
            )
        ok = sysbuild_kconfig(ctx.old.dir, ctx.old.workspace, ctx.runner)
        nk = sysbuild_kconfig(ctx.new.dir, ctx.new.workspace, ctx.runner)
        user = user_settings(ctx.old.spec.app.resolve(), ctx.sysbuild_config_files)
        rules = {side: self._rules(b) for side, b in (("old", ctx.old), ("new", ctx.new))}
        imgs = {side: self._images(b) for side, b in (("old", ctx.old), ("new", ctx.new))}
        findings: list[Finding] = []
        not_checked: list[NotChecked] = []
        if not rules["old"] or not rules["new"]:
            not_checked.append(
                NotChecked(
                    "SB_CONFIG propagation",
                    "share/sysbuild/image_configurations not found at one of the refs",
                    self.name,
                )
            )

        # Pair rules live in share/sysbuild/image_configurations; only MAIN and BOOTLOADER
        # are read (zmig.sysbuild_rules). Name whatever else this tree has.
        known = {Path(f).name for f in IMAGE_FILES.values()}
        others = sorted(
            {
                f"{f.name}@{b.workspace.resolved.ref.label}"
                for b in (ctx.old, ctx.new)
                for f in (b.workspace.zephyr / "share/sysbuild/image_configurations").glob(
                    "*.cmake"
                )
                if f.name not in known
            }
        )
        not_checked.append(
            NotChecked(
                "sysbuild pair rules outside MAIN/BOOTLOADER",
                "only MAIN_image_default.cmake and BOOTLOADER_image_default.cmake are read; "
                + ("not read: " + ", ".join(others) if others else "no other files at either ref")
                + ". Image pairs set up by other sysbuild CMake (modules, vendor images) aren't "
                "checked.",
                self.name,
            )
        )

        def propagation(syms: set[str]) -> list[Propagation]:
            out = []
            for side in ("old", "new"):
                for d in rules[side]:
                    if not ({t.removeprefix("SB_CONFIG_") for t in d.triggers} & syms):
                        continue
                    img = imgs["new"].get(d.role) or imgs["old"].get(d.role)
                    oc = imgs["old"].get(d.role)
                    nc = imgs["new"].get(d.role)
                    out.append(
                        Propagation(
                            d.role,
                            img[0].name if img else None,
                            d.option,
                            f"{d.file}:{d.line}@{ctx.old_spec if side == 'old' else ctx.new_spec}",
                            oc[1].get(d.option.removeprefix("CONFIG_")) if oc else None,
                            nc[1].get(d.option.removeprefix("CONFIG_")) if nc else None,
                        )
                    )
            return sorted(set(out), key=lambda p: (p.role, p.option, p.rule))

        image_syms: dict[str, Symbols] = {}

        def symbols_of(role: str) -> Symbols:
            if role not in image_syms:
                img = imgs["new"].get(role)
                image_syms[role] = (
                    kconfig_symbols(img[0], ctx.new.workspace, ctx.runner) if img else {}
                )
            return image_syms[role]

        def behavior(props: list[Propagation]) -> tuple[bool, list[JSON], list[JSON]]:
            diff = [
                (p.role, {"image": p.image, "option": p.option, "old": p.old, "new": p.new})
                for p in {(x.role, x.option): x for x in props}.values()
                if not _same(p.old, p.new)
            ]
            changed = [c for _, c in diff]
            renamed: list[JSON] = []
            for role, c in diff:
                # An image option renamed in step: old y -> off, deprecated at the target,
                # and the replacement its help names goes off -> y in the same image.
                name = str(c["option"]).removeprefix("CONFIG_")
                if c["old"] != "y" or not _off(c["new"]):
                    continue
                syms = symbols_of(role)
                if not (syms.get(name) or {}).get("deprecated"):
                    continue
                repl = self._replacement(name, syms, syms)
                twin = next(
                    (
                        d
                        for r, d in diff
                        if r == role
                        and d["option"] == f"CONFIG_{repl}"
                        and _off(d["old"])
                        and d["new"] == "y"
                    ),
                    None,
                )
                if twin is not None:
                    changed = [x for x in changed if x is not c and x is not twin]
                    renamed.append({**c, "renamed_to": twin["option"]})
            return not changed, changed, renamed

        handled: set[str] = set()
        # 1. Choices whose active member changed (e.g. the MCUboot mode).
        choices: dict[str, list[str]] = {}
        for sym in ok.keys() | nk.keys():
            ch = (nk.get(sym) or ok.get(sym) or {}).get("choice")
            if ch:
                choices.setdefault(ch, []).append(sym)
        for ch, members in sorted(choices.items()):
            a = next((m for m in members if ov.get(m) == "y"), None)
            b = next((m for m in members if nv.get(m) == "y"), None)
            if a == b:
                continue
            handled.update(members)
            set_by_user = next((m for m in members if m in user), None)
            props = propagation({x for x in (a, b) if x})
            same, changed, renamed = behavior(props)
            findings.append(
                self._finding(
                    ctx,
                    "sb-choice-changed" if set_by_user else "sb-choice-default-changed",
                    ch,
                    title=f"Sysbuild choice {ch}: SB_CONFIG_{a} at {ctx.old_spec} → "
                    f"SB_CONFIG_{b} at {ctx.new_spec}"
                    + ("" if set_by_user else " (default; not set in sysbuild.conf)")
                    + ("; image options unchanged" if same else ""),
                    a=a,
                    b=b,
                    props=props,
                    changed=changed,
                    ok=ok,
                    nk=nk,
                    user=user,
                )
            )

        # 2. Symbols the user sets that are deprecated or gone at the target.
        for sym, (path, line, _value, text) in sorted(user.items()):
            if sym in handled:
                continue
            if sym in nk and not nk[sym].get("deprecated"):
                continue
            handled.add(sym)
            repl = self._replacement(sym, ok, nk)
            props = propagation({sym, *([repl] if repl else [])})
            same, changed, renamed = behavior(props)
            gone = sym not in nk
            if same:
                title = (
                    f"SB_CONFIG_{sym} is {'removed' if gone else 'deprecated'} at "
                    f"{ctx.new_spec}"
                    + (f", renamed to SB_CONFIG_{repl}" if repl else "")
                    + "; behavior unchanged (the image options it propagates to are "
                    + (
                        "identical at both refs"
                        if not renamed
                        else "identical at both refs, "
                        "except image options renamed in step: "
                        + ", ".join(f"{r['option']} → {r['renamed_to']}" for r in renamed)
                    )
                    + ")"
                )
                sev, remedy = Severity.INFO, None
            else:
                title = (
                    f"SB_CONFIG_{sym} is {'removed' if gone else 'deprecated'} at "
                    f"{ctx.new_spec}, and the image options it propagates to changed"
                )
                sev, remedy = Severity.RISKY, Remedy.APP_CHANGE
            ev: list[JSON] = [file_line(path, line, text)]
            ev += self._kconfig_ev(ctx, sym, ok, nk)
            findings.append(
                Finding(
                    category=Category.SYSBUILD,
                    kind="sb-option-renamed"
                    if repl
                    else ("sb-option-removed" if gone else "sb-option-deprecated"),
                    subject=f"SB_CONFIG_{sym}",
                    title=title,
                    severity=sev,
                    confidence=Confidence.HIGH if same else Confidence.MEDIUM,
                    action=(
                        f"Switch sysbuild.conf to SB_CONFIG_{repl}=y before the old name is "
                        f"removed."
                        if repl
                        else "Find its replacement in the migration guide."
                    ),
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=tuple(ev),
                    old={"value": ov.get(sym), "propagates_to": [p.__dict__ for p in props]},
                    new={
                        "value": nv.get(sym),
                        "replacement": repl,
                        "behavior_unchanged": same,
                        "changed_image_options": changed,
                        "renamed_image_options": renamed,
                    },
                    remedy=remedy,
                    heuristics=("sysbuild.replacement-from-help",) if repl else (),
                    provenance=provenance(
                        f"sysbuild .config and share/sysbuild Kconfig at "
                        f"{ctx.old_spec} and {ctx.new_spec}; propagation from "
                        f"{', '.join(IMAGE_FILES.values())}",
                        bootloader_role(ctx.new),
                    ),
                )
            )

        # 3. Any other SB_CONFIG value that changed.
        for sym in sorted(ov.keys() | nv.keys()):
            if sym in handled or sym in DERIVED or _same(ov.get(sym), nv.get(sym)):
                continue
            props = propagation({sym})
            same, changed, renamed = behavior(props)
            findings.append(
                Finding(
                    category=Category.SYSBUILD,
                    kind="sb-option-changed" if sym in user else "sb-option-default-changed",
                    subject=f"SB_CONFIG_{sym}",
                    title=f"SB_CONFIG_{sym}: {ov.get(sym, UNSET)} → {nv.get(sym, UNSET)}"
                    + ("" if props else " (no image propagation derived)"),
                    severity=Severity.INFO if same else Severity.RISKY,
                    confidence=Confidence.HIGH,
                    action="Check the image options it reaches (listed)."
                    if props
                    else "Sysbuild-level only; no image option rule found for it.",
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=(
                        artifact_diff(
                            "sysbuild zephyr/.config",
                            ctx.old_spec,
                            ctx.new_spec,
                            ov.get(sym),
                            nv.get(sym),
                        ),
                    ),
                    old={"value": ov.get(sym), "propagates_to": [p.__dict__ for p in props]},
                    new={"value": nv.get(sym), "changed_image_options": changed},
                    provenance=provenance(
                        "sysbuild resolved .config at both refs", bootloader_role(ctx.new)
                    ),
                )
            )
        return AnalyzerResult(findings, not_checked=not_checked)

    def _finding(
        self,
        ctx: ScanContext,
        kind: str,
        choice: str,
        *,
        title: str,
        a: str | None,
        b: str | None,
        props: list[Propagation],
        changed: list[JSON],
        ok: Symbols,
        nk: Symbols,
        user: dict[str, tuple[str, int, str, str]],
    ) -> Finding:
        same = not changed
        # config-pin: the old member still exists at the target and isn't deprecated.
        pin = a is not None and a in nk and not nk[a].get("deprecated")
        ev: list[JSON] = []
        for sym in (a, b):
            if sym:
                ev += self._kconfig_ev(ctx, sym, ok, nk)
        if a in user:
            path, line, _, text = user[a]
            ev.insert(0, file_line(path, line, text))
        return Finding(
            category=Category.SYSBUILD,
            kind=kind,
            subject=choice,
            title=title,
            severity=Severity.INFO if same else Severity.RISKY,
            confidence=Confidence.HIGH,
            action=(
                "Behavior is unchanged; nothing to do."
                if same
                else (
                    f"To keep the source behavior, set SB_CONFIG_{a}=y in sysbuild.conf (it still "
                    f"exists at {ctx.new_spec}); the hazard analysis says whether you must."
                    if pin
                    else "The previous option no longer exists at the target."
                )
            ),
            analyzer=self.name,
            scope=ctx.scope,
            evidence=tuple(ev),
            old={"active": a, "propagates_to": [p.__dict__ for p in props]},
            new={
                "active": b,
                "behavior_unchanged": same,
                "changed_image_options": changed,
                "pin_available": pin,
            },
            remedy=None if same else (Remedy.CONFIG_PIN if pin else Remedy.UNKNOWN),
            provenance=provenance(
                "sysbuild resolved .config and Kconfig at both refs; image .config values",
                bootloader_role(ctx.new),
            ),
        )

    @staticmethod
    def _kconfig_ev(ctx: ScanContext, sym: str, ok: Symbols, nk: Symbols) -> list[JSON]:
        ev: list[JSON] = []
        for syms, ref in ((ok, ctx.old_spec), (nk, ctx.new_spec)):
            for loc in (syms.get(sym) or {}).get("locations", [])[:1]:
                path, _, line = loc.rpartition(":")
                ev.append(
                    file_line(
                        f"<zephyr {ref}>/{path}",
                        int(line or 1),
                        f"config {sym}" + (" [deprecated]" if syms[sym].get("deprecated") else ""),
                    )
                )
        return ev

    @staticmethod
    def _replacement(sym: str, ok: Symbols, nk: Symbols) -> str | None:
        """The symbol a deprecated or removed SB option points to: named in its own help
        text at the target (or the source, if it is gone), and defined at the target."""
        info = nk.get(sym) or ok.get(sym) or {}
        for name in re.findall(r"\b(?:SB_CONFIG_)?([A-Z][A-Z0-9_]{3,})\b", info.get("help") or ""):
            if name != sym and name in nk and not nk[name].get("deprecated"):
                return str(name)
        return None

    @staticmethod
    def _rules(b: BuildContext) -> list[Directive]:
        z = b.workspace.zephyr
        out: list[Directive] = []
        for role, f in IMAGE_FILES.items():
            if (z / f).is_file():
                out += parse((z / f).read_text(), role, f)
        return out

    @staticmethod
    def _images(b: BuildContext) -> dict[str, tuple[ImageContext, ConfigValues]]:
        return {
            i.role: (i, config_values(i))
            for i in b.images
            if i.role in ("app", "bootloader") and i.has(Artifact.CONFIG)
        }


def _normalized(values: dict[str, str], b: BuildContext) -> dict[str, str]:
    """Values with this ref's workspace and build directory replaced by placeholders."""
    subs = ((str(b.workspace.root), "<workspace>"), (str(b.dir), "<build>"))
    out = {}
    for k, v in values.items():
        for path, token in subs:
            v = v.replace(path, token)
        out[k] = v
    return out
