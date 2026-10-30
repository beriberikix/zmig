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
import logging
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
from zmig.snapshots import build_dependencies

log = logging.getLogger("zmig")

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
        # The C++ include directories asked of the build's compilers (apidiff), recorded so
        # a reader can see what libclang was given.
        from zmig.apidiff import CXX_INCLUDE_DIRS

        if CXX_INCLUDE_DIRS:
            ctx.shared.setdefault("environment", {})["cxx_include_dirs"] = {
                ctx.runner.redact(k): [ctx.runner.redact(d) for d in v]
                for k, v in sorted(CXX_INCLUDE_DIRS.items())
            }
        not_checked += gaps
        errs = [sum(1 for t in tus if t["errors"]) for tus in (src, tgt)]
        stats = parse_stats(ctx.shared, ctx.image)
        log.info(
            "api@%s: %d TU parse(s), %d from the parse cache",
            ctx.image,
            stats.get("parsed", 0),
            stats.get("cached", 0),
        )
        checked.append(
            Checked(
                "libclang parse coverage",
                "partial" if gaps else "clean",
                coverage_detail(version, (len(src), errs[0]), (len(tgt), errs[1]), stats),
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

        src_deps = _keyed_deps(ctx.old_img, ctx.old, old_roots, src_entries, ctx.runner)
        tgt_deps = _keyed_deps(ctx.new_img, ctx.new, new_roots, tgt_entries, ctx.runner)
        cache_dir = ctx.old.dir.parent.parent / "api"
        stats = {"parsed": 0, "cached": 0, "unchanged": 0}

        def run(b: Any, roots: dict[str, str], todo: dict[str, Any], deps: Any) -> list[Any]:
            """Parse each TU, or reuse a parse whose inputs (the TU, its normalized flags,
            the content of every file it included) are identical: keyed by content, not by
            build directory, so a fresh build of the same tree reuses it."""
            results = []
            values: dict[str, Any] = {}
            for rel, e in sorted(todo.items()):
                key = _parse_key(version, e, b, deps.get(rel))
                cached = cache_dir / f"{key}.json" if key else None
                if cached is not None and cached.is_file():
                    results.append(json.loads(cached.read_text()))
                    stats["cached"] += 1
                    continue
                r: TuResult = parse_tu(
                    e["file"],
                    e["command"],
                    roots,
                    lambda f: project_path(f, roots),
                    str(b.workspace.root),
                    str(b.dir),
                    values,
                )
                stats["parsed"] += 1
                one = {
                    "file": rel,
                    "decls": r.decls,
                    "refs": r.refs,
                    "diags": r.diags,
                    "fn_values": r.fn_values,
                    "errors": r.errors,
                    "dropped": r.dropped,
                }
                if cached is not None:
                    cached.parent.mkdir(parents=True, exist_ok=True)
                    tmp = cached.with_suffix(".tmp")
                    tmp.write_text(json.dumps(one))
                    tmp.replace(cached)
                results.append(one)
            return results

        # A TU whose every included file is identical at both refs can't use a changed
        # declaration: parse neither side.
        todo = {rel: tgt_entries[rel] for rel in src_entries if rel in tgt_entries}
        same = {
            rel
            for rel in todo
            if src_deps.get(rel) is not None and src_deps.get(rel) == tgt_deps.get(rel)
        }
        stats["unchanged"] = len(same)
        src = run(ctx.old, old_roots, {r: e for r, e in src_entries.items() if r not in same},
                  src_deps)  # fmt: skip
        todo = {r: e for r, e in todo.items() if r not in same}
        tgt = run(ctx.new, new_roots, todo, tgt_deps) if todo else []
        # Per image: `shared` is common to every image of a sysbuild build (regression: the
        # app's record showed MCUboot's 0/0 counts, DOGFOOD-5 M20).
        record_parse_stats(ctx.shared, ctx.image, stats)
        todo_all = {rel: tgt_entries[rel] for rel in src_entries if rel in tgt_entries}
        # Project TUs whose API use couldn't be compared: no target compile command, or a
        # parse with errors at either side. The verdict's confidence reflects it.
        uninterpreted = (set(src_entries) - set(todo_all)) | {
            t["file"] for t in (*src, *tgt) if t["errors"]
        }
        ctx.shared.setdefault("api_coverage", {})[ctx.image] = {
            "tus": len(src_entries),
            "not_interpreted": len(uninterpreted),
            # Compared at both refs without parse errors (or no included file changed):
            # the sbom analyzer trusts these TUs' "no declaration changed" (AUDIT.md F8).
            "clean_tus": sorted(set(todo_all) - uninterpreted),
        }
        for rel in sorted(set(src_entries) - set(todo_all)):
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
        # Project callbacks whose function-pointer type no longer matches at the target:
        # their definitions are what has to change (N1a in docs/oracles/cannectivity.md).
        old_mismatch = {(m["function"], m["at"]) for t in src for m in t.get("fn_values", [])}
        new_mismatch = [
            m
            for t in tgt
            for m in t.get("fn_values", [])
            if (m["function"], m["at"]) not in old_mismatch
        ]

        def definitions(expected: str, sites: list[str]) -> list[tuple[str, int, str]]:
            """Definition sites of the project callbacks that no longer match: passed at one
            of `sites`, or converted to `expected` in one of their files."""
            at = set(sites)
            files = {x.rpartition(":")[0] for x in sites}
            out = set()
            for m in new_mismatch:
                same_type = _bare(m["expected"]) == _bare(expected)
                if m["at"] in at or (same_type and m["at"].rpartition(":")[0] in files):
                    path, _, line = m["defined_at"].rpartition(":")
                    out.add((path, int(line), f"{m['function']}: definition to change"))
            return sorted(out)

        findings: list[Finding] = []

        def add(kind: str, name: str, title: str, sev: Severity, action: str,
                sites: list[str], old: Any, new: Any, decl_ev: list[JSON],
                defs: list[tuple[str, int, str]] = []) -> None:  # noqa: B006  # fmt: skip
            # Definition sites (what to change) first, then the use sites.
            ev: list[JSON] = [file_line(p, ln, text) for p, ln, text in defs]
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
                if what == "function" and (old_ws := getattr(ctx.old, "workspace", None)):
                    # The same declaration in the same header at both refs isn't a removal:
                    # a target parse with errors records no reference to a call it couldn't
                    # type-check (regression: CoffeeCaller's led_strip_update_rgb, DOGFOOD-8
                    # B3). A changed declaration still falls through to "removed".
                    before = _declaration_text(old_ws.root, o["where"], name)
                    if before and before == _declaration_text(
                        ctx.new.workspace.root, o["where"], name
                    ):
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
            if n["kind"] != what:
                # The same name is now another kind of declaration (only referenced ones are
                # recorded, so a function the project calls may be a macro at the target).
                if what == "function" and n["kind"] == "macro" and n.get("function_like"):
                    add(f"{what}-became-macro", name,
                        f"{what_l} {name}, which your code uses, is a macro in {ctx.new_spec}",
                        Severity.INFO,
                        "Calls keep working; code that takes its address or #undefs it "
                        "doesn't.", sites, o.get("type"), n["where"],
                        [decl_old, tool_output("macro at the target", n["where"])])  # fmt: skip
                else:
                    add(f"{what}-kind-changed", name,
                        f"{what_l} {name}, which your code uses, is now a {n['kind']} in "
                        f"{ctx.new_spec}", Severity.RISKY,
                        "Check every use: a different kind of declaration behaves differently.",
                        sites, what, n["kind"], [decl_old])  # fmt: skip
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
                    [decl_old, decl_new], definitions(name, sites + extra_sites))  # fmt: skip
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
                    {"expected": key, "message": msg}, [], definitions(key, sites))  # fmt: skip
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


def coverage_detail(
    version: str, src: tuple[int, int], tgt: tuple[int, int], stats: dict[str, int]
) -> str:
    """The parse-coverage record: (TUs, with errors) per side. Parses served from zmig's
    parse cache count as parsed, so a warm cache doesn't change report.json (docs/DESIGN.md
    §3); the split is logged."""
    parsed = stats.get("parsed", 0) + stats.get("cached", 0)
    return (
        f"{version}: {src[0]} project TU(s) with the source flags ({src[1]} with errors), "
        f"{tgt[0]} with the target flags ({tgt[1]} with errors); {parsed} parsed, "
        f"{stats.get('unchanged', 0)} TU(s) skipped (no included file changed)"
    )


def record_parse_stats(shared: dict[str, Any], image: str, stats: dict[str, int]) -> None:
    shared.setdefault("api_parse_stats", {})[image] = stats


def parse_stats(shared: dict[str, Any], image: str) -> dict[str, int]:
    """This image's parse counts (`shared` is common to every image of a sysbuild build)."""
    stats: dict[str, int] = shared.get("api_parse_stats", {}).get(image, {})
    return stats


def _bare(type_name: str) -> str:
    """A type name without qualifiers: diagnostics say 'const state_execution'."""
    return " ".join(w for w in type_name.split() if w not in ("const", "volatile"))


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


def _declaration_text(root: Any, where: str, name: str) -> str | None:
    """Function `name`'s declaration in the header `where` (relative to the workspace
    `root`) names, from its name to the closing parenthesis, whitespace collapsed; None if
    the header doesn't declare it."""
    try:
        text = (Path(str(root)) / where.rpartition(":")[0]).read_text(errors="replace")
    except OSError:
        return None
    # Not a preprocessor line, a return statement or a comment (`* @see name()` in the
    # doc comment comes first in led_strip.h, and is reworded between versions).
    m = re.search(rf"^(?!\s*(?:#|return\b|\*|/[/*]))[^;=\n]*\b{re.escape(name)}\s*\(", text, re.M)
    if m is None:
        return None
    depth = 0
    for k in range(m.end() - 1, len(text)):
        depth += {"(": 1, ")": -1}.get(text[k], 0)
        if depth == 0:
            return " ".join(text[m.start() : k + 1].split())
    return None


def _macro_at(ws: Any, where: str, name: str) -> dict[str, Any] | None:
    """The definition of macro `name` at the target, looked up in the header that defined
    it at the source (`where` is relative to the workspace root): function-like with its
    arity, or object-like (no value: it wasn't evaluated)."""
    path = where.rpartition(":")[0]
    for f in (Path(str(ws.root)) / path,):
        if not f.is_file():
            continue
        lines = f.read_text(errors="replace").splitlines()
        for i, line in enumerate(lines, 1):
            # The name must end there (regression: CONTAINER_OF matched
            # "#define CONTAINER_OF_VALIDATE(" earlier in the header, DOGFOOD-5 M17).
            m = re.match(rf"^\s*#\s*define\s+{re.escape(name)}(?![\w])(\()?", line)
            if not m:
                continue
            fn = m[1] is not None
            params: list[str] = []
            if fn:
                # A parameter list continued over backslash-continued lines
                # (DNS_SD_REGISTER_SERVICE: 7 parameters on two lines).
                text, j = line[m.end() :], i
                while ")" not in text and text.rstrip().endswith("\\") and j < len(lines):
                    text = text.rstrip()[:-1] + " " + lines[j]
                    j += 1
                params = [p for p in text.split(")", 1)[0].split(",") if p.strip()]
            return {
                "kind": "macro",
                "function_like": fn,
                "arity": len(params) if fn else None,
                "tokens": line.strip()[:400],
                "where": f"{path}:{i}",
                "deprecated": False,
            }
    return None


def _keyed_deps(
    img: ImageContext, b: Any, roots: dict[str, str], entries: dict[str, Any], runner: Any
) -> dict[str, dict[str, str]]:
    """{project TU: {file key: content hash}} for every file the TU included, from ninja's
    dependency records. Keys don't depend on the build directory or workspace location
    (workspace-relative, build-relative, or the project path), so two builds of the same
    content compare equal."""
    import hashlib

    deps = build_dependencies(img, runner)
    if deps is None:
        return {}
    ws, bdir = str(b.workspace.root), str(img.dir)
    hashes: dict[str, str] = {}

    def digest(f: str) -> str:
        if f not in hashes:
            try:
                hashes[f] = hashlib.sha256(Path(f).read_bytes()).hexdigest()
            except OSError:
                hashes[f] = "missing"
        return hashes[f]

    out: dict[str, dict[str, str]] = {}
    for rel, e in entries.items():
        obj = _object_of(e)
        # compile_commands' "output" is absolute; ninja keys objects relative to the build.
        if obj and obj.startswith(bdir + "/"):
            obj = obj[len(bdir) + 1 :]
        files = deps.get(obj) if obj else None
        if files is None:
            continue
        keyed = {}
        for f in files:
            if f == e["file"]:
                continue
            own = project_path(f, roots)
            if own is not None:
                k = f"project:{own}"
            elif f.startswith(bdir + "/"):
                k = f"build:{f[len(bdir) + 1 :]}"
            elif f.startswith(ws + "/"):
                k = f"ws:{f[len(ws) + 1 :]}"
            else:
                k = f"abs:{f}"
            keyed[k] = digest(f)
        out[rel] = keyed
    return out


def _object_of(e: dict[str, Any]) -> str | None:
    """The object file a compile_commands entry produces (its "output", or -o)."""
    if e.get("output"):
        return str(e["output"])
    import shlex

    args = shlex.split(e["command"])
    return args[args.index("-o") + 1] if "-o" in args else None


def _parse_key(version: str, e: dict[str, Any], b: Any, deps: dict[str, str] | None) -> str | None:
    """Content key of one TU parse: the parser, the TU's text, its flags with build- and
    workspace-specific paths replaced, and the content of every file it included. None
    (don't cache) when the included files aren't known."""
    import hashlib

    if deps is None:
        return None
    try:
        text = Path(e["file"]).read_bytes()
    except OSError:
        return None
    cmd = e["command"].replace(str(b.dir), "<build>").replace(str(b.workspace.root), "<ws>")
    cmd = cmd.replace(str(b.src), "<app>")
    h = hashlib.sha256()
    for part in (str(API_VERSION), version, cmd, json.dumps(sorted(deps.items()))):
        h.update(part.encode())
        h.update(b"\0")
    h.update(text)
    return h.hexdigest()
