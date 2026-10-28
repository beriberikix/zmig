"""P2 (docs/DESIGN-P2.md) unit tests."""

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
                "errors": r.errors,
                "dropped": r.dropped,
            }
        )
    ctx = SimpleNamespace(
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
    assert found["s.used_field"].severity is Severity.RISKY  # type changed
    assert found["USED_C"].severity is Severity.RISKY  # value changed
    assert found["USED_FM"].severity is Severity.BREAKING  # arity changed
    # 32 -> 64 bit and stored in an unsigned int: the value changes (G1b's shape).
    assert found["USED_M"].severity is Severity.BREAKING
    assert any(e.get("line") == 5 for e in found["USED_M"].evidence)
    # Every finding points at a project line.
    assert all(any(e["type"] == "file_line" for e in f.evidence) for f in found.values())


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
