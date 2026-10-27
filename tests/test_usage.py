from __future__ import annotations

from pathlib import Path

from zmig.usage import NodeRef, dt_token, resolve, scan

APP = Path(__file__).parent / "fixtures" / "app"


def test_fixture_app_usage() -> None:
    u = scan(APP)
    ((loc, value),) = u.kconfig_set["CPLUSPLUS"]
    assert (loc.path, loc.line, value) == ("prj.conf", 3, "y")
    assert (
        u.kconfig_set["ASSERT"][0][0].path,
        u.kconfig_set["ASSERT"][0][0].line,
    ) == ("boards/native_sim_64.conf", 1)
    assert [loc.line for loc in u.kconfig_read["BOARD"]] == [9]
    refs = {(r, loc.path, loc.line) for r, loc in u.dt_nodes}
    assert (NodeRef("tokens", "gpio_800"), "src/main.c", 4) in refs
    assert (NodeRef("label", "can0"), "src/main.c", 5) in refs
    assert (NodeRef("label", "can0"), "boards/native_sim_64.overlay", 1) in refs
    # DT_PROP(CAN_NODE, ...) resolves the #define to DT_NODELABEL(can0).
    assert [(r, p, loc.line) for r, p, loc in u.dt_props_read] == [
        (NodeRef("label", "can0"), "sample_point", 13)
    ]
    assert [(r, p, loc.line) for r, p, loc in u.dt_props_set] == [
        (NodeRef("label", "can0"), "bus-speed", 2)
    ]


def test_overlay_scanner_nesting_and_comments(tmp_path: Path) -> None:
    (tmp_path / "app.overlay").write_text(
        "/* &not_a_ref { */\n"
        "/ {\n"
        "\tsensor: sensor@40 {\n"
        '\t\tcompatible = "vnd,thing";\n'
        "\t};\n"
        "};\n"
        "&i2c0 { child@1 { reg = <1>; }; };\n"
        "&{/soc/uart@1000} {\n"
        "\tcurrent-speed = <9600>; // fast\n"
        "};\n"
    )
    u = scan(tmp_path)
    nodes = [(r, loc.line) for r, loc in u.dt_nodes]
    assert (NodeRef("path", "/sensor@40"), 3) in nodes
    assert (NodeRef("label", "i2c0", "child@1"), 7) in nodes
    assert (NodeRef("path", "/soc/uart@1000"), 8) in nodes
    assert not any(r.value == "not_a_ref" for r, _ in nodes)
    props = {(r.value, p) for r, p, _ in u.dt_props_set}
    assert ("/soc/uart@1000", "current-speed") in props and ("i2c0", "reg") in props
    assert [loc.line for loc in u.dt_compats["vnd_thing"]] == [4]


def test_resolve() -> None:
    edt = {
        "nodes": {
            "/": {"labels": [], "aliases": []},
            "/gpio@800": {"labels": ["gpio0"], "aliases": ["led-port"]},
            "/i2c@0": {"labels": ["i2c0"], "aliases": []},
            "/i2c@0/child@1": {"labels": [], "aliases": []},
        }
    }
    assert resolve(NodeRef("label", "gpio0"), edt) == "/gpio@800"
    assert resolve(NodeRef("tokens", "gpio_800"), edt) == "/gpio@800"
    assert resolve(NodeRef("alias", "led_port"), edt) == "/gpio@800"
    assert resolve(NodeRef("label", "i2c0", "child@1"), edt) == "/i2c@0/child@1"
    assert resolve(NodeRef("label", "nope"), edt) is None
    assert dt_token("zephyr,native-linux-can") == "zephyr_native_linux_can"
