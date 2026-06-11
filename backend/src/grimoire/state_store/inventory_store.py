"""InventoryStore — derived inventory holdings and review flags (#444).

Extracted from :class:`~grimoire.state_store.store.StateStore` (#521). Owns
the derived ``inventory_holdings`` / ``inventory_flags`` SQLite tables; the
SSOT remains the ``inventory:`` sections in campaign overlay files, which
:meth:`InventoryStore.rebuild_holdings_from_files` re-reads wholesale.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import aiosqlite

from grimoire.files import load_yaml, read_markdown
from grimoire.state_store.paths import KIND_TO_DIR, campaigns_root
from grimoire.storage import Database
from grimoire.util import new_id, slugify_id

logger = logging.getLogger(__name__)

TxnFactory = Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]]

# write_emergent(campaign_id=..., kind=..., entity_id=..., frontmatter=...,
# body=..., source=..., turn_id=...) — the owning store's emergent-file write,
# injected so emergent item creation goes through the canonical file path.
WriteEmergent = Callable[..., Awaitable[object]]


class InventoryStore:
    def __init__(
        self,
        *,
        db: Database,
        data_root: Path,
        txn: TxnFactory,
        write_emergent: WriteEmergent,
    ) -> None:
        self._db = db
        self._data_root = data_root
        self._txn = txn
        self._write_emergent = write_emergent

    async def upsert_holding(
        self,
        *,
        campaign_id: str,
        holder_kind: str,
        holder_id: str,
        item_ref: str,
        item_name: str,
        quantity: int,
        fungible: bool,
        equipped: bool,
        provenance: str | None,
        notes: str | None,
    ) -> None:
        rid = f"{campaign_id}:{holder_kind}:{holder_id}:{item_ref}"
        await self._db.execute(
            """
            INSERT INTO inventory_holdings
              (id, campaign_id, holder_kind, holder_id, item_ref, item_name,
               quantity, fungible, equipped, provenance, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              item_name=excluded.item_name, quantity=excluded.quantity,
              fungible=excluded.fungible, equipped=excluded.equipped,
              provenance=excluded.provenance, notes=excluded.notes
            """,
            (
                rid,
                campaign_id,
                holder_kind,
                holder_id,
                item_ref,
                item_name,
                int(quantity),
                int(fungible),
                int(equipped),
                provenance,
                notes,
            ),
        )

    async def delete_holding(
        self, campaign_id: str, holder_kind: str, holder_id: str, item_ref: str
    ) -> None:
        rid = f"{campaign_id}:{holder_kind}:{holder_id}:{item_ref}"
        await self._db.execute("DELETE FROM inventory_holdings WHERE id = ?", (rid,))

    async def clear_holder(self, campaign_id: str, holder_kind: str, holder_id: str) -> None:
        await self._db.execute(
            "DELETE FROM inventory_holdings WHERE campaign_id=? AND holder_kind=? AND holder_id=?",
            (campaign_id, holder_kind, holder_id),
        )

    async def rebuild_holdings_from_files(self) -> int:
        """Rebuild the derived ``inventory_holdings`` table by reading the
        ``inventory:`` sections directly from campaign overlay files (the SSOT).

        Reads files rather than ``campaign_content_index`` so the rebuild is
        immune to content-index keying (emergent rows are keyed by the raw
        ``kind`` while the watcher classifies by directory). A full
        truncate-and-repopulate, so removed sections and removed holders leave
        no stale rows. The storage layer owns this derived table and file I/O.

        A holder file that fails to parse is logged (with its path + exception)
        and skipped rather than aborting the whole rebuild — the SSOT file is
        intact, so this is a recoverable partial rebuild. Returns the number of
        holder files skipped so a bad file is observable to callers.
        """
        dir_to_kind = {dir_name: kind for kind, dir_name in KIND_TO_DIR.items()}
        wanted = {"character", "location"}
        # Rows hold campaign_id, kind, holder_id, entries
        discovered: list[tuple[str, str, str, list]] = []
        skipped = 0

        def _subdirs(parent: Path) -> list[Path]:
            if not parent.is_dir():
                return []
            return [p for p in parent.iterdir() if p.is_dir()]

        def _entries(block: object) -> list:
            return (block or {}).get("entries") or [] if isinstance(block, dict) else []

        root = campaigns_root(self._data_root)
        for camp_dir in _subdirs(root):
            cid = camp_dir.name
            # Emergent holders: emergent/<dir>/<id>.md (markdown + frontmatter).
            for kind_dir in _subdirs(camp_dir / "emergent"):
                kind = dir_to_kind.get(kind_dir.name, kind_dir.name)
                if kind not in wanted:
                    continue
                for f in kind_dir.glob("*.md"):
                    try:
                        fm = read_markdown(f).frontmatter or {}
                    except Exception:
                        logger.warning("inventory rebuild: failed to parse %s", f, exc_info=True)
                        skipped += 1
                        continue
                    entries = _entries(fm.get("inventory"))
                    if entries:
                        discovered.append((cid, kind, f.stem, entries))
            # Library-scoped holders: overrides/worlds/<world>/<dir>/<id>.yaml.
            for world_dir in _subdirs(camp_dir / "overrides" / "worlds"):
                for kind_dir in _subdirs(world_dir):
                    kind = dir_to_kind.get(kind_dir.name, kind_dir.name)
                    if kind not in wanted:
                        continue
                    for f in kind_dir.glob("*.yaml"):
                        try:
                            data = load_yaml(f) or {}
                        except Exception:
                            logger.warning(
                                "inventory rebuild: failed to parse %s", f, exc_info=True
                            )
                            skipped += 1
                            continue
                        entries = _entries(data.get("inventory"))
                        if entries:
                            discovered.append((cid, kind, f.stem, entries))

        async with self._txn() as conn:
            await conn.execute("DELETE FROM inventory_holdings")
            for cid, kind, hid, entries in discovered:
                for e in entries:
                    rid = f"{cid}:{kind}:{hid}:{e['item_ref']}"
                    await conn.execute(
                        """
                        INSERT INTO inventory_holdings
                          (id, campaign_id, holder_kind, holder_id, item_ref, item_name,
                           quantity, fungible, equipped, provenance, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                          item_name=excluded.item_name, quantity=excluded.quantity,
                          fungible=excluded.fungible, equipped=excluded.equipped,
                          provenance=excluded.provenance, notes=excluded.notes
                        """,
                        (
                            rid,
                            cid,
                            kind,
                            hid,
                            e["item_ref"],
                            e.get("item_name", e["item_ref"]),
                            int(e.get("quantity", 1)),
                            int(bool(e.get("fungible", False))),
                            int(bool(e.get("equipped", False))),
                            e.get("provenance"),
                            e.get("notes"),
                        ),
                    )
        return skipped

    async def list_holdings(
        self,
        campaign_id: str,
        *,
        holder_kind: str | None = None,
        holder_id: str | None = None,
        item_ref: str | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM inventory_holdings WHERE campaign_id = ?"
        params: list = [campaign_id]
        if holder_kind is not None:
            sql += " AND holder_kind = ?"
            params.append(holder_kind)
        if holder_id is not None:
            sql += " AND holder_id = ?"
            params.append(holder_id)
        if item_ref is not None:
            sql += " AND item_ref = ?"
            params.append(item_ref)
        rows = await self._db.fetchall(sql, tuple(params))
        return [dict(r) for r in rows]

    async def record_flag(
        self,
        *,
        campaign_id: str,
        turn_id: str | None,
        op_json: str,
        flag_reason: str,
        created_at: str,
    ) -> str:
        fid = new_id("invflag")
        await self._db.execute(
            """
            INSERT INTO inventory_flags
              (id, campaign_id, turn_id, op_json, flag_reason, resolved, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (fid, campaign_id, turn_id, op_json, flag_reason, created_at),
        )
        return fid

    async def list_flags(self, campaign_id: str, *, resolved: bool) -> list[dict]:
        rows = await self._db.fetchall(
            "SELECT * FROM inventory_flags WHERE campaign_id=? AND resolved=? "
            "ORDER BY created_at DESC",
            (campaign_id, int(resolved)),
        )
        return [dict(r) for r in rows]

    async def resolve_flag(self, campaign_id: str, flag_id: str) -> None:
        await self._db.execute(
            "UPDATE inventory_flags SET resolved=1 WHERE campaign_id=? AND id=?",
            (campaign_id, flag_id),
        )

    async def delete_flag(self, campaign_id: str, flag_id: str) -> None:
        """Remove a flag outright — used when the apply that recorded it rolls
        back (#584), so no review row survives for an unapplied change."""
        await self._db.execute(
            "DELETE FROM inventory_flags WHERE campaign_id=? AND id=?",
            (campaign_id, flag_id),
        )

    async def find_item_by_name(self, campaign_id: str, name: str) -> dict | None:
        """Resolve an item name to a campaign-visible item via the content index."""
        slug = slugify_id(name)
        row = await self._db.fetchone(
            "SELECT asset_id, frontmatter FROM campaign_content_index "
            "WHERE campaign_id=? AND entity_subkind='item' AND asset_id=?",
            (campaign_id, slug),
        )
        if row is None:
            return None
        fm = json.loads(row["frontmatter"]) if row["frontmatter"] else {}
        return {"item_ref": row["asset_id"], "item_name": fm.get("name", name)}

    async def create_emergent_item(
        self, campaign_id: str, name: str, *, source: str, turn_id: str | None = None
    ) -> str:
        slug = slugify_id(name)
        await self._write_emergent(
            campaign_id=campaign_id,
            kind="item",
            entity_id=slug,
            frontmatter={"id": slug, "name": name, "tags": ["emergent"]},
            body="",
            source=source,
            turn_id=turn_id,
        )
        return slug
