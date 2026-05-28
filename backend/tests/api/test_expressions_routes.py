"""REST tests for the sprite-resolve and PC PATCH routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from grimoire.api.container import ServiceContainer


@pytest.fixture(autouse=True)
def _refresh_main_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Force ``grimoire.main.settings`` to point at this test's tmp data root.

    ``grimoire.main`` binds ``settings`` via ``from grimoire.config import
    settings`` at import time, so the base ``container`` fixture's
    ``config_module.settings = Settings()`` replacement doesn't reach
    inside main. Patch it directly so the lifespan creates its DB +
    library scan under ``tmp_path``.
    """
    monkeypatch.setenv("GRIMOIRE_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("GRIMOIRE_DATABASE_PATH", str(tmp_path / "test.sqlite"))
    from grimoire import config as config_module
    from grimoire import main as main_module

    fresh = config_module.Settings()
    monkeypatch.setattr(config_module, "settings", fresh)
    monkeypatch.setattr(main_module, "settings", fresh)


def _seed_character_files(
    *,
    data_root: Path,
    world_id: str,
    asset_id: str,
    frontmatter: dict | None = None,
    sprites: tuple[str, ...] = (),
    with_avatar: bool = False,
) -> Path:
    """Lay down a directory-form character on disk before lifespan scans."""
    char_dir = data_root / "library" / "worlds" / world_id / "characters" / asset_id
    char_dir.mkdir(parents=True, exist_ok=True)
    card = char_dir / "card.md"
    fm = frontmatter or {"id": asset_id, "name": asset_id.title()}
    card.write_text(
        "---\n" + "\n".join(f"{k}: {json.dumps(v)}" for k, v in fm.items()) + "\n---\n\nbody\n",
        encoding="utf-8",
    )
    if sprites:
        sprite_dir = char_dir / "sprites"
        sprite_dir.mkdir(exist_ok=True)
        for emotion in sprites:
            (sprite_dir / f"{emotion}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    if with_avatar:
        (char_dir / "avatar.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return card


def _enable_expressions(
    client: TestClient,
    campaign_id: str,
    characters: list[str],
) -> None:
    """Ensure the campaign exists and enable expressions for the given characters."""
    client.post(
        "/api/campaigns",
        json={"id": campaign_id, "name": "Test", "composition": {"worlds": []}},
    )
    r = client.put(
        f"/api/campaigns/{campaign_id}/expressions",
        json={"enabled_characters": characters},
    )
    assert r.status_code == 200, r.text


def _set_via_patch(
    client: TestClient,
    *,
    campaign_id: str,
    character_id: str,
    emotion: str,
    post_id: str,
    turn_id: str | None = None,
) -> None:
    """Seed an expression_state row by calling the PATCH endpoint.

    Avoids ``asyncio.run`` inside ``with TestClient(app)``, which is
    fragile across anyio backend variants on different runners.
    Provenance is ``user:pc`` here; the routing-specific behaviour for
    extractor provenance is exercised by ``tests/expressions/``.
    """
    body: dict[str, str] = {"emotion": emotion, "post_id": post_id}
    if turn_id is not None:
        body["turn_id"] = turn_id
    r = client.patch(
        f"/api/campaigns/{campaign_id}/characters/{character_id}/expression",
        json=body,
    )
    assert r.status_code == 200, r.text


def test_returns_neutral_when_no_state(tmp_path: Path, container: ServiceContainer) -> None:
    _seed_character_files(
        data_root=tmp_path,
        world_id="w",
        asset_id="beatrice",
        sprites=("neutral", "happy"),
        with_avatar=True,
    )
    from grimoire.main import create_app

    app = create_app()
    app.state.container = container
    with TestClient(app) as client:
        _enable_expressions(client, "cmp_1", ["beatrice"])
        r = client.get("/api/campaigns/cmp_1/characters/beatrice/expression")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["emotion"] == "neutral"
        assert body["sprite_url"].endswith("/sprites/neutral.png")
        assert body["fallback_used"] is False


def test_returns_requested_sprite_when_present(tmp_path: Path, container: ServiceContainer) -> None:
    _seed_character_files(
        data_root=tmp_path,
        world_id="w",
        asset_id="beatrice",
        sprites=("neutral", "happy"),
        with_avatar=True,
    )
    from grimoire.main import create_app

    app = create_app()
    app.state.container = container
    with TestClient(app) as client:
        _enable_expressions(client, "cmp_1", ["beatrice"])
        _set_via_patch(
            client,
            campaign_id="cmp_1",
            character_id="beatrice",
            emotion="happy",
            post_id="p_1",
            turn_id="t_1",
        )
        r = client.get("/api/campaigns/cmp_1/characters/beatrice/expression")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["emotion"] == "happy"
        assert body["sprite_url"].endswith("/sprites/happy.png")
        assert body["fallback_used"] is False


def test_falls_back_to_neutral_when_sprite_missing(
    tmp_path: Path, container: ServiceContainer
) -> None:
    _seed_character_files(
        data_root=tmp_path,
        world_id="w",
        asset_id="beatrice",
        sprites=("neutral",),
        with_avatar=True,
    )
    from grimoire.main import create_app

    app = create_app()
    app.state.container = container
    with TestClient(app) as client:
        _enable_expressions(client, "cmp_1", ["beatrice"])
        _set_via_patch(
            client,
            campaign_id="cmp_1",
            character_id="beatrice",
            emotion="smug",
            post_id="p_1",
            turn_id="t_1",
        )
        r = client.get("/api/campaigns/cmp_1/characters/beatrice/expression")
        body = r.json()
        assert body["emotion"] == "neutral"
        assert body["sprite_url"].endswith("/sprites/neutral.png")
        assert body["fallback_used"] is True


def test_falls_back_to_avatar_when_no_neutral(tmp_path: Path, container: ServiceContainer) -> None:
    _seed_character_files(
        data_root=tmp_path,
        world_id="w",
        asset_id="ralph",
        sprites=(),
        with_avatar=True,
    )
    from grimoire.main import create_app

    app = create_app()
    app.state.container = container
    with TestClient(app) as client:
        _enable_expressions(client, "cmp_1", ["ralph"])
        r = client.get("/api/campaigns/cmp_1/characters/ralph/expression")
        body = r.json()
        assert body["sprite_url"] is not None
        assert body["sprite_url"].endswith("/avatar.png")
        assert body["fallback_used"] is True


def test_returns_null_sprite_when_nothing_available(
    tmp_path: Path, container: ServiceContainer
) -> None:
    _seed_character_files(
        data_root=tmp_path,
        world_id="w",
        asset_id="naked",
        sprites=(),
        with_avatar=False,
    )
    from grimoire.main import create_app

    app = create_app()
    app.state.container = container
    with TestClient(app) as client:
        _enable_expressions(client, "cmp_1", ["naked"])
        r = client.get("/api/campaigns/cmp_1/characters/naked/expression")
        body = r.json()
        assert body["sprite_url"] is None
        assert body["fallback_used"] is True


def test_path_traversal_rejected(tmp_path: Path, container: ServiceContainer) -> None:
    _seed_character_files(
        data_root=tmp_path,
        world_id="w",
        asset_id="beatrice",
        sprites=("neutral",),
    )
    from grimoire.main import create_app

    app = create_app()
    app.state.container = container
    with TestClient(app) as client:
        r = client.get("/api/campaigns/cmp_1/characters/..%2F..%2Fetc%2Fpasswd/expression")
        assert r.status_code in {400, 404}


def test_as_of_turn_returns_historical(tmp_path: Path, container: ServiceContainer) -> None:
    _seed_character_files(
        data_root=tmp_path,
        world_id="w",
        asset_id="beatrice",
        sprites=("neutral", "happy"),
    )
    from grimoire.main import create_app

    app = create_app()
    app.state.container = container
    with TestClient(app) as client:
        _enable_expressions(client, "cmp_1", ["beatrice"])
        _set_via_patch(
            client,
            campaign_id="cmp_1",
            character_id="beatrice",
            emotion="happy",
            post_id="p_1",
            turn_id="t_1",
        )
        _set_via_patch(
            client,
            campaign_id="cmp_1",
            character_id="beatrice",
            emotion="neutral",
            post_id="p_5",
            turn_id="t_5",
        )
        r = client.get("/api/campaigns/cmp_1/characters/beatrice/expression?as_of_turn=t_1")
        body = r.json()
        assert body["emotion"] == "happy"


def test_patch_pc_expression_writes_state(tmp_path: Path, container: ServiceContainer) -> None:
    _seed_character_files(
        data_root=tmp_path,
        world_id="w",
        asset_id="beatrice",
        sprites=("neutral",),
    )
    from grimoire.main import create_app

    app = create_app()
    app.state.container = container
    with TestClient(app) as client:
        _enable_expressions(client, "cmp_1", ["beatrice"])
        r = client.patch(
            "/api/campaigns/cmp_1/characters/beatrice/expression",
            json={"emotion": "determined", "post_id": "p_42", "turn_id": "t_42"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # No determined.png sprite → fallback to neutral.
        assert body["emotion"] == "neutral"
        assert body["fallback_used"] is True


def test_patch_rejects_unknown_emotion(tmp_path: Path, container: ServiceContainer) -> None:
    _seed_character_files(
        data_root=tmp_path,
        world_id="w",
        asset_id="beatrice",
        sprites=("neutral",),
    )
    from grimoire.main import create_app

    app = create_app()
    app.state.container = container
    with TestClient(app) as client:
        _enable_expressions(client, "cmp_1", ["beatrice"])
        r = client.patch(
            "/api/campaigns/cmp_1/characters/beatrice/expression",
            json={"emotion": "ecstatic", "post_id": "p_1"},
        )
        assert r.status_code == 400


def test_vocabulary_route(tmp_path: Path, container: ServiceContainer) -> None:
    from grimoire.main import create_app

    app = create_app()
    app.state.container = container
    with TestClient(app) as client:
        r = client.get("/api/expressions/vocabulary")
        body: dict[str, Any] = r.json()
        assert "happy" in body["core"]
        assert "neutral" in body["core"]
        assert isinstance(body["extensions"], dict)


# ---------------------------------------------------------------------------
# expressions toggle (issue #474)
# ---------------------------------------------------------------------------


def test_disabled_character_returns_neutral_without_404(
    tmp_path: Path, container: ServiceContainer
) -> None:
    """When expressions are not enabled for a character, GET returns neutral
    immediately — no library lookup, no 404."""
    from grimoire.main import create_app

    app = create_app()
    app.state.container = container
    with TestClient(app) as client:
        _enable_expressions(client, "cmp_1", [])
        r = client.get("/api/campaigns/cmp_1/characters/nonexistent/expression")
        assert r.status_code == 200
        body = r.json()
        assert body["emotion"] == "neutral"
        assert body["sprite_url"] is None
        assert body["fallback_used"] is True


def test_enabled_character_resolves_sprite(tmp_path: Path, container: ServiceContainer) -> None:
    """When expressions are enabled for a character, GET resolves sprites normally."""
    _seed_character_files(
        data_root=tmp_path,
        world_id="w",
        asset_id="beatrice",
        sprites=("neutral", "happy"),
    )
    from grimoire.main import create_app

    app = create_app()
    app.state.container = container
    with TestClient(app) as client:
        _enable_expressions(client, "cmp_1", ["beatrice"])
        r = client.get("/api/campaigns/cmp_1/characters/beatrice/expression")
        assert r.status_code == 200
        body = r.json()
        assert body["sprite_url"] is not None
        assert body["sprite_url"].endswith("/sprites/neutral.png")


def test_default_off_prevents_404_flood(tmp_path: Path, container: ServiceContainer) -> None:
    """Characters not in the library don't cause 404s when toggle is off (default)."""
    from grimoire.main import create_app

    app = create_app()
    app.state.container = container
    with TestClient(app) as client:
        _enable_expressions(client, "cmp_1", [])
        for char in ("deleted-char", "old-npc", "missing-entity"):
            r = client.get(f"/api/campaigns/cmp_1/characters/{char}/expression")
            assert r.status_code == 200
            assert r.json()["emotion"] == "neutral"


def test_malformed_ref_rejected_before_disabled_shortcut(
    tmp_path: Path, container: ServiceContainer
) -> None:
    """Path component validation runs before the disabled-character shortcut.

    A malformed ref (containing characters outside the safe allowlist) must
    be rejected with 400 even when expressions are disabled — otherwise the
    shortcut would mask the traversal guard.
    """
    from grimoire.main import create_app

    app = create_app()
    app.state.container = container
    with TestClient(app) as client:
        _enable_expressions(client, "cmp_1", [])
        # Colon is not in the safe-component allowlist; this routes to the
        # handler as a single segment but must be rejected before the
        # disabled shortcut returns a neutral 200.
        r = client.get("/api/campaigns/cmp_1/characters/bad%3Achar/expression")
        assert r.status_code == 400
