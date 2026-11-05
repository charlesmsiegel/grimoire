"""V2 app-managed device sync (028): pairing and an authenticated peer transfer over 019's core.

An adapter over ``grimoire.sync`` and ``grimoire.store``. Two paired devices synchronize over one
authenticated connection: the initiator rebuilds the peer's library as a checked local stand-in,
runs 019's plan and apply against it unchanged, and replays each of the stand-in's steps on the
peer in 019's own order. See docs/DEVICE_SYNC.md for the contract.
"""

from .errors import (
    AuthenticationFailed, MirrorMismatch, NotPaired, PairingError, PeerRefused, PeerSyncError, TransportError,
)
from .pairing import PEER_SCOPES, Pairing, new_pairing_key, pair, peer_principal, read_pairing, unpair
from .server import PeerServer
from .session import TransferReport, transfer
from .snapshot import Limits

__all__ = [
    "AuthenticationFailed", "Limits", "MirrorMismatch", "NotPaired", "PEER_SCOPES", "Pairing", "PairingError",
    "PeerRefused", "PeerServer", "PeerSyncError", "TransferReport", "TransportError", "new_pairing_key", "pair",
    "peer_principal", "read_pairing", "transfer", "unpair",
]
