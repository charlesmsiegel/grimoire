"""L5 scenario: POST /scenes/{scene_id}/analyze end-to-end through the HTTP API.

Drives the analyze endpoint against the real FastAPI app: bootstraps a
world, campaign, and scene; injects a fake analyzer into the container
so the LLM gateway is not invoked; then POSTs to /analyze and verifies
that the scene sidecar is updated and the response surfaces summary,
key beats, threads, and extraction counts.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.scenes.analysis import SceneAnalysisResult
from grimoire.scenes.types import AuthorKind, Thread
from grimoire.testing.scenario import ScenarioApp
from grimoire.types.extraction import EntityCandidate, ExtractionResult


async def test_analyze_scene_endpoint(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None
        assert app.container is not None
        client = app.client

        # 1. World + campaign bootstrap (atmosphere inline skips LLM).
        world_id = "scenario-world"
        resp = await client.post(
            "/api/library/worlds",
            json={
                "id": world_id,
                "meta": {
                    "name": "Scenario World",
                    "description": "Analyze-scene scenario.",
                    "atmosphere": {"themes": ["scenario"], "tone": "neutral"},
                },
            },
        )
        assert resp.status_code == 201, resp.text

        campaign_id = "scenario-analyze-campaign"
        resp = await client.post(
            "/api/campaigns",
            json={
                "id": campaign_id,
                "name": "Analyze Scenario",
                "description": "Drives POST /scenes/{id}/analyze.",
                "composition": {"worlds": [{"world_id": world_id, "priority": 0}]},
            },
        )
        assert resp.status_code == 201, resp.text

        # 2. Create a scene with one post directly through the SceneManager.
        # The scenes router doesn't expose a "create scene with posts" route
        # except via the turn loop; for this test we want to exercise the
        # analyze endpoint with a known scene, not the full turn flow.
        from grimoire.scenes import SceneInit, new_post

        scenes = app.container.scenes
        assert scenes is not None
        scene = await scenes.start_scene(SceneInit(campaign_id=campaign_id, title="Analyzed Scene"))
        post = new_post(
            author_kind=AuthorKind.NARRATOR,
            body="The party crossed the threshold into the ruined tower.",
            turn_id="t1",
            is_player=False,
        )
        await scenes.append_post(scene.id, post)

        # 3. Inject a fake analyzer so the HTTP path doesn't require an LLM
        # fixture. The analyzer returns a fixed extraction result with one
        # fact, one entity candidate, and one thread.
        async def fake_analyzer(scene_arg, posts_arg, campaign_id_arg):
            return SceneAnalysisResult(
                summary="The party entered the ruined tower.",
                key_beats=["Crossed the threshold"],
                threads_introduced=[Thread(text="The ruined tower's secret", introduced_at_post=1)],
                extraction=ExtractionResult(
                    candidates=[
                        EntityCandidate(
                            kind="location",
                            proposed_id="ruined-tower",
                            proposed_name="Ruined Tower",
                            confidence=0.8,
                        )
                    ],
                    extraction_strategies_run=["scene_analysis"],
                ),
            )

        scenes.set_scene_analyzer(fake_analyzer)

        # 4. POST /analyze and check the response shape.
        resp = await client.post(
            f"/api/campaigns/{campaign_id}/scenes/{scene.id}/analyze",
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["summary"] == "The party entered the ruined tower."
        assert body["key_beats"] == ["Crossed the threshold"]
        assert len(body["threads_introduced"]) == 1
        assert body["threads_introduced"][0]["text"] == "The ruined tower's secret"
        assert len(body["entity_candidates"]) == 1
        assert body["entity_candidates"][0]["proposed_name"] == "Ruined Tower"
        # No deltas in this fixture, just candidates + summary/threads.
        assert body["deltas_applied"] == 0
        assert body["deltas_queued"] == 0

        # 5. Re-fetch the scene and confirm summary + threads were persisted.
        resp = await client.get(f"/api/campaigns/{campaign_id}/scenes/{scene.id}")
        assert resp.status_code == 200, resp.text
        scene_payload = resp.json()
        scene_data = scene_payload["scene"]
        assert scene_data["running_summary"] == "The party entered the ruined tower."
        assert "Crossed the threshold" in scene_data["key_beats"]
        thread_texts = [t["text"] for t in scene_data.get("threads_introduced", [])]
        assert "The ruined tower's secret" in thread_texts

        # 6. force=False should short-circuit and not re-invoke the analyzer.
        call_count = {"n": 0}

        async def counting_analyzer(scene_arg, posts_arg, campaign_id_arg):
            call_count["n"] += 1
            return await fake_analyzer(scene_arg, posts_arg, campaign_id_arg)

        scenes.set_scene_analyzer(counting_analyzer)

        resp = await client.post(
            f"/api/campaigns/{campaign_id}/scenes/{scene.id}/analyze",
        )
        assert resp.status_code == 200, resp.text
        assert call_count["n"] == 0  # short-circuit because summary already exists

        # 7. force=true bypasses the guard.
        resp = await client.post(
            f"/api/campaigns/{campaign_id}/scenes/{scene.id}/analyze?force=true",
        )
        assert resp.status_code == 200, resp.text
        assert call_count["n"] == 1
