from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from grimoire.api.campaigns.import_scene import router as import_router
from grimoire.scenes.importer import ImportProgress, parse_import_source, run_import_pipeline
from grimoire.scenes.types import Scene


def test_parse_import_source_md_only(tmp_path: Path) -> None:
    md = tmp_path / "scene.md"
    md.write_text(
        "## Post 1 — narrator\n\nThe tower looms.\n\n"
        "## Post 2 — pc:alistair\n\nI step inside.\n\n"
        "## Post 3 — npc:gardner\n\nWelcome, my lord.\n",
        encoding="utf-8",
    )
    result = parse_import_source(md)
    assert result.post_count == 3
    assert result.detected_pc_refs == ["alistair"]
    assert result.detected_npc_refs == ["gardner"]
    assert result.sidecar_metadata is None


def test_parse_import_source_with_sidecar(tmp_path: Path) -> None:
    md = tmp_path / "0001-tower.md"
    md.write_text("## Post 1 — narrator\n\nHello.\n", encoding="utf-8")
    yaml = tmp_path / "0001-tower.yaml"
    yaml.write_text(
        "title: The Tower\nlocation_ref: blackspire\nmood: tense\ntags:\n  - night\n",
        encoding="utf-8",
    )
    result = parse_import_source(md)
    assert result.post_count == 1
    assert result.sidecar_metadata is not None
    assert result.sidecar_metadata["title"] == "The Tower"
    assert result.sidecar_metadata["mood"] == "tense"


def test_parse_import_source_bad_format(tmp_path: Path) -> None:
    md = tmp_path / "plain.md"
    md.write_text("Just some prose with no post headings.", encoding="utf-8")
    result = parse_import_source(md)
    assert result.post_count == 0


def _make_test_app():
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(import_router, prefix="/campaigns")
    return app


def test_preview_endpoint(tmp_path: Path) -> None:
    md = tmp_path / "0002-tavern.md"
    md.write_text(
        "## Post 1 — narrator\n\nRain falls.\n\n## Post 2 — pc:beatrice\n\nI enter.\n",
        encoding="utf-8",
    )
    yaml = tmp_path / "0002-tavern.yaml"
    yaml.write_text("title: Tavern Rain\nmood: melancholy\n", encoding="utf-8")

    client = TestClient(_make_test_app())
    resp = client.post(
        "/campaigns/test-campaign/scenes/import/preview",
        json={"path": str(md)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["post_count"] == 2
    assert data["detected_characters"]["pc_refs"] == ["beatrice"]
    assert data["sidecar"]["title"] == "Tavern Rain"


def test_preview_endpoint_not_found() -> None:
    client = TestClient(_make_test_app())
    resp = client.post(
        "/campaigns/test-campaign/scenes/import/preview",
        json={"path": "/nonexistent/scene.md"},
    )
    assert resp.status_code == 400


def _make_scene_manager(tmp_path: Path, *, state_store: object | None = None) -> AsyncMock:
    """Build a SceneManager test double for ``run_import_pipeline``.

    A blanket ``AsyncMock()`` auto-creates *every* attribute as an awaitable
    child — including structural/sync members the real ``SceneManager`` holds
    (``data_root``, ``config``, ``_state_store``, the active-scene maps). When
    the pipeline then calls e.g. ``naming_pattern.format(...)`` or enters the
    cadence-suppression branch, those auto-mocked coroutines are never awaited
    and leak ``RuntimeWarning: coroutine ... was never awaited``.

    So pin the structural members to real values and leave only the genuinely
    async methods (``start_scene``/``append_post``/...) as awaitable mocks.
    """
    scene_manager = AsyncMock()
    # Structural / sync members — must be real so the pipeline doesn't await them.
    scene_manager.data_root = tmp_path
    scene_manager.config = None  # -> default scene-naming pattern, no mock .format()
    scene_manager._active_scene = {}
    scene_manager._pc_current_scene = {}
    scene_manager._state_store = state_store

    scene = Scene(id="camp:0001-test", campaign_id="camp", ordinal=1, slug="test", title="Test")
    scene_manager.start_scene.return_value = scene
    scene_manager.detect_threads.return_value = []
    scene_manager.generate_summary.return_value = ("Summary", ["beat1"])
    return scene_manager


@pytest.mark.asyncio
async def test_run_import_pipeline_progress_events(tmp_path: Path) -> None:
    """Verify the pipeline yields the right progress steps.

    With ``_state_store=None`` the documented "no state store -> skip cadence
    suppression" path is taken; see the cadence test below for the wired path.
    """
    md = tmp_path / "scene.md"
    md.write_text(
        "## Post 1 — narrator\n\nHello.\n\n## Post 2 — pc:alice\n\nHi.\n",
        encoding="utf-8",
    )
    scene_manager = _make_scene_manager(tmp_path)

    events: list[ImportProgress] = []
    async for progress in run_import_pipeline(
        scene_manager=scene_manager,
        md_path=md,
        campaign_id="camp",
        title="Test",
        metadata={},
    ):
        events.append(progress)

    steps = [e.step for e in events]
    assert "copy" in steps
    assert steps.count("append") == 2
    assert "threads" in steps
    assert "summarize" in steps
    assert "done" in steps
    assert scene_manager.start_scene.called
    assert scene_manager.append_post.call_count == 2


def test_import_endpoint_error_frame_carries_status(tmp_path: Path) -> None:
    """A pipeline ValueError surfaces as an error frame with an HTTP status."""
    from grimoire.api.deps import get_container, get_scenes, get_state_store

    md = tmp_path / "plain.md"
    md.write_text("Just some prose with no post headings.", encoding="utf-8")

    app = _make_test_app()
    state_store = MagicMock()
    state_store.db.fetchone = AsyncMock(return_value={"id": "camp"})
    app.dependency_overrides[get_state_store] = lambda: state_store
    app.dependency_overrides[get_scenes] = lambda: AsyncMock()
    app.dependency_overrides[get_container] = lambda: MagicMock()

    client = TestClient(app)
    resp = client.post(
        "/campaigns/camp/scenes/import",
        json={"path": str(md), "title": "Plain"},
    )

    assert resp.status_code == 200
    assert "event: error" in resp.text
    assert '"status": 400' in resp.text


class _RecordingDb:
    """Minimal async DB double recording the cadence reads/writes."""

    def __init__(self, config_json: str | None) -> None:
        self._config_json = config_json
        self.executed: list[tuple[str, tuple]] = []

    async def fetchone(self, _sql: str, _params: tuple) -> dict | None:
        return {"config": self._config_json}

    async def execute(self, sql: str, params: tuple) -> None:
        self.executed.append((sql, params))


class _RecordingStateStore:
    def __init__(self, config_json: str | None) -> None:
        self.db = _RecordingDb(config_json)


@pytest.mark.asyncio
async def test_run_import_pipeline_suppresses_then_restores_cadence(tmp_path: Path) -> None:
    """The wired state-store path overrides the running-summary cadence to 0
    during bulk append, then restores the saved value afterwards."""
    md = tmp_path / "scene.md"
    md.write_text(
        "## Post 1 — narrator\n\nHello.\n\n## Post 2 — pc:alice\n\nHi.\n",
        encoding="utf-8",
    )
    config_json = json.dumps({"summaries": {"running_every_n_posts": 3}})
    state_store = _RecordingStateStore(config_json)
    scene_manager = _make_scene_manager(tmp_path, state_store=state_store)

    async for _ in run_import_pipeline(
        scene_manager=scene_manager,
        md_path=md,
        campaign_id="camp",
        title="Test",
        metadata={},
    ):
        pass

    # First write suppresses the cadence (0), final write restores the saved 3.
    written_values = [params[0] for _sql, params in state_store.db.executed]
    assert written_values == [0, 3]
    assert all(params[-1] == "camp" for _sql, params in state_store.db.executed)
