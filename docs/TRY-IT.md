# Trying zmig on one of your projects

This page covers running zmig on your own project and noting what it got right and
wrong, so the findings can feed the next round of changes.

## Before you start

- On a good connection, run `zmig doctor --board <your board> --sdk <SDK dir>`.
  - Fix every `FAIL` it reports.
  - The first `scan` or `plan` clones Zephyr, about 1 GB.
- Pick an upgrade you already did, or one you're about to do, and note two things:
  - **the two refs:** a tag, a SHA, or a local Zephyr checkout for `--from`/`--to`;
  - **the build:** the board, `--sysbuild` if you use it, extra modules, and the CMake
    arguments you normally pass (`-DEXTRA_CONF_FILE=…`, `-DSHIELD=…`, `-DFILE_SUFFIX=…`).
- If you already did the upgrade, keep the commit that did it. It's your answer key.

## 1. Plan: workspace and patches

```sh
zmig plan --workspace <west topdir> --from <old ref> --to <new ref> --out out/plan
# patch files applied with plain `git apply`:
#   add --patch-method git-apply --patch-dir <your patch dir>
```

**What to look at:**

- module changes in `west.yml`;
- for each local commit or patch: upstreamed, applies, or conflicts, with the failing
  hunk;
- the suggested path through releases.

## 2. Scan: your app on your board

```sh
zmig scan --app <app dir> --board <board at old ref> --from <old ref> --to <new ref> \
          --sdk <SDK dir> [--sysbuild] [--module <name> ...] [--cmake-arg=-D...] --out out/scan
```

**What to look at, in this order:**

1. The first lines in the terminal: the build-migration counts, and whether the target
   built.
2. `out/scan/MIGRATION.md`, the "By tier" section: **Act now** first, then **Review**.
   FYI is everything else, still listed in full.
3. The field-upgrade verdict, if you ship updates to devices: each category's status and
   evidence.
4. `out/scan/logs/`: every command zmig ran. Look here when a finding doesn't make sense.

If the target doesn't configure, zmig comments out the lines that stop it in its own copy
("salvaged") and keeps going. Findings in Kconfig, devicetree and init order then come
from that salvaged build.

## 3. Fix kconfig: dry run first

```sh
zmig fix kconfig <the same arguments as scan>            # dry run: prints the plan and a diff
zmig fix kconfig <the same arguments as scan> --apply    # commits on a new branch
```

- `--apply` needs a clean git tree. It creates the branch `zmig/fix-kconfig-<target>`, with
  one commit per fix kind.
- It edits only two things:
  - renames that Kconfig itself evidences;
  - assignments of symbols the target removed with no evidenced replacement, which it
    comments out with a note.
- Everything else is listed under "Needs a human".
- After `--apply` it rebuilds and reports findings as gone, revealed or introduced. Exit
  code 3 means the fix introduced something.
- To undo it: `git switch -` and `git branch -D zmig/fix-kconfig-<target>`.

## 4. Record what you saw

Make one entry per finding you have an opinion on. Use the finding id (`` `KCONFIG-…` ``)
from MIGRATION.md or `report.json`.

| Id | Tier | What it says (short) | Verdict | Why / what you actually had to do |
|---|---|---|---|---|
| | | | useful / noisy / wrong | |

- **Useful:** right, and it saved you time. Did the action name the file you edited?
- **Noisy:** true, but irrelevant to your build, or a duplicate. Should it have been a
  lower tier?
- **Wrong:** false, or the wrong cause or file. Paste the evidence line.

**Missing.** For each change you had to make that zmig didn't mention, note:

- the file and line, and the commit if you have one;
- whether it was in Kconfig, devicetree, C code, CMake, west/modules, or a patch;
- whether your build would have shown it at all, for example a compile error or a changed
  value in `.config` or `zephyr.dts`.

**Also note:**

- How long each command took. Was it the first run, or was the mirror already there?
- Anything you had to work out to get the build running under zmig: arguments, modules,
  environment variables.
- Any crash or confusing message: the command and the last 20 lines of stderr.

Send back the table, the missing list, and the `out/` directories, or at least
`report.json` and `logs/invocations.json`. Treat paths in them as private: they contain
your home directory.
