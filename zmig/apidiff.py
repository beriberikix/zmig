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
API_VERSION = 11

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

# A value that no longer fits where the project puts it (a 64-bit event in a uint32_t).
TRUNCATION_DIAG = re.compile(r"changes value from|loses integer precision")
_UNNAMED = re.compile(r"\((?:anonymous|unnamed)(?: (?:struct|union|enum))? at [^)]*\)")


def norm_type(spelling: str) -> str:
    """A type spelling comparable across refs: an unnamed record's spelling names its file
    and line ("struct x::(unnamed at /…/workspaces/<sha>/…/zcbor.h:52:2)")."""
    return _UNNAMED.sub("(anonymous)", spelling)


_DTS_DEVICE = re.compile(r"'__device_dts_ord_(\d+)'")
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
    pre += [
        "-Wno-everything",
        "-Wincompatible-function-pointer-types",
        "-Wincompatible-pointer-types",
        "-Wconstant-conversion",
        "-Wshorten-64-to-32",
    ]
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
    macro_values: dict[str, Any] | None = None,
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
    sources: dict[str, list[str]] = {}
    uses = value_uses(tu, show)

    def use_of(ext: Any, outermost: bool) -> str:
        """How the expression a reference forms (the name itself, or for a macro the
        outermost expression its expansion produces) uses the value."""
        s, e = ext.start, ext.end
        own = show(os.path.normpath(s.file.name)) if s.file else None
        if own is None:
            return "unknown"
        if not outermost:
            return uses.get((own, s.line, s.column, e.line, e.column), "unknown")
        best: tuple[tuple[int, int], str] | None = None
        for (f, sl, sc, el, ec), u in uses.items():
            inside = f == own and (sl, sc) == (s.line, s.column) and (el, ec) <= (e.line, e.column)
            if inside and (best is None or (el, ec) > best[0]):
                best = ((el, ec), u)
        return best[1] if best else "unknown"

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
                    "type": norm_type(c.type.get_canonical().spelling),
                    "where": f"{decl_path(loc)}:{line}",
                    "deprecated": c.availability == ci.AvailabilityKind.DEPRECATED,
                }
            elif c.kind == K.ENUM_CONSTANT_DECL and c.spelling not in res.decls:
                res.decls[c.spelling] = {
                    "kind": "enum",
                    "value": c.enum_value,
                    "type": norm_type(c.type.get_canonical().spelling),
                    "where": f"{decl_path(loc)}:{line}",
                    "deprecated": c.availability == ci.AvailabilityKind.DEPRECATED,
                }
            elif c.kind == K.TYPEDEF_DECL and c.spelling not in res.decls:
                res.decls[c.spelling] = {
                    "kind": "typedef",
                    "type": norm_type(c.underlying_typedef_type.get_canonical().spelling),
                    "where": f"{decl_path(loc)}:{line}",
                    "deprecated": c.availability == ci.AvailabilityKind.DEPRECATED,
                }
            continue
        if c.kind == K.MACRO_INSTANTIATION:
            # Matched at the preprocessor's instantiation site, never by text.
            d = c.referenced
            if d is None or d.location.file is None or where(d.location)[0] is not None:
                continue
            dfile = os.path.normpath(d.location.file.name)
            if build_dir and dfile.startswith(build_dir + os.sep):
                continue
            if d.spelling not in res.decls:
                toks = [t.spelling for t in d.get_tokens()]
                fn_like = _function_like(d)
                res.decls[d.spelling] = {
                    "kind": "macro",
                    "function_like": fn_like,
                    "arity": _arity(toks) if fn_like else None,
                    "tokens": " ".join(toks[1:])[:400],
                    "where": f"{decl_path(d.location)}:{d.location.line}",
                    "deprecated": False,
                }
            res.refs.append(
                {
                    "name": d.spelling,
                    "kind": "macro",
                    "at": f"{own}:{line}",
                    "use": use_of(c.extent, True),
                }
            )
            continue
        if c.kind in (K.CALL_EXPR, K.DECL_REF_EXPR, K.TYPE_REF, K.MEMBER_REF_EXPR):
            ref = c.referenced
            if ref is None or ref.location.file is None:
                continue
            if where(ref.location)[0] is not None:
                continue  # the project's own declaration
            # Written by the project, not by a Zephyr macro body expanded in its code
            # (regression: LOG_DBG in ZMK's kscan_gpio_matrix.c "used" the logging
            # internals Z_LOG_MSG2_MODE_*). Macro arguments are spelled in the project.
            written = _written_at(c.location, ref.spelling, sources)
            if written is None:
                continue
            line = written
            kind = None
            if ref.kind == K.FUNCTION_DECL:
                kind = "function"
            elif ref.kind == K.TYPEDEF_DECL:
                kind = "typedef"
            elif ref.kind == K.ENUM_CONSTANT_DECL:
                kind = "enum"
            elif ref.kind == K.FIELD_DECL:
                owner = _record_name(ref.semantic_parent)
                if not owner:
                    continue
                key = norm_type(f"{owner}.{ref.spelling}")
                if key not in res.decls:
                    res.decls[key] = {
                        "kind": "field",
                        "type": norm_type(ref.type.get_canonical().spelling),
                        "offset": ref.get_field_offsetof(),
                        "where": f"{decl_path(ref.location)}:{ref.location.line}",
                        "deprecated": ref.availability == ci.AvailabilityKind.DEPRECATED,
                    }
                res.refs.append({"name": key, "kind": "field", "at": f"{own}:{line}"})
                # A field whose type is a typedef: the project uses that typedef.
                decl = ref.type.get_declaration()
                if decl.kind == K.TYPEDEF_DECL:
                    res.refs.append(
                        {"name": decl.spelling, "kind": "typedef", "at": f"{own}:{line}"}
                    )
                continue
            if kind:
                r = {"name": ref.spelling, "kind": kind, "at": f"{own}:{line}"}
                if kind == "enum":
                    r["use"] = use_of(c.extent, False)
                res.refs.append(r)
    object_like = sorted(
        n for n, d in res.decls.items() if d["kind"] == "macro" and not d["function_like"]
    )
    extra = ["-I", stubs.name] if stubs is not None else []
    # A macro's value is the same in every TU of one build: evaluate each once.
    known = macro_values if macro_values is not None else {}
    todo = [n for n in object_like if n not in known]
    known.update(evaluate_macros(index, file, [*args, *extra], todo))
    for name in object_like:
        res.decls[name].update(known.get(name, {}))
    if stubs is not None:
        stubs.cleanup()
    for d in tu.diagnostics:
        if d.severity >= ci.Diagnostic.Error:
            f = d.location.file.name if d.location.file else ""
            res.errors.append(f"{show(f) or decl_path(d.location)}:{d.location.line}: {d.spelling}")
            dts = _DTS_DEVICE.search(d.spelling)
            if not dts:
                continue
            # A devicetree device the project's code takes (DEVICE_DT_GET and friends) that
            # the target doesn't define: the error is inside Zephyr's macros, the use is in
            # an "expanded from macro" note.
            use = next(
                (s for n in [d, *d.children] if (s := where(n.location))[0] is not None), None
            )
            if use is not None:
                res.diags.append(
                    {
                        "at": f"{use[0]}:{use[1]}",
                        "message": d.spelling,
                        "kind": "devicetree",
                        "ord": int(dts[1]),
                    }
                )
            continue
        if CALLBACK_DIAG.search(d.spelling):
            dkind = "callback"
        elif TRUNCATION_DIAG.search(d.spelling):
            dkind = "truncation"
        else:
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
            res.diags.append({"at": f"{site[0]}:{site[1]}", "message": d.spelling, "kind": dkind})
    res.refs = [dict(t) for t in {tuple(sorted(r.items())) for r in res.refs}]
    return res


def _record_name(parent: Any) -> str:
    """A field's struct/union, named for comparison across refs: an anonymous one by the
    nearest named record around it ("net_mgmt_event_callback.<anonymous>"); its own
    spelling holds an absolute path. "" when no named record encloses it (a typedef'd
    anonymous struct): such fields aren't compared, rather than risk a false removal."""
    import clang.cindex as ci

    records = (ci.CursorKind.STRUCT_DECL, ci.CursorKind.UNION_DECL)
    parts: list[str] = []
    while (
        parent is not None
        and parent.kind in records
        and (
            not parent.spelling or "(anonymous" in parent.spelling or "(unnamed" in parent.spelling
        )
    ):
        parts.append("<anonymous>")
        parent = parent.semantic_parent
    if parent is None or parent.kind not in records:
        return ""
    return ".".join([parent.spelling, *reversed(parts)])


def _written_at(loc: Any, name: str, sources: dict[str, list[str]]) -> int | None:
    """The line where `name` is written in the project source, or None when the reference
    comes from a macro body. libclang places a token from a macro body at the macro's name,
    and a token from a macro argument (clang_getFileLocation) where the argument is
    written, so the name is at that position only in the second case."""
    import ctypes

    import clang.cindex as ci

    f = ci.c_object_p()
    line, col, off = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
    ci.conf.lib.clang_getFileLocation(
        loc, ctypes.byref(f), ctypes.byref(line), ctypes.byref(col), ctypes.byref(off)
    )
    if not f:
        return None
    path = str(ci.File(f).name)
    if path not in sources:
        try:
            sources[path] = Path(path).read_text(errors="replace").splitlines()
        except OSError:
            sources[path] = []
    lines = sources[path]
    if not 0 < line.value <= len(lines):
        return None
    text = lines[line.value - 1]
    return line.value if text[col.value - 1 :].startswith(name) else None


def _unnamed(spelling: str) -> bool:
    return not spelling or "(anonymous" in spelling or "(unnamed" in spelling


def _function_like(cursor: Any) -> bool:
    """clang_Cursor_isMacroFunctionLike (not wrapped by the libclang 18 bindings)."""
    import ctypes

    import clang.cindex as ci

    f = ci.conf.lib.clang_Cursor_isMacroFunctionLike
    f.argtypes = [ci.Cursor]
    f.restype = ctypes.c_uint
    return bool(f(cursor))


def _arity(tokens: list[str]) -> int:
    """Parameter count of a function-like macro from its definition tokens
    (NAME ( a , b ) body...)."""
    if len(tokens) < 3 or tokens[1] != "(":
        return 0
    depth, count, seen = 0, 0, False
    for t in tokens[1:]:
        if t == "(":
            depth += 1
            continue
        if t == ")":
            depth -= 1
            if depth == 0:
                break
            continue
        if depth == 1:
            if t == ",":
                count += 1
            else:
                seen = True
    return count + 1 if seen else 0


def evaluate_macros(index: Any, file: str, args: list[str], names: list[str]) -> dict[str, Any]:
    """Value, type and size of each object-like macro as an expression in this TU (the TU's
    own text with a probe per macro appended, parsed with the same flags). Macros that
    aren't constant expressions get only their type, or nothing."""
    import ctypes

    import clang.cindex as ci

    if not names:
        return {}
    try:
        src = Path(file).read_text(errors="replace")
    except OSError:
        return {}
    probes = "".join(f"\n__typeof__(({n})) __zmig_probe_{i} = ({n});" for i, n in enumerate(names))
    tu = index.parse(
        file,
        args=args,
        unsaved_files=[(file, src + "\n" + probes + "\n")],
        options=0x200,  # KeepGoing
    )
    lib = ci.conf.lib
    lib.clang_Cursor_Evaluate.argtypes = [ci.Cursor]
    lib.clang_Cursor_Evaluate.restype = ctypes.c_void_p
    for fn, res_t in (
        ("clang_EvalResult_getKind", ctypes.c_int),
        ("clang_EvalResult_isUnsignedInt", ctypes.c_uint),
        ("clang_EvalResult_getAsUnsigned", ctypes.c_ulonglong),
        ("clang_EvalResult_getAsLongLong", ctypes.c_longlong),
    ):
        getattr(lib, fn).argtypes = [ctypes.c_void_p]
        getattr(lib, fn).restype = res_t
    lib.clang_EvalResult_dispose.argtypes = [ctypes.c_void_p]
    out: dict[str, Any] = {}
    for c in tu.cursor.get_children():
        if c.kind != ci.CursorKind.VAR_DECL or not c.spelling.startswith("__zmig_probe_"):
            continue
        name = names[int(c.spelling.rsplit("_", 1)[1])]
        t = c.type.get_canonical()
        if t.kind == ci.TypeKind.INVALID:
            continue
        v: dict[str, Any] = {"vtype": norm_type(t.spelling), "vsize": t.get_size()}
        res = lib.clang_Cursor_Evaluate(c)
        if res:
            if lib.clang_EvalResult_getKind(res) == 1:  # CXEval_Int
                v["value"] = (
                    lib.clang_EvalResult_getAsUnsigned(res)
                    if lib.clang_EvalResult_isUnsignedInt(res)
                    else lib.clang_EvalResult_getAsLongLong(res)
                )
            lib.clang_EvalResult_dispose(res)
        out[name] = v
    return out


_COMPARE = {"==", "!=", "<", ">", "<=", ">="}
_ASSIGN = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}
# What a use of a value can do with it (docs/DESIGN-P2.md review of P2, item 3). Only
# "other" uses let a changed value be demoted; anything unrecognized stays risky.
VALUE_USES = ("stored", "passed", "compared", "narrowed", "other", "unknown")


def _operator(c: Any) -> str | None:
    """The operator of a BINARY_OPERATOR cursor: the first token after its left operand."""
    kids = list(c.get_children())
    if len(kids) != 2:
        return None
    end = kids[0].extent.end.offset
    for t in c.get_tokens():
        if t.extent.start.offset >= end and t.kind.name == "PUNCTUATION":
            return str(t.spelling)
    return None


def value_use(stack: list[Any]) -> str:
    """How the expression at the end of `stack` (its ancestors before it) uses its value."""
    import clang.cindex as ci

    K = ci.CursorKind
    child = stack[-1]
    for anc in reversed(stack[:-1]):
        k = anc.kind
        if k in (K.PAREN_EXPR, K.UNEXPOSED_EXPR, K.CONDITIONAL_OPERATOR):
            child = anc
            continue
        if k in (K.UNARY_OPERATOR, K.CXX_UNARY_EXPR):
            toks = [t.spelling for t in anc.get_tokens()]
            if toks and toks[0] in ("sizeof", "_Alignof", "__alignof__"):
                return "other"  # sizeof/_Alignof (CXX_UNARY_EXPR in libclang): no value
            child = anc
            continue
        if k == K.BINARY_OPERATOR:
            op = _operator(anc)
            if op in _COMPARE:
                return "compared"
            if op in _ASSIGN:
                kids = list(anc.get_children())
                return "stored" if kids and kids[-1] == child else "unknown"
            if op is None:
                return "unknown"
            child = anc  # arithmetic/bitwise: the value flows on
            continue
        if k == K.COMPOUND_ASSIGNMENT_OPERATOR:
            return "stored"
        if k == K.CALL_EXPR:
            return "passed"
        if k in (K.VAR_DECL, K.FIELD_DECL, K.INIT_LIST_EXPR, K.RETURN_STMT):
            return "stored"
        if k == K.CSTYLE_CAST_EXPR:
            return "narrowed"
        if k in (K.CASE_STMT, K.IF_STMT, K.SWITCH_STMT, K.WHILE_STMT, K.DO_STMT, K.FOR_STMT):
            return "compared"
        if k == K.COMPOUND_STMT:
            return "other"  # an expression statement: the value is discarded
        if k == K.ARRAY_SUBSCRIPT_EXPR:
            return "other"
        return "unknown"
    return "unknown"


def value_uses(tu: Any, show: Any) -> dict[tuple[str, int, int, int, int], str]:
    """{(file, start line, start col, end line, end col): use} for every expression in
    project code, from its ancestors."""
    import clang.cindex as ci

    out: dict[tuple[str, int, int, int, int], str] = {}

    def visit(c: Any, stack: list[Any]) -> None:
        stack.append(c)
        s, e = c.extent.start, c.extent.end
        if c.kind.is_expression() and s.file is not None:
            own = show(os.path.normpath(s.file.name))
            if own is not None:
                key = (own, s.line, s.column, e.line, e.column)
                out.setdefault(key, value_use(stack))
        for k in c.get_children():
            if (
                k.kind == ci.CursorKind.MACRO_INSTANTIATION
                or k.kind == ci.CursorKind.MACRO_DEFINITION
            ):
                continue
            visit(k, stack)
        stack.pop()

    for top in tu.cursor.get_children():
        if top.location.file is not None and show(os.path.normpath(top.location.file.name)):
            visit(top, [tu.cursor])
    return out
