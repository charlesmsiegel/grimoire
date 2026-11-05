"""Pairing records and peer principals (028): who may connect, and what its writes may touch.

A pairing is a 32-byte key both devices hold, recorded under the reserved ``secrets`` root
(``secrets/peers/<device>.json``) and never transferred. Pairing also grants the peer's
principal, ``app:peer:<device>``, ``APPLY`` over the synchronized roots of this library. That
principal is never an owner and can issue no grant or approval (both need a human); this
library's own sync engine applies the peer's verified writes under it, so the owner sees them in
the audit and can end them by revoking its grants (``unpair``). See docs/DEVICE_SYNC.md.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field

from grimoire.security import Grant, Permission, ResourcePath, is_human, is_valid_principal
from grimoire.store import LibraryStore, etag
from grimoire.store.jsonfmt import JsonError, parse_strict, render
from grimoire.sync import device_of
from grimoire.sync.documents import check_device_id

from .errors import PairingError

PAIRING_FORMAT = "grimoire.peersync.pairing/1"
KEY_BYTES = 32
PRINCIPAL_PREFIX = "app:peer:"
#: What a paired peer's writes may reach on this library: 019's synchronized records, kept
#: versions and blobs. Never a reserved root (secrets, ledgers).
PEER_SCOPES = ("sources", "campaigns", "kept", "assets")


@dataclass(frozen=True)
class Pairing:
    """An active pairing: the peer's device id, the shared key and the grants pairing issued."""

    peer: str
    key: bytes = field(repr=False)
    grants: tuple[str, ...] = ()


def new_pairing_key() -> bytes:
    """A fresh random pairing key, to be shown on one device and entered on the other."""
    return secrets.token_bytes(KEY_BYTES)


def peer_principal(device: str) -> str:
    """The principal a paired device's writes are authorized under on this library."""
    check_device_id(device)
    principal = PRINCIPAL_PREFIX + device
    if not is_valid_principal(principal):
        raise PairingError(f"device id {device!r} is too long to name a peer principal")
    return principal


def _secret_name(peer: str) -> str:
    return f"peers/{peer}.json"


def _grant_ids(peer: str, key: bytes) -> tuple[str, ...]:
    """One grant per scope, named for this key: a later pairing with a new key never reuses an id."""
    digest = hashlib.sha256(b"grimoire.peersync.grant|" + key).hexdigest()[:16]
    return tuple(f"peer:{peer}:{scope}:{digest}" for scope in PEER_SCOPES)


def _parse(peer: str, data: bytes | None) -> Pairing | None:
    if data is None:
        return None
    try:
        document = parse_strict(data)
    except JsonError:
        return None
    if not isinstance(document, Mapping) or document.get("format") != PAIRING_FORMAT or document.get("peer") != peer:
        return None
    if "revoked" in document:  # an ended pairing
        return None
    key, grants = document.get("key"), document.get("grants")
    if set(document) != {"format", "peer", "key", "grants"} or type(key) is not str or not isinstance(grants, (list, tuple)):
        return None
    try:
        raw = bytes.fromhex(key)
    except ValueError:
        return None
    if len(raw) != KEY_BYTES or key != raw.hex() or not all(type(grant) is str for grant in grants):
        return None
    return Pairing(peer, raw, tuple(grants))


def read_pairing(store: LibraryStore, peer: str) -> Pairing | None:
    """This library's active pairing with ``peer``; ``None`` if there is none, it was ended, or it is damaged."""
    check_device_id(peer)
    return _parse(peer, store.read_secret(_secret_name(peer)))


def pair(store: LibraryStore, authority, peer: str, key: bytes, *, granted_by: str) -> Pairing:  # type: ignore[no-untyped-def]
    """Record a pairing with ``peer`` and grant its principal the synchronized roots.

    ``granted_by`` is the human owner who paired the devices (a direct human action; the key was
    shown on one device and entered on the other). The grants are issued first and the key is
    written after them, so an interrupted pairing leaves at most grants that no connection can
    use (no key authenticates it). Pairing again replaces the key and ends the old grants.
    """
    if type(key) is not bytes or len(key) != KEY_BYTES:
        raise PairingError(f"a pairing key is exactly {KEY_BYTES} bytes")
    if not is_human(granted_by):
        raise PairingError("a pairing is granted by a human owner")
    try:
        check_device_id(peer)
    except ValueError as error:
        raise PairingError(str(error)) from None
    if peer == device_of(store):
        raise PairingError("a library cannot pair with its own device id")
    principal = peer_principal(peer)
    name = _secret_name(peer)
    old = store.read_secret(name)
    current = _parse(peer, old)
    ids = _grant_ids(peer, key)
    active = {grant.grant_id for grant in authority.active_grants()}
    if current is not None and hmac.compare_digest(current.key, key) and set(ids) <= active:
        return current  # already paired with this very key
    for grant_id, scope in zip(ids, PEER_SCOPES, strict=True):
        if grant_id not in active:
            try:
                authority.add_grant(Grant(grant_id, principal, frozenset({Permission.APPLY}),
                                          ResourcePath.parse(scope), granted_by))
            except ValueError as error:  # an id this ledger already used (the same key, after unpair)
                raise PairingError(f"this key was already used to pair {peer}; pair with a new key ({error})") from None
    txn = store.transaction()
    txn.put_secret(name, render({"format": PAIRING_FORMAT, "peer": peer, "key": key.hex(), "grants": list(ids)}),
                   None if old is None else etag(old))
    txn.commit(None)
    if current is not None:
        _revoke(authority, set(current.grants) - set(ids))
    return Pairing(peer, key, ids)


def unpair(store: LibraryStore, authority, peer: str) -> None:  # type: ignore[no-untyped-def]
    """End the pairing with ``peer``: its grants are revoked first, then its key is erased."""
    check_device_id(peer)
    name = _secret_name(peer)
    old = store.read_secret(name)
    current = _parse(peer, old)
    principal = peer_principal(peer)
    _revoke(authority, {grant.grant_id for grant in authority.active_grants() if grant.principal == principal}
            | (set(current.grants) if current is not None else set()))
    if old is None:
        return
    txn = store.transaction()
    txn.put_secret(name, render({"format": PAIRING_FORMAT, "peer": peer, "revoked": True}), etag(old))
    txn.commit(None)


def _revoke(authority, grant_ids: set[str]) -> None:  # type: ignore[no-untyped-def]
    active = {grant.grant_id for grant in authority.active_grants()}
    for grant_id in sorted(grant_ids & active):
        authority.revoke_grant(grant_id)
