"""Deny-by-default authorization for tools and workers, with an audit trail.

Trusted application code owns an ``Authorizer``. Each worker receives only a
``WorkerHandle`` whose principal and origin were fixed when trusted code made
it, so content a worker reads cannot choose its identity, claim trust, add
grants or register approvals. This is an API boundary checked by tests and
review, not an in-process sandbox against arbitrary imported code.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .policy import (
    CONSEQUENTIAL, Approval, Grant, Permission, Reason, is_human, is_valid_principal,
    payload_digest,
)
from .resource import InvalidResourcePath, ResourcePath


@dataclass(frozen=True, slots=True)
class Decision:
    """The outcome of one request; executors act only on ``resource``."""

    allowed: bool
    reason: Reason
    resource: ResourcePath | None = None
    grant_id: str | None = None
    approval_id: str | None = None


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One decision, without payload content or caller-chosen approval IDs."""

    sequence: int
    principal: str
    permission: Permission | None
    resource: str | None
    trusted: bool
    allowed: bool
    reason: Reason
    grant_id: str | None
    approval_id: str | None


def _deny(reason: Reason, resource: ResourcePath | None = None) -> Decision:
    return Decision(False, reason, resource)


def _well_formed(permission: object, resource: object, payload: object,
                 approval_id: object) -> bool:
    if not isinstance(permission, Permission) or not isinstance(resource, str):
        return False
    if payload is not None and not isinstance(payload, bytes):
        return False
    if approval_id is not None and not isinstance(approval_id, str):
        return False
    # Inspection carries no payload; every other permission acts on exact bytes.
    return (payload is None) == (permission is Permission.INSPECT)


class Authorizer:
    """Holds grants, approvals and the audit log; never handed to workers."""

    def __init__(self, owners: Iterable[str]) -> None:
        self._owners = frozenset(owners)
        if not self._owners or not all(is_human(owner) for owner in self._owners):
            raise ValueError("owners must be a nonempty set of human principals")
        self._grants: dict[str, Grant] = {}
        self._revoked_grants: set[str] = set()
        self._approvals: dict[str, tuple[Approval, str]] = {}
        self._closed_approvals: set[str] = set()
        self._audit: list[AuditRecord] = []

    def add_grant(self, grant: Grant) -> None:
        if not isinstance(grant, Grant):
            raise TypeError("expected a Grant")
        if grant.grant_id in self._grants:
            raise ValueError("grant ids are never reused")
        if grant.granted_by not in self._owners:
            raise ValueError("only an owner may issue grants")
        self._grants[grant.grant_id] = grant

    def revoke_grant(self, grant_id: str) -> None:
        if grant_id not in self._grants:
            raise KeyError(grant_id)
        self._revoked_grants.add(grant_id)
        for approval_id, (_, pinned) in self._approvals.items():
            if pinned == grant_id:
                self._closed_approvals.add(approval_id)

    def register_approval(self, approval: Approval) -> None:
        if not isinstance(approval, Approval):
            raise TypeError("expected an Approval")
        if approval.approval_id in self._approvals:
            raise ValueError("approval ids are never reused")
        pinned = next((
            grant for grant in self.active_grants()
            if grant.granted_by == approval.approved_by
            and grant.covers(approval.principal, approval.permission, approval.resource)
        ), None)
        if pinned is None:
            raise ValueError("an approval must come from the issuer of a covering grant")
        self._approvals[approval.approval_id] = (approval, pinned.grant_id)

    def revoke_approval(self, approval_id: str) -> None:
        if approval_id not in self._approvals:
            raise KeyError(approval_id)
        self._closed_approvals.add(approval_id)

    def active_grants(self) -> tuple[Grant, ...]:
        return tuple(grant for grant_id, grant in self._grants.items()
                     if grant_id not in self._revoked_grants)

    def pending_approvals(self) -> tuple[Approval, ...]:
        return tuple(approval for approval_id, (approval, _) in self._approvals.items()
                     if approval_id not in self._closed_approvals)

    def describe_policy(self) -> tuple[str, ...]:
        return tuple(item.describe() for item in (*self.active_grants(), *self.pending_approvals()))

    def audit(self) -> tuple[AuditRecord, ...]:
        return tuple(self._audit)

    def handle_for(self, principal: str, *, trusted: bool = False) -> WorkerHandle:
        if not is_valid_principal(principal):
            raise ValueError("not a valid principal id")
        if trusted is not False and not (
                trusted is True and (is_human(principal) or principal.startswith("app:"))):
            raise ValueError("only human: or app: principals may hold a trusted handle")
        return WorkerHandle(self, principal, trusted)

    def _evaluate(self, principal: str, trusted: bool, permission: object,
                  resource: object, payload: object, approval_id: object) -> Decision:
        try:
            decision = self._decide(principal, trusted, permission, resource, payload, approval_id)
        except Exception:
            decision = _deny(Reason.INTERNAL_ERROR)
        try:
            self._audit.append(AuditRecord(
                sequence=len(self._audit) + 1,
                principal=principal,
                permission=permission if isinstance(permission, Permission) else None,
                resource=None if decision.resource is None else str(decision.resource),
                trusted=trusted,
                allowed=decision.allowed,
                reason=decision.reason,
                grant_id=decision.grant_id,
                approval_id=decision.approval_id,
            ))
        except Exception:
            pass  # a failed audit write never turns a decision into an exception
        return decision

    def _decide(self, principal: str, trusted: bool, permission: object,
                resource: object, payload: object, approval_id: object) -> Decision:
        if not _well_formed(permission, resource, payload, approval_id):
            return _deny(Reason.MALFORMED_REQUEST)
        try:
            path = ResourcePath.parse(resource)
        except InvalidResourcePath:
            return _deny(Reason.INVALID_PATH)
        if path.reserved:
            return _deny(Reason.RESERVED_RESOURCE, path)
        grant = next((grant for grant in self.active_grants()
                      if grant.covers(principal, permission, path)), None)
        if grant is None:
            return _deny(Reason.NOT_GRANTED, path)
        if trusted or permission not in CONSEQUENTIAL:
            return Decision(True, Reason.GRANTED, path, grant.grant_id)
        entry = self._approvals.get(approval_id) if approval_id is not None else None
        if entry is None or approval_id in self._closed_approvals:  # revoking a grant closes these
            return _deny(Reason.APPROVAL_REQUIRED, path)
        approval, pinned = entry
        if (approval.principal != principal
                or approval.permission is not permission
                or approval.resource != path
                or approval.payload_digest != payload_digest(payload)):
            return _deny(Reason.APPROVAL_REQUIRED, path)
        self._closed_approvals.add(approval.approval_id)  # consumed last, only on allow
        return Decision(True, Reason.APPROVED, path, pinned, approval.approval_id)


class WorkerHandle:
    """A worker's only door: fixed principal and origin, decisions only."""

    __slots__ = ("_authority", "_principal", "_trusted")

    def __init__(self, authority: Authorizer, principal: str, trusted: bool) -> None:
        self._authority = authority
        self._principal = principal
        self._trusted = trusted

    @property
    def principal(self) -> str:
        return self._principal

    @property
    def trusted(self) -> bool:
        return self._trusted

    def authorize(self, permission, resource, payload=None, approval_id=None) -> Decision:
        """Decide one request; never raises and never allows on error."""
        return self._authority._evaluate(
            self._principal, self._trusted, permission, resource, payload, approval_id)
