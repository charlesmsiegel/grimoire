"""SQLite mirror writer for ``entity_extras`` and FTS index.

The mirror is for query only -- substring search across ``value_text``,
listing pinned extras, and observability. Reads do not touch it (cascade
resolution uses frontmatter dicts on ``ResolvedEntity``). Every
``ExtrasService.set`` / ``delete`` re-materializes the row; a periodic
reconciliation job rebuilds the mirror from the SSOT on drift.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from grimoire.storage.db import Database
from grimoire.types.extras import flatten_extras_value_for_search


def _serialize_value(value: Any) -> str:
    """Encode an extras value for ``value_json``. We use real JSON so the
    fts5 ``value_text`` rebuild path can keep working through the triggers."""
    text = flatten_extras_value_for_search(value)
    # Store the flattened text as the FTS payload; the structured value
    # lives on disk in frontmatter (the SSOT) so a JSON copy in the mirror
    # would be redundant. The triggers project ``value_json`` directly into
    # ``value_text`` -- by writing the flattened form here we keep the FTS
    # tokens human-readable.
    return text or json.dumps(value, default=str)


class ExtrasMirror:
    """Thin write helper around ``entity_extras``.

    Lifecycle: the service constructs one mirror per ``ExtrasService`` and
    calls ``upsert`` / ``delete`` after each frontmatter write. The schema
    enforces the reserved-prefix CHECK as a belt-and-suspenders guard.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    async def upsert(
        self,
        *,
        campaign_id: str,
        entity_kind: str,
        entity_id: str,
        scope: str,
        key: str,
        value: Any,
        set_at: datetime,
        set_by: str,
    ) -> None:
        value_json = _serialize_value(value)
        # Database connections are opened with ``isolation_level=None``
        # (autocommit). Each statement persists on its own; no explicit
        # commit() needed.
        await self.db.execute(
            """
            INSERT INTO entity_extras
                (campaign_id, entity_kind, entity_id, scope, key,
                 value_json, set_at, set_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, entity_kind, entity_id, scope, key)
            DO UPDATE SET
                value_json = excluded.value_json,
                set_at     = excluded.set_at,
                set_by     = excluded.set_by
            """,
            (
                campaign_id,
                entity_kind,
                entity_id,
                scope,
                key,
                value_json,
                set_at.isoformat(),
                set_by,
            ),
        )

    async def delete(
        self,
        *,
        campaign_id: str,
        entity_kind: str,
        entity_id: str,
        scope: str,
        key: str,
    ) -> None:
        await self.db.execute(
            """
            DELETE FROM entity_extras
            WHERE campaign_id = ?
              AND entity_kind = ?
              AND entity_id = ?
              AND scope = ?
              AND key = ?
            """,
            (campaign_id, entity_kind, entity_id, scope, key),
        )

    async def delete_all_for_entity(
        self,
        *,
        campaign_id: str,
        entity_kind: str,
        entity_id: str,
        scope: str | None = None,
    ) -> None:
        """Remove every mirrored row for an entity. Used during reconcile
        when frontmatter has been edited out-of-band."""
        sql = (
            "DELETE FROM entity_extras WHERE campaign_id = ? AND entity_kind = ? AND entity_id = ?"
        )
        params: tuple = (campaign_id, entity_kind, entity_id)
        if scope is not None:
            sql += " AND scope = ?"
            params = (*params, scope)
        await self.db.execute(sql, params)

    async def search(
        self,
        query: str,
        *,
        entity_kind: str | None = None,
        key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """FTS5 MATCH against ``value_text``. Returns raw rows (caller
        translates to typed hits)."""
        where = ["entity_extras_fts MATCH ?"]
        params: list[Any] = [query]
        if entity_kind is not None:
            where.append("entity_kind = ?")
            params.append(entity_kind)
        if key is not None:
            where.append("key = ?")
            params.append(key)
        sql = (
            "SELECT entity_kind, entity_id, key, value_text "
            "FROM entity_extras_fts "
            f"WHERE {' AND '.join(where)} LIMIT ?"
        )
        params.append(limit)
        rows = await self.db.fetchall(sql, tuple(params))
        return [dict(r) for r in rows]
