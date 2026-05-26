from __future__ import annotations

from pathlib import Path

from grimoire.scenes.importer import parse_import_source


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


# --- Preview endpoint tests ---

from starlette.testclient import TestClient

from grimoire.api.campaigns.import_scene import router as import_router


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
