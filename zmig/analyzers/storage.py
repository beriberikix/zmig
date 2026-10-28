"""Persistent storage across the migration (docs/DESIGN-P1.md §6).

Data a deployed device wrote stays where and how the source build wrote it. For the app
image at both refs this checks:

  backends        enabled storage backends: `y` symbols whose Kconfig definition lies under
                  a backend tree (zmig/data/hazard_seeds.yaml `storage.backends`)
  switches        a backend disabled, or a backend choice (e.g. the ITS store
                  implementation) switched, between the refs
  causes          why a value the user didn't set changed: a `select`/`imply` that
                  appeared or disappeared, or an unmet `depends on` for a value the user
                  did set, with the Kconfig lines at both refs
  remedy          the user-settable symbols that restore the source's backends
                  (app-change), or unknown
  partitions      partitions referenced by the backend's compiled code (app SBOM), resolved
                  in the final devicetree; layout hazards on them are linked
  on-media        srcdiff of the backend's seeds (ATE structs, magic, version, IDs)
  implementation  compiled backend files that changed without touching those definitions
                  (medium confidence)
"""

from __future__ import annotations

import re
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
    file_line,
)
from zmig.hazards import CategoryStatus, provenance, set_category
from zmig.sbom import compiled_sources, read_spdx_dir
from zmig.scan import ScanContext
from zmig.snapshots import config_values, edt, kconfig_symbols, kconfig_try
from zmig.srcdiff import Seed, SeedDiff, Source, compare, load_seeds

Symbols = dict[str, dict[str, Any]]
_KCONFIG_ENTRY = re.compile(
    r"^(menuconfig|config|choice|endchoice|menu|endmenu|if|endif|source|rsource|osource|comment)\b"
)


def _zephyr_files(img: ImageContext) -> dict[str, str] | None:
    if not img.has(Artifact.SPDX):
        return None
    z = compiled_sources(read_spdx_dir(img.artifact(Artifact.SPDX).parent)).get("zephyr", {})
    return {f.removeprefix("zephyr/"): h for f, h in z.items()}


class StorageAnalyzer:
    name: ClassVar[str] = "storage"
    kind: ClassVar[Literal["build"]] = "build"
    per_image: ClassVar[bool] = False
    requires: ClassVar[frozenset[Artifact]] = frozenset()

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        app_o, app_n = ctx.old.app, ctx.new.app
        nc: list[NotChecked] = []
        for img in (app_o, app_n):
            if not img.has(Artifact.CONFIG):
                reason = f"app image at {img.ref} has no .config ({img.status})"
                set_category(ctx.shared, "storage", "unknown", "low", [], reason=reason)
                return AnalyzerResult(
                    not_checked=[NotChecked("persistent storage", reason, self.name)]
                )
        others = [i.name for i in ctx.new.images if i.role == "other"]
        if others:
            nc.append(
                NotChecked(
                    "persistent storage of non-app images",
                    f"only the app image is checked; not: {', '.join(others)}",
                    self.name,
                )
            )

        cfg = load_seeds()["storage"]["backends"]
        ks_o = kconfig_symbols(app_o, ctx.old.workspace, ctx.runner)
        ks_n = kconfig_symbols(app_n, ctx.new.workspace, ctx.runner)
        cv_o, cv_n = config_values(app_o), config_values(app_n)
        user = {k: v for k, v in ctx.usage.kconfig_set.items()}
        src_o = Source(
            ctx.old.workspace.resolved.mirror.path,
            ctx.old.workspace.resolved.ref.sha,
            ctx.runner,
            bare=True,
        )
        src_n = Source(
            ctx.new.workspace.resolved.mirror.path,
            ctx.new.workspace.resolved.ref.sha,
            ctx.runner,
            bare=True,
        )
        unknowns: list[str] = []
        checked: list[str] = []
        findings: list[Finding] = []
        linked: list[str] = []
        confidence = "high"

        for b, info in cfg.items():
            for t in info["trees"]:
                for ws, ref in (
                    (ctx.old.workspace, ctx.old_spec),
                    (ctx.new.workspace, ctx.new_spec),
                ):
                    if not (ws.zephyr / t).is_dir():
                        # The backend can't be enabled from a tree that isn't there (ZMS
                        # before v4.0, secure storage before v4.1); not a gap in the check.
                        checked.append(f"backend tree {t} ({b}) not present at {ref}")

        def backend_of(sym: str, ks: Symbols) -> str | None:
            for loc in (ks.get(sym) or {}).get("locations", []):
                path = loc.rsplit(":", 1)[0]
                for b, info in cfg.items():
                    if any(path.startswith(t + "/") for t in info["trees"]) and not any(
                        path.startswith(x + "/") for x in info.get("exclude", [])
                    ):
                        return str(b)
            return None

        def members(ks: Symbols) -> dict[str, list[str]]:
            out: dict[str, list[str]] = {}
            for s in ks:
                b = backend_of(s, ks)
                if b:
                    out.setdefault(b, []).append(s)
            return out

        mem_o, mem_n = members(ks_o), members(ks_n)
        enabled = {
            side: {
                b: sorted(
                    s for s in syms if cv.get(s) == "y" and (ks.get(s) or {}).get("type") == "bool"
                )
                for b, syms in mem.items()
            }
            for side, mem, cv, ks in (("old", mem_o, cv_o, ks_o), ("new", mem_n, cv_n, ks_n))
        }
        on_o = {b for b, v in enabled["old"].items() if v}
        on_n = {b for b, v in enabled["new"].items() if v}
        if not on_o and not on_n:
            set_category(
                ctx.shared,
                "storage",
                "not-applicable",
                "high",
                ["no storage backend symbol is enabled in the app at either ref"],
            )
            return AnalyzerResult(not_checked=nc)
        checked.append(
            "enabled backends: "
            + "; ".join(
                f"{b} {ctx.old_spec}={'on' if b in on_o else 'off'} "
                f"{ctx.new_spec}={'on' if b in on_n else 'off'}"
                for b in sorted(on_o | on_n)
            )
        )

        def prop_line(
            src: Source, ks: Symbols, owner: str, prop: str, target: str, ref: str
        ) -> JSON | None:
            """The `select/imply/depends on` line of `owner` that names `target`."""
            for loc in (ks.get(owner) or {}).get("locations", []):
                path, _, line = loc.rpartition(":")
                lines = src.show(path) if not path.startswith("..") else None
                if not lines:
                    continue
                for i in range(int(line), len(lines)):
                    text = lines[i]
                    if _KCONFIG_ENTRY.match(text):
                        break
                    if re.match(rf"^\s*{prop}\b.*\b{re.escape(target)}\b", text):
                        return file_line(f"<zephyr {ref}>/{path}", i + 1, text.strip())
            return None

        def settable(sym: str) -> bool:
            i = ks_n.get(sym) or {}
            return bool(i.get("prompt")) and not i.get("choice") and not i.get("deprecated")

        def causes(sym: str) -> list[JSON]:
            """Why `sym` changed value between the refs, from Kconfig metadata at both. Each
            cause names the settable symbols that would undo it ("assign")."""
            out: list[JSON] = []
            for prop, key in (("select", "selects"), ("imply", "implies")):
                for owner, info in ks_o.items():
                    if sym not in info.get(key, []) or cv_o.get(owner) != "y":
                        continue
                    still = sym in (ks_n.get(owner) or {}).get(key, [])
                    if still and cv_n.get(owner) == "y":
                        continue
                    ev = prop_line(src_o, ks_o, owner, prop, sym, ctx.old_spec)
                    verb = {"select": "selects", "imply": "implies"}[prop]
                    why = (
                        f"CONFIG_{owner} {'no longer ' if not still else ''}{verb} CONFIG_{sym}"
                        + (
                            ""
                            if not still
                            else f" but is {cv_n.get(owner) or 'unset'} at {ctx.new_spec}"
                        )
                    )
                    out.append(
                        {
                            "cause": why,
                            "evidence": [e for e in [ev] if e],
                            "assign": [sym] if settable(sym) else [],
                        }
                    )
            if sym in user:
                dep_o = (ks_o.get(sym) or {}).get("depends", "")
                dep_n = (ks_n.get(sym) or {}).get("depends", "")
                unmet = [
                    s
                    for s in re.findall(r"\b[A-Z][A-Z0-9_]+\b", dep_n)
                    if s in ks_n and cv_n.get(s) != "y"
                ]
                if dep_o != dep_n:
                    dev = [
                        prop_line(src_n, ks_n, sym, "depends on", s, ctx.new_spec) for s in unmet
                    ]
                    loc, val = user[sym][0]
                    out.append(
                        {
                            "cause": f"you set CONFIG_{sym}={val} ({loc.path}:{loc.line}); "
                            f"at {ctx.new_spec} "
                            f"it depends on {dep_n}"
                            + (
                                f", unmet: {', '.join('CONFIG_' + s for s in unmet)}"
                                if unmet
                                else ""
                            ),
                            "evidence": [file_line(loc.path, loc.line, loc.text)]
                            + [e for e in dev if e],
                            "assign": [u for u in unmet if settable(u)],
                        }
                    )
            return out

        # --- switches and changes, per backend ----------------------------------------------
        hazards_pending: list[dict[str, Any]] = []
        for b in sorted(on_o | on_n):
            syms = sorted(set(mem_o.get(b, [])) | set(mem_n.get(b, [])))
            changed = [s for s in syms if cv_o.get(s) != cv_n.get(s)]
            if not changed:
                continue
            # Choices of this backend whose active member changed.
            choices: dict[str, dict[str, Any]] = {}
            for s in syms:
                ch = (ks_n.get(s) or ks_o.get(s) or {}).get("choice")
                if ch:
                    c = choices.setdefault(
                        ch,
                        {
                            "old": None,
                            "new": None,
                            "prompt": (ks_n.get(s) or ks_o.get(s) or {}).get("choice_prompt") or ch,
                        },
                    )
                    if cv_o.get(s) == "y":
                        c["old"] = s
                    if cv_n.get(s) == "y":
                        c["new"] = s
            switched = {k: v for k, v in choices.items() if v["old"] != v["new"]}
            lost = b in on_o and b not in on_n
            why: list[JSON] = []
            for s in changed:
                if s not in user or s in [c["old"] for c in switched.values()]:
                    why += causes(s)
            if lost or switched:
                hazards_pending.append(
                    {"backend": b, "lost": lost, "switched": switched, "why": why}
                )
            else:
                # Parameters of on-media data exist at both refs; a symbol the source didn't
                # define can't describe data a device wrote (regression: smp_svr's new
                # FUSE/virtiofs symbols, None -> 0, were reported with a config pin).
                vals = [
                    s
                    for s in changed
                    if (ks_n.get(s) or ks_o.get(s) or {}).get("type") != "bool"
                    and s in ks_o
                    and s in ks_n
                    and cv_o.get(s) is not None
                ]
                bools = [s for s in changed if s not in vals]
                if vals:
                    findings.append(
                        self._finding(
                            ctx,
                            "backend-parameters-changed",
                            b,
                            f"Storage backend {b} parameters changed: "
                            + ", ".join(f"CONFIG_{s} {cv_o.get(s)} → {cv_n.get(s)}" for s in vals),
                            Severity.RISKY,
                            Confidence.MEDIUM,
                            "Parameters such as sector counts and key prefixes shape on-media "
                            "data; "
                            "pin the source values if they do for this backend.",
                            old={s: cv_o.get(s) for s in vals},
                            new={s: cv_n.get(s) for s in vals},
                            remedy=Remedy.CONFIG_PIN
                            if all((ks_n.get(s) or {}).get("prompt") for s in vals)
                            else Remedy.UNKNOWN,
                            ev=[e for c in why for e in c["evidence"]][:6],
                        )
                    )
                if bools:
                    findings.append(
                        self._finding(
                            ctx,
                            "backend-options-changed",
                            b,
                            f"Storage backend {b} options changed: "
                            + ", ".join(
                                f"CONFIG_{s} {cv_o.get(s) or 'unset'} → {cv_n.get(s) or 'unset'}"
                                for s in bools[:8]
                            ),
                            Severity.INFO,
                            Confidence.HIGH,
                            "The backend stays enabled; review these options.",
                            old={s: cv_o.get(s) for s in bools},
                            new={s: cv_n.get(s) for s in bools},
                        )
                    )
        # Remedy: the settable symbols the causes name, verified by re-resolving the target's
        # Kconfig with them (zmig/extract/kconfig_try.py): every backend symbol enabled at the
        # source, and every switched choice's source member, must come back.
        assign = sorted({a for h in hazards_pending for c in h["why"] for a in c["assign"]})
        want = sorted(
            {s for h in hazards_pending for s in enabled["old"].get(h["backend"], [])}
            | {v["old"] for h in hazards_pending for v in h["switched"].values() if v["old"]}
        )
        # Follow unmet `depends on` of what didn't come back (settable symbols only), a few
        # rounds: e.g. NVS needs FLASH_MAP, which the source also only got by `imply`.
        tried: dict[str, str | None] | None = None
        for _ in range(4):
            if not assign:
                break
            deps = sorted(
                {
                    d
                    for w in want + assign
                    for d in re.findall(
                        r"\b[A-Z][A-Z0-9_]+\b", (ks_n.get(w) or {}).get("depends", "")
                    )
                    if d in ks_n
                }
            )
            tried = kconfig_try(
                app_n, ctx.new.workspace, ctx.runner, dict.fromkeys(assign, "y"), want + deps
            )
            if tried is None:
                break
            more = [
                d
                for d in deps
                if tried.get(d) != "y" and cv_o.get(d) == "y" and settable(d) and d not in assign
            ]
            if not more or all(tried.get(w) == "y" for w in want):
                break
            assign = sorted(set(assign) | set(more))
        missing = [s for s in want if tried is None or tried.get(s) != "y"]
        verified = bool(assign) and tried is not None and not missing
        pin = ", ".join(f"CONFIG_{a}=y" for a in assign)
        for h in hazards_pending:
            b, why = h["backend"], h["why"]
            title = (
                f"Storage backend {b} is no longer enabled at {ctx.new_spec}"
                if h["lost"]
                else f"Storage backend {b}: "
                + "; ".join(
                    f'"{v["prompt"]}" {v["old"]} → {v["new"]}' for v in h["switched"].values()
                )
            )
            title += " (data written by deployed devices isn't read by the new firmware)"
            if why:
                title += ": " + "; ".join(c["cause"] for c in why[:3])
            findings.append(
                self._finding(
                    ctx,
                    "backend-disabled" if h["lost"] else "backend-switched",
                    b,
                    title,
                    Severity.BREAKING,
                    Confidence.HIGH,
                    f"Add {pin} to prj.conf: re-resolving the {ctx.new_spec} Kconfig with them "
                    "restores the source's storage backends (verified with kconfiglib, not built)."
                    if verified
                    else "Restore the source backend explicitly, or migrate the data first"
                    + (
                        f" ({pin} was tried and doesn't restore: {', '.join(missing)})"
                        if assign
                        else ""
                    )
                    + ".",
                    old={
                        "enabled": enabled["old"].get(b, []),
                        "choices": {k: v["old"] for k, v in h["switched"].items()},
                    },
                    new={
                        "enabled": enabled["new"].get(b, []),
                        "choices": {k: v["new"] for k, v in h["switched"].items()},
                        "causes": why,
                        "restore": {
                            "assign": [f"CONFIG_{a}=y" for a in assign],
                            "verified": verified,
                            "resolved": tried,
                        },
                    },
                    remedy=Remedy.CONFIG_PIN if verified else Remedy.UNKNOWN,
                    ev=[e for c in why for e in c["evidence"]][:8],
                )
            )
        for b in sorted(on_n - on_o):
            findings.append(
                self._finding(
                    ctx,
                    "backend-enabled",
                    b,
                    f"Storage backend {b} is newly enabled at {ctx.new_spec}"
                    + (
                        ": " + "; ".join(c["cause"] for c in causes(enabled["new"][b][0])[:2])
                        if enabled["new"][b]
                        else ""
                    ),
                    Severity.RISKY,
                    Confidence.MEDIUM,
                    "It will initialize its partition on first boot; check nothing else "
                    "stored data there.",
                    new={"enabled": enabled["new"][b]},
                )
            )
        checked.append(
            "value changes of backend symbols, with select/imply/depends causes at both refs"
        )

        # --- partitions ----------------------------------------------------------------------
        part_map = self._partitions(ctx, cfg, {"old": on_o, "new": on_n}, unknowns)
        prior = ctx.shared.get("findings", [])
        for b, labels in part_map.items():
            for f in prior:
                if f.category is Category.LAYOUT and f.subject in labels and f.remedy is not None:
                    linked.append(f.id)
            checked.append(f"{b} uses {', '.join(sorted(labels)) or '(none resolved)'}")

        # --- on-media definitions and implementation-only ---------------------------------------
        impl: list[str] = []
        fo, fn = _zephyr_files(app_o), _zephyr_files(app_n)
        for b in sorted(on_o & on_n):
            seeds = [
                Seed(s["kind"], s["name"], s.get("reason", "")) for s in cfg[b].get("seeds", [])
            ]
            diffs: list[SeedDiff] = [
                compare(
                    s,
                    src_o.locate(s, cfg[b]["trees"]),
                    src_n.locate(s, cfg[b]["trees"]),
                    ctx.old_spec,
                    ctx.new_spec,
                )
                for s in seeds
            ]
            # A seed absent at both refs has nothing to compare (e.g. settings' ZMS IDs
            # before ZMS existed); absent at one ref is a gap.
            gaps = [
                d
                for d in diffs
                if d.status == "not-found" and len(d.names[0].get("missing_at", [])) < 2
            ]
            if gaps:
                unknowns.append(
                    f"{b}: seeds not found at one ref: " + ", ".join(d.seed.label for d in gaps)
                )
            bad = [d for d in diffs if d.status in ("changed", "not-interpreted")]
            if bad:
                migr = self._migration(src_n, bad, cfg[b]["trees"])
                findings.append(
                    self._finding(
                        ctx,
                        "on-media-format-changed",
                        b,
                        f"{b} on-media definitions changed: "
                        + ", ".join(d.seed.label for d in bad)
                        + (
                            "; the target has a version-upgrade path "
                            "(one-way migration on first boot)"
                            if migr
                            else "; new code may not read old data"
                        ),
                        Severity.RISKY if migr else Severity.BREAKING,
                        Confidence.MEDIUM,
                        "Test reading data written by the source firmware; downgrades after "
                        "migration "
                        "won't read it."
                        if migr
                        else "Migrate the data (an intermediate image) or keep the old backend "
                        "version.",
                        new={"definitions": [d.to_json() for d in bad], "migration_evidence": migr},
                        remedy=None if migr else Remedy.INTERMEDIATE_HOP,
                        ev=[
                            file_line(
                                f"<zephyr {s['ref']}>/{s['file']}",
                                s["line"],
                                f"{e['name']} {s['value']}"[:150],
                            )
                            for d in bad
                            for e in d.names
                            if e["status"] != "unchanged"
                            for side in ("old", "new")
                            for s in e.get(side, [])[:1]
                        ][:10],
                    )
                )
            elif diffs:
                checked.append(f"{b}: on-media definitions unchanged ({len(diffs)} seeds)")
            if fo is not None and fn is not None:
                impl += [
                    f
                    for t in cfg[b]["trees"]
                    for f in sorted(fo.keys() & fn.keys())
                    if f.startswith(t + "/") and fo[f] != fn[f]
                ]
        if impl:
            confidence = "medium"
            findings.append(
                self._finding(
                    ctx,
                    "implementation-changed",
                    "storage",
                    f"{len(impl)} compiled storage backend files changed; their on-media "
                    "definitions didn't",
                    Severity.INFO,
                    Confidence.MEDIUM,
                    "Behavior can change without the declarations changing; named in the "
                    "verdict basis.",
                    new={"files": impl},
                )
            )

        hazards = [
            f.id for f in findings if f.remedy is not None and f.severity is not Severity.INFO
        ] + linked
        status: CategoryStatus = "hazard" if hazards else ("unknown" if unknowns else "clean")
        if unknowns:
            confidence = "low" if status != "hazard" else "medium"
        set_category(
            ctx.shared,
            "storage",
            status,
            confidence,
            checked,
            findings=hazards,
            reason="; ".join(unknowns) or None,
            extra={
                "backends": {"old": sorted(on_o), "new": sorted(on_n)},
                "partitions": {b: sorted(v) for b, v in part_map.items()},
                "implementation_only": impl,
                "linked_layout_findings": linked,
            },
        )
        return AnalyzerResult(findings, not_checked=nc)

    # ----------------------------------------------------------------------------------------

    def _finding(
        self,
        ctx: ScanContext,
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
    ) -> Finding:
        return Finding(
            category=Category.STORAGE,
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
            provenance=provenance(
                f"app .config and Kconfig at {ctx.old_spec} and {ctx.new_spec}; backend trees "
                "from zmig/data/hazard_seeds.yaml; compiled files from the app SBOM"
            ),
        )

    @staticmethod
    def _partitions(
        ctx: ScanContext,
        cfg: dict[str, Any],
        backends: dict[str, set[str]],
        unknowns: list[str],
    ) -> dict[str, set[str]]:
        """Partition labels each backend's compiled code references, at each ref for the
        backends enabled there. Libraries such as NVS take their flash area from a caller
        (settings), so only "no enabled backend references any partition" is unknown."""
        out: dict[str, set[str]] = {}
        for side, b_ctx in (("old", ctx.old), ("new", ctx.new)):
            if not backends[side]:
                continue
            app = b_ctx.app
            files = _zephyr_files(app)
            if files is None or not app.has(Artifact.EDT):
                unknowns.append(
                    f"partitions at {b_ctx.workspace.resolved.ref.label}: no SBOM or devicetree"
                )
                continue
            tree = edt(app, b_ctx.workspace, ctx.runner)
            parts = partitions(tree)
            by_path = {p: lb for lb, (p, _) in parts.items()}
            chosen = {
                k.replace(",", "_").replace("-", "_"): v for k, v in tree.get("chosen", {}).items()
            }
            names = set(parts) | set(chosen)
            src = Source(
                b_ctx.workspace.resolved.mirror.path,
                b_ctx.workspace.resolved.ref.sha,
                ctx.runner,
                bare=True,
            )
            any_found = False
            for b in sorted(backends[side]):
                found: set[str] = set()
                for t in cfg[b]["trees"]:
                    pat = r"\b(" + "|".join(re.escape(n) for n in sorted(names)) + r")\b"
                    for path, _, text in src.grep(pat, [t]):
                        if not (path.endswith(".h") or path in files):
                            continue
                        for n in re.findall(pat, text):
                            lb = by_path.get(chosen[n]) if n in chosen else n
                            if lb in parts:
                                found.add(lb)
                out.setdefault(b, set()).update(found)
                any_found = any_found or bool(found)
            if not any_found:
                unknowns.append(
                    "no enabled storage backend's compiled code references a partition at "
                    f"{b_ctx.workspace.resolved.ref.label}"
                )
        return out

    @staticmethod
    def _migration(src: Source, bad: list[SeedDiff], trees: list[str]) -> list[str]:
        """Comparisons of a changed *VERSION* define in the target tree (an upgrade path)."""
        out: list[str] = []
        for d in bad:
            for e in d.names:
                if "VERSION" not in e["name"] or e["status"] == "unchanged":
                    continue
                for path, line, text in src.grep(
                    rf"\b{e['name']}\b.*(==|!=|<|>)|(==|!=|<|>).*\b{e['name']}\b", trees
                ):
                    out.append(f"{path}:{line}: {text.strip()[:100]}")
        return out
