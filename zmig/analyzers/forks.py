"""Local patches and forks in the user's workspace, classified against the target ref."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import ClassVar, Literal

from zmig.analyzers import AnalyzerResult
from zmig.errors import ZmigError
from zmig.findings import (
    JSON,
    Category,
    Confidence,
    Finding,
    Severity,
    git_commit,
    git_conflict,
    plural,
)
from zmig.forks import CommitResult, ForkResult, TreeDiff, classify, compare_tree
from zmig.manifest import UserProject
from zmig.plan import PlanContext, same_repo
from zmig.refs import UPSTREAM_ZEPHYR

log = logging.getLogger("zmig")

MAX_LISTED_FILES = 20


def is_zephyr_tree(path: Path) -> bool:
    return all((path / p).exists() for p in ("VERSION", "west.yml", "scripts/west_commands"))


def _files_note(files: tuple[str, ...]) -> str:
    shown = ", ".join(files[:MAX_LISTED_FILES])
    more = len(files) - MAX_LISTED_FILES
    return shown + (f" (+{more} more)" if more > 0 else "")


def _finding(
    scope: tuple[str, ...],
    kind: str,
    subject: str,
    title: str,
    sev: Severity,
    conf: Confidence,
    action: str,
    evidence: tuple[JSON, ...] = (),
    new: object = None,
    heuristics: tuple[str, ...] = (),
) -> Finding:
    return Finding(
        category=Category.FORK,
        kind=kind,
        subject=subject,
        title=title,
        severity=sev,
        confidence=conf,
        action=action,
        analyzer="forks",
        scope=scope,
        evidence=evidence,
        new=new,
        heuristics=heuristics,
    )


class ForksAnalyzer:
    name: ClassVar[str] = "forks"
    kind: ClassVar[Literal["workspace"]] = "workspace"

    def analyze(self, ctx: PlanContext) -> AnalyzerResult:
        if ctx.user is None:
            return AnalyzerResult.skipped("no west workspace given (--workspace)")
        findings: list[Finding] = []
        for name in sorted(ctx.user.projects):
            up = ctx.user.projects[name]
            if up.presence == "missing":
                continue
            target = self._upstream(ctx, up)
            if target is None:
                continue
            label, url, base, tgt = target
            findings += self._project(ctx, up, label, url, base, tgt)
        return AnalyzerResult(findings)

    def _upstream(
        self, ctx: PlanContext, up: UserProject
    ) -> tuple[str, str, str, str | None] | None:
        """(label, upstream url, base revision, target revision or None if removed)."""
        if up.is_manifest_repo or up.name == "zephyr":
            if not is_zephyr_tree(up.path):
                return None  # an application manifest repo: nothing upstream to compare
            url = ctx.upstream_overrides.get(up.name, UPSTREAM_ZEPHYR)
            return ("zephyr", url, ctx.old.resolved.ref.sha, ctx.new.resolved.ref.sha)
        old = ctx.old_manifest.get(up.name)
        if old is None:
            return None  # the user's own project, not part of upstream Zephyr
        url = ctx.upstream_overrides.get(up.name, old.url)
        new = ctx.new_manifest.get(up.name)
        return (up.name, url, old.revision, new.revision if new else None)

    def _project(
        self, ctx: PlanContext, up: UserProject, label: str, url: str, base: str, target: str | None
    ) -> list[Finding]:
        rel = ctx.rel(up.path)
        out: list[Finding] = []

        scope = ctx.scope

        mirror = ctx.mirror(url)
        mirror.ensure()

        if up.presence == "tree":
            base_sha = mirror.resolve(base)[0]
            diff: TreeDiff = compare_tree(mirror, base_sha, up.path)
            if not diff.clean:
                changed = diff.modified + diff.missing + diff.added
                out.append(
                    _finding(
                        scope,
                        "content-mismatch",
                        label,
                        f"{label} at {rel} is not a git checkout and differs from upstream "
                        f"{base[:12]} in {plural(len(changed), 'file')}",
                        Severity.RISKY,
                        Confidence.HIGH,
                        f"Turn it into a recorded fork: commit these changes on top of {base[:12]} "
                        f"in a git checkout, then classify them with `zmig plan`. "
                        f"Changed: {_files_note(changed)}",
                        new={
                            "modified": list(diff.modified),
                            "missing": list(diff.missing),
                            "added": list(diff.added),
                            "compared_files": diff.compared,
                        },
                        heuristics=("fork.blob-hash-no-filters",),
                    )
                )
            return out

        if up.dirty:
            files = tuple(line[3:] for line in up.dirty)
            out.append(
                _finding(
                    scope,
                    "uncommitted-changes",
                    label,
                    f"{label} ({rel}/) has {plural(len(files), 'uncommitted change')}",
                    Severity.RISKY,
                    Confidence.HIGH,
                    f"Commit or stash them; uncommitted work is not carried across an upgrade "
                    f"and zmig can only classify commits. Files: {_files_note(files)}",
                    new={"files": list(files)},
                )
            )

        base_sha = mirror.resolve(base)[0]
        if up.head == base_sha:
            return out
        head = mirror.fetch_path(up.path)
        tgt_sha = mirror.resolve(target)[0] if target else base_sha
        try:
            res: ForkResult = classify(
                mirror, head, base_sha, tgt_sha, up.unpushed, ctx.patch_methods
            )
        except ZmigError as e:
            out.append(
                _finding(
                    scope,
                    "unrelated-history",
                    label,
                    f"{label}: {e}",
                    Severity.RISKY,
                    Confidence.HIGH,
                    f"Check that {rel} is a fork of {url}.",
                )
            )
            return out
        if not res.commits:
            return out

        declared = up.declared
        if declared is not None and same_repo(declared.url, url):
            out.append(
                _finding(
                    scope,
                    "unrecorded-fork",
                    label,
                    f"{label} has {plural(len(res.commits), 'local commit')} but the "
                    f"manifest records it as upstream {url}",
                    Severity.RISKY,
                    Confidence.HIGH,
                    f"`west update` checks out the manifest revision and leaves these commits "
                    f"behind. Push them to a fork and point the manifest entry for {label} at it.",
                    evidence=tuple(git_commit(label, c.sha, c.subject) for c in res.commits),
                    new={"commits": [c.sha for c in res.commits]},
                )
            )

        counts = {
            s: sum(1 for c in res.commits if c.state == s)
            for s in ("upstreamed", "applies", "conflicts")
        }
        out.append(
            _finding(
                scope,
                "fork-summary",
                label,
                f"{label}: {plural(len(res.commits), 'local commit')} on top of upstream "
                f"({counts['upstreamed']} upstreamed, {counts['applies']} apply cleanly, "
                f"{counts['conflicts']} conflict)",
                Severity.INFO,
                Confidence.HIGH,
                "Work through the per-commit findings below.",
                evidence=(git_commit(label, res.merge_base, "merge-base with upstream"),),
                new={
                    "head": res.head,
                    "merge_base": res.merge_base,
                    "target": res.target,
                    "counts": counts,
                    "merges_skipped": res.merges_skipped,
                },
            )
        )
        if not target:
            return out
        for c in res.commits:
            out.append(self._commit_finding(c, label, scope, ctx.patch_file(c.subject)))
        return out

    @staticmethod
    def _commit_finding(
        c: CommitResult, label: str, scope: tuple[str, ...], patch_file: str | None = None
    ) -> Finding:
        ev: list[JSON] = [git_commit(label, c.sha, c.subject, c.patch_id or None)]
        new: dict[str, object] = {
            "state": c.state,
            "files": list(c.files),
            "provenance": "local-unpushed" if c.unpushed else "remote",
            "methods": {
                m.method: {"state": m.state, "files": list(m.files), "hunks": list(m.hunks)}
                for m in c.methods
            },
            **({"patch_file": patch_file} if patch_file else {}),
        }
        # Which method says what, when they disagree ("applies under 3way, fails under
        # git-apply"); the state is the stricter one.
        by = {m.method: m.state for m in c.methods}
        fails = [m for m, st in by.items() if st == "conflicts"]
        applies = [m for m, st in by.items() if st == "applies"]
        split = (
            f" (fails under {', '.join(fails)}; applies under {', '.join(applies)})"
            if fails and applies
            else ""
        )
        what = f"the project's patch {patch_file}" if patch_file else "the local commit"
        where = (
            f"; failing hunk(s): {', '.join(c.failed_hunks)}"
            if c.failed_hunks
            else (
                f"; conflicting files: {_files_note(c.conflict_files)}" if c.conflict_files else ""
            )
        )
        if c.overlap:
            new["overlap"] = list(c.overlap)
            new["overlap_commits"] = [list(x) for x in c.overlap_commits]
        subject = f"{label}:{c.sha}"
        short = f'{c.sha[:12]} "{c.subject}"'
        if c.state == "upstreamed" and c.match == "absorbed":
            new["match"] = "absorbed"
            return _finding(
                scope,
                "upstreamed",
                subject,
                f"{label} {short} is already in the target, but it no longer applies{split}",
                Severity.RISKY,
                Confidence.MEDIUM,
                f"Drop {what}: upstream already absorbed the change (its added lines are "
                f"present and its removed lines are gone at the target{where}).",
                tuple(ev),
                new,
                ("fork.absorbed",),
            )
        if c.state == "upstreamed" and c.upstream is not None:
            up_sha, up_subj = c.upstream
            ev.append(git_commit("upstream", up_sha, up_subj))
            new["upstream"] = up_sha
            new["match"] = c.match
            if c.match == "patch-id":
                return _finding(
                    scope,
                    "upstreamed",
                    subject,
                    f"{label} {short} is already upstream",
                    Severity.INFO,
                    Confidence.HIGH,
                    f"Drop it when rebasing: upstream {up_sha[:12]} has the identical change.",
                    tuple(ev),
                    new,
                )
            kept = (
                f" Kept as is, it conflicts in {_files_note(c.conflict_files)}."
                if c.conflict_files
                else " Kept as is, it applies cleanly."
            )
            new["match"], new["similarity"], new["signals"] = c.match, c.similarity, c.signals
            sim = f"{c.similarity:.2f}" if c.similarity is not None else "?"
            if c.match == "subject" and c.signals == "agree":
                title = (
                    f"{label} {short} matches upstream {up_sha[:12]} by subject, and the "
                    f"changed lines largely agree (similarity {sim})"
                )
                conf = Confidence.MEDIUM
                heur: tuple[str, ...] = ("fork.subject-match", "fork.content-similarity")
            elif c.match == "subject":
                title = (
                    f"{label} {short} has the subject of upstream {up_sha[:12]}, but its "
                    f"changed lines differ (similarity {sim})"
                )
                conf = Confidence.LOW
                heur = ("fork.subject-match", "fork.content-similarity", "fork.signals-disagree")
            else:
                title = (
                    f"{label} {short} makes nearly the same change as upstream "
                    f'{up_sha[:12]} "{up_subj}" under a different subject '
                    f"(similarity {sim})"
                )
                conf = Confidence.LOW
                heur = ("fork.content-similarity", "fork.signals-disagree")
            return _finding(
                scope,
                "upstreamed",
                subject,
                title,
                Severity.RISKY,
                conf,
                f'Compare with upstream {up_sha[:12]} "{up_subj}" and drop the local commit '
                f"only if nothing local is lost." + kept,
                tuple(ev),
                new,
                heur,
            )
        if c.state == "conflicts":
            ev.append(git_conflict(label, c.sha, list(c.conflict_files)))
            new["conflict_files"] = list(c.conflict_files)
            return _finding(
                scope,
                "conflicts",
                subject,
                f"{label} {short} conflicts with the target{split}",
                Severity.BREAKING,
                Confidence.HIGH,
                f"Refresh {what} against the target{where}.",
                tuple(ev),
                new,
            )
        if c.overlap_near:
            new["overlap_near"] = list(c.overlap_near)
            return _finding(
                scope,
                "applies-cleanly",
                subject,
                f"{label} {short} applies cleanly, but upstream changed lines next to it",
                Severity.RISKY,
                Confidence.HIGH,
                f"Applies without conflicts; review it against the upstream changes next to "
                f"its lines in {_files_note(c.overlap_near)}.",
                tuple(ev),
                new,
            )
        if c.overlap:
            # Upstream changed the same files, but nowhere near the commit's lines.
            return _finding(
                scope,
                "applies-cleanly",
                subject,
                f"{label} {short} applies cleanly; upstream changed other parts of "
                f"{_files_note(c.overlap)}",
                Severity.INFO,
                Confidence.HIGH,
                "Nothing to do unless the commit depends on code elsewhere in those files.",
                tuple(ev),
                new,
            )
        return _finding(
            scope,
            "applies-cleanly",
            subject,
            f"{label} {short} applies cleanly",
            Severity.INFO,
            Confidence.HIGH,
            "Carry it forward; it applies cleanly and upstream didn't touch its files.",
            tuple(ev),
            new,
        )
