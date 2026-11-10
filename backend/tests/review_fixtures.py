"""Synthetic libraries for Sprint 110's review API: a secured V2 library with its durable authority, as the
app opens it, plus the 037, 026, 036, 042 and 043 fixtures. Every name and text is invented."""

from __future__ import annotations

import base64
from pathlib import Path

from ledger_helpers import fixed_clock
from scene_fixtures import secure
from store_helpers import OWNER

from grimoire.ledgers import DurableAuthority
from grimoire.reviewapi import ReviewApi
from grimoire.store import LibraryStore

CLOCK = "2026-11-10T00:00:00Z"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class Ids:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}-{self.n}"


class Desk:
    """One secured library, open with its durable authority, and the review API over it."""

    def __init__(self, folder: Path, *, home: Path | None = None) -> None:
        self.folder = folder
        self.store = LibraryStore.open(folder)
        self.authority = DurableAuthority.open(self.store, [OWNER], clock=fixed_clock())
        self.api = ReviewApi(self.store, self.authority, owner=OWNER, clock=lambda: CLOCK, new_id=Ids(),
                             environ={}, home=home)

    def __call__(self, op: str, **args):
        return self.api.call(op, args)

    def close(self) -> None:
        self.authority.close()
        self.store.close()

    def __enter__(self) -> Desk:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def secured(folder: Path, seed=None) -> Path:
    """A new library, ``seed(store)`` written with the plain owner handle first, then secured."""
    store = LibraryStore.create(folder)
    try:
        if seed is not None:
            seed(store)
    finally:
        store.close()
    secure(folder)
    return folder
