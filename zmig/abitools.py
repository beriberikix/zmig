"""Optional binary tools for the API diff (DESIGN.md, "Optional binary tools").

pahole   the layout the GCC-built ELF really has (DWARF), to cross-check clang's model of
         the structs the project accesses
abidiff  the ABI a binary boundary sees (a prebuilt static library the project links, or
         LLEXT extensions), between the two ELFs

Both are optional: a missing tool is a "not checked" entry, never an error or a clean
result.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

_STRUCT = re.compile(r"^(?:struct|union) (?P<name>\w+) \{$")
# A direct member: one tab, "<type> <name>[:bits];  /* <offset>[:<bit>] <size> */".
_MEMBER = re.compile(
    r"^\t(?!\t)[^/]*?\b(?P<name>\w+)(?::\d+)?;\s*/\*\s*(?P<off>\d+)(?::\s*(?P<bit>\d+))?\s+\d+\s*\*/"
)


def tool(name: str) -> str | None:
    return shutil.which(name)


def pahole_layouts(elf: Path, structs: list[str]) -> dict[str, dict[str, int]] | None:
    """{struct: {member: bit offset}} for direct members, from `pahole -C`. None when pahole
    isn't installed; structs pahole doesn't know are absent."""
    exe = tool("pahole")
    if exe is None:
        return None
    if not structs:
        return {}
    proc = subprocess.run(
        [exe, "-C", ",".join(sorted(set(structs))), str(elf)], capture_output=True, text=True
    )
    out: dict[str, dict[str, int]] = {}
    current: dict[str, int] | None = None
    for line in proc.stdout.splitlines():
        if m := _STRUCT.match(line):
            current = out.setdefault(m["name"], {})
        elif line.startswith("}"):
            current = None
        elif current is not None and (m := _MEMBER.match(line)):
            current[m["name"]] = int(m["off"]) * 8 + int(m["bit"] or 0)
    return out


def binary_boundaries(img_dir: Path, project_roots: list[str]) -> list[str]:
    """Binary boundaries of an image: static libraries from the project tree linked into
    it (prebuilt, not built by this build), and "llext" when CONFIG_LLEXT is set."""
    found: list[str] = []
    ninja = img_dir / "build.ninja"
    if ninja.is_file():
        text = ninja.read_text(errors="replace")
        for lib in sorted(set(re.findall(r"(/\S+\.a)\b", text))):
            if any(lib.startswith(r + "/") for r in project_roots) and not lib.startswith(
                str(img_dir) + "/"
            ):
                found.append(lib)
    config = img_dir / "zephyr" / ".config"
    if config.is_file() and re.search(r"^CONFIG_LLEXT=y$", config.read_text(), re.M):
        found.append("llext")
    return found


def undefined_symbols(archive: Path) -> set[str]:
    """Symbols the objects in a static library reference but don't define."""
    data = archive.read_bytes()
    if not data.startswith(b"!<arch>\n"):
        return set()
    undefined: set[str] = set()
    defined: set[str] = set()
    pos = 8
    while pos + 60 <= len(data):
        header = data[pos : pos + 60]
        size = int(header[48:58].decode().strip() or 0)
        body = data[pos + 60 : pos + 60 + size]
        pos += 60 + size + (size & 1)
        if not body.startswith(b"\x7fELF"):
            continue
        import io

        elf = ELFFile(io.BytesIO(body))
        for sec in elf.iter_sections():
            if not isinstance(sec, SymbolTableSection):
                continue
            for sym in sec.iter_symbols():
                if not sym.name:
                    continue
                if sym["st_shndx"] == "SHN_UNDEF":
                    undefined.add(sym.name)
                else:
                    defined.add(sym.name)
    return undefined - defined


def abidiff_changes(old_elf: Path, new_elf: Path, symbols: set[str]) -> list[str] | None:
    """abidiff's report lines about `symbols` (functions and variables whose type, size or
    presence changed between the two ELFs). None when abidiff isn't installed."""
    exe = tool("abidiff")
    if exe is None:
        return None
    proc = subprocess.run([exe, str(old_elf), str(new_elf)], capture_output=True, text=True)
    lines = []
    for line in proc.stdout.splitlines():
        m = re.search(r"'(?:[^']*\s)?(\w+)\(|'(\w+)'", line)
        name = (m[1] or m[2]) if m else None
        if name and name in symbols:
            lines.append(line.strip())
    return lines
