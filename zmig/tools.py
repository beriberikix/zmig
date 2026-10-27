"""External tool discovery and the single logged subprocess wrapper.

Every command zmig runs goes through Runner.run(), which logs argv, cwd,
environment overrides, exit status and output to <out>/logs/ and keeps an
Invocation record for logs/invocations.json.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from zmig.errors import ToolFailed, ToolMissing

log = logging.getLogger("zmig")

INSTALL_HINTS: dict[str, str] = {
    "git": "install git (>= 2.38, for `git merge-tree --write-tree`)",
    "cmake": "install CMake >= 3.20 (https://cmake.org/download/)",
    "ninja": "install Ninja (e.g. `apt install ninja-build`)",
    "dtc": "install the device tree compiler (e.g. `apt install device-tree-compiler`)",
    "gcc": "install a host GCC with 32-bit multilib (e.g. `apt install gcc-multilib`)",
}

# Python modules Zephyr's build scripts import. Checked up front so a missing one
# fails with an install hint instead of a CMake traceback.
ZEPHYR_PY_MODULES: dict[str, str] = {
    "elftools": "pyelftools",
    "yaml": "PyYAML",
    "pykwalify": "pykwalify",
    "packaging": "packaging",
    "west": "west",
}


def find_tool(name: str, explicit: str | None = None) -> Path:
    """Locate an executable: explicit path, active venv, zmig's own env, then PATH."""
    hint = INSTALL_HINTS.get(name, f"install {name} and make sure it is on PATH")
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return p
        raise ToolMissing(f"{name} not found at {explicit}; {hint}")
    candidates: list[Path] = []
    if venv := os.environ.get("VIRTUAL_ENV"):
        candidates.append(Path(venv) / "bin" / name)
    candidates.append(Path(sys.prefix) / "bin" / name)
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    if found := shutil.which(name):
        return Path(found)
    raise ToolMissing(f"{name} not found; {hint}")


# MCUboot's imgtool, run by sysbuild when an image includes MCUboot.
MCUBOOT_PY_MODULES: dict[str, str] = {
    "cryptography": "cryptography",
    "intelhex": "intelhex",
    "click": "click",
    "cbor2": "cbor2",
}


def check_mcuboot_python() -> None:
    missing = [
        pkg for mod, pkg in MCUBOOT_PY_MODULES.items() if importlib.util.find_spec(mod) is None
    ]
    if missing:
        raise ToolMissing(
            f"MCUboot's imgtool needs Python packages missing from {sys.executable}: "
            f"{', '.join(missing)}. Install them with `pip install 'zmig[mcuboot]'`."
        )


def check_zephyr_python() -> None:
    """Fail clearly if zmig's interpreter can't run Zephyr's build scripts."""
    missing = [
        pkg for mod, pkg in ZEPHYR_PY_MODULES.items() if importlib.util.find_spec(mod) is None
    ]
    if missing:
        raise ToolMissing(
            f"Python packages needed by Zephyr's build scripts are missing from "
            f"{sys.executable}: {', '.join(missing)}. "
            f"Install them with `pip install 'zmig[zephyr]'`."
        )


@dataclass(frozen=True)
class Invocation:
    id: str
    argv: list[str]
    cwd: str
    exit: int
    log: str
    duration_s: float

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "argv": self.argv,
            "cwd": self.cwd,
            "exit": self.exit,
            "log": self.log,
            "duration_s": round(self.duration_s, 3),
        }


@dataclass
class Runner:
    """Runs external commands, logging each one."""

    log_dir: Path | None = None
    redactions: dict[str, str] = field(default_factory=dict)
    invocations: list[Invocation] = field(default_factory=list)

    def redact(self, s: str) -> str:
        """Replace machine-specific prefixes (cache root, home) with stable tokens."""
        for prefix, token in sorted(self.redactions.items(), key=lambda kv: -len(kv[0])):
            s = s.replace(prefix, token)
        return s

    def run(
        self,
        argv: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        unset_env: Sequence[str] = (),
        check: bool = True,
        input: str | None = None,
        merge_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command. With merge_output, stderr is interleaved into stdout in the
        order the tool wrote them (needed to attribute warnings), and stderr is empty."""
        args = [str(a) for a in argv]
        inv_id = f"cmd-{len(self.invocations) + 1:03d}"
        full_env = dict(os.environ)
        for k in unset_env:
            full_env.pop(k, None)
        full_env.update(env or {})
        log.info("$ %s%s", " ".join(args), f"  (in {cwd})" if cwd else "")
        start = time.monotonic()
        proc = subprocess.run(
            args,
            cwd=cwd,
            env=full_env,
            input=input,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_output else subprocess.PIPE,
            text=True,
            errors="replace",
        )
        if proc.stderr is None:
            proc.stderr = ""
        duration = time.monotonic() - start

        log_rel = ""
        if self.log_dir is not None:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.log_dir / f"{inv_id}.log"
            env_lines = "".join(f"  {k}={v}\n" for k, v in sorted((env or {}).items()))
            log_path.write_text(
                f"argv: {args}\ncwd: {cwd}\nenv overrides:\n{env_lines}"
                f"unset: {list(unset_env)}\nexit: {proc.returncode}\n"
                f"duration_s: {duration:.3f}\n"
                f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
            )
            log_rel = f"logs/{log_path.name}"
        self.invocations.append(
            Invocation(
                id=inv_id,
                argv=[self.redact(a) for a in args],
                cwd=self.redact(str(cwd or Path.cwd())),
                exit=proc.returncode,
                log=log_rel,
                duration_s=duration,
            )
        )
        if proc.returncode != 0:
            log.debug("exit %d: %s", proc.returncode, proc.stderr.strip()[-2000:])
        if check and proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-15:]
            where = f" (full log: {self.log_dir / (inv_id + '.log')})" if self.log_dir else ""
            raise ToolFailed(
                f"command failed with exit {proc.returncode}: {' '.join(args)}{where}\n"
                + "\n".join(tail)
            )
        return proc


def tool_version(runner: Runner, name: str, argv: Sequence[str]) -> str | None:
    """First line of `<tool> --version`, or None if the tool isn't available."""
    try:
        exe = find_tool(name)
    except ToolMissing:
        return None
    proc = runner.run([exe, *argv], check=False)
    out = (proc.stdout or proc.stderr).strip().splitlines()
    return out[0].strip() if out else None
