"""`zmig compare BEFORE AFTER`: two scans of the same app, before and after an edit.

A thin layer over `zmig.watch`: BEFORE becomes an in-memory baseline that accepts every
breaking and risky finding and every gap it has, and AFTER is compared against it. Two
passes are added on top:

- pairing: watch's fingerprint includes the first file a finding cites, so clearing every
  use site (or build error) in that file moves the finding to a new fingerprint. A gone
  item and a new item with the same (category, kind, subject, image) are one finding,
  reported once with its site counts. Site counts also catch what a fingerprint match
  misses: fewer sites behind an unchanged value, more errors in the same files.
- revealed: a new finding that BEFORE couldn't have reported, because the target build or
  the finding's analyzer was a gap in BEFORE and isn't in AFTER. Without an edit list
  compare can't tell hidden from caused, so it makes the run inconclusive (exit 4).

Reads two report.json files and nothing else: no git, no builds, no cache.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zmig.errors import UsageError
from zmig.findings import SCHEMA_URI
from zmig.report.human import effective_contexts, render_compare
from zmig.watch import (
    EXIT_INCONCLUSIVE,
    EXIT_NEW_FINDING,
    EXIT_OK,
    GATED,
    Acceptance,
    Baseline,
    Comparison,
    FingerprintContext,
    Gap,
    Item,
    Outcome,
    assess,
    decide,
    evidence_files,
    evidence_set,
    fingerprint,
    gaps,
    gaps_json,
    item_json,
    make_baseline,
    policy_for,
)

JSON = dict[str, Any]

_SEV_RANK = {"breaking": 0, "risky": 1, "info": 2}

# The in-memory baseline sets no expiry, so the date never matters; a fixed one keeps the
# output independent of the clock.
FIXED_DAY = dt.date(1970, 1, 1)
_ACCEPT = Acceptance("zmig compare BEFORE", "zmig compare", "", None)


def load(before_path: Path, after_path: Path) -> tuple[JSON, JSON]:
    if before_path.resolve() == after_path.resolve():
        raise UsageError(f"BEFORE and AFTER are the same file ({before_path})")
    docs = []
    for p in (before_path, after_path):
        try:
            doc = json.loads(p.read_text())
        except FileNotFoundError:
            raise UsageError(f"{p}: no such file") from None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise UsageError(f"{p}: not a readable JSON report ({e})") from None
        if not isinstance(doc, dict) or doc.get("schema") != SCHEMA_URI:
            raise UsageError(f"{p}: not a zmig report")
        if doc.get("command") != "scan":
            raise UsageError(
                f"{p}: a `zmig {doc.get('command')}` report; compare needs two `zmig scan` reports"
            )
        if str(doc.get("schema_version", "")).split(".")[0] != "1":
            raise UsageError(
                f"{p}: schema_version {doc.get('schema_version')!r}; this zmig reads 1.x"
            )
        docs.append(doc)
    return docs[0], docs[1]


def _analyzer_names(report: JSON) -> list[str]:
    return sorted({str(a["name"]).split("@")[0] for a in report.get("analyzers", [])})


def mismatches(before: JSON, after: JSON) -> list[str]:
    """Why the two reports can't be compared (empty: they can). The scan options that
    report.json doesn't record (--module, --cmake-arg, --to-board, --wire-allowlist)
    can't be checked."""

    def fields(r: JSON) -> JSON:
        i = r["inputs"]
        return {
            "inputs.app": i.get("app"),
            "inputs.from.sha": i["from"]["sha"],
            "inputs.to.sha": i["to"]["sha"],
            "inputs.board": i.get("board"),
            "inputs.build_mode": i.get("build_mode"),
            "inputs.sysbuild": i.get("sysbuild"),
            "analyzers": _analyzer_names(r),
        }

    fb, fa = fields(before), fields(after)
    bb = (before.get("environment") or {}).get("zmig_build")
    ab = (after.get("environment") or {}).get("zmig_build")
    if bb and ab:
        fb["environment.zmig_build"], fa["environment.zmig_build"] = bb, ab
    else:  # a 1.5 report: the version string is all there is
        fb["zmig_version"], fa["zmig_version"] = before["zmig_version"], after["zmig_version"]
    out = [f"{k}: BEFORE {fb[k]!r}, AFTER {fa[k]!r}" for k in sorted(fb) if fb[k] != fa[k]]
    # A pinned AFTER (scan --pin-source) compares the edited app against BEFORE's source
    # build only if that is the build it was pinned to (DESIGN.md, "Pinned-source scans").
    if before["inputs"].get("pinned_source"):
        out.append("BEFORE is a pinned scan: compare against the original scan it was pinned to")
    if pin := after["inputs"].get("pinned_source"):
        source, _, _ = effective_contexts(before.get("contexts", []))
        key = source.get("key") if source else None
        if pin.get("key") != key:
            out.append(
                f"AFTER is pinned to source build {pin.get('key')!r}; "
                f"BEFORE's source build is {key!r}"
            )
    return out


def baseline_of(before: JSON, path: Path) -> Baseline:
    env = before.get("environment") or {}
    data = make_baseline(
        before,
        {},
        before["inputs"]["to"],
        _ACCEPT,
        before["zmig_version"],
        build=env.get("zmig_build") or "unrecorded",
    )
    return Baseline(path, data)


def sites(finding: JSON) -> int:
    """How many places a finding points at: its build errors for target-build-failed (the
    evidence is capped), else its project file_line evidence."""
    new = finding.get("new")
    if finding["kind"] == "target-build-failed" and isinstance(new, dict):
        errors = new.get("errors")
        if isinstance(errors, list):
            return len(errors)
    return sum(1 for e in finding.get("evidence", ()) if e.get("type") == "file_line")


@dataclass
class Result:
    cmp: Comparison
    outcome: Outcome
    before_ids: dict[str, list[str]]
    # id(item) -> (sites before, sites after), for items the site counts decided.
    site_counts: dict[int, tuple[int, int]] = field(default_factory=dict)
    revealed: list[Item] = field(default_factory=list)
    unseen_source_build_broke: int = 0
    # id(item) -> BEFORE's title, for a target failure whose error changed: its site counts
    # compare two different errors, so they aren't shown.
    error_before: dict[int, str] = field(default_factory=dict)
    # id(item) -> BEFORE's severity, for "de-escalated" items.
    severity_before: dict[int, str] = field(default_factory=dict)


def _by_fingerprint(report: JSON) -> dict[str, list[JSON]]:
    ctx = FingerprintContext.of(report)
    out: dict[str, list[JSON]] = defaultdict(list)
    for f in sorted(
        (f for f in report["findings"] if f["severity"] in GATED),
        key=lambda f: _SEV_RANK[f["severity"]],
    ):
        out[fingerprint(f, ctx)].append(f)
    return out


def _key(f: JSON, ctx: FingerprintContext) -> tuple[str, str, str, str]:
    return (f["category"], f["kind"], ctx.normalize(f["subject"]), f.get("image") or "")


def _by_sites(
    before_f: JSON, after_f: JSON, ctx: FingerprintContext
) -> tuple[str | None, list[str]]:
    """The state site counts call for, or None to keep watch's: more sites or a new file is
    a new use site, fewer is an update."""
    b, a = sites(before_f), sites(after_f)
    added = sorted(
        evidence_files(evidence_set(after_f, ctx)) - evidence_files(evidence_set(before_f, ctx))
    )
    if added or a > b:
        return "new-use-site", added
    if a < b:
        return "updated", []
    return None, []


_TARGET_FAILURES = ("target-configure-failed", "target-build-failed")


def _note_error_change(res: Result, item: Item, before_f: JSON) -> None:
    """A target failure keeps its id whatever the error; when the error changed (the edit
    fixed one and the build now stops at another), say so instead of counting sites
    (regression: ardep's board.yml error, fixed, then a Kconfig abort, read "1 -> 0
    sites")."""
    after = item.finding
    if after and after["kind"] in _TARGET_FAILURES and after["title"] != before_f["title"]:
        res.error_before[id(item)] = before_f["title"]


def pair(res: Result, before_fp: dict[str, list[JSON]], ctx: FingerprintContext) -> None:
    """Merge gone+new items that are one finding under a moved fingerprint, and apply site
    counts to the items watch matched by fingerprint."""
    items = res.cmp.items
    # Items watch matched: compare site counts, in the order watch matched them.
    taken: dict[str, int] = defaultdict(int)
    for i in items:
        if i.finding is None or i.state in ("new", "fixed", "unseen"):
            continue
        olds = before_fp.get(i.fingerprint, [])
        k = taken[i.fingerprint]
        taken[i.fingerprint] += 1
        if k >= len(olds):
            continue
        before_f = olds[k]
        res.site_counts[id(i)] = (sites(before_f), sites(i.finding))
        _note_error_change(res, i, before_f)
        if i.state in ("unchanged", "updated") and (
            _SEV_RANK[i.finding["severity"]] > _SEV_RANK[before_f["severity"]]
        ):
            # Less severe than in BEFORE: watch has no state for it, so it read "unchanged"
            # (regression: golioth's NET_EVENT_IPV4_ADDR_ADD, breaking -> risky once the
            # edit stopped narrowing it).
            i.state = "de-escalated"
            res.severity_before[id(i)] = before_f["severity"]
            continue
        if i.state in ("unchanged", "updated"):
            state, added = _by_sites(before_f, i.finding, ctx)
            if state:
                i.state = state
                i.new_sites = added or i.new_sites
    # Gone + new with the same identity: one finding, moved.
    gone = [i for i in items if i.state in ("fixed", "unseen")]
    new = [i for i in items if i.state == "new" and i.finding is not None]
    used: set[int] = set()
    merged: dict[int, Item] = {}
    for g in gone:
        olds = before_fp.get(g.fingerprint, [])
        if not olds:
            continue
        before_f = olds[0]
        n = next(
            (
                n
                for n in new
                if id(n) not in used and n.finding and _key(n.finding, ctx) == _key(before_f, ctx)
            ),
            None,
        )
        if n is None or n.finding is None:
            continue
        used.add(id(n))
        if _SEV_RANK[n.finding["severity"]] < _SEV_RANK[before_f["severity"]]:
            state, added = "escalated", []
        else:
            by, added = _by_sites(before_f, n.finding, ctx)
            state = by or "updated"
        n.state = state
        n.entry = g.entry
        n.cause = None
        n.new_sites = added
        res.site_counts[id(n)] = (sites(before_f), sites(n.finding))
        _note_error_change(res, n, before_f)
        merged[id(g)] = n
    res.cmp.items = [i for i in items if id(i) not in merged]


def to_info(res: Result, after: JSON) -> None:
    """A "fixed" item whose findings are all still in AFTER at info severity was only
    de-escalated: compare looks at breaking and risky findings, so a drop to info read as
    fixed."""
    info = {f["id"]: f for f in after["findings"] if f["severity"] == "info"}
    for i in res.cmp.items:
        ids = res.before_ids.get(i.fingerprint, [])
        if i.state == "fixed" and ids and all(x in info for x in ids):
            i.state = "de-escalated"
            i.finding = info[ids[0]]
            res.severity_before[id(i)] = i.severity
            i.severity = "info"


def _app_image(contexts: list[JSON]) -> str | None:
    for c in contexts:
        for img in c.get("images") or []:
            if img.get("role") == "app":
                return str(img.get("name"))
    return None


def reveal(res: Result, before_gaps: list[Gap], after_gaps: list[Gap], after: JSON) -> None:
    """Relabel "new" items BEFORE couldn't have reported. A gap that only improved in rank
    (skipped -> partial) is still a gap, so it doesn't reveal anything."""
    closed = {g.id for g in before_gaps} - {g.id for g in after_gaps}
    if not closed:
        return
    app = _app_image(after.get("contexts") or [])
    for i in res.cmp.items:
        if i.state != "new" or i.finding is None:
            continue
        f = i.finding
        name = f"analyzer:{f['analyzer']}"
        # Gap labels carry @image in multi-image builds; app findings carry no image.
        keys = {f"{name}@{f['image']}"} if f.get("image") else {name, f"{name}@{app}"}
        if "target-build" in closed or keys & closed:
            i.state = "revealed"
            i.cause = None
            res.revealed.append(i)


def classify(
    before: JSON, after: JSON, before_path: Path, fail_on: str, allow_inconclusive: bool
) -> Result:
    baseline = baseline_of(before, before_path)
    gap_states, cmp = assess(after, baseline, FIXED_DAY)
    before_fp = _by_fingerprint(before)
    res = Result(
        cmp,
        Outcome(EXIT_OK, [], [], [], ""),
        {fp: [f["id"] for f in fs] for fp, fs in before_fp.items()},
    )
    pair(res, before_fp, FingerprintContext.of(after))
    to_info(res, after)
    reveal(res, gaps(before), [g.gap for g in gap_states], after)
    for i in cmp.items:
        i.cause = None  # compare claims no cause (watch says "change": the target didn't move)
    out = decide(cmp, gap_states, policy_for(fail_on, allow_inconclusive))
    out.summary = summary(out, allow_inconclusive)
    if res.revealed and out.code == EXIT_OK:
        what = f"{len(res.revealed)} revealed finding(s): hidden before, or caused by the edit"
        if allow_inconclusive:
            out.summary += f"; WARNING: {what} (allowed by --allow-inconclusive)"
        else:
            out = Outcome(
                EXIT_INCONCLUSIVE,
                [],
                out.warned,
                out.gaps,
                f"inconclusive: {what}; compare can't tell which",
            )
    res.outcome = out
    src_b, _, _ = effective_contexts(before.get("contexts") or [])
    src_a, _, _ = effective_contexts(after.get("contexts") or [])
    if src_b and src_a and src_b.get("status") == "ok" and src_a.get("status") != "ok":
        res.unseen_source_build_broke = sum(1 for i in cmp.items if i.state == "unseen")
    return res


# How compare names watch's gap states: BEFORE is the baseline, so an "accepted" gap is one
# both scans have.
GAP_STATES = {"accepted": "in both scans", "unaccepted": "new in AFTER", "worse": "worse in AFTER"}


def summary(out: Outcome, allow_inconclusive: bool) -> str:
    """The outcome in compare's terms. watch's text speaks of a baseline and accepted gaps;
    compare has two scans (regression: "no new finding outside the baseline; analysis
    incomplete (accepted gaps)" on every ardep dogfood run)."""
    if out.code == EXIT_NEW_FINDING:
        n = Counter(i.severity for i in out.failing)
        what = ", ".join(f"{v} {k}" for k, v in sorted(n.items(), key=lambda kv: _SEV_RANK[kv[0]]))
        return f"new in AFTER: {what}"
    blocking = [g for g in out.gaps if g.blocking]
    if blocking:
        what = f"AFTER couldn't check everything BEFORE did ({len(blocking)} gap(s) new or worse)"
        if allow_inconclusive:
            return f"nothing new; WARNING: {what} (allowed by --allow-inconclusive)"
        return f"inconclusive: nothing new, but {what}"
    if out.gaps:
        return "nothing new; some analyses were incomplete in both scans"
    return "nothing new"


def _shown(path: Path) -> str:
    """The path as given when relative; just its name when absolute (no home or cache
    paths in a file users share)."""
    return path.name if path.is_absolute() else path.as_posix()


def compare_json(
    res: Result, before: JSON, after: JSON, before_path: Path, after_path: Path
) -> JSON:
    def ids(i: Item) -> list[str]:
        # A finding's id doesn't depend on its evidence, so a paired item's AFTER id is
        # its BEFORE id too. Fixed and unseen items have only BEFORE's.
        if i.finding is not None:
            return [i.finding["id"]]
        return res.before_ids.get(i.fingerprint, [])

    def one(i: Item) -> JSON:
        j = {k: v for k, v in item_json(i).items() if k not in ("cause", "expired", "id")}
        j["ids"] = ids(i)
        if id(i) in res.severity_before:
            j["severity_before"] = res.severity_before[id(i)]
        if id(i) in res.error_before:
            j["error_before"] = res.error_before[id(i)]
        elif (sc := res.site_counts.get(id(i))) and i.state in (
            "updated",
            "new-use-site",
            "escalated",
        ):
            j["sites_before"], j["sites_after"] = sc
        return j

    out = res.outcome
    return {
        "exit_code": out.code,
        "summary": out.summary,
        "before": {
            "path": _shown(before_path),
            "zmig_build": (before.get("environment") or {}).get("zmig_build"),
        },
        "after": {
            "path": _shown(after_path),
            "zmig_build": (after.get("environment") or {}).get("zmig_build"),
        },
        "from": after["inputs"]["from"],
        "to": after["inputs"]["to"],
        "pinned": bool(after["inputs"].get("pinned_source")),
        "unseen_source_build_broke": res.unseen_source_build_broke,
        "inconclusive": out.inconclusive,
        "gaps": gaps_json(out),
        "items": [one(i) for i in res.cmp.items],
    }


def run(args: argparse.Namespace) -> int:
    before, after = load(args.before, args.after)
    if diff := mismatches(before, after):
        raise UsageError(
            "the reports can't be compared (re-run the same `zmig scan` command):\n  "
            + "\n  ".join(diff)
        )
    res = classify(before, after, args.before, args.fail_on, args.allow_inconclusive)
    result = compare_json(res, before, after, args.before, args.after)
    text = json.dumps(result, indent=2) + "\n"
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "compare.json").write_text(text)
    sys.stdout.write(text if args.format == "json" else render_compare(result))
    return int(res.outcome.code)
