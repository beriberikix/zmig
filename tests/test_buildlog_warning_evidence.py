"""Kconfig-diagnostic findings (kconfig-override-orphaned, kconfig-name-collision) list the
target's own warning as tool_output evidence, with the log and line it is in, not only in
`new.message`. Regression: ZMK's BT_LL_SW_LLCP_IMPL finding (its warning came from a
salvage round's log)."""

from __future__ import annotations

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
