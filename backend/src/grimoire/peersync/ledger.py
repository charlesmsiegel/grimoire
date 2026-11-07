"""``resolve_ledger`` over the authenticated peer transport (028): one campaign's ledger review, decided.

The initiator rebuilds the peer's library as a checked stand-in (as ``transfer`` does), plans the
decision with 019's ``plan_resolution`` against it, and replays each step on the peer in 019's order. The
peer receives ordinary sync steps: merged ledger files under its grants to this device, and its own
bookkeeping. It never receives a discard. ``keep`` may be ``MERGE`` (nothing is discarded) or the
peer's device id (a ``str``, ``"merge"`` included), which discards this device's own changes to that ledger: only an owner of this library
(``decided_by``, one of its ``DurableAuthority.owners``) may decide that. Keeping this device's ledger
would discard the peer's changes, so it is decided on the peer.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from grimoire.store import LibraryStore
from grimoire.sync import MERGE, LedgerMerge, ResolveRefused, SyncReport, apply_sync, device_of
from grimoire.sync.ledger_resolve import plan_resolution

from .replay import send_step
from .session import _check_paired, dial, hang_up, pairing_for
from .snapshot import Limits, receive_mirror
from .wire import client_handshake


def resolve_ledger_with(store: LibraryStore, authority, peer: str, address: tuple[str, int],  # type: ignore[no-untyped-def]
                        campaign: str, keep: str | LedgerMerge, token: str, *, decided_by: str, timeout: float = 600.0,
                        workdir: str | Path | None = None, limits: Limits = Limits()) -> SyncReport:
    """Decide campaign ``campaign``'s ledger review with the paired device ``peer`` (a loopback address).

    Raises ``ResolveRefused`` (with why) when the decision cannot be taken, ``TransportError`` when the
    connection fails; each step that landed did so whole, and the next sync finds what is left.
    """
    local = device_of(store)
    if keep == local:
        raise ResolveRefused(f"keeping {local}'s ledger discards {peer}'s changes; decide it on {peer}")
    if keep is not MERGE and decided_by not in authority.owners:
        raise ResolveRefused(f"keeping {peer}'s ledger discards this device's changes; only an owner may decide that")
    pairing, local_handle = pairing_for(store, authority, peer, address)
    connection = dial(address, timeout)
    channel = None
    try:
        channel = client_handshake(connection, local, peer, pairing.key)
        _check_paired(store, peer, pairing.key)
        channel.send({"t": "snapshot"})
        with tempfile.TemporaryDirectory(prefix="grimoire-peer-", dir=workdir, ignore_cleanup_errors=True) as scratch:
            mirror = receive_mirror(channel, Path(scratch) / "peer", local, limits)
            try:
                handles = {local: local_handle, peer: mirror.handle}
                plan = plan_resolution(store, mirror.store, campaign, keep, token, handles,
                                       owners={local: authority.owners}, decided_by=decided_by)

                def replay(step) -> None:  # type: ignore[no-untyped-def]
                    if step.device == peer:
                        _check_paired(store, peer, pairing.key)
                        send_step(channel, step, mirror.store.read_blob_exact)
                return apply_sync(plan, handles, observer=replay)
            finally:
                mirror.store.close()
    finally:
        hang_up(channel, connection)


__all__ = ["resolve_ledger_with"]
