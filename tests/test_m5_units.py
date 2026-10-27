"""SPDX reading, warning identity, and renderers."""

from __future__ import annotations

import json

from zmig.diagnostics import parse_warnings
from zmig.findings import Category, Confidence, Finding, Report, Severity, file_line
from zmig.report import render
from zmig.sbom import compiled_sources, parse_spdx

# Shapes as written by `west spdx` at v3.6.0 (SPDX-2.2, module package named "zcbor")
# and v3.7.0 (SPDX-2.3, "zcbor-sources").
SPDX_22 = """SPDXVersion: SPDX-2.2
PackageName: zephyr-sources
SPDXID: SPDXRef-zephyr-sources

FileName: ./zephyr/kernel/sched.c
FileChecksum: SHA1: aaa
FileChecksum: SHA256: bbb

PackageName: zcbor
FileName: ./src/zcbor_common.c
FileChecksum: SHA1: ccc
"""
SPDX_23 = SPDX_22.replace("SPDX-2.2", "SPDX-2.3").replace(
    "PackageName: zcbor\n", "PackageName: zcbor-sources\n"
)


def test_spdx_versions_and_module_names() -> None:
    for text, version in ((SPDX_22, "SPDX-2.2"), (SPDX_23, "SPDX-2.3")):
        doc = parse_spdx(text, "zephyr")
        assert doc.version == version
        assert compiled_sources({"zephyr": doc}) == {
            "zephyr": {"zephyr/kernel/sched.c": "aaa"},
            "zcbor": {"src/zcbor_common.c": "ccc"},
        }


def test_warning_identity_ignores_paths_and_lines() -> None:
    old = parse_warnings(
        "CMake Warning at /ws/AAA/zephyr/CMakeLists.txt:1969 (message):\n"
        "  __ASSERT() statements are globally ENABLED\n\n\n"
        "/b/x/app/src/main.c:10:5: warning: unused variable 'x' [-Wunused-variable]\n"
        "warning: Deprecated symbol CPLUSPLUS is enabled.\n"
    )
    new = parse_warnings(
        "CMake Warning at /ws/BBB/zephyr/CMakeLists.txt:2094 (message):\n"
        "  __ASSERT() statements are globally ENABLED\n\n"
        "/b/y/app/src/main.c:12:5: warning: unused variable 'x' [-Wunused-variable]\n"
    )
    assert [w.source for w in old] == ["compiler", "kconfig", "cmake"]
    assert old[0].flag == "-Wunused-variable"
    ko = {w.key({"/ws/AAA/zephyr": "<zephyr>", "/b/x/app": "<app>"}) for w in old}
    kn = {w.key({"/ws/BBB/zephyr": "<zephyr>", "/b/y/app": "<app>"}) for w in new}
    assert kn <= ko and ko - kn == {("kconfig", "", "Deprecated symbol CPLUSPLUS is enabled.")}


def _report() -> Report:
    ref = {"spec": "v1", "sha": "a" * 40, "kind": "tag", "dirty": False}
    f = Finding(
        category=Category.KCONFIG,
        kind="symbol-removed",
        subject="CONFIG_X",
        title="CONFIG_X removed",
        severity=Severity.BREAKING,
        confidence=Confidence.HIGH,
        action="Drop it.",
        analyzer="kconfig",
        scope=("a",),
        evidence=(file_line("prj.conf", 3, "CONFIG_X=y"), file_line("<zephyr v2>/Kconfig", 9)),
    )
    g = Finding(
        category=Category.SBOM,
        kind="zephyr-sources",
        subject="zephyr",
        title="no location",
        severity=Severity.INFO,
        confidence=Confidence.HIGH,
        action="-",
        analyzer="sbom",
        scope=("a",),
    )
    return Report(
        command="scan",
        zmig_version="0",
        findings=[f, g],
        inputs={
            "from": ref,
            "to": {**ref, "spec": "v2"},
            "app": "app",
            "board": {"from": "b", "to": "b"},
        },
        environment={},
    )


def test_sarif_only_user_locations() -> None:
    doc = json.loads(render(_report(), "sarif"))
    (res,) = doc["runs"][0]["results"]
    assert res["level"] == "error" and res["ruleId"] == "kconfig/symbol-removed"
    assert [loc["physicalLocation"]["artifactLocation"]["uri"] for loc in res["locations"]] == [
        "prj.conf"
    ]
    assert res["partialFingerprints"]["zmigFindingId"].startswith("KCONFIG-SYMBOL-REMOVED-")


def test_human_summary() -> None:
    text = render(_report(), "human")
    assert "1 breaking, 0 risky, 1 info" in text and "[prj.conf:3]" in text
    assert "(+1 info findings" in text
