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

from zmig.analyzers import AnalyzerResult
from zmig.apidiff import API_VERSION, TuResult, libclang_version
from zmig.apidiff import parse as parse_tu
from zmig.buildctx import Artifact, ImageContext, project_path, project_roots
from zmig.findings import (
    JSON,
    Category,
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
            for header, decls in project_syscalls(ctx).items():
                lost = [d for d in decls if d[0] in old and d[0] not in new]
                if lost:
                    findings.append(self._lost_syscalls(ctx, header, lost))
        version = libclang_version()
        if version is None:
            not_checked.append(
                NotChecked(
                    "declarations the project uses (functions, typedefs, callbacks)",
                    "libclang isn't installed: pip install 'zmig[api]'",
                    self.name,
                )
            )
            return AnalyzerResult(findings, status="partial", not_checked=not_checked)
        ctx.shared.setdefault("environment", {})["libclang"] = version
        src, tgt, gaps = self._parse_both(ctx, version)
        not_checked += gaps
        findings += self._declaration_changes(ctx, src, tgt)
        return AnalyzerResult(findings, status="partial" if gaps else "ok", not_checked=not_checked)

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
                for rel, e in sorted(todo.items()):
                    r: TuResult = parse_tu(
                        e["file"],
                        e["command"],
                        roots,
                        lambda f: project_path(f, roots),
                        str(b.workspace.root),
                        str(b.dir),
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
        for t in src:
            for r in t["refs"]:
                uses.setdefault(r["name"], []).append(r["at"])
        old_diag_sites = {d["at"] for t in src for d in t["diags"]}
        callback_diags = [d for t in tgt for d in t["diags"] if d["at"] not in old_diag_sites]

        findings: list[Finding] = []

        def add(kind: str, name: str, title: str, sev: Severity, action: str,
                sites: list[str], old: Any, new: Any, decl_ev: list[JSON]) -> None:  # fmt: skip
            ev: list[JSON] = []
            for s in sorted(set(sites), key=_site_key):
                path, _, line = s.rpartition(":")
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

        explained: set[str] = set()
        for name in sorted(uses):
            o = old_decls.get(name)
            if o is None:
                continue
            sites = uses[name]
            # Only claim a removal if a target parse covered a file that uses it.
            covered = any(s.rpartition(":")[0] in parsed_at_target for s in sites)
            n = new_decls.get(name)
            what = o["kind"]
            decl_old = tool_output("declaration at the source", f"{o['where']}: {o['type']}")
            if n is None:
                macro = _macro_definition(ctx.new.workspace.zephyr, name) if covered else None
                if macro:
                    # Calls still compile through a function-like macro of the same name
                    # (regression: k_ms_to_ticks_ceil64 became a macro in v3.5, used by ZMK
                    # only through K_MSEC); taking its address doesn't.
                    add(f"{what}-became-macro", name,
                        f"{what.capitalize()} {name}, which your code uses, is a macro in "
                        f"{ctx.new_spec}", Severity.INFO,
                        "Calls keep working; code that takes its address or #undefs it "
                        "doesn't.", sites, o["type"], macro,
                        [decl_old, tool_output("macro at the target", macro)])  # fmt: skip
                    continue
                if covered:
                    add(f"{what}-removed", name,
                        f"{what.capitalize()} {name}, which your code uses, no longer exists "
                        f"in {ctx.new_spec}", Severity.BREAKING,
                        "Find its replacement in the migration guides.", sites, o["type"],
                        None, [decl_old])  # fmt: skip
                continue
            decl_new = tool_output("declaration at the target", f"{n['where']}: {n['type']}")
            if n["type"] != o["type"]:
                cb_sites = [d["at"] for d in callback_diags if f"'{name}'" in d["message"]]
                explained |= set(cb_sites)
                fn_ptr = "(*)" in n["type"]
                # Only const/volatile added or removed: callers still compile (regression:
                # sys_dlist_peek_head(const sys_dnode_t *) on Golioth was breaking).
                cv_only = _strip_cv(n["type"]) == _strip_cv(o["type"])
                if cv_only and not cb_sites:
                    sev = Severity.INFO if what == "function" else Severity.RISKY
                elif what == "function" or cb_sites or fn_ptr:
                    sev = Severity.BREAKING
                else:
                    sev = Severity.RISKY
                add(f"{what}-changed", name,
                    f"{what.capitalize()} {name}, which your code uses, changed: "
                    f"{o['type']} → {n['type']}", sev,
                    "Update the code that calls, implements or stores it"
                    + (" (your callbacks no longer match it)." if cb_sites else "."),
                    sites + cb_sites, o["type"], n["type"], [decl_old, decl_new])  # fmt: skip
            elif n.get("deprecated") and not o.get("deprecated"):
                add(f"{what}-deprecated", name,
                    f"{what.capitalize()} {name}, which your code uses, is deprecated in "
                    f"{ctx.new_spec}", Severity.RISKY,
                    "Move off it before the release that removes it.", sites, None, None,
                    [decl_new])  # fmt: skip

        # Callbacks the project passes or assigns whose expected type changed, where the
        # slot isn't a typedef reported above (a struct field, a parameter).
        groups: dict[str, list[str]] = {}
        for d in callback_diags:
            if d["at"] in explained:
                continue
            m = re.search(
                r"(?:assigning to|initializing|passing|returning) '([^']+)'", d["message"]
            )
            groups.setdefault(m[1] if m else d["message"], []).append(d["at"])
        for expected, sites in sorted(groups.items()):
            msg = next(d["message"] for d in callback_diags if d["at"] == sites[0])
            add("callback-type-changed", expected,
                f"Callbacks your code provides no longer match the type Zephyr expects at "
                f"{ctx.new_spec}: {expected}", Severity.BREAKING,
                f"Change their signatures to match ({msg[:200]}).", sites, None,
                {"expected": expected, "message": msg}, [])  # fmt: skip
        return findings

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
    return " ".join(re.sub(r"\b(?:const|volatile)\b", " ", t).split())


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
