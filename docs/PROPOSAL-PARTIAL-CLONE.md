# Proposal: a partial-clone Zephyr mirror (`--filter=blob:none`)

Status: **proposal only. Nothing has been run or changed.** Written 2026-10-29 from the
code, on hotel wifi (no network tests).

## Why

The first run makes a full bare clone of Zephyr, about 1 GB (`zmig/refs.py:95`,
`git clone --bare <url>`). A run on slow wifi was stopped after 224 MB in 44 minutes
(EVALUATION.md, known limitations). A blobless clone downloads commits and trees, and
fetches file contents (blobs) only when something reads them.

**The proposal:** `git clone --bare --filter=blob:none`.

**Not a treeless clone (`--filter=tree:0`).** Several analyzers run path-limited history
(`git log -- <paths>`, `diff --name-only`, `cat-file -e <rev>:<path>`). Those need trees,
and a treeless clone would fetch trees commit by commit.

## What reads what

How much each git use would cost in a blobless mirror. The two checked-out refs' blobs
arrive with their worktrees (row 1), so "at the two refs" costs nothing extra.

| # | Use (code) | Needs | Blobs it would fetch beyond the two refs |
|---|---|---|---|
| 1 | Worktree checkout of each ref: `worktree add --detach` (`refs.py:158`); every build needs it | all blobs of each checked-out tree | none extra: this is the main lazy fetch, one batch per checkout. The two refs share most blobs |
| 2 | `fetch --prune --tags` (`refs.py:107`) | commits and trees (the filter is inherited) | none |
| 3 | Release hops, `describe`, `rev-list`, `tag`, `log --format` (`analyzers/hops.py`, `analyzers/images.py`, `manifest.py`, `refs.py`) | commits only | none |
| 4 | `VERSION` and other files at a ref (`hops.py:67,105`) | one blob per ref | a few, at refs other than the two checked out |
| 5 | Migration guides at every release crossed (`guides.py:469`, `show <tag>:doc/releases/…`) | one or two blobs per release | a few per release; v3.2 → v4.2 crosses about 10 |
| 6 | `boards/deprecated.cmake` at each historic tag (`cli.py:364`) | one blob per tag | one per minor release crossed |
| 7 | SBOM upstream commits touching compiled files (`analyzers/sbom.py:210`, `log --name-only o..n -- files`) | trees only | none |
| 8 | SBOM file added/removed/changed (`sbom.py:218–222`: `cat-file -e`, `diff --numstat o n -- path`) | trees; blobs at the two refs | none |
| 9 | `git grep` and `show <sha>:path` (`analyzers/layout.py:409–457`; `srcdiff.Source` in mcuboot, storage, ble, wire) | blobs under the grepped paths at that SHA | none on the Zephyr mirror: `layout.py:409` greps at the build's own ref (checked out). `srcdiff.Source` reads module repositories (e.g. `mcuboot.py:143`), which this filter doesn't affect |
| 10 | Fork triage, upstream patch-ids: `log -p mb..target -- <files>` piped to `patch-id` (`forks.py:150–156, 232`) | **every version of the touched files across the whole upgrade range** | **the largest cost.** Every blob of those files changed in the range. For OpenDeck's 31 patches, some files changed in hundreds of upstream commits |
| 11 | Fork triage, the local commit's own diff and patch-id (`forks.py:197–201, 389, 414, 464`) | the fork's commits | none from upstream, if the fork's objects are local (fetched from the user's tree, `refs.py:140`) |
| 12 | Merge-tree, `git apply` trial and absorbed check (`forks.py:388, 442, 510`) | blobs of touched files at merge base, target and parent | blobs at the merge base (usually not a checked-out ref) |
| 13 | Content similarity against upstream commits (`forks.py:464`, `show -U0 <upstream sha>`) | each candidate upstream commit's diff | 2 blobs per changed file per candidate |
| 14 | `plan --from <untracked tree>` (`compare_tree`, `refs.py:197`) | blobs at the guessed release tag | the whole tree at that tag, once |
| 15 | `zmig doctor` (`doctor.py`: `ls-tree`, `show <ref>:<board>.yaml`, `SDK_VERSION`) | trees, plus two blobs | two blobs: **doctor would need the network** for them |

## What would trigger lazy fetches

- **Expected and cheap:** the worktree checkouts (once per ref, batched), and a handful of
  single files at historic tags (rows 4–6, 15).
- **Expected and possibly expensive:** fork triage (rows 10, 12, 13). It reads many
  versions of the patched files across the range, and `git log -p` may fetch blobs one
  commit at a time. Whether git 2.53 batches these prefetches has to be measured, not
  assumed.
- **Operational change.** Today, after the first run, the cache is self-contained and
  later scans work offline. With a partial clone, any of the rows above can need the
  network mid-analysis.
  - Mitigation: run analysis with `GIT_NO_LAZY_FETCH=1` once the expected objects are
    fetched, so a missing object fails loudly with its name, not with a silent download.
  - Also: report which objects were fetched lazily, in `logs/`.

## How to measure it (on a good connection)

**Setup.**

- Same machine and git (2.53). Two clean caches: `A` (today's full clone) and `B`
  (`--filter=blob:none`). B needs a one-line change behind an opt-in environment variable,
  so A is unchanged.
- Record the link speed first (one 100 MB download), so the times can be normalized.

**Per cache, in order:**

1. The clone: wall time, `du -sh` of the mirror, `git count-objects -vH`.
2. The quickstart fixture scan, cold (`quickstart2.sh`'s scan 1). Record:
   - wall time;
   - bytes and count of lazy fetches: `GIT_TRACE2_EVENT=<file>`, count the `fetch` child
     processes and their sizes;
   - mirror size afterwards.
3. `zmig plan --from v3.6.0 --to v3.7.0`, no workspace.
4. Fork triage on a patched tree: OpenDeck's plan with `--patch-method git-apply` (rows
   10–13; the worst case).
5. The CANnectivity watch fixture flow (two scans, pinned).
6. The same scan again with the network off and `GIT_NO_LAZY_FETCH=1`: does it still work
   from the cache?

**Requirement (review, 2026-10-29).** With lazy fetches disabled
(`GIT_NO_LAZY_FETCH=1`), a missing blob must **never** produce a silently shorter result.
Every analyzer or step that hits a missing object records a `not_checked` entry. The entry
names the analyzer, the object or path, and "blob not in the partial clone". Whatever
depends on it is reported as not checked, not as clean. A test runs a scan against a
blobless mirror with lazy fetches off and asserts that every git failure in the logs has
a matching `not_checked` entry.

**Accept only if all of these hold:**

- `report.json` findings are identical between A and B for every run, compared by id,
  severity and evidence;
- first-run time and bytes are lower for B, including the lazy fetches;
- B's step 6 works for scan, or exactly which analyzers need the network is documented.

**Reject or limit it if** fork triage's lazy fetches cost more than the full clone saves.
In that case, keep blobless clones for `scan` and fetch the blobs of patched files in one
batch before triage (`git rev-list --objects mb..target -- <files>` piped to
`git fetch origin <missing>`). That is a separate, measured step.
