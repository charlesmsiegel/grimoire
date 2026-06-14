"""In-memory TTL cache for the two-phase character-card import flow.

``preview`` parses a card and stashes the resulting
:class:`~grimoire.types.characters.IngestedCharacterCard` here so ``commit``
can finalise it without re-parsing. The cache is process-local; the key
returned to the client is opaque.

Each slot expires after :attr:`ImportPreviewCache.ttl_seconds` and the cache
is hard-capped at ``max_entries`` slots, so a burst of previewed-but-never-
committed cards can't leak memory: expired slots are swept and, if the cap is
still reached, the soonest-to-expire slots are evicted before a new one is
inserted.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from grimoire.types.characters import IngestedCharacterCard
from grimoire.util import new_id

PREVIEW_TTL_SECONDS = 15 * 60

# Hard cap on concurrently cached previews. Combined with the TTL sweep this
# bounds the cache regardless of client behaviour: a single local user is never
# realistically mid-preview on hundreds of cards at once, so the cap only ever
# bites under abuse, where it degrades to evicting the oldest in-flight preview.
MAX_PREVIEW_ENTRIES = 256


@dataclass(frozen=True)
class PreviewSlot:
    """A cached, parsed card awaiting commit."""

    ingested: IngestedCharacterCard
    world_id: str
    filename: str
    expires_at: float


class ImportPreviewCache:
    """Bounded, TTL-swept store of previewed character-card ingests."""

    def __init__(
        self,
        *,
        ttl_seconds: float = PREVIEW_TTL_SECONDS,
        max_entries: int = MAX_PREVIEW_ENTRIES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._slots: dict[str, PreviewSlot] = {}
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    def __len__(self) -> int:
        return len(self._slots)

    def _gc_expired(self) -> None:
        now = self._clock()
        expired = [k for k, v in self._slots.items() if v.expires_at <= now]
        for key in expired:
            self._slots.pop(key, None)

    def put(self, ingested: IngestedCharacterCard, *, world_id: str, filename: str) -> str:
        """Cache an ingest and return its opaque preview id.

        Sweeps expired slots first; if the cache is still at ``max_entries``,
        evicts the soonest-to-expire slots until there is room, so the cache
        never exceeds the cap.
        """
        self._gc_expired()
        while len(self._slots) >= self._max_entries:
            oldest = min(self._slots, key=lambda k: self._slots[k].expires_at)
            self._slots.pop(oldest, None)
        preview_id = new_id("preview")
        self._slots[preview_id] = PreviewSlot(
            ingested=ingested,
            world_id=world_id,
            filename=filename,
            expires_at=self._clock() + self._ttl_seconds,
        )
        return preview_id

    def take(self, preview_id: str) -> PreviewSlot | None:
        """Pop a slot for commit (single-use), sweeping expired slots first."""
        self._gc_expired()
        return self._slots.pop(preview_id, None)


__all__ = [
    "MAX_PREVIEW_ENTRIES",
    "PREVIEW_TTL_SECONDS",
    "ImportPreviewCache",
    "PreviewSlot",
]
