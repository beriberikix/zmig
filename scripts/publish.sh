#!/bin/bash
# Scan the commits a push would publish, print a report, and push only if the scan is
# clean and you type "yes". Never force-pushes: the remote branch must be an ancestor.
#
#   scripts/publish.sh [--scan-only] [--remote URL] [--branch NAME]
#
# The first run scans the branch's whole history; later runs scan only the commits after
# the last one this script pushed (recorded in .git/zmig-publish-scanned).
#
# Checks, on every commit's metadata, message, paths and added lines:
#   secret         token and key formats
#   email          addresses not in scripts/publish-allow.txt
#   home-path      /home/<user>, /Users/<user>, C:\Users\<user> not in the allowlist
#   internal-url   hosts under .internal/.corp/.lan/.local/.intranet, private IPv4
#   private        patterns from the private list (work-account names, internal hosts,
#                  private paths), kept outside the repo so the list itself isn't published:
#                  $ZMIG_PRIVATE_LIST, default ~/zmig-notes/publish-private.txt
set -euo pipefail

remote=https://github.com/beriberikix/zmig.git
branch=main
scan_only=0
while [ $# -gt 0 ]; do
  case $1 in
    --scan-only) scan_only=1 ;;
    --remote) remote=$2; shift ;;
    --branch) branch=$2; shift ;;
    *) echo "usage: $0 [--scan-only] [--remote URL] [--branch NAME]" >&2; exit 2 ;;
  esac
  shift
done

top=$(git rev-parse --show-toplevel)
cd "$top"
private=${ZMIG_PRIVATE_LIST:-$HOME/zmig-notes/publish-private.txt}
allow=$top/scripts/publish-allow.txt
state=$(git rev-parse --git-dir)/zmig-publish-scanned
[ -f "$private" ] || { echo "error: private list $private not found; refusing to scan without it" >&2; exit 2; }
[ -f "$allow" ] || { echo "error: allowlist $allow not found" >&2; exit 2; }
[ -z "$(git status --porcelain)" ] || { echo "error: the working tree is dirty" >&2; exit 2; }

local_sha=$(git rev-parse "refs/heads/$branch")
remote_sha=$(git ls-remote "$remote" "refs/heads/$branch" | cut -f1)
if [ -n "$remote_sha" ]; then
  if ! git cat-file -e "$remote_sha^{commit}" 2>/dev/null \
      || ! git merge-base --is-ancestor "$remote_sha" "$local_sha"; then
    echo "error: remote $branch ($remote_sha) is not an ancestor of local $branch; not pushing" >&2
    exit 1
  fi
fi

if [ -f "$state" ] && git cat-file -e "$(cat "$state")^{commit}" 2>/dev/null; then
  range="$(cat "$state")..$local_sha"
  what="commits after the last push ($(git rev-parse --short "$(cat "$state")"))"
else
  range=$local_sha
  what="the whole history of $branch (first run)"
fi
n=$(git rev-list --count "$range")
echo "zmig publish: $branch $(git rev-parse --short "$local_sha") -> $remote"
echo "remote $branch: ${remote_sha:-(none)}"
echo "scanning $n commit(s): $what"
echo

scan_py=$(cat <<'PY'
import re, subprocess, sys
from collections import Counter

private_file, allow_file, rev_range, expected = sys.argv[1:5]

def entries(path):
    out = []
    for ln in open(path, encoding="utf-8"):
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            kind, _, val = ln.partition(" ")
            out.append((kind, val.strip()))
    return out

allowed = {}
for kind, val in entries(allow_file):
    allowed.setdefault(kind, set()).add(val)
private = [(kind, re.compile(val)) for kind, val in entries(private_file)]

SECRETS = [
    ("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    ("aws-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("slack-token", r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    ("openai-key", r"\bsk-[A-Za-z0-9]{40,}"),
    ("google-key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("assignment", r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"),
]
SECRETS = [(k, re.compile(r)) for k, r in SECRETS]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
HOME = re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\\\?Users\\\\?)[^/\\\s\"'`),:;]+")
INTERNAL = re.compile(
    r"\b(?:[a-z0-9-]+\.)+(?:internal|corp|lan|local|intranet)\b(?![.\w])"
    r"|\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b",
    re.I,
)

def git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True, errors="replace", check=True).stdout

hits, allowed_seen = [], Counter()

def check(sha, where, text):
    for kind, rx in SECRETS:
        for m in rx.finditer(text):
            hits.append(("secret", sha, where, f"{kind}: {m[0][:12]}…"))
    for m in EMAIL.finditer(text):
        if m[0] in allowed.get("email", ()):
            allowed_seen["email " + m[0]] += 1
        else:
            hits.append(("email", sha, where, m[0]))
    for m in HOME.finditer(text):
        if m[0] in allowed.get("home", ()):
            allowed_seen["home " + m[0]] += 1
        else:
            hits.append(("home-path", sha, where, m[0]))
    for m in INTERNAL.finditer(text):
        if m[0] in allowed.get("host", ()):
            allowed_seen["host " + m[0]] += 1
        else:
            hits.append(("internal-url", sha, where, m[0]))
    for kind, rx in private:
        if kind == "path":
            continue
        for m in rx.finditer(text):
            hits.append((f"private:{kind}", sha, where, m[0]))

shas = git("rev-list", "--reverse", rev_range).split()
if len(shas) != int(expected):
    sys.exit(f"error: scanned {len(shas)} commits, expected {expected}")
for sha in shas:
    short = sha[:7]
    meta = git("log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B", sha)
    check(short, "commit metadata/message", meta)
    for path in git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha).splitlines():
        check(short, "path", path)
        for kind, rx in private:
            if kind == "path" and rx.search(path):
                hits.append(("private:path", short, "path", path))
    cur, line = None, 0
    for ln in git("show", "--format=", "-U0", "--no-color", "--no-ext-diff", sha).splitlines():
        if ln.startswith("+++ "):
            cur = ln[6:] if ln.startswith("+++ b/") else ln[4:]
        elif ln.startswith("@@"):
            m = re.search(r"\+(\d+)", ln)
            line = int(m[1]) if m else 0
        elif ln.startswith("+") and cur:
            check(short, f"{cur}:{line}", ln[1:])
            line += 1

kinds = ["secret", "email", "home-path", "internal-url", "private:name", "private:url", "private:path"]
counts = Counter(h[0] for h in hits)
print(f"scanned {len(shas)} commit(s)\n")
print("Check          Hits")
for k in kinds + sorted(set(counts) - set(kinds)):
    print(f"{k:<14} {counts.get(k, 0)}")
if allowed_seen:
    print("\nAllowed by scripts/publish-allow.txt (occurrences):")
    for k, v in sorted(allowed_seen.items()):
        print(f"  {v:>5}  {k}")
if hits:
    print("\nHits:")
    seen = set()
    for kind, sha, where, what in hits:
        key = (kind, where, what)
        if key in seen:
            continue
        seen.add(key)
        print(f"  {kind:<13} {sha} {where}: {what}")
    sys.exit(1)
print("\nclean")
PY
)
if ! python3 -I -c "$scan_py" "$private" "$allow" "$range" "$n"; then
  echo
  echo "Not pushing: the scan found the hits above." >&2
  exit 1
fi

echo
echo "Commits to push:"
git log --format='  %h %s' "${remote_sha:+$remote_sha..}$local_sha"
[ $scan_only = 1 ] && { echo; echo "--scan-only: not pushing"; exit 0; }
echo
read -r -p "Push $branch to $remote? Type yes to push: " answer
if [ "$answer" != yes ]; then
  echo "Not pushed."
  exit 1
fi
git push "$remote" "$local_sha:refs/heads/$branch"
pushed=$(git ls-remote "$remote" "refs/heads/$branch" | cut -f1)
if [ "$pushed" != "$local_sha" ]; then
  echo "error: remote $branch is $pushed after the push, expected $local_sha" >&2
  exit 1
fi
echo "$local_sha" > "$state"
echo "Pushed: remote $branch == local $(git rev-parse --short "$local_sha")"
