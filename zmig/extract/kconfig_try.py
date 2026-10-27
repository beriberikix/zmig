"""Re-resolve one build's Kconfig with extra assignments, as Zephyr would.

Usage: kconfig_try.py <zephyr>/scripts/kconfig <Kconfig root> <request.json> <out.json>
request.json: {"fragments": [first (loaded), merged...], "assign": {"NAME": "y", ...},
               "report": ["NAME", ...]}
The fragments are the ones the build's configure log names, in order; the assignments
are merged last. The environment must be the one Zephyr's build used for its Kconfig
targets. out.json: {"values": {NAME: value or null}}.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
import kconfiglib

sys.setrecursionlimit(100_000)


def main():
    with open(sys.argv[3]) as f:
        req = json.load(f)
    kconf = kconfiglib.Kconfig(sys.argv[2], warn=False)
    frags = req["fragments"]
    kconf.load_config(frags[0], replace=True)
    for frag in frags[1:]:
        kconf.load_config(frag, replace=False)
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as t:
        for name, value in req["assign"].items():
            t.write(f"CONFIG_{name}={value}\n")
    try:
        kconf.load_config(t.name, replace=False)
    finally:
        os.unlink(t.name)
    values = {}
    for name in req["report"]:
        sym = kconf.syms.get(name)
        values[name] = sym.str_value if sym is not None and sym.nodes else None
    with open(sys.argv[4], "w") as f:
        json.dump({"values": values}, f, sort_keys=True)


main()
