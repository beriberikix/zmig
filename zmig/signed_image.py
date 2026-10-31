"""MCUboot signed images, read with the definitions at a given MCUboot pin.

DESIGN.md, "Field-upgrade verdict". Nothing about the format is hard-coded here: struct member
order and types, magic values and TLV type IDs come from `image.h` at the pin (via
zmig.srcdiff), and the byte order from imgtool's `--endian` option (its default, or the
value the build passed). A field the definitions don't give raises ImageParseError
naming it; the caller marks the checks that need it unknown. No offsets are guessed.

imgtool (scripts/imgtool at the pin) is read with Python's `ast`, never executed: its
module-level literal constants, its `sign` command's flag options (so build.ninja's
command line is split correctly), and the `--endian` default.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zmig.buildctx import ImageContext
from zmig.srcdiff import Definition, Seed, Source

PRIMITIVE = {f"{s}int{b}_t": b // 8 for s in ("u", "") for b in (8, 16, 32, 64)}


class ImageParseError(Exception):
    def __init__(self, field: str, why: str):
        super().__init__(f"{field}: {why}")
        self.field = field


def c_int(value: str) -> int | None:
    """An integer #define value (0x.., decimal, optional U/L suffixes and parentheses)."""
    v = value.strip().strip("()").strip()
    m = re.fullmatch(r"(0[xX][0-9a-fA-F]+|\d+)[uUlL]*", v)
    return int(m[1], 0) if m else None


# --- layout from definitions --------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    name: str
    offset: int
    size: int
    struct: str | None  # nested struct tag, when the member is one


@dataclass
class Layout:
    """Header and TLV layout at one MCUboot pin."""

    pin: str
    structs: dict[str, tuple[list[Field], int]]  # tag -> (fields, size)
    defines: dict[str, int]  # IMAGE_* integer defines
    tlv_names: dict[int, str]  # TLV type id -> IMAGE_TLV_* name
    sites: dict[str, str]  # tag/define -> "file:line"

    def struct(self, tag: str) -> tuple[list[Field], int]:
        if tag not in self.structs:
            raise ImageParseError(f"struct {tag}", f"not defined at MCUboot {self.pin}")
        return self.structs[tag]

    def define(self, name: str) -> int:
        if name not in self.defines:
            raise ImageParseError(name, f"not defined as an integer at MCUboot {self.pin}")
        return self.defines[name]


def layout_at(src: Source, paths: list[str], pin: str) -> Layout:
    defs: dict[str, Definition] = {}
    structs: dict[str, tuple[list[Field], int]] = {}
    sites: dict[str, str] = {}
    raw: dict[str, Definition] = {}
    for tag in ("image_header", "image_version", "image_tlv_info", "image_tlv"):
        found = src.locate(Seed("struct", tag, ""), paths)
        if found:
            raw[tag] = found[0]
            sites[f"struct {tag}"] = f"{found[0].file}:{found[0].line}"

    def size_of(tag: str, seen: frozenset[str] = frozenset()) -> tuple[list[Field], int]:
        if tag in structs:
            return structs[tag]
        d = raw.get(tag)
        if d is None or d.members is None or tag in seen:
            raise ImageParseError(f"struct {tag}", "not found or not interpretable")
        packed = "packed" in d.decl.lower()
        fields: list[Field] = []
        off = max_align = 0
        for typ, name, arr in d.members:
            nested = typ.removeprefix("struct ").strip() if typ.startswith("struct ") else None
            if nested is not None:
                sub, sz = size_of(nested, seen | {tag})
                align = max((f.size for f in sub), default=1)
            elif typ in PRIMITIVE:
                sz = align = PRIMITIVE[typ]
            else:
                raise ImageParseError(f"{tag}.{name}", f"type {typ} has no known size")
            if arr:
                n = c_int(arr.strip("[]"))
                if n is None:
                    raise ImageParseError(f"{tag}.{name}", f"array size {arr} not an integer")
                sz *= n
            if not packed:
                off = (off + align - 1) // align * align
                max_align = max(max_align, align)
            fields.append(Field(name, off, sz, nested))
            off += sz
        if not packed and max_align:
            off = (off + max_align - 1) // max_align * max_align
        structs[tag] = (fields, off)
        return structs[tag]

    for tag in raw:
        with contextlib.suppress(ImageParseError):
            size_of(tag)
    for seed in (Seed("define-prefix", "IMAGE_", ""),):
        for d in src.locate(seed, paths):
            defs.setdefault(d.name, d)
    defines = {n: v for n, d in defs.items() if (v := c_int(d.value)) is not None}
    for n, d in defs.items():
        sites[n] = f"{d.file}:{d.line}"
    tlv_names = {
        v: n
        for n, v in defines.items()
        if n.startswith("IMAGE_TLV_")
        and n not in ("IMAGE_TLV_INFO_MAGIC", "IMAGE_TLV_PROT_INFO_MAGIC")
    }
    return Layout(pin, structs, defines, tlv_names, sites)


# --- parsing -----------------------------------------------------------------------------


def _read(data: bytes, off: int, size: int, endian: str, field: str) -> int:
    if off + size > len(data):
        raise ImageParseError(field, f"needs bytes {off}..{off + size}, image has {len(data)}")
    return int.from_bytes(data[off : off + size], "little" if endian == "little" else "big")


def _struct(data: bytes, base: int, lay: Layout, tag: str, endian: str) -> dict[str, Any]:
    fields, _ = lay.struct(tag)
    out: dict[str, Any] = {}
    for f in fields:
        if f.struct is not None:
            out[f.name] = _struct(data, base + f.offset, lay, f.struct, endian)
        else:
            out[f.name] = _read(data, base + f.offset, f.size, endian, f"{tag}.{f.name}")
    return out


def parse(path: Path, lay: Layout, endian: str) -> dict[str, Any]:
    """Header fields and TLV entries of a signed .bin, read with `lay`."""
    data = path.read_bytes()
    hdr = _struct(data, 0, lay, "image_header", endian)
    for f in ("ih_magic", "ih_hdr_size", "ih_img_size", "ih_protect_tlv_size", "ih_flags"):
        if f not in hdr:
            raise ImageParseError(f"image_header.{f}", f"no such member at MCUboot {lay.pin}")
    magics = {lay.define(n): n for n in ("IMAGE_MAGIC",) if n in lay.defines}
    magics |= {v: n for n, v in lay.defines.items() if n.startswith("IMAGE_MAGIC_")}
    info_fields, info_size = lay.struct("image_tlv_info")
    tlv_fields, tlv_size = lay.struct("image_tlv")
    names = {f.name for f in info_fields} | {f.name for f in tlv_fields}
    for need in ("it_magic", "it_tlv_tot", "it_type", "it_len"):
        if need not in names:
            raise ImageParseError(f"tlv.{need}", f"no such member at MCUboot {lay.pin}")
    areas = []
    off = hdr["ih_hdr_size"] + hdr["ih_img_size"]
    expected = []
    if hdr["ih_protect_tlv_size"]:
        expected.append(("protected", lay.define("IMAGE_TLV_PROT_INFO_MAGIC")))
    expected.append(("unprotected", lay.define("IMAGE_TLV_INFO_MAGIC")))
    tlvs: list[dict[str, Any]] = []
    for area, magic in expected:
        info = _struct(data, off, lay, "image_tlv_info", endian)
        if info["it_magic"] != magic:
            raise ImageParseError(
                f"{area} TLV area magic",
                f"0x{info['it_magic']:04x} at offset {off}, expected 0x{magic:04x}",
            )
        end = off + info["it_tlv_tot"]
        p = off + info_size
        while p < end:
            t = _struct(data, p, lay, "image_tlv", endian)
            value = data[p + tlv_size : p + tlv_size + t["it_len"]]
            tlvs.append(
                {
                    "area": area,
                    "type": t["it_type"],
                    "name": lay.tlv_names.get(t["it_type"], f"0x{t['it_type']:02x}"),
                    "len": t["it_len"],
                    "sha256": hashlib.sha256(value).hexdigest(),
                }
            )
            p += tlv_size + t["it_len"]
        areas.append({"area": area, "offset": off, "magic": magic, "size": info["it_tlv_tot"]})
        off = end
    return {
        "path": str(path),
        "size": len(data),
        "endian": endian,
        "magic": {"value": hdr["ih_magic"], "name": magics.get(hdr["ih_magic"])},
        "header": hdr,
        "flags": sorted(
            n for n, v in lay.defines.items() if n.startswith("IMAGE_F_") and hdr["ih_flags"] & v
        ),
        "tlv_areas": areas,
        "tlvs": tlvs,
    }


# --- imgtool, read with ast ----------------------------------------------------------------


def imgtool_constants(src: Source, files: list[str]) -> dict[str, dict[str, Any]]:
    """Module-level `NAME = <literal>` assignments: {name: {value, file, line}}."""
    out: dict[str, dict[str, Any]] = {}
    for f in files:
        lines = src.show(f)
        if lines is None:
            continue
        for node in ast.parse("\n".join(lines)).body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.isupper()
            ):
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, SyntaxError, TypeError):
                    continue
                out[node.targets[0].id] = {
                    "value": _jsonable(value),
                    "file": f,
                    "line": node.lineno,
                }
    return out


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [_jsonable(x) for x in v]
    return v


@dataclass(frozen=True)
class SignCli:
    flags: frozenset[str]  # long options of `sign` that take no value
    endian_default: str | None
    endian_site: str | None


def imgtool_cli(src: Source, main: str) -> SignCli | None:
    lines = src.show(main)
    if lines is None:
        return None
    flags: set[str] = set()
    endian: str | None = None
    site: str | None = None
    for node in ast.walk(ast.parse("\n".join(lines))):
        if not (isinstance(node, ast.FunctionDef) and node.name == "sign"):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "option"):
                continue
            names = [a.value for a in dec.args if isinstance(a, ast.Constant)]
            longs = [n for n in names if isinstance(n, str) and n.startswith("--")]
            kw = {k.arg: k.value for k in dec.keywords}
            is_flag = kw.get("is_flag")
            if isinstance(is_flag, ast.Constant) and is_flag.value is True:
                flags |= {n[2:] for n in longs}
            default = kw.get("default")
            if "--endian" in longs and isinstance(default, ast.Constant):
                endian = str(default.value)
                site = f"{main}:{dec.lineno}"
    return SignCli(frozenset(flags), endian, site)


def sign_command(img: ImageContext, flags: frozenset[str]) -> dict[str, Any] | None:
    """The `imgtool sign` producing a .bin in the image's build.ninja: {options, infile,
    outfile}. Options are split using imgtool's own flag list for that pin."""
    ninja = img.dir / "build.ninja"
    if not ninja.is_file():
        return None
    for line in ninja.read_text().splitlines():
        for part in line.split("&&"):
            if "imgtool.py" not in part or " sign " not in part:
                continue
            argv = shlex.split(part.replace("COMMAND = ", ""))
            i = argv.index("sign") if "sign" in argv else None
            if i is None:
                continue
            opts: dict[str, str] = {}
            pos: list[str] = []
            rest = argv[i + 1 :]
            k = 0
            while k < len(rest):
                a = rest[k]
                if a.startswith("--") and "=" in a:
                    key, val = a[2:].split("=", 1)
                    opts[key] = val
                elif a.startswith("--") and a[2:] in flags:
                    opts[a[2:]] = ""
                elif a.startswith("--") and k + 1 < len(rest):
                    opts[a[2:]] = rest[k + 1]
                    k += 1
                else:
                    pos.append(a)
                k += 1
            if len(pos) >= 2 and pos[-1].endswith(".bin"):
                return {"options": opts, "infile": pos[-2], "outfile": pos[-1]}
    return None
