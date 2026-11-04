"""Synthetic holders, items and helpers for Sprint 023 counted-holdings tests (no real library)."""

from __future__ import annotations

import json

from grimoire.core.identity import InstanceRef
from grimoire.holdings import Holdings, LogEntry, holdings_to_payload

MARA = InstanceRef("c1", "character", "mara-1")
TOMA = InstanceRef("c1", "character", "toma-1")
CHEST = InstanceRef("c1", "item", "chest-1")
OTHER = InstanceRef("c2", "character", "ivo-1")
ORDINARY, STRONG, GREAT = "ordinary-ball", "strong-ball", "great-ball"
AT = "2026-11-01T10:00:00Z"


class ZeroSizer:
    """Size is never a concern."""

    def document(self, holdings: Holdings) -> int:
        return 0

    def entry(self, holdings: Holdings, entry: LogEntry) -> int:
        return 0


class JsonSizer:
    """The payload rendered the way the store renders documents; close to, not exactly, the file size."""

    def document(self, holdings: Holdings) -> int:
        return len(json.dumps(holdings_to_payload(holdings), indent=2, sort_keys=True,
                              ensure_ascii=False).encode())

    def entry(self, holdings: Holdings, entry: LogEntry) -> int:
        from grimoire.holdings import entry_to_payload
        text = json.dumps(entry_to_payload(holdings.holder, entry), indent=2, sort_keys=True, ensure_ascii=False)
        return len(text.replace("\n", "\n" + " " * 4).encode()) + 6


def everyone(_ref: InstanceRef) -> bool:
    return True


# -- a store-backed library ---------------------------------------------------------------------

def seed_holders(store, holders=(MARA, TOMA, CHEST)) -> None:
    """Holder instance files only: holdings need an existing instance, not a definition."""
    from grimoire.store import RecordEnvelope
    from store_helpers import owner_handle
    txn = store.transaction()
    for holder in holders:
        txn.put(RecordEnvelope(holder, {"note": "synthetic holder"}), None)
    txn.commit(owner_handle())


class Clock:
    """Deterministic UTC timestamps, one second apart."""

    def __init__(self) -> None:
        self.ticks = 0

    def __call__(self) -> str:
        self.ticks += 1
        minutes, seconds = divmod(self.ticks, 60)
        hours, minutes = divmod(minutes, 60)
        return f"2026-11-01T{10 + hours:02d}:{minutes:02d}:{seconds:02d}Z"  # real times for 14 hours of ticks


def holdings_library(store, handle=None, owner="human:owner"):
    from grimoire.holdingstore import HoldingsLibrary
    from store_helpers import owner_handle
    return HoldingsLibrary(store, handle or owner_handle(), clock=Clock(), owner=owner)
