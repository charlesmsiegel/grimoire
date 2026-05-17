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
    await manager.close_scene(scene.id)
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
    calls: list[tuple[str | None, int]] = []

    async def summarize(previous, recent):
        calls.append((previous, len(recent)))
        return f"summary after {len(recent)}"

    config = SceneManagerConfig(running_summary_every_n_posts=2)
    manager = SceneManager(
        tmp_path,
        config=config,
        event_bus=InMemoryEventBus(),
        summarizer=summarize,
    )
    scene = await manager.start_scene(
        SceneInit(campaign_id="c", title="Scene", present_pc_refs=["alistair"])
    )
    for i in range(4):
        await manager.append_post(
            scene.id,
            new_post(author_kind=AuthorKind.NARRATOR, body=f"line {i}", is_player=False),
        )
    refreshed = await manager.get_scene(scene.id)
    assert refreshed.running_summary == "summary after 4"
    assert len(calls) == 2  # at post 2 and post 4


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
    assert "Unresolved mystery" in report.threads_unresolved
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
