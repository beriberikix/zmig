"""libclang extraction for the API diff (docs/DESIGN-P2.md §3, §10).

For each project translation unit of the SOURCE build (compile_commands.json entries whose
file is project code), parse it with that build's flags, and again with the target
build's flags for the same project file. From each parse:

  decls   declarations located outside project code (Zephyr, modules): functions
          (canonical signature, deprecated), typedefs (canonical type)
  refs    the project's references to those declarations, with file:line
  diags   clang diagnostics in project code of the kinds that show an API break
          (incompatible callback types), mapped to the project line through macro
          expansion notes
  errors  errors anywhere in the parse (a parse gap: reported as "not interpreted")

The flags are the build's own, translated for clang (GCC-only options dropped, --target
added, a clang resource directory for builtin headers).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Bump when what a parse records changes (invalidates the per-build snapshots).
API_VERSION = 2

# GCC options clang rejects or that don't matter for parsing (prefix match).
GCC_ONLY = (
    "-mfp16-format",
    "--param",
    "-fno-reorder-functions",
    "-fno-defer-pop",
    "-mabi=",
    "-fno-printf-return-value",
    "--specs",
    "-specs",
    "-fdiagnostics-color",
    "-fmacro-prefix-map",
    "-ffile-prefix-map",
    "-fdebug-prefix-map",
    "-ftls-model",
    "-fno-freestanding",
    "-Werror",
    "-fno-pic",
    "-fno-pie",
    "-fno-asynchronous-unwind-tables",
    "-fno-common",
    "-mno-",
    "-Wno-",
    "-W",
    "-g",
)
# Options that take the next argument and only concern output.
_OUTPUT_OPTS = ("-o", "-MT", "-MF", "-MQ")
_ALWAYS_DROP = ("-c", "-MD", "-MMD", "-MP")

_NOT_FOUND = re.compile(r"'([^']+)' file not found")

# Diagnostics that show a project callback no longer matches what Zephyr expects.
CALLBACK_DIAG = re.compile(
    r"incompatible (?:function )?pointer types? (?:assigning to|initializing|passing|returning)",
    re.I,
)


def triple(compiler: str, args: list[str]) -> str | None:
    """--target for the build's compiler; None for the host (native_sim)."""
    name = Path(compiler).name
    if name.startswith("arm-"):
        return "arm-none-eabi"
    if name.startswith("aarch64-"):
        return "aarch64-none-elf"
    if name.startswith("riscv64-") or name.startswith("riscv32-"):
        march = next((a for a in args if a.startswith("-march=rv")), "-march=rv32")
        return "riscv64-unknown-elf" if "rv64" in march else "riscv32-unknown-elf"
    if name.startswith("x86_64-zephyr"):
        return "x86_64-unknown-elf"
    if name.startswith("xtensa"):
        return "i686-unknown-none"  # stand-in: declarations right, layouts not checked
    return None


def resource_dir() -> str | None:
    """A clang resource directory (builtin headers: stddef.h, stdarg.h, ...). The libclang
    wheel ships none."""
    env = os.environ.get("ZMIG_CLANG_RESOURCE_DIR")
    if env:
        return env
    clang = shutil.which("clang")
    if clang:
        out = subprocess.run([clang, "-print-resource-dir"], capture_output=True, text=True)
        d = out.stdout.strip()
        if out.returncode == 0 and Path(d, "include", "stddef.h").is_file():
            return d
    return None


def clang_args(command: str, file: str) -> tuple[list[str], str | None, list[str]]:
    """(clang arguments, target triple, dropped options) for a compile_commands command."""
    args = shlex.split(command)
    compiler, rest = args[0], args[1:]
    out: list[str] = []
    dropped: list[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in _OUTPUT_OPTS:
            i += 2
            continue
        if a in _ALWAYS_DROP or a == file:
            i += 1
            continue
        if a.startswith(GCC_ONLY) and not a.startswith(("-Wl,", "-Wa,")):
            dropped.append(a)
            i += 1
            continue
        out.append(a)
        i += 1
    t = triple(compiler, rest)
    pre = [f"--target={t}"] if t else []
    rd = resource_dir()
    if rd:
        pre += ["-resource-dir", rd]
    else:
        gcc_inc = subprocess.run(
            [compiler, "-print-file-name=include"], capture_output=True, text=True
        ).stdout.strip()
        if gcc_inc:
            pre += ["-isystem", gcc_inc]
    pre += ["-Wno-everything", "-Wincompatible-function-pointer-types",
            "-Wincompatible-pointer-types"]  # fmt: skip
    return pre + out, t, dropped


@dataclass
class TuResult:
    file: str  # project path as shown
    decls: dict[str, dict[str, Any]] = field(default_factory=dict)
    refs: list[dict[str, Any]] = field(default_factory=list)
    diags: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def libclang_version() -> str | None:
    try:
        import clang.cindex as ci  # type: ignore[import-untyped]
    except ImportError:
        return None
    lib_file = os.environ.get("ZMIG_LIBCLANG")
    if lib_file and not ci.Config.loaded:
        ci.Config.set_library_file(lib_file)
    lib = ci.conf.lib
    lib.clang_getClangVersion.restype = ci._CXString
    lib.clang_getClangVersion.errcheck = ci._CXString.from_result
    return str(lib.clang_getClangVersion())


def parse(
    file: str,
    command: str,
    roots: dict[str, str],
    show: Any,
    zephyr_root: str,
    build_dir: str = "",
) -> TuResult:
    """Parse one TU. `roots` decide what's project code; `show(path)` gives the project
    path as shown, None for non-project code; non-project declarations are keyed with a
    path relative to `zephyr_root` (the workspace)."""
    import clang.cindex as ci

    args, _, dropped = clang_args(command, file)
    index = ci.Index.create()
    keep_going = 0x200  # CXTranslationUnit_KeepGoing: parse on after a missing header
    opts = ci.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD | keep_going
    tu = index.parse(file, args=args, options=opts)
    res = TuResult(show(file) or file, dropped=dropped)
    # A missing header is a fatal error, and clang reports nothing after one, even with
    # KeepGoing (regression: ZMK's combo.c at v3.5 stopped at syscalls/behavior.h, hiding
    # its SYS_INIT). Parse again with empty stand-ins for exactly those headers; the stand-ins
    # are recorded as parse errors (a gap), never silently.
    missing = sorted({m[1] for d in tu.diagnostics if (m := _NOT_FOUND.match(d.spelling))})
    stubs = tempfile.TemporaryDirectory(prefix="zmig-stubs-") if missing else None
    if stubs is not None:
        for h in missing:
            p = Path(stubs.name, h)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("")
        res.errors += [f"{h}: file not found (parsed with an empty stand-in)" for h in missing]
        tu = index.parse(file, args=[*args, "-I", stubs.name], options=opts)

    def where(loc: Any) -> tuple[str | None, int]:
        f = loc.file.name if loc.file else ""
        return (show(os.path.normpath(f)) if f else None), loc.line

    def decl_path(loc: Any) -> str:
        f = os.path.normpath(loc.file.name) if loc.file else ""
        return os.path.relpath(f, zephyr_root) if f.startswith(zephyr_root) else f

    K = ci.CursorKind
    for c in tu.cursor.walk_preorder():
        loc = c.location
        if loc.file is None:
            continue
        own, line = where(loc)
        if own is None:
            # A declaration outside project code: Zephyr's or a module's, not one the build
            # generated (regression: ZMK's own __syscall wrappers in the build directory's
            # syscalls/behavior.h were reported as removed Zephyr functions).
            if build_dir and os.path.normpath(loc.file.name).startswith(build_dir + os.sep):
                continue
            if c.kind == K.FUNCTION_DECL and c.spelling not in res.decls:
                res.decls[c.spelling] = {
                    "kind": "function",
                    "type": c.type.get_canonical().spelling,
                    "where": f"{decl_path(loc)}:{line}",
                    "deprecated": c.availability == ci.AvailabilityKind.DEPRECATED,
                }
            elif c.kind == K.TYPEDEF_DECL and c.spelling not in res.decls:
                res.decls[c.spelling] = {
                    "kind": "typedef",
                    "type": c.underlying_typedef_type.get_canonical().spelling,
                    "where": f"{decl_path(loc)}:{line}",
                    "deprecated": c.availability == ci.AvailabilityKind.DEPRECATED,
                }
            continue
        if c.kind in (K.CALL_EXPR, K.DECL_REF_EXPR, K.TYPE_REF, K.MEMBER_REF_EXPR):
            ref = c.referenced
            if ref is None or ref.location.file is None:
                continue
            if where(ref.location)[0] is not None:
                continue  # the project's own declaration
            kind = None
            if ref.kind == K.FUNCTION_DECL:
                kind = "function"
            elif ref.kind == K.TYPEDEF_DECL:
                kind = "typedef"
            elif ref.kind == K.FIELD_DECL:
                # A field whose type is a typedef: the project uses that typedef.
                decl = ref.type.get_declaration()
                if decl.kind == K.TYPEDEF_DECL:
                    res.refs.append(
                        {"name": decl.spelling, "kind": "typedef", "at": f"{own}:{line}"}
                    )
                continue
            if kind:
                res.refs.append({"name": ref.spelling, "kind": kind, "at": f"{own}:{line}"})
    if stubs is not None:
        stubs.cleanup()
    for d in tu.diagnostics:
        if d.severity >= ci.Diagnostic.Error:
            f = d.location.file.name if d.location.file else ""
            res.errors.append(f"{show(f) or decl_path(d.location)}:{d.location.line}: {d.spelling}")
            continue
        if not CALLBACK_DIAG.search(d.spelling):
            continue
        site: tuple[str | None, int] = where(d.location)
        if site[0] is None:
            # Raised inside a Zephyr macro: the project line is in an "expanded from" note.
            for n in d.children:
                s = where(n.location)
                if s[0] is not None:
                    site = s
                    break
        if site[0] is not None:
            res.diags.append({"at": f"{site[0]}:{site[1]}", "message": d.spelling})
    res.refs = [dict(t) for t in {tuple(sorted(r.items())) for r in res.refs}]
    return res
