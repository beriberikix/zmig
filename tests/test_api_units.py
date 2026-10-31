"""P2 (DESIGN.md, "API and ABI diff") unit tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from zmig.analyzers.api import _SYSCALL_DECL, syscall_names


def _img(tmp: Path, entries: list[object]) -> SimpleNamespace:
    p = tmp / "zephyr" / "misc" / "generated"
    p.mkdir(parents=True)
    (p / "syscalls.json").write_text(json.dumps(entries))
    return SimpleNamespace(dir=tmp)


def test_syscall_names_both_formats(tmp_path: Path) -> None:
    # v3.2: [[ret+name, args], header]; v4.x adds an is_syscall flag.
    old = _img(
        tmp_path / "old",
        [[["const struct device *behavior_get_binding", "const char *n"], "behavior.h"]],
    )
    new = _img(
        tmp_path / "new",
        [
            [["int zephyr_read_stdin", "char *buf, int nbytes"], "libc-hooks.h", True],
            [["int k_object_marker", "void"], "kernel.h", False],
        ],
    )
    assert syscall_names(old) == {"behavior_get_binding"}  # type: ignore[arg-type]
    assert syscall_names(new) == {"zephyr_read_stdin"}  # type: ignore[arg-type]
    assert syscall_names(SimpleNamespace(dir=tmp_path / "none")) is None  # type: ignore[arg-type]


def test_syscall_declarations_in_a_project_header() -> None:
    text = (
        "/* __syscall in a comment */\n"
        "__syscall const struct device *behavior_get_binding(const char *name);\n"
        "static inline int z_impl_x(void);\n"
        "__syscall int ext_power_enable(const struct device *dev);\n"
    )
    assert [m["name"] for m in _SYSCALL_DECL.finditer(text)] == [
        "behavior_get_binding",
        "ext_power_enable",
    ]


# --- step 2/3: declarations the project uses, from libclang ---------------------------

clang = pytest.importorskip("clang.cindex")

OLD_H = """\
#pragma once
int used_fn(int a);
int unused_fn(int a);
typedef void (*cb_t)(unsigned int ev);
void register_cb(cb_t cb);
struct s { int used_field; int unused_field; };
enum e { USED_C = 1, UNUSED_C = 2 };
#define USED_M 0x80000001u
#define UNUSED_M 2u
#define USED_FM(a) (a)
#define UNUSED_FM(a) (a)
"""
NEW_H = """\
#pragma once
int used_fn(long a);
int unused_fn(long a);
typedef void (*cb_t)(unsigned long long ev);
void register_cb(cb_t cb);
struct s { long used_field; long unused_field; };
enum e { USED_C = 3, UNUSED_C = 4 };
#define USED_M 0x8000000100000001ull
#define USED_FM(a, b) (a)
#define UNUSED_FM(a, b) (a)
"""
MAIN_USED = """\
#include <zephyr/api.h>
static void my_cb(unsigned int ev) { (void)ev; }
int main(void) {
    struct s x;
    unsigned int v = USED_M;
    x.used_field = USED_C;
    register_cb(my_cb);
    return used_fn(USED_FM(1)) + (int)v;
}
"""
MAIN_UNUSED = """\
#include <zephyr/api.h>
int main(void) { return 0; }
"""


def _tus(
    tmp: Path, main: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], SimpleNamespace]:
    from zmig.apidiff import parse

    app = tmp / "app"
    app.mkdir()
    (app / "main.c").write_text(main)
    out = []
    for side, h in (("old", OLD_H), ("new", NEW_H)):
        inc = tmp / side / "include" / "zephyr"
        inc.mkdir(parents=True)
        (inc / "api.h").write_text(h)
        cmd = f"cc -I{tmp / side / 'include'} -c {app / 'main.c'}"
        r = parse(
            str(app / "main.c"),
            cmd,
            {str(app): ""},
            lambda f, a=str(app): f[len(a) + 1 :] if f.startswith(a + "/") else None,
            str(tmp / side),
        )
        out.append(
            {
                "file": "main.c",
                "decls": r.decls,
                "refs": r.refs,
                "diags": r.diags,
                "fn_values": r.fn_values,
                "errors": r.errors,
                "dropped": r.dropped,
            }
        )
    ctx = SimpleNamespace(
        old=SimpleNamespace(workspace=SimpleNamespace(zephyr=tmp / "old", root=tmp / "old")),
        new=SimpleNamespace(workspace=SimpleNamespace(zephyr=tmp / "new", root=tmp / "new")),
        new_spec="new",
        scope=("s",),
    )
    return [out[0]], [out[1]], ctx


def test_declarations_the_project_uses_and_only_those(tmp_path: Path) -> None:
    from zmig.analyzers.api import ApiAnalyzer
    from zmig.findings import Severity

    src, tgt, ctx = _tus(tmp_path, MAIN_USED)
    found = {f.subject: f for f in ApiAnalyzer()._declaration_changes(ctx, src, tgt)}  # type: ignore[arg-type]
    assert not any(k.startswith(("unused", "UNUSED")) or k == "s.unused_field" for k in found), (
        found
    )
    assert found["used_fn"].severity is Severity.BREAKING  # signature changed
    # register_cb's parameter type (cb_t) widened: my_cb no longer matches it.
    assert found["register_cb"].severity is Severity.BREAKING
    assert any(e.get("line") == 7 for e in found["register_cb"].evidence)
    # ... and cites my_cb's definition, the line to change.
    assert ("main.c", 2, "my_cb: definition to change") in [
        (e.get("path"), e.get("line"), e.get("text")) for e in found["register_cb"].evidence
    ]
    assert found["s.used_field"].severity is Severity.RISKY  # type changed
    assert found["USED_C"].severity is Severity.RISKY  # value changed
    assert found["USED_FM"].severity is Severity.BREAKING  # arity changed
    # 32 -> 64 bit and stored in an unsigned int: the value changes (G1b's shape).
    assert found["USED_M"].severity is Severity.BREAKING
    assert any(e.get("line") == 5 for e in found["USED_M"].evidence)
    # Every finding points at a project line.
    assert all(any(e["type"] == "file_line" for e in f.evidence) for f in found.values())


SMF_OLD_H = """\
#pragma once
typedef void (*state_method)(void *obj);
typedef void (*state_execution)(void *obj);
struct smf_state { const state_method entry; const state_execution run; };
#define SMF_CREATE_STATE(_entry, _run) { .entry = _entry, .run = _run }
"""
SMF_NEW_H = """\
#pragma once
enum smf_state_result { SMF_EVENT_HANDLED, SMF_EVENT_PROPAGATE };
typedef void (*state_method)(void *obj);
typedef enum smf_state_result (*state_execution)(void *obj);
struct smf_state { const state_method entry; const state_execution run; };
#define SMF_CREATE_STATE(_entry, _run) { .entry = _entry, .run = _run }
"""
SMF_MAIN = """\
#include <zephyr/smf.h>
static void st_entry(void *o) { (void)o; }

static void st_run(void *o)
{
    (void)o;
}

static const struct smf_state states[] = {
    SMF_CREATE_STATE(st_entry, st_run),
};
const void *keep = states;
"""


def test_callback_findings_cite_definitions_and_uses(tmp_path: Path) -> None:
    """CANnectivity N1a (eval/oracles/cannectivity.md): the run functions passed to
    SMF_CREATE_STATE no longer match `state_execution`. The finding cited only the
    SMF_CREATE_STATE line (the use); the maintainers changed the definitions."""
    from zmig.analyzers.api import ApiAnalyzer
    from zmig.apidiff import parse

    app = tmp_path / "app"
    app.mkdir()
    (app / "main.c").write_text(SMF_MAIN)
    out = []
    for side, h in (("old", SMF_OLD_H), ("new", SMF_NEW_H)):
        inc = tmp_path / side / "include" / "zephyr"
        inc.mkdir(parents=True)
        (inc / "smf.h").write_text(h)
        cmd = f"cc -I{tmp_path / side / 'include'} -c {app / 'main.c'}"
        r = parse(
            str(app / "main.c"),
            cmd,
            {str(app): ""},
            lambda f, a=str(app): f[len(a) + 1 :] if f.startswith(a + "/") else None,
            str(tmp_path / side),
        )
        out.append(
            {
                "file": "main.c",
                "decls": r.decls,
                "refs": r.refs,
                "diags": r.diags,
                "fn_values": r.fn_values,
                "errors": r.errors,
                "dropped": r.dropped,
            }
        )
    # Only the run function is a mismatch at the target; the entry function still fits.
    assert out[0]["fn_values"] == []
    assert [(m["function"], m["defined_at"]) for m in out[1]["fn_values"]] == [
        ("st_run", "main.c:4")
    ]
    ctx = SimpleNamespace(
        new=SimpleNamespace(
            workspace=SimpleNamespace(zephyr=tmp_path / "new", root=tmp_path / "new")
        ),
        new_spec="new",
        scope=("s",),
    )
    found = ApiAnalyzer()._declaration_changes(ctx, [out[0]], [out[1]])  # type: ignore[arg-type]
    cb = [f for f in found if any("definition to change" in str(e.get("text")) for e in f.evidence)]
    assert len(cb) == 1, [(f.kind, f.subject) for f in found]
    lines = [
        (e["path"], e["line"], e.get("text")) for e in cb[0].evidence if e["type"] == "file_line"
    ]
    assert lines[0] == ("main.c", 4, "st_run: definition to change")  # definition first
    assert ("main.c", 10) in [(p, ln) for p, ln, _ in lines[1:]]  # then the use


def test_zero_findings_for_symbols_the_project_does_not_use(tmp_path: Path) -> None:
    from zmig.analyzers.api import ApiAnalyzer

    src, tgt, ctx = _tus(tmp_path, MAIN_UNUSED)
    assert ApiAnalyzer()._declaration_changes(ctx, src, tgt) == []  # type: ignore[arg-type]


def test_missing_header_is_a_parse_gap_not_clean(tmp_path: Path) -> None:
    from zmig.apidiff import parse

    app = tmp_path / "app"
    app.mkdir()
    (app / "main.c").write_text("#include <syscalls/gone.h>\nstatic int f(void) { return 0; }\n")
    r = parse(str(app / "main.c"), f"cc -c {app / 'main.c'}", {}, lambda f: None, str(tmp_path))
    assert r.errors == ["syscalls/gone.h: file not found (parsed with an empty stand-in)"]


def test_uses_inside_zephyr_macro_bodies_are_not_project_uses(tmp_path: Path) -> None:
    """LOG_DBG in ZMK's kscan_gpio_matrix.c 'used' the logging internals
    Z_LOG_MSG2_MODE_*; macro arguments are the project's own (CFB_DISPLAY_HEIGH)."""
    from zmig.apidiff import parse

    inc, app = tmp_path / "inc", tmp_path / "app"
    inc.mkdir()
    app.mkdir()
    (inc / "z.h").write_text(
        "enum { Z_INTERNAL = 1, Z_ARG = 2 };\nint z_fn(int);\n"
        "#define Z_LOG(x) z_fn(Z_INTERNAL + (x))\n"
    )
    (app / "m.c").write_text(
        "#include <z.h>\nint f(void) {\n    return Z_LOG(\n        Z_ARG);\n}\n"
    )
    r = parse(str(app / "m.c"), f"cc -I{inc} -c {app / 'm.c'}", {},
              lambda f: "m.c" if f.endswith("/m.c") else None, str(tmp_path))  # fmt: skip
    refs = sorted((x["name"], x["at"]) for x in r.refs)
    assert refs == [("Z_ARG", "m.c:4"), ("Z_LOG", "m.c:3")]


def test_value_changes_demoted_only_when_provably_unused_as_a_value(tmp_path: Path) -> None:
    """Review of P2, item 3: an enum/macro value change is info only when no use stores,
    passes, compares or narrows the value."""
    from zmig.analyzers.api import ApiAnalyzer
    from zmig.apidiff import parse
    from zmig.findings import Severity

    app = tmp_path / "app"
    app.mkdir()
    (app / "main.c").write_text(
        "#include <zephyr/v.h>\n"
        "void api(int v);\n"
        "unsigned long f(int v) {\n"
        "    int x = E_STORED;\n"
        "    api(E_PASSED);\n"
        "    if (v == E_COMPARED) { x++; }\n"
        "    char c = (char)E_NARROWED;\n"
        "    return sizeof(E_SIZE) + sizeof(M_SIZE) + x + c;\n"
        "}\n"
    )
    out = []
    for side, base in (("old", 1), ("new", 10)):
        inc = tmp_path / side / "include" / "zephyr"
        inc.mkdir(parents=True)
        names = ["E_STORED", "E_PASSED", "E_COMPARED", "E_NARROWED", "E_SIZE"]
        enum = ", ".join(f"{n} = {base + i}" for i, n in enumerate(names))
        (inc / "v.h").write_text(f"enum e {{ {enum} }};\n#define M_SIZE {base}u\n")
        r = parse(str(app / "main.c"), f"cc -I{tmp_path / side / 'include'} -c {app / 'main.c'}",
                  {}, lambda f, a=str(app): f[len(a) + 1:] if f.startswith(a + "/") else None,
                  str(tmp_path / side))  # fmt: skip
        out.append(
            {
                "file": "main.c",
                "decls": r.decls,
                "refs": r.refs,
                "diags": r.diags,
                "errors": r.errors,
            }
        )
    uses = {x["name"]: x.get("use") for x in out[0]["refs"] if "use" in x}
    assert uses == {
        "E_STORED": "stored",
        "E_PASSED": "passed",
        "E_COMPARED": "compared",
        "E_NARROWED": "narrowed",
        "E_SIZE": "other",
        "M_SIZE": "other",
    }
    ws = SimpleNamespace(zephyr=tmp_path, root=tmp_path / "new")
    ctx = SimpleNamespace(new=SimpleNamespace(workspace=ws), new_spec="new", scope=("s",))
    found = {
        f.subject: f.severity for f in ApiAnalyzer()._declaration_changes(ctx, [out[0]], [out[1]])
    }  # type: ignore[arg-type]
    assert found == {
        "E_STORED": Severity.RISKY,
        "E_PASSED": Severity.RISKY,
        "E_COMPARED": Severity.RISKY,
        "E_NARROWED": Severity.RISKY,
        "E_SIZE": Severity.INFO,
        "M_SIZE": Severity.INFO,
    }


def test_function_that_became_a_macro_at_the_target(tmp_path: Path) -> None:
    """ZMK calls k_ms_to_ticks_ceil64(), a macro at v3.5: crashed the diff once declaration
    tables held only referenced declarations (KeyError: 'type')."""
    from zmig.analyzers.api import ApiAnalyzer
    from zmig.findings import Severity

    src = [{"file": "a.c", "errors": [], "diags": [],
            "decls": {"k_ms": {"kind": "function", "type": "int (int)", "where": "t.h:3"}},
            "refs": [{"name": "k_ms", "kind": "function", "at": "a.c:4"}]}]  # fmt: skip
    tgt = [{"file": "a.c", "errors": [], "diags": [],
            "decls": {"k_ms": {"kind": "macro", "function_like": True, "arity": 1,
                               "where": "zephyr/include/t.h:9"}},
            "refs": [{"name": "k_ms", "kind": "macro", "at": "a.c:4"}]}]  # fmt: skip
    ws = SimpleNamespace(zephyr=tmp_path, root=tmp_path)
    ctx = SimpleNamespace(new=SimpleNamespace(workspace=ws), new_spec="new", scope=("s",))
    (f,) = ApiAnalyzer()._declaration_changes(ctx, src, tgt)  # type: ignore[arg-type]
    assert (f.kind, f.severity) == ("function-became-macro", Severity.INFO)


def test_parse_cache_key_ignores_build_and_workspace_location(tmp_path: Path) -> None:
    from zmig.analyzers.api import _object_of, _parse_key

    src = tmp_path / "main.c"
    src.write_text("int x;\n")
    deps = {
        "ws:zephyr/include/zephyr/kernel.h": "aa",
        "build:zephyr/include/generated/zephyr/autoconf.h": "bb",
    }

    def key(build: str, ws: str) -> str | None:
        b = SimpleNamespace(dir=Path(build), workspace=SimpleNamespace(root=Path(ws)), src=tmp_path)
        e = {
            "file": str(src),
            "command": f"cc -I{ws}/zephyr/include -I{build}/zephyr/include -c {src}",
        }
        return _parse_key("clang 18", e, b, deps)

    assert key("/c/builds/1", "/c/ws/a") == key("/c/builds/2", "/c/ws/b")
    assert key("/c/builds/1", "/c/ws/a") != _parse_key(
        "clang 18", {"file": str(src), "command": "cc -c x"},
        SimpleNamespace(dir=Path("/b"), workspace=SimpleNamespace(root=Path("/w")), src=tmp_path),
        {**deps, "ws:zephyr/include/zephyr/kernel.h": "changed"},
    )  # fmt: skip
    assert _parse_key("clang 18", {"file": str(src), "command": "cc"}, None, None) is None
    assert (
        _object_of({"file": "a.c", "command": "cc -o CMakeFiles/a.c.obj -c a.c"})
        == "CMakeFiles/a.c.obj"
    )


def test_parse_coverage_detail_is_the_same_on_a_warm_cache() -> None:
    """report.json is byte-stable whether or not the cache was warm (DESIGN.md, "Determinism"):
    "2 parsed, 0 from the parse cache" on a cold run vs "0 parsed, 2 from the parse
    cache" on the next failed test_deterministic once a build-key change made the
    fixture's builds cold."""
    from zmig.analyzers.api import coverage_detail

    cold = coverage_detail("clang 18", (1, 0), (1, 0), {"parsed": 2, "cached": 0, "unchanged": 0})
    warm = coverage_detail("clang 18", (1, 0), (1, 0), {"parsed": 0, "cached": 2, "unchanged": 0})
    assert cold == warm
    assert "2 parsed" in cold and "1 project TU(s) with the source flags (0 with errors)" in cold


def test_a_function_declared_the_same_at_both_refs_is_not_removed(tmp_path: Path) -> None:
    """DOGFOOD-8 B3, CoffeeCaller: the target parse of leds.c had errors (missing devicetree
    aliases), so no reference to `led_strip_update_rgb` was recorded and it was reported as
    removed, although led_strip.h declares it identically at v4.2.0 and v4.4.0. A removed
    declaration (`smf_set_handled`, CANnectivity N1b) is still told apart."""
    from zmig.analyzers.api import _declaration_text

    decl = (
        "static inline int led_strip_update_rgb(const struct device *dev,\n"
        "\t\t\t\t       struct led_rgb *pixels,\n"
        "\t\t\t\t       size_t num_pixels)\n{\n"
        "\treturn led_strip_update_rgb(dev, pixels, num_pixels);\n}\n"
    )
    where = "zephyr/include/zephyr/drivers/led_strip.h:107"
    # The doc comments mention the function first and are reworded between the versions
    # (v4.2.0 led_strip.h:60 vs v4.4.0 :70); only the declaration counts.
    old_doc = "/**\n * @see led_strip_update_rgb() for argument descriptions.\n */\n"
    new_doc = "/**\n * See @a led_strip_update_rgb() for argument descriptions.\n */\n\n"
    for side, pre in (("old", old_doc), ("new", new_doc)):
        h = tmp_path / side / "zephyr" / "include" / "zephyr" / "drivers" / "led_strip.h"
        h.parent.mkdir(parents=True)
        h.write_text(pre + decl)
    a = _declaration_text(tmp_path / "old", where, "led_strip_update_rgb")
    assert a == (
        "static inline int led_strip_update_rgb(const struct device *dev, "
        "struct led_rgb *pixels, size_t num_pixels)"
    )
    assert a == _declaration_text(tmp_path / "new", where, "led_strip_update_rgb")
    smf = tmp_path / "new" / "zephyr" / "include" / "zephyr" / "smf.h"
    smf.write_text("enum smf_state_result { A };\n")
    assert (
        _declaration_text(tmp_path / "new", "zephyr/include/zephyr/smf.h:9", "smf_set_handled")
        is None
    )
    assert _declaration_text(tmp_path / "new", "zephyr/include/zephyr/gone.h:3", "x") is None


def test_compiler_builtin_headers_are_not_zephyr_api(tmp_path: Path) -> None:
    """DOGFOOD-8 B3, ardep (`modules/iso14229/drv.c`): `va_start` was reported as "2
    parameters -> 1, update every use". Both declarations were clang's own builtin
    `__stdarg_va_arg.h`, whose C23 branch has the 1-parameter form; GCC never reads those
    headers. Declarations in clang's resource directory aren't Zephyr's or a module's."""
    from zmig.analyzers.api import ApiAnalyzer
    from zmig.apidiff import parse, resource_dir

    if resource_dir() is None:
        pytest.skip("no clang resource directory")
    app = tmp_path / "app"
    app.mkdir()
    (app / "main.c").write_text(
        "#include <stdarg.h>\nint f(int n, ...) { va_list ap; va_start(ap, n);\n"
        "int v = va_arg(ap, int); va_end(ap); return v; }\n"
    )
    out = []
    for std in ("c11", "c2x"):
        r = parse(str(app / "main.c"), f"cc -std={std} -c {app / 'main.c'}", {str(app): ""},
                  lambda f, a=str(app): f[len(a) + 1 :] if f.startswith(a + "/") else None,
                  str(tmp_path))  # fmt: skip
        assert r.decls["va_start"]["kind"] == "builtin", r.decls["va_start"]
        fields = ("decls", "refs", "diags", "fn_values", "errors", "dropped")
        out.append({"file": "main.c", **{k: getattr(r, k) for k in fields}})
    ws = SimpleNamespace(zephyr=tmp_path, root=tmp_path)
    ctx = SimpleNamespace(old=SimpleNamespace(workspace=ws), new=SimpleNamespace(workspace=ws),
                          new_spec="new", scope=("s",))  # fmt: skip
    assert ApiAnalyzer()._declaration_changes(ctx, [out[0]], [out[1]]) == []  # type: ignore[arg-type]
    # ZMK (zephyr fork v3.2 -> v3.5): size_t from Zephyr's libc header at the source and
    # clang's stddef.h at the target is not a removal.
    src = dict(out[0], decls={"size_t": {"kind": "typedef", "type": "unsigned int",
                                         "where": "lib/libc/minimal/include/stddef.h:9"}},
               refs=[{"name": "size_t", "kind": "typedef", "at": "main.c:2"}])  # fmt: skip
    tgt = dict(out[1], decls={"size_t": {"kind": "builtin", "where": "/clang/include/stddef.h:1"}},
               refs=[{"name": "size_t", "kind": "typedef", "at": "main.c:2"}])  # fmt: skip
    assert ApiAnalyzer()._declaration_changes(ctx, [src], [tgt]) == []  # type: ignore[arg-type]


def test_compatibility_macros_are_followed_not_reported(tmp_path: Path) -> None:
    """DOGFOOD-12 E2, ble-copro: Zephyr 4.4 namespaced the network API and keeps the old
    names as macros (net_compat.h: `#define sockaddr_in net_sockaddr_in`, `in_addr`,
    `sa_family_t`, `IPPROTO_TCP`). 13 findings said fields were removed, signatures changed
    and enum constants became macros, for code that compiles unchanged. An old name that
    is an alias at the target is compared with what it names; a real change through the
    alias is still reported."""
    from zmig.analyzers.api import ApiAnalyzer

    def tu(decls: dict[str, Any], refs: list[tuple[str, str]]) -> dict[str, Any]:
        return {"file": "main.c", "errors": [], "diags": [], "fn_values": [], "decls": decls,
                "refs": [{"name": n, "kind": k, "at": "main.c:5"} for n, k in refs]}  # fmt: skip

    def alias(to: str) -> dict[str, Any]:
        return {"kind": "macro", "function_like": False, "tokens": to, "where": "net_compat.h:1"}

    def field(t: str) -> dict[str, Any]:
        return {"kind": "field", "type": t, "offset": 32, "where": "net_ip.h:1"}

    def enum(v: int) -> dict[str, Any]:
        return {"kind": "enum", "value": v, "type": "int", "where": "net_ip.h:1"}

    def typed(kind: str, t: str) -> dict[str, Any]:
        return {"kind": kind, "type": t, "where": "net_ip.h:1"}

    fn = "struct net_if_addr *(struct net_if *, const struct {}in_addr *)"
    used = [
        ("sockaddr_in.sin_addr", "field"),
        ("sa_family_t", "typedef"),
        ("IPPROTO_TCP", "enum"),
        ("net_if_ipv4_addr_add", "function"),
        ("SOCK_DGRAM", "enum"),
        ("K_THREAD_STACK_SIZEOF", "macro"),
        ("net_addr.<anonymous>.in_addr", "field"),
        ("in_port_t", "typedef"),
    ]
    src = tu(
        {
            "sockaddr_in.sin_addr": field("struct in_addr"),
            "sa_family_t": typed("typedef", "unsigned short"),
            "IPPROTO_TCP": enum(6),
            "SOCK_DGRAM": enum(2),
            "net_if_ipv4_addr_add": typed("function", fn.format("")),
            # A macro that's an alias of a function-like macro at the target still expands
            # the same way: CANnectivity's K_THREAD_STACK_SIZEOF (the regression the first
            # version of this fix made).
            "K_THREAD_STACK_SIZEOF": {
                "kind": "macro",
                "function_like": False,
                "vtype": "int",
                "vsize": 4,
                "where": "thread_stack.h:292",
            },
            # `#define in_addr net_in_addr` renames the union member too.
            "net_addr.<anonymous>.in_addr": field("struct in_addr"),
            # Never named by the project at the target, so known only from the headers.
            "in_port_t": typed("typedef", "unsigned short"),
        },
        used,
    )
    tgt = tu(
        {
            "sockaddr_in": alias("net_sockaddr_in"),
            "in_addr": alias("net_in_addr"),
            "sa_family_t": alias("net_sa_family_t"),
            "IPPROTO_TCP": alias("NET_IPPROTO_TCP"),
            "SOCK_DGRAM": alias("NET_SOCK_DGRAM"),
            "net_sockaddr_in.sin_addr": field("struct net_in_addr"),
            "net_sa_family_t": typed("typedef", "unsigned short"),
            "NET_IPPROTO_TCP": enum(6),
            "NET_SOCK_DGRAM": enum(3),
            "net_if_ipv4_addr_add": typed("function", fn.format("net_")),
            "K_THREAD_STACK_SIZEOF": alias("Z_THREAD_STACK_SIZEOF"),
            "Z_THREAD_STACK_SIZEOF": {
                "kind": "macro",
                "function_like": True,
                "arity": 1,
                "where": "thread_stack.h:282",
            },
            "net_addr.<anonymous>.net_in_addr": field("struct net_in_addr"),
        },
        used,
    )
    compat = tmp_path / "include" / "zephyr" / "net" / "net_compat.h"
    compat.parent.mkdir(parents=True)
    compat.write_text("#define in_port_t     net_in_port_t\n")
    ws = SimpleNamespace(zephyr=tmp_path, root=tmp_path)
    ctx = SimpleNamespace(old=SimpleNamespace(workspace=ws), new=SimpleNamespace(workspace=ws),
                          new_spec="v4.4.2", scope=("s",))  # fmt: skip
    found = ApiAnalyzer()._declaration_changes(ctx, [src], [tgt])  # type: ignore[arg-type]
    # Only the value that really changed through its alias (SOCK_DGRAM 2 -> 3).
    assert [(f.kind, f.subject) for f in found] == [("enum-changed", "SOCK_DGRAM")]
