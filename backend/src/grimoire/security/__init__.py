"""V2 tool authority kernel: least-privilege grants for untrusted workers.

Pure and in-memory: no filesystem, network, provider or UI dependencies.
See docs/TOOL_AUTHORITY.md for the contract and threat model.
"""

from .resource import RESERVED_ROOTS, InvalidResourcePath, ResourcePath

__all__ = ["RESERVED_ROOTS", "InvalidResourcePath", "ResourcePath"]
