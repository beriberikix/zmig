"""Kconfig-diagnostic findings (kconfig-override-orphaned, kconfig-name-collision) list the
target's own warning as tool_output evidence, with the log and line it is in, not only in
`new.message`. Regression: ZMK's BT_LL_SW_LLCP_IMPL finding (its warning came from a
salvage round's log)."""

from __future__ import annotations

# ruff: noqa: E501  (verbatim log lines)
import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema

from zmig.analyzers.buildlog import BuildlogAnalyzer
from zmig.buildctx import SalvageEdit
from zmig.tools import Runner

WARNING = (
    "warning: <choice BT_LL_SW_LLCP_IMPL> (defined at /home/user/.cache/zmig/workspaces/"
    "339570e2393b108bf8f9241018ecaf5615c25469/apps/c81d0237f8e705d7/app/Kconfig:175) "
    "defined with type unknown"
)
SCHEMA = json.loads((Path(__file__).parents[1] / "zmig/schema/report-1.json").read_text())


def test_the_warning_is_evidence_with_its_log_line(tmp_path: Path) -> None:
    first = tmp_path / "first" / "app.log"  # the first configure stopped on another error
    first.parent.mkdir()
    first.write_text(
        "Parsing Kconfig\nwarning: SSD1306_REVERSE_MODE (defined at …) defined without a type\n"
    )
    round2 = tmp_path / "round2" / "app.log"
    round2.parent.mkdir()
    round2.write_text(
        "Parsing Kconfig\nLoaded configuration '…'\n" + WARNING + "\nerror: Aborting\n"
    )
    ctx = SimpleNamespace(runner=Runner())
    ev = BuildlogAnalyzer._warning_evidence(ctx, WARNING, [first, round2])  # type: ignore[arg-type]
    assert ev == {
        "type": "tool_output",
        "invocation": "target configure (Kconfig)",
        "excerpt": WARNING,
        "log": str(round2),
        "log_line": 3,
    }
    defs = SCHEMA.get("$defs") or SCHEMA["definitions"]
    jsonschema.validate(ev, {**defs["evidence"], "$defs": defs, "definitions": defs})
    # Not in any log the scan kept: still evidence, without a location.
    lost = BuildlogAnalyzer._warning_evidence(ctx, WARNING, [first])  # type: ignore[arg-type]
    assert lost["excerpt"] == WARNING and "log" not in lost


def test_salvage_edits_record_their_log() -> None:
    e = SalvageEdit("Kconfig", 175, "choice BT_LL_SW_LLCP_IMPL", WARNING, "/b/r2/zmig-logs/app.log")
    assert e.to_json()["log"] == "/b/r2/zmig-logs/app.log"
    assert "log" not in SalvageEdit("Kconfig", 175, "x", WARNING).to_json()


# RIT FSW sensor_module at v4.2.0 (DOGFOOD-8 B4), paths shortened; verbatim otherwise.
KCONFIG_ABORT_LOG = """\
warning: ENTROPY_STM32_RNG (defined at drivers/entropy/Kconfig.stm32:6) was assigned the value 'y'
but got the value 'n'. Check these unsatisfied dependencies: (DT_HAS_ST_STM32_RNG_ENABLED ||
DT_HAS_ST_STM32_RNG_NOIRQ_ENABLED) (=n).

/src/FSW/boards/arm/sensor_module/sensor_module_defconfig:33: warning: attempt to assign the value 'y' to the undefined symbol TMP116
Parsing /ws/apps/x/sensor_module/Kconfig
Loaded configuration '/src/FSW/boards/arm/sensor_module/sensor_module_defconfig'
error: Aborting due to Kconfig warnings
"""


def test_kconfig_abort_names_the_undefined_assignment() -> None:
    """The configure failure said only "Aborting due to Kconfig warnings"; the warning that
    caused it is earlier in the log, with the project file and line."""
    from zmig.analyzers.buildlog import kconfig_abort_causes

    causes = kconfig_abort_causes(KCONFIG_ABORT_LOG, {"/src/FSW": "FSW/"})
    assert causes == [
        (
            "FSW/boards/arm/sensor_module/sensor_module_defconfig",
            33,
            "attempt to assign the value 'y' to the undefined symbol TMP116",
        )
    ]
    # A file outside the project keeps its place as text only; no abort, nothing.
    assert kconfig_abort_causes(KCONFIG_ABORT_LOG, {}) == [
        ("", 0, "/src/FSW/boards/arm/sensor_module/sensor_module_defconfig:33: attempt to assign "
         "the value 'y' to the undefined symbol TMP116")
    ]  # fmt: skip
    assert kconfig_abort_causes(KCONFIG_ABORT_LOG.replace("error: Aborting", "x"), {}) == []


# scripts/schemas/board-schema.yaml, reduced to the part that changed at v4.4.0: a new
# board needs `full_name` next to `name` (an extending one needs only `extend`).
BOARD_SCHEMA_OLD = """\
type: object
properties:
  board: {$ref: "#/$defs/boardSchema"}
  boards: {type: array, items: {$ref: "#/$defs/boardSchema"}}
$defs:
  boardSchema:
    type: object
    properties:
      name: {type: string}
      full_name: {type: string}
      extend: {type: string}
      vendor: {type: string}
      socs: {type: array}
"""
BOARD_SCHEMA_NEW = (
    BOARD_SCHEMA_OLD
    + """\
    oneOf:
      - required: [name, full_name]
      - required: [extend]
"""
)


def test_a_malformed_board_yml_names_the_missing_property(tmp_path: Path) -> None:
    """ardep A3 (DOGFOOD-8): v4.4.0 says only "Malformed board YAML file ... is not valid
    under any of the given schemas in $.board". Validating the file against the target's
    schema names what's missing: `full_name`, at the board's `name:` line. A `boards:` list
    (a module's board.yml) points at the entry."""
    from zmig.analyzers.buildlog import board_yaml_causes

    old, new = tmp_path / "old", tmp_path / "new"
    for z, schema in ((old, BOARD_SCHEMA_OLD), (new, BOARD_SCHEMA_NEW)):
        (z / "scripts" / "schemas").mkdir(parents=True)
        (z / "scripts" / "schemas" / "board-schema.yaml").write_text(schema)
    board = tmp_path / "ardep" / "boards" / "mercedes" / "ardep" / "board.yml"
    board.parent.mkdir(parents=True)
    board.write_text(
        "board:\n  name: ardep\n  vendor: mercedes\n  socs:\n    - name: stm32g474xx\n"
    )
    log = (
        "  Error message: ERROR: Malformed board YAML file:\n"
        f"  {board}\n\n  {{'name': 'ardep', ...}} is not valid under any of the given "
        "schemas in $.board\n"
    )
    roots = {str(tmp_path / "ardep"): "ardep/"}
    ((path, line, msg),) = board_yaml_causes(log, new, old, roots)
    assert (path, line) == ("ardep/boards/mercedes/ardep/board.yml", 2)
    assert "`full_name`" in msg and "valid at the source" in msg

    board.write_text(
        "boards:\n  - name: a\n    full_name: A\n    socs: []\n"
        "  - name: cytron_maker_pi_rp2040\n    vendor: cytron\n    socs: []\n"
    )
    ((path, line, msg),) = board_yaml_causes(log, new, old, roots)
    assert line == 5 and "`full_name`" in msg and "$.boards[1]" in msg
    assert board_yaml_causes("no such error", new, old, roots) == []


def test_project_inputs_the_source_applied_and_the_target_does_not() -> None:
    """CoffeeCaller C1 (DOGFOOD-10 §4): v4.4.0 no longer applies boards/extensions/, so
    native_sim.conf and native_sim.overlay, merged and applied at v4.2.0, are silently
    ignored. Only judged where the target got that far: Kconfig merged anything (for
    fragments), a devicetree was generated (for overlays). Zephyr's and generated files
    aren't the project's."""
    from zmig.analyzers.buildlog import inputs_not_applied
    from zmig.diagnostics import LogFacts

    old = LogFacts(
        overlays=("/m/cc/boards/extensions/native_sim/native_sim.overlay",),
        configs=(
            "/m/cc/boards/extensions/native_sim/native_sim.conf",
            "/s/app/prj.conf",
            "/s/build/zephyr/misc/generated/extra_kconfig_options.conf",
        ),
    )
    new = LogFacts(configs=("/t/app/prj.conf", "/t/build/extra_kconfig_options.conf"))
    old_roots = {"/s/app": "", "/m/cc": "cc/"}
    new_roots = {"/t/app": "", "/m/cc": "cc/"}
    assert inputs_not_applied(old, new, old_roots, new_roots, target_dts=True) == [
        ("cc/boards/extensions/native_sim/native_sim.conf", "configuration fragment"),
        ("cc/boards/extensions/native_sim/native_sim.overlay", "devicetree overlay"),
    ]
    # The target never reached Kconfig or devicetree: nothing can be judged.
    assert inputs_not_applied(old, LogFacts(), old_roots, new_roots, target_dts=False) == []


def test_a_symbol_a_header_stopped_providing_names_the_include_to_add(tmp_path: Path) -> None:
    """DOGFOOD-12 E4, ble-copro B1/B2: at v4.4.2 `stream_client.c` fails with implicit
    declarations of socket()/LOG_ERR() and LOG_MODULE_REGISTER defaulting to int. At v4.3.0
    the file got zephyr/posix/sys/socket.h and zephyr/logging/log.h only through
    net/socket.h and net_core.h (ninja's records); it includes neither itself. The remedy
    is the include, at the file's include block."""
    from zmig.analyzers.buildlog import BuildError, missing_includes

    inc = tmp_path / "zephyr" / "include" / "zephyr"
    for rel, text in {
        "net/socket.h": "#include <zephyr/posix/sys/socket.h>\nint zsock_socket(int f);\n"
                        "static inline int zsock_x(void) { return socket(1, 2, 3); }\n",
        "posix/sys/socket.h": "int socket(int family, int type, int proto);\n"
                              "ssize_t send(int sock, const void *buf,\n\tsize_t len, int flags);\n",
        "logging/log.h": "#define LOG_ERR(...) Z_LOG(1, __VA_ARGS__)\n#define LOG_MODULE_REGISTER(...)\n",
    }.items():  # fmt: skip
        (inc / rel).parent.mkdir(parents=True, exist_ok=True)
        (inc / rel).write_text(text)
    app = tmp_path / "app"
    (app / "src").mkdir(parents=True)
    f = app / "src" / "stream_client.c"
    f.write_text(
        "#include <zephyr/kernel.h>\n#include <zephyr/net/socket.h>\n\nLOG_MODULE_REGISTER(x);\n"
    )
    tgt = "/tgt/app/src/stream_client.c"

    def err(line: int, msg: str) -> BuildError:
        return BuildError("compiler", msg, tgt, line)

    errors = [
        err(4, "type defaults to 'int' in declaration of 'LOG_MODULE_REGISTER' [-Wimplicit-int]"),
        err(188, "implicit declaration of function 'socket'; did you mean 'socklen_t'?"),
        err(304, "implicit declaration of function 'send' [-Wimplicit-function-declaration]"),
        err(190, "implicit declaration of function 'LOG_ERR' [-Wimplicit-function-declaration]"),
        err(200, "'x' undeclared (first use in this function)"),  # declared nowhere: no finding
    ]
    deps = {"stream_client.c.obj": [str(f), *(str(inc / r) for r in
            ("net/socket.h", "posix/sys/socket.h", "logging/log.h"))]}  # fmt: skip
    found = missing_includes(errors, deps, {str(app): ""}, {"/tgt/app": ""})
    assert [(m.file, m.line, m.header, m.symbols) for m in found] == [
        ("src/stream_client.c", 2, "zephyr/logging/log.h", ("LOG_ERR", "LOG_MODULE_REGISTER")),
        ("src/stream_client.c", 2, "zephyr/posix/sys/socket.h", ("send", "socket")),
    ]
    # A header the file includes itself isn't one it lost.
    f.write_text("#include <zephyr/logging/log.h>\n#include <zephyr/net/socket.h>\n")
    assert [m.header for m in missing_includes(errors, deps, {str(app): ""}, {"/tgt/app": ""})] == [
        "zephyr/posix/sys/socket.h"
    ]
