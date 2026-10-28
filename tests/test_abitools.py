"""P2 optional tools (docs/DESIGN-P2.md §6, §10.5): both the tools-present and the
tools-absent paths."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from zmig import abitools
from zmig.buildctx import Artifact

HAVE = shutil.which("gcc") and shutil.which("ar")


def _cc(tmp: Path, name: str, src: str, *flags: str) -> Path:
    c = tmp / f"{name}.c"
    c.write_text(src)
    out = tmp / name
    subprocess.run(["gcc", "-g", *flags, "-o", str(out), str(c)], check=True)
    return out


@pytest.mark.skipif(not (HAVE and shutil.which("pahole")), reason="needs gcc and pahole")
def test_pahole_layouts_present(tmp_path: Path) -> None:
    obj = _cc(tmp_path, "a.o", "struct s { char a; int b; unsigned e : 3; }; struct s g;\n", "-c")
    assert abitools.pahole_layouts(obj, ["s"]) == {"s": {"a": 0, "b": 32, "e": 64}}


@pytest.mark.skipif(not (HAVE and shutil.which("abidiff")), reason="needs gcc and abidiff")
def test_abidiff_on_a_boundary_present(tmp_path: Path) -> None:
    old = "int api(int a) { return a; }\nint other(int a) { return a; }\n"
    new = "long api(long a) { return a; }\nint other(int a) { return a; }\n"
    v1 = _cc(tmp_path, "v1.so", old, "-shared", "-fPIC")
    v2 = _cc(tmp_path, "v2.so", new, "-shared", "-fPIC")
    obj = _cc(tmp_path, "user.o", "int api(int);\nint use(void) { return api(1); }\n", "-c")
    lib = tmp_path / "libuser.a"
    subprocess.run(["ar", "rcs", str(lib), str(obj)], check=True)
    assert abitools.undefined_symbols(lib) == {"api"}
    changes = abitools.abidiff_changes(v1, v2, {"api"})
    assert changes and all("api" in c for c in changes)
    assert not any("other" in c for c in changes)


def test_tools_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(abitools, "tool", lambda name: None)
    assert abitools.pahole_layouts(tmp_path / "x.elf", ["s"]) is None
    assert abitools.abidiff_changes(tmp_path / "a", tmp_path / "b", {"api"}) is None


def test_analyzer_reports_missing_tools_as_not_checked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from zmig.analyzers import api

    monkeypatch.setattr(abitools, "tool", lambda name: None)
    app = tmp_path / "app"
    (app / "lib").mkdir(parents=True)
    (app / "lib" / "libvendor.a").write_bytes(b"!<arch>\n")
    imgs = []
    for side in ("old", "new"):
        d = tmp_path / side
        (d / "zephyr").mkdir(parents=True)
        (d / "zephyr" / "zephyr.elf").write_bytes(b"\x7fELF")
        (d / "build.ninja").write_text(f"build zephyr/zephyr.elf: link a.o {app}/lib/libvendor.a\n")
        imgs.append(
            SimpleNamespace(
                dir=d,
                has=lambda a, d=d: (d / a.value).is_file(),
                artifact=lambda a, d=d: d / a.value,
            )
        )
    b = SimpleNamespace(src=app, workspace=SimpleNamespace(root=tmp_path / "ws"))
    ctx = SimpleNamespace(
        old=b, new=b, old_img=imgs[0], new_img=imgs[1], new_spec="new", scope=("s",)
    )
    tu = {"decls": {"s.b": {"kind": "field", "offset": 32}}}
    findings, gaps = api.ApiAnalyzer()._binary_checks(ctx, [tu], [tu])  # type: ignore[arg-type]
    assert findings == []
    areas = {g.area: g.reason for g in gaps}
    assert (
        "pahole isn't installed" in areas["struct layouts against the toolchain's DWARF (pahole)"]
    )
    assert areas["ABI seen by lib/libvendor.a"] == "abidiff isn't installed (abigail-tools)"
    assert Artifact.ELF.value == "zephyr/zephyr.elf"
