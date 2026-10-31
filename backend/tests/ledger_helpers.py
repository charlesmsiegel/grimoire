"""Synthetic fixtures for durable-authority tests: one owner, one importer grant."""

from __future__ import annotations

import itertools

from grimoire.core.identity import InstanceRef
from grimoire.ledgers import DurableAuthority
from grimoire.security import Approval, Grant, Permission, ResourcePath, payload_digest
from grimoire.store import RecordEnvelope

OWNER = "human:owner"
IMPORTER = "app:importer"
RECORD = RecordEnvelope(InstanceRef("c1", "character", "x"), {"n": 1})
ADDRESS = "campaigns/c1/instances/character/x.json"


def fixed_clock():
    counter = itertools.count()
    return lambda: f"2026-01-01T00:00:{next(counter) % 60:02d}+00:00"


def open_authority(store) -> DurableAuthority:
    return DurableAuthority.open(store, [OWNER], clock=fixed_clock())


def grant_importer(authority, permissions=(Permission.APPLY, Permission.INSPECT), scope="campaigns", gid="g1"):
    authority.add_grant(Grant(gid, IMPORTER, frozenset(permissions), ResourcePath.parse(scope), OWNER))


def record_bytes(store) -> bytes:
    txn = store.transaction()
    txn.put(RECORD, None)
    return txn.preview()[ADDRESS]


def approve(authority, data: bytes, aid="a1", address=ADDRESS, permission=Permission.APPLY):
    authority.register_approval(Approval(aid, IMPORTER, permission, ResourcePath.parse(address),
                                         payload_digest(data), OWNER))
