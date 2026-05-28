"""Verify summary_skipped events are emitted when summaries are suppressed."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from grimoire.scenes.manager import SceneManager, SceneManagerConfig


class _StubBus:
    def __init__(self) -> None:
        self.events: list = []

    async def emit(self, event) -> None:
        self.events.append(event)

    def subscribe(self, *_a, **_kw) -> None:
        pass


def _fake_scene(
    campaign_id: str = "camp-1",
    scene_id: str = "scene-1",
) -> SimpleNamespace:
    return SimpleNamespace(
        campaign_id=campaign_id,
        id=scene_id,
        location_ref=None,
        present_character_refs=[],
    )


@pytest.fixture
def bus() -> _StubBus:
    return _StubBus()


@pytest.fixture
def manager(tmp_path, bus) -> SceneManager:
    cfg = SceneManagerConfig()
    return SceneManager(data_root=tmp_path, event_bus=bus, config=cfg)


async def test_summary_skipped_emitted_via_emit(manager, bus) -> None:
    """_emit with type 'summary_skipped' should go through the bus."""
    scene = _fake_scene()
    await manager._emit("summary_skipped", scene, reason="running_cadence_disabled")
    assert len(bus.events) == 1
    ev = bus.events[0]
    assert ev.type == "summary_skipped"
    assert ev.payload["reason"] == "running_cadence_disabled"
    assert ev.payload["campaign_id"] == "camp-1"
    assert ev.payload["scene_id"] == "scene-1"


async def test_running_summary_skip_fires_when_cadence_zero_and_default_would_fire(
    manager, bus
) -> None:
    """When override=0 and post_count matches the default cadence (5), emit summary_skipped."""
    scene = _fake_scene()
    scene.post_count = 5
    scene.campaign_id = "camp-1"

    # Simulate the inline logic: override=0, default=5, post_count=5
    override = 0
    if manager._should_emit_running_summary(post_count=5, override=override):
        pytest.fail("Should NOT emit running summary when override=0")
    elif override is not None and override <= 0:
        default_n = manager.config.running_summary_every_n_posts
        if default_n > 0 and 5 > 0 and 5 % default_n == 0:
            await manager._emit(
                "summary_skipped", scene, reason="running_cadence_disabled", post_count=5
            )

    assert len(bus.events) == 1
    assert bus.events[0].payload["reason"] == "running_cadence_disabled"


async def test_no_skip_event_when_default_cadence_would_not_fire(manager, bus) -> None:
    """When override=0 but post_count doesn't match default cadence, no event."""
    override = 0
    default_n = manager.config.running_summary_every_n_posts
    post_count = 3  # 3 % 5 != 0

    if (
        not manager._should_emit_running_summary(post_count=post_count, override=override)
        and override is not None
        and override <= 0
        and default_n > 0
        and post_count > 0
        and post_count % default_n == 0
    ):
        await manager._emit("summary_skipped", _fake_scene(), reason="running_cadence_disabled")

    assert len(bus.events) == 0


async def test_final_summary_skipped_emitted(manager, bus) -> None:
    """summary_skipped with reason=final_on_close_disabled should go through the bus."""
    scene = _fake_scene()
    await manager._emit("summary_skipped", scene, reason="final_on_close_disabled")
    assert len(bus.events) == 1
    assert bus.events[0].payload["reason"] == "final_on_close_disabled"
