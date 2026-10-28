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


def zmig_build() -> str:
    """Content hash of the installed zmig package (code and data). The version string
    doesn't change between commits; this does, so a baseline can tell which zmig made
    it (version skew, §3.7)."""
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.suffix in (".py", ".json", ".yaml", ".yml"):
            h.update(f.relative_to(root).as_posix().encode() + b"\0" + f.read_bytes())
    return h.hexdigest()[:16]


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


# What --update-baseline keeps from an entry that is still found: who accepted it, when,
# why, until when, and where it came from in a rebase.
KEPT_ON_UPDATE = (
    "reason",
    "accepted_by",
    "accepted_at",
    "accepted_commit",
    "until",
    "rebased_from",
)


def make_baseline(
    report: JSON,
    config: JSON,
    to: JSON,
    acc: Acceptance,
    zmig_version: str,
    previous: Baseline | None = None,
    build: str | None = None,
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
            for k in KEPT_ON_UPDATE:
                if k in prior:
                    e[k] = prior[k]
                else:
                    e.pop(k, None)
        accepted.append(e)
    path = report.get("upgrade_path") or {}
    prior_gaps: dict[str, JSON] = {
        e["id"]: e for e in (previous.data.get("accepted_gaps", []) if previous else [])
    }
    accepted_gaps: list[JSON] = []
    for g in gaps(report, previous.data if previous else None):
        was = prior_gaps.get(g.id)
        if was is not None and g.rank <= int(was.get("rank", 0)):
            # Still within what was accepted: keep who/when/why (and the accepted level).
            accepted_gaps.append(was)
        else:
            accepted_gaps.append(_gap_entry(g, acc))
    return {
        "schema": BASELINE_SCHEMA,
        "zmig_version": zmig_version,
        "zmig_build": build or zmig_build(),
        "fingerprint_version": FINGERPRINT_VERSION,
        "config": config,
        "from": {"spec": report["inputs"]["from"]["spec"], "sha": report["inputs"]["from"]["sha"]},
        "to": to,
        "verdict": {"verdict": path.get("verdict"), "confidence": path.get("confidence")},
        "accepted_gaps": sorted(accepted_gaps, key=lambda e: e["id"]),
        "accepted": sorted(accepted, key=lambda e: (_SEV_RANK[e["severity"]], e["fingerprint"])),
        "rebases": previous.data.get("rebases", []) if previous else [],
    }


@dataclass
class Rebase:
    data: JSON
    remapped: list[tuple[JSON, JSON]]  # (old entry, new entry)
    unmapped: list[tuple[JSON, str]]  # (old entry, why)
    not_accepted: list[JSON]  # current findings with no old entry: not accepted by a rebase


def rebase_baseline(
    previous: Baseline, report: JSON, acc: Acceptance, zmig_version: str, build: str
) -> Rebase:
    """Re-key a baseline made by another zmig build onto this build's findings.

    Old entries map to this run's breaking/risky findings by (category, kind, normalized
    subject), as multisets; fingerprints, paths and titles come from this build, while
    who/when/why/until are kept. An entry whose finding is gone, or whose severity rose,
    is not carried over. Findings with no old entry stay unaccepted: a rebase never
    accepts anything new.
    """
    ctx = FingerprintContext.of(report)
    pool: dict[tuple[str, str, str], list[JSON]] = defaultdict(list)
    for f in sorted(_gated(report), key=lambda f: _SEV_RANK[f["severity"]]):
        pool[(f["category"], f["kind"], ctx.normalize(f["subject"]))].append(f)
    remapped, unmapped, accepted = [], [], []
    keep = ("reason", "accepted_by", "accepted_at", "accepted_commit", "until")
    for e in previous.accepted:
        cands = pool[(e["category"], e["kind"], e["subject"])]
        ok = [f for f in cands if _SEV_RANK[f["severity"]] >= _SEV_RANK[e["severity"]]]
        if not ok:
            why = (
                f"severity rose ({e['severity']} -> {cands[0]['severity']})"
                if cands
                else "no finding with this category/kind/subject in this build"
            )
            unmapped.append((e, why))
            continue
        f = ok[0]
        cands.remove(f)
        new = _entry(f, ctx, Acceptance("", "", "", None))
        for k in keep:
            new.pop(k, None)
            if k in e:
                new[k] = e[k]
        new["rebased_from"] = e["fingerprint"]
        remapped.append((e, new))
        accepted.append(new)
    not_accepted = [f for fs in pool.values() for f in fs]
    data = dict(previous.data)
    data.update(
        {
            "zmig_version": zmig_version,
            "zmig_build": build,
            "accepted": sorted(
                accepted, key=lambda e: (_SEV_RANK[e["severity"]], e["fingerprint"])
            ),
            "rebases": [
                *previous.data.get("rebases", []),
                {
                    "from_version": previous.data.get("zmig_version"),
                    "from_build": previous.data.get("zmig_build"),
                    "to_version": zmig_version,
                    "to_build": build,
                    "remapped": len(remapped),
                    "dropped": len(unmapped),
                    "reason": acc.reason,
                    "by": acc.by,
                    "at": acc.at,
                    "commit": acc.commit,
                },
            ],
        }
    )
    return Rebase(data, remapped, unmapped, not_accepted)


# --- Comparison (§2, §4) -----------------------------------------------------------------


@dataclass
class Item:
    state: str  # new | escalated | updated | unchanged | fixed | unseen
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
    blind: bool = False,
) -> Comparison:
    """Match the report's breaking and risky findings against the baseline's accepted
    entries as multisets per fingerprint.

    `blind`: the run has a gap the baseline didn't accept (e.g. the target configure now
    fails), so an accepted finding that wasn't found may simply not have been looked for.
    It's "unseen", not "fixed" (regression: the first CI nightly of the CANnectivity test
    repo reported 7 accepted findings as fixed when the v4.4.2 configure failed).

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
            gone = "unseen" if blind else "fixed"
            items.append(Item(gone, fp, e["severity"], e["title"], e.get("path", ""), entry=e))
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
        if i.state not in ("fixed", "unseen") and i.path:
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


@dataclass(frozen=True)
class Gap:
    """Something the run couldn't analyze. `id` is stable across runs; `rank` orders the
    levels of one id, so a gap can get worse (e.g. analyzer partial -> skipped)."""

    id: str
    level: str
    rank: int
    detail: str


_BUILD_RANK = {"build-failed": 1, "configure-failed": 2}
_ANALYZER_RANK = {"partial": 1, "skipped": 2, "error": 3}


def gaps(report: JSON, baseline: JSON | None = None) -> list[Gap]:
    """Why this run can't establish that there's no new breaking finding."""
    ctx = FingerprintContext.of(report)
    out = []
    ctxs = report.get("contexts") or []
    if len(ctxs) >= 2 and ctxs[-1].get("status") != "ok":
        st = str(ctxs[-1].get("status"))
        out.append(
            Gap(
                "target-build",
                st,
                _BUILD_RANK.get(st, 3),
                f"the target build is {st}: later build stages and the analyzers that need "
                "them saw nothing",
            )
        )
    for a in report.get("analyzers", []):
        if a["status"] != "ok":
            out.append(
                Gap(
                    f"analyzer:{a['name']}",
                    a["status"],
                    _ANALYZER_RANK.get(a["status"], 3),
                    ctx.normalize(
                        f"analyzer {a['name']} {a['status']}"
                        + (f": {a['reason']}" if a.get("reason") else "")
                    ),
                )
            )
    path = report.get("upgrade_path") or {}
    was = ((baseline or {}).get("verdict") or {}).get("confidence")
    now = path.get("confidence")
    if was in _CONF_RANK and now in _CONF_RANK and _CONF_RANK[now] > _CONF_RANK[was]:
        out.append(
            Gap(
                "verdict-confidence",
                str(now),
                _CONF_RANK[now],
                f"the verdict's confidence dropped from {was} to {now}",
            )
        )
    return out


def inconclusive_reasons(report: JSON, baseline: JSON | None = None) -> list[str]:
    return [g.detail for g in gaps(report, baseline)]


@dataclass
class GapState:
    gap: Gap
    state: str  # accepted | unaccepted | worse | expired
    entry: JSON | None = None

    @property
    def blocking(self) -> bool:
        return self.state != "accepted"


def classify_gaps(found: list[Gap], baseline: Baseline, today: dt.date) -> list[GapState]:
    """Same policy as accepted findings: an accepted gap passes until its `until`; a gap
    not accepted, or worse than the level accepted, doesn't."""
    accepted = {e["id"]: e for e in baseline.data.get("accepted_gaps", [])}
    out = []
    for g in found:
        e = accepted.get(g.id)
        if e is None:
            out.append(GapState(g, "unaccepted"))
        elif g.rank > int(e.get("rank", 0)):
            out.append(GapState(g, "worse", e))
        elif e.get("until") and dt.date.fromisoformat(e["until"]) < today:
            out.append(GapState(g, "expired", e))
        else:
            out.append(GapState(g, "accepted", e))
    return out


def _gap_entry(g: Gap, acc: Acceptance) -> JSON:
    return {
        "id": g.id,
        "level": g.level,
        "rank": g.rank,
        "detail": g.detail,
        "reason": acc.reason,
        "accepted_by": acc.by,
        "accepted_at": acc.at,
        "accepted_commit": acc.commit,
        **({"until": acc.until} if acc.until else {}),
    }


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
    gaps: list[GapState]
    summary: str

    @property
    def blocking_gaps(self) -> list[GapState]:
        return [g for g in self.gaps if g.blocking]

    @property
    def inconclusive(self) -> list[str]:
        """The blocking gaps' details (what makes the run inconclusive)."""
        return [g.gap.detail for g in self.blocking_gaps]


def decide(cmp: Comparison, gap_states: list[GapState], policy: Policy) -> Outcome:
    gating = [i for i in cmp.items if i.gating and i.severity in policy.fail_on]
    failing = [
        i
        for i in gating
        if not (i.cause == "target-movement" and policy.on_target_movement == "warn")
    ]
    warned = [i for i in gating if i not in failing]
    blocking = [g for g in gap_states if g.blocking]
    if failing:
        n = Counter(i.severity for i in failing)
        what = ", ".join(f"{v} {k}" for k, v in sorted(n.items(), key=lambda kv: _SEV_RANK[kv[0]]))
        return Outcome(
            EXIT_NEW_FINDING,
            failing,
            warned,
            gap_states,
            f"new finding(s) outside the baseline: {what}",
        )
    if blocking and not policy.allow_inconclusive:
        states = Counter(g.state for g in blocking)
        what = ", ".join(f"{v} {k}" for k, v in sorted(states.items()))
        return Outcome(
            EXIT_INCONCLUSIVE,
            [],
            warned,
            gap_states,
            f"inconclusive: no new finding, but the run couldn't check everything ({what} gap(s))",
        )
    if blocking:
        return Outcome(
            EXIT_OK,
            [],
            warned,
            gap_states,
            "no new finding; WARNING: inconclusive (allowed by --allow-inconclusive)",
        )
    if gap_states:
        return Outcome(
            EXIT_OK,
            [],
            warned,
            gap_states,
            "no new finding outside the baseline; analysis incomplete (accepted gaps)",
        )
    return Outcome(EXIT_OK, [], warned, gap_states, "no new finding outside the baseline")


# --- Outputs (§5) ------------------------------------------------------------------------


_CAUSE = {
    "change": "this change",
    "target-movement": "target movement",
    "unattributed": "this change or target movement (unattributed)",
}


def _short(spec: str) -> str:
    return spec[:12] if _SHA40.fullmatch(spec) else spec


def _age_days(stamp: str | None, today: dt.date) -> str:
    if not stamp:
        return "unknown"
    days = (today - dt.date.fromisoformat(stamp[:10])).days
    return f"{days} day" + ("" if days == 1 else "s")


def render_comment(
    report: JSON,
    baseline: Baseline,
    cmp: Comparison,
    out: Outcome,
    board: str,
    today: dt.date,
    warnings: tuple[str, ...] = (),
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
    for w in warnings:
        lines.append(f"\n> **Warning:** {w}")

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
    if out.warned:
        lines.append(
            f"\n{len(out.warned)} new finding(s) due to target movement don't fail this run "
            "(--on-target-movement warn)."
        )
    section("Fixed (accepted, no longer found)", cmp.of("fixed"), False)
    section(
        "Accepted, not seen in this run (analysis incomplete: not counted as fixed)",
        cmp.of("unseen"),
        False,
    )
    section("Accepted, value changed", cmp.of("updated"), False)
    unchanged = Counter(i.severity for i in cmp.of("unchanged", "updated"))
    lines.extend(
        [
            "",
            f"Accepted and still present: {unchanged['breaking']} breaking, "
            f"{unchanged['risky']} risky.",
        ]
    )
    if out.gaps:
        lines.extend(["", "#### Analysis incomplete"])
        for g in out.gaps:
            e = g.entry or {}
            if g.state == "accepted":
                age = _age_days(e.get("accepted_at"), today)
                lines.append(
                    "- analysis incomplete: new breaking findings cannot be seen while this gap "
                    f"exists: {g.gap.detail} (accepted gap, age {age}; by "
                    f"{e.get('accepted_by')}: {e.get('reason')}"
                    + (f"; until {e['until']}" if e.get("until") else "")
                    + ")"
                )
            elif g.state == "worse":
                lines.append(
                    f"- **worse than accepted** ({e.get('level')} accepted, now "
                    f"{g.gap.level}): {g.gap.detail}"
                )
            elif g.state == "expired":
                lines.append(f"- **acceptance expired {e.get('until')}**: {g.gap.detail}")
            else:
                lines.append(f"- **not accepted**: {g.gap.detail}")
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
        # Not found because the run couldn't look: keep the alert open.
        "unseen": "unchanged",
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
        "gaps": [
            {"id": g.gap.id, "level": g.gap.level, "state": g.state, "detail": g.gap.detail}
            for g in out.gaps
        ],
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
