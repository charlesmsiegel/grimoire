"""Exact, deterministic JSON for the values the authority ledgers record.

Encoding sorts every set, so re-encoding decoded bytes reproduces them. Decoding
is strict: exact keys, exact types, and the value's own constructor validation.
Anything else is ``LedgerInvalid`` — the ledgers fail closed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from grimoire.core.authority import Correction, Delegation, Revocation
from grimoire.core.identity import validate_segment
from grimoire.security import Approval, AuditRecord, Grant, Permission, Reason, ResourcePath
from grimoire.store.errors import LedgerInvalid

SCHEMA = 1
MAX_ID = 128
MAX_NOTE = 4096


@dataclass(frozen=True, slots=True)
class ControlChange:
    """Who controls an actor from now on; ``controller_id=None`` unassigns it."""

    campaign_id: str
    actor_id: str
    controller_id: str | None

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")
        validate_segment(self.actor_id, "actor_id")
        if self.controller_id is not None and (type(self.controller_id) is not str or not self.controller_id.strip()):
            raise ValueError("controller_id must be nonempty text or None")


def check_size(value: object, label: str, limit: int) -> None:
    if value is not None and (type(value) is not str or len(value) > limit):
        raise ValueError(f"{label} must be text of at most {limit} characters")


def _exact(entry: Mapping[str, object], kind: str, keys: set[str]) -> Mapping[str, object]:
    if not isinstance(entry, Mapping):
        raise LedgerInvalid("a ledger entry must be an object")
    expected = keys | {"schema", "kind", "recorded_at"}
    if set(entry) != expected or entry.get("schema") != SCHEMA or entry.get("kind") != kind:
        raise LedgerInvalid(f"{kind} entry has unexpected fields, schema or kind: {sorted(entry)}")
    if type(entry["recorded_at"]) is not str:
        raise LedgerInvalid("recorded_at must be text")
    return entry


def _text(entry: Mapping[str, object], key: str, optional: bool = False) -> str | None:
    value = entry[key]
    if value is None and optional:
        return None
    if type(value) is not str:
        raise LedgerInvalid(f"{key} must be text")
    return value


def _build(factory, *args, **kwargs):
    try:
        return factory(*args, **kwargs)
    except (ValueError, TypeError) as error:
        raise LedgerInvalid(f"{getattr(factory, '__name__', factory)}: {error}") from None


def envelope(kind: str, recorded_at: str, **fields: object) -> dict[str, object]:
    return {"schema": SCHEMA, "kind": kind, "recorded_at": recorded_at, **fields}


# -- security values ----------------------------------------------------------------

def encode_grant(grant: Grant) -> dict[str, object]:
    if type(grant) is not Grant:
        raise ValueError("expected a Grant")
    check_size(grant.grant_id, "grant_id", MAX_ID)
    return {"grant_id": grant.grant_id, "principal": grant.principal,
            "permissions": sorted(p.value for p in grant.permissions), "scope": str(grant.scope),
            "granted_by": grant.granted_by}


def decode_grant(data: object) -> Grant:
    if not isinstance(data, Mapping) or set(data) != {"grant_id", "principal", "permissions", "scope", "granted_by"}:
        raise LedgerInvalid("a grant has unexpected fields")
    permissions = data["permissions"]
    if not isinstance(permissions, tuple) or not all(type(p) is str for p in permissions):
        raise LedgerInvalid("permissions must be a list of names")
    return _build(Grant, _text(data, "grant_id"), _text(data, "principal"),
                  frozenset(_build(Permission, p) for p in permissions),
                  _build(ResourcePath.parse, _text(data, "scope")), _text(data, "granted_by"))


def encode_approval(approval: Approval, pinned_grant: str) -> dict[str, object]:
    if type(approval) is not Approval:
        raise ValueError("expected an Approval")
    check_size(approval.approval_id, "approval_id", MAX_ID)
    return {"approval_id": approval.approval_id, "principal": approval.principal,
            "permission": approval.permission.value, "resource": str(approval.resource),
            "payload_digest": approval.payload_digest, "approved_by": approval.approved_by,
            "grant_id": pinned_grant}


def decode_approval(data: object) -> tuple[Approval, str]:
    keys = {"approval_id", "principal", "permission", "resource", "payload_digest", "approved_by", "grant_id"}
    if not isinstance(data, Mapping) or set(data) != keys:
        raise LedgerInvalid("an approval has unexpected fields")
    approval = _build(Approval, _text(data, "approval_id"), _text(data, "principal"),
                      _build(Permission, _text(data, "permission")),
                      _build(ResourcePath.parse, _text(data, "resource")), _text(data, "payload_digest"),
                      _text(data, "approved_by"))
    return approval, _text(data, "grant_id")  # type: ignore[return-value]


def encode_decision(record: AuditRecord) -> dict[str, object]:
    if type(record) is not AuditRecord:
        raise ValueError("expected an AuditRecord")
    return {"sequence": record.sequence, "principal": record.principal,
            "permission": None if record.permission is None else record.permission.value,
            "resource": record.resource, "trusted": record.trusted, "allowed": record.allowed,
            "reason": record.reason.value, "grant_id": record.grant_id, "approval_id": record.approval_id}


def decode_decision(data: object) -> AuditRecord:
    keys = {"sequence", "principal", "permission", "resource", "trusted", "allowed", "reason", "grant_id",
            "approval_id"}
    if not isinstance(data, Mapping) or set(data) != keys:
        raise LedgerInvalid("a decision has unexpected fields")
    if type(data["sequence"]) is not int or type(data["trusted"]) is not bool or type(data["allowed"]) is not bool:
        raise LedgerInvalid("a decision has mistyped fields")
    permission = _text(data, "permission", optional=True)
    return AuditRecord(data["sequence"], _text(data, "principal"),  # type: ignore[arg-type]
                       None if permission is None else _build(Permission, permission),
                       _text(data, "resource", optional=True), data["trusted"], data["allowed"],  # type: ignore[arg-type]
                       _build(Reason, _text(data, "reason")), _text(data, "grant_id", optional=True),
                       _text(data, "approval_id", optional=True))


# -- sovereignty values -------------------------------------------------------------

_DELEGATION = ("campaign_id", "delegation_id", "actor_id", "granted_by", "domains", "scope", "mode", "scene_id",
               "turn_id", "contribution_id")


def encode_sovereignty(value: object) -> tuple[str, dict[str, object]]:
    """``(kind, fields)`` for a ControlChange, Delegation, Revocation or Correction."""
    if type(value) is ControlChange:
        return "control", {"campaign_id": value.campaign_id, "actor_id": value.actor_id,
                           "controller_id": value.controller_id}
    if type(value) is Delegation:
        fields = {key: getattr(value, key) for key in _DELEGATION}
        fields["domains"] = sorted(value.domains)
        for key, item in fields.items():
            if key != "domains":
                check_size(item, key, 512)
        return "delegation", fields
    if type(value) is Revocation:
        return "revocation", {"campaign_id": value.campaign_id, "delegation_id": value.delegation_id,
                              "revoked_by": value.revoked_by}
    if type(value) is Correction:
        check_size(value.note, "note", MAX_NOTE)
        check_size(value.claim_id, "claim_id", 512)
        return "correction", {"campaign_id": value.campaign_id, "claim_id": value.claim_id,
                              "corrected_by": value.corrected_by, "note": value.note}
    raise ValueError(f"not a sovereignty value: {type(value).__name__}")


def decode_sovereignty(kind: str, data: Mapping[str, object]) -> object:
    if kind == "control":
        return _build(ControlChange, _text(data, "campaign_id"), _text(data, "actor_id"),
                      _text(data, "controller_id", optional=True))
    if kind == "delegation":
        domains = data["domains"]
        if not isinstance(domains, tuple) or not all(type(d) is str for d in domains):
            raise LedgerInvalid("domains must be a list of names")
        return _build(Delegation, _text(data, "campaign_id"), _text(data, "delegation_id"),
                      _text(data, "actor_id"), _text(data, "granted_by"), frozenset(domains),
                      _text(data, "scope"), _text(data, "mode"), _text(data, "scene_id", optional=True),
                      _text(data, "turn_id", optional=True), _text(data, "contribution_id", optional=True))
    if kind == "revocation":
        return _build(Revocation, _text(data, "campaign_id"), _text(data, "delegation_id"),
                      _text(data, "revoked_by"))
    if kind == "correction":
        return _build(Correction, _text(data, "campaign_id"), _text(data, "claim_id"),
                      _text(data, "corrected_by"), _text(data, "note"))
    raise LedgerInvalid(f"unknown sovereignty entry kind {kind!r}")


SOVEREIGNTY_KEYS = {
    "control": {"campaign_id", "actor_id", "controller_id"},
    "delegation": set(_DELEGATION),
    "revocation": {"campaign_id", "delegation_id", "revoked_by"},
    "correction": {"campaign_id", "claim_id", "corrected_by", "note"},
}


def exact(entry: Mapping[str, object], kind: str, keys: set[str]) -> Mapping[str, object]:
    return _exact(entry, kind, keys)
