"""Durable authority records on the native store (filesystem adapter).

``DurableAuthority`` persists grants, approvals and the decision audit;
``SovereigntyLedger`` persists player control history, delegations, revocations
and corrections. Both write only the reserved ``authority/`` ledgers. See
docs/TOOL_AUTHORITY.md and docs/PLAYER_SOVEREIGNTY.md.
"""

from grimoire.store.errors import AuthorityUnavailable, LedgerBroken, LedgerInvalid

from .codec import ControlChange
from .security import DurableAuthority
from .sovereignty import ControlEvent, SovereigntyLedger, SovereigntyState

__all__ = [
    "AuthorityUnavailable", "ControlChange", "ControlEvent", "DurableAuthority", "LedgerBroken",
    "LedgerInvalid", "SovereigntyLedger", "SovereigntyState",
]
