"""`zmig scan --pin-source REPORT`: the source side of this scan is REPORT's source build,
reused from the cache by its key, while the target is built from the (edited) app.

The source then means the firmware deployed today, and the target the candidate: an edit
that only builds at the target ref is still compared against the unedited app's source
build (DESIGN.md, "Pinned-source scans"). Every check runs before any build.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zmig.errors import UsageError
from zmig.findings import SCHEMA_URI
from zmig.report.human import effective_contexts

JSON = dict[str, Any]


@dataclass(frozen=True)
class Pin:
    path: Path
    report: JSON
    key: str  # the source build's key
    app_tree_hash: str  # of the unedited app the source build was built from
    modules_added: tuple[str, ...]  # module recovery added these to the source workspace

    def to_json(self) -> JSON:
        """`inputs.pinned_source` (schema 1.7). The path as given when relative, else the
        file name, as compare.json writes paths."""
        shown = str(self.path) if not self.path.is_absolute() else self.path.name
        return {
            "report": shown,
            "key": self.key,
            "app_tree_hash": self.app_tree_hash,
            "zmig_build": self.report.get("environment", {}).get("zmig_build"),
        }


def read_pin(path: Path) -> Pin:
    """REPORT, if it can be pinned to at all: a scan report of schema 1.7 or later (one that
    records its source build's key and side), not itself pinned, with a source build."""
    try:
        doc = json.loads(path.read_text())
    except FileNotFoundError:
        raise UsageError(f"--pin-source {path}: no such file") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise UsageError(f"--pin-source {path}: not a readable JSON report ({e})") from None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA_URI:
        raise UsageError(f"--pin-source {path}: not a zmig report")
    if doc.get("command") != "scan":
        raise UsageError(f"--pin-source {path}: a `zmig {doc.get('command')}` report, not a scan")
    major, _, minor = str(doc.get("schema_version", "")).partition(".")
    if major != "1" or not minor.isdigit() or int(minor) < 7:
        raise UsageError(
            f"--pin-source {path}: schema_version {doc.get('schema_version')!r}; "
            "pinning needs a report from this zmig (1.7 or later): re-run that scan"
        )
    if doc["inputs"].get("pinned_source"):
        raise UsageError(
            f"--pin-source {path}: that scan was itself pinned; pin to the original scan"
        )
    source, _, _ = effective_contexts(doc.get("contexts", []))
    if source is None or "key" not in source:
        raise UsageError(f"--pin-source {path}: the report has no source build")
    if source.get("status") != "ok":
        raise UsageError(f"--pin-source {path}: its source build is {source.get('status')}, not ok")
    return Pin(
        path=path,
        report=doc,
        key=str(source["key"]),
        app_tree_hash=str(doc["inputs"]["app_tree_hash"]),
        modules_added=tuple(source.get("modules_added", [])),
    )


def mismatches(pin: Pin, now: JSON) -> list[str]:
    """How this scan's inputs differ from the pinned report's: `now` holds the same fields
    as report.json's inputs and environment.zmig_build. Empty: they match."""

    def fields(i: JSON, build: object) -> JSON:
        return {
            "app": i.get("app"),
            "from": i["from"]["sha"],
            "to": i["to"]["sha"],
            "board": (i.get("board") or {}).get("from"),
            "to-board": (i.get("board") or {}).get("to"),
            "build mode": i.get("build_mode"),
            "sysbuild": i.get("sysbuild"),
            "zmig build": build,
        }

    then = fields(pin.report["inputs"], pin.report.get("environment", {}).get("zmig_build"))
    cur = fields(now, now.get("zmig_build"))
    return [f"{k}: pinned {then[k]!r}, this scan {cur[k]!r}" for k in then if then[k] != cur[k]]
