"""Cache-miss reasons (debuggability fix): each cache marker records the build key's inputs,
and a miss names the inputs that differ from the closest cached build of the same app.
The key itself is unchanged."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from zmig.buildctx import _key_of, miss_reason

INPUTS: dict[str, Any] = {
    "recipe": 12,
    "zmig": "0.1.0",
    "python": {
        "executable": "/v/bin/python",
        "prefix": "/v",
        "version": "3.12.13",
        "packages": "aaaa",
    },
    "ref": {"sha": "1" * 40},
    "modules": [["zcbor", "2" * 40]],
    "board": "native_sim_64",
    "app": "3" * 64,
    "mode": "full",
    "sysbuild": False,
    "cmake_args": [],
    "sdk": None,
}


def _marker(builds: Path, name: str, app: Path, inputs: dict[str, Any] | None) -> None:
    meta: dict[str, Any] = {"status": "ok", "spec": {"app": str(app)}, "images": []}
    if inputs is not None:
        meta["key_inputs"] = inputs
    (builds / f"{name}.json").write_text(json.dumps(meta))


def test_the_key_is_the_same_hash_as_before() -> None:
    old_formula = hashlib.sha256(json.dumps(INPUTS, sort_keys=True).encode()).hexdigest()[:16]
    assert _key_of(INPUTS) == old_formula


def test_names_what_changed_against_the_closest_build(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    other = {**INPUTS, "ref": {"sha": "9" * 40}, "board": "x", "zmig": "0.0.9"}
    _marker(tmp_path, "far", app, other)
    _marker(tmp_path, "close", app, INPUTS)
    _marker(tmp_path, "legacy", app, None)  # from before inputs were recorded: ignored
    _marker(tmp_path, "elsewhere", tmp_path / "other-app", {**INPUTS, "sdk": {"path": "/s"}})
    now = {**INPUTS, "python": {**INPUTS["python"], "prefix": "/w", "packages": "bbbb"}}
    assert miss_reason(tmp_path, app, now) == "package digest changed, venv changed"
    sdk = {**INPUTS, "sdk": {"path": "/sdk", "version": "0.16.8"}}
    assert miss_reason(tmp_path, app, sdk) == "SDK changed"
    assert (
        miss_reason(tmp_path, app, {**INPUTS, "modules": [["zcbor", "4" * 40]]})
        == "modules changed"
    )


def test_no_recorded_build_of_this_app(tmp_path: Path) -> None:
    app = tmp_path / "app"
    _marker(tmp_path, "legacy", app, None)
    assert miss_reason(tmp_path, app, INPUTS).startswith("no earlier build of this app")


def test_copies_with_different_directory_names_do_not_share_a_build(tmp_path: Path) -> None:
    """The app copy a build compiles is apps/<tree hash>/<directory name>, so its ninja
    records and image names carry the name. Keyed on content alone, tests/fixtures/app and
    an identical copy named fixture-app shared one build: every source looked uncompiled,
    and the scan reported phantom input-added and image-added/removed findings."""
    from types import SimpleNamespace

    from zmig.buildctx import _INPUT_NAMES, BuildSpec, build_key

    a, b, c = tmp_path / "x" / "app", tmp_path / "y" / "fixture-app", tmp_path / "z" / "app"
    for d in (a, b, c):
        (d / "src").mkdir(parents=True)
        (d / "src" / "main.c").write_text("int main(void) { return 0; }\n")
        (d / "prj.conf").write_text("")
    ws = SimpleNamespace(resolved=SimpleNamespace(ref=SimpleNamespace(key={"sha": "1" * 40})))

    def key(app: Path) -> str:
        return build_key(ws, BuildSpec(app=app, board="native_sim"), [])  # type: ignore[arg-type]

    assert key(a) != key(b)
    assert key(a) == key(c)  # same content and name, wherever it lives: one build
    assert "app_name" in _INPUT_NAMES
