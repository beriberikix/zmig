"""Reading the SPDX tag-value documents `west spdx` writes (SPDX 2.2 at v3.6, 2.3 later).

Only what the diff needs is read: packages and the files listed under each, with their
SHA1. In Zephyr's documents a package's files follow its PackageName in order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SpdxPackage:
    name: str
    files: dict[str, str] = field(default_factory=dict)  # path -> sha1


@dataclass
class SpdxDocument:
    name: str
    version: str
    packages: dict[str, SpdxPackage] = field(default_factory=dict)


def parse_spdx(text: str, name: str) -> SpdxDocument:
    doc = SpdxDocument(name=name, version="")
    pkg: SpdxPackage | None = None
    current_file: str | None = None
    for raw in text.splitlines():
        key, sep, value = raw.partition(": ")
        if not sep:
            continue
        value = value.strip()
        if key == "SPDXVersion":
            doc.version = value
        elif key == "PackageName":
            pkg = doc.packages.setdefault(value, SpdxPackage(value))
            current_file = None
        elif key == "FileName" and pkg is not None:
            current_file = value.removeprefix("./")
            pkg.files[current_file] = ""
        elif key == "FileChecksum" and value.startswith("SHA1:") and pkg and current_file:
            pkg.files[current_file] = value.split(":", 1)[1].strip()
    return doc


def read_spdx_dir(path: Path) -> dict[str, SpdxDocument]:
    """{document name: document} for every *.spdx in a `west spdx` output directory."""
    return {f.stem: parse_spdx(f.read_text(), f.stem) for f in sorted(path.glob("*.spdx"))}


ZEPHYR_PACKAGES = {"zephyr-sources", "zephyr"}


def compiled_sources(docs: dict[str, SpdxDocument]) -> dict[str, dict[str, str]]:
    """{package (zephyr or module name): {file: sha1}} from the zephyr document."""
    zdoc = docs.get("zephyr")
    if zdoc is None:
        return {}
    out: dict[str, dict[str, str]] = {}
    for name, pkg in zdoc.packages.items():
        key = "zephyr" if name in ZEPHYR_PACKAGES else name.removesuffix("-sources")
        out.setdefault(key, {}).update(pkg.files)
    return out
