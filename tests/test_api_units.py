"""P2 (docs/DESIGN-P2.md) unit tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

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
