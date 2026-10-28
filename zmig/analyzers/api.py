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
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.buildctx import Artifact, ImageContext, project_path, project_roots
from zmig.findings import JSON, Category, Confidence, Finding, NotChecked, Severity, file_line
from zmig.scan import ScanContext
from zmig.snapshots import build_dependencies

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
        return AnalyzerResult(findings, not_checked=not_checked)

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
