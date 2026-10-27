"""Fork and local-patch classification.

All git work happens in zmig's cache mirror of the upstream repository, after the
user's HEAD has been fetched into it read-only. Nothing is checked out: trial
application uses `git merge-tree --write-tree` (git >= 2.38), which merges in
memory and writes only into the mirror's object store.

Classification of each local, non-merge commit, first match wins:
  upstreamed  same `git patch-id --stable` as a commit in merge-base..target (high);
              else same normalized subject line, corroborated or contradicted by a
              content-similarity check of the changed lines (signals agree: medium;
              disagree: low); else nearly the same changed lines as an upstream commit
              under a different subject (low). These are still trial-applied, and record
              conflict_files if keeping them would conflict
  applies     merges cleanly onto target plus the previously applied local commits
  conflicts   merge-tree reports conflicted paths
Independently, `overlap` lists files the commit touches that upstream also changed.
The sequential replay mirrors what `git rebase --onto <target>` would do.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from zmig.errors import ToolFailed, ZmigError
from zmig.refs import Mirror

CommitState = Literal["upstreamed", "applies", "conflicts"]

# Fixed identity and dates for trial commits in the mirror, so replay is deterministic.
_TRIAL_ENV = {
    "GIT_AUTHOR_NAME": "zmig",
    "GIT_AUTHOR_EMAIL": "zmig@localhost",
    "GIT_COMMITTER_NAME": "zmig",
    "GIT_COMMITTER_EMAIL": "zmig@localhost",
    "GIT_AUTHOR_DATE": "1970-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "1970-01-01T00:00:00Z",
}

MIN_SUBJECT_WORDS = 3
MAX_OVERLAP_COMMITS = 5


def normalize_subject(s: str) -> str:
    """Strip vendor tags like '[nrf fromlist]' and normalize case and whitespace."""
    s = s.strip()
    while (m := re.match(r"^\[[^\]]*\]\s*", s)) is not None:
        s = s[m.end() :]
    return " ".join(s.lower().split())


@dataclass(frozen=True)
class CommitResult:
    sha: str
    subject: str
    files: tuple[str, ...]
    patch_id: str
    state: CommitState
    match: Literal["patch-id", "subject", "content"] | None = None
    upstream: tuple[str, str] | None = None  # (sha, subject) when upstreamed
    conflict_files: tuple[str, ...] = ()
    overlap: tuple[str, ...] = ()
    overlap_commits: tuple[tuple[str, str], ...] = ()
    unpushed: bool = False
    # Content similarity (0..1) to the upstream commit named in `upstream`, for matches
    # that aren't patch-id identical; and whether subject and content agree.
    similarity: float | None = None
    signals: Literal["agree", "disagree"] | None = None


@dataclass(frozen=True)
class ForkResult:
    head: str
    base: str
    target: str
    merge_base: str
    commits: tuple[CommitResult, ...]
    merges_skipped: int = 0


@dataclass
class _Git:
    mirror: Mirror

    def __call__(
        self,
        *args: str,
        check: bool = True,
        input: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        proc = self.mirror.runner.run(
            [self.mirror.git, "--git-dir", self.mirror.path, *args],
            check=check,
            input=input,
            env=env,
        )
        return proc.returncode, proc.stdout

    def out(self, *args: str, input: str | None = None) -> str:
        return self(*args, input=input)[1]


def _patch_ids(git: _Git, log_args: Iterable[str]) -> dict[str, str]:
    """patch-id -> commit sha for the commits selected by `git log` arguments."""
    patches = git.out("log", "-p", "--no-merges", "--format=commit %H", *log_args)
    if not patches.strip():
        return {}
    ids: dict[str, str] = {}
    for line in git.out("patch-id", "--stable", input=patches).splitlines():
        pid, sha = line.split()
        ids.setdefault(pid, sha)
    return ids


def classify(
    mirror: Mirror, head: str, base: str, target: str, unpushed: frozenset[str] = frozenset()
) -> ForkResult:
    """Classify the commits on `head` that upstream doesn't have, against `target`."""
    git = _Git(mirror)
    rc, mb = git("merge-base", head, base, check=False)
    if rc != 0:
        raise ZmigError(f"{head[:12]} shares no history with upstream {base[:12]}")
    mb = mb.strip()
    local = git.out(
        "rev-list", "--reverse", "--no-merges", head, "--not", "--branches", "--tags"
    ).split()
    merges = len(git.out("rev-list", "--merges", head, "--not", "--branches", "--tags").split())

    upstream_changed = set(git.out("diff", "--name-only", mb, target).splitlines())
    subjects: dict[str, tuple[str, str]] = {}
    for line in git.out("log", "--no-merges", "--format=%H%x00%s", f"{mb}..{target}").splitlines():
        sha, subj = line.split("\0", 1)
        norm = normalize_subject(subj)
        if len(norm.split()) >= MIN_SUBJECT_WORDS:
            subjects.setdefault(norm, (sha, subj))

    results: list[CommitResult] = []
    cur = target
    for c in local:
        subject = git.out("log", "-1", "--format=%s", c).strip()
        files = tuple(
            git.out("diff-tree", "--no-commit-id", "--name-only", "-r", "--root", c).splitlines()
        )
        pid_out = git.out("patch-id", "--stable", input=git.out("show", "--format=commit %H", c))
        pid = pid_out.split()[0] if pid_out.strip() else ""
        overlap = tuple(sorted(set(files) & upstream_changed))
        overlap_commits: tuple[tuple[str, str], ...] = ()
        if overlap:
            lines = git.out(
                "log",
                "--no-merges",
                "--format=%H%x00%s",
                f"-{MAX_OVERLAP_COMMITS}",
                f"{mb}..{target}",
                "--",
                *overlap,
            ).splitlines()
            overlap_commits = tuple((ln.split("\0", 1)[0], ln.split("\0", 1)[1]) for ln in lines)
        res = CommitResult(
            sha=c,
            subject=subject,
            files=files,
            patch_id=pid,
            state="applies",
            overlap=overlap,
            overlap_commits=overlap_commits,
            unpushed=c in unpushed,
        )

        upstream_ids = _patch_ids(git, [f"{mb}..{target}", "--", *files]) if files else {}
        if pid and pid in upstream_ids:
            up = upstream_ids[pid]
            up_subject = git.out("log", "-1", "--format=%s", up).strip()
            results.append(
                replace(res, state="upstreamed", match="patch-id", upstream=(up, up_subject))
            )
            continue
        # Replay like `git rebase`: patch-id matches are dropped above; everything else,
        # including subject-only matches (whose content differs), is applied in order.
        tree, conflicted = _trial_apply(git, cur, c)
        if tree is not None:
            cur = _commit_tree(git, tree, cur, c)
        mine = changed_lines(git, c)
        if (norm := normalize_subject(subject)) in subjects:
            by_subject = subjects[norm]
            sim = similarity(mine, changed_lines(git, by_subject[0]))
            results.append(
                replace(
                    res,
                    state="upstreamed",
                    match="subject",
                    upstream=by_subject,
                    conflict_files=conflicted,
                    similarity=round(sim, 2),
                    signals="agree" if sim >= SUBJECT_CONTENT_AGREE else "disagree",
                )
            )
            continue
        best = _most_similar(git, mine, mb, target, files)
        if best is not None and best[2] >= CONTENT_MATCH:
            sha, subj, sim = best
            results.append(
                replace(
                    res,
                    state="upstreamed",
                    match="content",
                    upstream=(sha, subj),
                    conflict_files=conflicted,
                    similarity=round(sim, 2),
                    signals="disagree",
                )
            )
        elif tree is not None:
            results.append(res)
        else:
            results.append(replace(res, state="conflicts", conflict_files=conflicted))
    return ForkResult(head, base, target, mb, tuple(results), merges)


# A subject match is corroborated when at least half of the changed lines agree; a
# different-subject commit counts as the same change only when nearly all of them do.
SUBJECT_CONTENT_AGREE = 0.5
CONTENT_MATCH = 0.8
MAX_CONTENT_CANDIDATES = 200

ChangedLines = Counter[tuple[str, str, str]]


def changed_lines(git: _Git, sha: str) -> ChangedLines:
    """Multiset of (+/-, file, whitespace-normalized text) of a commit's changed lines.
    Context lines and line numbers are ignored, so the same change applied to slightly
    different surroundings still compares equal (unlike patch-id)."""
    out: ChangedLines = Counter()
    path = ""
    for line in git.out("show", "--format=", "--no-color", "--no-renames", "-U0", sha).splitlines():
        if line.startswith("--- "):
            path = line[6:] if line.startswith("--- a/") else ""
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else path
        elif line.startswith(("diff ", "index ", "@@", "new file", "deleted file", "similarity")):
            continue
        elif line[:1] in ("+", "-") and path:
            out[(line[0], path, " ".join(line[1:].split()))] += 1
    return out


def similarity(a: ChangedLines, b: ChangedLines) -> float:
    """Jaccard index of two multisets of changed lines."""
    union = sum((a | b).values())
    return sum((a & b).values()) / union if union else 0.0


def _most_similar(
    git: _Git, mine: ChangedLines, mb: str, target: str, files: tuple[str, ...]
) -> tuple[str, str, float] | None:
    """The upstream commit in mb..target, touching the same files, whose changed lines are
    most similar to `mine`."""
    if not files or not mine:
        return None
    best: tuple[str, str, float] | None = None
    log = git.out(
        "log",
        "--no-merges",
        "--format=%H%x00%s",
        f"-{MAX_CONTENT_CANDIDATES}",
        f"{mb}..{target}",
        "--",
        *files,
    )
    for line in log.splitlines():
        sha, subj = line.split("\0", 1)
        sim = similarity(mine, changed_lines(git, sha))
        if best is None or sim > best[2]:
            best = (sha, subj, sim)
    return best


def _trial_apply(git: _Git, onto: str, c: str) -> tuple[str | None, tuple[str, ...]]:
    """Cherry-pick `c` onto `onto` in memory: (result tree, ()) or (None, conflicted paths)."""
    rc, out = git(
        "merge-tree",
        "--write-tree",
        "--name-only",
        "--no-messages",
        f"--merge-base={c}^",
        onto,
        c,
        check=False,
    )
    if rc not in (0, 1):
        raise ToolFailed(f"git merge-tree failed on {c[:12]} (exit {rc})")
    lines = out.splitlines()
    if rc == 0:
        return lines[0], ()
    return None, tuple(sorted({ln for ln in lines[1:] if ln}))


def _commit_tree(git: _Git, tree: str, parent: str, original: str) -> str:
    return git(
        "commit-tree", tree, "-p", parent, "-m", f"zmig trial of {original}", env=_TRIAL_ENV
    )[1].strip()


@dataclass(frozen=True)
class TreeDiff:
    modified: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    compared: int = 0

    @property
    def clean(self) -> bool:
        return not (self.modified or self.missing or self.added)


# Directories in an untracked tree that are build or tool output, not source.
TREE_IGNORE = {".git", "__pycache__", "build", ".west", ".venv"}


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def compare_tree(mirror: Mirror, rev: str, root: Path) -> TreeDiff:
    """Compare files on disk under `root` with upstream's tree at `rev`, by git blob hash.

    Hashes are computed without git's clean filters (eol/attribute conversion), so a
    tree checked out with CRLF conversion would show as modified.
    """
    listing = _Git(mirror).out("ls-tree", "-r", "-z", "--full-tree", rev)
    expected: dict[str, tuple[str, str]] = {}
    for entry in listing.split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, kind, sha = meta.split()
        if kind == "blob":
            expected[path] = (mode, sha)
    modified, missing = [], []
    for path, (mode, sha) in sorted(expected.items()):
        p = root / path
        if mode == "120000":
            if not p.is_symlink():
                missing.append(path)
            elif blob_sha(os.readlink(p).encode()) != sha:
                modified.append(path)
            continue
        if not p.is_file():
            missing.append(path)
        elif blob_sha(p.read_bytes()) != sha:
            modified.append(path)
    added = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in TREE_IGNORE)
        for f in filenames:
            rel = (Path(dirpath) / f).relative_to(root).as_posix()
            if rel not in expected:
                added.append(rel)
    return TreeDiff(tuple(modified), tuple(missing), tuple(sorted(added)), len(expected))


@dataclass(frozen=True)
class VersionGuess:
    tag: str | None
    version: str
    reason: str = field(default="")


def guess_version_tag(root: Path) -> VersionGuess:
    """Infer the release tag of an untracked Zephyr tree from its VERSION file."""
    vf = root / "VERSION"
    if not vf.is_file():
        raise ZmigError(f"{root} has no VERSION file; pass the revision explicitly")
    kv = dict(re.findall(r"^(\w+)\s*=\s*(\S*)\s*$", vf.read_text(), re.M))
    try:
        major, minor, patch = kv["VERSION_MAJOR"], kv["VERSION_MINOR"], kv["PATCHLEVEL"]
    except KeyError as e:
        raise ZmigError(f"{vf} is missing {e.args[0]}") from e
    extra = kv.get("EXTRAVERSION", "")
    version = f"{major}.{minor}.{patch}" + (f"-{extra}" if extra else "")
    if patch == "99":
        return VersionGuess(None, version, "PATCHLEVEL 99 marks a development snapshot of main")
    return VersionGuess(f"v{version}", version)
