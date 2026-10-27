"""Which options sysbuild forces on which image, read from the tree being analyzed.

Sysbuild sets per-image Kconfig from SB_CONFIG_* in
share/sysbuild/image_configurations/{MAIN,BOOTLOADER}_image_default.cmake, e.g.

    if(SB_CONFIG_MCUBOOT_MODE_SWAP_USING_OFFSET)          # MAIN
      set_config_bool(${ZCMAKE_APPLICATION} CONFIG_MCUBOOT_BOOTLOADER_MODE_SWAP_USING_OFFSET y)

    elseif(SB_CONFIG_MCUBOOT_MODE_SWAP_USING_OFFSET)      # BOOTLOADER
      set(bootmode CONFIG_BOOT_SWAP_USING_OFFSET)
    ...
    foreach(loopbootmode ${bootmodes})
      if("${loopbootmode}" STREQUAL "${bootmode}")
        set_config_bool(${ZCMAKE_APPLICATION} ${loopbootmode} y)

An app option and a bootloader option enabled by the same SB_CONFIG symbol are a pair
that must agree: the app is built for the bootloader mode the bootloader implements.

This reads the small CMake subset those files use (if/elseif/else/endif, set() of a
CONFIG_ name, the foreach + STREQUAL indirection above, set_config_*). Lines it doesn't
understand are ignored, so a construct added later yields fewer rules, never wrong ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

IMAGE_FILES = {
    "app": "share/sysbuild/image_configurations/MAIN_image_default.cmake",
    "bootloader": "share/sysbuild/image_configurations/BOOTLOADER_image_default.cmake",
}

_CALL = re.compile(r"^\s*(\w+)\s*\((.*)\)\s*$")
_SB = re.compile(r"\bSB_CONFIG_\w+")
_STREQ = re.compile(r'"\$\{(\w+)\}"\s+STREQUAL\s+"\$\{(\w+)\}"')


@dataclass(frozen=True)
class Directive:
    role: str  # "app" | "bootloader"
    triggers: tuple[str, ...]  # SB_CONFIG symbols of the innermost enabling condition
    option: str  # CONFIG_...
    value: str  # "y", "n", or the literal value
    file: str
    line: int


@dataclass(frozen=True)
class Pair:
    trigger: str
    app: Directive
    bootloader: Directive


def _strip(line: str) -> str:
    return line.split("#", 1)[0].rstrip() if '"#' not in line else line


def parse(text: str, role: str, file: str) -> list[Directive]:
    """Directives that set an option to a value under a positive SB_CONFIG condition."""
    stack: list[str] = []  # innermost condition text per open if; "" for else
    assigns: dict[str, list[tuple[tuple[str, ...], str, int]]] = {}
    loops: list[tuple[str, str]] = []  # (loop var, list var)
    out: list[Directive] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = _strip(lines[i])
        # Join a call that spans lines.
        while raw.count("(") > raw.count(")") and i + 1 < len(lines):
            i += 1
            raw += " " + _strip(lines[i]).strip()
        lineno = i + 1
        i += 1
        m = _CALL.match(raw)
        if not m:
            continue
        cmd, args = m[1].lower(), m[2].strip()
        if cmd == "if":
            stack.append(args)
        elif cmd == "elseif" and stack:
            stack[-1] = args
        elif cmd == "else" and stack:
            stack[-1] = ""
        elif cmd == "endif" and stack:
            stack.pop()
        elif cmd == "foreach":
            parts = args.split()
            if len(parts) == 2 and (lm := re.fullmatch(r"\$\{(\w+)\}", parts[1])):
                loops.append((parts[0], lm[1]))
        elif cmd == "endforeach" and loops:
            loops.pop()
        elif cmd == "set":
            parts = args.split()
            if len(parts) == 2 and parts[1].startswith("CONFIG_"):
                triggers = tuple(_SB.findall(stack[-1])) if stack and stack[-1] else ()
                assigns.setdefault(parts[0], []).append((triggers, parts[1], lineno))
        elif cmd.startswith("set_config_"):
            parts = args.split(None, 2)
            if len(parts) < 3:
                continue
            option, value = parts[1], parts[2].strip().strip('"')
            vm = re.fullmatch(r"\$\{(\w+)\}", option)
            if vm and loops and vm[1] == loops[-1][0]:
                # foreach + STREQUAL: the option is whichever CONFIG_ the compared variable
                # was set to, under that assignment's condition.
                cond = next((c for c in reversed(stack) if _STREQ.search(c)), "")
                sm = _STREQ.search(cond)
                if sm and value == "y":
                    var = sm[2] if sm[1] == vm[1] else sm[1]
                    for triggers, opt, _ in assigns.get(var, []):
                        if triggers:
                            out.append(Directive(role, triggers, opt, "y", file, lineno))
                continue
            if not option.startswith("CONFIG_"):
                continue
            if (sb := re.fullmatch(r"\$\{(SB_CONFIG_\w+)\}", value)) is not None:
                out.append(Directive(role, (sb[1],), option, "y", file, lineno))
                continue
            if stack and stack[-1] and (triggers := tuple(_SB.findall(stack[-1]))):
                out.append(Directive(role, triggers, option, value, file, lineno))
    return out


def pairs(app: list[Directive], bootloader: list[Directive]) -> list[Pair]:
    """App and bootloader options switched on by the same SB_CONFIG symbol."""
    out = []
    for a in app:
        if a.value != "y":
            continue
        for b in bootloader:
            if b.value != "y":
                continue
            for t in sorted(set(a.triggers) & set(b.triggers)):
                out.append(Pair(t, a, b))
    return sorted(set(out), key=lambda p: (p.trigger, p.app.option, p.bootloader.option))
