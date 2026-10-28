"""The upgrade-path verdict (docs/DESIGN-P1.md §8): can a device running the source build
take the target build by app OTA?

Inputs are only what the hazard analyzers recorded (`shared["categories"]`, via
zmig.hazards.set_category) and the findings. Rules:

  hazard        a finding with a remedy and severity risky or breaking, introduced by the
                migration
  categories    every deployment-relevant category must be present; one whose analyzer
                didn't run or failed is unknown. sysbuild, images (bootloader identity)
                and crossimage are derived from their findings.
  pre-existing  a breaking finding that is not introduced by the migration, in layout,
                mcuboot, crossimage or storage, makes that category unknown and caps the
                verdict confidence at low (§8.3): the deployed state can't be the source
                build it contradicts
  remedies      bootloader-update resolves to reflash unless bootloader updatability is
                evidenced (§5.3)
  verdict       blocked > needs-changes > unknown > direct-ok; it starts as unknown and is
                never defaulted to OK
  confidence    the minimum over clean and hazard categories (and low when capped)
  basis         what clean categories rest on: implementation-only files (§5.7) and the
                wire allowlist caveat
"""

from __future__ import annotations

from typing import Any

from zmig.findings import (
    JSON,
    NOT_BY_APP_OTA,
    AnalyzerStatus,
    Category,
    Finding,
    Remedy,
    Severity,
)

# Categories the verdict needs, and the analyzer that decides each.
CATEGORIES = {
    "layout": "layout",
    "mcuboot": "mcuboot",
    "storage": "storage",
    "ble": "ble",
    "wire": "wire",
    "sysbuild": "sysbuild",
    "images": "images",
    "crossimage": "crossimage",
}
DERIVED = {
    "sysbuild": Category.SYSBUILD,
    "images": Category.IMAGES,
    "crossimage": Category.CROSSIMAGE,
}
DEPLOYMENT = {
    "layout": Category.LAYOUT,
    "mcuboot": Category.MCUBOOT,
    "crossimage": Category.CROSSIMAGE,
    "storage": Category.STORAGE,
}
RANK = {"high": 2, "medium": 1, "low": 0}
# Pre-existing findings of one category that also contradict another: a partition the app
# and the bootloader disagree on is a fact about the flash layout too (xapp-mismatch).
TOUCHES = {("layout", Category.CROSSIMAGE, "partition")}


def _hazard(f: Finding) -> bool:
    return f.remedy is not None and f.severity is not Severity.INFO and f.introduced_by_migration


def _ran(statuses: list[AnalyzerStatus], analyzer: str) -> str | None:
    """'ok' when every run of the analyzer (all images) succeeded, else why not."""
    runs = [s for s in statuses if s.name == analyzer or s.name.startswith(analyzer + "@")]
    if not runs:
        return "not run"
    bad = [s for s in runs if s.status != "ok"]
    return (
        None
        if not bad
        else "; ".join(f"{s.name} {s.status}: {s.reason or ''}".strip() for s in bad)
    )


def upgrade_path(
    shared: dict[str, Any],
    findings: list[Finding],
    statuses: list[AnalyzerStatus],
    target_status: str = "ok",
) -> JSON:
    recorded: dict[str, Any] = dict(shared.get("categories", {}))
    cats: dict[str, dict[str, Any]] = {}
    for name, analyzer in CATEGORIES.items():
        why_not = _ran(statuses, analyzer)
        if why_not is not None:
            cats[name] = {
                "status": "unknown",
                "confidence": "low",
                "checked": [],
                "reason": f"analyzer {analyzer}: {why_not}",
            }
            continue
        if name in DERIVED and name not in recorded:
            fs = [f for f in findings if f.category is DERIVED[name] and _hazard(f)]
            cats[name] = {
                "status": "hazard" if fs else "clean",
                "confidence": min(
                    (f.confidence.value for f in fs), key=RANK.__getitem__, default="high"
                ),
                "checked": [f"{name} findings of the analyzer"],
                **({"findings": [f.id for f in fs]} if fs else {}),
            }
            continue
        if name not in recorded:
            cats[name] = {
                "status": "unknown",
                "confidence": "low",
                "checked": [],
                "reason": f"analyzer {analyzer} recorded no result",
            }
            continue
        cats[name] = dict(recorded[name])

    # §8.3 pre-existing breaking findings.
    assumptions: list[str] = list(recorded.get("mcuboot", {}).get("assumptions", []))
    if not assumptions:
        assumptions.append("deployed app == the source build's app image")
    capped = False
    for name, cat in DEPLOYMENT.items():
        pre = [
            f
            for f in findings
            if (f.category is cat or (name, f.category, f.kind) in TOUCHES)
            and f.severity is Severity.BREAKING
            and not f.introduced_by_migration
        ]
        if pre:
            capped = True
            cats[name] = {
                **cats[name],
                "status": "unknown",
                "confidence": "low",
                "reason": "the source build is inconsistent, so the deployed state can't be "
                "the source build: " + ", ".join(f.id for f in pre),
            }
            assumptions.append(
                "deployed == source build: contradicted by "
                + ", ".join(f.id for f in pre)
                + f" ({name} treated as unknown; verdict confidence capped at low)"
            )

    # Hazards and remedies.
    updatable = recorded.get("mcuboot", {}).get("updatable") == "yes"
    hazards = [f for f in findings if _hazard(f)]
    remedies: dict[str, list[str]] = {}
    resolved: list[str] = []
    for f in hazards:
        r = f.remedy
        assert r is not None
        if r is Remedy.BOOTLOADER_UPDATE and not updatable:
            resolved.append(
                f"{f.id}: bootloader-update → reflash (bootloader updatability not evidenced)"
            )
            r = Remedy.REFLASH
        remedies.setdefault(r.value, []).append(f.id)

    # A breaking/risky finding in a hazard category without a remedy, or a category an
    # analyzer recorded as a hazard, still rules out direct-ok; its remedy is unknown
    # (regression: B-rak's layout category said "hazard" while the verdict said direct-ok,
    # docs/DOGFOOD-3-baseline.md M8).
    hazard_categories = {Category(n) for n in CATEGORIES}
    for f in findings:
        if (
            f.category in hazard_categories
            and f.severity is not Severity.INFO
            and f.introduced_by_migration
            and f.remedy is None
        ):
            remedies.setdefault(Remedy.UNKNOWN.value, []).append(f.id)
    for name, c in cats.items():
        if c["status"] == "hazard" and not any(
            fid in ids for fid in c.get("findings", []) for ids in remedies.values()
        ):
            remedies.setdefault(Remedy.UNKNOWN.value, []).append(f"category:{name}")
    if hazards or remedies:
        verdict = (
            "blocked" if any(Remedy(r) in NOT_BY_APP_OTA for r in remedies) else "needs-changes"
        )
    elif any(c["status"] == "unknown" for c in cats.values()):
        verdict = "unknown"
    else:
        verdict = "direct-ok"
    judged = [c["confidence"] for c in cats.values() if c["status"] in ("clean", "hazard")]
    confidence = min(judged, key=RANK.__getitem__, default="low")
    if capped:
        confidence = "low"
    if target_status != "ok":
        # Categories then rest on configure-time data and whatever the failed build left;
        # "direct-ok" must not read as established (regression: bms-firmware's native_sim
        # and OLED targets failed to build, docs/DOGFOOD-3-baseline.md M11).
        confidence = "low"

    basis: list[str] = []
    for name, c in cats.items():
        if c["status"] != "clean":
            continue
        impl = c.get("implementation_only") or []
        if impl:
            basis.append(
                f"{name}: clean on definitions; implementation-only changes in "
                f"{len(impl)} files: " + ", ".join(impl)
            )
        if name == "wire":
            basis.append("wire: clean means only that no allowlisted item changed")
    basis += resolved
    if target_status != "ok":
        basis.append(
            f"the target {target_status.replace('-', ' ')}: the categories come from "
            "its configuration and whatever the failed build produced; confidence capped at low"
        )
    if verdict == "unknown":
        basis.append(
            "unknown: "
            + ", ".join(
                f"{n} ({c.get('reason', 'unknown')})"
                for n, c in cats.items()
                if c["status"] == "unknown"
            )
        )
    # Categories nothing was established for: a hazard elsewhere still decides the
    # verdict (precedence), but the remedies are then a lower bound, and the report says so
    # (regression: smp_svr was "needs-changes" with storage unchecked, worded as if the
    # listed changes were enough).
    unchecked = sorted(n for n, c in cats.items() if c["status"] == "unknown")
    if unchecked and verdict in ("needs-changes", "blocked"):
        basis.append("remedies are a lower bound: not established for " + ", ".join(unchecked))
    return {
        "verdict": verdict,
        "unchecked": unchecked,
        "confidence": confidence,
        "categories": cats,
        "remedies": remedies,
        "assumptions": assumptions,
        "basis": basis,
    }


def invariant_violations(report: JSON) -> list[str]:
    """Properties every scan report's verdict must have (empty when they all hold):

    1. direct-ok is impossible if any breaking or risky finding is in a hazard category
       (pre-existing ones included);
    2. direct-ok is impossible if any category is a hazard or unknown, and a hazard
       category means needs-changes or blocked;
    3. a category that didn't run (or recorded nothing) is unknown, listed in `unchecked`,
       and the verdict isn't direct-ok (a hazard elsewhere still decides needs-changes or
       blocked: §8 precedence);
    4. a pre-existing breaking finding in a deployment category caps confidence at low.
    """
    up = report.get("upgrade_path")
    if not up:
        return []
    out: list[str] = []
    verdict, cats = up["verdict"], up["categories"]
    hazard_cats = {c.value for c in (Category(n) for n in CATEGORIES)}
    sev = [
        f["id"]
        for f in report.get("findings", [])
        if f["category"] in hazard_cats and f["severity"] in ("breaking", "risky")
    ]
    if verdict == "direct-ok" and sev:
        out.append(f"direct-ok with breaking/risky hazard-category findings: {sev}")
    bad = sorted(n for n, c in cats.items() if c["status"] in ("hazard", "unknown"))
    if verdict == "direct-ok" and bad:
        out.append(f"direct-ok with categories {bad} not clean")
    if any(c["status"] == "hazard" for c in cats.values()) and verdict not in (
        "needs-changes",
        "blocked",
    ):
        out.append(f"a hazard category but verdict {verdict}")
    for n, c in cats.items():
        if c["status"] == "unknown" and n not in up.get("unchecked", []):
            out.append(f"category {n} is unknown but not listed as unchecked")
    statuses = report.get("analyzers", [])
    for n, analyzer in CATEGORIES.items():
        runs = [
            s for s in statuses if s["name"] == analyzer or s["name"].startswith(analyzer + "@")
        ]
        if (not runs or any(s["status"] != "ok" for s in runs)) and cats[n]["status"] != "unknown":
            out.append(f"category {n}'s analyzer didn't run cleanly, but it's {cats[n]['status']}")
    deploy = {c.value for c in DEPLOYMENT.values()}
    pre = [
        f["id"]
        for f in report.get("findings", [])
        if f["severity"] == "breaking"
        and not f.get("introduced_by_migration", True)
        and (
            f["category"] in deploy
            or any((n, Category(f["category"]), f["kind"]) in TOUCHES for n in DEPLOYMENT)
        )
    ]
    if pre and up["confidence"] != "low":
        out.append(f"pre-existing breaking {pre} but confidence {up['confidence']}")
    return out
