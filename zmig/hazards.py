"""Shared pieces of the field-upgrade hazard analyzers (docs/DESIGN-P1.md).

Role evidence: how sure we are which image is the bootloader the device runs.
  strong   the image's own .config says it is MCUboot (CONFIG_MCUBOOT=y)
  weak     only indirect evidence (the app is built for MCUboot, no bootloader image built)
  unknown  no evidence (a plain build that isn't built for a bootloader)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from zmig.buildctx import Artifact, BuildContext, ImageContext
from zmig.findings import JSON
from zmig.snapshots import config_values
from zmig.tools import Runner
from zmig.workspace import Module, RefWorkspace

RoleEvidence = Literal["strong", "weak", "unknown"]


@dataclass(frozen=True)
class BootloaderRole:
    image: ImageContext | None
    evidence: RoleEvidence
    source: str


def bootloader_role(b: BuildContext) -> BootloaderRole:
    boot = next((i for i in b.images if i.role == "bootloader"), None)
    if boot is not None:
        return BootloaderRole(boot, "strong", boot.role_source)
    app = next((i for i in b.images if i.role == "app"), None)
    if (
        app is not None
        and app.has(Artifact.CONFIG)
        and config_values(app).get("BOOTLOADER_MCUBOOT") == "y"
    ):
        return BootloaderRole(
            None,
            "weak",
            "the app is built for MCUboot "
            "(CONFIG_BOOTLOADER_MCUBOOT=y) but no bootloader image is built",
        )
    return BootloaderRole(None, "unknown", "no bootloader image and the app isn't built for one")


def provenance(evidence_source: str, role: BootloaderRole | None = None) -> JSON:
    return {
        "evidence_source": evidence_source,
        "role_source": role.source if role else "not applicable (no image role involved)",
        "role_evidence": role.evidence if role else "strong",
    }


# --- signing parameters ------------------------------------------------------------------


def signing_args(img: ImageContext) -> dict[str, str] | None:
    """The `imgtool sign` options the image's build runs, from its build.ninja; None if the
    image isn't signed by the build. This is what the device's bootloader will be handed:
    header size, slot size (the usable image size), alignment, key, version."""
    import shlex

    ninja = img.dir / "build.ninja"
    if not ninja.is_file():
        return None
    for line in ninja.read_text().splitlines():
        if "imgtool.py" not in line or " sign " not in line:
            continue
        part = next((p for p in line.split("&&") if "imgtool.py" in p and " sign " in p), "")
        argv = shlex.split(part.replace("COMMAND = ", ""))
        i = next((k for k, a in enumerate(argv) if a == "sign"), None)
        if i is None:
            continue
        out: dict[str, str] = {}
        rest = argv[i + 1 :]
        k = 0
        while k < len(rest):
            a = rest[k]
            if a.startswith("--") and "=" in a:
                key, val = a[2:].split("=", 1)
                out[key] = val
            elif a.startswith("--") and k + 1 < len(rest) and not rest[k + 1].startswith("--"):
                out[a[2:]] = rest[k + 1]
                k += 1
            elif a.startswith("--"):
                out[a[2:]] = ""
            k += 1
        return out
    return None


# --- category status for the upgrade-path verdict ----------------------------------------

CategoryStatus = Literal["clean", "hazard", "unknown", "not-applicable"]


def set_category(
    shared: dict[str, object],
    name: str,
    status: CategoryStatus,
    confidence: str,
    checked: list[str],
    findings: list[str] | None = None,
    reason: str | None = None,
    extra: JSON | None = None,
) -> None:
    """Record a hazard category's result where the verdict analyzer will read it."""
    cats = shared.setdefault("categories", {})
    assert isinstance(cats, dict)
    entry: JSON = {"status": status, "confidence": confidence, "checked": checked}
    if findings:
        entry["findings"] = findings
    if reason:
        entry["reason"] = reason
    if extra:
        entry.update(extra)
    cats[name] = entry


def mcuboot_module(ws: RefWorkspace, runner: Runner) -> Module | None:
    """The mcuboot west module of a workspace, if cloned."""
    return next((m for m in ws.modules(runner) if m.name == "mcuboot"), None)
