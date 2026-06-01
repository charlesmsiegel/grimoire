"""L5 scenario: DELETE /api/campaigns/{cid}/scenes/{sid}/posts/{pid} end-to-end.

Bootstraps a world, campaign, and scene; appends a PC post and a model turn
through the real SceneManager (the same store the orchestrator uses); then hits
the cascade-delete route over HTTP and verifies the response envelope plus that
the scene reflects the truncation. A closed scene is rejected with 409.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.scenes import AuthorKind, SceneInit, new_post
from grimoire.testing.scenario import ScenarioApp


async def _bootstrap(client, world_id: str, campaign_id: str) -> None:
    resp = await client.post(
        "/api/library/worlds",
        json={
            "id": world_id,
            "meta": {
                "name": "Delete World",
                "description": "Cascade-delete scenario.",
                "atmosphere": {"themes": ["scenario"], "tone": "neutral"},
            },
        },
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        "/api/campaigns",
        json={
            "id": campaign_id,
            "name": "Delete Scenario",
            "description": "Drives the cascade-delete endpoint.",
            "composition": {"worlds": [{"world_id": world_id, "priority": 0}]},
        },
    )
    assert resp.status_code == 201, resp.text


async def test_delete_post_cascade_truncates_scene(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None and app.container is not None
        client = app.client
        campaign_id = "delete-scenario"
        await _bootstrap(client, "delete-world", campaign_id)

        scenes = app.container.scenes
        assert scenes is not None
        scene = await scenes.start_scene(SceneInit(campaign_id=campaign_id, title="The Dock"))
        await scenes.append_post(
            scene.id,
            new_post(
                author_kind=AuthorKind.PC, author_pc_ref="alistair", body="hi", is_player=True
            ),
        )
        await scenes.append_post(
            scene.id,
            new_post(author_kind=AuthorKind.NARRATOR, body="m1", is_player=False, turn_id="T1"),
        )
        await scenes.append_post(
            scene.id,
            new_post(author_kind=AuthorKind.NARRATOR, body="m2", is_player=False, turn_id="T1"),
        )
        target = next(p for p in await scenes.get_posts(scene.id) if p.body == "m1")

        resp = await client.delete(
            f"/api/campaigns/{campaign_id}/scenes/{scene.id}/posts/{target.id}"
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # The envelope reports the removed suffix (m1 + m2) and is shaped the
        # way the frontend's deletePost client expects.
        assert set(body.keys()) >= {
            "deleted_post_ids",
            "reversed_turn_ids",
            "requeued_review_ids",
            "warnings",
        }
        assert len(body["deleted_post_ids"]) == 2

        # The scene now reflects the truncation: only the PC post survives.
        resp = await client.get(f"/api/campaigns/{campaign_id}/scenes/{scene.id}/posts")
        assert resp.status_code == 200, resp.text
        assert [p["body"] for p in resp.json()["posts"]] == ["hi"]


async def test_delete_post_cascade_on_closed_scene_is_409(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None and app.container is not None
        client = app.client
        campaign_id = "delete-scenario-closed"
        await _bootstrap(client, "delete-world-closed", campaign_id)

        scenes = app.container.scenes
        assert scenes is not None
        scene = await scenes.start_scene(SceneInit(campaign_id=campaign_id, title="The Dock"))
        await scenes.append_post(
            scene.id,
            new_post(author_kind=AuthorKind.NARRATOR, body="m1", is_player=False, turn_id="T1"),
        )
        target = (await scenes.get_posts(scene.id))[0]
        await scenes.close_scene(scene.id, closed_at_turn="T1")

        # Deleting on a closed scene is rejected so the final summary stays
        # stable.
        resp = await client.delete(
            f"/api/campaigns/{campaign_id}/scenes/{scene.id}/posts/{target.id}"
        )
        assert resp.status_code == 409, resp.text
        # The prose is untouched.
        resp = await client.get(f"/api/campaigns/{campaign_id}/scenes/{scene.id}/posts")
        assert [p["body"] for p in resp.json()["posts"]] == ["m1"]
