"""Assembled-prompt cache for the orchestrator's ``regenerate_last`` path.

Spec context-builder-remaining §11: "cache the assembled prompt on
regenerate." The :class:`ContextBuilderService` does not own this cache —
the orchestrator does — so that invalidation lives next to the
regenerate logic. This module just provides the data structure and a
deterministic key function.

The key includes everything that can vary the resolved prompt at the
orchestrator boundary: ``campaign_id``, ``scene_id``, ``pc_ref``, and
the player input. The composition hash is mixed in by the orchestrator
when it knows the composition; tests can pass it as
``composition_hash=""`` to opt out.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from grimoire.types.context import AssembledPrompt


def make_cache_key(
    *,
    campaign_id: str,
    player_input: str,
    composition_hash: str,
    scene_id: str | None,
    pc_ref: str | None,
) -> str:
    """Deterministic SHA-256 over the regenerate-stable inputs."""
    h = hashlib.sha256()
    for part in (
        campaign_id,
        player_input,
        composition_hash or "",
        scene_id or "",
        pc_ref or "",
    ):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


@dataclass
class ContextBuilderCache:
    """Tiny in-memory ``key -> AssembledPrompt`` store.

    Bounded by ``max_entries``; the oldest entries are dropped when the
    cap is reached. The eviction order is insertion order — adequate for
    a regenerate cache that only ever holds a handful of entries.
    """

    max_entries: int = 32
    _store: dict[str, AssembledPrompt] = field(default_factory=dict)

    def get(self, key: str) -> AssembledPrompt | None:
        return self._store.get(key)

    def put(self, key: str, prompt: AssembledPrompt) -> None:
        if key in self._store:
            # Refresh order
            del self._store[key]
        self._store[key] = prompt
        while len(self._store) > max(1, self.max_entries):
            oldest = next(iter(self._store))
            del self._store[oldest]

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._store)


__all__ = ["ContextBuilderCache", "make_cache_key"]
