"""Dump the final devicetree (edt.pickle) of one build as JSON.

Usage: edt_dump.py <zephyr>/scripts/dts/python-devicetree/src <edt.pickle> <root> <out.json>
<root> is the directory binding paths are made relative to (the workspace topdir).
"""

import json
import os
import pickle
import sys

sys.path.insert(0, sys.argv[1])
from devicetree import edtlib  # noqa: F401  (needed to unpickle)

root = sys.argv[3]


def rel(path):
    return os.path.relpath(path, root) if path else None


def node_id(node):
    """How a phandle target is recorded: by label when it has one, so renaming or moving
    the target doesn't show up as a change in every node that references it."""
    return "&" + node.labels[0] if node.labels else node.path


def norm(val):
    if isinstance(val, bytes):
        return {"bytes": val.hex()}
    if isinstance(val, list):
        return [norm(v) for v in val]
    if hasattr(val, "path") and hasattr(val, "labels"):  # a Node (phandle)
        return {"phandle": node_id(val)}
    if hasattr(val, "controller") and hasattr(val, "data"):  # ControllerAndData
        return {
            "controller": node_id(val.controller),
            "data": dict(val.data),
            "name": getattr(val, "name", None),
        }
    if val is None or isinstance(val, (int, str, float, bool)):
        return val
    return repr(val)


def dts_text(prop):
    text = str(prop)
    return text.split("=", 1)[1].strip().rstrip(";") if "=" in text else ""


def specs(node):
    binding = getattr(node, "_binding", None)
    p2s = getattr(binding, "prop2specs", None) or {}
    return {
        name: {
            "type": s.type,
            "required": bool(s.required),
            "deprecated": bool(getattr(s, "deprecated", False)),
        }
        for name, s in p2s.items()
    }


def main():
    with open(sys.argv[2], "rb") as f:
        edt = pickle.load(f)
    nodes = {}
    for node in edt.nodes:
        raw = getattr(node, "_node", None)
        nodes[node.path] = {
            "name": node.name,
            "labels": list(node.labels),
            "aliases": list(getattr(node, "aliases", [])),
            "compats": list(node.compats),
            "matching_compat": node.matching_compat,
            "status": node.status,
            "binding": rel(node.binding_path),
            "props": {k: norm(p.val) for k, p in node.props.items()},
            "raw_props": sorted(raw.props) if raw is not None else [],
            # The property as the DTS writes it (dtlib's rendering, phandles as &labels),
            # independent of what the node's binding declares or defaults.
            "raw_values": {k: dts_text(p) for k, p in raw.props.items()} if raw is not None else {},
            "specs": specs(node),
        }
    chosen = {}
    for name, n in (getattr(edt, "chosen_nodes", None) or {}).items():
        chosen[name] = n.path
    with open(sys.argv[4], "w") as f:
        json.dump({"nodes": nodes, "chosen": chosen}, f, sort_keys=True)


main()
