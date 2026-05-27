"""Module-level helper functions for the orchestrator package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from grimoire.scenes.types import Post as SceneFilePost
from grimoire.scenes.types import Scene as SceneFileScene
from grimoire.types.common import TurnId
from grimoire.types.mechanics import (
    MechanicsResult,
    ProposedRoll,
    Roll,
    RollModifier,
)
from grimoire.types.scene import Scene as PydanticScene


async def _campaign_generation_overrides(store: Any, campaign_id: str) -> dict[str, Any]:
    """Read ``campaigns.config["generation"]`` for ``campaign_id``.

    Returns ``{"max_tokens": int | None, "temperature": float | None}`` —
    each key is ``None`` when the user hasn't set an override for that
    field. Always best-effort: any error returns empty so the caller
    falls back to the prompt's default params.
    """
    import json as _json

    try:
        row = await store.db.fetchone("SELECT config FROM campaigns WHERE id = ?", (campaign_id,))
    except Exception:
        return {}
    if not row:
        return {}
    raw = row.get("config") if hasattr(row, "get") else row["config"]
    if not raw:
        return {}
    try:
        data = _json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    block = data.get("generation") or {}
    if not isinstance(block, dict):
        return {}
    return {
        "max_tokens": block.get("max_tokens"),
        "temperature": block.get("temperature"),
    }


def _pydantic_scene(scene: SceneFileScene) -> PydanticScene:
    """Adapt a scene-manager dataclass into the pydantic Scene used by
    Extractor/Mechanics.

    The pydantic model expects an ``InGameTime`` for ``in_game_start`` /
    ``in_game_end``; the dataclass stores a ``datetime``. We drop those
    fields here — the Extractor reads them but doesn't fail when absent.
    """
    return PydanticScene(
        id=scene.id,
        campaign_id=scene.campaign_id,
        ordinal=scene.ordinal,
        slug=scene.slug,
        file_path="",
        title=scene.title or "",
        location_ref=scene.location_ref,
        in_game_start=None,
        in_game_end=None,
        greeting_id=scene.greeting_id,
        pov_character_ref=scene.pov_character_ref,
        present_character_refs=list(scene.present_character_refs),
        present_pc_refs=list(scene.present_pc_refs),
        mood=scene.mood or "",
        post_count=scene.post_count,
        threads_introduced=[],
        threads_paid_off=[],
        tags=list(scene.tags),
        closed=scene.closed,
        closed_at_turn=scene.closed_at_turn,
        last_advance_at_post=scene.last_advance_at_post or None,
        running_summary=scene.running_summary or "",
        summary=scene.final_summary or "",
        key_beats=list(scene.key_beats),
        emotional_arc="",
    )


def _pydantic_post(post: SceneFilePost) -> Any:
    from grimoire.types.scene import AuthorKind as PydAuthorKind
    from grimoire.types.scene import Post as PydPost

    return PydPost(
        id=post.id,
        scene_id=post.scene_id,
        order_in_scene=post.order_in_scene,
        author_kind=PydAuthorKind(post.author_kind.value),
        body=post.body,
        is_player=post.is_player,
        created_at=post.created_at,
        turn_id=post.turn_id,
        author_pc_ref=post.author_pc_ref,
        author_npc_ref=post.author_npc_ref,
    )


def _build_continuity_fact(
    *,
    payload: dict,
    confidence: float,
    source: str,
    turn_id: TurnId,
) -> Any:
    """Build a dataclass :class:`Fact` from an extractor FACT_ADD payload."""
    from grimoire.continuity.types import Fact, FactSource, FactSubject, InGameTime

    about_data = payload.get("about") or {}
    if isinstance(about_data, FactSubject):
        about = about_data
    else:
        about = FactSubject(
            character_ids=list(about_data.get("character_ids") or []),
            location_ids=list(about_data.get("location_ids") or []),
            faction_ids=list(about_data.get("faction_ids") or []),
            item_ids=list(about_data.get("item_ids") or []),
            scope=str(about_data.get("scope") or "public"),
        )
    src_raw = payload.get("source") or source
    try:
        fact_source = FactSource(str(src_raw))
    except ValueError:
        fact_source = FactSource.NARRATOR
    when_data = payload.get("in_game_when") or {}
    when = InGameTime(
        day_count=int(when_data.get("day_count", 0)),
        label=str(when_data.get("label", "")),
    )
    return Fact(
        id="",
        text=str(payload.get("text", "")),
        established_in_post=str(payload.get("established_in_post") or turn_id),
        established_at_in_game=when,
        confidence=float(confidence),
        source=fact_source,
        speaker_id=payload.get("speaker_id"),
        about=about,
        keywords=list(payload.get("keywords") or []),
    )


def _build_continuity_commitment(
    *,
    payload: dict,
    turn_id: TurnId,
) -> Any | None:
    """Build a :class:`Commitment` from an extractor COMMITMENT_ADD payload."""
    from grimoire.continuity.types import (
        Commitment,
        CommitmentKind,
        CommitmentStatus,
        InGameTime,
    )

    text = str(payload.get("text") or "").strip()
    if not text:
        return None
    kind_raw = str(payload.get("kind") or "promise").lower()
    try:
        kind = CommitmentKind(kind_raw)
    except ValueError:
        kind = CommitmentKind.PROMISE
    when_data = payload.get("in_game_created_at") or {}
    created_at = InGameTime(
        day_count=int(when_data.get("day_count", 0)),
        label=str(when_data.get("label", "")),
    )
    due_data = payload.get("due") or payload.get("due_by")
    due_by: InGameTime | None = None
    if isinstance(due_data, dict):
        due_by = InGameTime(
            day_count=int(due_data.get("day_count", 0)),
            label=str(due_data.get("label", "")),
        )
    return Commitment(
        id="",
        kind=kind,
        text=text,
        created_in_post=str(payload.get("created_in_post") or turn_id),
        in_game_created_at=created_at,
        weight=int(payload.get("weight") or 1),
        from_id=payload.get("from") or payload.get("from_id"),
        to_id=payload.get("to") or payload.get("to_id"),
        due_by=due_by,
        status=CommitmentStatus.OPEN,
    )


@dataclass
class _PreRollOutcome:
    """Result of partitioning + resolving pre-roll proposals."""

    results: list[MechanicsResult]
    pending: list[ProposedRoll]


def _proposed_to_roll(proposal: ProposedRoll) -> Roll:
    """Materialise a ``ProposedRoll`` into a concrete ``Roll`` ready for resolve."""
    return Roll(
        id=f"proposal:{proposal.label}",
        kind=proposal.kind,
        pool=proposal.pool,
        seed=0,
        actor_ref=proposal.actor_ref,
        target_ref=proposal.target_ref,
        difficulty=proposal.difficulty,
        modifiers=list(proposal.modifiers),
        metadata=dict(proposal.metadata),
    )


def _clean_modifications(modifications: dict) -> dict:
    """Filter caller-supplied overrides to fields ``ProposedRoll`` actually accepts."""
    allowed = {
        "kind",
        "pool",
        "difficulty",
        "actor_ref",
        "target_ref",
        "rationale",
        "high_stakes",
        "modifiers",
        "metadata",
    }
    out: dict = {}
    for key, value in modifications.items():
        if key not in allowed:
            continue
        if key == "modifiers" and isinstance(value, list):
            out[key] = [
                v if isinstance(v, RollModifier) else RollModifier.model_validate(v) for v in value
            ]
        else:
            out[key] = value
    return out
