"""Permissions, principals, grants and approvals.

Authority is only ever created by these values, which trusted code builds on
behalf of a human. Imported content and model output never construct them.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from .resource import ResourcePath

_PRINCIPAL = re.compile(r"[a-z0-9][a-z0-9:_.-]{0,63}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class Permission(StrEnum):
    """Distinct capabilities; none implies another."""

    INSPECT = "inspect"
    PROPOSE = "propose"
    APPLY = "apply"
    EXECUTE = "execute"


class Reason(StrEnum):
    """Stable reason codes for every authorization decision."""

    GRANTED = "granted"
    APPROVED = "approved"
    MALFORMED_REQUEST = "malformed_request"
    INVALID_PATH = "invalid_path"
    RESERVED_RESOURCE = "reserved_resource"
    NOT_GRANTED = "not_granted"
    APPROVAL_REQUIRED = "approval_required"
    INTERNAL_ERROR = "internal_error"


_ORDER = tuple(Permission)
CONSEQUENTIAL = frozenset({Permission.APPLY, Permission.EXECUTE})


def is_valid_principal(value: object) -> bool:
    return (type(value) is str and _PRINCIPAL.fullmatch(value) is not None
            and not value.endswith(":"))


def is_human(principal: str) -> bool:
    return is_valid_principal(principal) and principal.startswith("human:")


def payload_digest(payload: bytes) -> str:
    if type(payload) is not bytes:
        raise TypeError("payload must be bytes")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _require_principal(value: object, label: str) -> None:
    if not is_valid_principal(value):
        raise ValueError(f"{label} is not a valid principal id")


def _require_id(value: object, label: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")


def _require_address(value: object, label: str) -> None:
    if not isinstance(value, ResourcePath):
        raise ValueError(f"{label} must be a ResourcePath")
    if value.reserved:
        raise ValueError(f"{label} is a reserved resource")


def _flag(path: ResourcePath) -> str:
    return "" if path.ascii else " [non-ASCII address]"


@dataclass(frozen=True, slots=True)
class Grant:
    """A human delegates some permissions over one address subtree."""

    grant_id: str
    principal: str
    permissions: frozenset[Permission]
    scope: ResourcePath
    granted_by: str

    def __post_init__(self) -> None:
        _require_id(self.grant_id, "grant_id")
        _require_principal(self.principal, "principal")
        if (not isinstance(self.permissions, frozenset) or not self.permissions
                or not all(type(p) is Permission for p in self.permissions)):
            raise ValueError("permissions must be a nonempty frozenset of Permission")
        _require_address(self.scope, "scope")
        if not is_human(self.granted_by):
            raise ValueError("grants must be issued by a human principal")

    def covers(self, principal: str, permission: Permission, resource: ResourcePath) -> bool:
        return (principal == self.principal and permission in self.permissions
                and resource.is_within(self.scope))

    def describe(self) -> str:
        permissions = ", ".join(p.value for p in _ORDER if p in self.permissions)
        return (f"{self.principal} may {permissions} in {self.scope} "
                f"(granted by {self.granted_by}){_flag(self.scope)}")


@dataclass(frozen=True, slots=True)
class Approval:
    """A human's one-time consent to one exact consequential request."""

    approval_id: str
    principal: str
    permission: Permission
    resource: ResourcePath
    payload_digest: str
    approved_by: str

    def __post_init__(self) -> None:
        _require_id(self.approval_id, "approval_id")
        _require_principal(self.principal, "principal")
        if type(self.permission) is not Permission or self.permission not in CONSEQUENTIAL:
            raise ValueError("only apply or execute requests need approval")
        _require_address(self.resource, "resource")
        if type(self.payload_digest) is not str or not _DIGEST.fullmatch(self.payload_digest):
            raise ValueError("payload_digest must be 'sha256:' and 64 lowercase hex digits")
        if not is_human(self.approved_by):
            raise ValueError("approvals must be issued by a human principal")

    def describe(self) -> str:
        return (f"{self.approved_by} approved {self.principal} to {self.permission.value} "
                f"{self.payload_digest[:19]}… at {self.resource}{_flag(self.resource)}")
