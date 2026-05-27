"""Campaign-level fork primitives.

This module bulk-copies every campaign-scoped SQLite table from a source
campaign id into a new campaign id, optionally filtered by a turn cutoff.
It also produces a deterministic fingerprint of a campaign's state for
the safety-net check on fork-from-earlier.

Most ids in this schema (post.id, scene.id, delta.id, image.id, etc.)
are globally unique strings rather than ``(campaign_id, local_id)``
composites, so copying a row verbatim with just ``campaign_id`` rewritten
would collide with the source. We rewrite every known TEXT id reference
in a row with a fork-specific prefix so PKs and FKs stay consistent
inside the new campaign.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass

import aiosqlite

from grimoire.storage import Database

# ---------------------------------------------------------------------------
# Rewrite spec
# ---------------------------------------------------------------------------
#
# Per-table list of columns and how to rewrite them when copying a row
# from the source campaign to the fork. Order matters across tables:
# referenced tables come before referencing ones. ``cutoff_col`` is the
# column used to filter rows by a turn cutoff (None → copy wholesale).
#
# Column rewrite kinds:
#   "campaign"  — replace with ``new_campaign_id``
#   "id"        — prefix the value with a fork token so the new row's
#                 PK / FK is unique to the fork
#   "ref"       — same as "id" but the column may also hold non-id values
#                 (e.g. ``embeddings.ref`` can be a library id we leave
#                 alone). We only rewrite when the value looks like a
#                 known forked id.
#
# A None entry under ``cutoff_col`` means the cutoff filter does not
# apply to this table.

TableSpec = dict[str, object]


CAMPAIGN_SCOPED_TABLES: list[TableSpec] = [
    {
        "table": "campaign_world_refs",
        "cutoff_col": None,
        "rewrites": {"campaign_id": "campaign"},
    },
    {
        "table": "campaign_pcs",
        "cutoff_col": None,
        "rewrites": {"campaign_id": "campaign"},
    },
    {
        "table": "campaign_mechanics_history",
        "cutoff_col": "switched_at",
        "rewrites": {"campaign_id": "campaign"},
    },
    {
        "table": "scenes",
        "cutoff_col": None,
        "rewrites": {
            "id": "id",
            "campaign_id": "campaign",
            "closed_at_turn": "ref",
        },
    },
    {
        "table": "posts",
        "cutoff_col": "created_at",
        "rewrites": {
            "id": "id",
            "scene_id": "id",
            "campaign_id": "campaign",
            "turn_id": "ref",
            "retconned_from": "ref",
        },
    },
    {
        "table": "facts",
        "cutoff_col": None,
        "rewrites": {
            "id": "id",
            "campaign_id": "campaign",
            "established_in_post": "id",
            "retired_in_post": "id",
        },
    },
    {
        "table": "commitments",
        "cutoff_col": None,
        "rewrites": {
            "id": "id",
            "campaign_id": "campaign",
            "created_in_post": "id",
            "resolved_in_post": "id",
        },
    },
    {
        "table": "relationships",
        "cutoff_col": None,
        "rewrites": {
            "id": "id",
            "campaign_id": "campaign",
            "updated_at_turn": "ref",
        },
    },
    {
        "table": "knowledge_state",
        "cutoff_col": None,
        "rewrites": {
            "fact_id": "id",
            "campaign_id": "campaign",
            "learned_in_post": "id",
        },
    },
    {
        "table": "calendar",
        "cutoff_col": None,
        "rewrites": {"campaign_id": "campaign"},
    },
    {
        "table": "character_state",
        "cutoff_col": None,
        "rewrites": {
            "campaign_id": "campaign",
            "current_scene_id": "id",
            "last_screen_time_turn": "ref",
            "updated_at_turn": "ref",
        },
    },
    {
        "table": "location_state",
        "cutoff_col": None,
        "rewrites": {
            "campaign_id": "campaign",
            "updated_at_turn": "ref",
        },
    },
    {
        "table": "faction_state",
        "cutoff_col": None,
        "rewrites": {
            "campaign_id": "campaign",
            "updated_at_turn": "ref",
        },
    },
    {
        "table": "images",
        "cutoff_col": "created_at",
        "rewrites": {
            "id": "id",
            "campaign_id": "campaign",
            "scene_id": "id",
            "post_id": "id",
        },
    },
    {
        "table": "deltas",
        "cutoff_col": "applied_at",
        "rewrites": {
            "id": "id",
            "campaign_id": "campaign",
            "turn_id": "ref",
        },
    },
    {
        "table": "review_queue",
        "cutoff_col": None,
        "rewrites": {
            "id": "id",
            "campaign_id": "campaign",
            "delta_id": "id",
        },
    },
    {
        "table": "embeddings",
        "cutoff_col": "embedded_at",
        "rewrites": {
            "id": "id",
            "campaign_id": "campaign",
            "ref": "ref",
        },
    },
    {
        "table": "turn_audits",
        "cutoff_col": "created_at",
        "rewrites": {
            "turn_id": "ref",
            "campaign_id": "campaign",
            "scene_id": "id",
        },
    },
    {
        "table": "contradiction_reports",
        "cutoff_col": "created_at",
        "rewrites": {
            "id": "id",
            "campaign_id": "campaign",
        },
    },
    {
        "table": "scheduled_events",
        "cutoff_col": "created_at",
        "rewrites": {
            "id": "id",
            "campaign_id": "campaign",
        },
    },
]

# Tables explicitly excluded (per-account observability, transient queues).
EXCLUDED_TABLES: tuple[str, ...] = (
    "cost_records",
    "llm_requests",
    "imagegen_jobs",
    "log_events",
    "error_records",
    "metric_samples",
    "embedding_cache",
    "export_records",
)


@dataclass(frozen=True)
class BulkCopyResult:
    rows_per_table: dict[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.rows_per_table.values())


def fork_prefix(new_campaign_id: str) -> str:
    """Prefix prepended to every TEXT id when forking into ``new_campaign_id``."""
    return f"{new_campaign_id}::"


def _rewrite_id(value: object, prefix: str) -> object:
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(prefix):
        return value
    return prefix + value


def _rewrite_ref(value: object, prefix: str) -> object:
    """Rewrite a TEXT column that may hold a fork-scoped id *or* an opaque
    external reference (e.g. a library path, a sentinel like ``"manual"``).

    Only prefix values that look like a fork-scoped id: a short token of
    the form ``<prefix>_<hex>`` (e.g. ``t_abcd``, ``fact_1234``). Library
    paths contain ``/``; the existing prefix marker contains ``::`` —
    both are left untouched so the embedding's pointer / sentinel value
    survives the copy intact.
    """
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(prefix):
        return value
    if "/" in value or ":" in value:
        return value
    # Heuristic: short prefix + underscore + alphanumeric body. Anything
    # else (e.g. the ``"manual"`` sentinel on ``closed_at_turn``, or a
    # human-readable label) we leave alone.
    underscore = value.find("_")
    if underscore <= 0 or underscore >= len(value) - 1:
        return value
    head, tail = value[:underscore], value[underscore + 1 :]
    if not head.isalpha() or not tail.replace("-", "").isalnum():
        return value
    return prefix + value


async def _table_exists(conn: aiosqlite.Connection, table: str) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    row = await cur.fetchone()
    await cur.close()
    return row is not None


async def _table_columns(conn: aiosqlite.Connection, table: str) -> list[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    await cur.close()
    return [r["name"] for r in rows]


async def bulk_copy(
    db: Database,
    *,
    original: str,
    new: str,
    cutoff_iso: str | None = None,
) -> BulkCopyResult:
    """Copy every campaign-scoped row from ``original`` into ``new``.

    Runs as a single transaction. When ``cutoff_iso`` is provided, rows
    in tables with a filter column are only copied when the cutoff
    column is ``<= cutoff_iso``. Newly inserted rows have their
    ``campaign_id`` rewritten to ``new`` and TEXT id columns prefixed
    with ``new::`` so PKs and FKs stay consistent inside the fork.
    """
    rows_per_table: dict[str, int] = {}
    prefix = fork_prefix(new)
    async with db.acquire() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            for spec in CAMPAIGN_SCOPED_TABLES:
                table = spec["table"]
                if not await _table_exists(conn, table):
                    continue
                columns = await _table_columns(conn, table)
                if not columns:
                    continue

                cutoff_col = spec["cutoff_col"]
                where = "campaign_id = ?"
                params: tuple = (original,)
                if cutoff_iso is not None and cutoff_col and cutoff_col in columns:
                    where += f" AND {cutoff_col} <= ?"
                    params = (original, cutoff_iso)

                cur = await conn.execute(f"SELECT * FROM {table} WHERE {where}", params)
                source_rows = await cur.fetchall()
                await cur.close()
                if not source_rows:
                    rows_per_table[table] = 0
                    continue

                placeholders = ", ".join("?" for _ in columns)
                col_list = ", ".join(columns)
                insert_sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"

                rewrites: dict[str, str] = spec.get("rewrites", {})

                count = 0
                for row in source_rows:
                    values: list[object] = []
                    for col in columns:
                        v = row[col]
                        kind = rewrites.get(col)
                        if kind == "campaign":
                            v = new
                        elif kind == "id":
                            v = _rewrite_id(v, prefix)
                        elif kind == "ref":
                            v = _rewrite_ref(v, prefix)
                        values.append(v)
                    await conn.execute(insert_sql, tuple(values))
                    count += 1
                rows_per_table[table] = count
            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise
    return BulkCopyResult(rows_per_table=rows_per_table)


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

# The ``campaigns`` row itself is not in the fingerprint set: it holds
# metadata (created_at, last_played_at, fork provenance) that diverges
# between a source and its fork by design. The actual game state lives
# in the per-campaign tables below.
FINGERPRINT_TABLES: tuple[str, ...] = (
    "campaign_world_refs",
    "campaign_pcs",
    "scenes",
    "posts",
    "facts",
    "commitments",
    "relationships",
    "knowledge_state",
    "calendar",
    "character_state",
    "location_state",
    "faction_state",
    "images",
    "scheduled_events",
)

_FINGERPRINT_EXCLUDED_COLS = frozenset(
    {
        "campaign_id",
        "id",
        "scene_id",
        "post_id",
        "established_in_post",
        "retired_in_post",
        "created_in_post",
        "resolved_in_post",
        "learned_in_post",
        "fact_id",
        "current_scene_id",
        "delta_id",
        "turn_id",
        "ref",
        "updated_at_turn",
        "closed_at_turn",
        "last_screen_time_turn",
        "retconned_from",
        "forked_from_campaign_id",
        "forked_at_post_id",
        "forked_at_turn_id",
        "forked_image_handling",
    }
)


def _row_canonical(row: aiosqlite.Row, exclude: Iterable[str] = ()) -> str:
    skip = set(exclude)
    payload = {k: row[k] for k in row.keys() if k not in skip}  # noqa: SIM118
    return json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)


# Per-table cutoff column for fingerprints. When ``cutoff_iso`` is
# provided, rows in tables with a cutoff column are only hashed when
# ``cutoff_col <= cutoff_iso``. Mirrors :data:`CAMPAIGN_SCOPED_TABLES`
# so the source's fingerprint at a cutoff matches the fork's
# fingerprint after replay.
_FINGERPRINT_CUTOFF_COLS: dict[str, str] = {
    "posts": "created_at",
    "images": "created_at",
    "scheduled_events": "created_at",
}


async def fingerprint(db: Database, campaign_id: str, *, cutoff_iso: str | None = None) -> str:
    """SHA-256 fingerprint of the campaign's game state.

    Independent of identifier values that are rewritten on fork — the
    fork's fingerprint matches the source's whenever the actual state
    is equivalent. When ``cutoff_iso`` is provided, time-ordered tables
    (posts, images, scheduled events) are filtered to ``<= cutoff_iso``
    so the source-at-cutoff and the fork-after-replay compare equal.
    """
    h = hashlib.sha256()
    async with db.acquire() as conn:
        for table in FINGERPRINT_TABLES:
            if not await _table_exists(conn, table):
                continue
            cutoff_col = _FINGERPRINT_CUTOFF_COLS.get(table)
            if cutoff_iso is not None and cutoff_col:
                cur = await conn.execute(
                    f"SELECT * FROM {table} WHERE campaign_id = ? "
                    f"AND {cutoff_col} <= ? ORDER BY rowid",
                    (campaign_id, cutoff_iso),
                )
            else:
                cur = await conn.execute(
                    f"SELECT * FROM {table} WHERE campaign_id = ? ORDER BY rowid",
                    (campaign_id,),
                )
            rows = await cur.fetchall()
            await cur.close()
            h.update(table.encode())
            h.update(b":")
            for row in rows:
                h.update(_row_canonical(row, exclude=_FINGERPRINT_EXCLUDED_COLS).encode())
                h.update(b"|")
            h.update(b";")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Replay (copy-and-truncate at a turn cutoff)
# ---------------------------------------------------------------------------


async def replay_to_turn(
    db: Database,
    *,
    original: str,
    new: str,
    cutoff_iso: str,
) -> int:
    """Materialize state of ``original`` at ``cutoff_iso`` into ``new``.

    Because every delta in the codebase stores the absolute ``after``
    snapshot, the state visible at any historical turn can be
    reconstructed from the state-tables (which already hold the cutoff
    state if we filter the audit by timestamp). Returns the number of
    delta rows copied.
    """
    result = await bulk_copy(db, original=original, new=new, cutoff_iso=cutoff_iso)
    return result.rows_per_table.get("deltas", 0)
