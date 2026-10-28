"""Bluetooth bonds and identity across the migration (docs/DESIGN-P1.md §7).

Active when the app enables a Bluetooth symbol (defined under subsys/bluetooth) that
depends on or selects SETTINGS: then bonds, identity and GATT state persist in settings.

From the compiled Bluetooth files of the app at each ref (SBOM), using the seeds in
zmig/data/hazard_seeds.yaml (`ble`):
  key names    settings handler paths (BT_SETTINGS_DEFINE / SETTINGS_STATIC_HANDLER_*;
               the BT_SETTINGS_DEFINE prefix read from its definition at each ref), keys
               passed to bt_settings_store/delete, and leaves the root "bt" handler
               compares against
  structs      srcdiff of the persisted key structs and their length define
  identity     resolved values of identity-affecting symbols

A key name gone at the target drops what was stored under it (breaking); a persisted
struct change may make stored bonds unreadable (breaking, medium); an identity option
change may change the identity peers see (risky).
"""

from __future__ import annotations

import re
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
    file_line,
)
from zmig.hazards import CategoryStatus, provenance, set_category
from zmig.sbom import compiled_sources, read_spdx_dir
from zmig.scan import ScanContext
from zmig.snapshots import config_values, kconfig_symbols
from zmig.srcdiff import Seed, Source, compare, load_seeds

_STR = re.compile(r'"([^"]*)"')


def _compiled(img: ImageContext) -> set[str] | None:
    if not img.has(Artifact.SPDX):
        return None
    z = compiled_sources(read_spdx_dir(img.artifact(Artifact.SPDX).parent)).get("zephyr", {})
    return {f.removeprefix("zephyr/") for f in z}


def _args(text: str) -> list[str]:
    """Top-level comma-separated arguments of the first parenthesized call in `text`."""
    start = text.find("(")
    out, depth, cur = [], 0, ""
    for ch in text[start + 1 :]:
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                out.append(cur.strip())
                return out
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return out


class BleAnalyzer:
    name: ClassVar[str] = "ble"
    kind: ClassVar[Literal["build"]] = "build"
    per_image: ClassVar[bool] = False
    requires: ClassVar[frozenset[Artifact]] = frozenset()

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        cfg = load_seeds()["ble"]
        app_o, app_n = ctx.old.app, ctx.new.app
        for img in (app_o, app_n):
            if not img.has(Artifact.CONFIG):
                reason = f"app image at {img.ref} has no .config ({img.status})"
                set_category(ctx.shared, "ble", "unknown", "low", [], reason=reason)
                return AnalyzerResult(not_checked=[NotChecked("BLE state", reason, self.name)])
        ks_o = kconfig_symbols(app_o, ctx.old.workspace, ctx.runner)
        ks_n = kconfig_symbols(app_n, ctx.new.workspace, ctx.runner)
        cv_o, cv_n = config_values(app_o), config_values(app_n)
        act_o = self._persisting(ks_o, cv_o, cfg["trees"])
        act_n = self._persisting(ks_n, cv_n, cfg["trees"])
        if not act_o:
            set_category(
                ctx.shared,
                "ble",
                "not-applicable",
                "high",
                [f"no Bluetooth symbol that uses SETTINGS is enabled at {ctx.old_spec}"]
                + (
                    [f"(enabled at {ctx.new_spec}: {', '.join(act_n)}; nothing deployed to keep)"]
                    if act_n
                    else []
                ),
            )
            return AnalyzerResult()
        findings: list[Finding] = []
        unknowns: list[str] = []
        checked = [f"persisting Bluetooth symbols at {ctx.old_spec}: {', '.join(act_o)}"]

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
        ) -> None:
            findings.append(
                Finding(
                    category=Category.BLE,
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
                        f"compiled Bluetooth files (app SBOM) and Kconfig at {ctx.old_spec} and "
                        f"{ctx.new_spec}; seeds from zmig/data/hazard_seeds.yaml"
                    ),
                )
            )

        if not act_n:
            add(
                "ble-persistence-disabled",
                "BT_SETTINGS",
                f"Bluetooth state no longer persists at {ctx.new_spec} (was: {', '.join(act_o)})",
                Severity.BREAKING,
                Confidence.HIGH,
                "Bonds and identity stored by deployed devices won't be loaded; enable "
                "the persisting options again.",
                old=act_o,
                new=[],
                remedy=Remedy.CONFIG_PIN
                if all((ks_n.get(s) or {}).get("prompt") for s in act_o)
                else Remedy.UNKNOWN,
            )

        # --- key names ----------------------------------------------------------------------
        keys: dict[str, dict[str, dict[str, Any]]] = {}
        for side, b in (("old", ctx.old), ("new", ctx.new)):
            got = self._keys(ctx, b, cfg)
            if got is None:
                unknowns.append(
                    f"key names at {b.workspace.resolved.ref.label}: no SBOM or seeds missing"
                )
            else:
                keys[side] = got
        if len(keys) == 2:
            gone = sorted(keys["old"].keys() - keys["new"].keys())
            added = sorted(keys["new"].keys() - keys["old"].keys())
            checked.append(
                f"{len(keys['old'])} settings key names at {ctx.old_spec}, "
                f"{len(keys['new'])} at {ctx.new_spec}"
            )
            if gone:
                add(
                    "ble-key-removed",
                    "settings keys",
                    f"Bluetooth settings keys not handled at {ctx.new_spec}: {', '.join(gone)}",
                    Severity.BREAKING,
                    Confidence.MEDIUM,
                    "State stored under these keys (bonds, identity, GATT) is not loaded by the "
                    "new firmware; find where it moved in the target source.",
                    old=gone,
                    new=added,
                    remedy=Remedy.UNKNOWN,
                    ev=[keys["old"][k]["evidence"] for k in gone][:8],
                )
            elif added:
                add(
                    "ble-key-added",
                    "settings keys",
                    f"New Bluetooth settings keys at {ctx.new_spec}: {', '.join(added)}",
                    Severity.INFO,
                    Confidence.HIGH,
                    "Nothing stored under them yet on deployed devices.",
                    new=added,
                    ev=[keys["new"][k]["evidence"] for k in added][:6],
                )

        # --- persisted structs ---------------------------------------------------------------
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
        paths = cfg["trees"] + ["include/zephyr/bluetooth"]
        diffs = [
            compare(
                Seed(s["kind"], s["name"], s["reason"]),
                src_o.locate(Seed(s["kind"], s["name"], ""), paths),
                src_n.locate(Seed(s["kind"], s["name"], ""), paths),
                ctx.old_spec,
                ctx.new_spec,
            )
            for s in cfg["seeds"]
        ]
        missing = [d.seed.label for d in diffs if d.status == "not-found"]
        if missing:
            unknowns.append(f"persisted structs not found: {', '.join(missing)}")
        bad = [d for d in diffs if d.status in ("changed", "not-interpreted")]
        checked.append(
            f"persisted definitions: {', '.join(f'{d.seed.label}={d.status}' for d in diffs)}"
        )
        if bad:
            add(
                "ble-struct-changed",
                "persisted structs",
                "Persisted Bluetooth definitions changed: " + ", ".join(d.seed.label for d in bad),
                Severity.BREAKING,
                Confidence.MEDIUM,
                "Bonds stored with the old layout may not load; test re-loading bonds made "
                "by the source firmware, or plan for re-pairing.",
                new=[d.to_json() for d in bad],
                remedy=Remedy.UNKNOWN,
                ev=[
                    file_line(f"<zephyr {s['ref']}>/{s['file']}", s["line"], e["name"])
                    for d in bad
                    for e in d.names
                    for side in ("old", "new")
                    for s in e.get(side, [])[:1]
                ][:8],
            )

        # --- identity options ------------------------------------------------------------------
        changed = []
        for s in cfg["identity_symbols"]:
            n = s["name"]
            if n not in ks_o and n not in ks_n:
                unknowns.append(f"identity symbol {n} not defined at either ref")
                continue
            if cv_o.get(n) != cv_n.get(n):
                changed.append(
                    {
                        "symbol": f"CONFIG_{n}",
                        "old": cv_o.get(n),
                        "new": cv_n.get(n),
                        "reason": s["reason"],
                    }
                )
        if changed:
            settable = all((ks_n.get(c["symbol"][7:]) or {}).get("prompt") for c in changed)
            add(
                "ble-identity-option-changed",
                "identity",
                "Identity-affecting Bluetooth options changed: "
                + ", ".join(
                    f"{c['symbol']} {c['old'] or 'unset'} → {c['new'] or 'unset'}" for c in changed
                ),
                Severity.RISKY,
                Confidence.MEDIUM,
                "Peers may see a different identity or lose bonds; pin the source values.",
                old=changed,
                remedy=Remedy.CONFIG_PIN if settable else Remedy.UNKNOWN,
            )
        checked.append(f"{len(cfg['identity_symbols'])} identity options compared")

        if len(keys) == 2:
            add(
                "ble-persisted-state",
                "settings keys",
                f"Bluetooth state persisted under {len(keys['new'])} settings keys at "
                f"{ctx.new_spec} ({len(keys['old'])} at {ctx.old_spec})",
                Severity.INFO,
                Confidence.MEDIUM,
                "For reference: what deployed devices have stored and the new firmware reads.",
                old={k: v["via"] for k, v in sorted(keys["old"].items())},
                new={k: v["via"] for k, v in sorted(keys["new"].items())},
            )
        hz = [f.id for f in findings if f.remedy is not None and f.severity is not Severity.INFO]
        status: CategoryStatus = "hazard" if hz else ("unknown" if unknowns else "clean")
        set_category(
            ctx.shared,
            "ble",
            status,
            "medium" if status == "clean" else ("low" if status == "unknown" else "high"),
            checked,
            findings=hz,
            reason="; ".join(unknowns) or None,
            extra={"keys": {k: sorted(v) for k, v in keys.items()}},
        )
        return AnalyzerResult(findings)

    @staticmethod
    def _persisting(
        ks: dict[str, dict[str, Any]], cv: dict[str, str], trees: list[str]
    ) -> list[str]:
        out = []
        for name, info in ks.items():
            if cv.get(name) != "y":
                continue
            if not any(loc.startswith(t + "/") for loc in info.get("locations", []) for t in trees):
                continue
            if "SETTINGS" in info.get("selects", []) or re.search(
                r"\bSETTINGS\b", info.get("depends", "")
            ):
                out.append(name)
        return sorted(out)

    @staticmethod
    def _keys(
        ctx: ScanContext, b: BuildContext, cfg: dict[str, Any]
    ) -> dict[str, dict[str, Any]] | None:
        files = _compiled(b.app)
        if files is None:
            return None
        ref = b.workspace.resolved.ref.label
        src = Source(
            b.workspace.resolved.mirror.path, b.workspace.resolved.ref.sha, ctx.runner, bare=True
        )
        out: dict[str, dict[str, Any]] = {}
        macros = cfg["handler_macros"]
        # Prefixes from macro definitions at this ref, e.g. `"bt/" _subtree`.
        prefixes: dict[str, str] = {}
        for m, spec in macros.items():
            if "prefix_from" in spec:
                d = src.grep(rf"^\s*#\s*define\s+{spec['prefix_from']}\(", cfg["trees"])
                if not d:
                    # Not defined at this ref (e.g. v3.2): nothing registers through it.
                    continue
                path, line, _ = d[0]
                body = " ".join((src.show(path) or [])[line - 1 : line + 4])
                lit = _STR.findall(body)
                prefixes[m] = lit[0] if lit else ""
        names = "|".join(sorted(macros, key=len, reverse=True))
        for path, line, text in src.grep(rf"\b({names})\(", cfg["trees"]):
            if path not in files or "#define" in text:
                continue
            lines = src.show(path) or []
            call = " ".join(x.strip() for x in lines[line - 1 : line + 3])
            m = re.search(rf"\b({names})\(", call)
            if m is None:
                continue
            args = _args(call[m.start() :])
            spec = macros[m[1]]
            if len(args) <= spec["path_arg"]:
                continue
            lit = _STR.findall(args[spec["path_arg"]])
            if not lit:
                continue
            key = prefixes.get(m[1], "") + "".join(lit)
            out.setdefault(
                key,
                {
                    "via": "handler",
                    "evidence": file_line(f"<zephyr {ref}>/{path}", line, text.strip()[:120]),
                },
            )
        for fn, spec in cfg["store_functions"].items():
            if not src.grep(rf"^\w.*\b{fn}\(", cfg["trees"]):
                # Not at this ref (it appeared after v3.5): keys come from the other sources.
                continue
            for path, line, text in src.grep(rf"\b{fn}\(\s*\"", cfg["trees"]):
                if path not in files:
                    continue
                lit = _STR.findall(text)
                if lit:
                    out.setdefault(
                        spec["prefix"] + lit[0],
                        {
                            "via": "store",
                            "evidence": file_line(
                                f"<zephyr {ref}>/{path}", line, text.strip()[:120]
                            ),
                        },
                    )
        root = cfg["root_handler_file"]
        # The prefix of the root handler's leaves: the path it registers in that file.
        root_keys = [
            k
            for k, v in out.items()
            if v["via"] == "handler" and str(v["evidence"].get("path", "")).endswith("/" + root)
        ]
        if root in files and root_keys:
            for path, line, text in src.grep(r"strn?cmp\(\s*name\s*,\s*\"", [root]):
                lit = _STR.findall(text)
                if lit:
                    out.setdefault(
                        f"{root_keys[0]}/{lit[0]}",
                        {
                            "via": "root-leaf",
                            "evidence": file_line(
                                f"<zephyr {ref}>/{path}", line, text.strip()[:120]
                            ),
                        },
                    )
        return out
