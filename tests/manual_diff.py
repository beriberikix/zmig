"""Independent diffs of build artifacts, for checking analyzer completeness.

Deliberately not built on zmig's own snapshot code: .config is read line by line and
zephyr.dts is normalized by dtc and parsed with a minimal brace tracker.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def dotconfig(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if m := re.match(r"^CONFIG_(\w+)=(.*)$", line):
            out[m[1]] = m[2]
        elif m := re.match(r"^# CONFIG_(\w+) is not set$", line):
            out[m[1]] = "n"
    return out


def config_diff(old: Path, new: Path) -> set[str]:
    """Symbols whose value differs; an absent symbol is 'n', as Kconfig treats it."""
    a, b = dotconfig(old), dotconfig(new)
    return {k for k in a.keys() | b.keys() if a.get(k, "n") != b.get(k, "n")}


def dts_nodes(path: Path) -> dict[str, frozenset[str]]:
    """node path -> set of normalized property lines, from `dtc -I dts -O dts -s`."""
    text = subprocess.run(
        ["dtc", "-q", "-I", "dts", "-O", "dts", "-s", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    nodes: dict[str, set[str]] = {}
    stack: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if m := re.match(r"^(?:[\w-]+:\s*)*([^\s{]+)\s*\{$", line):
            name = m[1]
            path_ = "/" if name == "/" else (stack[-1].rstrip("/") + "/" + name)
            stack.append(path_)
            nodes.setdefault(path_, set())
        elif line == "};":
            stack.pop()
        elif stack and line.endswith(";") and not line.startswith("/"):
            nodes[stack[-1]].add(re.sub(r"<&[\w-]+>|&[\w-]+", "<ref>", line))
    return {k: frozenset(v) for k, v in nodes.items()}


def dts_diff(old: Path, new: Path) -> set[str]:
    a, b = dts_nodes(old), dts_nodes(new)
    return {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)}
