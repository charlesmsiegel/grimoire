"""Accept-dispatch tests for auxiliary results.

Verifies that each `CommitAction` routes to the right canonical path:
  * SUBMIT_POST  → submit_post (canonical turn pipeline)
  * REPLACE_POST → new alternate + switch_primary
  * APPEND_POST  → append NPC-authored post
  * COPY / REPLACE_DRAFT → no server mutation
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from grimoire.auxiliary.types import (
    AuxiliaryResult,
    AuxiliaryTask,
    TaskKind,
)
from grimoire.orchestrator.errors import AuxiliaryNotFoundError


def _aux(kind: TaskKind, **kwargs) -> AuxiliaryResult:
    from grimoire.auxiliary.types import commit_action_for

    task = AuxiliaryTask(kind=kind, **kwargs.pop("task_kwargs", {}))
    return AuxiliaryResult(
        id=kwargs.get("id", "ar_test"),
        task=task,
        text=kwargs.get("text", "the canned text"),
        completed_at=datetime.now(UTC),
        model_used=kwargs.get("model_used", "claude-opus-4-7"),
        tokens=kwargs.get("tokens", 12),
        pending_commit_action=commit_action_for(kind),
    )


async def test_accept_unknown_id_raises(orchestrator, seeded_state):
    with pytest.raises(AuxiliaryNotFoundError):
        await orchestrator.accept_auxiliary(seeded_state.campaign_id, "ar_does_not_exist")


async def test_accept_impersonate_pc_submits_canonical_turn(
    orchestrator, scene_manager, seeded_state
):
    aux = _aux(
        TaskKind.IMPERSONATE_PC,
        text="The young man bowed and offered his hand.",
        task_kwargs={"extra_params": {"active_pc_ref": seeded_state.pc_ref}},
    )
    orchestrator._inflight_aux[aux.id] = aux

    out = await orchestrator.accept_auxiliary(seeded_state.campaign_id, aux.id)
    assert out["action"] == "submit_post"
    assert out["committed"] is True

    posts = await scene_manager.get_posts(seeded_state.scene.id)
    # submit_post appends the player's text and then drives a canonical turn,
    # so the player-authored post sits at position -2 (model reply is -1).
    player_post = next(p for p in reversed(posts) if p.is_player)
    assert player_post.body == "The young man bowed and offered his hand."
    assert player_post.author_pc_ref == seeded_state.pc_ref

    # The auxiliary is consumed.
    assert aux.id not in orchestrator._inflight_aux


async def test_accept_continue_as_appends_npc_post(orchestrator, scene_manager, seeded_state):
    aux = _aux(
        TaskKind.CONTINUE_AS,
        text="And he turned to the fire, troubled.",
        task_kwargs={"target_character_ref": "npc_crow"},
    )
    orchestrator._inflight_aux[aux.id] = aux

    out = await orchestrator.accept_auxiliary(seeded_state.campaign_id, aux.id)
    assert out["action"] == "append_post"

    posts = await scene_manager.get_posts(seeded_state.scene.id)
    last = posts[-1]
    assert last.body == "And he turned to the fire, troubled."
    assert last.author_npc_ref == "npc_crow"
    assert last.is_player is False


async def test_accept_copy_action_just_returns_text(orchestrator, seeded_state):
    aux = _aux(TaskKind.BRAINSTORM, text="ideas A, B, C")
    orchestrator._inflight_aux[aux.id] = aux
    out = await orchestrator.accept_auxiliary(seeded_state.campaign_id, aux.id)
    assert out["action"] == "copy"
    assert out["text"] == "ideas A, B, C"


async def test_accept_replace_draft_returns_text(orchestrator, seeded_state):
    aux = _aux(
        TaskKind.EDIT_PROSE,
        text="polished prose",
        task_kwargs={"snippet": "rough prose", "edit_instruction": "polish it"},
    )
    orchestrator._inflight_aux[aux.id] = aux
    out = await orchestrator.accept_auxiliary(seeded_state.campaign_id, aux.id)
    assert out["action"] == "replace_draft"
    assert out["text"] == "polished prose"


async def test_accept_uses_edited_text_when_provided(orchestrator, seeded_state):
    aux = _aux(TaskKind.BRAINSTORM, text="original")
    orchestrator._inflight_aux[aux.id] = aux
    out = await orchestrator.accept_auxiliary(
        seeded_state.campaign_id, aux.id, edited_text="overridden"
    )
    assert out["text"] == "overridden"


async def test_double_accept_raises_not_found(orchestrator, seeded_state):
    aux = _aux(TaskKind.BRAINSTORM, text="x")
    orchestrator._inflight_aux[aux.id] = aux
    await orchestrator.accept_auxiliary(seeded_state.campaign_id, aux.id)
    with pytest.raises(AuxiliaryNotFoundError):
        await orchestrator.accept_auxiliary(seeded_state.campaign_id, aux.id)


async def test_accept_rewrite_post_swaps_primary(orchestrator, scene_manager, seeded_state):
    """rewrite_post → new alternate created on the target post; primary switched."""
    # Seed: convert the existing model post into an alternate-bearing post.
    # The accept path calls scene_manager.append_alternate which will synthesize
    # an implicit primary alternate from the post body, then add ours.

    target = seeded_state.posts[0]
    aux = _aux(
        TaskKind.REWRITE_POST,
        text="The crow stooped, eyes black with hunger.",
        task_kwargs={
            "target_post_id": target.id,
            "edit_instruction": "More menacing.",
        },
    )
    orchestrator._inflight_aux[aux.id] = aux

    out = await orchestrator.accept_auxiliary(seeded_state.campaign_id, aux.id)
    assert out["action"] == "replace_post"
    assert out["post_id"] == target.id
    new_alt_id = out["alternate_id"]

    _, post = await scene_manager._find_post(target.id)
    alt = next(a for a in post.alternates if a.id == new_alt_id)
    assert alt.text == "The crow stooped, eyes black with hunger."
    assert post.primary_alternate_id == new_alt_id


async def test_accept_rewrite_post_on_failed_extraction_keeps_aux_parked(
    orchestrator, scene_manager, seeded_state, fake_extractor
):
    """If the rewrite path raises after the pop, the aux must be re-parked
    so the user can retry.

    Force a failure by stripping `_find_scene_and_post` lookup target —
    pass an aux with a missing post id.
    """
    aux = _aux(
        TaskKind.REWRITE_POST,
        text="x",
        task_kwargs={"target_post_id": "p_nonexistent"},
    )
    orchestrator._inflight_aux[aux.id] = aux
    with pytest.raises(KeyError):
        await orchestrator.accept_auxiliary(seeded_state.campaign_id, aux.id)
    # Re-parked.
    assert aux.id in orchestrator._inflight_aux
