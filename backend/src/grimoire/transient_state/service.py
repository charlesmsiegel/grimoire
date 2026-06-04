"""TransientStateService — per-field ephemeral state with supersession.

Spec: docs/superpowers/specs/2026-05-19-transient-state-design.md

Routing rules:
    - Write priority: user > mechanics > extractor:reviewed > extractor:auto.
    - Losing write is preserved via superseded_by (inserted as already-superseded).
    - Reads filter on superseded_by IS NULL and
      (expires_at IS NULL OR expires_at > now()).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from grimoire.transient_state.config import TransientStateConfig
from grimoire.transient_state.privacy import (
    PrivacyResolver,
    apply_privacy_filter,
)
from grimoire.types.transient import (
    EntityKind,
    ObserverKind,
    Provenance,
    TransientConflict,
    TransientValue,
    _ProvenanceMechanics,
)
from grimoire.util import now_iso

_TABLE = {
    EntityKind.CHARACTER: "transient_character_state",
    EntityKind.LOCATION: "transient_location_state",
    EntityKind.FACTION: "transient_faction_state",
    EntityKind.SCENE: "transient_scene_state",
}


def _priority_for(provenance: Provenance | _ProvenanceMechanics | str) -> int:
    raw = provenance.value if hasattr(provenance, "value") else str(provenance)
    if raw.startswith("user:"):
        return 3
    if raw.startswith("mechanics:"):
        return 2
    if raw == "extractor:reviewed":
        return 2
    if raw == "extractor:auto":
        return 1
    return 0


def _parse_dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    return datetime.fromisoformat(s)


def _parse_provenance(raw: str) -> Provenance | _ProvenanceMechanics:
    if raw.startswith("mechanics:"):
        return _ProvenanceMechanics(raw.split(":", 1)[1])
    return Provenance(raw)


def _row_to_value(row: Any) -> TransientValue:
    return TransientValue(
        id=row["id"],
        entity_id=row["entity_id"],
        field=row["field"],
        value=json.loads(row["value"]),
        provenance=_parse_provenance(row["provenance"]),
        confidence=row["confidence"],
        source_post_id=row["source_post_id"],
        created_at=_parse_dt(row["created_at"]) or datetime.now(UTC),
        expires_at=_parse_dt(row["expires_at"]),
        in_game_at=_parse_dt(row["in_game_at"]),
        decayed=False,
    )


class TransientStateService:
    """Per-field ephemeral state with supersession + lazy decay."""

    def __init__(
        self,
        store: Any,
        *,
        config: TransientStateConfig | None = None,
        privacy_resolver: PrivacyResolver | None = None,
    ) -> None:
        self.store = store
        self.config = config or TransientStateConfig()
        self.privacy_resolver = privacy_resolver

    async def get(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: str | None = None,
        *,
        for_observer: ObserverKind | None = None,
    ) -> TransientValue | dict[str, TransientValue] | None:
        table = _TABLE[entity_kind]
        now_str = now_iso()
        if field is None:
            rows = await self.store.db.fetchall(
                f"SELECT * FROM {table} "
                "WHERE campaign_id=? AND entity_id=? "
                "AND superseded_by IS NULL "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (campaign_id, entity_id, now_str),
            )
            bundle = {r["field"]: _row_to_value(r) for r in rows}
            if for_observer is not None:
                bundle = apply_privacy_filter(
                    self.privacy_resolver,
                    entity_kind,
                    entity_id,
                    bundle,
                    campaign_id=campaign_id,
                    observer=for_observer,
                )
            return bundle
        row = await self.store.db.fetchone(
            f"SELECT * FROM {table} "
            "WHERE campaign_id=? AND entity_id=? AND field=? "
            "AND superseded_by IS NULL "
            "AND (expires_at IS NULL OR expires_at > ?)",
            (campaign_id, entity_id, field, now_str),
        )
        if row is None:
            return None
        value = _row_to_value(row)
        if for_observer is not None:
            filtered = apply_privacy_filter(
                self.privacy_resolver,
                entity_kind,
                entity_id,
                {value.field: value},
                campaign_id=campaign_id,
                observer=for_observer,
            )
            return filtered.get(value.field)
        return value

    async def get_bulk(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_ids: Sequence[str],
        fields: Sequence[str] | None = None,
        *,
        for_observer: ObserverKind | None = None,
    ) -> dict[str, dict[str, TransientValue]]:
        if not entity_ids:
            return {}
        table = _TABLE[entity_kind]
        id_placeholders = ",".join("?" * len(entity_ids))
        sql = (
            f"SELECT * FROM {table} "
            f"WHERE campaign_id=? AND entity_id IN ({id_placeholders}) "
            "AND superseded_by IS NULL "
            "AND (expires_at IS NULL OR expires_at > ?)"
        )
        params: list[Any] = [campaign_id, *entity_ids, now_iso()]
        if fields:
            field_placeholders = ",".join("?" * len(fields))
            sql += f" AND field IN ({field_placeholders})"
            params.extend(fields)
        rows = await self.store.db.fetchall(sql, tuple(params))
        result: dict[str, dict[str, TransientValue]] = {eid: {} for eid in entity_ids}
        for r in rows:
            result.setdefault(r["entity_id"], {})[r["field"]] = _row_to_value(r)
        if for_observer is not None:
            result = {
                eid: apply_privacy_filter(
                    self.privacy_resolver,
                    entity_kind,
                    eid,
                    bundle,
                    campaign_id=campaign_id,
                    observer=for_observer,
                )
                for eid, bundle in result.items()
            }
        return result

    async def set(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: str,
        value: Any,
        *,
        provenance: Provenance | _ProvenanceMechanics,
        confidence: float = 1.0,
        source_post_id: str | None = None,
        in_game_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> TransientValue:
        table = _TABLE[entity_kind]
        provenance_str = provenance.value if hasattr(provenance, "value") else str(provenance)
        async with self.store.db.acquire() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                async with conn.execute(
                    f"SELECT * FROM {table} "
                    "WHERE campaign_id=? AND entity_id=? AND field=? "
                    "AND superseded_by IS NULL",
                    (campaign_id, entity_id, field),
                ) as cursor:
                    current = await cursor.fetchone()
                cursor = await conn.execute(
                    f"INSERT INTO {table} "
                    "(campaign_id, entity_id, field, value, provenance, "
                    " source_post_id, confidence, created_at, expires_at, "
                    " superseded_by, in_game_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                    (
                        campaign_id,
                        entity_id,
                        field,
                        json.dumps(value),
                        provenance_str,
                        source_post_id,
                        confidence,
                        now_iso(),
                        expires_at.isoformat() if expires_at else None,
                        (in_game_at or datetime.now(UTC)).isoformat(),
                    ),
                )
                new_id = cursor.lastrowid
                await cursor.close()
                if current is not None:
                    if _priority_for(provenance) >= _priority_for(current["provenance"]):
                        await conn.execute(
                            f"UPDATE {table} SET superseded_by=? WHERE id=?",
                            (new_id, current["id"]),
                        )
                    else:
                        await conn.execute(
                            f"UPDATE {table} SET superseded_by=? WHERE id=?",
                            (current["id"], new_id),
                        )
                async with conn.execute(
                    f"SELECT * FROM {table} WHERE id=?",
                    (new_id,),
                ) as cursor:
                    new_row = await cursor.fetchone()
            except Exception:
                await conn.execute("ROLLBACK")
                raise
            else:
                await conn.execute("COMMIT")
        return _row_to_value(new_row)

    async def clear(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: str | None = None,
        *,
        reason: str = "user:reset",
    ) -> None:
        table = _TABLE[entity_kind]
        now_str = now_iso()
        if field is None:
            await self.store.db.execute(
                f"UPDATE {table} SET expires_at=? "
                "WHERE campaign_id=? AND entity_id=? "
                "AND superseded_by IS NULL "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (now_str, campaign_id, entity_id, now_str),
            )
        else:
            await self.store.db.execute(
                f"UPDATE {table} SET expires_at=? "
                "WHERE campaign_id=? AND entity_id=? AND field=? "
                "AND superseded_by IS NULL "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (now_str, campaign_id, entity_id, field, now_str),
            )

    async def history(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: str,
        limit: int = 20,
    ) -> list[TransientValue]:
        table = _TABLE[entity_kind]
        rows = await self.store.db.fetchall(
            f"SELECT * FROM {table} "
            "WHERE campaign_id=? AND entity_id=? AND field=? "
            "ORDER BY id DESC LIMIT ?",
            (campaign_id, entity_id, field, limit),
        )
        return [_row_to_value(r) for r in rows]

    async def list_conflicts(
        self,
        campaign_id: str,
        *,
        within_posts: int | None = None,
    ) -> list[TransientConflict]:
        """List unresolved conflicts (extractor write lost to a user write).

        ``within_posts`` caps how many distinct source_post_ids back to look,
        defaulting to ``config.conflict_window_posts``. Posts-back is
        approximated by ranking distinct ``source_post_id`` values from the
        losing rows in descending insert order — full post-counter integration
        is deferred (see plan B2).
        """
        window = within_posts if within_posts is not None else self.config.conflict_window_posts
        conflicts: list[TransientConflict] = []
        for table in _TABLE.values():
            sql = (
                f"SELECT loser.id AS l_id, loser.entity_id AS l_entity, "
                "loser.field AS l_field, loser.value AS l_value, "
                "loser.provenance AS l_prov, loser.confidence AS l_conf, "
                "loser.source_post_id AS l_src, loser.created_at AS l_ca, "
                "loser.expires_at AS l_exp, loser.in_game_at AS l_iga, "
                "winner.id AS w_id, winner.entity_id AS w_entity, "
                "winner.field AS w_field, winner.value AS w_value, "
                "winner.provenance AS w_prov, winner.confidence AS w_conf, "
                "winner.source_post_id AS w_src, winner.created_at AS w_ca, "
                "winner.expires_at AS w_exp, winner.in_game_at AS w_iga "
                f"FROM {table} loser "
                f"JOIN {table} winner ON loser.superseded_by = winner.id "
                "WHERE loser.campaign_id=? "
                "AND loser.provenance LIKE 'extractor:%' "
                "AND winner.provenance LIKE 'user:%' "
                "ORDER BY loser.id DESC"
            )
            rows = await self.store.db.fetchall(sql, (campaign_id,))
            seen_post_ids: list[str] = []
            for r in rows:
                if window is not None and window >= 0:
                    src = r["l_src"]
                    if src is not None and src not in seen_post_ids:
                        if len(seen_post_ids) >= window:
                            continue
                        seen_post_ids.append(src)
                losing = TransientValue(
                    id=r["l_id"],
                    entity_id=r["l_entity"],
                    field=r["l_field"],
                    value=json.loads(r["l_value"]),
                    provenance=_parse_provenance(r["l_prov"]),
                    confidence=r["l_conf"],
                    source_post_id=r["l_src"],
                    created_at=_parse_dt(r["l_ca"]) or datetime.now(UTC),
                    expires_at=_parse_dt(r["l_exp"]),
                    in_game_at=_parse_dt(r["l_iga"]),
                    decayed=False,
                )
                current = TransientValue(
                    id=r["w_id"],
                    entity_id=r["w_entity"],
                    field=r["w_field"],
                    value=json.loads(r["w_value"]),
                    provenance=_parse_provenance(r["w_prov"]),
                    confidence=r["w_conf"],
                    source_post_id=r["w_src"],
                    created_at=_parse_dt(r["w_ca"]) or datetime.now(UTC),
                    expires_at=_parse_dt(r["w_exp"]),
                    in_game_at=_parse_dt(r["w_iga"]),
                    decayed=False,
                )
                conflicts.append(TransientConflict(current=current, losing=losing))
        return conflicts

    async def promote_to_fact(
        self,
        campaign_id: str,
        entity_kind: EntityKind,
        entity_id: str,
        field: str,
        *,
        evidence: str,
        turn_id: str,
        continuity: Any | None = None,
    ) -> tuple[str, int]:
        """Promote a transient row into a canonical Continuity fact.

        Returns ``(fact_id, transient_id)``. The transient row is then
        expired (cleared) so subsequent reads pick up the Continuity fact
        as the source of truth.
        """
        current = await self.get(campaign_id, entity_kind, entity_id, field)
        if current is None:
            raise ValueError(
                f"no current transient value for {entity_kind.value}/{entity_id}/{field}"
            )
        if not isinstance(current, TransientValue):
            raise TypeError("promote_to_fact requires a single-field get")
        fact_id = ""
        if continuity is not None:
            from grimoire.continuity.types import (
                Fact,
                FactSource,
                FactSubject,
                InGameTime,
            )

            subject_kwargs: dict[str, Any] = {}
            if entity_kind == EntityKind.CHARACTER:
                subject_kwargs["character_ids"] = [entity_id]
            elif entity_kind == EntityKind.LOCATION:
                subject_kwargs["location_ids"] = [entity_id]
            elif entity_kind == EntityKind.FACTION:
                subject_kwargs["faction_ids"] = [entity_id]
            fact = Fact(
                id=f"f_{entity_id}_{field}_{turn_id}",
                text=f"{entity_id} has {field}: {current.value}",
                established_in_post=current.source_post_id or turn_id,
                established_at_in_game=InGameTime(day_count=0),
                confidence=current.confidence,
                source=FactSource.INFERRED,
                about=FactSubject(scope="public", **subject_kwargs),
                tags=[evidence] if evidence else [],
            )
            fact_id = await continuity.add_fact(fact, source="transient_state:promote")
        await self.clear(
            campaign_id,
            entity_kind,
            entity_id,
            field=field,
            reason="promote_to_fact",
        )
        return fact_id, current.id

    async def supersede_with_fact(
        self,
        transient_id: int,
        fact_id: str,
        *,
        entity_kind: EntityKind,
    ) -> None:
        """Mark a transient row as superseded because a fact carries it now."""
        table = _TABLE[entity_kind]
        await self.store.db.execute(
            f"UPDATE {table} SET expires_at=? WHERE id=? AND superseded_by IS NULL",
            (now_iso(), transient_id),
        )
