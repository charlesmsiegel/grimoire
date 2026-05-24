"""In-process caches for character views and active-PC state."""

from __future__ import annotations

from collections import OrderedDict

from grimoire.types.common import CampaignId, CharacterRef


class CharacterViewCache:
    """LRU caches for rendered character views and per-campaign active PC.

    Collaborator of CharactersService (spec 2026-05-17 §5).
    """

    def __init__(self, *, max_view_entries: int = 256, max_active_pc: int = 256) -> None:
        self._view_cache: OrderedDict[tuple[str, str, str, int | None], str] = OrderedDict()
        self._max_view_entries = max_view_entries
        self._active_pc: OrderedDict[str, CharacterRef] = OrderedDict()
        self._max_active_pc = max_active_pc

    # ------------------------------------------------------------------
    # View cache
    # ------------------------------------------------------------------

    def view_get(
        self, ref: CharacterRef, campaign_id: CampaignId, view: str, seed: int | None
    ) -> str | None:
        key = (ref, campaign_id, view, seed)
        try:
            value = self._view_cache[key]
        except KeyError:
            return None
        self._view_cache.move_to_end(key)
        return value

    def view_set(
        self,
        ref: CharacterRef,
        campaign_id: CampaignId,
        view: str,
        seed: int | None,
        value: str,
    ) -> None:
        key = (ref, campaign_id, view, seed)
        self._view_cache[key] = value
        self._view_cache.move_to_end(key)
        while len(self._view_cache) > self._max_view_entries:
            self._view_cache.popitem(last=False)

    def view_invalidate(
        self, ref: CharacterRef | None = None, campaign_id: CampaignId | None = None
    ) -> None:
        if ref is None and campaign_id is None:
            self._view_cache.clear()
            return
        doomed = [
            key
            for key in self._view_cache
            if (ref is None or key[0] == ref) and (campaign_id is None or key[1] == campaign_id)
        ]
        for key in doomed:
            del self._view_cache[key]

    # ------------------------------------------------------------------
    # Active-PC cache
    # ------------------------------------------------------------------

    def seed_active_pc_from_rows(
        self, campaign_id: CampaignId, rows: list[dict]
    ) -> CharacterRef | None:
        cached = self._active_pc.get(campaign_id)
        if cached is not None:
            self._active_pc.move_to_end(campaign_id)
            return cached
        if not rows:
            return None
        chosen = next(
            (row["character_ref"] for row in rows if bool(row["active"])),
            rows[0]["character_ref"],
        )
        self.cache_active_pc(campaign_id, chosen)
        return chosen

    def cache_active_pc(self, campaign_id: str, character_ref: CharacterRef) -> None:
        self._active_pc[campaign_id] = character_ref
        self._active_pc.move_to_end(campaign_id)
        while len(self._active_pc) > self._max_active_pc:
            self._active_pc.popitem(last=False)

    def get_active_pc(self, campaign_id: CampaignId) -> CharacterRef | None:
        return self._active_pc.get(campaign_id)

    def pop_active_pc(self, campaign_id: CampaignId) -> None:
        self._active_pc.pop(campaign_id, None)
