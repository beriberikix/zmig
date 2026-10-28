"""Dump the init calls of one build, in the order the kernel runs them, as JSON.

Usage: init_dump.py <zephyr>/scripts/build <zephyr.elf> <edt.pickle> <out.json>

The ELF is read with that ref's own scripts/build/check_init_priorities.py
(ZephyrInitLevels, v3.5 on): the same reader Zephyr's CHECK_INIT_PRIORITIES build step
uses. Device arguments are mapped to devicetree paths with the same ref's edt.pickle,
and each init function to the file:line that defines it, from the ELF's DWARF.
Exits 3 if the ref's script can't list init levels.
"""

import inspect
import json
import os
import pickle
import re
import sys

sys.path.insert(0, sys.argv[1])
import check_init_priorities as cip
from elftools.elf.elffile import ELFFile

_CALL = re.compile(r"^(?P<obj>\S+): (?P<fn>[^(\s]+)\((?P<arg>[^)]*)\)$")
_DTS_ORD = re.compile(r"^__device_dts_ord_(\d+)$")


def file_name(lineprog, index, comp_dir):
    """The path of DWARF file entry `index` (1-based before DWARF 5, 0-based from 5)."""
    v5 = lineprog.header.version >= 5
    entries = lineprog.header.file_entry
    fe = entries[index if v5 else index - 1]
    name = fe.name.decode(errors="replace")
    dirs = lineprog.header.include_directory
    if fe.dir_index or v5:
        d = dirs[fe.dir_index if v5 else fe.dir_index - 1]
        d = d.decode(errors="replace")
    else:
        d = comp_dir
    path = os.path.join(d, name) if d and not os.path.isabs(name) else name
    if comp_dir and not os.path.isabs(path):
        path = os.path.join(comp_dir, path)
    return os.path.normpath(path)


def function_sites(elf_path, names):
    """{function name: [defining file, line]} for `names`, from DWARF subprograms."""
    sites = {}
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        if not elf.has_dwarf_info():
            return sites
        dwarf = elf.get_dwarf_info()
        for cu in dwarf.iter_CUs():
            top = cu.get_top_DIE()
            comp = top.attributes.get("DW_AT_comp_dir")
            comp_dir = comp.value.decode(errors="replace") if comp else ""
            lineprog = None
            for die in cu.iter_DIEs():
                if die.tag != "DW_TAG_subprogram":
                    continue
                attr = die.attributes.get("DW_AT_name")
                if attr is None:
                    continue
                name = attr.value.decode(errors="replace")
                if name not in names or name in sites:
                    continue
                decl = die.attributes.get("DW_AT_decl_file")
                if decl is None:
                    continue
                if lineprog is None:
                    lineprog = dwarf.line_program_for_CU(cu)
                line = die.attributes.get("DW_AT_decl_line")
                try:
                    path = file_name(lineprog, decl.value, comp_dir)
                except (IndexError, AttributeError):
                    continue
                sites[name] = [path, line.value if line else 0]
            if len(sites) == len(names):
                break
    return sites


def main():
    _, _, elf_path, edt_path, out_path = sys.argv
    if not hasattr(cip, "ZephyrInitLevels"):
        print("check_init_priorities.py has no ZephyrInitLevels", file=sys.stderr)
        sys.exit(3)
    # v3.5-v4.2 take the path; from v4.3 the path and an open file.
    params = list(inspect.signature(cip.ZephyrInitLevels.__init__).parameters)[1:]
    if len(params) >= 2:
        elf_file = open(elf_path, "rb")  # noqa: SIM115 (the reader keeps reading it)
        levels = cip.ZephyrInitLevels(elf_path, elf_file)
    else:
        levels = cip.ZephyrInitLevels(elf_path)
    with open(edt_path, "rb") as f:
        edt = pickle.load(f)
    ord2path = {n.dep_ordinal: n.path for n in edt.nodes}
    entries = []
    for level, calls in levels.initlevels.items():
        for i, call in enumerate(calls):
            m = _CALL.match(call.strip())
            if not m:
                continue
            arg = m["arg"]
            dts = _DTS_ORD.match(arg)
            entries.append(
                {
                    "level": level,
                    "index": i,
                    "object": m["obj"],
                    "fn": m["fn"],
                    "arg": arg,
                    "node": ord2path.get(int(dts[1])) if dts else None,
                }
            )
    names = {e["fn"] for e in entries}
    out = {
        "levels": list(levels.initlevels),
        "entries": entries,
        "sites": function_sites(elf_path, names),
    }
    with open(out_path, "w") as f:
        json.dump(out, f, sort_keys=True)


main()
