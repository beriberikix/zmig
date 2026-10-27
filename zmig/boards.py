"""Board name mapping across versions, from Zephyr's own boards/deprecated.cmake.

The target tree's table is checked first. Zephyr drops aliases a few releases after
the rename (native_sim_64 was listed through v4.0.0 and removed in v4.1.0), so for a
large hop the release tags between the two refs are searched too, newest first.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

from zmig.diagnostics import deprecated_board_line, parse_deprecated_boards


@dataclass(frozen=True)
class BoardMapping:
    source: str
    target: str
    how: str  # "same" | "--to-board" | "deprecated.cmake"
    tag: str | None = None  # whose deprecated.cmake listed the alias
    line: int = 0

    def to_json(self) -> dict[str, object]:
        d: dict[str, object] = {"from": self.source, "to": self.target, "mapping": self.how}
        if self.tag:
            d["listed_in"] = f"{self.tag}:boards/deprecated.cmake:{self.line}"
        return d


def _with_revision(target: str, rev: str) -> str:
    if not rev:
        return target
    head, _, quals = target.partition("/")
    return f"{head}@{rev}" + (f"/{quals}" if quals else "")


def map_board(board: str, explicit: str | None, tables: Iterable[tuple[str, str]]) -> BoardMapping:
    """Map `board` using (ref, deprecated.cmake text) tables, checked in order."""
    if explicit:
        return BoardMapping(board, explicit, "--to-board")
    name, _, rev = board.partition("@")
    for tag, text in tables:
        target = parse_deprecated_boards(text).get(name)
        if target is not None:
            return BoardMapping(
                board,
                _with_revision(target, rev),
                "deprecated.cmake",
                tag,
                deprecated_board_line(text, name),
            )
    return BoardMapping(board, board, "same")


def alias_tables(
    target_spec: str, target_text: str, history: Callable[[], Iterator[tuple[str, str]]]
) -> Iterator[tuple[str, str]]:
    """The target's table, then (lazily) historic ones."""
    yield target_spec, target_text
    yield from history()
