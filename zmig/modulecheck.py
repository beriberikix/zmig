"""Which Zephyr modules a target build needs that the scan didn't give it, and how the
modules a build uses changed between the two manifests.

A module is diagnosed as required from what the target build itself said:

  missing header   `fatal error: X.h: No such file or directory`, where the target's
                   zephyr/modules/<glue>/ ships X.h and <glue> names a module that isn't
                   active (bms-firmware at v4.2: `cmsis_core.h` lives in modules/cmsis and
                   modules/cmsis_6; cmsis was active, so cmsis_6 is the one missing)
  Kconfig          an unsatisfied dependency on ZEPHYR_<X>_MODULE, or a warning about a
                   symbol from zephyr/modules/<X>/Kconfig whose dependencies reduce to `&& 0`
  CMake            an error that names ZEPHYR_<X>_MODULE or ZEPHYR_<X>_MODULE_DIR

Only modules declared in the target ref's west.yml are proposed; each comes with where it
is required (the build's own message) and where the manifest declares it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from zmig.manifest import ProjectInfo

_MISSING_HEADER = re.compile(
    r"^(?P<file>[^\s:]+):(?P<line>\d+):(?:\d+:)? fatal error: (?P<header>[\w./-]+): "
    r"No such file or directory",
    re.M,
)
_KCONFIG_DEP = re.compile(r"\bZEPHYR_(?P<mod>[A-Z0-9_]+?)_MODULE\b(?!_DIR)")
_GLUE_KCONFIG = re.compile(r"(?<![\w/])modules/([\w.-]+)/Kconfig[\w.-]*:\d+")
_ZERO_DEPS = re.compile(r"direct dependencies (?:0|[^\n]*?&& 0)(?:\)| with value n)")
_CMAKE_ERROR = re.compile(r"^CMake Error.*?(?=^\S|\Z)", re.M | re.S)
_CMAKE_MOD = re.compile(r"\bZEPHYR_(?P<mod>[A-Z0-9_]+?)_MODULE(?:_DIR)?\b")


def module_var(name: str) -> str:
    """Zephyr's variable stem for a module name: upper case, non-identifier chars as _
    (cmake/modules/zephyr_module.cmake)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name).upper()


@dataclass(frozen=True)
class ModuleNeed:
    module: str  # west project name
    why: str  # one line: what in the build requires it
    site: str = ""  # where the build said so (file:line), as printed
    declared: str = ""  # where the target manifest declares it (west.yml:line)
    alternatives: tuple[str, ...] = field(default_factory=tuple)


def user_manifest(app: Path) -> tuple[Path, int, str] | None:
    """The user's own west manifest for `app`, and the line where a module is added to it:
    (file, line, what the line is). From a west workspace above the app (`.west/config`:
    manifest.path / manifest.file), else the nearest `west.yml` above the app, up to the
    app's git root, or 3 levels when it isn't in git. The line is the Zephyr import's
    `name-allowlist:`, else the `zephyr` project entry, else 1. None if there is none."""
    import configparser
    import subprocess

    app = app.resolve()
    proc = subprocess.run(
        ["git", "-C", str(app), "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    top = Path(proc.stdout.strip()).resolve() if proc.returncode == 0 else None
    found: Path | None = None
    for d in [app, *app.parents]:
        cfg = d / ".west" / "config"
        if cfg.is_file():
            c = configparser.ConfigParser()
            c.read(cfg)
            m = (
                d
                / c.get("manifest", "path", fallback="")
                / c.get("manifest", "file", fallback="west.yml")
            )
            if m.is_file():
                found = m
                break
    if found is None:
        limit = top if top is not None else app.parents[min(3, len(app.parents) - 1)]
        for d in [app, *app.parents]:
            if (d / "west.yml").is_file():
                found = d / "west.yml"
                break
            if d == limit:
                break
    if found is None:
        return None
    lines = found.read_text(errors="replace").splitlines()
    for pattern, what in (
        (r"^\s*name-allowlist:", "the name-allowlist of the Zephyr import"),
        (r"^\s*-\s*name:\s*zephyr\s*$", "the zephyr project entry"),
    ):
        for i, line in enumerate(lines, 1):
            if re.match(pattern, line):
                return found, i, what
    return found, 1, "the manifest"


def manifest_line(zephyr: Path, name: str) -> str:
    """`west.yml:<line>` of the project entry `name` in a Zephyr tree's manifest."""
    path = zephyr / "west.yml"
    if not path.is_file():
        return ""
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if re.match(rf"^\s*-\s*name:\s*{re.escape(name)}\s*$", line):
            return f"west.yml:{i}"
    return ""


def diagnose(
    log: str, zephyr: Path, manifest: dict[str, ProjectInfo], active: set[str]
) -> list[ModuleNeed]:
    """Modules the target build's log shows it needs, among the target manifest's
    projects that aren't active."""
    by_var = {module_var(n): n for n in manifest}
    needs: dict[str, ModuleNeed] = {}

    def add(name: str, why: str, site: str, alternatives: tuple[str, ...] = ()) -> None:
        if name in active or name not in manifest or name in needs:
            return
        needs[name] = ModuleNeed(name, why, site, manifest_line(zephyr, name), alternatives)

    glue = zephyr / "modules"
    for m in _MISSING_HEADER.finditer(log):
        header = m["header"]
        dirs = sorted(
            {p.relative_to(glue).parts[0] for p in glue.glob(f"*/**/{header}")}
            | {p.relative_to(glue).parts[0] for p in glue.glob(f"*/{header}")}
        )
        inactive = [d for d in dirs if d in manifest and d not in active]
        if dirs and len(inactive) == 1:
            add(
                inactive[0],
                f"`{header}` is missing; the target's zephyr/modules/{inactive[0]}/ provides it",
                f"{m['file']}:{m['line']}",
                tuple(d for d in dirs if d != inactive[0]),
            )
    # Kconfig warnings (Zephyr aborts on them) that name ZEPHYR_<X>_MODULE: an unmet
    # dependency on a module ("has direct dependencies ZEPHYR_TINYCRYPT_MODULE with value
    # n", "Check these unsatisfied dependencies: (ZEPHYR_ZCBOR_MODULE ...)").
    paras = [p for p in re.split(r"\n\s*\n", log) if p.lstrip().startswith("warning:")]
    for para in paras:
        first = para.strip().splitlines()[0]
        for d in _KCONFIG_DEP.finditer(para):
            if name := by_var.get(d["mod"]):
                add(name, f"a Kconfig dependency on ZEPHYR_{d['mod']}_MODULE is unmet", first)
    # A symbol from a module's glue Kconfig whose dependencies reduce to 0: Zephyr's Kconfig
    # for that module is there, the module isn't (nrf52840dk at v4.2 without cmsis_6:
    # "... modules/cmsis_6/Kconfig:23) has direct dependencies ... (HAS_CMSIS_CORE && 0)";
    # native_sim at v4.2 without mbedtls: "MBEDTLS (defined at modules/mbedtls/Kconfig:19)
    # has direct dependencies 0 with value n").
    for para in paras:
        if not _ZERO_DEPS.search(para):
            continue
        first = para.strip().splitlines()[0]
        for g in dict.fromkeys(_GLUE_KCONFIG.findall(first)):
            add(g, f"Kconfig from zephyr/modules/{g}/ is used, but the module is absent", first)
    for block in _CMAKE_ERROR.finditer(log):
        for d in _CMAKE_MOD.finditer(block[0]):
            if name := by_var.get(d["mod"]):
                first = block[0].splitlines()[0]
                add(name, f"CMake requires ZEPHYR_{d['mod']}_MODULE", first.strip())
    return list(needs.values())


@dataclass(frozen=True)
class ModuleChange:
    module: str
    kind: str  # "removed" | "repinned"
    old: str  # source manifest revision
    new: str  # target manifest revision, "" if removed
    declared: str  # west.yml:line at the side that declares it


def manifest_changes(
    used: list[str],
    old: dict[str, ProjectInfo],
    new: dict[str, ProjectInfo],
    old_zephyr: Path,
    new_zephyr: Path,
) -> list[ModuleChange]:
    """How the modules a source build used changed between the two Zephyr manifests."""
    out = []
    for name in sorted(set(used)):
        if name not in old:
            continue  # not one of Zephyr's modules (the project's own)
        if name not in new:
            out.append(
                ModuleChange(
                    name, "removed", old[name].revision, "", manifest_line(old_zephyr, name)
                )
            )
        elif old[name].revision != new[name].revision:
            out.append(
                ModuleChange(
                    name,
                    "repinned",
                    old[name].revision,
                    new[name].revision,
                    manifest_line(new_zephyr, name),
                )
            )
    return out
