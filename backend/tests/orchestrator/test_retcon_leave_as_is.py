"""Characterization tests for the leave-as-is retcon path (#583).

A retcon must be all-or-nothing: a failing re-extraction, a reversal failure
partway through the turn's deltas, and a re-apply failure partway through the
replacement deltas must each leave the post text and campaign state exactly
as they were, and surface the failure to the caller instead of returning a
clean ``RetconResult``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.event_bus import EventBus
from grimoire.orchestrator import OrchestratorService
from grimoire.orchestrator.errors import RetconExtractionError, RetconStateError
from grimoire.scenes.manager import SceneManager, SceneManagerConfig, new_post
from grimoire.scenes.types import AuthorKind, SceneInit
from grimoire.state_store import StateStore
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db
from grimoire.types.state import DeltaKind, StateDelta

from .conftest import FakeContextBuilder, FakeExtractor, FakeGateway, WSCollector

CAMPAIGN_ID = "c1"
TURN_ID = "t_retcon"
ORIGINAL_BODY = "winifred tenses."


@pytest.fixture
async def real_store(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(stamp_migrated_db(tmp_path / "c.sqlite"), pool_size=2)
    await db.connect()
    s = StateStore(db, data_root)
    try:
        yield s
    finally:
        await db.close()


@pytest.fixture
def scenes(tmp_path: Path) -> SceneManager:
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    return SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))


def _char_delta_dict(mood: str) -> dict:
    return {
        "kind": "character_state_update",
        "target_scope": "campaign-sqlite",
        "target_table": "character_state",
        "target_id": "lib:winifred",
        "after": {
            "character_ref": "lib:winifred",
            "campaign_id": CAMPAIGN_ID,
            "emotional_state": mood,
        },
    }


def _new_delta(
    mood: str, *, target_table: str = "character_state", confidence: float = 1.0
) -> StateDelta:
    return StateDelta(
        kind=DeltaKind.CHARACTER_STATE_UPDATE,
        target_scope="campaign-sqlite",
        target_table=target_table,
        target_id="lib:winifred",
        after={
            "character_ref": "lib:winifred",
            "campaign_id": CAMPAIGN_ID,
            "emotional_state": mood,
        },
        confidence=confidence,
    )


async def _seed_scene_with_turn(scenes: SceneManager, store: StateStore) -> tuple[str, str]:
    """Campaign + scene + a narrator post whose turn applied two deltas.

    Live state after seeding: winifred's mood is "tense".
    Returns ``(scene_id, narrator_post_id)``.
    """
    await store.upsert_campaign(campaign_id=CAMPAIGN_ID, name="Test")
    scene = await scenes.start_scene(SceneInit(campaign_id=CAMPAIGN_ID, title="Opening"))
    player = new_post(author_kind=AuthorKind.PC, body="I knock.", is_player=True)
    await scenes.append_post(scene.id, player)
    narrator = new_post(
        author_kind=AuthorKind.NARRATOR,
        body=ORIGINAL_BODY,
        is_player=False,
        turn_id=TURN_ID,
    )
    await scenes.append_post(scene.id, narrator)
    for mood in ("calm", "tense"):
        await store.apply_delta(
            delta=_char_delta_dict(mood),
            source="extractor",
            turn_id=TURN_ID,
            campaign_id=CAMPAIGN_ID,
        )
    return scene.id, narrator.id


def _make_orch(scenes: SceneManager, store: StateStore, extractor: FakeExtractor):
    return OrchestratorService(
        event_bus=EventBus(),
        scene_manager=scenes,
        llm_gateway=FakeGateway(),
        context_builder=FakeContextBuilder(),
        extractor=extractor,
        state_store=store,
        ws_push=WSCollector(),
    )


async def _mood(store: StateStore) -> str | None:
    state = await store.resolve_character_state(
        character_ref="lib:winifred", campaign_id=CAMPAIGN_ID
    )
    return state["emotional_state"] if state else None


async def _post_body(scenes: SceneManager, scene_id: str, post_id: str) -> str:
    posts = await scenes.get_posts(scene_id)
    return next(p.body for p in posts if p.id == post_id)


async def _turn_log(store: StateStore):
    return await store.get_delta_log(
        campaign_id=CAMPAIGN_ID, turn_id=TURN_ID, include_reversed=True
    )


async def test_retcon_swaps_turn_deltas_and_edits_post(
    scenes: SceneManager, real_store: StateStore
) -> None:
    scene_id, post_id = await _seed_scene_with_turn(scenes, real_store)
    extractor = FakeExtractor(
        deltas=[_new_delta("anxious"), _new_delta("suspicious", confidence=0.7)]
    )
    orch = _make_orch(scenes, real_store, extractor)

    result = await orch.retcon_post(post_id, "winifred frowns instead.")

    assert len(result.reversed_delta_ids) == 2
    assert len(result.new_delta_ids) == 1
    assert result.warnings == []
    assert await _mood(real_store) == "anxious"
    assert await _post_body(scenes, scene_id, post_id) == "winifred frowns instead."
    log = await _turn_log(real_store)
    assert [(r.source, r.reversed_at is not None) for r in log] == [
        ("extractor", True),
        ("extractor", True),
        ("retcon", False),
    ]
    # The low-confidence delta went to the review queue, not the swap.
    assert len(await real_store.pending_review_delta_ids(CAMPAIGN_ID)) == 1


async def test_extraction_failure_leaves_post_and_state_unchanged(
    scenes: SceneManager, real_store: StateStore
) -> None:
    scene_id, post_id = await _seed_scene_with_turn(scenes, real_store)
    extractor = FakeExtractor(raise_on_extract=RuntimeError("llm down"))
    orch = _make_orch(scenes, real_store, extractor)

    with pytest.raises(RetconExtractionError):
        await orch.retcon_post(post_id, "edited text")

    assert await _mood(real_store) == "tense"
    assert await _post_body(scenes, scene_id, post_id) == ORIGINAL_BODY
    log = await _turn_log(real_store)
    assert len(log) == 2
    assert all(r.reversed_at is None for r in log)


async def test_reapply_failure_unwinds_partials_and_restores_state(
    scenes: SceneManager, real_store: StateStore
) -> None:
    scene_id, post_id = await _seed_scene_with_turn(scenes, real_store)
    # First replacement applies cleanly; the second fails. The swap must
    # unwind the first replacement and un-reverse both originals.
    extractor = FakeExtractor(
        deltas=[_new_delta("anxious"), _new_delta("broken", target_table="not_a_real_table")]
    )
    orch = _make_orch(scenes, real_store, extractor)

    with pytest.raises(RetconStateError):
        await orch.retcon_post(post_id, "edited text")

    log = await _turn_log(real_store)
    assert [(r.source, r.reversed_at is None) for r in log] == [
        ("extractor", True),
        ("extractor", True),
    ]
    assert await _mood(real_store) == "tense"
    assert await _post_body(scenes, scene_id, post_id) == ORIGINAL_BODY


async def test_reversal_failure_partway_restores_state(
    scenes: SceneManager, real_store: StateStore
) -> None:
    await real_store.upsert_campaign(campaign_id=CAMPAIGN_ID, name="Test")
    scene = await scenes.start_scene(SceneInit(campaign_id=CAMPAIGN_ID, title="Opening"))
    narrator = new_post(
        author_kind=AuthorKind.NARRATOR,
        body=ORIGINAL_BODY,
        is_player=False,
        turn_id=TURN_ID,
    )
    await scenes.append_post(scene.id, narrator)
    # First delta of the turn is irreversible (campaign-local scope); the
    # second is fine. The LIFO reversal succeeds on the second, fails on the
    # first — the second's reversal must roll back too.
    await real_store.apply_delta(
        delta={
            "kind": "other",
            "target_scope": "campaign-local",
            "target_id": "note-1",
            "after": {"text": "scribble"},
        },
        source="extractor",
        turn_id=TURN_ID,
        campaign_id=CAMPAIGN_ID,
    )
    await real_store.apply_delta(
        delta=_char_delta_dict("tense"),
        source="extractor",
        turn_id=TURN_ID,
        campaign_id=CAMPAIGN_ID,
    )
    orch = _make_orch(scenes, real_store, FakeExtractor(deltas=[_new_delta("anxious")]))

    with pytest.raises(RetconStateError):
        await orch.retcon_post(narrator.id, "edited text")

    log = await _turn_log(real_store)
    assert len(log) == 2
    assert all(r.reversed_at is None for r in log)
    assert await _mood(real_store) == "tense"
    assert await _post_body(scenes, scene.id, narrator.id) == ORIGINAL_BODY
