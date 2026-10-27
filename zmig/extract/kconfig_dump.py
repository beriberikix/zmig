"""Dump Kconfig symbol metadata for one build as JSON.

Usage: kconfig_dump.py <zephyr>/scripts/kconfig <Kconfig root> <out.json>
The environment must be the one Zephyr's build used for its Kconfig targets.
"""

import json
import os
import sys

sys.path.insert(0, sys.argv[1])
import kconfiglib

# kconfiglib's expr_str recurses once per operand; some trees have long `||` chains
# (nrf52840dk's app Kconfig at v4.3.0 overflows the default limit of 1000).
sys.setrecursionlimit(100_000)

srctree = os.environ.get("srctree", "")  # noqa: SIM112 (Zephyr's own name)


def rel(path):
    return os.path.relpath(path, srctree) if srctree and os.path.isabs(path) else path


# Tuple shapes differ across kconfiglib versions (defaults/selects gained a third
# element after v3.7), so entries are indexed rather than unpacked.


def choice_id(sym):
    c = sym.choice
    if c is None:
        return None
    if c.name:
        return c.name
    prompts = [n.prompt[0] for n in c.nodes if n.prompt]
    return prompts[0] if prompts else "<choice>"


def choice_prompt(sym):
    c = sym.choice
    if c is None:
        return None
    prompts = [n.prompt[0] for n in c.nodes if n.prompt]
    return prompts[0] if prompts else None


def menu(sym):
    """The prompt of the nearest enclosing menu (or menuconfig symbol), if any."""
    node = sym.nodes[0].parent if sym.nodes else None
    while node is not None and node.parent is not None:
        is_menu = node.item is kconfiglib.MENU or (
            isinstance(node.item, kconfiglib.Symbol) and node.is_menuconfig
        )
        if is_menu and node.prompt:
            return node.prompt[0]
        node = node.parent
    return None


def deprecated(sym):
    selects = {sel[0].name for sel in sym.selects}
    deps = kconfiglib.expr_str(sym.direct_dep)
    return "DEPRECATED" in selects or "DEPRECATED" in deps.split()


def main():
    kconf = kconfiglib.Kconfig(sys.argv[2], warn=False)
    out = {}
    for sym in kconf.unique_defined_syms:
        prompts = [n.prompt[0] for n in sym.nodes if n.prompt]
        helps = [n.help for n in sym.nodes if n.help]
        out[sym.name] = {
            "type": kconfiglib.TYPE_TO_STR[sym.type],
            "prompt": prompts[0] if prompts else None,
            "help": helps[0] if helps else None,
            "deprecated": deprecated(sym),
            "selects": sorted({sel[0].name for sel in sym.selects}),
            "depends": kconfiglib.expr_str(sym.direct_dep),
            "defaults": [
                kconfiglib.expr_str(v) + ("" if c is kconf.y else " if " + kconfiglib.expr_str(c))
                for v, c in ((d[0], d[1]) for d in sym.defaults)
            ],
            "locations": [f"{rel(n.filename)}:{n.linenr}" for n in sym.nodes],
            # The choice the symbol is a member of (name, or its prompt when unnamed).
            "choice": choice_id(sym),
            "choice_prompt": choice_prompt(sym),
            "menu": menu(sym),
        }
    with open(sys.argv[3], "w") as f:
        json.dump({"symbols": out}, f, sort_keys=True)


main()
