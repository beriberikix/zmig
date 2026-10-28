"""Public API changes the project's compiled code depends on (docs/DESIGN-P2.md).

Step 1 (§5.1): the project's own system calls. Zephyr turns each `__syscall` declaration
it scans into generated wrappers (`syscalls/<header>.h`) and lists it in
`zephyr/misc/generated/syscalls.json`. A project `__syscall` that the source build
generated and the target build doesn't is a break: every file including that header fails
on the missing `syscalls/<header>.h` (ZMK v3.5, DOGFOOD-1 #5). The remedy comes from what the
target tree provides: `zephyr_syscall_header()` (cmake/modules/extensions.cmake) and/or
`CONFIG_APPLICATION_DEFINED_SYSCALL`.

Project headers come from the source build's dependency records (ninja), so this works
when the target build fails.

Step 2 (§3, §4, §10): with libclang (`zmig[api]`), every project translation unit of the
source build is parsed with the source build's flags and with the target build's flags.
Functions and typedefs the project references that were removed or changed, newly
deprecated functions, and callbacks the project passes or assigns whose expected type
changed (clang's incompatible-pointer diagnostics that appear only at the target) are
reported at the project's lines. Parse gaps are "not interpreted", never clean.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, ClassVar, Literal

from zmig.abitools import (
    abidiff_changes,
    binary_boundaries,
    pahole_layouts,
    undefined_symbols,
)
from zmig.analyzers import AnalyzerResult
from zmig.apidiff import API_VERSION, TuResult, libclang_version
from zmig.apidiff import parse as parse_tu
from zmig.buildctx import Artifact, ImageContext, project_path, project_roots
from zmig.errors import ZmigError
from zmig.findings import (
    JSON,
    Category,
    Checked,
    Confidence,
    Finding,
    NotChecked,
    Severity,
    file_line,
    tool_output,
)
from zmig.scan import ScanContext
from zmig.snapshots import _cached, build_dependencies

_EXPECTED = re.compile(r"(?:assigning to|initializing|passing|returning) '([^']+)'")
SYSCALLS_JSON = "zephyr/misc/generated/syscalls.json"
_SYSCALL_DECL = re.compile(r"^\s*__syscall\b[^;(]*?\b(?P<name>\w+)\s*\(", re.M)


def syscall_names(img: ImageContext) -> set[str] | None:
    """Functions a build turned into system calls (syscalls.json: [[ret+name, args], header,
    (is_syscall)]; the is_syscall flag appeared after v3.5)."""
    path = img.dir / SYSCALLS_JSON
    if not path.is_file():
        return None
    data: Any = json.loads(path.read_text())
    entries = data if isinstance(data, list) else list(data.values())
    out = set()
    for e in entries:
        if len(e) > 2 and not e[2]:
            continue  # a __subsystem/__syscall_always_inline marker, not a syscall
        out.add(str(e[0][0]).split()[-1].lstrip("*"))
    return out


def project_syscalls(ctx: ScanContext) -> dict[str, list[tuple[str, int, str]]]:
    """{project header (as shown): [(function, line, text)]} for every header the source
    build's compiled project files included that declares `__syscall`."""
    deps = build_dependencies(ctx.old_img, ctx.runner)
    if deps is None:
        return {}
    roots = project_roots(ctx.old, ctx.old_img.dir)
    out: dict[str, list[tuple[str, int, str]]] = {}
    for f in sorted({f for files in deps.values() for f in files if f.endswith(".h")}):
        rel = project_path(f, roots)
        if rel is None or rel in out:
            continue
        text = Path(f).read_text(errors="replace") if Path(f).is_file() else ""
        decls = [
            (m["name"], text.count("\n", 0, m.start()) + 1, m[0].strip())
            for m in _SYSCALL_DECL.finditer(text)
        ]
        if decls:
            out[rel] = decls
    return out


class ApiAnalyzer:
    name: ClassVar[str] = "api"
    kind: ClassVar[Literal["build"]] = "build"
    requires: ClassVar[frozenset[Artifact]] = frozenset()

    def analyze(self, ctx: ScanContext) -> AnalyzerResult:
        findings: list[Finding] = []
        not_checked: list[NotChecked] = []
        checked: list[Checked] = []
        old, new = syscall_names(ctx.old_img), syscall_names(ctx.new_img)
        if old is None or new is None:
            side = "source" if old is None else "target"
            not_checked.append(
                NotChecked(
                    "the project's own system calls",
                    f"no {SYSCALLS_JSON} in the {side} build (it stopped before generating it)",
                    self.name,
                )
            )
        else:
            mine = project_syscalls(ctx)
            for header, decls in mine.items():
                lost = [d for d in decls if d[0] in old and d[0] not in new]
                if lost:
                    findings.append(self._lost_syscalls(ctx, header, lost))
            checked.append(
                Checked(
                    "the project's own system calls",
                    "found" if findings else "clean",
                    f"{sum(len(d) for d in mine.values())} __syscall declaration(s) in "
                    f"{len(mine)} project header(s) compared with both builds' syscalls.json",
                    self.name,
                )
            )
        version = libclang_version()
        if version is None:
            not_checked.append(
                NotChecked(
                    "declarations the project uses (functions, typedefs, callbacks)",
                    "libclang isn't installed: pip install 'zmig[api]'",
                    self.name,
                )
            )
            return AnalyzerResult(
                findings,
                status="partial",
                reason="libclang isn't installed",
                not_checked=not_checked,
                checked=checked,
            )
        ctx.shared.setdefault("environment", {})["libclang"] = version
        src, tgt, gaps = self._parse_both(ctx, version)
        not_checked += gaps
        errs = [sum(1 for t in tus if t["errors"]) for tus in (src, tgt)]
        checked.append(
            Checked(
                "libclang parse coverage",
                "partial" if gaps else "clean",
                f"{version}: {len(src)} project TU(s) parsed with the source flags "
                f"({errs[0]} with errors), {len(tgt)} with the target flags ({errs[1]} with "
                f"errors, {len(src) - len(tgt)} without a target compile command)",
                self.name,
            )
        )
        decl_findings = self._declaration_changes(ctx, src, tgt)
        findings += decl_findings
        used = {r["name"] for t in src for r in t["refs"]}
        checked.append(
            Checked(
                "declarations the project uses",
                "found" if decl_findings else "clean",
                f"{len(used)} Zephyr/module declaration(s) the project's TUs use, compared at "
                f"both refs",
                self.name,
            )
        )
        b_findings, b_gaps, b_checked = self._binary_checks(ctx, src, tgt)
        findings += b_findings
        not_checked += b_gaps
        checked += b_checked
        return AnalyzerResult(
            findings,
            status="partial" if gaps else "ok",
            reason=f"{len(gaps)} input(s) not interpreted (see Not checked)" if gaps else None,
            not_checked=not_checked,
            checked=checked,
        )

    def _parse_both(
        self, ctx: ScanContext, version: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[NotChecked]]:
        """Parses of the source build's project TUs: with the source flags, and with the
        target flags for the same project file. Cached per build image."""
        old_roots = project_roots(ctx.old, ctx.old_img.dir)
        new_roots = project_roots(ctx.new, ctx.new_img.dir)
        gaps: list[NotChecked] = []

        def entries(img: ImageContext, roots: dict[str, str]) -> dict[str, dict[str, Any]]:
            if not img.has(Artifact.COMPILE_COMMANDS):
                return {}
            cc = json.loads(img.artifact(Artifact.COMPILE_COMMANDS).read_text())
            out = {}
            for e in cc:
                rel = project_path(e["file"], roots)
                if rel and rel.endswith((".c", ".cpp", ".cc")):
                    out[rel] = e
            return out

        src_entries = entries(ctx.old_img, old_roots)
        tgt_entries = entries(ctx.new_img, new_roots)

        def run(img: ImageContext, b: Any, roots: dict[str, str], todo: dict[str, Any]) -> Any:
            def produce(out: Path) -> None:
                results = []
                values: dict[str, Any] = {}
                for rel, e in sorted(todo.items()):
                    r: TuResult = parse_tu(
                        e["file"],
                        e["command"],
                        roots,
                        lambda f: project_path(f, roots),
                        str(b.workspace.root),
                        str(b.dir),
                        values,
                    )
                    results.append(
                        {
                            "file": rel,
                            "decls": r.decls,
                            "refs": r.refs,
                            "diags": r.diags,
                            "errors": r.errors,
                            "dropped": r.dropped,
                        }
                    )
                out.write_text(json.dumps({"tus": results}))

            return _cached(img, f"api{API_VERSION}-{version.split()[-1]}", produce)["tus"]

        src = run(ctx.old_img, ctx.old, old_roots, src_entries)
        todo = {rel: tgt_entries[rel] for rel in src_entries if rel in tgt_entries}
        tgt = run(ctx.new_img, ctx.new, new_roots, todo) if todo else []
        for rel in sorted(set(src_entries) - set(todo)):
            gaps.append(
                NotChecked(
                    f"API use in {rel} at {ctx.new_spec}",
                    "the target build has no compile command for it (not configured, or "
                    "no longer compiled), so its declarations at the target are unknown",
                    self.name,
                )
            )
        for side, tus in (("source", src), ("target", tgt)):
            for t in tus:
                if t["errors"]:
                    gaps.append(
                        NotChecked(
                            f"API use in {t['file']} ({side} parse)",
                            f"{len(t['errors'])} parse error(s); first: {t['errors'][0]}",
                            self.name,
                        )
                    )
        return src, tgt, gaps

    def _declaration_changes(
        self, ctx: ScanContext, src: list[dict[str, Any]], tgt: list[dict[str, Any]]
    ) -> list[Finding]:
        old_decls: dict[str, dict[str, Any]] = {}
        new_decls: dict[str, dict[str, Any]] = {}
        for t in src:
            for k, v in t["decls"].items():
                old_decls.setdefault(k, v)
        for t in tgt:
            for k, v in t["decls"].items():
                new_decls.setdefault(k, v)
        parsed_at_target = {t["file"] for t in tgt}
        uses: dict[str, list[str]] = {}
        value_uses: dict[str, set[str]] = {}
        for t in src:
            for r in t["refs"]:
                uses.setdefault(r["name"], []).append(r["at"])
                value_uses.setdefault(r["name"], set()).add(r.get("use", "unknown"))
        old_diag_sites = {(d["at"], d.get("kind")) for t in src for d in t["diags"]}
        new_diags = [
            d for t in tgt for d in t["diags"] if (d["at"], d.get("kind")) not in old_diag_sites
        ]
        callback_diags = [d for d in new_diags if d.get("kind", "callback") == "callback"]
        truncations = [d for d in new_diags if d.get("kind") == "truncation"]

        findings: list[Finding] = []

        def add(kind: str, name: str, title: str, sev: Severity, action: str,
                sites: list[str], old: Any, new: Any, decl_ev: list[JSON]) -> None:  # fmt: skip
            ev: list[JSON] = []
            for site in sorted(set(sites), key=_site_key):
                path, _, line = site.rpartition(":")
                ev.append(file_line(path, int(line), name))
            findings.append(
                Finding(
                    category=Category.API,
                    kind=kind,
                    subject=name,
                    title=title,
                    severity=sev,
                    confidence=Confidence.HIGH,
                    action=action,
                    analyzer=self.name,
                    scope=ctx.scope,
                    evidence=(*ev, *decl_ev),
                    old=old,
                    new=new,
                )
            )

        def shown(d: dict[str, Any]) -> str:
            if d["kind"] == "macro":
                if d.get("function_like"):
                    return f"function-like macro, {d.get('arity')} parameter(s)"
                if "vtype" in d:
                    val = f" = {d['value']}" if d.get("value") is not None else ""
                    return f"({d['vtype']}, {d.get('vsize')} bytes){val}"
                return str(d.get("tokens", ""))[:120]
            if d["kind"] == "enum":
                return f"= {d.get('value')}"
            if d["kind"] == "field":
                return f"{d['type']} at bit offset {d.get('offset')}"
            return str(d.get("type", ""))

        label = {"function": "Function", "typedef": "Typedef", "macro": "Macro",
                 "enum": "Enum constant", "field": "Field"}  # fmt: skip
        explained: set[str] = set()
        typedef_changes = {
            (old_decls[k]["type"], new_decls[k]["type"])
            for k in uses
            if k in old_decls
            and k in new_decls
            and old_decls[k]["kind"] == "typedef"
            and old_decls[k]["type"] != new_decls[k]["type"]
        }
        for name in sorted(uses):
            o = old_decls.get(name)
            if o is None:
                continue
            sites = uses[name]
            # Only claim a removal if a target parse covered a file that uses it.
            covered = any(s.rpartition(":")[0] in parsed_at_target for s in sites)
            n = new_decls.get(name)
            what = o["kind"]
            what_l = label.get(what, what)
            decl_old = tool_output("declaration at the source", f"{o['where']}: {shown(o)}")
            if n is None and what == "macro":
                # A macro whose new form the target parse couldn't expand (its arity
                # changed) is never instantiated there: read its definition instead.
                n = _macro_at(ctx.new.workspace, o["where"], name)
            if n is None:
                if not covered:
                    continue
                macro = (
                    _macro_definition(ctx.new.workspace.zephyr, name)
                    if what == "function"
                    else None
                )
                if macro:
                    # Calls still compile through a function-like macro of the same name
                    # (regression: k_ms_to_ticks_ceil64 became a macro in v3.5, used by ZMK
                    # only through K_MSEC); taking its address doesn't.
                    add(f"{what}-became-macro", name,
                        f"{what_l} {name}, which your code uses, is a macro in "
                        f"{ctx.new_spec}", Severity.INFO,
                        "Calls keep working; code that takes its address or #undefs it "
                        "doesn't.", sites, o.get("type"), macro,
                        [decl_old, tool_output("macro at the target", macro)])  # fmt: skip
                    continue
                add(f"{what}-removed", name,
                    f"{what_l} {name}, which your code uses, no longer exists in "
                    f"{ctx.new_spec}", Severity.BREAKING,
                    "Find its replacement in the migration guides.", sites, shown(o), None,
                    [decl_old])  # fmt: skip
                continue
            decl_new = tool_output("declaration at the target", f"{n['where']}: {shown(n)}")
            if what == "field" and (o.get("type"), n.get("type")) in typedef_changes:
                continue  # the same change as a typedef reported for these lines
            change = self._change(
                name, what, o, n, sites, callback_diags, truncations, explained,
                value_uses.get(name, {"unknown"}),
            )  # fmt: skip
            if change is not None:
                sev, why, extra_sites = change
                add(f"{what}-changed", name,
                    f"{what_l} {name}, which your code uses, changed: {shown(o)} → {shown(n)}",
                    sev, why, sites + extra_sites, shown(o), shown(n),
                    [decl_old, decl_new])  # fmt: skip
            elif n.get("deprecated") and not o.get("deprecated"):
                add(f"{what}-deprecated", name,
                    f"{what_l} {name}, which your code uses, is deprecated in {ctx.new_spec}",
                    Severity.RISKY, "Move off it before the release that removes it.", sites,
                    None, None, [decl_new])  # fmt: skip

        # New diagnostics no declaration change above explains: callbacks the project
        # provides whose expected type changed where the slot isn't a reported typedef (a
        # struct field, a parameter), and values that no longer fit.
        groups: dict[tuple[str, str], list[str]] = {}
        for d in new_diags:
            if d["at"] in explained:
                continue
            if d.get("kind") == "devicetree":
                groups.setdefault(("devicetree", str(d.get("ord"))), []).append(d["at"])
                continue
            if d.get("kind", "callback") == "callback":
                m = _EXPECTED.search(d["message"])
                groups.setdefault(("callback", m[1] if m else d["message"]), []).append(d["at"])
            else:
                groups.setdefault(("truncation", d["message"]), []).append(d["at"])
        for (kind, key), sites in sorted(groups.items()):
            if kind == "devicetree":
                findings.append(self._missing_device(ctx, int(key), sites))
                continue
            if kind == "callback":
                msg = next(d["message"] for d in callback_diags if d["at"] == sites[0])
                add("callback-type-changed", key,
                    f"Callbacks your code provides no longer match the type Zephyr expects at "
                    f"{ctx.new_spec}: {key}", Severity.BREAKING,
                    f"Change their signatures to match ({msg[:200]}).", sites, None,
                    {"expected": key, "message": msg}, [])  # fmt: skip
            else:
                add("value-truncated", key[:80],
                    f"A value your code stores no longer fits at {ctx.new_spec}: {key[:160]}",
                    Severity.RISKY,
                    "Widen the variable or parameter it's stored in.", sites, None,
                    {"message": key}, [])  # fmt: skip
        return findings

    def _binary_checks(
        self, ctx: ScanContext, src: list[dict[str, Any]], tgt: list[dict[str, Any]]
    ) -> tuple[list[Finding], list[NotChecked], list[Checked]]:
        """§6: pahole cross-checks clang's layout of the structs the project accesses
        against the GCC-built ELFs; abidiff compares what a binary boundary (a prebuilt
        static library the project links, LLEXT) sees. Missing tools are not-checked."""
        findings: list[Finding] = []
        gaps: list[NotChecked] = []
        checked: list[Checked] = []
        for side, img, tus in (("source", ctx.old_img, src), ("target", ctx.new_img, tgt)):
            fields = {
                k: v
                for t in tus
                for k, v in t["decls"].items()
                if v["kind"] == "field" and k.count(".") == 1 and not k.startswith("<")
            }
            if not fields:
                continue
            if not img.has(Artifact.ELF):
                gaps.append(
                    NotChecked(
                        f"struct layouts at the {side} (pahole)",
                        f"no ELF at the {side}: {img.missing_reason(Artifact.ELF)}",
                        self.name,
                    )
                )
                continue
            layouts = pahole_layouts(img.artifact(Artifact.ELF), [k.split(".")[0] for k in fields])
            if layouts is None:
                gaps.append(
                    NotChecked(
                        "struct layouts against the toolchain's DWARF (pahole)",
                        "pahole isn't installed: struct layouts come from clang's model only",
                        self.name,
                    )
                )
                break
            differ = []
            for k, v in sorted(fields.items()):
                struct, member = k.split(".")
                elf_bit = layouts.get(struct, {}).get(member)
                if elf_bit is not None and v.get("offset", -1) >= 0 and elf_bit != v["offset"]:
                    differ.append(f"{k} (clang bit {v['offset']}, ELF bit {elf_bit})")
            compared = sum(
                1
                for k in fields
                if layouts.get(k.split(".")[0], {}).get(k.split(".")[1]) is not None
            )
            checked.append(
                Checked(
                    f"struct layouts at the {side} (pahole)",
                    "partial" if differ else "clean",
                    f"{compared} of {len(fields)} field offset(s) clang modelled checked "
                    f"against the GCC-built ELF; {len(differ)} disagree",
                    self.name,
                )
            )
            if differ:
                gaps.append(
                    NotChecked(
                        f"struct layouts at the {side} (clang vs. the GCC-built ELF)",
                        "clang's layout model disagrees with pahole for "
                        + ", ".join(differ[:5])
                        + (", ..." if len(differ) > 5 else "")
                        + "; offset findings for these fields aren't reliable",
                        self.name,
                    )
                )
        roots = project_roots(ctx.old, ctx.old_img.dir)
        bounds = binary_boundaries(ctx.old_img.dir, list(roots))
        if not bounds:
            checked.append(
                Checked(
                    "binary boundaries (abidiff)",
                    "clean",
                    "no prebuilt static library from the project tree on the link line, and "
                    "CONFIG_LLEXT isn't set: no binary ABI to compare",
                    self.name,
                )
            )
        for b in bounds:
            if b == "llext":
                gaps.append(
                    NotChecked(
                        "ABI seen by LLEXT extensions",
                        "CONFIG_LLEXT is set, but the extensions aren't part of this build, so "
                        "the symbols they import are unknown",
                        self.name,
                    )
                )
                continue
            rel = project_path(b, roots) or b
            if not (ctx.old_img.has(Artifact.ELF) and ctx.new_img.has(Artifact.ELF)):
                gaps.append(NotChecked(f"ABI seen by {rel}", "needs both builds' ELFs", self.name))
                continue
            changes = abidiff_changes(
                ctx.old_img.artifact(Artifact.ELF),
                ctx.new_img.artifact(Artifact.ELF),
                undefined_symbols(Path(b)),
            )
            if changes is None:
                gaps.append(
                    NotChecked(
                        f"ABI seen by {rel}",
                        "abidiff isn't installed (abigail-tools)",
                        self.name,
                    )
                )
            elif not changes:
                checked.append(
                    Checked(f"ABI seen by {rel} (abidiff)", "clean",
                            "abidiff reports no change to the symbols it imports", self.name)
                )  # fmt: skip
            else:
                checked.append(
                    Checked(f"ABI seen by {rel} (abidiff)", "found",
                            f"{len(changes)} change(s) to symbols it imports", self.name)
                )  # fmt: skip
                findings.append(
                    Finding(
                        category=Category.API,
                        kind="abi-changed",
                        subject=rel,
                        title=f"{rel}, a prebuilt library your build links, uses Zephyr "
                        f"symbols whose ABI changed in {ctx.new_spec}",
                        severity=Severity.RISKY,
                        confidence=Confidence.MEDIUM,
                        action="Rebuild the library against the new version, or check that "
                        "the changes listed don't affect it.",
                        analyzer=self.name,
                        scope=ctx.scope,
                        evidence=(
                            file_line(rel, 1, "prebuilt library linked into the image"),
                            *(tool_output("abidiff", c) for c in changes[:10]),
                        ),
                        new=changes,
                    )
                )
        return findings, gaps, checked

    def _missing_device(self, ctx: ScanContext, ord_: int, sites: list[str]) -> Finding:
        """A devicetree device the project takes (DEVICE_DT_GET, DT_CHOSEN, ...) that the
        target defines no device for: the node, its status at both refs, and the chosen
        property naming it (regression: bms-firmware native_sim, ThingSet's serial.c on a
        uart1 that v4.2 leaves disabled, docs/DOGFOOD-3-baseline.md B5)."""
        try:
            new_nodes = ctx.new_edt["nodes"]
            old_nodes = ctx.old_edt["nodes"]
            chosen = ctx.new_edt.get("chosen", {})
        except ZmigError:  # no devicetree snapshot: report what clang said
            new_nodes, old_nodes, chosen = {}, {}, {}
        path = next((p for p, n in new_nodes.items() if n.get("ord") == ord_), None)
        status_new = new_nodes.get(path, {}).get("status") if path else None
        status_old = old_nodes.get(path, {}).get("status") if path else None
        names = sorted(k for k, v in chosen.items() if v == path)
        what = (
            f"{path}" + (f" (chosen {', '.join(names)})" if names else "")
            if path
            else (f"devicetree ordinal {ord_}")
        )
        ev: list[JSON] = []
        for site in sorted(set(sites), key=_site_key):
            p, _, line = site.rpartition(":")
            ev.append(file_line(p, int(line), f"__device_dts_ord_{ord_}"))
        if path:
            ev.append(
                tool_output(
                    "devicetree",
                    f"{path}: status {status_old!r} at {ctx.old_spec}, {status_new!r} at "
                    f"{ctx.new_spec}",
                )
            )
        return Finding(
            category=Category.API,
            kind="device-missing",
            subject=path or f"ord {ord_}",
            title=f"Your code takes the device for {what}, which has no device at "
            f"{ctx.new_spec}" + (f" (status {status_new!r}, was {status_old!r})" if path else ""),
            severity=Severity.BREAKING,
            confidence=Confidence.HIGH,
            action='Enable the node in an overlay (status = "okay") or point the code (or '
            "the chosen property) at the node that replaced it.",
            analyzer=self.name,
            scope=ctx.scope,
            evidence=tuple(ev),
            new={"node": path, "status": status_new, "chosen": names},
            old={"status": status_old},
        )

    @staticmethod
    def _change(
        name: str,
        what: str,
        o: dict[str, Any],
        n: dict[str, Any],
        sites: list[str],
        callback_diags: list[dict[str, Any]],
        truncations: list[dict[str, Any]],
        explained: set[str],
        value_use: frozenset[str] | set[str] = frozenset({"unknown"}),
    ) -> tuple[Severity, str, list[str]] | None:
        """(severity, action, extra sites) when the declaration changed in a way §10.3
        reports; None otherwise."""
        at = set(sites)
        if what == "macro":
            if o.get("function_like") != n.get("function_like"):
                return Severity.RISKY, "It changed between object-like and function-like.", []
            if o.get("function_like"):
                if o.get("arity") != n.get("arity"):
                    return Severity.BREAKING, "Update every use to the new parameters.", []
                return None  # a body change alone says nothing (§4)
            if "vtype" not in o or "vtype" not in n:
                return None  # not an expression at one of the refs: not compared
            width = o.get("vsize") != n.get("vsize")
            value = o.get("value") != n.get("value")
            if not width and not value:
                return None
            cut = [d["at"] for d in truncations if d["at"] in at]
            explained.update(cut)
            if width and cut:
                return (
                    Severity.BREAKING,
                    "Its type got wider and your code stores it in a narrower type, which "
                    "changes the value (see the lines listed); widen those.",
                    [],
                )
            if value_use <= {"other"}:
                return (
                    Severity.INFO,
                    "Your code never stores, passes, compares or narrows it (only discards "
                    "it or takes its size), so the new value can't change behavior.",
                    [],
                )
            return (
                Severity.RISKY,
                "Check how your code uses the value: "
                + ", ".join(sorted(value_use))
                + " (comparisons, storage, the wire).",
                [],
            )
        if what == "enum":
            if o.get("value") == n.get("value"):
                return None
            if value_use <= {"other"}:
                return (
                    Severity.INFO,
                    "Your code never stores, passes, compares or narrows it, so the new "
                    "value can't change behavior.",
                    [],
                )
            return (
                Severity.RISKY,
                "Its value changed and your code "
                + ", ".join(sorted(value_use))
                + " it: check stored, transmitted or compared values.",
                [],
            )
        if o.get("type") == n.get("type"):
            if what == "field" and o.get("offset") != n.get("offset"):
                return (
                    Severity.INFO,
                    "Only its position moved; source code is unaffected, binaries built "
                    "against the old layout aren't.",
                    [],
                )
            return None
        cb_sites = [
            d["at"]
            for d in callback_diags
            if d["at"] in at or (what == "typedef" and f"'{name}'" in d["message"])
        ]
        explained.update(cb_sites)
        if _strip_cv(n["type"]) == _strip_cv(o["type"]) and not cb_sites:
            # Only const/volatile added or removed: callers still compile (regression:
            # sys_dlist_peek_head(const sys_dnode_t *) on Golioth was breaking).
            sev = Severity.INFO if what == "function" else Severity.RISKY
            return sev, "Only const/volatile qualifiers changed.", []
        fn_ptr = "(*)" in str(n.get("type"))
        if what == "function" or cb_sites or fn_ptr:
            return (
                Severity.BREAKING,
                "Update the code that calls, implements or stores it"
                + (" (your callbacks no longer match it)." if cb_sites else "."),
                cb_sites,
            )
        return Severity.RISKY, "Check the code that uses it.", []

    @staticmethod
    def _lost_syscalls(ctx: ScanContext, header: str, lost: list[tuple[str, int, str]]) -> Finding:
        zephyr = ctx.new.workspace.zephyr
        ext = zephyr / "cmake" / "modules" / "extensions.cmake"
        has_fn = ext.is_file() and "function(zephyr_syscall_header" in ext.read_text()
        has_kconfig = "APPLICATION_DEFINED_SYSCALL" in ctx.new_kconfig
        steps = []
        if has_fn:
            steps.append(
                f"register it in your CMakeLists.txt with zephyr_syscall_header(<path>/{header})"
            )
        if has_kconfig and ctx.new_config.get("APPLICATION_DEFINED_SYSCALL", "n") != "y":
            steps.append("set CONFIG_APPLICATION_DEFINED_SYSCALL=y")
        names = ", ".join(n for n, _, _ in lost[:4]) + (", ..." if len(lost) > 4 else "")
        ev: tuple[JSON, ...] = tuple(file_line(header, line, text) for _, line, text in lost)
        return Finding(
            category=Category.API,
            kind="syscalls-not-generated",
            subject=header,
            title=f"{header} declares system calls ({names}) that {ctx.new_spec} no longer "
            f"generates; files including it fail on the missing syscalls/"
            f"{Path(header).name}",
            severity=Severity.BREAKING,
            confidence=Confidence.HIGH,
            action=(
                "Zephyr at the target only scans registered headers for __syscall: "
                + " and ".join(steps)
                + "."
                if steps
                else "Find how the target registers application system calls "
                "(see the migration guides)."
            ),
            analyzer="api",
            scope=ctx.scope,
            evidence=ev,
            old=sorted(n for n, _, _ in lost),
            new={
                "zephyr_syscall_header": has_fn,
                "APPLICATION_DEFINED_SYSCALL": has_kconfig,
            },
        )


def _site_key(s: str) -> tuple[str, int]:
    path, _, line = s.rpartition(":")
    return (path, int(line) if line.isdigit() else 0)


def _strip_cv(t: str) -> str:
    """A type spelling without const/volatile, spacing normalized around punctuation."""
    t = re.sub(r"\b(?:const|volatile)\b", " ", t)
    return re.sub(r"\s*([(),*\[\]])\s*", r"\1", " ".join(t.split()))


def _macro_definition(zephyr: Path, name: str) -> str | None:
    """`path:line: #define name(...)` of a function-like macro in the Zephyr tree's
    public headers, if there is one."""
    pat = re.compile(rf"^\s*#\s*define\s+{re.escape(name)}\(")
    for f in sorted((zephyr / "include").rglob("*.h")):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        if name not in text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat.match(line):
                return f"zephyr/{f.relative_to(zephyr)}:{i}: {line.strip()[:120]}"
    return None


def _macro_at(ws: Any, where: str, name: str) -> dict[str, Any] | None:
    """The definition of macro `name` at the target, looked up in the header that defined
    it at the source (`where` is relative to the workspace root): function-like with its
    arity, or object-like (no value: it wasn't evaluated)."""
    path = where.rpartition(":")[0]
    for f in (Path(str(ws.root)) / path,):
        if not f.is_file():
            continue
        for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
            m = re.match(rf"^\s*#\s*define\s+{re.escape(name)}(\(([^)]*)\))?", line)
            if not m:
                continue
            fn = m[1] is not None
            params = [p for p in (m[2] or "").split(",") if p.strip()]
            return {
                "kind": "macro",
                "function_like": fn,
                "arity": len(params) if fn else None,
                "tokens": line.strip()[:400],
                "where": f"{path}:{i}",
                "deprecated": False,
            }
    return None
