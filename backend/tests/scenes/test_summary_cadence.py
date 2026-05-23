"""Per-campaign summary cadence overrides the SceneManager default."""

from __future__ import annotations

import pytest

from grimoire.scenes.manager import SceneManager, SceneManagerConfig


class _StubBus:
    def __init__(self) -> None:
        self.events: list = []

    async def emit(self, event) -> None:
        self.events.append(event)

    def subscribe(self, *_a, **_kw) -> None:
        pass


@pytest.fixture
def manager(tmp_path) -> SceneManager:
    cfg = SceneManagerConfig()
    # SceneManagerConfig().running_summary_every_n_posts defaults to 5;
    # leave the default in place for these tests.
    return SceneManager(
        data_root=tmp_path,
        event_bus=_StubBus(),
        config=cfg,
    )


def test_should_emit_running_summary_when_count_matches_default(manager: SceneManager) -> None:
    # Default cadence = 5; override = None → use default.
    assert manager._should_emit_running_summary(post_count=5, override=None) is True
    assert manager._should_emit_running_summary(post_count=10, override=None) is True
    assert manager._should_emit_running_summary(post_count=3, override=None) is False


def test_zero_override_disables_running_summary(manager: SceneManager) -> None:
    assert manager._should_emit_running_summary(post_count=5, override=0) is False
    assert manager._should_emit_running_summary(post_count=100, override=0) is False


def test_custom_override_changes_cadence(manager: SceneManager) -> None:
    assert manager._should_emit_running_summary(post_count=3, override=3) is True
    assert manager._should_emit_running_summary(post_count=5, override=3) is False
    assert manager._should_emit_running_summary(post_count=6, override=3) is True
