"""Definitions in C sources at a pinned SHA, and how they differ between two SHAs.

docs/DESIGN-P1.md §3. Reads go through `git grep`/`git show` at the SHA (never a
checkout). Seeds (zmig/data/hazard_seeds.yaml) say what to look for:

  define         every `#define NAME ...` (conditional definitions are all kept)
  define-prefix  every `#define` whose name starts with NAME
  struct/union   `struct NAME { ... }`: the normalized body, and the member list when
                 every statement in it is a plain `type name[array];`
  initializer    `... NAME = { ... };`: the normalized initializer

Comparison is on normalized text (comments removed, whitespace collapsed). A struct whose
body changed but whose members can't be listed is "not-interpreted", never "unchanged".
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from zmig.tools import Runner, find_tool

SEEDS_FILE = Path(__file__).parent / "data" / "hazard_seeds.yaml"

SeedKind = Literal["define", "define-prefix", "struct", "union", "initializer"]
Status = Literal[
    "unchanged", "extended", "changed", "added", "removed", "not-found", "not-interpreted"
]


@dataclass(frozen=True)
class Seed:
    kind: SeedKind
    name: str
    reason: str

    @property
    def label(self) -> str:
        return f"{self.kind} {self.name}"


@dataclass(frozen=True)
class Definition:
    name: str
    kind: SeedKind
    value: str
    file: str
    line: int
    # (type, name, array suffix) per member, for struct/union; None if not interpretable.
    members: tuple[tuple[str, str, str], ...] | None = None
    # What precedes the name: `struct`, `typedef struct`, a packing macro (STRUCT_PACKED).
    decl: str = ""


@dataclass
class SeedDiff:
    seed: Seed
    status: Status
    # Per defined name (one for most kinds; many for define-prefix): status and sites.
    names: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"seed": self.seed.label, "status": self.status, "names": self.names}


def load_seeds() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(SEEDS_FILE.read_text())
    return data


def seeds_of(group: dict[str, Any]) -> list[Seed]:
    return [Seed(s["kind"], s["name"], s.get("reason", "")) for s in group["seeds"]]


_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def normalize(text: str) -> str:
    return " ".join(_COMMENT.sub(" ", text).split())


class Source:
    """One git repository at one SHA."""

    def __init__(self, repo: Path, sha: str, runner: Runner, bare: bool = False):
        self.repo, self.sha, self.runner = repo, sha, runner
        self._git: list[str | Path] = [
            find_tool("git"),
            f"--git-dir={repo if bare else repo / '.git'}",
        ]
        self._files: dict[str, list[str] | None] = {}

    def show(self, path: str) -> list[str] | None:
        if path not in self._files:
            proc = self.runner.run([*self._git, "show", f"{self.sha}:{path}"], check=False)
            self._files[path] = proc.stdout.splitlines() if proc.returncode == 0 else None
        return self._files[path]

    def grep(self, pattern: str, paths: list[str]) -> list[tuple[str, int, str]]:
        """(path, line, text) for lines matching an extended regex."""
        proc = self.runner.run(
            [*self._git, "grep", "-n", "-I", "-E", "-e", pattern, self.sha, "--", *paths],
            check=False,
        )
        out = []
        for ln in proc.stdout.splitlines():
            parts = ln.split(":", 3)
            if len(parts) == 4:
                out.append((parts[1], int(parts[2]), parts[3]))
        return out

    def locate(self, seed: Seed, paths: list[str]) -> list[Definition]:
        n = re.escape(seed.name)
        if seed.kind in ("define", "define-prefix"):
            tail = r"\w*" if seed.kind == "define-prefix" else r"\b"
            return [
                d
                for path, line, _ in self.grep(rf"^\s*#\s*define\s+{n}{tail}", paths)
                if (d := self._define(path, line, seed.kind)) is not None
            ]
        if seed.kind in ("struct", "union"):
            # The keyword may come from a macro (MCUboot's STRUCT_PACKED at older pins).
            pat = rf"^\s*[A-Za-z_][A-Za-z0-9_ ]*\b{n}\s*([{{].*)?$"
            return [
                d
                for path, line, text in self.grep(pat, paths)
                if seed.kind in text.split(seed.name)[0].lower()
                and (d := self._block(path, line, seed)) is not None
            ]
        pat = rf"\b{n}\s*=\s*([{{].*)?$"
        return [
            d
            for path, line, _ in self.grep(pat, paths)
            if (d := self._block(path, line, seed)) is not None
        ]

    def _define(self, path: str, line: int, kind: SeedKind) -> Definition | None:
        lines = self.show(path)
        if lines is None:
            return None
        text = lines[line - 1]
        k = line
        while text.rstrip().endswith("\\") and k < len(lines):
            text = text.rstrip()[:-1] + " " + lines[k]
            k += 1
        m = re.match(r"^\s*#\s*define\s+(\w+(?:\([^)]*\))?)(.*)$", normalize(text))
        if m is None:
            return None
        return Definition(m[1].split("(")[0], kind, m[2].strip(), path, line)

    def _block(self, path: str, line: int, seed: Seed) -> Definition | None:
        """The brace-matched block starting at or after `line`."""
        lines = self.show(path)
        if lines is None:
            return None
        text = "\n".join(lines[line - 1 :])
        start = text.find("{")
        if start < 0 or ";" in text[:start]:
            return None  # a declaration (extern ..., struct x;), not a definition
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    body = text[start + 1 : i]
                    members = _members(body) if seed.kind in ("struct", "union") else None
                    decl = normalize(lines[line - 1].split(seed.name)[0])
                    return Definition(
                        seed.name, seed.kind, normalize(body), path, line, members, decl
                    )
        return None


_MEMBER = re.compile(r"^((?:const\s+)?(?:struct\s+|union\s+|enum\s+)?\w+)\s+(\w+)\s*(\[[^\]]*\])?$")


def _members(body: str) -> tuple[tuple[str, str, str], ...] | None:
    clean = _COMMENT.sub(" ", body)
    if "#" in clean or "{" in clean:
        return None  # conditional members or nested aggregates: not interpreted
    out = []
    for stmt in clean.split(";"):
        stmt = " ".join(stmt.split())
        if not stmt:
            continue
        m = _MEMBER.match(stmt)
        if m is None:
            return None
        out.append((m[1], m[2], m[3] or ""))
    return tuple(out)


def _site(d: Definition, ref: str) -> dict[str, Any]:
    out: dict[str, Any] = {"ref": ref, "file": d.file, "line": d.line, "value": d.value}
    if d.decl:
        out["decl"] = d.decl
    return out


def compare(
    seed: Seed,
    old: list[Definition],
    new: list[Definition],
    old_ref: str,
    new_ref: str,
) -> SeedDiff:
    if not old or not new:
        return SeedDiff(
            seed,
            "not-found",
            [
                {
                    "name": seed.name,
                    "status": "not-found",
                    "missing_at": [r for r, defs in ((old_ref, old), (new_ref, new)) if not defs],
                }
            ],
        )
    by_o: dict[str, list[Definition]] = {}
    by_n: dict[str, list[Definition]] = {}
    for d in old:
        by_o.setdefault(d.name, []).append(d)
    for d in new:
        by_n.setdefault(d.name, []).append(d)
    names: list[dict[str, Any]] = []
    for name in sorted(by_o.keys() | by_n.keys()):
        o, n = by_o.get(name, []), by_n.get(name, [])
        st: Status
        if not o:
            st = "added"
        elif not n:
            st = "removed"
        elif sorted(d.value for d in o) == sorted(d.value for d in n):
            st = "unchanged"
        elif seed.kind in ("define", "define-prefix") and not (
            Counter(d.value for d in o) - Counter(d.value for d in n)
        ):
            # Every old definition is still there; new (conditional) variants were added.
            # Conditions aren't evaluated, so this is reported, not cleared.
            st = "extended"
        elif seed.kind in ("struct", "union") and (o[0].members is None or n[0].members is None):
            st = "not-interpreted"
        else:
            st = "changed"
        entry: dict[str, Any] = {
            "name": name,
            "status": st,
            "old": [_site(d, old_ref) for d in o],
            "new": [_site(d, new_ref) for d in n],
        }
        if st == "changed" and seed.kind in ("struct", "union"):
            entry["members"] = {"old": o[0].members, "new": n[0].members}
        if o and n and o[0].decl != n[0].decl:
            entry["decl_changed"] = {"old": o[0].decl, "new": n[0].decl}
        names.append(entry)
    statuses = {e["status"] for e in names}
    overall: Status = (
        "not-interpreted"
        if "not-interpreted" in statuses
        else "changed"
        if statuses & {"changed", "removed"}
        else "extended"
        if "extended" in statuses
        else "added"
        if "added" in statuses
        else "unchanged"
    )
    return SeedDiff(seed, overall, names)
