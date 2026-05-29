"""Resolve natural-language item strings to stable item refs.

Order: fungible keyword -> existing item -> auto-created emergent item.
Depends on a narrow store protocol so it is unit-testable with a fake.
"""

from __future__ import annotations

import re
from typing import Protocol

from grimoire.util import slugify_id

from .config import InventoryConfig

# Leading quantity/article noise: "120 gold", "a rusty key", "the silver ring"
_QTY_PREFIX = re.compile(r"^\s*(\d+\s+|a\s+|an\s+|the\s+|some\s+)+", re.IGNORECASE)


class InventoryItemStore(Protocol):
    async def find_item_by_name(self, campaign_id: str, name: str) -> dict | None: ...
    async def create_emergent_item(
        self, campaign_id: str, name: str, *, source: str, turn_id: str | None = None
    ) -> str: ...


def _clean_name(raw: str) -> str:
    return _QTY_PREFIX.sub("", raw or "").strip()


class ItemResolver:
    def __init__(self, store: InventoryItemStore, config: InventoryConfig) -> None:
        self._store = store
        self._config = config

    async def resolve(
        self, campaign_id: str, raw_item: str, *, turn_id: str | None
    ) -> tuple[str, str, bool]:
        """Return (item_ref, item_name, fungible)."""
        name = _clean_name(raw_item)
        slug = slugify_id(name) if name else "unknown-item"

        # 1. Fungible keyword.
        if slug in self._config.fungible_resources:
            return f"resource:{slug}", name.title() or slug, True

        # 2. Existing item.
        match = await self._store.find_item_by_name(campaign_id, name)
        if match is not None:
            return match["item_ref"], match["item_name"], False

        # 3. Auto-create emergent item.
        new_ref = await self._store.create_emergent_item(
            campaign_id, name, source="inventory", turn_id=turn_id
        )
        return new_ref, name, False
