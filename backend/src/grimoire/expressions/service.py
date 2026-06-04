"""Service for reading and writing ``expression_state`` rows.

The table is append-only (every write is a new row); reads pick the
newest row for ``(campaign_id, character_id)`` — optionally constrained by
``as_of_turn`` for replay. Vocabulary checks compare against the active
module set's extension labels, plus the closed core enum.
"""

from __future__ import annotations

import logging

from grimoire.storage.db import Database
from grimoire.types.expressions import (
    ExpressionRecord,
    ExpressionVocabulary,
    VocabularyError,
    is_known_label,
)
from grimoire.util import now_iso

logger = logging.getLogger(__name__)


def _record_from_row(row) -> ExpressionRecord:
    return ExpressionRecord(
        campaign_id=row["campaign_id"],
        scene_id=row["scene_id"],
        character_id=row["character_id"],
        turn_id=row["turn_id"],
        post_id=row["post_id"],
        emotion=row["emotion"],
        provenance=row["provenance"],
        confidence=float(row["confidence"]),
        set_at=row["set_at"],
    )


class ExpressionStateService:
    """Persistence layer for character expressions.

    Vocabulary validation is optional; if no ``vocabulary`` is configured
    on construction, only the closed ``CoreExpression`` values pass.
    Module extensions are supplied per-write via the
    ``module_extensions`` kwarg as a snapshot of the campaign's active
    module set.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def set(
        self,
        *,
        campaign_id: str,
        scene_id: str,
        character_id: str,
        turn_id: str,
        post_id: str,
        emotion: str,
        provenance: str,
        confidence: float = 1.0,
        module_extensions: dict[str, list[str]] | None = None,
        set_at: str | None = None,
    ) -> ExpressionRecord | None:
        """Append an expression row. Returns ``None`` if vocab-rejected.

        Vocab rejection logs once and discards; this lets stale extractor
        labels (e.g. after a module switch) fail safely without breaking
        the calling pipeline.
        """
        if not is_known_label(emotion, module_extensions=module_extensions or {}):
            logger.info(
                "discarding expression %r for character %s/%s: not in active vocabulary",
                emotion,
                campaign_id,
                character_id,
            )
            return None
        ts = set_at or now_iso()
        await self._db.execute(
            """
            INSERT INTO expression_state (
                campaign_id, scene_id, character_id, turn_id, post_id,
                emotion, provenance, confidence, set_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                campaign_id,
                scene_id,
                character_id,
                turn_id,
                post_id,
                emotion,
                provenance,
                float(confidence),
                ts,
            ),
        )
        return ExpressionRecord(
            campaign_id=campaign_id,
            scene_id=scene_id,
            character_id=character_id,
            turn_id=turn_id,
            post_id=post_id,
            emotion=emotion,
            provenance=provenance,
            confidence=float(confidence),
            set_at=ts,
        )

    async def set_strict(
        self,
        *,
        campaign_id: str,
        scene_id: str,
        character_id: str,
        turn_id: str,
        post_id: str,
        emotion: str,
        provenance: str,
        confidence: float = 1.0,
        module_extensions: dict[str, list[str]] | None = None,
    ) -> ExpressionRecord:
        """Like :meth:`set`, but raises ``VocabularyError`` on rejection.

        Used by the PC PATCH route where the caller (a player) should see
        the error rather than silent discard.
        """
        result = await self.set(
            campaign_id=campaign_id,
            scene_id=scene_id,
            character_id=character_id,
            turn_id=turn_id,
            post_id=post_id,
            emotion=emotion,
            provenance=provenance,
            confidence=confidence,
            module_extensions=module_extensions,
        )
        if result is None:
            raise VocabularyError(f"{emotion!r} is not part of the current expression vocabulary")
        return result

    async def current_for(
        self,
        campaign_id: str,
        character_id: str,
        *,
        as_of_turn: str | None = None,
    ) -> ExpressionRecord | None:
        """Return the most recent row for ``(campaign_id, character_id)``.

        ``as_of_turn`` constrains the lookup to rows with ``turn_id <=``
        the given value, enabling historical replay.
        """
        if as_of_turn is None:
            row = await self._db.fetchone(
                """
                SELECT * FROM expression_state
                WHERE campaign_id = ? AND character_id = ?
                ORDER BY turn_id DESC, id DESC
                LIMIT 1
                """,
                (campaign_id, character_id),
            )
        else:
            row = await self._db.fetchone(
                """
                SELECT * FROM expression_state
                WHERE campaign_id = ? AND character_id = ? AND turn_id <= ?
                ORDER BY turn_id DESC, id DESC
                LIMIT 1
                """,
                (campaign_id, character_id, as_of_turn),
            )
        if row is None:
            return None
        return _record_from_row(row)

    async def history_for(
        self,
        campaign_id: str,
        character_id: str,
        *,
        limit: int = 100,
    ) -> list[ExpressionRecord]:
        rows = await self._db.fetchall(
            """
            SELECT * FROM expression_state
            WHERE campaign_id = ? AND character_id = ?
            ORDER BY turn_id DESC, id DESC
            LIMIT ?
            """,
            (campaign_id, character_id, int(limit)),
        )
        return [_record_from_row(row) for row in rows]


def vocabulary_from_modules(modules: list[dict] | None) -> ExpressionVocabulary:
    """Build a vocabulary snapshot from a list of mechanics-module manifest dicts."""
    extensions: dict[str, list[str]] = {}
    for m in modules or []:
        mod_id = m.get("id") if isinstance(m, dict) else None
        if not isinstance(mod_id, str):
            continue
        labels = m.get("expression_vocabulary_extensions") or []
        if isinstance(labels, list):
            extensions[mod_id] = [str(x) for x in labels]
    return ExpressionVocabulary(module_extensions=extensions)


__all__ = ["ExpressionStateService", "vocabulary_from_modules"]
