"""Report renderers. Each takes a Report and returns text."""

from __future__ import annotations

import json
from collections.abc import Callable

from zmig.errors import ZmigError
from zmig.findings import Report
from zmig.report.human import render_human
from zmig.report.markdown import render_markdown
from zmig.report.sarif import render_sarif


def render_json(report: Report) -> str:
    return json.dumps(report.to_json(), indent=2, sort_keys=False) + "\n"


RENDERERS: dict[str, Callable[[Report], str]] = {
    "json": render_json,
    "markdown": render_markdown,
    "human": render_human,
    "sarif": render_sarif,
}


def render(report: Report, fmt: str) -> str:
    if fmt not in RENDERERS:
        raise ZmigError(f"unknown format {fmt}")
    return RENDERERS[fmt](report)
