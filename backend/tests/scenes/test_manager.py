from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from grimoire.scenes import (
    ADVANCE_DISABLED,
    ADVANCE_REQUESTED,
    PC_POST_APPENDED,
    POST_APPENDED,
    SCENE_ENDED,
    SCENE_STARTED,
    AuthorKind,
    InMemoryEventBus,
    NothingToAdvance,
    SceneInit,
    SceneManager,
    SceneManagerConfig,
    Thread,
    new_post,
)


def _manager(tmp_path: Path, **kwargs) -> tuple[SceneManager, InMemoryEventBus]:
    bus = InMemoryEventBus()
    config = kwargs.pop("config", SceneManagerConfig(running_summary_every_n_posts=0))
    manager = SceneManager(tmp_path, config=config, event_bus=bus, **kwargs)
    return manager, bus


@pytest.fixture
def manager(tmp_path: Path) -> SceneManager:
    m, _ = _manager(tmp_path)
    return m


async def test_start_scene_writes_files_and_emits_event(tmp_path: Path) -> None:
    manager, bus = _manager(tmp_path)
    scene = await manager.start_scene(
        SceneInit(
            campaign_id="campaign-a",
            title="Elysium Opening",
            location_ref="elysium",
            in_game_start=datetime(2024, 10, 31, 22, 0, 0),
            present_pc_refs=["alistair"],
            present_character_refs=["alistair"],
        )
    )
    assert scene.ordinal == 1
    assert scene.slug == "elysium-opening"
    md_path = tmp_path / "campaigns" / "campaign-a" / "scenes" / "0001-elysium-opening.md"
    yaml_path = md_path.with_suffix(".yaml")
    assert md_path.exists()
    assert yaml_path.exists()
    assert any(e.type == SCENE_STARTED for e in bus.events)


async def test_start_scene_assigns_consecutive_ordinals(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    s1 = await manager.start_scene(SceneInit(campaign_id="c", title="One"))
    s2 = await manager.start_scene(SceneInit(campaign_id="c", title="Two"))
    assert (s1.ordinal, s2.ordinal) == (1, 2)


async def test_append_post_updates_files_and_counts(tmp_path: Path) -> None:
    manager, bus = _manager(tmp_path)
    scene = await manager.start_scene(
        SceneInit(campaign_id="c", title="Elysium", present_pc_refs=["alistair"])
    )
    await manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="The tower is candle-lit.", is_player=False),
    )
    await manager.append_post(
        scene.id,
        new_post(
            author_kind=AuthorKind.PC,
            author_pc_ref="alistair",
            body="I incline my head.",
            is_player=True,
        ),
    )
    refreshed = await manager.get_scene(scene.id)
    assert refreshed.post_count == 2
    posts = await manager.get_posts(scene.id)
    assert [p.order_in_scene for p in posts] == [1, 2]
    assert posts[1].author_pc_ref == "alistair"
    assert any(e.type == PC_POST_APPENDED for e in bus.events)
    assert sum(1 for e in bus.events if e.type == POST_APPENDED) == 2

    md = (tmp_path / "campaigns" / "c" / "scenes" / "0001-elysium.md").read_text(encoding="utf-8")
    assert "## Post 1 — narrator" in md
    assert "## Post 2 — pc:alistair" in md


async def test_cannot_append_to_closed_scene(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    await manager.close_scene(scene.id, closed_at_turn="t1")
    with pytest.raises(RuntimeError):
        await manager.append_post(
            scene.id,
            new_post(author_kind=AuthorKind.NARRATOR, body="too late", is_player=False),
        )


async def test_single_pc_scene_auto_responds(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    scene = await manager.start_scene(
        SceneInit(campaign_id="c", title="Scene", present_pc_refs=["alistair"])
    )
    post = new_post(
        author_kind=AuthorKind.PC,
        author_pc_ref="alistair",
        body="...",
        is_player=True,
    )
    decision = await manager.on_post_submitted(scene.id, post)
    assert decision.auto_respond is True
    assert decision.reason == "single_pc_scene"


async def test_multi_pc_scene_requires_advance(tmp_path: Path) -> None:
    manager, _bus = _manager(tmp_path)
    scene = await manager.start_scene(
        SceneInit(
            campaign_id="c",
            title="Crossover",
            present_pc_refs=["alistair", "beatrice"],
        )
    )
    post = new_post(
        author_kind=AuthorKind.PC,
        author_pc_ref="alistair",
        body="...",
        is_player=True,
    )
    decision = await manager.on_post_submitted(scene.id, post)
    assert decision.auto_respond is False
    assert decision.reason == "multi_pc_pending_advance"


async def test_advance_request_marks_last_advance(tmp_path: Path) -> None:
    manager, bus = _manager(tmp_path)
    scene = await manager.start_scene(
        SceneInit(
            campaign_id="c",
            title="Crossover",
            present_pc_refs=["alistair", "beatrice"],
        )
    )
    for pc in ("alistair", "beatrice"):
        await manager.append_post(
            scene.id,
            new_post(
                author_kind=AuthorKind.PC,
                author_pc_ref=pc,
                body=f"{pc} speaks",
                is_player=True,
            ),
        )
    result = await manager.on_advance_requested(scene.id)
    assert [p.order_in_scene for p in result.pending_posts] == [1, 2]
    assert result.scene.last_advance_at_post == 2
    assert any(e.type == ADVANCE_REQUESTED for e in bus.events)

    with pytest.raises(NothingToAdvance):
        await manager.on_advance_requested(scene.id)


async def test_pc_entering_disables_advance(tmp_path: Path) -> None:
    manager, bus = _manager(tmp_path)
    scene = await manager.start_scene(
        SceneInit(campaign_id="c", title="Scene", present_pc_refs=["alistair"])
    )
    await manager.add_present_pc(scene.id, "beatrice")
    assert any(e.type == ADVANCE_DISABLED for e in bus.events)
    refreshed = await manager.get_scene(scene.id)
    assert set(refreshed.present_pc_refs) == {"alistair", "beatrice"}


async def test_running_summary_triggers_on_cadence(tmp_path: Path) -> None:
    from grimoire.scenes.summary_jobs import RunningSummaryWorker

    calls: list[tuple[str | None, int]] = []

    async def summarize(previous, recent):
        calls.append((previous, len(recent)))
        return f"summary after {len(recent)}"

    config = SceneManagerConfig(running_summary_every_n_posts=2)
    bus = InMemoryEventBus()
    manager = SceneManager(
        tmp_path,
        config=config,
        event_bus=bus,
        summarizer=summarize,
    )
    worker = RunningSummaryWorker(manager, bus)
    worker.start()
    scene = await manager.start_scene(
        SceneInit(campaign_id="c", title="Scene", present_pc_refs=["alistair"])
    )
    for i in range(4):
        await manager.append_post(
            scene.id,
            new_post(author_kind=AuthorKind.NARRATOR, body=f"line {i}", is_player=False),
        )
    await worker.drain()
    await worker.stop()
    refreshed = await manager.get_scene(scene.id)
    assert refreshed.running_summary == "summary after 4"
    # Coalesced: the two cadence events (post 2 + post 4) collapse into a
    # single trailing pass when they arrive during the same scheduling slice.
    assert len(calls) in (1, 2)


async def test_threads_introduced_and_paid_off(tmp_path: Path) -> None:
    manager, _bus = _manager(tmp_path)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    await manager.add_thread(
        scene.id, Thread(text="The Prince has summoned Alistair"), "introduced"
    )
    await manager.add_thread(scene.id, Thread(text="Resolved later"), "paid_off")
    threads = await manager.list_threads(scene.id)
    assert [t.text for t in threads.introduced] == ["The Prince has summoned Alistair"]
    assert [t.text for t in threads.paid_off] == ["Resolved later"]


async def test_close_scene_returns_report(tmp_path: Path) -> None:
    manager, bus = _manager(tmp_path)
    scene = await manager.start_scene(
        SceneInit(
            campaign_id="c",
            title="Scene",
            in_game_start=datetime(2024, 1, 1, 12, 0, 0),
        )
    )
    await manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="opening line", is_player=False),
    )
    await manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="closing line", is_player=False),
    )
    await manager.add_thread(scene.id, Thread(text="Unresolved mystery"), "introduced")
    report = await manager.close_scene(scene.id, closed_at_turn="t123")
    assert report.scene.closed is True
    assert report.scene.closed_at_turn == "t123"
    assert any(t.text == "Unresolved mystery" for t in report.threads_unresolved)
    assert report.threads_unresolved[0].introduced_at_post == 2
    assert any(e.type == SCENE_ENDED for e in bus.events)


async def test_edit_post_updates_markdown(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    post = new_post(author_kind=AuthorKind.NARRATOR, body="original", is_player=False)
    await manager.append_post(scene.id, post)
    appended = (await manager.get_posts(scene.id))[0]
    await manager.edit_post(appended.id, "revised", source="user")
    posts = await manager.get_posts(scene.id)
    assert posts[0].body == "revised"


async def test_delete_post_reorders_remaining(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    for i in range(3):
        await manager.append_post(
            scene.id,
            new_post(author_kind=AuthorKind.NARRATOR, body=f"line {i}", is_player=False),
        )
    second = (await manager.get_posts(scene.id))[1]
    await manager.delete_post(second.id, source="user")
    posts = await manager.get_posts(scene.id)
    assert [p.order_in_scene for p in posts] == [1, 2]
    assert [p.body for p in posts] == ["line 0", "line 2"]
    refreshed = await manager.get_scene(scene.id)
    assert refreshed.post_count == 2


async def test_active_scene_tracking(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    s1 = await manager.start_scene(
        SceneInit(campaign_id="c", title="One", present_pc_refs=["alistair"])
    )
    s2 = await manager.start_scene(
        SceneInit(campaign_id="c", title="Two", present_pc_refs=["beatrice"])
    )
    assert (await manager.active_scene_for_campaign("c")).id == s2.id
    assert (await manager.active_scene_for_pc("c", "alistair")).id == s1.id
    assert (await manager.active_scene_for_pc("c", "beatrice")).id == s2.id
    assert await manager.active_scene_for_pc("c", "vance") is None


async def test_fork_copies_scenes_to_branch(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    await manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="prologue", is_player=False),
    )
    forked = await manager.fork_scenes_for_branch("c", "what-if")
    assert len(forked) == 1
    assert forked[0].branch_id == "what-if"
    branch_md = tmp_path / "campaigns" / "c" / "branches" / "what-if" / "scenes" / "0001-scene.md"
    assert branch_md.exists()
    assert "prologue" in branch_md.read_text()


async def test_list_scenes_sorts_by_ordinal(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    for title in ("A", "B", "C"):
        await manager.start_scene(SceneInit(campaign_id="c", title=title))
    scenes = await manager.list_scenes("c")
    assert [s.ordinal for s in scenes] == [1, 2, 3]


async def test_reindex_from_disk_updates_post_count(tmp_path: Path) -> None:
    manager, _bus = _manager(tmp_path)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    md_path = await manager.get_scene_file_path(scene.id)
    md_path.write_text(
        "## Post 1 — narrator\n\nhello\n\n## Post 2 — narrator\n\nworld\n",
        encoding="utf-8",
    )
    refreshed = await manager.reindex_from_disk(scene.id)
    assert refreshed.post_count == 2


async def test_running_summary_due_event_emitted_on_cadence(tmp_path: Path) -> None:
    """§4 — append_post emits running_summary_due instead of blocking inline."""
    from grimoire.scenes import RUNNING_SUMMARY_DUE

    config = SceneManagerConfig(running_summary_every_n_posts=2)
    manager, bus = _manager(tmp_path, config=config)
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    for i in range(4):
        await manager.append_post(
            scene.id,
            new_post(author_kind=AuthorKind.NARRATOR, body=f"line {i}", is_player=False),
        )
    due_events = [e for e in bus.events if e.type == RUNNING_SUMMARY_DUE]
    # Cadence trips at post 2 and post 4.
    assert [e.payload["post_count"] for e in due_events] == [2, 4]


async def test_thread_detector_seam(tmp_path: Path) -> None:
    """§6 — detect_threads delegates to the injected callable when enabled."""

    async def fake_detector(scene, posts):
        return [(Thread(text=f"detected from {len(posts)} posts"), "introduced")]

    config = SceneManagerConfig(running_summary_every_n_posts=0)
    config.thread_detection.enabled = True
    bus = InMemoryEventBus()
    manager = SceneManager(
        tmp_path,
        config=config,
        event_bus=bus,
        thread_detector=fake_detector,
    )
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    await manager.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="lead", is_player=False),
    )
    proposals = await manager.detect_threads(scene.id)
    assert len(proposals) == 1
    assert proposals[0][1] == "introduced"
    assert proposals[0][0].text == "detected from 1 posts"


async def test_thread_detector_disabled_returns_empty(tmp_path: Path) -> None:
    async def fake_detector(scene, posts):
        return [(Thread(text="should not run"), "introduced")]

    bus = InMemoryEventBus()
    manager = SceneManager(
        tmp_path,
        config=SceneManagerConfig(running_summary_every_n_posts=0),
        event_bus=bus,
        thread_detector=fake_detector,
    )
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    assert await manager.detect_threads(scene.id) == []


async def test_post_records_survive_restart(tmp_path: Path) -> None:
    """§2 — id/turn_id/created_at/is_player round-trip via the sidecar."""
    manager_a, _ = _manager(tmp_path)
    scene = await manager_a.start_scene(SceneInit(campaign_id="c", title="Scene"))
    post = new_post(
        author_kind=AuthorKind.PC,
        author_pc_ref="alistair",
        body="player line",
        is_player=True,
    )
    await manager_a.append_post(scene.id, post)
    original = (await manager_a.get_posts(scene.id))[0]
    assert original.id == post.id

    # Simulate a process restart by constructing a fresh manager.
    manager_b, _ = _manager(tmp_path)
    rehydrated = (await manager_b.get_posts(scene.id))[0]
    assert rehydrated.id == post.id
    assert rehydrated.turn_id == post.turn_id
    assert rehydrated.is_player is True


async def test_multi_pc_leave_flushes_advance_watermark(tmp_path: Path) -> None:
    """§8 — when a PC leaves and present_pc_refs drops to ≤1, pending posts
    are flushed so the now-single PC's submission auto-responds again."""
    from grimoire.scenes import ADVANCE_ENABLED

    manager, bus = _manager(tmp_path)
    scene = await manager.start_scene(
        SceneInit(
            campaign_id="c",
            title="Crossover",
            present_pc_refs=["alistair", "beatrice"],
        )
    )
    for pc in ("alistair", "beatrice"):
        await manager.append_post(
            scene.id,
            new_post(
                author_kind=AuthorKind.PC,
                author_pc_ref=pc,
                body=f"{pc} speaks",
                is_player=True,
            ),
        )
    refreshed = await manager.get_scene(scene.id)
    assert refreshed.last_advance_at_post == 0

    await manager.remove_present_character(scene.id, "beatrice")

    refreshed = await manager.get_scene(scene.id)
    assert refreshed.last_advance_at_post == 2
    advance_events = [e for e in bus.events if e.type == ADVANCE_ENABLED]
    assert advance_events, "advance_enabled was not emitted"
    assert advance_events[-1].payload.get("flushed_to_post") == 2

    # The remaining PC's next post submission now auto-responds again.
    decision = await manager.on_post_submitted(
        scene.id,
        new_post(
            author_kind=AuthorKind.PC,
            author_pc_ref="alistair",
            body="still here",
            is_player=True,
        ),
    )
    assert decision.auto_respond is True


async def test_thread_provenance_round_trips(tmp_path: Path) -> None:
    """§10 — sidecar persists each thread with introduced_at_post / paid_off_at_post."""
    manager_a, _ = _manager(tmp_path)
    scene = await manager_a.start_scene(SceneInit(campaign_id="c", title="Scene"))
    await manager_a.append_post(
        scene.id,
        new_post(author_kind=AuthorKind.NARRATOR, body="setup", is_player=False),
    )
    await manager_a.add_thread(scene.id, Thread(text="loose end"), "introduced")
    threads_a = await manager_a.list_threads(scene.id)
    assert threads_a.introduced[0].introduced_at_post == 1

    # Reload from disk.
    manager_b, _ = _manager(tmp_path)
    threads_b = await manager_b.list_threads(scene.id)
    assert threads_b.introduced[0].text == "loose end"
    assert threads_b.introduced[0].introduced_at_post == 1


async def test_legacy_thread_sidecar_loads(tmp_path: Path) -> None:
    """§10 backward compat — sidecars written with the old string-list shape
    still load (the parser upgrades them to Thread objects)."""
    import yaml as _yaml

    # Build a sidecar by hand with the legacy schema.
    sidecar = tmp_path / "campaigns" / "c" / "scenes" / "0001-scene.yaml"
    sidecar.parent.mkdir(parents=True)
    (sidecar.parent / "0001-scene.md").write_text("")
    sidecar.write_text(
        _yaml.safe_dump(
            {
                "id": "c:0001-scene",
                "campaign_id": "c",
                "branch_id": "main",
                "ordinal": 1,
                "slug": "scene",
                "title": "Scene",
                "threads_introduced": ["old style"],
                "threads_paid_off": [],
            }
        )
    )

    manager, _ = _manager(tmp_path)
    threads = await manager.list_threads("c:0001-scene")
    assert [t.text for t in threads.introduced] == ["old style"]
    assert threads.introduced[0].introduced_at_post is None


async def test_scene_break_classifier_refines_borderline(tmp_path: Path) -> None:
    """§7 — when heuristic confidence is between prompt and auto thresholds,
    the optional LLM classifier overrides the decision."""
    from grimoire.scenes.types import SceneBreakDecision

    async def force_break(scene, player_input, recent):
        return SceneBreakDecision(is_break=True, confidence=0.95, reason="llm:tonal_shift")

    bus = InMemoryEventBus()
    manager = SceneManager(
        tmp_path,
        config=SceneManagerConfig(running_summary_every_n_posts=0),
        event_bus=bus,
        scene_break_classifier=force_break,
    )
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    # Trigger a tonal_shift heuristic match (confidence 0.55, borderline).
    decision = await manager.is_scene_break(
        scene.id, "HE SCREAMED in unfettered rage at the dying sky"
    )
    assert decision.confidence == 0.95
    assert decision.reason == "llm:tonal_shift"


async def test_scene_break_classifier_left_alone_at_high_confidence(
    tmp_path: Path,
) -> None:
    """The classifier never sees decisions above the auto threshold."""
    from grimoire.scenes.types import SceneBreakDecision

    calls: list = []

    async def force_no_break(scene, player_input, recent):
        calls.append(player_input)
        return SceneBreakDecision(is_break=False, confidence=0.0, reason="overridden")

    bus = InMemoryEventBus()
    manager = SceneManager(
        tmp_path,
        config=SceneManagerConfig(running_summary_every_n_posts=0),
        event_bus=bus,
        scene_break_classifier=force_no_break,
    )
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="Scene"))
    # /end scene gets confidence 1.0 — never reaches the classifier.
    decision = await manager.is_scene_break(scene.id, "/end scene")
    assert decision.is_break is True
    assert decision.reason == "user_signal"
    assert not calls


async def test_is_scene_break_uses_active_scene(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    scene = await manager.start_scene(
        SceneInit(
            campaign_id="c",
            title="Scene",
            location_ref="elysium",
            in_game_start=datetime(2024, 10, 31, 22, 0, 0),
        )
    )
    decision = await manager.is_scene_break(scene.id, "/end scene")
    assert decision.is_break is True
    assert decision.reason == "user_signal"
