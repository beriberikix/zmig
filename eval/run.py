"""Re-run zmig's scored evaluation configurations and compare them with their baselines.

    python eval/run.py fetch [CONFIG...]
    python eval/run.py scan --out DIR [--zmig REF] [CONFIG...]
    python eval/run.py compare --out DIR [CONFIG...]
    python eval/run.py snapshot CONFIG REPORT_JSON

`scan` runs each configuration exactly as it was scored (eval/configs.toml), with this
checkout's zmig or, with --zmig, another commit (a git worktree). `compare` matches findings
by ID against eval/baselines/<config>.json (ORACLE-RULES §3: a finding is one finding ID) and
lists what a scorer has to look at: breaking/risky findings that are new, gone, or changed
severity. Verdicts stay a human's job; unchanged findings keep their published verdicts.

Inputs are fetched into $ZMIG_EVAL_DIR (default ~/.cache/zmig/eval). Run with the shell's
PYTHONPATH unset if it has one (zmig unsets it for builds; this script does too).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
CONFIGS = REPO / "eval" / "configs.toml"
BASELINES = REPO / "eval" / "baselines"
CACHE = Path(os.environ.get("ZMIG_CACHE", Path.home() / ".cache" / "zmig"))
EVAL = Path(os.environ.get("ZMIG_EVAL_DIR", CACHE / "eval"))
MIRROR = CACHE / "mirrors" / "github.com_zephyrproject-rtos_zephyr.git"
GATED = ("breaking", "risky")


def load() -> dict[str, Any]:
    with CONFIGS.open("rb") as f:
        return tomllib.load(f)


def git(*args: str, cwd: Path | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    )
    return proc.stdout.strip()


# --- Sources ---------------------------------------------------------------------------------


def source_path(name: str, spec: dict[str, Any]) -> Path:
    """<name>-<sha>/<repository name, or `dir`>. The last component is what the scored run
    used: Zephyr names a module after its directory (ZEPHYR_GOLIOTH_FIRMWARE_SDK_MODULE_DIR),
    and zmig labels a path ref after its directory, which is in finding subjects and IDs
    (zephyr-to@339570e2393b). No "@" in the parent: a module path with two "@" reaches Kconfig
    with everything between them cut out."""
    url: str = spec["url"]
    leaf: str = spec.get("dir") or url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    return EVAL / "src" / f"{name}-{spec['sha'][:12]}" / leaf


def fetch_source(name: str, spec: dict[str, Any]) -> Path:
    dest = source_path(name, spec)
    if (dest / ".git").exists() and git("rev-parse", "HEAD", cwd=dest) == spec["sha"]:
        return dest
    print(f"fetching {name} @ {spec['sha'][:12]}", file=sys.stderr)
    if spec["kind"] == "git":
        dest.mkdir(parents=True, exist_ok=True)
        git("init", "-q", cwd=dest)
        git("fetch", "-q", "--depth", "1", spec["url"], spec["sha"], cwd=dest)
        git("checkout", "-q", "--detach", "FETCH_HEAD", cwd=dest)
        for sub in spec.get("submodules", []):
            git("submodule", "update", "-q", "--init", "--depth", "1", "--", sub, cwd=dest)
    elif spec["kind"] == "zephyr-fork":
        # Borrow the upstream mirror's objects: the fork's history then connects to upstream
        # tags without downloading them, and zmig can place the commit (DOGFOOD-1).
        bare = EVAL / "zephyr-fork.git"
        if not bare.exists():
            git("init", "-q", "--bare", str(bare))
            (bare / "objects" / "info" / "alternates").write_text(f"{MIRROR / 'objects'}\n")
        git("--git-dir", str(bare), "fetch", "-q", spec["url"], f"{spec['sha']}:refs/eval/{name}")
        git("--git-dir", str(bare), "worktree", "add", "-q", "--detach", str(dest), spec["sha"])
    else:
        raise SystemExit(f"source {name}: unknown kind {spec['kind']!r}")
    if git("rev-parse", "HEAD", cwd=dest) != spec["sha"]:
        raise SystemExit(f"source {name}: checked out {git('rev-parse', 'HEAD', cwd=dest)}")
    return dest


def sources_of(cfg: dict[str, Any]) -> set[str]:
    text = json.dumps(cfg)
    return set(re.findall(r"\{src:([\w-]+)\}", text))


def expand(value: str, paths: dict[str, Path]) -> str:
    """{src:NAME}: a source checkout; {sdk:VERSION}: ~/.cache/zmig/sdks/zephyr-sdk-VERSION, for
    configurations whose refs need different SDKs (Zephyr 4.3 rejects SDK 1.x, 4.4 needs it)."""
    value = re.sub(r"\{sdk:([\w.]+)\}", lambda m: str(CACHE / "sdks" / f"zephyr-sdk-{m[1]}"), value)
    return re.sub(r"\{src:([\w-]+)\}", lambda m: str(paths[m[1]]), value)


def patched_app(name: str, app: Path, patch: Path) -> Path:
    """A fresh copy of `app` with `patch` (paths relative to the app directory) applied, for
    apps that can't build from zmig's copy as they are (RIT FSW finds its repository by a
    relative path out of the app directory). The copy keeps the app's directory name."""
    dest = EVAL / "patched" / name / app.name
    shutil.rmtree(dest.parent, ignore_errors=True)
    shutil.copytree(app, dest, ignore=shutil.ignore_patterns(".git"))
    git("apply", "-p1", str(patch), cwd=dest)
    return dest


# --- Running zmig ----------------------------------------------------------------------------


def zmig_command(ref: str | None) -> tuple[list[str], dict[str, str], str]:
    """(argv prefix, environment, commit) for running zmig from this checkout or `ref`."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    python = REPO / ".venv" / "bin" / "python"
    if ref is None:
        return (
            [str(REPO / ".venv" / "bin" / "zmig")],
            env,
            git("rev-parse", "--short", "HEAD", cwd=REPO),
        )
    sha = git("rev-parse", ref, cwd=REPO)
    tree = EVAL / "zmig" / sha[:12]
    if not tree.exists():
        git("worktree", "add", "-q", "--detach", str(tree), sha, cwd=REPO)
    env["PYTHONPATH"] = str(tree)
    # PYTHONPATH precedes the venv's editable install; the assert proves which zmig ran.
    code = (
        "import sys, zmig; "
        f"assert zmig.__file__.startswith({str(tree)!r}), zmig.__file__; "
        "from zmig.cli import main; sys.exit(main(sys.argv[1:]))"
    )
    return [str(python), "-c", code], env, sha[:12]


def zmig_cwd(ref: str | None) -> Path:
    """`python -c` puts the working directory first on sys.path: run a worktree's zmig from
    the worktree, so this checkout's zmig/ can't shadow it."""
    return REPO if ref is None else EVAL / "zmig" / git("rev-parse", ref, cwd=REPO)[:12]


def scan(
    configs: dict[str, Any], names: list[str], out: Path, ref: str | None, analyzers: str | None
) -> None:
    allc = configs["configs"]
    prefix, env, commit = zmig_command(ref)
    out.mkdir(parents=True, exist_ok=True)
    runs = []
    for name in names:
        cfg = allc[name]
        paths = {s: fetch_source(s, configs["sources"][s]) for s in sorted(sources_of(cfg))}
        app = expand(cfg["app"], paths)
        if "app_patch" in cfg:
            app = str(patched_app(name, Path(app), REPO / cfg["app_patch"]))
        argv = [
            *prefix, "scan",
            "--app", app,
            "--board", cfg["board"],
            "--from", expand(cfg["from"], paths),
            "--to", expand(cfg["to"], paths),
        ]  # fmt: skip
        if "sdk" in cfg:
            argv += ["--sdk", str(CACHE / "sdks" / f"zephyr-sdk-{cfg['sdk']}")]
        for m in cfg.get("modules", []):
            argv += ["--module", m]
        argv += [f"--cmake-arg={expand(a, paths)}" for a in cfg.get("cmake_args", [])]
        if analyzers is not None:
            # "none": a verification build only (no findings), for checking an invocation on a
            # held-out project before its oracle is committed (ORACLE-RULES §1.4).
            argv += ["--analyzers", analyzers]
        dest = out / name
        argv += ["--out", str(dest)]
        start = time.monotonic()
        with (out / f"{name}.human").open("w") as h, (out / f"{name}.err").open("w") as e:
            rc = subprocess.run(
                argv, env=env, cwd=zmig_cwd(ref), stdout=h, stderr=e, check=False
            ).returncode
        secs = round(time.monotonic() - start)
        print(f"{name}: exit {rc}, {secs} s", file=sys.stderr)
        runs.append({"config": name, "exit": rc, "seconds": secs, "argv": argv[len(prefix) :]})
    meta = {"zmig": commit, "runs": runs}
    (out / "eval-run.json").write_text(json.dumps(meta, indent=1) + "\n")


# --- Baselines and comparison ----------------------------------------------------------------


def summarize(report: dict[str, Any]) -> list[dict[str, str]]:
    """What a baseline keeps of a report: only what scoring needs, and no machine paths.

    Besides ID and severity, it keeps the action (actionable: does it name a file the
    maintainers edited) and the project files the evidence points at (recall's location
    condition): both can change while ID and severity don't (DOGFOOD-7 corrections)."""
    keep = ("id", "severity", "confidence", "kind", "title", "action")
    out = []
    for f in report["findings"]:
        files = sorted({str(e["path"]) for e in f["evidence"] if e.get("type") == "file_line"})
        out.append({**{k: str(f[k]) for k in keep}, "files": " ".join(files)})
    return sorted(out, key=lambda f: f["id"])


def snapshot(configs: dict[str, Any], name: str, report: Path) -> None:
    data = json.loads(report.read_text())
    base = {
        "config": name,
        "scored": configs["configs"][name]["scored"],
        "schema_version": data.get("schema_version"),
        "verdict": data.get("upgrade_path", {}).get("verdict"),
        "findings": summarize(data),
    }
    BASELINES.mkdir(parents=True, exist_ok=True)
    (BASELINES / f"{name}.json").write_text(json.dumps(base, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {BASELINES / f'{name}.json'}", file=sys.stderr)


def gated(findings: dict[str, dict[str, str]]) -> int:
    return sum(1 for f in findings.values() if f["severity"] in GATED)


def compare(configs: dict[str, Any], names: list[str], out: Path) -> int:
    """Markdown delta per configuration; exit 1 if any breaking/risky finding needs a verdict."""
    needs = 0
    for name in names:
        bpath, rpath = BASELINES / f"{name}.json", out / name / "report.json"
        if not bpath.exists() or not rpath.exists():
            print(f"## {name}\n\nskipped: {'no baseline' if not bpath.exists() else 'no report'}\n")
            continue
        base = {f["id"]: f for f in json.loads(bpath.read_text())["findings"]}
        now = {f["id"]: f for f in summarize(json.loads(rpath.read_text()))}
        lines = []
        for i in sorted(base.keys() & now.keys()):
            a, b = base[i]["severity"], now[i]["severity"]
            if a != b and (a in GATED or b in GATED):
                lines.append(f"- changed {a} → {b}: `{i}` {now[i]['title'][:100]}")
                needs += b in GATED and a not in GATED
            elif a in GATED and b in GATED:
                # Same ID and severity, but what a scorer reads may differ.
                for key, what in (("action", "action"), ("files", "evidence files")):
                    if key in base[i] and base[i][key] != now[i][key]:
                        lines.append(
                            f"- {what} changed (recheck actionable/location): `{i}`\n"
                            f"  - was: {base[i][key][:160]}\n  - now: {now[i][key][:160]}"
                        )
        for i in sorted(now.keys() - base.keys()):
            if now[i]["severity"] in GATED:
                sev, title = now[i]["severity"], now[i]["title"][:100]
                lines.append(f"- **new {sev}** (needs a verdict): `{i}` {title}")
                needs += 1
        for i in sorted(base.keys() - now.keys()):
            if base[i]["severity"] in GATED:
                lines.append(f"- gone {base[i]['severity']}: `{i}` {base[i]['title'][:100]}")
        scored = configs["configs"][name]["scored"]
        print(f"## {name}\n\nbreaking+risky {gated(base)} → {gated(now)} ({scored} → this run)\n")
        print("\n".join(lines) or "- no breaking/risky finding changed")
        print()
    return 1 if needs else 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("configs", nargs="*")
    s = sub.add_parser("scan")
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--zmig", help="zmig commit to run (default: this checkout)")
    s.add_argument("--analyzers", help='passed to zmig scan; "none" = verification build only')
    s.add_argument("configs", nargs="*")
    c = sub.add_parser("compare")
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("configs", nargs="*")
    n = sub.add_parser("snapshot")
    n.add_argument("config")
    n.add_argument("report", type=Path)
    args = p.parse_args()
    configs = load()
    names = getattr(args, "configs", None) or list(configs["configs"])
    unknown = [x for x in names if x not in configs["configs"]]
    if unknown:
        raise SystemExit(f"unknown configuration(s): {', '.join(unknown)}")
    if args.cmd == "fetch":
        for src in sorted({s for x in names for s in sources_of(configs["configs"][x])}):
            print(fetch_source(src, configs["sources"][src]))
    elif args.cmd == "scan":
        scan(configs, names, args.out, args.zmig, args.analyzers)
    elif args.cmd == "compare":
        return compare(configs, names, args.out)
    elif args.cmd == "snapshot":
        snapshot(configs, args.config, args.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
