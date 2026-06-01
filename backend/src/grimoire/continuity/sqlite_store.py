"""SQLite-backed implementations of the Continuity persistence seams.

Binds to the `facts`, `commitments`, `knowledge_state` and
`contradiction_reports` tables defined by the State Store migrations. A
single store instance is scoped to one campaign_id so multi-tenant
queries always have a where clause.

This complements the in-memory store in :mod:`grimoire.continuity.store`:
the in-memory version is used by unit tests and as a fall-back; this
SQLite version is what the Orchestrator wires up at runtime.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
from collections.abc import Iterable

from grimoire.continuity.protocols import ContinuityStore
from grimoire.continuity.types import (
    Commitment,
    CommitmentId,
    CommitmentKind,
    CommitmentStatus,
    ContradictionCandidate,
    ContradictionReport,
    ContradictionReportId,
    ContradictionVerdict,
    Fact,
    FactId,
    FactSource,
    FactSubject,
    InGameTime,
    KnowledgeEntry,
    RetirementReason,
)
from grimoire.storage import Database
from grimoire.util import now_iso


def _dumps(value: object) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, default=str)


def _loads(value: object) -> object:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _ingame_to_str(t: InGameTime | None) -> str | None:
    if t is None:
        return None
    return json.dumps({"day_count": t.day_count, "label": t.label})


def _ingame_from_str(raw: object) -> InGameTime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, InGameTime):
        return raw
    data = _loads(raw)
    if not isinstance(data, dict):
        return None
    return InGameTime(day_count=int(data.get("day_count", 0)), label=str(data.get("label", "")))


def _fact_to_row(fact: Fact, campaign_id: str) -> dict:
    about_payload = {
        "character_ids": list(fact.about.character_ids),
        "location_ids": list(fact.about.location_ids),
        "faction_ids": list(fact.about.faction_ids),
        "item_ids": list(fact.about.item_ids),
        "scope": fact.about.scope,
    }
    return {
        "id": fact.id,
        "campaign_id": campaign_id,
        "text": fact.text,
        "established_in_post": fact.established_in_post,
        "in_game_when": _ingame_to_str(fact.established_at_in_game),
        "about": _dumps(about_payload),
        "source": fact.source.value if isinstance(fact.source, FactSource) else fact.source,
        "speaker_ref": fact.speaker_id,
        "confidence": float(fact.confidence),
        "keywords": _dumps(list(fact.keywords)),
        "retired": 1 if fact.retired else 0,
        "retired_in_post": fact.retired_in_post,
        "contradicts": _dumps(list(fact.contradicts)),
        "tags": _dumps(_tags_with_retired_reason(fact)),
    }


def _tags_with_retired_reason(fact: Fact) -> list[str]:
    """Encode `retired_reason` as a tag so we can round-trip it.

    The `facts` table has no `retired_reason` column; encoding it in the
    `tags` JSON keeps the schema unchanged while preserving information.
    """
    tags = [t for t in fact.tags if not t.startswith("retired_reason:")]
    if fact.retired_reason is not None:
        tags.append(f"retired_reason:{fact.retired_reason.value}")
    return tags


def _fact_from_row(row: dict) -> Fact:
    about_raw = _loads(row.get("about")) or {}
    if isinstance(about_raw, dict):
        about = FactSubject(
            character_ids=list(about_raw.get("character_ids") or []),
            location_ids=list(about_raw.get("location_ids") or []),
            faction_ids=list(about_raw.get("faction_ids") or []),
            item_ids=list(about_raw.get("item_ids") or []),
            scope=str(about_raw.get("scope") or "public"),
        )
    else:
        about = FactSubject()

    tags_raw = _loads(row.get("tags")) or []
    retired_reason: RetirementReason | None = None
    tags: list[str] = []
    for tag in tags_raw:
        if isinstance(tag, str) and tag.startswith("retired_reason:"):
            with contextlib.suppress(ValueError):
                retired_reason = RetirementReason(tag.split(":", 1)[1])
        else:
            tags.append(tag)

    source_val = row.get("source") or FactSource.NARRATOR.value
    try:
        source = FactSource(source_val)
    except ValueError:
        source = FactSource.NARRATOR

    return Fact(
        id=row["id"],
        text=row.get("text") or "",
        established_in_post=row.get("established_in_post") or "",
        established_at_in_game=_ingame_from_str(row.get("in_game_when")) or InGameTime(day_count=0),
        confidence=float(row.get("confidence") or 0.0),
        source=source,
        speaker_id=row.get("speaker_ref"),
        about=about,
        keywords=list(_loads(row.get("keywords")) or []),
        retired=bool(int(row.get("retired") or 0)),
        retired_in_post=row.get("retired_in_post"),
        retired_reason=retired_reason,
        contradicts=list(_loads(row.get("contradicts")) or []),
        tags=tags,
    )


def _commitment_to_row(c: Commitment, campaign_id: str) -> dict:
    extra = {
        "weight": int(c.weight),
        "last_activity_at": _ingame_to_str(c.last_activity_at),
        "due_by": _ingame_to_str(c.due_by),
    }
    tags = [t for t in c.tags if not t.startswith("_meta:")]
    tags.append("_meta:" + json.dumps(extra, sort_keys=True))
    return {
        "id": c.id,
        "campaign_id": campaign_id,
        "kind": c.kind.value if isinstance(c.kind, CommitmentKind) else c.kind,
        "text": c.text,
        "from_character_ref": c.from_id,
        "to_character_ref": c.to_id,
        "due_by": _ingame_to_str(c.due_by),
        "status": c.status.value if isinstance(c.status, CommitmentStatus) else c.status,
        "weight": int(c.weight),
        "created_in_post": c.created_in_post,
        "in_game_created_at": _ingame_to_str(c.in_game_created_at),
        "resolved_in_post": c.resolved_in_post,
        "tags": _dumps(tags),
        "related_fact_ids": _dumps(list(c.related_fact_ids)),
    }


def _commitment_from_row(row: dict) -> Commitment:
    tags_raw = _loads(row.get("tags")) or []
    last_activity: InGameTime | None = None
    plain_tags: list[str] = []
    for tag in tags_raw:
        if isinstance(tag, str) and tag.startswith("_meta:"):
            try:
                meta = json.loads(tag[len("_meta:") :])
                last_activity = _ingame_from_str(meta.get("last_activity_at"))
            except (ValueError, KeyError):
                pass
        else:
            plain_tags.append(tag)

    try:
        kind = CommitmentKind(row.get("kind") or "promise")
    except ValueError:
        kind = CommitmentKind.PROMISE
    try:
        status = CommitmentStatus(row.get("status") or "open")
    except ValueError:
        status = CommitmentStatus.OPEN

    return Commitment(
        id=row["id"],
        kind=kind,
        text=row.get("text") or "",
        created_in_post=row.get("created_in_post") or "",
        in_game_created_at=_ingame_from_str(row.get("in_game_created_at"))
        or InGameTime(day_count=0),
        weight=int(row.get("weight") or 1),
        from_id=row.get("from_character_ref"),
        to_id=row.get("to_character_ref"),
        due_by=_ingame_from_str(row.get("due_by")),
        status=status,
        resolved_in_post=row.get("resolved_in_post"),
        last_activity_at=last_activity,
        tags=plain_tags,
        related_fact_ids=list(_loads(row.get("related_fact_ids")) or []),
    )


def _knowledge_to_row(entry: KnowledgeEntry, campaign_id: str) -> dict:
    return {
        "fact_id": entry.fact_id,
        "character_ref": entry.character_id,
        "campaign_id": campaign_id,
        "knows": 1 if entry.knows else 0,
        "learned_in_post": entry.learned_in_post,
        "source": entry.source,
    }


def _knowledge_from_row(row: dict) -> KnowledgeEntry:
    return KnowledgeEntry(
        fact_id=row["fact_id"],
        character_id=row["character_ref"],
        knows=bool(int(row.get("knows") or 0)),
        learned_in_post=row.get("learned_in_post"),
        source=row.get("source") or "",
    )


class SqliteContinuityStore(ContinuityStore):
    """Continuity persistence backed by SQLite via :class:`Database`.

    Each instance is bound to one campaign_id so every read and write
    naturally scopes to that campaign.
    """

    def __init__(self, db: Database, *, campaign_id: str) -> None:
        self._db = db
        self._campaign_id = campaign_id

    @property
    def campaign_id(self) -> str:
        return self._campaign_id

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    async def put_fact(self, fact: Fact) -> None:
        row = _fact_to_row(fact, self._campaign_id)
        await self._db.execute(
            """
            INSERT INTO facts (
              id, campaign_id, text, established_in_post, in_game_when,
              about, source, speaker_ref, confidence, keywords, retired,
              retired_in_post, contradicts, tags
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              campaign_id = excluded.campaign_id,
              text = excluded.text,
              established_in_post = excluded.established_in_post,
              in_game_when = excluded.in_game_when,
              about = excluded.about,
              source = excluded.source,
              speaker_ref = excluded.speaker_ref,
              confidence = excluded.confidence,
              keywords = excluded.keywords,
              retired = excluded.retired,
              retired_in_post = excluded.retired_in_post,
              contradicts = excluded.contradicts,
              tags = excluded.tags
            """,
            (
                row["id"],
                row["campaign_id"],
                row["text"],
                row["established_in_post"],
                row["in_game_when"],
                row["about"],
                row["source"],
                row["speaker_ref"],
                row["confidence"],
                row["keywords"],
                row["retired"],
                row["retired_in_post"],
                row["contradicts"],
                row["tags"],
            ),
        )

    async def get_fact(self, fact_id: FactId) -> Fact | None:
        row = await self._db.fetchone(
            """
            SELECT * FROM facts
            WHERE id = ? AND campaign_id = ?
            """,
            (fact_id, self._campaign_id),
        )
        if row is None:
            return None
        return _fact_from_row(dict(row))

    async def list_facts(self, *, include_retired: bool = False) -> list[Fact]:
        if include_retired:
            rows = await self._db.fetchall(
                """
                SELECT * FROM facts
                WHERE campaign_id = ?
                """,
                (self._campaign_id,),
            )
        else:
            rows = await self._db.fetchall(
                """
                SELECT * FROM facts
                WHERE campaign_id = ? AND retired = 0
                """,
                (self._campaign_id,),
            )
        return [_fact_from_row(dict(row)) for row in rows]

    # ------------------------------------------------------------------
    # Commitments
    # ------------------------------------------------------------------

    async def put_commitment(self, commitment: Commitment) -> None:
        row = _commitment_to_row(commitment, self._campaign_id)
        await self._db.execute(
            """
            INSERT INTO commitments (
              id, campaign_id, kind, text, from_character_ref,
              to_character_ref, due_by, status, weight, created_in_post,
              in_game_created_at, resolved_in_post, tags, related_fact_ids
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              campaign_id = excluded.campaign_id,
              kind = excluded.kind,
              text = excluded.text,
              from_character_ref = excluded.from_character_ref,
              to_character_ref = excluded.to_character_ref,
              due_by = excluded.due_by,
              status = excluded.status,
              weight = excluded.weight,
              created_in_post = excluded.created_in_post,
              in_game_created_at = excluded.in_game_created_at,
              resolved_in_post = excluded.resolved_in_post,
              tags = excluded.tags,
              related_fact_ids = excluded.related_fact_ids
            """,
            (
                row["id"],
                row["campaign_id"],
                row["kind"],
                row["text"],
                row["from_character_ref"],
                row["to_character_ref"],
                row["due_by"],
                row["status"],
                row["weight"],
                row["created_in_post"],
                row["in_game_created_at"],
                row["resolved_in_post"],
                row["tags"],
                row["related_fact_ids"],
            ),
        )

    async def get_commitment(self, cid: CommitmentId) -> Commitment | None:
        row = await self._db.fetchone(
            """
            SELECT * FROM commitments
            WHERE id = ? AND campaign_id = ?
            """,
            (cid, self._campaign_id),
        )
        if row is None:
            return None
        return _commitment_from_row(dict(row))

    async def list_commitments(
        self, *, statuses: Iterable[CommitmentStatus] | None = None
    ) -> list[Commitment]:
        if statuses is None:
            rows = await self._db.fetchall(
                """
                SELECT * FROM commitments
                WHERE campaign_id = ?
                """,
                (self._campaign_id,),
            )
        else:
            values = [s.value if isinstance(s, CommitmentStatus) else s for s in statuses]
            if not values:
                return []
            placeholders = ",".join("?" * len(values))
            rows = await self._db.fetchall(
                f"""
                SELECT * FROM commitments
                WHERE campaign_id = ? AND status IN ({placeholders})
                """,
                (self._campaign_id, *values),
            )
        return [_commitment_from_row(dict(row)) for row in rows]

    async def delete_commitment(self, cid: CommitmentId) -> None:
        await self._db.execute(
            "DELETE FROM commitments WHERE id = ? AND campaign_id = ?",
            (cid, self._campaign_id),
        )

    # ------------------------------------------------------------------
    # Knowledge
    # ------------------------------------------------------------------

    async def put_knowledge(self, entry: KnowledgeEntry) -> None:
        row = _knowledge_to_row(entry, self._campaign_id)
        await self._db.execute(
            """
            INSERT INTO knowledge_state (
              fact_id, character_ref, campaign_id, knows,
              learned_in_post, source
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(fact_id, character_ref) DO UPDATE SET
              campaign_id = excluded.campaign_id,
              knows = excluded.knows,
              learned_in_post = excluded.learned_in_post,
              source = excluded.source
            """,
            (
                row["fact_id"],
                row["character_ref"],
                row["campaign_id"],
                row["knows"],
                row["learned_in_post"],
                row["source"],
            ),
        )

    async def get_knowledge(self, character_id: str, fact_id: FactId) -> KnowledgeEntry | None:
        row = await self._db.fetchone(
            """
            SELECT * FROM knowledge_state
            WHERE fact_id = ? AND character_ref = ? AND campaign_id = ?
            """,
            (fact_id, character_id, self._campaign_id),
        )
        if row is None:
            return None
        return _knowledge_from_row(dict(row))

    async def knowledge_for_character(self, character_id: str) -> list[KnowledgeEntry]:
        rows = await self._db.fetchall(
            """
            SELECT * FROM knowledge_state
            WHERE character_ref = ? AND campaign_id = ?
            """,
            (character_id, self._campaign_id),
        )
        return [_knowledge_from_row(dict(row)) for row in rows]

    # ------------------------------------------------------------------
    # Contradiction reports
    # ------------------------------------------------------------------

    async def put_contradiction_report(self, report: ContradictionReport) -> None:
        candidate_payload = _serialise_fact(report.candidate_fact)
        conflicts_payload = [_serialise_candidate(c) for c in report.conflicts]
        existing = await self._db.fetchone(
            "SELECT id, created_at FROM contradiction_reports WHERE id = ?",
            (report.id,),
        )
        if existing is None:
            await self._db.execute(
                """
                INSERT INTO contradiction_reports (
                  id, campaign_id, candidate_fact, conflicts,
                  resolved, resolution, created_at, resolved_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.id,
                    self._campaign_id,
                    _dumps(candidate_payload),
                    _dumps(conflicts_payload),
                    1 if report.resolved else 0,
                    _dumps(report.resolution),
                    now_iso(),
                    now_iso() if report.resolved else None,
                ),
            )
        else:
            await self._db.execute(
                """
                UPDATE contradiction_reports SET
                  candidate_fact = ?,
                  conflicts = ?,
                  resolved = ?,
                  resolution = ?,
                  resolved_at = ?
                WHERE id = ?
                """,
                (
                    _dumps(candidate_payload),
                    _dumps(conflicts_payload),
                    1 if report.resolved else 0,
                    _dumps(report.resolution),
                    now_iso() if report.resolved else None,
                    report.id,
                ),
            )

    async def get_contradiction_report(
        self, report_id: ContradictionReportId
    ) -> ContradictionReport | None:
        row = await self._db.fetchone(
            """
            SELECT * FROM contradiction_reports
            WHERE id = ? AND campaign_id = ?
            """,
            (report_id, self._campaign_id),
        )
        if row is None:
            return None
        candidate = _deserialise_fact(_loads(row["candidate_fact"]) or {})
        conflicts_raw = _loads(row["conflicts"]) or []
        conflicts = [_deserialise_candidate(c) for c in conflicts_raw if isinstance(c, dict)]
        return ContradictionReport(
            id=row["id"],
            candidate_fact=candidate,
            conflicts=conflicts,
            resolved=bool(int(row["resolved"])),
            resolution=_loads(row["resolution"]) if row["resolution"] else None,
        )

    async def list_contradiction_reports(
        self,
        *,
        resolved: bool | None = None,
        limit: int = 50,
    ) -> list[ContradictionReport]:
        where = ["campaign_id = ?"]
        params: list[object] = [self._campaign_id]
        if resolved is True:
            where.append("resolved = 1")
        elif resolved is False:
            where.append("resolved = 0")
        sql = (
            "SELECT * FROM contradiction_reports "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY created_at DESC, id DESC LIMIT ?"
        )
        rows = await self._db.fetchall(sql, (*params, limit))
        out: list[ContradictionReport] = []
        for row in rows:
            candidate = _deserialise_fact(_loads(row["candidate_fact"]) or {})
            conflicts_raw = _loads(row["conflicts"]) or []
            conflicts = [_deserialise_candidate(c) for c in conflicts_raw if isinstance(c, dict)]
            out.append(
                ContradictionReport(
                    id=row["id"],
                    candidate_fact=candidate,
                    conflicts=conflicts,
                    resolved=bool(int(row["resolved"])),
                    resolution=_loads(row["resolution"]) if row["resolution"] else None,
                )
            )
        return out


def _serialise_fact(fact: Fact) -> dict:
    """Serialise a Fact to a JSON-safe dict for contradiction-report storage."""
    return {
        "id": fact.id,
        "text": fact.text,
        "established_in_post": fact.established_in_post,
        "in_game_when": {
            "day_count": fact.established_at_in_game.day_count,
            "label": fact.established_at_in_game.label,
        },
        "confidence": fact.confidence,
        "source": fact.source.value if isinstance(fact.source, FactSource) else fact.source,
        "speaker_id": fact.speaker_id,
        "about": dataclasses.asdict(fact.about),
        "keywords": list(fact.keywords),
        "retired": fact.retired,
        "retired_in_post": fact.retired_in_post,
        "retired_reason": fact.retired_reason.value
        if isinstance(fact.retired_reason, RetirementReason)
        else fact.retired_reason,
        "contradicts": list(fact.contradicts),
        "tags": list(fact.tags),
    }


def _deserialise_fact(payload: dict) -> Fact:
    about_data = payload.get("about") or {}
    about = FactSubject(
        character_ids=list(about_data.get("character_ids") or []),
        location_ids=list(about_data.get("location_ids") or []),
        faction_ids=list(about_data.get("faction_ids") or []),
        item_ids=list(about_data.get("item_ids") or []),
        scope=str(about_data.get("scope") or "public"),
    )
    when = payload.get("in_game_when") or {}
    retired_reason: RetirementReason | None = None
    rr_val = payload.get("retired_reason")
    if rr_val:
        try:
            retired_reason = RetirementReason(rr_val)
        except ValueError:
            retired_reason = None
    try:
        source = FactSource(payload.get("source") or FactSource.NARRATOR.value)
    except ValueError:
        source = FactSource.NARRATOR
    return Fact(
        id=payload.get("id") or "",
        text=payload.get("text") or "",
        established_in_post=payload.get("established_in_post") or "",
        established_at_in_game=InGameTime(
            day_count=int(when.get("day_count", 0)),
            label=str(when.get("label", "")),
        ),
        confidence=float(payload.get("confidence") or 0.0),
        source=source,
        speaker_id=payload.get("speaker_id"),
        about=about,
        keywords=list(payload.get("keywords") or []),
        retired=bool(payload.get("retired")),
        retired_in_post=payload.get("retired_in_post"),
        retired_reason=retired_reason,
        contradicts=list(payload.get("contradicts") or []),
        tags=list(payload.get("tags") or []),
    )


def _serialise_candidate(candidate: ContradictionCandidate) -> dict:
    return {
        "existing_fact": _serialise_fact(candidate.existing_fact),
        "similarity": candidate.similarity,
        "verdict": candidate.verdict.value
        if isinstance(candidate.verdict, ContradictionVerdict)
        else candidate.verdict,
        "confidence": candidate.confidence,
        "rationale": candidate.rationale,
    }


def _deserialise_candidate(payload: dict) -> ContradictionCandidate:
    try:
        verdict = ContradictionVerdict(payload.get("verdict") or "uncertain")
    except ValueError:
        verdict = ContradictionVerdict.UNCERTAIN
    return ContradictionCandidate(
        existing_fact=_deserialise_fact(payload.get("existing_fact") or {}),
        similarity=float(payload.get("similarity") or 0.0),
        verdict=verdict,
        confidence=float(payload.get("confidence") or 0.0),
        rationale=str(payload.get("rationale") or ""),
    )


__all__ = ["SqliteContinuityStore"]
