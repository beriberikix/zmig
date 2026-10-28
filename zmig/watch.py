"""`zmig watch`: compare a scan against a committed baseline (docs/DESIGN-WATCH.md).

Everything here works on report JSON (`Report.to_json()` or a report.json read back), so
the comparison can be re-run on saved reports without building anything.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zmig.errors import ZmigError

JSON = dict[str, Any]

BASELINE_SCHEMA = "https://zmig.dev/schema/baseline/1"
FINGERPRINT_VERSION = 1
SARIF_FINGERPRINT_KEY = "zmigFingerprint/v1"
COMMENT_MARKER = "<!-- zmig-watch -->"

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_NEW_FINDING, EXIT_INCONCLUSIVE = 0, 1, 2, 3, 4

GATED = ("breaking", "risky")
_SEV_RANK = {"breaking": 0, "risky": 1, "info": 2}
_CONF_RANK = {"high": 0, "medium": 1, "low": 2}

_SHA40 = re.compile(r"\b[0-9a-f]{40}\b")
# Hash-named directories under the cache: workspaces/<sha>/apps/<16 hex>/, builds/<16 hex>/.
_HASH_DIR = re.compile(r"(?<=/)[0-9a-f]{16}(?=/)")


# --- Fingerprints (§2) -------------------------------------------------------------------


@dataclass(frozen=True)
class FingerprintContext:
    """What a fingerprint must not depend on: the refs compared, under any spelling."""

    board: str
    refs: tuple[str, ...]  # specs and SHAs of --from and --to

    @classmethod
    def of(cls, report: JSON) -> FingerprintContext:
        inputs = report["inputs"]
        refs: list[str] = []
        for side in ("from", "to"):
            ref = inputs.get(side) or {}
            refs += [str(ref.get("sha") or ""), str(ref.get("spec") or "")]
        board = inputs.get("board") or {}
        return cls(
            board=str(board.get("from", "")) if isinstance(board, dict) else str(board),
            refs=tuple(sorted({r for r in refs if len(r) >= 4}, key=len, reverse=True)),
        )

    def normalize(self, text: str) -> str:
        for ref in self.refs:
            text = re.sub(rf"(?<![\w.-]){re.escape(ref)}(?![\w.-])", "<ref>", text)
            if _SHA40.fullmatch(ref):
                text = re.sub(rf"\b{ref[:12]}[0-9a-f]*\b", "<ref>", text)
        text = _SHA40.sub("<sha>", text)
        return _HASH_DIR.sub("<h>", text)


def primary_location(finding: JSON) -> str:
    """The first file the evidence points at, without its line (lines move with unrelated
    edits); else the first documentation entry; else ""."""
    for e in finding.get("evidence", ()):
        if e.get("type") == "file_line":
            return str(e["path"])
    for e in finding.get("evidence", ()):
        if e.get("type") == "doc_entry":
            return f"doc:{e['entry_id']}"
    return ""


def first_line(finding: JSON) -> int | None:
    for e in finding.get("evidence", ()):
        if e.get("type") == "file_line":
            return int(e["line"])
    return None


def fingerprint(finding: JSON, ctx: FingerprintContext) -> str:
    material = json.dumps(
        [
            FINGERPRINT_VERSION,
            finding["category"],
            finding["kind"],
            ctx.normalize(finding["subject"]),
            finding.get("image") or "",
            ctx.board,
            ctx.normalize(primary_location(finding)),
        ]
    )
    return hashlib.sha256(material.encode()).hexdigest()[:16]


# `file.c:123` in values (compiler sites): the line goes, like in the fingerprint.
_PATH_LINE = re.compile(r"(\.[A-Za-z]{1,4}):\d+\b")


def _without_lines(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _without_lines(v) for k, v in value.items() if k not in ("line", "lines")}
    if isinstance(value, list):
        return [_without_lines(v) for v in value]
    if isinstance(value, str):
        return _PATH_LINE.sub(r"\1", value)
    return value


def value_digest(finding: JSON, ctx: FingerprintContext) -> str:
    """Hash of the finding's `new` value, to tell "same finding, changed value" apart.
    Line numbers are left out (regression: an unrelated 5-line shift in CANnectivity's
    led.c made three accepted findings "updated")."""
    text = ctx.normalize(json.dumps(_without_lines(finding.get("new")), sort_keys=True))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# --- The baseline file (§3) --------------------------------------------------------------


@dataclass
class Baseline:
    path: Path
    data: JSON

    @property
    def accepted(self) -> list[JSON]:
        return list(self.data.get("accepted", []))

    @property
    def to_sha(self) -> str:
        return str(self.data["to"]["sha"])

    @classmethod
    def load(cls, path: Path) -> Baseline:
        data = json.loads(path.read_text())
        if data.get("schema") != BASELINE_SCHEMA:
            raise ZmigError(f"{path} is not a zmig baseline (schema {data.get('schema')!r})")
        if data.get("fingerprint_version") != FINGERPRINT_VERSION:
            raise ZmigError(
                f"{path} uses fingerprint version {data.get('fingerprint_version')}; this zmig "
                f"uses {FINGERPRINT_VERSION}. Re-create it with `zmig watch --init`."
            )
        return cls(path, data)


def baseline_root(path: Path) -> Path:
    """The directory paths in the baseline are relative to: the repository holding
    `.zmig/baseline.json`."""
    path = path.resolve()
    return path.parent.parent if path.parent.name == ".zmig" else path.parent


def portable(text: str, root: Path) -> str:
    """Absolute paths under the baseline's repository, written relative to it, so a
    baseline made on a laptop matches the same command line in CI."""
    return text.replace(str(root.resolve()), "<repo>")


def config_mismatch(baseline: JSON, config: JSON) -> list[str]:
    old = baseline.get("config", {})
    return [
        f"{k}: baseline {old.get(k)!r}, this run {config.get(k)!r}"
        for k in sorted(set(old) | set(config))
        if old.get(k) != config.get(k)
    ]


@dataclass(frozen=True)
class Acceptance:
    """Who accepts findings into the baseline, when, at which commit, and why."""

    reason: str
    by: str
    at: str
    commit: str | None
    until: str | None = None


def _entry(finding: JSON, ctx: FingerprintContext, acc: Acceptance) -> JSON:
    return {
        "fingerprint": fingerprint(finding, ctx),
        "severity": finding["severity"],
        "category": finding["category"],
        "kind": finding["kind"],
        "subject": ctx.normalize(finding["subject"]),
        "title": ctx.normalize(finding["title"]),
        "path": ctx.normalize(primary_location(finding)),
        "value": value_digest(finding, ctx),
        "reason": acc.reason,
        "accepted_by": acc.by,
        "accepted_at": acc.at,
        "accepted_commit": acc.commit,
        **({"until": acc.until} if acc.until else {}),
    }


def make_baseline(
    report: JSON,
    config: JSON,
    to: JSON,
    acc: Acceptance,
    zmig_version: str,
    previous: Baseline | None = None,
) -> JSON:
    """A baseline accepting the report's breaking and risky findings. Entries that were
    already accepted keep their reason, author, date and expiry; only findings not in
    `previous` get `acc`."""
    ctx = FingerprintContext.of(report)
    kept: dict[str, list[JSON]] = defaultdict(list)
    for e in previous.accepted if previous else []:
        kept[e["fingerprint"]].append(e)
    accepted = []
    for f in _gated(report):
        e = _entry(f, ctx, acc)
        old = kept[e["fingerprint"]]
        if old:
            prior = old.pop(0)
            for k in ("reason", "accepted_by", "accepted_at", "accepted_commit", "until"):
                if k in prior:
                    e[k] = prior[k]
                else:
                    e.pop(k, None)
        accepted.append(e)
    path = report.get("upgrade_path") or {}
    return {
        "schema": BASELINE_SCHEMA,
        "zmig_version": zmig_version,
        "fingerprint_version": FINGERPRINT_VERSION,
        "config": config,
        "from": {"spec": report["inputs"]["from"]["spec"], "sha": report["inputs"]["from"]["sha"]},
        "to": to,
        "verdict": {"verdict": path.get("verdict"), "confidence": path.get("confidence")},
        "inconclusive": inconclusive_reasons(report),
        "accepted": sorted(accepted, key=lambda e: (_SEV_RANK[e["severity"]], e["fingerprint"])),
    }


# --- Comparison (§2, §4) -----------------------------------------------------------------


@dataclass
class Item:
    state: str  # new | escalated | updated | unchanged | fixed
    fingerprint: str
    severity: str
    title: str
    path: str
    finding: JSON | None = None  # None for "fixed"
    entry: JSON | None = None  # the baseline entry it matched (or, for "fixed", the entry)
    cause: str | None = None  # new/escalated: change | target-movement | unattributed
    expired: bool = False  # new because its acceptance expired

    @property
    def gating(self) -> bool:
        return self.state in ("new", "escalated")


@dataclass
class Comparison:
    items: list[Item]
    moved: bool
    attributed: bool
    file_counts: dict[str, Counter[str]] = field(default_factory=dict)

    def of(self, *states: str) -> list[Item]:
        return [i for i in self.items if i.state in states]


def _gated(report: JSON) -> list[JSON]:
    return [f for f in report["findings"] if f["severity"] in GATED]


def compare(
    report: JSON,
    baseline: Baseline,
    today: dt.date,
    pinned: JSON | None = None,
) -> Comparison:
    """Match the report's breaking and risky findings against the baseline's accepted
    entries as multisets per fingerprint.

    `pinned` is the same project scanned against the baseline's target, given when this
    run's target moved: a new finding also present there is due to this change, one
    absent there is due to the target's movement.
    """
    ctx = FingerprintContext.of(report)
    current: dict[str, list[JSON]] = defaultdict(list)
    for f in sorted(_gated(report), key=lambda f: _SEV_RANK[f["severity"]]):
        current[fingerprint(f, ctx)].append(f)
    active: dict[str, list[JSON]] = defaultdict(list)
    expired: dict[str, list[JSON]] = defaultdict(list)
    for e in sorted(baseline.accepted, key=lambda e: _SEV_RANK[e["severity"]]):
        until = e.get("until")
        live = until is None or dt.date.fromisoformat(until) >= today
        (active if live else expired)[e["fingerprint"]].append(e)
    pinned_counts: Counter[str] = Counter()
    if pinned is not None:
        pctx = FingerprintContext.of(pinned)
        pinned_counts.update(fingerprint(f, pctx) for f in _gated(pinned))
    moved = report["inputs"]["to"]["sha"] != baseline.to_sha

    items: list[Item] = []
    for fp in sorted(set(current) | set(active) | set(expired)):
        cur, act, exp = current[fp], list(active[fp]), list(expired[fp])
        news: list[tuple[JSON, bool]] = []
        for f in cur:
            if act:
                e = act.pop(0)
                if _SEV_RANK[f["severity"]] < _SEV_RANK[e["severity"]]:
                    news.append((f, False))
                    items.append(_item("escalated", fp, f, ctx, e))
                    continue
                same = e.get("value") == value_digest(f, ctx)
                items.append(_item("unchanged" if same else "updated", fp, f, ctx, e))
            else:
                was = exp.pop(0) if exp else None
                news.append((f, was is not None))
                items.append(_item("new", fp, f, ctx, was, expired=was is not None))
        for e in [*act, *exp]:
            items.append(Item("fixed", fp, e["severity"], e["title"], e.get("path", ""), entry=e))
        # Attribute the new ones: the first k are explained by this change.
        gating = [i for i in items if i.fingerprint == fp and i.gating]
        if not moved:
            causes = ["change"] * len(gating)
        elif pinned is None:
            causes = ["unattributed"] * len(gating)
        else:
            accepted_n = len(active[fp])
            k = max(0, min(len(cur), pinned_counts[fp]) - accepted_n)
            causes = ["change"] * k + ["target-movement"] * (len(gating) - k)
        for i, c in zip(gating, causes, strict=True):
            i.cause = c

    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for i in items:
        if i.state != "fixed" and i.path:
            counts[i.path][i.fingerprint] += 1
    return Comparison(items, moved, pinned is not None, dict(counts))


def _item(
    state: str, fp: str, f: JSON, ctx: FingerprintContext, e: JSON | None, expired: bool = False
) -> Item:
    return Item(
        state,
        fp,
        f["severity"],
        f["title"],
        ctx.normalize(primary_location(f)),
        finding=f,
        entry=e,
        expired=expired,
    )


# --- Exit policy (§4) --------------------------------------------------------------------


def inconclusive_reasons(report: JSON, baseline: JSON | None = None) -> list[str]:
    """Why this run can't establish that there's no new breaking finding."""
    out = []
    ctxs = report.get("contexts") or []
    if len(ctxs) >= 2 and ctxs[-1].get("status") != "ok":
        out.append(
            f"the target build is {ctxs[-1].get('status')}: later build stages and "
            "the analyzers that need them saw nothing"
        )
    for a in report.get("analyzers", []):
        if a["status"] != "ok":
            out.append(
                f"analyzer {a['name']} {a['status']}"
                + (f": {a['reason']}" if a.get("reason") else "")
            )
    out = [FingerprintContext.of(report).normalize(r) for r in out]
    path = report.get("upgrade_path") or {}
    was = ((baseline or {}).get("verdict") or {}).get("confidence")
    now = path.get("confidence")
    if was in _CONF_RANK and now in _CONF_RANK and _CONF_RANK[now] > _CONF_RANK[was]:
        out.append(f"the verdict's confidence dropped from {was} to {now}")
    return out


@dataclass(frozen=True)
class Policy:
    fail_on: tuple[str, ...] = ("breaking",)
    allow_inconclusive: bool = False
    on_target_movement: str = "fail"  # fail | warn


@dataclass
class Outcome:
    code: int
    failing: list[Item]
    warned: list[Item]
    inconclusive: list[str]
    summary: str


def decide(cmp: Comparison, inconclusive: list[str], policy: Policy) -> Outcome:
    gating = [i for i in cmp.items if i.gating and i.severity in policy.fail_on]
    failing = [
        i
        for i in gating
        if not (i.cause == "target-movement" and policy.on_target_movement == "warn")
    ]
    warned = [i for i in gating if i not in failing]
    if failing:
        n = Counter(i.severity for i in failing)
        what = ", ".join(f"{v} {k}" for k, v in sorted(n.items(), key=lambda kv: _SEV_RANK[kv[0]]))
        return Outcome(
            EXIT_NEW_FINDING,
            failing,
            warned,
            inconclusive,
            f"new finding(s) outside the baseline: {what}",
        )
    if inconclusive and not policy.allow_inconclusive:
        return Outcome(
            EXIT_INCONCLUSIVE,
            [],
            warned,
            inconclusive,
            "inconclusive: no new finding, but the run couldn't check everything",
        )
    if inconclusive:
        return Outcome(
            EXIT_OK,
            [],
            warned,
            inconclusive,
            "no new finding; WARNING: inconclusive (allowed by --allow-inconclusive)",
        )
    return Outcome(EXIT_OK, [], warned, inconclusive, "no new finding outside the baseline")


# --- Outputs (§5) ------------------------------------------------------------------------


_CAUSE = {
    "change": "this change",
    "target-movement": "target movement",
    "unattributed": "this change or target movement (unattributed)",
}


def _short(spec: str) -> str:
    return spec[:12] if _SHA40.fullmatch(spec) else spec


def render_comment(
    report: JSON, baseline: Baseline, cmp: Comparison, out: Outcome, board: str
) -> str:
    ctx = FingerprintContext.of(report)
    inputs = report["inputs"]
    path = report.get("upgrade_path") or {}
    was = baseline.data.get("verdict") or {}
    lines = [
        COMMENT_MARKER,
        f"### zmig watch: {_short(inputs['from']['spec'])} → {_short(inputs['to']['spec'])} "
        f"({board})",
        "",
        f"**Exit {out.code}: {out.summary}.**",
        "",
        f"Verdict: {path.get('verdict')} ({path.get('confidence')}); baseline: "
        f"{was.get('verdict')} ({was.get('confidence')}).",
    ]
    to_sha = inputs["to"]["sha"]
    if cmp.moved:
        lines.append(
            f"Target **moved**: baseline `{baseline.to_sha[:12]}`, this run `{to_sha[:12]}`. "
            + (
                "New findings are labelled by cause (a second scan against the baseline's "
                "target separates them)."
                if cmp.attributed
                else "New findings are **unattributed** (no scan against the baseline's target)."
            )
        )
    else:
        lines.append(f"Target pinned to the baseline's `{to_sha[:12]}`.")

    def section(title: str, items: list[Item], show_cause: bool) -> None:
        if not items:
            return
        lines.extend(["", f"#### {title} ({len(items)})"])
        by_file: dict[str, list[Item]] = defaultdict(list)
        for i in items:
            by_file[i.path or "(no file)"].append(i)
        for p in sorted(by_file):
            group = by_file[p]
            here = sum(cmp.file_counts.get(p, Counter()).values())
            lines.append(f"- **`{p}`**: {len(group)} here; {here} breaking/risky in this file")
            for i in sorted(group, key=lambda i: (_SEV_RANK[i.severity], i.title)):
                n = cmp.file_counts.get(p, Counter())[i.fingerprint]
                extra = []
                if show_cause and i.cause:
                    extra.append(f"due to {_CAUSE[i.cause]}")
                if i.state == "escalated" and i.entry:
                    extra.append(f"was {i.entry['severity']}")
                if i.expired and i.entry:
                    extra.append(f"acceptance expired {i.entry.get('until')}")
                if n > 1:
                    extra.append(f"{n} in this file")
                tail = f" ({'; '.join(extra)})" if extra else ""
                line = first_line(i.finding) if i.finding else None
                at = f" line {line}" if line else ""
                lines.append(
                    f"  - {i.severity}{at}: {ctx.normalize(i.title)}{tail} `{i.fingerprint}`"
                )

    section("New", cmp.of("new", "escalated"), True)
    section("Fixed (accepted, no longer found)", cmp.of("fixed"), False)
    section("Accepted, value changed", cmp.of("updated"), False)
    unchanged = Counter(i.severity for i in cmp.of("unchanged", "updated"))
    lines.extend(
        [
            "",
            f"Accepted and still present: {unchanged['breaking']} breaking, "
            f"{unchanged['risky']} risky.",
        ]
    )
    if out.inconclusive:
        lines.extend(["", "#### Not fully checked"])
        known = set(baseline.data.get("inconclusive", []))
        for r in out.inconclusive:
            lines.append(f"- {r}" + (" (also in the baseline)" if r in known else ""))
    return "\n".join(lines) + "\n"


def render_watch_sarif(report: JSON, cmp: Comparison, zmig_version: str, base: str) -> str:
    """SARIF 2.1.0 with the ref-independent fingerprint and a baselineState per result.
    Fixed entries come back as `absent` results at their file, without a line."""
    rules: dict[str, JSON] = {}
    results = []
    level = {"breaking": "error", "risky": "warning", "info": "note"}
    state = {
        "new": "new",
        "escalated": "new",
        "updated": "updated",
        "unchanged": "unchanged",
        "fixed": "absent",
    }
    for i in cmp.items:
        src = i.finding or i.entry or {}
        rule = f"{src['category']}/{src['kind']}"
        rules.setdefault(
            rule,
            {
                "id": rule,
                "name": src["kind"],
                "shortDescription": {"text": f"{src['category']}: {src['kind']}"},
            },
        )
        locs = []
        if i.path and not i.path.startswith(("<", "/", "(", "doc:", "$", "~")):
            loc: JSON = {"artifactLocation": {"uri": i.path, "uriBaseId": "APPROOT"}}
            line = first_line(i.finding) if i.finding else None
            if line:
                loc["region"] = {"startLine": line}
            locs.append({"physicalLocation": loc})
        results.append(
            {
                "ruleId": rule,
                "level": level[i.severity],
                "message": {"text": i.title + (f". {i.finding['action']}" if i.finding else "")},
                "locations": locs,
                "partialFingerprints": {SARIF_FINGERPRINT_KEY: i.fingerprint},
                "baselineState": state[i.state],
                "properties": {"zmigState": i.state, **({"zmigCause": i.cause} if i.cause else {})},
            }
        )
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "zmig",
                        "version": zmig_version,
                        "rules": sorted(rules.values(), key=lambda r: r["id"]),
                    }
                },
                "originalUriBaseIds": {"APPROOT": {"description": {"text": base}}},
                "results": results,
            }
        ],
    }
    return json.dumps(doc, indent=2) + "\n"


def watch_json(report: JSON, baseline: Baseline, cmp: Comparison, out: Outcome) -> JSON:
    return {
        "exit_code": out.code,
        "summary": out.summary,
        "from": report["inputs"]["from"],
        "to": report["inputs"]["to"],
        "baseline_to_sha": baseline.to_sha,
        "target_moved": cmp.moved,
        "attributed": cmp.attributed,
        "inconclusive": out.inconclusive,
        "items": [
            {
                "state": i.state,
                "fingerprint": i.fingerprint,
                "severity": i.severity,
                "title": i.title,
                "path": i.path,
                **({"line": first_line(i.finding)} if i.finding and first_line(i.finding) else {}),
                **({"id": i.finding["id"]} if i.finding else {}),
                **({"cause": i.cause} if i.cause else {}),
                **({"expired": True} if i.expired else {}),
            }
            for i in cmp.items
        ],
    }
