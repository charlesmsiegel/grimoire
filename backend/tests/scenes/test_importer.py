from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from grimoire.api.campaigns.import_scene import router as import_router
from grimoire.scenes.importer import ImportProgress, parse_import_source, run_import_pipeline


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
        "## Post 1 — narrator\n\nRain falls.\n\n"
        "## Post 2 — pc:beatrice\n\nI enter.\n",
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


@pytest.mark.asyncio
async def test_run_import_pipeline_progress_events(tmp_path: Path) -> None:
    """Verify the pipeline yields the right progress steps."""
    md = tmp_path / "scene.md"
    md.write_text(
        "## Post 1 — narrator\n\nHello.\n\n"
        "## Post 2 — pc:alice\n\nHi.\n",
        encoding="utf-8",
    )
    scene_manager = AsyncMock()
    scene_mock = MagicMock()
    scene_mock.id = "camp:0001-test"
    scene_mock.campaign_id = "camp"
    scene_mock.branch_id = "main"
    scene_manager.start_scene.return_value = scene_mock
    scene_manager.detect_threads.return_value = []
    scene_manager.generate_summary.return_value = ("Summary", ["beat1"])

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
