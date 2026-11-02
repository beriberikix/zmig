"""`zmig scan --pin-source REPORT` (DESIGN.md, "Pinned-source scans"): reading and checking the
pinned report, loading its source build from the cache, and compare's refusals. Nothing
builds."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from zmig.buildctx import BuildSpec, _key_of, key_inputs, load_build
from zmig.cache import Cache
from zmig.cli import build_parser
from zmig.compare import mismatches
from zmig.compare import run as compare_run
from zmig.errors import UsageError
from zmig.pin import mismatches as pin_mismatches
from zmig.pin import read_pin
from zmig.tools import Runner

FIX = Path(__file__).parent / "fixtures" / "watch"
JSON = dict[str, Any]


def report() -> JSON:
    """A real scan report (cannectivity-c1), as this zmig writes it (1.7, with sides)."""
    r: JSON = json.loads((FIX / "cannectivity-c1.json").read_text())
    r["schema_version"] = "1.7"
    r["environment"]["zmig_build"] = "0123456789abcdef"
    sides = ["source"] + ["target"] * (len(r["contexts"]) - 1)
    for c, side in zip(r["contexts"], sides, strict=True):
        c["side"] = side
    return r


def write(tmp_path: Path, r: JSON, name: str = "before.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(r))
    return p


def pinned(before: JSON, key: str | None = None) -> JSON:
    """AFTER: a scan pinned to `before`'s source build."""
    after = copy.deepcopy(before)
    src = before["contexts"][0]["key"]
    after["inputs"]["pinned_source"] = {
        "report": "before/report.json",
        "key": key or src,
        "app_tree_hash": before["inputs"]["app_tree_hash"],
        "zmig_build": before["environment"]["zmig_build"],
    }
    after["contexts"][0]["pinned"] = True
    return after


# --- read_pin -----------------------------------------------------------------------------


def test_reads_a_scan_report(tmp_path: Path) -> None:
    r = report()
    pin = read_pin(write(tmp_path, r))
    assert pin.key == r["contexts"][0]["key"]
    assert pin.app_tree_hash == r["inputs"]["app_tree_hash"]
    assert pin.to_json()["report"] == "before.json"  # an absolute path: the file name


def _refused(tmp_path: Path, r: Any, match: str) -> None:
    p = tmp_path / "r.json"
    p.write_text(r if isinstance(r, str) else json.dumps(r))
    with pytest.raises(UsageError, match=match):
        read_pin(p)


def test_refuses_missing_file(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="no such file"):
        read_pin(tmp_path / "nope.json")


def test_refuses_unreadable_and_foreign_files(tmp_path: Path) -> None:
    _refused(tmp_path, "{", "not a readable JSON report")
    _refused(tmp_path, {"a": 1}, "not a zmig report")


def test_refuses_a_non_scan_report(tmp_path: Path) -> None:
    r = report()
    r["command"] = "watch"
    _refused(tmp_path, r, "not a scan")


@pytest.mark.parametrize("version", ["1.6", "1.5", "2.0", "x"])
def test_refuses_a_report_older_than_1_7(tmp_path: Path, version: str) -> None:
    r = report()
    r["schema_version"] = version
    _refused(tmp_path, r, "1.7 or later")


def test_refuses_a_pinned_report(tmp_path: Path) -> None:
    _refused(tmp_path, pinned(report()), "itself pinned")


def test_refuses_a_source_build_that_failed(tmp_path: Path) -> None:
    r = report()
    r["contexts"][0]["status"] = "configure-failed"
    _refused(tmp_path, r, "not ok")


def test_pins_the_effective_source_after_module_recovery(tmp_path: Path) -> None:
    """Module recovery rebuilt the source: the pin is the rebuilt one, with its modules."""
    r = report()
    first = {**r["contexts"][0], "key": "superseded0000", "superseded_by_module_recovery": ["x"]}
    r["contexts"][0]["modules_added"] = ["x"]
    r["contexts"].insert(0, first)
    pin = read_pin(write(tmp_path, r))
    assert pin.key == r["contexts"][1]["key"]
    assert pin.modules_added == ("x",)


# --- this scan's inputs against the pin -------------------------------------------------


def _inputs(r: JSON) -> JSON:
    return {**r["inputs"], "zmig_build": r["environment"]["zmig_build"]}


def test_same_inputs_match(tmp_path: Path) -> None:
    r = report()
    assert pin_mismatches(read_pin(write(tmp_path, r)), _inputs(r)) == []


@pytest.mark.parametrize(
    ("field", "change"),
    [
        ("app", lambda i: {**i, "app": "/elsewhere"}),
        ("from", lambda i: {**i, "from": {**i["from"], "sha": "f" * 40}}),
        ("to", lambda i: {**i, "to": {**i["to"], "sha": "f" * 40}}),
        ("board", lambda i: {**i, "board": {**i["board"], "from": "other"}}),
        ("to-board", lambda i: {**i, "board": {**i["board"], "to": "other"}}),
        ("build mode", lambda i: {**i, "build_mode": "fast"}),
        ("sysbuild", lambda i: {**i, "sysbuild": not i.get("sysbuild")}),
        ("zmig build", lambda i: {**i, "zmig_build": "fedcba9876543210"}),
    ],
)
def test_each_input_difference_is_named(tmp_path: Path, field: str, change: Any) -> None:
    r = report()
    (diff,) = pin_mismatches(read_pin(write(tmp_path, r)), change(_inputs(r)))
    assert diff.startswith(f"{field}: ")


# --- loading the build by key -----------------------------------------------------------


def _ws(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        root=tmp_path / "ws",
        resolved=SimpleNamespace(ref=SimpleNamespace(key={"sha": "1" * 40}, label="v1")),
        modules=lambda runner: [],
    )


def _cached(tmp_path: Path, spec: BuildSpec) -> tuple[Cache, str, Path]:
    """A cache holding a build of `spec`, as build() leaves it, and the app copy it used."""
    cache = Cache(tmp_path / "cache")
    cache.ensure()
    inputs = key_inputs(_ws(tmp_path), spec, [])  # type: ignore[arg-type]
    key = _key_of(inputs)
    bdir = cache.builds / key
    bdir.mkdir()
    image = {"name": "app", "role": "app", "role_source": "x", "dir": str(bdir),
             "log": str(bdir / "app.log"), "status": "ok"}  # fmt: skip
    (cache.builds / f"{key}.json").write_text(
        json.dumps({"status": "ok", "images": [image], "key_inputs": inputs})
    )
    src = tmp_path / "ws" / "apps" / "0123456789abcdef" / spec.app.name
    src.mkdir(parents=True)
    return cache, key, src


def _app(tmp_path: Path, text: str = "int main(void) { return 0; }\n") -> Path:
    app = tmp_path / "app"
    (app / "src").mkdir(parents=True, exist_ok=True)
    (app / "src" / "main.c").write_text(text)
    return app


def test_loads_the_build_for_the_edited_app(tmp_path: Path) -> None:
    """The edited app has another tree hash (another key), but the build is loaded by the
    pinned key, with `src` the unedited copy and `spec.app` the live app."""
    app = _app(tmp_path)
    cache, key, src = _cached(tmp_path, BuildSpec(app=app, board="b"))
    _app(tmp_path, "int main(void) { return 1; }\n")  # the edit
    b, why = load_build(_ws(tmp_path), BuildSpec(app=app, board="b"), key, src, cache, Runner())  # type: ignore[arg-type]
    assert why == []
    assert b is not None and b.key == key and b.src == src and b.spec.app == app
    assert b.status == "ok" and b.app.name == "app"


def test_refuses_an_evicted_build_or_copy(tmp_path: Path) -> None:
    app = _app(tmp_path)
    cache, key, src = _cached(tmp_path, BuildSpec(app=app, board="b"))
    spec = BuildSpec(app=app, board="b")
    b, why = load_build(_ws(tmp_path), spec, "0" * 16, src, cache, Runner())  # type: ignore[arg-type]
    assert b is None and "no longer in the cache" in why[0]
    b, why = load_build(_ws(tmp_path), spec, key, tmp_path / "gone", cache, Runner())  # type: ignore[arg-type]
    assert b is None and "app copy" in why[0]


def test_refuses_a_build_with_other_inputs(tmp_path: Path) -> None:
    app = _app(tmp_path)
    cache, key, src = _cached(tmp_path, BuildSpec(app=app, board="b"))
    other = BuildSpec(app=app, board="b", cmake_args=("-DX=1",))
    b, why = load_build(_ws(tmp_path), other, key, src, cache, Runner())  # type: ignore[arg-type]
    assert b is None and why == ["cmake_args differs"]


# --- compare ------------------------------------------------------------------------------


def test_compare_accepts_after_pinned_to_before() -> None:
    before = report()
    after = pinned(before)
    assert mismatches(before, after) == []


def test_compare_refuses_after_pinned_elsewhere() -> None:
    before = report()
    (diff,) = mismatches(before, pinned(before, key="feedfacefeedface"))
    assert "AFTER is pinned to source build 'feedfacefeedface'" in diff


def test_compare_refuses_a_pinned_before() -> None:
    before = report()
    after = pinned(before)
    diffs = mismatches(after, copy.deepcopy(after))
    assert any("BEFORE is a pinned scan" in d for d in diffs)


def test_compare_json_says_pinned(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = report()
    b, a = write(tmp_path, before), write(tmp_path, pinned(before), "after.json")
    args = build_parser().parse_args(["compare", str(b), str(a), "--format", "json"])
    assert compare_run(args) == 0
    assert json.loads(capsys.readouterr().out)["pinned"] is True


def test_scan_takes_pin_source() -> None:
    args = build_parser().parse_args(
        [
            "scan",
            "--app",
            "a",
            "--board",
            "b",
            "--from",
            "x",
            "--to",
            "y",
            "--pin-source",
            "before/report.json",
        ]
    )
    assert args.pin_source == Path("before/report.json")


def test_only_scan_takes_pin_source() -> None:
    """fix and watch reuse scan_report but not the option (DESIGN.md, "Pinned-source scans")."""
    import argparse

    sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))

    def options(p: argparse.ArgumentParser) -> set[str]:
        out = {o for a in p._actions for o in a.option_strings}
        for a in p._actions:
            if isinstance(a, argparse._SubParsersAction):
                for child in a.choices.values():
                    out |= options(child)
        return out

    assert "--pin-source" in options(sub.choices["scan"])
    assert "--pin-source" not in options(sub.choices["fix"])
    assert "--pin-source" not in options(sub.choices["watch"])


# --- pinned-mode rules (piece 3) ----------------------------------------------------------


def test_pinned_build_files_include_the_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    """A file the edit added is compiled only by the target: pinned, it counts as used."""
    from zmig import scan

    deps = {
        "old": {"a.obj": ["/s/app/src/main.c"]},
        "new": {"a.obj": ["/t/app/src/main.c"], "b.obj": ["/t/app/src/added.c"]},
    }
    monkeypatch.setattr(scan, "build_dependencies", lambda img, runner: deps[img.dir])
    monkeypatch.setattr(
        scan, "project_roots", lambda b, d: {"/s/app": ""} if d == "old" else {"/t/app": ""}
    )

    def files(pinned: bool) -> set[str] | None:
        fake = SimpleNamespace(
            pinned=pinned,
            old=SimpleNamespace(),
            new=SimpleNamespace(),
            old_img=SimpleNamespace(dir="old"),
            new_img=SimpleNamespace(dir="new"),
            runner=None,
        )
        return scan.ScanContext.build_files.func(fake)  # type: ignore[attr-defined,no-any-return]

    assert files(False) == {"src/main.c"}
    assert files(True) == {"src/main.c", "src/added.c"}


def test_moved_input_is_checked_in_the_live_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """input-not-applied, pinned: a fragment the unedited source applied and the user has
    since moved (the usual fix) no longer exists at that path, so it isn't reported."""
    from zmig.analyzers import buildlog

    copy, app, mod = (
        tmp_path / "ws" / "apps" / "0123456789abcdef" / "app",
        tmp_path / "app",
        tmp_path / "mod",
    )
    for d in (copy / "boards", app / "boards", mod / "boards"):
        d.mkdir(parents=True)
    monkeypatch.setattr(buildlog, "project_roots", lambda b, d: {str(copy): "", str(mod): "mod/"})
    ws = SimpleNamespace(root=tmp_path / "ws")
    ctx = SimpleNamespace(
        old=SimpleNamespace(src=copy, spec=SimpleNamespace(app=app), workspace=ws),
        old_img=SimpleNamespace(dir=tmp_path),
    )
    (copy / "boards" / "b.conf").write_text("")  # in the unedited copy only: moved
    assert not buildlog._live_exists(ctx, "boards/b.conf")  # type: ignore[arg-type]
    (app / "boards" / "b.conf").write_text("")
    assert buildlog._live_exists(ctx, "boards/b.conf")  # type: ignore[arg-type]
    assert not buildlog._live_exists(ctx, "mod/boards/x.conf")  # type: ignore[arg-type]
    (mod / "boards" / "x.conf").write_text("")
    assert buildlog._live_exists(ctx, "mod/boards/x.conf")  # type: ignore[arg-type]
