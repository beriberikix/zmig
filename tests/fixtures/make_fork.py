"""Build the synthetic fork workspace used to test `zmig plan`.

The workspace is a T1 west workspace (zephyr is the manifest repository) based
on v3.6.0, with known divergences:

zephyr (git, branch `product`), commits oldest first:
  V1 vendor  exact cherry-pick of a real v3.6.0..v3.7.0 commit     -> upstreamed (patch-id)
  V2 vendor  cherry-pick of another one, then altered              -> upstreamed (subject)
  P1 local   change the line upstream changed in kernel/Kconfig    -> conflicts
  P2 local   append to a file upstream never touched in the range  -> applies cleanly
  P3 local   append to a file upstream changed far from its end    -> applies, overlaps
  V1 and V2 are pushed to a `vendor` remote; P1-P3 are unpushed.
  Plus an uncommitted edit to README.rst.
tinycrypt (git, at v3.6.0's pin + 1 local commit, url still upstream) -> unrecorded fork
segger (plain directory, no .git, one file edited)                     -> content mismatch

Commit choices are computed from the fixed release tags, so the fixture is the same on
every machine. Expected outcomes are written to expected.json next to the workspace.

Usage: python tests/fixtures/make_fork.py <dest> <zephyr mirror (bare repo)>
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

FROM, TO = "v3.6.0", "v3.7.0"
ENV = {
    "GIT_AUTHOR_NAME": "Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.com",
    "GIT_COMMITTER_NAME": "Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.com",
    "GIT_AUTHOR_DATE": "2024-03-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2024-03-01T00:00:00Z",
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "GIT_CONFIG_NOSYSTEM": "1",
}
MODULES = {
    "tinycrypt": ("https://github.com/zephyrproject-rtos/tinycrypt", "modules/crypto/tinycrypt"),
    "segger": ("https://github.com/zephyrproject-rtos/segger", "modules/debug/segger"),
}


def git(repo: Path | str, *args: str, input: str | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=ENV,
        input=input,
    ).stdout.strip()


def pick_upstream_commits(mirror: Path, n: int) -> list[str]:
    """Oldest small single-file C commits in FROM..TO whose file is unchanged between FROM
    and the commit's parent, so a cherry-pick onto FROM keeps the same patch-id."""
    out = []
    for c in git(mirror, "rev-list", "--reverse", "--no-merges", f"{FROM}..{TO}").split():
        files = git(mirror, "diff-tree", "--no-commit-id", "--name-only", "-r", c).splitlines()
        if len(files) != 1 or not files[0].endswith((".c", ".h")):
            continue
        added, removed, _ = git(mirror, "diff-tree", "--no-commit-id", "--numstat", "-r", c).split(
            "\t"
        )
        if not (added.isdigit() and int(added) + int(removed) <= 6):
            continue
        if (
            subprocess.run(
                ["git", "-C", str(mirror), "diff", "--quiet", FROM, f"{c}^", "--", files[0]],
                env=ENV,
            ).returncode
            != 0
        ):
            continue
        if len(git(mirror, "log", "-1", "--format=%s", c).split()) < 4:
            continue
        if any(
            files[0] == git(mirror, "diff-tree", "--no-commit-id", "--name-only", "-r", o)
            for o in out
        ):
            continue
        out.append(c)
        if len(out) == n:
            return out
    raise SystemExit("no suitable upstream commits found")


def untouched_file(mirror: Path) -> str:
    changed = set(git(mirror, "diff", "--name-only", FROM, TO).splitlines())
    for f in git(mirror, "ls-tree", "-r", "--name-only", FROM, "lib/os").splitlines():
        if f.endswith(".c") and f not in changed:
            return f
    raise SystemExit("no untouched file found")


def changed_file_with_quiet_end(mirror: Path) -> str:
    """A .c file upstream changed in FROM..TO, whose last 30 lines it didn't touch."""
    for f in git(mirror, "diff", "--name-only", FROM, TO, "--", "kernel").splitlines():
        if not f.endswith(".c"):
            continue
        try:
            n_lines = len(git(mirror, "show", f"{FROM}:{f}").splitlines())
        except subprocess.CalledProcessError:
            continue
        hunks = [
            h
            for h in git(mirror, "diff", "-U0", FROM, TO, "--", f).splitlines()
            if h.startswith("@@")
        ]
        last = max(int(h.split()[1][1:].split(",")[0]) for h in hunks)
        if n_lines - last > 30:
            return f
    raise SystemExit("no suitable overlapping file found")


def commit_all(repo: Path, msg: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


def build(dest: Path, mirror: Path) -> dict[str, object]:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    zephyr = dest / "zephyr"
    git(dest, "clone", "-q", "--shared", "--no-checkout", str(mirror), str(zephyr))
    git(zephyr, "checkout", "-q", "-b", "product", FROM)
    git(zephyr, "remote", "set-url", "origin", "https://github.com/zephyrproject-rtos/zephyr")
    vendor = dest / "vendor.git"
    git(dest, "init", "-q", "--bare", str(vendor))
    git(zephyr, "remote", "add", "vendor", str(vendor))

    v1_up, v2_up = pick_upstream_commits(mirror, 2)
    git(zephyr, "cherry-pick", v1_up)
    v1 = git(zephyr, "rev-parse", "HEAD")
    git(zephyr, "cherry-pick", "--no-commit", v2_up)
    f2 = git(zephyr, "diff", "--cached", "--name-only")
    with open(zephyr / f2, "a") as fh:
        fh.write("/* vendor variant of the upstream fix */\n")
    v2 = commit_all(zephyr, git(mirror, "log", "-1", "--format=%s", v2_up))
    git(zephyr, "push", "-q", "vendor", "product:vendor-main")
    git(zephyr, "fetch", "-q", "vendor")

    kconfig = zephyr / "kernel" / "Kconfig"
    old_line = "\tdefault 512 if ZTEST && !(RISCV || X86 || ARM)\n"
    text = kconfig.read_text()
    assert old_line in text, "kernel/Kconfig no longer has the expected line at v3.6.0"
    kconfig.write_text(text.replace(old_line, "\tdefault 768 if ZTEST && !(RISCV || X86 || ARM)\n"))
    p1 = commit_all(zephyr, "kernel: product: raise ZTEST main stack size")

    f_untouched = untouched_file(mirror)
    with open(zephyr / f_untouched, "a") as fh:
        fh.write("/* product: audited 2024-03 */\n")
    p2 = commit_all(zephyr, f"product: mark {f_untouched} as audited")

    f_overlap = changed_file_with_quiet_end(mirror)
    with open(zephyr / f_overlap, "a") as fh:
        fh.write("/* product: tracing hook placeholder */\n")
    p3 = commit_all(zephyr, f"product: add tracing hook note to {f_overlap}")

    with open(zephyr / "README.rst", "a") as fh:
        fh.write("\nProduct fork notes (uncommitted).\n")

    west_dir = dest / ".west"
    west_dir.mkdir()
    (west_dir / "config").write_text(
        "[manifest]\npath = zephyr\nfile = west.yml\n\n[zephyr]\nbase = zephyr\n"
    )

    pins = {}
    for name, (url, path) in MODULES.items():
        pin = git(mirror, "show", f"{FROM}:west.yml")
        block = pin.split(f"- name: {name}\n", 1)[1]
        rev = next(
            ln.split(":", 1)[1].strip()
            for ln in block.splitlines()
            if ln.strip().startswith("revision:")
        )
        pins[name] = rev
        clone = dest / path
        git(dest, "clone", "-q", url, str(clone))
        git(clone, "checkout", "-q", rev)

    tc = dest / MODULES["tinycrypt"][1]
    git(tc, "checkout", "-q", "-b", "local")
    readme = next(p for p in sorted(tc.iterdir()) if p.name.lower().startswith("readme"))
    with open(readme, "a") as fh:
        fh.write("\nLocal note.\n")
    tc_commit = commit_all(tc, "tinycrypt: local note")

    sg = dest / MODULES["segger"][1]
    shutil.rmtree(sg / ".git")
    edited = sorted(p for p in sg.rglob("*.c"))[0]
    with open(edited, "a") as fh:
        fh.write("/* vendored copy, locally patched */\n")

    expected = {
        "from": FROM,
        "to": TO,
        "zephyr": {
            v1: {
                "state": "upstreamed",
                "match": "patch-id",
                "upstream": v1_up,
                "provenance": "remote",
            },
            v2: {
                "state": "upstreamed",
                "match": "subject",
                "upstream": v2_up,
                "provenance": "remote",
            },
            p1: {
                "state": "conflicts",
                "conflict_files": ["kernel/Kconfig"],
                "provenance": "local-unpushed",
            },
            p2: {"state": "applies", "overlap": [], "provenance": "local-unpushed"},
            p3: {"state": "applies", "overlap": [f_overlap], "provenance": "local-unpushed"},
        },
        "zephyr_uncommitted": ["README.rst"],
        "tinycrypt": {"pin": pins["tinycrypt"], "local_commit": tc_commit},
        "segger": {"pin": pins["segger"], "modified": [edited.relative_to(sg).as_posix()]},
    }
    (dest / "expected.json").write_text(json.dumps(expected, indent=2) + "\n")
    return expected


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(json.dumps(build(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()), indent=2))
