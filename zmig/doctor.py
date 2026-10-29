"""`zmig doctor`: check the host tools and the toolchain a board needs, before a scan.

Each check prints ok, warn or FAIL with what was found and, when it isn't ok, what to do.
The board's architecture comes from its own `<board>.yaml` in the cached Zephyr mirror
(at `--ref`, default the mirror's HEAD), so the SDK check needs one earlier clone; without
it, the SDKs found are listed but not matched to the board.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from zmig.buildctx import is_host_board
from zmig.cache import Cache
from zmig.tools import INSTALL_HINTS, ZEPHYR_PY_MODULES

ZEPHYR_MIRROR = "github.com_zephyrproject-rtos_zephyr.git"
MIN_GIT = (2, 38)
MIN_CMAKE = (3, 20, 5)
MIN_PYTHON = (3, 12)

# The Zephyr SDK's GNU toolchain for each `arch:` in a board's yaml (xtensa toolchains are
# per SoC: xtensa-<vendor>_<soc>_zephyr-elf).
ARCH_TOOLCHAINS = {
    "arm": "arm-zephyr-eabi",
    "arm64": "aarch64-zephyr-elf",
    "arc": "arc-zephyr-elf",
    "riscv": "riscv64-zephyr-elf",
    "x86": "x86_64-zephyr-elf",
    "mips": "mips-zephyr-elf",
    "sparc": "sparc-zephyr-elf",
    "microblaze": "microblazeel-zephyr-elf",
    "rx": "rx-zephyr-elf",
    "openrisc": "or1k-zephyr-elf",
}


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # ok | warn | FAIL
    detail: str
    action: str = ""


def _version(text: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    return tuple(int(g) for g in m.groups() if g is not None) if m else ()


def _show(v: tuple[int, ...]) -> str:
    return ".".join(map(str, v))


def _run(argv: list[str]) -> str | None:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout + proc.stderr if proc.returncode == 0 else None


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def _versioned_tool(name: str, minimum: tuple[int, ...] | None) -> Check:
    exe = shutil.which(name)
    if exe is None:
        return Check(name, "FAIL", "not found on PATH", INSTALL_HINTS[name])
    out = _run([exe, "--version"])
    found = _version(out or "")
    if out is None or not found:
        return Check(name, "FAIL", f"{exe}: `{name} --version` failed", INSTALL_HINTS[name])
    if minimum and found < minimum:
        return Check(
            name,
            "FAIL",
            f"{exe}: {_show(found)}, need >= {_show(minimum)}",
            INSTALL_HINTS[name],
        )
    return Check(name, "ok", f"{exe}: {_show(found)}")


def check_python() -> list[Check]:
    v = sys.version_info[:3]
    out = [
        Check(
            "python",
            "ok" if v[:2] >= MIN_PYTHON else "FAIL",
            f"{sys.executable}: {_show(v)}",
            "" if v[:2] >= MIN_PYTHON else "use Python >= 3.12 for zmig's venv",
        )
    ]
    missing = [pkg for mod, pkg in ZEPHYR_PY_MODULES.items() if not importlib.util.find_spec(mod)]
    out.append(
        Check(
            "python packages",
            "FAIL" if missing else "ok",
            f"missing: {', '.join(missing)}" if missing else "Zephyr's build-script packages",
            "install zmig with its zephyr extra: uv pip install -e '.[zephyr]'" if missing else "",
        )
    )
    if pp := os.environ.get("PYTHONPATH"):
        out.append(
            Check(
                "PYTHONPATH",
                "warn",
                f"set ({pp[:80]}{'…' if len(pp) > 80 else ''})",
                "zmig removes it from the builds and tools it runs; if zmig itself fails to "
                "import, run it as `env -u PYTHONPATH zmig …`",
            )
        )
    return out


def check_host_gcc(board: str | None) -> Check:
    """native_sim builds 32-bit by default; native_sim/native/64 (native_sim_64) 64-bit."""
    gcc = shutil.which("gcc")
    if gcc is None:
        return Check("host gcc", "FAIL", "not found on PATH", INSTALL_HINTS["gcc"])
    if board and not is_host_board(board):
        return Check("host gcc", "ok", f"{gcc} (32-bit not needed for {board})")
    bits = "64" if board and ("64" in board.split("/")[-1] or board.endswith("_64")) else "32"
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "t.c"
        src.write_text("int main(void) { return 0; }\n")
        ok = _run([gcc, f"-m{bits}", str(src), "-o", str(Path(d) / "t")]) is not None
    if ok:
        return Check(f"host gcc -m{bits}", "ok", f"{gcc} compiles and links -m{bits}")
    return Check(
        f"host gcc -m{bits}",
        "FAIL",
        f"{gcc} cannot link a -m{bits} program",
        INSTALL_HINTS["gcc"] if bits == "32" else "install a host GCC",
    )


def find_sdks(explicit: Path | None, cache: Cache) -> list[tuple[Path, str]]:
    """Zephyr SDKs where Zephyr's own lookup would find them, and zmig's cache."""
    cands: list[Path] = []
    if explicit:
        cands.append(explicit.expanduser())
    if env := os.environ.get("ZEPHYR_SDK_INSTALL_DIR"):
        cands.append(Path(env).expanduser())
    reg = Path.home() / ".cmake" / "packages" / "Zephyr-sdk"
    if reg.is_dir():
        for f in sorted(reg.iterdir()):
            text = f.read_text(errors="replace").strip()
            if text:
                cands.append(Path(text).parent)  # the registry names <sdk>/cmake
    home = Path.home()
    for root in (home, home / ".local", home / ".local/opt", home / "bin", Path("/opt"),
                 Path("/usr"), Path("/usr/local"), cache.root / "sdks"):  # fmt: skip
        if root.is_dir():
            cands.extend(sorted(root.glob("zephyr-sdk-*")))
    out, seen = [], set()
    for c in cands:
        version_file = c / "sdk_version"
        if not version_file.is_file():
            continue
        key = c.resolve()
        if key in seen:
            continue
        seen.add(key)
        out.append((c, version_file.read_text().strip()))
    return out


def has_toolchain(sdk: Path, triple: str) -> bool:
    """`triple` may be a glob (xtensa-*_esp32s3_zephyr-elf)."""
    return any(
        (t / "bin" / f"{t.name}-gcc").exists()
        for base in (sdk / "gnu", sdk)  # SDK >= 1.0 keeps GNU toolchains under gnu/
        if base.is_dir()
        for t in base.glob(triple)
    )


def board_yaml(mirror: Path, ref: str, board: str) -> tuple[str, dict[str, str]] | None:
    """(path, {arch, toolchain}) of the board's yaml at `ref`: the one whose identifier is
    the board name (with or without its qualifiers)."""
    first = board.split("/")[0]
    names = _git(mirror, "ls-tree", "-r", "--name-only", ref, "boards")
    if names is None:
        return None
    paths = [
        p
        for p in names.splitlines()
        if p.endswith(".yaml") and (f"/{first}/" in p or p.rsplit("/", 1)[-1].startswith(first))
    ]
    best = None
    for p in paths:
        text = _git(mirror, "show", f"{ref}:{p}") or ""
        ident = re.search(r"^identifier:\s*(\S+)", text, re.M)
        if not ident:
            continue
        fields = {
            "arch": (re.search(r"^arch:\s*(\S+)", text, re.M) or [None, ""])[1] or "",
            "toolchain": ",".join(re.findall(r"^\s*-\s*(\S+)", _section(text, "toolchain"), re.M)),
            "identifier": ident[1],
        }
        if ident[1] == board:
            return p, fields
        if best is None and (ident[1] == first or ident[1].startswith(first + "/")):
            best = (p, fields)
    return best


def _section(text: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*\n((?:\s+-.*\n?)+)", text, re.M)
    return m[1] if m else ""


def _git(mirror: Path, *args: str) -> str | None:
    return _run(["git", "-C", str(mirror), *args])


def check_sdk(board: str | None, sdk: Path | None, ref: str, cache: Cache) -> list[Check]:
    sdks = find_sdks(sdk, cache)
    listing = ", ".join(f"{p} ({v})" for p, v in sdks) or "none"
    if board and is_host_board(board):
        return [Check("zephyr sdk", "ok", f"not needed for {board} (host build); found: {listing}")]
    if sdk and not any(p == sdk.expanduser() for p, _ in sdks):
        return [
            Check(
                "zephyr sdk",
                "FAIL",
                f"--sdk {sdk}: no sdk_version file there",
                "point --sdk at the SDK's top directory (the one with sdk_version and cmake/)",
            )
        ]
    if not board:
        status = "ok" if sdks else "warn"
        return [
            Check("zephyr sdk", status, f"found: {listing}", "" if sdks else _sdk_install_hint())
        ]
    mirror = cache.mirrors / ZEPHYR_MIRROR
    if not mirror.is_dir():
        return [
            Check(
                "zephyr sdk",
                "warn",
                f"cannot tell which toolchain {board} needs: no Zephyr mirror in {cache.root} "
                f"yet; found: {listing}",
                "run any zmig scan or plan once (it clones Zephyr), then rerun zmig doctor",
            )
        ]
    found = board_yaml(mirror, ref, board)
    if found is None:
        return [
            Check(
                "board",
                "FAIL",
                f"{board}: no board yaml with that identifier in Zephyr {ref}",
                "check the board name (`west boards`), or pass --ref for an older release",
            )
        ]
    path, fields = found
    arch, ident = fields["arch"], fields["identifier"]
    if sdk:  # the scan will use this one: judge it alone
        sdks = [(p, v) for p, v in sdks if p == sdk.expanduser()]
        listing = ", ".join(f"{p} ({v})" for p, v in sdks)
    out = [Check("board", "ok", f"{ident}: arch {arch} ({path} at {ref})")]
    if arch == "xtensa":
        soc = ident.split("/")[1] if "/" in ident else ""
        want = f"xtensa-*_{soc}_zephyr-elf" if soc else "xtensa-*_zephyr-elf"
        have = [(p, v) for p, v in sdks if has_toolchain(p, want)]
    else:
        triple = ARCH_TOOLCHAINS.get(arch, "")
        want = triple or f"(unknown arch {arch})"
        have = [(p, v) for p, v in sdks if triple and has_toolchain(p, triple)]
    if "zephyr" not in fields["toolchain"].split(","):
        out.append(
            Check(
                "zephyr sdk",
                "warn",
                f"{ident} doesn't list the Zephyr SDK among its toolchains "
                f"({fields['toolchain'] or 'none listed'})",
                "set ZEPHYR_TOOLCHAIN_VARIANT for the toolchain it needs",
            )
        )
        return out
    if not have:
        out.append(
            Check(
                "zephyr sdk",
                "FAIL",
                f"no SDK with the {want} toolchain; found: {listing}",
                _sdk_install_hint(want),
            )
        )
        return out
    need = _git(mirror, "show", f"{ref}:SDK_VERSION")
    need_v = _version(need or "")
    ok = [(p, v) for p, v in have if not need_v or _version(v) >= need_v]
    if need_v and not ok:
        out.append(
            Check(
                "zephyr sdk",
                "FAIL",
                f"{want}: found in {', '.join(f'{p} ({v})' for p, v in have)}, but Zephyr "
                f"{ref} needs SDK >= {_show(need_v)}",
                _sdk_install_hint(want),
            )
        )
        return out
    p, v = ok[0]
    out.append(
        Check(
            "zephyr sdk",
            "ok",
            f"{want} in {p} ({v})"
            + (f"; Zephyr {ref} needs >= {_show(need_v)}" if need_v else "")
            + (f"; pass --sdk {p} to zmig scan" if sdk is None and len(sdks) > 1 else ""),
        )
    )
    return out


def _sdk_install_hint(toolchain: str = "") -> str:
    return (
        "install the Zephyr SDK (https://docs.zephyrproject.org/latest/develop/toolchains/"
        "zephyr_sdk.html)"
        + (f" with the {toolchain} toolchain" if toolchain else "")
        + ", then pass --sdk <dir> to zmig scan"
    )


def run_checks(board: str | None, sdk: Path | None, ref: str, cache: Cache) -> list[Check]:
    return [
        *check_python(),
        _versioned_tool("git", MIN_GIT),
        _versioned_tool("cmake", MIN_CMAKE),
        _versioned_tool("ninja", None),
        _versioned_tool("dtc", None),
        check_host_gcc(board),
        *check_sdk(board, sdk, ref, cache),
    ]


def format_checks(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = []
    for c in checks:
        lines.append(f"{c.status:<4}  {c.name:<{width}}  {c.detail}")
        if c.action:
            lines.append(f"{'':<4}  {'':<{width}}  -> {c.action}")
    fails = sum(c.status == "FAIL" for c in checks)
    warns = sum(c.status == "warn" for c in checks)
    summary = f"{fails} check(s) failed" if fails else "ready"
    if warns:
        summary += f", {warns} warning(s)"
    return "\n".join([*lines, "", summary])
