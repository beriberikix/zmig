"""Regression: concurrent runs of one ref with different module sets raced on the
workspace's manifest.project-filter (a p1-mode build saw p1-layout's modules)."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

CHILD = """
import sys, time
from pathlib import Path
from zmig.workspace import lock_workspace
lock_workspace(Path(sys.argv[1]))
print("locked", time.monotonic(), flush=True)
time.sleep(float(sys.argv[2]))
"""


def test_second_process_waits_for_the_workspace_lock(tmp_path: Path) -> None:
    lock = tmp_path / "ws.lock"
    first = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(lock), "1.5"], stdout=subprocess.PIPE, text=True
    )
    assert first.stdout is not None
    t1 = float(first.stdout.readline().split()[1])
    second = subprocess.run(
        [sys.executable, "-c", CHILD, str(lock), "0"], capture_output=True, text=True, check=True
    )
    t2 = float(second.stdout.split()[1])
    first.wait()
    assert t2 - t1 >= 1.4
    assert "waiting for another zmig run" in second.stderr


def test_lock_is_reentrant_within_a_process(tmp_path: Path) -> None:
    from zmig.workspace import lock_workspace

    start = time.monotonic()
    lock_workspace(tmp_path / "a.lock")
    lock_workspace(tmp_path / "a.lock")
    assert time.monotonic() - start < 1
