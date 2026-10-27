"""ROM/RAM footprint from an ELF, by section flags and per-symbol sizes (pyelftools).

ROM: allocated read-only sections, plus the initial values of initialized data.
RAM: allocated writable sections (data, bss, noinit).
For native boards the ELF is a host executable, so totals include host-side runtime;
they are still comparable between two builds of the same board.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

SHF_WRITE, SHF_ALLOC = 0x1, 0x2


@dataclass(frozen=True)
class Footprint:
    rom: int
    ram: int
    symbols: dict[str, int] = field(default_factory=dict)


def footprint(elf_path: Path) -> Footprint:
    from elftools.elf.elffile import ELFFile
    from elftools.elf.sections import SymbolTableSection

    rom = ram = 0
    symbols: dict[str, int] = {}
    with elf_path.open("rb") as f:
        elf = ELFFile(f)
        for sec in elf.iter_sections():
            flags, size = sec["sh_flags"], sec["sh_size"]
            if not flags & SHF_ALLOC:
                continue
            if flags & SHF_WRITE:
                ram += size
                if sec["sh_type"] != "SHT_NOBITS":
                    rom += size  # initial values live in ROM
            else:
                rom += size
        symtab = elf.get_section_by_name(".symtab")
        if isinstance(symtab, SymbolTableSection):
            for sym in symtab.iter_symbols():
                if sym["st_info"]["type"] in ("STT_FUNC", "STT_OBJECT") and sym["st_size"]:
                    symbols[sym.name] = symbols.get(sym.name, 0) + sym["st_size"]
    return Footprint(rom, ram, symbols)
