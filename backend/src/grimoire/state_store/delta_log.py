"""Delta log: append, reverse, queue-for-review.

Every campaign state change is recorded as a row in ``deltas``. The
``before`` JSON column is the inverse needed for reversal. Reversal walks
deltas in LIFO order and reapplies their ``before`` payloads, then marks
each delta with ``reversed_at``.

Reversal logic is per-target-scope:

- ``campaign-sqlite`` targets are reversed by re-running an UPDATE/INSERT
  against ``target_table`` using ``before`` (or a DELETE if ``before`` is
  null, meaning the row didn't exist before the delta).
- ``campaign-file`` and ``library`` targets emit a reversal request that
  callers (the StateStore) execute against the filesystem.

Deltas with non-reversible targets (or targets the store cannot reverse on
its own) raise ``StateStoreError`` so callers handle them explicitly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import aiosqlite

from grimoire.state_store.errors import NotFoundError, StateStoreError
from grimoire.util import new_id, now_iso


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, default=str)


def _json_loads(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


@dataclass(frozen=True)
class DeltaRecord:
    """A row from ``deltas`` materialized into Python."""

    id: str
    campaign_id: str | None
    branch_id: str | None
    turn_id: str | None
    source: str | None
    kind: str | None
    target_scope: str | None
    target_table: str | None
    target_path: str | None
    target_id: str | None
    before: Any
    after: Any
    confidence: float | None
    applied_at: str | None
    reversed_at: str | None
    notes: str | None
    delta_set_id: str | None = None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> DeltaRecord:
        # delta_set_id is only present after migration 024; tolerate older rows.
        try:
            delta_set_id = row["delta_set_id"]
        except (KeyError, IndexError):
            delta_set_id = None
        return cls(
            id=row["id"],
            campaign_id=row["campaign_id"],
            branch_id=row["branch_id"],
            turn_id=row["turn_id"],
            source=row["source"],
            kind=row["kind"],
            target_scope=row["target_scope"],
            target_table=row["target_table"],
            target_path=row["target_path"],
            target_id=row["target_id"],
            before=_json_loads(row["before"]),
            after=_json_loads(row["after"]),
            confidence=row["confidence"],
            applied_at=row["applied_at"],
            reversed_at=row["reversed_at"],
            notes=row["notes"],
            delta_set_id=delta_set_id,
        )


async def insert_delta(
    conn: aiosqlite.Connection,
    *,
    campaign_id: str | None,
    branch_id: str | None,
    turn_id: str | None,
    source: str,
    kind: str,
    target_scope: str,
    target_table: str | None,
    target_path: str | None,
    target_id: str | None,
    before: Any,
    after: Any,
    confidence: float | None = None,
    notes: str | None = None,
    delta_set_id: str | None = None,
) -> str:
    delta_id = new_id("d", length=16)
    await conn.execute(
        """
        INSERT INTO deltas (
          id, campaign_id, branch_id, turn_id, source, kind,
          target_scope, target_table, target_path, target_id,
          before, after, confidence, applied_at, reversed_at, notes,
          delta_set_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            delta_id,
            campaign_id,
            branch_id,
            turn_id,
            source,
            kind,
            target_scope,
            target_table,
            target_path,
            target_id,
            _json_dumps(before),
            _json_dumps(after),
            confidence,
            now_iso(),
            notes,
            delta_set_id,
        ),
    )
    return delta_id


async def mark_reversed(conn: aiosqlite.Connection, delta_id: str) -> None:
    await conn.execute(
        "UPDATE deltas SET reversed_at = ? WHERE id = ?",
        (now_iso(), delta_id),
    )


async def get_delta(conn: aiosqlite.Connection, delta_id: str) -> DeltaRecord:
    cur = await conn.execute("SELECT * FROM deltas WHERE id = ?", (delta_id,))
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise NotFoundError(f"delta {delta_id!r} not found")
    return DeltaRecord.from_row(row)


async def list_deltas(
    conn: aiosqlite.Connection,
    *,
    campaign_id: str | None = None,
    since: str | None = None,
    turn_id: str | None = None,
    include_reversed: bool = True,
    limit: int | None = None,
) -> list[DeltaRecord]:
    where: list[str] = []
    params: list[Any] = []
    if campaign_id is not None:
        where.append("campaign_id = ?")
        params.append(campaign_id)
    if since is not None:
        where.append("applied_at >= ?")
        params.append(since)
    if turn_id is not None:
        where.append("turn_id = ?")
        params.append(turn_id)
    if not include_reversed:
        where.append("reversed_at IS NULL")
    sql = "SELECT * FROM deltas"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY applied_at ASC, rowid ASC"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    cur = await conn.execute(sql, tuple(params))
    rows = await cur.fetchall()
    await cur.close()
    return [DeltaRecord.from_row(row) for row in rows]


async def queue_for_review(
    conn: aiosqlite.Connection,
    *,
    delta_id: str,
    campaign_id: str | None,
) -> str:
    review_id = new_id("r", length=16)
    await conn.execute(
        """
        INSERT INTO review_queue (id, delta_id, campaign_id, status, reviewed_at, reviewer_notes)
        VALUES (?, ?, ?, 'pending', NULL, NULL)
        """,
        (review_id, delta_id, campaign_id),
    )
    return review_id


# ---------------------------------------------------------------------------
# SQLite-target reversal
# ---------------------------------------------------------------------------


_REVERSIBLE_TABLES: set[str] = {
    "character_state",
    "location_state",
    "faction_state",
    "facts",
    "commitments",
    "relationships",
    "knowledge_state",
    "calendar",
    "images",
    "scenes",
    "posts",
}


def _columns_for(table: str) -> list[str]:
    return _TABLE_COLUMNS[table]


_TABLE_COLUMNS: dict[str, list[str]] = {
    "character_state": [
        "character_ref",
        "campaign_id",
        "branch_id",
        "location_ref",
        "emotional_state",
        "physical_state",
        "immediate_intent",
        "knowledge_state",
        "last_action",
        "last_screen_time_turn",
        "visible_to_pc",
        "drift_score",
        "tier_pin",
        "current_scene_id",
        "updated_at_turn",
        "appearances_since_last_drift_check",
    ],
    "location_state": [
        "location_ref",
        "campaign_id",
        "branch_id",
        "weather",
        "time_of_day",
        "occupants",
        "condition",
        "transient_features",
        "updated_at_turn",
    ],
    "faction_state": [
        "faction_ref",
        "campaign_id",
        "branch_id",
        "state",
        "updated_at_turn",
    ],
    "facts": [
        "id",
        "campaign_id",
        "branch_id",
        "text",
        "established_in_post",
        "in_game_when",
        "about",
        "source",
        "speaker_ref",
        "confidence",
        "keywords",
        "retired",
        "retired_in_post",
        "contradicts",
        "tags",
    ],
    "commitments": [
        "id",
        "campaign_id",
        "branch_id",
        "kind",
        "text",
        "from_character_ref",
        "to_character_ref",
        "due_by",
        "status",
        "weight",
        "created_in_post",
        "in_game_created_at",
        "resolved_in_post",
        "tags",
        "related_fact_ids",
    ],
    "relationships": [
        "id",
        "campaign_id",
        "branch_id",
        "from_character_ref",
        "to_character_ref",
        "types",
        "state",
        "updated_at_turn",
    ],
    "knowledge_state": [
        "fact_id",
        "character_ref",
        "campaign_id",
        "branch_id",
        "knows",
        "learned_in_post",
        "source",
    ],
    "calendar": [
        "campaign_id",
        "branch_id",
        "current_in_game_time",
    ],
    "images": [
        "id",
        "campaign_id",
        "branch_id",
        "scene_id",
        "post_id",
        "file_path",
        "thumbnail_path",
        "prompt",
        "negative_prompt",
        "params",
        "backend",
        "model",
        "seed",
        "created_at",
        "user_starred",
        "tags",
    ],
    "scenes": [
        "id",
        "campaign_id",
        "branch_id",
        "ordinal",
        "slug",
        "file_path",
        "location_ref",
        "in_game_start",
        "in_game_end",
        "pov_character_ref",
        "present_character_refs",
        "present_pc_refs",
        "summary",
        "running_summary",
        "key_beats",
        "tags",
        "emotional_arc",
        "post_count",
        "threads_introduced",
        "threads_paid_off",
        "title",
        "greeting_id",
        "closed",
        "closed_at_turn",
    ],
    "posts": [
        "id",
        "scene_id",
        "campaign_id",
        "branch_id",
        "turn_id",
        "order_in_scene",
        "author_kind",
        "author_pc_ref",
        "body_excerpt",
        "body_hash",
        "is_player",
        "created_at",
        "retconned_from",
    ],
}


_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "character_state": ("character_ref", "branch_id"),
    "location_state": ("location_ref", "branch_id"),
    "faction_state": ("faction_ref", "branch_id"),
    "facts": ("id",),
    "commitments": ("id",),
    "relationships": ("id",),
    "knowledge_state": ("fact_id", "character_ref", "branch_id"),
    "calendar": ("branch_id",),
    "images": ("id",),
    "scenes": ("id",),
    "posts": ("id",),
}


def _coerce_for_column(table: str, column: str, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return value


async def upsert_row(
    conn: aiosqlite.Connection,
    *,
    table: str,
    values: dict[str, Any],
) -> None:
    """Upsert a row into a delta-reversible table.

    The dict's keys must be a subset of the table's columns; missing keys
    fall back to ``NULL`` (or the column's default) on insert. Primary key
    columns must be present.
    """
    if table not in _TABLE_COLUMNS:
        raise StateStoreError(f"table {table!r} is not registered for upsert")
    pk = _PRIMARY_KEYS[table]
    for col in pk:
        if col not in values:
            raise StateStoreError(f"missing primary-key column {col!r} for {table}")

    columns = [c for c in _TABLE_COLUMNS[table] if c in values]
    placeholders = ", ".join("?" for _ in columns)
    column_list = ", ".join(columns)
    pk_conflict = ", ".join(pk)
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c not in pk)
    coerced = tuple(_coerce_for_column(table, c, values[c]) for c in columns)

    sql = (
        f"INSERT INTO {table} ({column_list}) VALUES ({placeholders}) "
        f"ON CONFLICT({pk_conflict}) DO UPDATE SET {updates}"
        if updates
        else f"INSERT OR REPLACE INTO {table} ({column_list}) VALUES ({placeholders})"
    )
    await conn.execute(sql, coerced)


async def delete_row(
    conn: aiosqlite.Connection,
    *,
    table: str,
    pk_values: dict[str, Any],
) -> None:
    if table not in _PRIMARY_KEYS:
        raise StateStoreError(f"table {table!r} is not registered for delete")
    pk = _PRIMARY_KEYS[table]
    where = " AND ".join(f"{c} = ?" for c in pk)
    params = tuple(pk_values[c] for c in pk)
    await conn.execute(f"DELETE FROM {table} WHERE {where}", params)


async def reverse_sqlite_delta(
    conn: aiosqlite.Connection,
    delta: DeltaRecord,
) -> None:
    if delta.target_table is None:
        raise StateStoreError(f"delta {delta.id} has no target_table")
    if delta.target_table not in _REVERSIBLE_TABLES:
        raise StateStoreError(f"table {delta.target_table!r} is not registered for delta reversal")

    pk_cols = _PRIMARY_KEYS[delta.target_table]
    after = delta.after or {}
    before = delta.before

    if before is None:
        # Row did not exist before the delta: deletion is the inverse.
        if not all(col in after for col in pk_cols):
            raise StateStoreError(
                f"cannot reverse insertion of delta {delta.id}: after-state missing PK"
            )
        await delete_row(
            conn,
            table=delta.target_table,
            pk_values={col: after[col] for col in pk_cols},
        )
        return

    await upsert_row(conn, table=delta.target_table, values=before)


def primary_key_columns(table: str) -> tuple[str, ...] | None:
    """Public accessor for the PK columns of a delta-reversible table."""
    return _PRIMARY_KEYS.get(table)


__all__ = [
    "DeltaRecord",
    "delete_row",
    "get_delta",
    "insert_delta",
    "list_deltas",
    "mark_reversed",
    "primary_key_columns",
    "queue_for_review",
    "reverse_sqlite_delta",
    "upsert_row",
]
