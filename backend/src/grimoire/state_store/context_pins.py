"""ContextPinStore — context inspector pin/exclude rows.

Extracted from :class:`~grimoire.state_store.store.StateStore` (#521). Owns
the ``context_pins`` table and the turn-counting TTL expiry that reads
``turn_audits``.
"""

from __future__ import annotations

from grimoire.storage import Database
from grimoire.util import new_id, now_iso


class ContextPinStore:
    def __init__(self, *, db: Database) -> None:
        self._db = db

    async def write_pin(
        self,
        *,
        campaign_id: str,
        kind: str,
        target_source_id: str | None = None,
        target_entity_kind: str | None = None,
        target_entity_id: str | None = None,
        created_at_turn_id: str | None = None,
        ttl_turns: int | None = None,
        created_by: str = "user",
        pin_id: str | None = None,
    ) -> str:
        """Insert a context pin/exclude row.

        TTL is stored as an integer turn count alongside
        ``created_at_turn_id``. Expiry is resolved at read time by counting
        turns elapsed between ``created_at_turn_id`` and the current turn id
        using ``turn_audits.created_at`` ordering — turn ids themselves are
        random hex and not lexicographically comparable.
        """
        if kind not in ("pin", "exclude"):
            raise ValueError(f"context pin kind must be 'pin' or 'exclude', got {kind!r}")
        target_kind = "source" if target_source_id else "entity"
        if target_kind == "entity" and not (target_entity_kind and target_entity_id):
            raise ValueError("entity-targeted pin requires both entity_kind and entity_id")
        if ttl_turns is not None and ttl_turns <= 0:
            raise ValueError(f"ttl_turns must be positive when set, got {ttl_turns}")
        pid = pin_id or new_id("ctx_pin", length=16)
        await self._db.execute(
            """
            INSERT INTO context_pins (
                id, campaign_id, kind, target_kind,
                target_source_id, target_entity_kind, target_entity_id,
                created_at, created_by, created_at_turn_id, ttl_turns
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                campaign_id,
                kind,
                target_kind,
                target_source_id,
                target_entity_kind,
                target_entity_id,
                now_iso(),
                created_by,
                created_at_turn_id,
                ttl_turns,
            ),
        )
        return pid

    async def list_active(
        self,
        *,
        campaign_id: str,
        current_turn_id: str | None = None,
    ) -> list[dict]:
        """Return every active pin/exclude for the campaign.

        Active = ``cleared_at IS NULL`` AND TTL not exhausted. TTL is
        exhausted when the number of canonical turns recorded in
        ``turn_audits`` between ``created_at_turn_id`` (exclusive) and
        ``current_turn_id`` (inclusive) is >= ``ttl_turns``. Rows with
        ``ttl_turns IS NULL`` never expire.
        """
        rows = await self._db.fetchall(
            """
            SELECT * FROM context_pins
            WHERE campaign_id = ? AND cleared_at IS NULL
            ORDER BY created_at
            """,
            (campaign_id,),
        )
        out: list[dict] = []
        for row in rows:
            ttl = row["ttl_turns"]
            if ttl is None or current_turn_id is None or row["created_at_turn_id"] is None:
                out.append(dict(row))
                continue
            elapsed = await self._turns_elapsed(
                campaign_id=campaign_id,
                from_turn_id=row["created_at_turn_id"],
                to_turn_id=current_turn_id,
            )
            if elapsed is None or elapsed < int(ttl):
                out.append(dict(row))
        return out

    async def _turns_elapsed(
        self,
        *,
        campaign_id: str,
        from_turn_id: str,
        to_turn_id: str,
    ) -> int | None:
        """Count canonical turns between two turn ids.

        Uses ``turn_audits.created_at`` ordering. Returns ``None`` when
        either turn id is unknown to the audit table (caller treats this
        as "TTL not yet exhausted" so a missing audit can't silently
        evict pins).
        """
        rows = await self._db.fetchall(
            """
            SELECT turn_id, created_at FROM turn_audits
            WHERE campaign_id = ?
              AND turn_id IN (?, ?)
            """,
            (campaign_id, from_turn_id, to_turn_id),
        )
        by_id = {row["turn_id"]: row["created_at"] for row in rows}
        if from_turn_id not in by_id or to_turn_id not in by_id:
            return None
        start = by_id[from_turn_id]
        end = by_id[to_turn_id]
        if end < start:
            return 0
        count_row = await self._db.fetchone(
            """
            SELECT COUNT(*) AS n FROM turn_audits
            WHERE campaign_id = ?
              AND created_at > ? AND created_at <= ?
            """,
            (campaign_id, start, end),
        )
        return int(count_row["n"]) if count_row else 0

    async def mark_cleared(
        self,
        *,
        pin_id: str,
        cleared_by: str = "user",
    ) -> None:
        await self._db.execute(
            "UPDATE context_pins SET cleared_at = ?, cleared_by = ? WHERE id = ?",
            (now_iso(), cleared_by, pin_id),
        )
