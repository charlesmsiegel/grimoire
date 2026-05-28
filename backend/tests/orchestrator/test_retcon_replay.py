"""``RetconReplaySession`` end-to-end against a real StateStore (spec
2026-05-19-retcon-design §Replay-mode state machine).

These tests seed a campaign + one scene with three model posts (each
post has a primary alternate carrying a tracked ``delta_set_id``), then
drive the replay batch through start / accept / try-again / cancel and
assert both the state machine transitions and the side effects on the
sidecar + delta log.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.event_bus import EventBus
from grimoire.orchestrator import OrchestratorService
from grimoire.orchestrator.errors import (
    RetconBatchClosedError,
    RetconBatchNotFoundError,
    RetconInFlightError,
)
from grimoire.scenes.manager import SceneManager, SceneManagerConfig, new_post
from grimoire.scenes.types import Alternate, AuthorKind, SceneInit
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.types.state import DeltaKind, StateDelta

from .conftest import FakeContextBuilder, FakeExtractor, FakeGateway, WSCollector


@pytest.fixture
async def real_store(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(tmp_path / "c.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    s = StateStore(db, data_root)
    try:
        yield s
    finally:
        await db.close()


def _char_delta(mood: str, *, campaign_id: str) -> dict:
    return {
        "kind": "character_state_update",
        "target_scope": "campaign-sqlite",
        "target_table": "character_state",
        "target_id": "lib:winifred",
        "after": {
            "character_ref": "lib:winifred",
            "campaign_id": campaign_id,
            "emotional_state": mood,
        },
    }


def _new_delta(mood: str) -> StateDelta:
    return StateDelta(
        kind=DeltaKind.CHARACTER_STATE_UPDATE,
        target_scope="campaign-sqlite",
        target_table="character_state",
        target_id="lib:winifred",
        after={
            "character_ref": "lib:winifred",
            "campaign_id": "c1",
            "emotional_state": mood,
        },
    )


async def _seed_three_model_posts(
    scenes: SceneManager, store: StateStore
) -> tuple[str, str, list[str]]:
    """Build a scene with three model posts; each gets a primary alternate
    backed by a tracked ``delta_set_id`` so the replay path exercises the
    full swap_delta_set machinery.

    Returns (campaign_id, scene_id, [post1_id, post2_id, post3_id]).
    """
    campaign_id = "c1"
    await store.upsert_campaign(campaign_id=campaign_id, name="Test")
    scene = await scenes.start_scene(SceneInit(campaign_id=campaign_id, title="Opening"))

    player = new_post(author_kind=AuthorKind.PC, body="I knock.", is_player=True)
    await scenes.append_post(scene.id, player)
    posts_ids: list[str] = []
    for i, mood in enumerate(["calm", "tense", "fierce"]):
        model = new_post(
            author_kind=AuthorKind.NARRATOR,
            body=f"Narration {i}.",
            is_player=False,
        )
        await scenes.append_post(scene.id, model)
        ds = f"ds_p{i}"
        await store.apply_delta_set(
            deltas=[_char_delta(mood, campaign_id=campaign_id)],
            delta_set_id=ds,
            campaign_id=campaign_id,
            turn_id=model.turn_id,
            source="test",
        )
        # Each model post needs an explicit primary alternate carrying that
        # delta_set_id; otherwise the swap path falls through to the legacy
        # branch and ds_pX never gets rewound during replay.
        await store.rewind_delta_set(ds, campaign_id=campaign_id)
        primary = Alternate(
            id=f"a_p{i}",
            post_id=model.id,
            text=model.body,
            delta_set_id=ds,
            author_kind=AuthorKind.NARRATOR,
            is_primary=True,
        )
        await scenes.append_alternate(model.id, primary)
        await scenes.set_primary_alternate(model.id, primary.id)
        posts_ids.append(model.id)

    # Re-apply only the last set so the live world state matches the timeline.
    await store.apply_delta_set(
        deltas=[_char_delta("fierce", campaign_id=campaign_id)],
        delta_set_id="ds_p2",
        campaign_id=campaign_id,
        turn_id=None,
        source="test",
    )
    await store.set_current_alternate_delta_set(
        campaign_id=campaign_id,
        post_id=posts_ids[-1],
        delta_set_id="ds_p2",
    )
    return campaign_id, scene.id, posts_ids


def _make_orch(scenes: SceneManager, store: StateStore) -> OrchestratorService:
    return OrchestratorService(
        event_bus=EventBus(),
        scene_manager=scenes,
        llm_gateway=FakeGateway(chunks=["replay-text"]),
        context_builder=FakeContextBuilder(),
        extractor=FakeExtractor(deltas=[_new_delta("anxious")]),
        state_store=store,
        ws_push=WSCollector(),
    )


async def test_replay_starts_batch_and_creates_first_alternate(
    tmp_path: Path, real_store: StateStore
) -> None:
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    _campaign_id, scene_id, post_ids = await _seed_three_model_posts(scenes, real_store)
    orch = _make_orch(scenes, real_store)

    # Retcon the first model post; expect replay to pick up posts 2 and 3.
    result = await orch.retcon_post(
        post_ids[0], "winifred never lit the lamp.", replay_subsequent=True
    )

    assert result.replay_batch_id is not None
    state = orch.retcon_replay.get(result.replay_batch_id)
    assert state.subsequent_post_ids == post_ids[1:]
    assert state.current_index == 0
    assert state.current_alternate_id is not None
    assert not state.completed

    # The first subsequent post now carries a new alternate tagged with the
    # batch id.
    posts = await scenes.get_posts(scene_id)
    p2 = next(p for p in posts if p.id == post_ids[1])
    new_alt = next(a for a in p2.alternates if a.id == state.current_alternate_id)
    assert new_alt.replay_batch_id == result.replay_batch_id
    assert new_alt.is_primary is False


async def test_replay_accept_advances_and_completes(tmp_path: Path, real_store: StateStore) -> None:
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, scene_id, post_ids = await _seed_three_model_posts(scenes, real_store)
    orch = _make_orch(scenes, real_store)

    result = await orch.retcon_post(post_ids[0], "edit", replay_subsequent=True)
    batch_id = result.replay_batch_id
    assert batch_id is not None

    # Accept post 2 → primary switched, index advances, alternate created for post 3.
    view1 = await orch.accept_replay(campaign_id)
    assert post_ids[1] in view1.accepted_post_ids
    assert view1.current_index == 1
    assert view1.current_post_id == post_ids[2]
    assert view1.current_alternate_id is not None
    assert not view1.completed

    # Accept post 3 → batch completes.
    view2 = await orch.accept_replay(campaign_id)
    assert view2.completed is True
    assert view2.cancelled_at_post_id is None
    assert view2.accepted_post_ids == post_ids[1:]

    # Both posts now point at the new alternates as primary.
    posts = await scenes.get_posts(scene_id)
    p2 = next(p for p in posts if p.id == post_ids[1])
    p3 = next(p for p in posts if p.id == post_ids[2])
    assert p2.primary_alternate_id != "a_p1"
    assert p3.primary_alternate_id != "a_p2"


async def test_replay_try_again_replaces_in_flight_alternate(
    tmp_path: Path, real_store: StateStore
) -> None:
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, scene_id, post_ids = await _seed_three_model_posts(scenes, real_store)
    orch = _make_orch(scenes, real_store)

    result = await orch.retcon_post(post_ids[0], "edit", replay_subsequent=True)
    state = orch.retcon_replay.get(result.replay_batch_id)  # type: ignore[arg-type]
    first_alt_id = state.current_alternate_id
    assert first_alt_id is not None

    # Try again → drop the in-flight alternate, regenerate.
    view = await orch.try_again_replay(campaign_id)
    assert view.current_index == 0
    assert view.current_alternate_id is not None
    assert view.current_alternate_id != first_alt_id

    # The dropped alternate is gone from the sidecar; only the new one remains
    # (plus the original primary).
    posts = await scenes.get_posts(scene_id)
    p2 = next(p for p in posts if p.id == post_ids[1])
    ids = {a.id for a in p2.alternates}
    assert first_alt_id not in ids
    assert view.current_alternate_id in ids


async def test_replay_cancel_finalizes_at_current_post(
    tmp_path: Path, real_store: StateStore
) -> None:
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, scene_id, post_ids = await _seed_three_model_posts(scenes, real_store)
    orch = _make_orch(scenes, real_store)

    result = await orch.retcon_post(post_ids[0], "edit", replay_subsequent=True)
    assert result.replay_batch_id is not None
    # Accept post 2; cancel at post 3.
    await orch.accept_replay(campaign_id)
    view = await orch.cancel_replay(campaign_id)
    assert view.completed is True
    assert view.cancelled_at_post_id == post_ids[2]
    assert post_ids[1] in view.accepted_post_ids

    # Post 3 keeps its original primary; the in-flight alternate is gone.
    posts = await scenes.get_posts(scene_id)
    p3 = next(p for p in posts if p.id == post_ids[2])
    assert p3.primary_alternate_id == "a_p2"

    # And the batch is closed — a second accept on the same campaign raises.
    with pytest.raises(RetconBatchClosedError):
        await orch.accept_replay(campaign_id)


async def test_concurrent_replay_rejected_with_in_flight(
    tmp_path: Path, real_store: StateStore
) -> None:
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    _campaign_id, scene_id, post_ids = await _seed_three_model_posts(scenes, real_store)
    orch = _make_orch(scenes, real_store)

    await orch.retcon_post(post_ids[0], "edit", replay_subsequent=True)
    # Snapshot the second post's body BEFORE the rejected retcon attempt;
    # the in-flight guard must fire before any mutation happens.
    posts_before = await scenes.get_posts(scene_id)
    body_before = next(p.body for p in posts_before if p.id == post_ids[1])

    with pytest.raises(RetconInFlightError):
        await orch.retcon_post(post_ids[1], "edit2", replay_subsequent=True)

    posts_after = await scenes.get_posts(scene_id)
    body_after = next(p.body for p in posts_after if p.id == post_ids[1])
    assert body_after == body_before, "rejected retcon must not mutate the post"


async def test_replay_with_no_subsequent_posts_completes_immediately(
    tmp_path: Path, real_store: StateStore
) -> None:
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    _campaign_id, _scene_id, post_ids = await _seed_three_model_posts(scenes, real_store)
    orch = _make_orch(scenes, real_store)

    # Retcon the LAST post — nothing follows it.
    result = await orch.retcon_post(post_ids[-1], "edit", replay_subsequent=True)
    batch_id = result.replay_batch_id
    assert batch_id is not None
    state = orch.retcon_replay.get(batch_id)
    assert state.completed is True
    assert state.subsequent_post_ids == []


async def test_replay_emits_event_sequence(tmp_path: Path, real_store: StateStore) -> None:
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, _scene_id, post_ids = await _seed_three_model_posts(scenes, real_store)

    bus = EventBus()
    seen: list[str] = []

    def _record(event):
        seen.append(event.type)

    bus.subscribe("retcon_started", _record)
    bus.subscribe("retcon_post_replayed", _record)
    bus.subscribe("retcon_post_accepted", _record)
    bus.subscribe("retcon_complete", _record)
    bus.subscribe("retcon_cancelled", _record)

    orch = OrchestratorService(
        event_bus=bus,
        scene_manager=scenes,
        llm_gateway=FakeGateway(chunks=["x"]),
        context_builder=FakeContextBuilder(),
        extractor=FakeExtractor(deltas=[_new_delta("a")]),
        state_store=real_store,
        ws_push=WSCollector(),
    )

    await orch.retcon_post(post_ids[0], "edit", replay_subsequent=True)
    # Start emits retcon_started + retcon_post_replayed for post 2.
    assert seen[0] == "retcon_started"
    assert seen[1] == "retcon_post_replayed"
    await orch.accept_replay(campaign_id)
    # Accept emits retcon_post_accepted + retcon_post_replayed (next post's alt).
    assert "retcon_post_accepted" in seen
    await orch.accept_replay(campaign_id)
    # Final accept emits retcon_complete.
    assert seen[-1] == "retcon_complete"


async def test_get_replay_state_unknown_batch_raises(
    tmp_path: Path, real_store: StateStore
) -> None:
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, _scene_id, _post_ids = await _seed_three_model_posts(scenes, real_store)
    orch = _make_orch(scenes, real_store)
    with pytest.raises(RetconBatchNotFoundError):
        await orch.get_replay_state(campaign_id, "rb_nope")


async def test_action_with_stale_batch_id_raises_not_found(
    tmp_path: Path, real_store: StateStore
) -> None:
    """Closing the TOCTOU race: if the batch_id passed by the client doesn't
    match the currently-open batch (because someone else cancelled it and
    started a new one between the GET and the POST), the session refuses
    rather than silently acting on the new batch."""
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, _scene_id, post_ids = await _seed_three_model_posts(scenes, real_store)
    orch = _make_orch(scenes, real_store)

    await orch.retcon_post(post_ids[0], "edit", replay_subsequent=True)
    with pytest.raises(RetconBatchNotFoundError):
        await orch.accept_replay(campaign_id, batch_id="rb_stale")


async def test_closed_batches_evicted_when_new_batch_starts(
    tmp_path: Path, real_store: StateStore
) -> None:
    """``_closed`` is keyed by batch_id, so without explicit eviction it
    grows unboundedly across long-lived processes. Starting a new batch
    on the same campaign must prune the old terminal entries."""
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    _campaign_id, _scene_id, post_ids = await _seed_three_model_posts(scenes, real_store)
    orch = _make_orch(scenes, real_store)

    # First batch: retcon the LAST post → no subsequents → immediately closes.
    first = await orch.retcon_post(post_ids[-1], "edit", replay_subsequent=True)
    first_batch_id = first.replay_batch_id
    assert first_batch_id is not None
    # The closed batch is reachable until a new one opens for this campaign.
    closed = orch.retcon_replay.get(first_batch_id)
    assert closed.completed is True

    # Second batch on the same campaign → first batch's entry is pruned.
    second = await orch.retcon_post(post_ids[0], "edit-again", replay_subsequent=True)
    assert second.replay_batch_id is not None
    with pytest.raises(RetconBatchNotFoundError):
        orch.retcon_replay.get(first_batch_id)


async def test_cancel_restores_original_primary_delta_set(
    tmp_path: Path, real_store: StateStore
) -> None:
    """Cancel-mid-replay leaves neither the original nor the in-flight delta
    set applied unless we explicitly re-activate the original. Verify the
    state store's current pointer for the cancel-point post is back at
    the original primary's delta set after cancel."""
    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, _scene_id, post_ids = await _seed_three_model_posts(scenes, real_store)
    orch = _make_orch(scenes, real_store)

    # Start replay. Generation rewinds post_ids[1]'s original primary (ds_p1)
    # and applies the new alternate's set. Cancel → both should rewind, then
    # original should be re-activated.
    await orch.retcon_post(post_ids[0], "edit", replay_subsequent=True)
    view = await orch.cancel_replay(campaign_id)
    assert view.completed is True
    assert view.cancelled_at_post_id == post_ids[1]
    # The original primary's delta set is active again on the second post.
    row = await real_store.db.fetchone(
        "SELECT reversed_at FROM deltas WHERE delta_set_id=?",
        ("ds_p1",),
    )
    assert row is not None
    # `re_activate_delta_set` clears reversed_at so the row is live again.
    assert row["reversed_at"] is None


async def test_concurrent_accepts_serialized_by_lock(
    tmp_path: Path, real_store: StateStore
) -> None:
    """Two coroutines firing accept() concurrently must not both pass the
    open-batch check and double-advance. The per-campaign lock serialises
    them; the second observes the index advance from the first."""
    import asyncio

    scenes_root = tmp_path / "scenes_root"
    scenes_root.mkdir()
    scenes = SceneManager(scenes_root, config=SceneManagerConfig(running_summary_every_n_posts=0))
    campaign_id, _scene_id, post_ids = await _seed_three_model_posts(scenes, real_store)
    orch = _make_orch(scenes, real_store)

    await orch.retcon_post(post_ids[0], "edit", replay_subsequent=True)
    # Fire two accepts. Without the lock, both would observe current_index=0
    # and append two entries; with the lock, the second waits and either
    # completes against the next post (post 3) or raises if no more.
    results = await asyncio.gather(
        orch.accept_replay(campaign_id),
        orch.accept_replay(campaign_id),
        return_exceptions=True,
    )
    # The first accept advanced to post 3; the second either accepts post 3
    # (completing the batch) or — if both somehow raced — leaves invalid
    # state. We assert no duplicates and a consistent terminal state.
    state = (
        orch.retcon_replay.get(orch.retcon_replay._open[campaign_id].batch_id)
        if orch.retcon_replay.is_active(campaign_id)
        else None
    )
    if state is None:
        # Batch completed — exactly two distinct accepted posts.
        # Pull the closed batch by scanning _closed.
        closed = next(iter(orch.retcon_replay._closed.values()))
        assert closed.accepted_post_ids == post_ids[1:]
    else:
        assert len(state.accepted_post_ids) == len({*state.accepted_post_ids})
    # Neither call should have raised an unrelated error.
    for r in results:
        if isinstance(r, BaseException) and not isinstance(r, RetconBatchClosedError):
            raise r
