"""Location and layout of zmig's cache directory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def default_cache_root() -> Path:
    if env := os.environ.get("ZMIG_CACHE"):
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(xdg) / "zmig"


@dataclass(frozen=True)
class Cache:
    root: Path

    @property
    def mirrors(self) -> Path:
        return self.root / "mirrors"

    @property
    def workspaces(self) -> Path:
        return self.root / "workspaces"

    @property
    def builds(self) -> Path:
        return self.root / "builds"

    @property
    def snapshots(self) -> Path:
        return self.root / "snapshots"

    def ensure(self) -> None:
        for d in (self.mirrors, self.workspaces, self.builds, self.snapshots):
            d.mkdir(parents=True, exist_ok=True)
