"""DeltaOps — delta-log, delta-set, and review-queue operations.

Extracted from :class:`~grimoire.state_store.store.StateStore` (#521). The
store constructs one instance and delegates: applying/reversing deltas,
atomic set rewind/swap (swipes, alternates, retcon), and the low-confidence
review queue all live here. Review-queue handling is part of the same
cluster because :meth:`DeltaOps.swap_turn_deltas` rejects/queues review rows
inside the same transaction that swaps the turn's deltas.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from grimoire.files import ParsedDocument, write_markdown, write_yaml
from grimoire.state_store.delta_log import (
    DeltaRecord,
    get_delta,
    insert_delta,
    list_deltas,
    mark_reversed,
    primary_key_columns,
    reverse_sqlite_delta,
    upsert_row,
)
from grimoire.state_store.delta_log import queue_for_review as _queue_for_review
from grimoire.state_store.errors import NotFoundError, StateStoreError
from grimoire.state_store.file_snapshots import snapshot_file_before
from grimoire.state_store.indexers import (
    delete_campaign_content_row,
    delete_library_index_row,
    upsert_campaign_content_index,
    upsert_library_index,
)
from grimoire.state_store.paths import campaign_id_for_path
from grimoire.storage import Database
from grimoire.util import now_iso

TxnFactory = Callable[[], AbstractAsyncContextManager[aiosqlite.Connection]]


@dataclass(frozen=True)
class SwapResult:
    """Outcome of :meth:`DeltaOps.swap_delta_set` / :meth:`DeltaOps.swap_turn_deltas`.

    The review-queue fields are populated only by ``swap_turn_deltas``:
    ``queued_review_ids`` are the review rows created for the replacement
    set's low-confidence deltas; ``rejected_review_ids`` are the turn's
    stale pending review rows rejected because the text they were extracted
    from was retconned away.
    """

    rewound: list[DeltaRecord]
    applied: list[DeltaRecord]
    queued_review_ids: list[str] = field(default_factory=list)
    rejected_review_ids: list[str] = field(default_factory=list)


class DeltaOps:
    """Reversible-delta machinery over the ``deltas`` / ``review_queue`` tables.

    ``txn`` is the owning store's transaction factory, handed in explicitly so
    every multi-step operation here shares the store's BEGIN IMMEDIATE +
    metrics behaviour without reaching into store privates.
    """

    def __init__(self, *, db: Database, data_root: Path, txn: TxnFactory) -> None:
        self._db = db
        self._data_root = data_root
        self._txn = txn

    # ------------------------------------------------------------------
    # Delta log
    # ------------------------------------------------------------------

    async def apply_delta(
        self,
        *,
        delta: dict | Any,
        source: str | None = None,
        turn_id: str | None = None,
        campaign_id: str | None = None,
        delta_set_id: str | None = None,
    ) -> str:
        """Apply a delta to the SQLite layer and record it in ``deltas``."""
        async with self._txn() as conn:
            return await self._apply_delta_on_conn(
                conn,
                delta=delta,
                source=source,
                turn_id=turn_id,
                campaign_id=campaign_id,
                delta_set_id=delta_set_id,
            )

    async def _apply_delta_on_conn(
        self,
        conn: aiosqlite.Connection,
        *,
        delta: dict | Any,
        source: str | None = None,
        turn_id: str | None = None,
        campaign_id: str | None = None,
        delta_set_id: str | None = None,
    ) -> str:
        """Apply one delta inside an already-open transaction. Returns delta id."""
        payload = _delta_to_dict(delta)
        target_scope = payload.get("target_scope")
        target_table = payload.get("target_table")
        kind = payload.get("kind", "other")
        after = payload.get("after") or {}
        provided_before = payload.get("before")

        captured_before: Any | None = None
        if target_scope == "campaign-sqlite":
            if not target_table:
                raise StateStoreError("campaign-sqlite delta missing target_table")
            captured_before = await _capture_current_row(conn, target_table, after)
            await upsert_row(conn, table=target_table, values=after)
        elif target_scope in ("library", "campaign-file", "campaign-local"):
            pass
        else:
            raise StateStoreError(f"unknown target_scope {target_scope!r}; use file APIs for files")

        before_for_log = provided_before if provided_before is not None else captured_before
        delta_id = await insert_delta(
            conn,
            campaign_id=campaign_id or payload.get("campaign_id"),
            turn_id=turn_id or payload.get("turn_id"),
            source=source or payload.get("source") or "unknown",
            kind=kind,
            target_scope=target_scope,
            target_table=target_table,
            target_path=payload.get("target_path"),
            target_id=payload.get("target_id"),
            before=before_for_log,
            after=after,
            confidence=payload.get("confidence"),
            notes=payload.get("notes"),
            delta_set_id=delta_set_id or payload.get("delta_set_id"),
        )
        return delta_id

    async def reverse_delta(self, delta_id: str) -> None:
        async with self._txn() as conn:
            await self._reverse_delta_on_conn(conn, delta_id)

    async def _reverse_delta_on_conn(
        self,
        conn: aiosqlite.Connection,
        delta_id: str,
    ) -> None:
        delta = await get_delta(conn, delta_id)
        if delta.reversed_at is not None:
            raise StateStoreError(f"delta {delta_id} already reversed at {delta.reversed_at}")
        if delta.target_scope == "campaign-sqlite":
            await reverse_sqlite_delta(conn, delta)
        elif delta.target_scope in ("library", "campaign-file"):
            await self._reverse_file_delta(conn, delta)
        else:
            raise StateStoreError(f"cannot reverse delta with scope {delta.target_scope!r}")
        await mark_reversed(conn, delta_id)

    async def _reverse_file_delta(
        self,
        conn: aiosqlite.Connection,
        delta: DeltaRecord,
    ) -> None:
        if delta.target_path is None:
            raise StateStoreError(f"file delta {delta.id} has no target_path")
        target = Path(delta.target_path)
        if delta.before is None:
            # The file did not exist before — delete it now.
            if target.exists():
                target.unlink()
            if delta.target_scope == "library" and delta.target_id:
                await delete_library_index_row(conn, delta.target_id)
            if delta.target_scope == "campaign-file" and delta.target_id:
                await delete_campaign_content_row(conn, delta.target_id)
            return

        before = delta.before
        fm = before.get("frontmatter") if isinstance(before, dict) else None
        body = (before.get("body") if isinstance(before, dict) else None) or ""

        if target.suffix == ".yaml":
            write_yaml(target, fm if fm is not None else before)
        else:
            write_markdown(target, ParsedDocument(frontmatter=fm or {}, body=body))

        if delta.target_scope == "library" and delta.target_id:
            await upsert_library_index(
                conn,
                data_root=self._data_root,
                library_id=delta.target_id,
                path=target,
                frontmatter=fm or {},
                body=body,
            )
        elif delta.target_scope == "campaign-file" and delta.target_id:
            cid = campaign_id_for_path(self._data_root, target) or ""
            kind = _content_kind_from_id(delta.target_id)
            await upsert_campaign_content_index(
                conn,
                data_root=self._data_root,
                campaign_id=cid,
                composite_id=delta.target_id,
                kind=kind,
                entity_subkind=None,
                asset_id=None,
                path=target,
                frontmatter=fm,
                body=body,
            )

    # ------------------------------------------------------------------
    # Delta sets (swipes / alternates)
    # ------------------------------------------------------------------

    async def apply_delta_set(
        self,
        *,
        deltas: list[Any],
        delta_set_id: str,
        campaign_id: str | None,
        turn_id: str | None,
        source: str,
    ) -> list[DeltaRecord]:
        """Apply every delta atomically, tagging each with ``delta_set_id``."""
        records: list[DeltaRecord] = []
        async with self._txn() as conn:
            for d in deltas:
                delta_id = await self._apply_delta_on_conn(
                    conn,
                    delta=d,
                    source=source,
                    turn_id=turn_id,
                    campaign_id=campaign_id,
                    delta_set_id=delta_set_id,
                )
                rec = await get_delta(conn, delta_id)
                records.append(rec)
        return records

    async def rewind_delta_set(
        self,
        delta_set_id: str,
        *,
        campaign_id: str,
    ) -> list[DeltaRecord]:
        """LIFO-reverse every non-reversed delta tagged with ``delta_set_id``."""
        reversed_records: list[DeltaRecord] = []
        async with self._txn() as conn:
            cur = await conn.execute(
                """
                SELECT * FROM deltas
                WHERE campaign_id = ?
                  AND delta_set_id = ? AND reversed_at IS NULL
                ORDER BY applied_at DESC, rowid DESC
                """,
                (campaign_id, delta_set_id),
            )
            rows = await cur.fetchall()
            await cur.close()
            for row in rows:
                delta_id = row["id"]
                await self._reverse_delta_on_conn(conn, delta_id)
                reversed_records.append(await get_delta(conn, delta_id))
        return reversed_records

    async def re_activate_delta_set(
        self,
        *,
        delta_set_id: str,
        campaign_id: str,
    ) -> int:
        """Re-apply every previously-reversed delta in a set (oldest first)."""
        count = 0
        async with self._txn() as conn:
            cur = await conn.execute(
                """
                SELECT * FROM deltas
                WHERE campaign_id = ?
                  AND delta_set_id = ? AND reversed_at IS NOT NULL
                ORDER BY applied_at ASC, rowid ASC
                """,
                (campaign_id, delta_set_id),
            )
            rows = await cur.fetchall()
            await cur.close()
            for row in rows:
                rec = DeltaRecord.from_row(row)
                if rec.target_scope == "campaign-sqlite" and rec.target_table:
                    after = rec.after or {}
                    if after:
                        await upsert_row(conn, table=rec.target_table, values=after)
                await conn.execute(
                    "UPDATE deltas SET reversed_at = NULL WHERE id = ?",
                    (rec.id,),
                )
                count += 1
        return count

    async def swap_delta_set(
        self,
        *,
        rewind_set_id: str,
        apply_deltas: list[Any] | None,
        apply_set_id: str,
        campaign_id: str,
        turn_id: str | None,
        source: str,
    ) -> SwapResult:
        """Atomic rewind of one set followed by application of another."""
        rewound: list[DeltaRecord] = []
        applied: list[DeltaRecord] = []
        async with self._txn() as conn:
            cur = await conn.execute(
                """
                SELECT id FROM deltas
                WHERE campaign_id = ?
                  AND delta_set_id = ? AND reversed_at IS NULL
                ORDER BY applied_at DESC, rowid DESC
                """,
                (campaign_id, rewind_set_id),
            )
            rewind_rows = await cur.fetchall()
            await cur.close()
            for row in rewind_rows:
                await self._reverse_delta_on_conn(conn, row["id"])
                rewound.append(await get_delta(conn, row["id"]))

            if apply_deltas:
                for d in apply_deltas:
                    delta_id = await self._apply_delta_on_conn(
                        conn,
                        delta=d,
                        source=source,
                        turn_id=turn_id,
                        campaign_id=campaign_id,
                        delta_set_id=apply_set_id,
                    )
                    applied.append(await get_delta(conn, delta_id))
            else:
                cur = await conn.execute(
                    """
                    SELECT * FROM deltas
                    WHERE campaign_id = ?
                      AND delta_set_id = ? AND reversed_at IS NOT NULL
                    ORDER BY applied_at ASC, rowid ASC
                    """,
                    (campaign_id, apply_set_id),
                )
                rows = await cur.fetchall()
                await cur.close()
                for row in rows:
                    rec = DeltaRecord.from_row(row)
                    if rec.target_scope == "campaign-sqlite" and rec.target_table:
                        after = rec.after or {}
                        if after:
                            await upsert_row(conn, table=rec.target_table, values=after)
                    await conn.execute(
                        "UPDATE deltas SET reversed_at = NULL WHERE id = ?",
                        (rec.id,),
                    )
                    applied.append(await get_delta(conn, rec.id))
        return SwapResult(rewound=rewound, applied=applied)

    async def swap_turn_deltas(
        self,
        *,
        campaign_id: str,
        turn_id: str | None,
        deltas: list[Any],
        source: str,
        review_deltas: list[Any] | None = None,
    ) -> SwapResult:
        """Atomically replace a turn's recorded effects with re-extracted ones.

        In one transaction: rejects the turn's stale pending review rows
        (their proposals were extracted from text the caller is replacing —
        approving them later would apply old-text state, mirroring the
        cascade-delete path's rejection), LIFO-reverses every non-reversed
        delta recorded for ``turn_id`` — skipping queued-but-unapplied review
        rows, which were never applied to their target — applies the
        replacement ``deltas``, and queues ``review_deltas`` for review. Any
        failure rolls the whole swap back (file targets touched by a reversal
        are snapshot/restored), so the campaign is left exactly as it was
        before the call (#583).

        ``turn_id=None`` means the post being retconned has no turn: nothing
        is reversed or rejected and the replacements are applied atomically.
        """
        rewound: list[DeltaRecord] = []
        applied: list[DeltaRecord] = []
        queued_review_ids: list[str] = []
        rejected_review_ids: list[str] = []
        # File targets are snapshot as the reversal walk reaches them; the
        # stack restores them (LIFO) after a failed transaction rolls back.
        with contextlib.ExitStack() as file_snapshots:
            async with self._txn() as conn:
                rows: list[aiosqlite.Row] = []
                if turn_id is not None:
                    cur = await conn.execute(
                        """
                        SELECT rq.id AS review_id, rq.delta_id AS delta_id
                        FROM review_queue rq
                        JOIN deltas d ON d.id = rq.delta_id
                        WHERE rq.status = 'pending'
                          AND d.campaign_id = ? AND d.turn_id = ?
                        """,
                        (campaign_id, turn_id),
                    )
                    stale = list(await cur.fetchall())
                    await cur.close()
                    for stale_row in stale:
                        await conn.execute(
                            """
                            UPDATE review_queue
                            SET status = 'rejected', reviewed_at = ?, reviewer_notes = ?
                            WHERE id = ?
                            """,
                            (now_iso(), "superseded by retcon", stale_row["review_id"]),
                        )
                        # Never applied, so marking reversed is pure bookkeeping:
                        # it drops the row from active-delta queries.
                        await mark_reversed(conn, stale_row["delta_id"])
                        rejected_review_ids.append(stale_row["review_id"])
                    cur = await conn.execute(
                        """
                        SELECT * FROM deltas
                        WHERE campaign_id = ? AND turn_id = ? AND reversed_at IS NULL
                          AND id NOT IN (
                            SELECT delta_id FROM review_queue WHERE status = 'pending'
                          )
                        ORDER BY applied_at DESC, rowid DESC
                        """,
                        (campaign_id, turn_id),
                    )
                    rows = list(await cur.fetchall())
                    await cur.close()
                for row in rows:
                    rec = DeltaRecord.from_row(row)
                    if rec.target_scope in ("library", "campaign-file") and rec.target_path:
                        file_snapshots.enter_context(snapshot_file_before(Path(rec.target_path)))
                    await self._reverse_delta_on_conn(conn, rec.id)
                    rewound.append(await get_delta(conn, rec.id))
                for d in deltas:
                    delta_id = await self._apply_delta_on_conn(
                        conn,
                        delta=d,
                        source=source,
                        turn_id=turn_id,
                        campaign_id=campaign_id,
                    )
                    applied.append(await get_delta(conn, delta_id))
                for d in review_deltas or []:
                    queued_review_ids.append(
                        await self._queue_for_review_on_conn(
                            conn, delta=d, source=source, campaign_id=campaign_id
                        )
                    )
        return SwapResult(
            rewound=rewound,
            applied=applied,
            queued_review_ids=queued_review_ids,
            rejected_review_ids=rejected_review_ids,
        )

    async def set_current_alternate_delta_set(
        self,
        *,
        campaign_id: str,
        post_id: str,
        delta_set_id: str,
    ) -> None:
        """Record which delta set is the current primary for ``post_id``."""
        async with self._txn() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO current_alternate_delta_sets
                  (campaign_id, post_id, delta_set_id, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (campaign_id, post_id, delta_set_id, now_iso()),
            )

    async def clear_current_alternate_delta_set(
        self,
        *,
        campaign_id: str,
        post_id: str,
    ) -> None:
        async with self._txn() as conn:
            await conn.execute(
                """
                DELETE FROM current_alternate_delta_sets
                WHERE campaign_id = ? AND post_id = ?
                """,
                (campaign_id, post_id),
            )

    async def current_delta_set_for(
        self,
        *,
        post_id: str | None,
        campaign_id: str,
        set_id: str | None = None,
    ) -> str | None:
        """Look up the primary delta set for ``post_id``."""
        async with self._db.acquire() as conn:
            if post_id is not None:
                cur = await conn.execute(
                    """
                    SELECT delta_set_id FROM current_alternate_delta_sets
                    WHERE campaign_id = ? AND post_id = ?
                    """,
                    (campaign_id, post_id),
                )
                row = await cur.fetchone()
                await cur.close()
                return row["delta_set_id"] if row else None
            if set_id is None:
                return None
            cur = await conn.execute(
                """
                SELECT delta_set_id FROM current_alternate_delta_sets
                WHERE campaign_id = ? AND delta_set_id = ?
                LIMIT 1
                """,
                (campaign_id, set_id),
            )
            row = await cur.fetchone()
            await cur.close()
            return row["delta_set_id"] if row else None

    # ------------------------------------------------------------------
    # Review queue
    # ------------------------------------------------------------------

    async def queue_for_review(
        self,
        *,
        delta: dict | Any,
        source: str | None = None,
        campaign_id: str | None = None,
    ) -> str:
        """Persist a low-confidence delta for human review without applying it."""
        async with self._txn() as conn:
            return await self._queue_for_review_on_conn(
                conn, delta=delta, source=source, campaign_id=campaign_id
            )

    async def _queue_for_review_on_conn(
        self,
        conn: aiosqlite.Connection,
        *,
        delta: dict | Any,
        source: str | None = None,
        campaign_id: str | None = None,
    ) -> str:
        """Queue one delta for review inside an already-open transaction."""
        payload = _delta_to_dict(delta)
        delta_id = await insert_delta(
            conn,
            campaign_id=campaign_id or payload.get("campaign_id"),
            turn_id=payload.get("turn_id"),
            source=source or payload.get("source") or "unknown",
            kind=payload.get("kind") or "other",
            target_scope=payload.get("target_scope") or "campaign-sqlite",
            target_table=payload.get("target_table"),
            target_path=payload.get("target_path"),
            target_id=payload.get("target_id"),
            before=payload.get("before"),
            after=payload.get("after"),
            confidence=payload.get("confidence"),
            notes="queued for review",
        )
        # The delta is logged but not applied — caller approves later by
        # calling apply via approve_review_item().
        return await _queue_for_review(conn, delta_id=delta_id, campaign_id=campaign_id)

    async def approve_review_item(self, review_id: str) -> str:
        """Apply a previously-queued delta. Returns the delta id."""
        async with self._txn() as conn:
            cur = await conn.execute(
                "SELECT delta_id, status FROM review_queue WHERE id = ?",
                (review_id,),
            )
            row = await cur.fetchone()
            await cur.close()
            if row is None:
                raise NotFoundError(f"review item {review_id} not found")
            if row["status"] != "pending":
                raise StateStoreError(f"review item {review_id} already {row['status']}")
            delta = await get_delta(conn, row["delta_id"])
            if delta.target_scope == "campaign-sqlite" and delta.target_table:
                await upsert_row(conn, table=delta.target_table, values=delta.after or {})
            await conn.execute(
                """
                UPDATE review_queue
                SET status = 'approved', reviewed_at = ?
                WHERE id = ?
                """,
                (now_iso(), review_id),
            )
        return delta.id

    async def reject_review_item(self, review_id: str, *, notes: str = "") -> None:
        async with self._txn() as conn:
            cur = await conn.execute(
                "SELECT delta_id FROM review_queue WHERE id = ?",
                (review_id,),
            )
            row = await cur.fetchone()
            await cur.close()
            if row is None:
                raise NotFoundError(f"review item {review_id} not found")
            await conn.execute(
                """
                UPDATE review_queue
                SET status = 'rejected', reviewed_at = ?, reviewer_notes = ?
                WHERE id = ?
                """,
                (now_iso(), notes, review_id),
            )
            # Mark the unapplied delta as reversed so it doesn't appear in
            # active-deltas queries.
            await mark_reversed(conn, row["delta_id"])

    async def pending_review_delta_ids(self, campaign_id: str) -> set[str]:
        """Delta ids that are queued for review but not yet applied.

        These rows live in the delta table (so ``get_delta_log`` returns them)
        but were never applied to their target, so they must not be reversed —
        reversing a never-applied delta with ``before=None`` would delete a live
        row. Rejected review items are already ``mark_reversed``, so only
        ``pending`` rows need filtering.
        """
        async with self._db.acquire() as conn:
            cur = await conn.execute(
                "SELECT delta_id FROM review_queue WHERE campaign_id = ? AND status = 'pending'",
                (campaign_id,),
            )
            rows = await cur.fetchall()
            await cur.close()
        return {row["delta_id"] for row in rows}

    async def pending_review_items(self, campaign_id: str) -> list[tuple[str, str | None]]:
        """``(review_id, turn_id)`` for each pending review item in the campaign.

        Joins the review queue to the delta table so callers (cascade delete)
        can reject the review rows that belong to turns being removed.
        """
        async with self._db.acquire() as conn:
            cur = await conn.execute(
                """
                SELECT rq.id AS review_id, d.turn_id AS turn_id
                FROM review_queue rq
                JOIN deltas d ON d.id = rq.delta_id
                WHERE rq.campaign_id = ? AND rq.status = 'pending'
                """,
                (campaign_id,),
            )
            rows = await cur.fetchall()
            await cur.close()
        return [(row["review_id"], row["turn_id"]) for row in rows]

    async def get_delta_log(
        self,
        *,
        campaign_id: str | None = None,
        since: datetime | str | None = None,
        turn_id: str | None = None,
        include_reversed: bool = True,
        limit: int | None = None,
    ) -> list[DeltaRecord]:
        since_str = since.isoformat() if isinstance(since, datetime) else since
        async with self._db.acquire() as conn:
            return await list_deltas(
                conn,
                campaign_id=campaign_id,
                since=since_str,
                turn_id=turn_id,
                include_reversed=include_reversed,
                limit=limit,
            )

    async def count_deltas(self, campaign_id: str) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS cnt FROM deltas WHERE campaign_id = ?",
            (campaign_id,),
        )
        return int(row["cnt"]) if row else 0


# ---------------------------------------------------------------------------
# Delta payload helpers
# ---------------------------------------------------------------------------


def _delta_to_dict(value: Any) -> dict:
    if value is None:
        raise StateStoreError("delta is None")
    if isinstance(value, dict):
        return value
    # Try to interoperate with pydantic models (StateDelta).
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    raise StateStoreError(f"cannot convert delta of type {type(value).__name__}")


async def _capture_current_row(
    conn: aiosqlite.Connection,
    table: str,
    after: dict,
) -> dict | None:
    """Look up the current row for the PK in ``after``; ``None`` if absent.

    ``table`` is interpolated into the SELECT — safe today because
    ``primary_key_columns`` returns ``None`` for any table that isn't in
    the hard-coded ``_PRIMARY_KEYS`` allowlist, so the gate below already
    rejects untrusted names. The explicit membership check makes that
    invariant local to this function rather than relying on a transitive
    property of a helper in another module.
    """
    pk = primary_key_columns(table)
    if pk is None:
        return None
    if table not in _CAPTURE_SAFE_TABLES:
        # Belt-and-braces: should be unreachable because primary_key_columns
        # returns None for anything outside this set. If a future edit adds
        # a table to _PRIMARY_KEYS but forgets to extend _CAPTURE_SAFE_TABLES,
        # we'd rather fail closed here than silently interpolate the new
        # name. Mismatch is a programming error, not a runtime contingency.
        raise StateStoreError(f"refusing to capture from non-allowlisted table {table!r}")
    if not all(col in after for col in pk):
        return None
    where = " AND ".join(f"{c} = ?" for c in pk)
    params = tuple(after[c] for c in pk)
    async with conn.execute(
        f"SELECT * FROM {table} WHERE {where}",
        params,
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return dict(row)


# Tables _capture_current_row is allowed to SELECT from. Must be a
# superset of the keys in delta_log._PRIMARY_KEYS for the check above
# to pass; kept as a separate constant so a future edit to either has
# to consciously mirror it.
_CAPTURE_SAFE_TABLES: frozenset[str] = frozenset(
    {
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
)


def _content_kind_from_id(composite_id: str) -> str:
    """Infer ``kind`` for ``campaign_content_index`` from a composite id."""
    parts = composite_id.split("/")
    # campaigns/<id>/<kind>/...
    if len(parts) >= 3 and parts[0] == "campaigns":
        return parts[2].rstrip("s") if parts[2].endswith("s") else parts[2]
    return "unknown"
