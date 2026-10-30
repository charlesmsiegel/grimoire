"""State shared by the interpreters of one scan: findings, losses, verified reads."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import _text
from ._fs import Entry, Snapshot, TooLarge

#: Every finding code and its severity. ``blocking`` keeps the report from being
#: ready for review: the future importer must refuse until the source is fixed
#: and scanned again. ``warning`` needs a reviewer's eye; ``info`` is context.
SEVERITY = {
    # walk and paths
    "refused_entry": "blocking", "mount_point": "blocking", "unreadable": "blocking",
    "too_large": "blocking", "uninterpreted": "blocking", "excluded_link": "info", "hard_link": "info",
    "legacy_id_refused": "warning", "normalization_collision": "warning", "case_collision": "warning",
    "invalid_v1_id": "warning", "unknown_entry": "info",
    # formats
    "newer_format": "blocking", "invalid_utf8": "warning", "malformed_json": "warning",
    "malformed_card": "warning", "card_spec": "info", "missing_default_version": "warning",
    "no_versions": "warning", "legacy_full_copy_campaign": "warning", "legacy_scene_name": "warning",
    "invalid_scene_name": "warning", "missing_scene_identity": "warning",
    "duplicate_scene_identity": "warning", "unknown_fields": "info", "dangling_ref": "warning",
    "unresolved_locked_copy": "warning",
    # campaigns
    "invalid_ledger": "blocking", "invalid_ledger_entry": "warning", "world_missing": "warning",
    "world_case_variant": "warning", "missing_locked_version": "warning",
    # images
    "invalid_object": "warning", "object_shard_mismatch": "warning", "blob_hash_mismatch": "blocking",
    "blob_size_mismatch": "warning", "unarrived_blob": "warning", "orphan_blob": "info",
    "invalid_blob_name": "warning", "invalid_placement": "warning", "dangling_object": "warning",
    "legacy_image_ambiguous": "warning", "expression_layer_unavailable": "warning",
    "stale_expression_revision": "info", "invalid_collection": "warning",
    "unresolved_image_ref": "warning", "external_reference": "info",
    # identity
    "id_candidate_collision": "warning", "instance_candidate_collision": "warning",
    "no_id_candidate": "info",
}


@dataclass(frozen=True, order=True)
class Finding:
    severity_rank: int
    code: str
    paths: tuple[str, ...]
    detail: str

    @property
    def severity(self) -> str:
        return SEVERITY[self.code]

    def as_json(self) -> dict:
        return {"code": self.code, "severity": self.severity, "paths": list(self.paths),
                "detail": self.detail}


_RANK = {"blocking": 0, "warning": 1, "info": 2}


@dataclass
class Context:
    snap: Snapshot
    archive_id: str
    findings: set[Finding] = field(default_factory=set)
    losses: list[dict] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    declined: set[str] = field(default_factory=set)
    loss_details: dict[str, str] = field(default_factory=dict)

    def finding(self, code: str, *paths: str, detail: str = "") -> None:
        self.findings.add(Finding(_RANK[SEVERITY[code]], code, tuple(paths), detail))

    def loss(self, path: str, what: str, detail: str = "") -> None:
        self.losses.append({"path": path, "what": what, "detail": detail})

    def unresolve(self, what: str, path: str, reason: str) -> None:
        self.unresolved.append({"what": what, "path": path, "reason": reason})

    def decline(self, path: str) -> None:
        """Mark an interpreted file deliberately not parsed (a finding says why)."""
        self.declined.add(path)

    def read(self, entry: Entry) -> bytes | None:
        try:
            return self.snap.read(entry.path)
        except TooLarge:
            self.finding("too_large", entry.path, detail=str(entry.size))
            return None

    def read_text(self, entry: Entry) -> str | None:
        data = self.read(entry)
        if data is None:
            return None
        try:
            return _text.decode(data)
        except _text.Undecodable:
            self.finding("invalid_utf8", entry.path)
            return None

    def read_json(self, entry: Entry) -> tuple[object, bool]:
        """(value, True), or (None, False) after recording why V1 could not read it."""
        data = self.read(entry)
        if data is None:
            return None, False
        value, problem = _text.load_json(data)
        if problem is not None:
            self.finding(problem, entry.path)
            return None, False
        return value, True
