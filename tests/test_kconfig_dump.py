"""kconfig_dump.py against synthetic Kconfig trees, with a cached workspace's kconfiglib."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
CACHE = Path(os.environ.get("ZMIG_CACHE", Path.home() / ".cache" / "zmig"))
KCONFIGLIB = next(iter(sorted(CACHE.glob("workspaces/*/zephyr/scripts/kconfig"))), None)


@pytest.mark.skipif(KCONFIGLIB is None, reason="no cached Zephyr workspace")
def test_long_or_chain_does_not_overflow(tmp_path: Path) -> None:
    """Regression: a 1000+ operand `||` chain raised RecursionError in expr_str."""
    n = 3000
    terms = " || ".join(f"B{i}" for i in range(n))
    decls = "".join(f"config B{i}\n\tbool\n" for i in range(n))
    (tmp_path / "Kconfig").write_text(f'config A\n\tbool "a"\n\tdepends on {terms}\n{decls}')
    out = tmp_path / "out.json"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "zmig/extract/kconfig_dump.py"),
            str(KCONFIGLIB),
            str(tmp_path / "Kconfig"),
            str(out),
        ],
        check=True,
        env={**os.environ, "srctree": str(tmp_path)},
        capture_output=True,
    )
    a = json.loads(out.read_text())["symbols"]["A"]
    assert a["depends"].count("||") == n - 1
