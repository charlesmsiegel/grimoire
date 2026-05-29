"""Read/write the `inventory:` section in a holder's overlay file (SSOT),
keeping the derived `inventory_holdings` rows in sync.

Holder origin determines the overlay file:
  - campaign-emergent  -> emergent frontmatter
  - campaign-override / library-*  -> override YAML patch
PC profiles resolve as characters via the same cascade; their inventory rides
in the resolved frontmatter, written through the override patch path.
"""

from __future__ import annotations

from typing import Any

from grimoire.state_store.indexers import make_library_id

from .models import HolderKind, InventoryEntry


class InventoryPersistence:
    def __init__(self, store: Any) -> None:
        self._store = store

    async def read_holder_inventory(
        self, campaign_id: str, holder_kind: HolderKind, holder_id: str
    ) -> list[InventoryEntry]:
        world_id = await self._world_id_for(campaign_id, holder_kind, holder_id)
        resolved = await self._store.resolve_entity(
            campaign_id=campaign_id, kind=holder_kind.value, asset_id=holder_id,
            world_id=world_id,
        )
        fm = (resolved or {}).get("frontmatter", {}) or {}
        block = fm.get("inventory") or {}
        return [InventoryEntry.model_validate(e) for e in block.get("entries", [])]

    async def write_holder_inventory(
        self,
        *,
        campaign_id: str,
        holder_kind: HolderKind,
        holder_id: str,
        entries: list[InventoryEntry],
        source: str,
        turn_id: str | None,
    ) -> None:
        section = {"entries": [e.model_dump(exclude_none=True) for e in entries]}
        world_id = await self._world_id_for(campaign_id, holder_kind, holder_id)
        resolved = await self._store.resolve_entity(
            campaign_id=campaign_id, kind=holder_kind.value, asset_id=holder_id,
            world_id=world_id,
        )
        origin = (resolved or {}).get("source", "")

        if origin == "campaign-emergent":
            doc = await self._store.get_emergent(campaign_id, holder_kind.value, holder_id)
            fm = dict((doc or {}).get("frontmatter", {}))
            body = (doc or {}).get("body", "")
            fm["inventory"] = section
            await self._store.write_emergent(
                campaign_id=campaign_id, kind=holder_kind.value, entity_id=holder_id,
                frontmatter=fm, body=body, source=source, turn_id=turn_id,
            )
        else:
            # Library-scoped (override / library-*): write an override patch.
            library_id = make_library_id(world_id, holder_kind.value, holder_id)
            await self._store.write_override(
                campaign_id=campaign_id, library_id=library_id,
                patch={"inventory": section}, source=source, turn_id=turn_id,
            )

        await self._sync_derived(campaign_id, holder_kind, holder_id, entries)

    async def _sync_derived(
        self, campaign_id: str, holder_kind: HolderKind, holder_id: str,
        entries: list[InventoryEntry],
    ) -> None:
        await self._store.clear_holder_inventory(campaign_id, holder_kind.value, holder_id)
        for e in entries:
            await self._store.upsert_inventory_holding(
                campaign_id=campaign_id, holder_kind=holder_kind.value, holder_id=holder_id,
                item_ref=e.item_ref, item_name=e.item_name, quantity=e.quantity,
                fungible=e.fungible, equipped=e.equipped, provenance=e.provenance,
                notes=e.notes,
            )

    async def _world_id_for(
        self, campaign_id: str, holder_kind: HolderKind, holder_id: str
    ) -> str | None:
        """Find which world a library-defined holder belongs to, or None if emergent."""
        # Emergent holders have no world_id. Probe emergent first.
        emergent = await self._store.get_emergent(campaign_id, holder_kind.value, holder_id)
        if emergent is not None:
            return None
        refs = await self._store.list_world_refs(campaign_id)
        for r in refs:
            wid = r["world_id"]
            resolved = await self._store.resolve_entity(
                campaign_id=campaign_id, kind=holder_kind.value, asset_id=holder_id, world_id=wid,
            )
            if resolved is not None:
                return wid
        # Default to the first world ref so override path is well-formed.
        return refs[0]["world_id"] if refs else None
