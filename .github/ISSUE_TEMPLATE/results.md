---
name: Results on my project
about: How zmig did on a real Zephyr upgrade of your project
title: "Results: <project>, <from> → <to>"
labels: results
---

<!-- Thanks! CONTRIBUTING.md explains how to run zmig for this. Check any attached
report.json or logs for paths from your machine (your home directory) before posting. -->

**Project:** <!-- name, and a link if it's public -->
**Upgrade:** <!-- --from and --to refs -->
**Build:** <!-- board, --sysbuild, modules, CMake arguments -->
**zmig version:** <!-- `zmig --version`, or the commit -->
**The upgrade commit(s):** <!-- if you already did the upgrade, and they're public -->

### Findings

One row per finding you have an opinion on. The ID is in MIGRATION.md or report.json.

| ID | Tier | What it says (short) | Verdict | Why, or what you actually had to do |
|---|---|---|---|---|
| | | | useful / noisy / wrong | |

- **Useful:** right, and it saved you time. Did the action name the file you edited?
- **Noisy:** true, but irrelevant to your build, or a duplicate. Should it have been a lower tier?
- **Wrong:** false, or the wrong cause or file. Paste the evidence line.

### Missed

For each change you had to make that zmig didn't mention:
- the file and line, and the commit if you have one;
- whether it was in Kconfig, devicetree, C code, CMake, west/modules, or a patch;
- whether the build would have shown it at all (a compile error, a changed value in
  `.config` or `zephyr.dts`).

### Running it

- How long each command took, and whether it was the first run (the Zephyr clone):
- Anything you had to work out to get the build running under zmig (arguments, modules,
  environment variables):
- Any crash or confusing message (the command, and the last 20 lines of stderr):
