"""V2 tool authority kernel: least-privilege grants for untrusted workers.

Pure and in-memory: no filesystem, network, provider or UI dependencies.
See docs/TOOL_AUTHORITY.md for the contract and threat model.
"""

from .authorizer import AuditRecord, Authorizer, Decision, WorkerHandle
from .policy import (
    Approval, Grant, Permission, Reason, is_human, is_valid_principal, payload_digest,
)
from .resource import RESERVED_ROOTS, InvalidResourcePath, ResourcePath

__all__ = [
    "Approval", "AuditRecord", "Authorizer", "Decision", "Grant", "InvalidResourcePath",
    "Permission", "RESERVED_ROOTS", "Reason", "ResourcePath", "WorkerHandle",
    "is_human", "is_valid_principal", "payload_digest",
]
