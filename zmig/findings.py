"""Findings and the report envelope (schema version 1, see zmig/schema/report-1.json).

A Finding's id is a hash of what it is about and which trees it compares, so the
same inputs always produce the same ids, and reports can be diffed by id.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

SCHEMA_URI = "https://zmig.dev/schema/report/1"
SCHEMA_VERSION = "1.4"


class Severity(StrEnum):
    BREAKING = "breaking"
    RISKY = "risky"
    INFO = "info"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Category(StrEnum):
    MANIFEST = "manifest"
    FORK = "fork"
    HOP = "hop"
    BOARD = "board"
    KCONFIG = "kconfig"
    DEVICETREE = "devicetree"
    BUILD = "build"
    GUIDE = "guide"
    SBOM = "sbom"
    IMAGES = "images"
    CROSSIMAGE = "crossimage"
    INIT = "init"
    API = "api"
    # Field-upgrade hazard categories (P1).
    SYSBUILD = "sysbuild"
    LAYOUT = "layout"
    MCUBOOT = "mcuboot"
    STORAGE = "storage"
    BLE = "ble"
    WIRE = "wire"


class Remedy(StrEnum):
    """What resolves a field-upgrade hazard (docs/DESIGN-P1.md §8.1)."""

    CONFIG_PIN = "config-pin"
    APP_CHANGE = "app-change"
    INTERMEDIATE_HOP = "intermediate-hop"
    BOOTLOADER_UPDATE = "bootloader-update"
    REFLASH = "reflash"
    UNKNOWN = "unknown"


# Remedies an application OTA alone can't carry out (make the verdict "blocked").
NOT_BY_APP_OTA = frozenset({Remedy.BOOTLOADER_UPDATE, Remedy.REFLASH})


_SEV_ORDER = {Severity.BREAKING: 0, Severity.RISKY: 1, Severity.INFO: 2}
_CONF_ORDER = {Confidence.HIGH: 0, Confidence.MEDIUM: 1, Confidence.LOW: 2}

JSON = dict[str, Any]


# Evidence constructors. Each returns a plain dict tagged with "type"; the schema
# defines the fields per type.


def file_line(path: str, line: int, text: str | None = None) -> JSON:
    ev: JSON = {"type": "file_line", "path": path, "line": line}
    if text is not None:
        ev["text"] = text
    return ev


def artifact_diff(artifact: str, old_ref: str, new_ref: str, old: Any, new: Any) -> JSON:
    return {
        "type": "artifact_diff",
        "artifact": artifact,
        "old_ref": old_ref,
        "new_ref": new_ref,
        "old": old,
        "new": new,
    }


def git_commit(repo: str, sha: str, subject: str, patch_id: str | None = None) -> JSON:
    ev: JSON = {"type": "git_commit", "repo": repo, "sha": sha, "subject": subject}
    if patch_id is not None:
        ev["patch_id"] = patch_id
    return ev


def git_conflict(repo: str, sha: str, files: list[str]) -> JSON:
    return {"type": "git_conflict", "repo": repo, "sha": sha, "files": sorted(files)}


def doc_entry(doc: str, line: int, ref: str, entry_id: str, text: str | None = None) -> JSON:
    ev: JSON = {"type": "doc_entry", "doc": doc, "line": line, "ref": ref, "entry_id": entry_id}
    if text is not None:
        ev["text"] = text
    return ev


def tool_output(invocation: str, excerpt: str) -> JSON:
    return {"type": "tool_output", "invocation": invocation, "excerpt": excerpt}


def plural(n: int, word: str, suffix: str = "s") -> str:
    """'1 file', '3 files'."""
    return f"{n} {word}" if n == 1 else f"{n} {word}{suffix}"


def make_id(category: Category, kind: str, subject: str, scope: tuple[str, ...]) -> str:
    """Stable id: CATEGORY-KIND-hash8 over (category, kind, subject, scope).

    `scope` identifies the comparison (e.g. old sha, new sha, board) so that the
    same subject in a different migration gets a different id.
    """
    material = json.dumps([str(category), kind, subject, list(scope)])
    digest = hashlib.sha256(material.encode()).hexdigest()[:8]
    return f"{category.upper()}-{kind.upper()}-{digest}"


@dataclass(frozen=True)
class Finding:
    category: Category
    kind: str
    subject: str
    title: str
    severity: Severity
    confidence: Confidence
    action: str
    analyzer: str
    scope: tuple[str, ...]
    evidence: tuple[JSON, ...] = ()
    old: Any = None
    new: Any = None
    related: tuple[str, ...] = ()
    heuristics: tuple[str, ...] = ()
    # Sysbuild image the finding is about; None means the app image (or the build as a
    # whole), which keeps reports of plain builds unchanged.
    image: str | None = None
    # Schema 1.2. True when the migration caused the problem; False when the source build
    # already had it, unchanged (docs/DESIGN-P1.md §2.3).
    introduced_by_migration: bool = True
    # Hazard findings only: what resolves it, and where the evidence and role came from.
    remedy: Remedy | None = None
    provenance: JSON | None = None

    @property
    def id(self) -> str:
        return make_id(self.category, self.kind, self.subject, self.scope)

    def sort_key(self) -> tuple[int, int, str, str]:
        return (
            _SEV_ORDER[self.severity],
            _CONF_ORDER[self.confidence],
            str(self.category),
            self.id,
        )

    def to_json(self) -> JSON:
        return {
            "id": self.id,
            "category": str(self.category),
            "kind": self.kind,
            "subject": self.subject,
            "title": self.title,
            "severity": str(self.severity),
            "confidence": str(self.confidence),
            "old": self.old,
            "new": self.new,
            "evidence": list(self.evidence),
            "action": self.action,
            "related": sorted(self.related),
            "analyzer": self.analyzer,
            "heuristics": sorted(self.heuristics),
            **({"image": self.image} if self.image else {}),
            "introduced_by_migration": self.introduced_by_migration,
            **({"remedy": str(self.remedy)} if self.remedy else {}),
            **({"provenance": self.provenance} if self.provenance else {}),
        }


@dataclass(frozen=True)
class NotChecked:
    """Something the analysis could not verify (report section "not checked")."""

    area: str
    reason: str
    analyzer: str

    def to_json(self) -> JSON:
        return {"area": self.area, "reason": self.reason, "analyzer": self.analyzer}


@dataclass(frozen=True)
class Checked:
    """Something an analyzer or tool did examine (report section "checked"), so a report
    tells "ran, found nothing" apart from "did not run" (that's NotChecked)."""

    area: str
    result: str  # clean | found | partial
    detail: str
    analyzer: str

    def to_json(self) -> JSON:
        return {
            "area": self.area,
            "result": self.result,
            "detail": self.detail,
            "analyzer": self.analyzer,
        }


@dataclass(frozen=True)
class AnalyzerStatus:
    name: str
    status: str  # ok | partial | skipped | error
    reason: str | None = None
    # What it reported when it ran: all findings, and the breaking/risky ones.
    findings: int | None = None
    hazards: int | None = None

    def to_json(self) -> JSON:
        d: JSON = {"name": self.name, "status": self.status}
        if self.reason:
            d["reason"] = self.reason
        if self.findings is not None:
            d["findings"] = self.findings
            d["hazards"] = self.hazards or 0
        return d


TOP_N = 5


@dataclass
class Report:
    command: str
    zmig_version: str
    inputs: JSON
    environment: JSON
    contexts: list[JSON] = field(default_factory=list)
    analyzers: list[AnalyzerStatus] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    not_checked: list[NotChecked] = field(default_factory=list)
    checked: list[Checked] = field(default_factory=list)
    upgrade_path: JSON | None = None

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=Finding.sort_key)

    def top(self) -> list[Finding]:
        """The first findings to act on: breaking and risky ones only. Findings the migration
        introduces come before ones already true of the source build; then breaking findings
        that point into the user's own files ahead of the plain order."""

        def key(f: Finding) -> tuple[int, int, tuple[int, int, str, str]]:
            in_app = any(e.get("type") == "file_line" for e in f.evidence)
            boost = 0 if (f.severity is Severity.BREAKING and in_app) else 1
            return (0 if f.introduced_by_migration else 1, boost, f.sort_key())

        actionable = [f for f in self.findings if f.severity is not Severity.INFO]
        return sorted(actionable, key=key)[:TOP_N]

    def counts(self) -> dict[str, int]:
        return {str(s): sum(1 for f in self.findings if f.severity is s) for s in Severity}

    def to_json(self) -> JSON:
        return {
            "schema": SCHEMA_URI,
            "schema_version": SCHEMA_VERSION,
            "zmig_version": self.zmig_version,
            "command": self.command,
            "inputs": self.inputs,
            "environment": self.environment,
            "contexts": self.contexts,
            "analyzers": [a.to_json() for a in sorted(self.analyzers, key=lambda a: a.name)],
            "summary": {"counts": self.counts(), "top": [f.id for f in self.top()]},
            "findings": [f.to_json() for f in self.sorted_findings()],
            **({"upgrade_path": self.upgrade_path} if self.upgrade_path else {}),
            "not_checked": [
                n.to_json()
                for n in sorted(set(self.not_checked), key=lambda n: (n.area, n.analyzer, n.reason))
            ],
            "checked": [
                c.to_json()
                for c in sorted(set(self.checked), key=lambda c: (c.analyzer, c.area, c.detail))
            ],
        }
