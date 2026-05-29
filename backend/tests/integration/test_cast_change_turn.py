"""§464 — cast-change detection end-to-end.

Two halves of the feature wired against real components:

1. The Extractor surfaces a ``cast_changes`` proposal from a structured-LLM
   response.
2. The Orchestrator resolution helper queues a *known* character as a pending
   cast change through the Scene Manager, and confirming it updates the scene
   cast (the YAML sidecar source of truth).
"""

from __future__ import annotations

import pytest

from grimoire.characters import CharactersService
from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.service import ExtractorService
from grimoire.library import LibraryService
from grimoire.mechanics import MechanicsConfig, MechanicsService
from grimoire.orchestrator.service import resolve_cast_changes
from grimoire.scenes.cast_changes import CastChangeStore
from grimoire.scenes.manager import SceneManager
from grimoire.scenes.types import SceneInit
from grimoire.state_store import StateStore
from grimoire.storage import Database
from grimoire.testing import MockLLMGateway
from grimoire.testing.db_template import stamp_migrated_db
from grimoire.types.characters import CharacterData, CharacterRole
from grimoire.types.scene import Scene
from grimoire.types.state import StateSnapshot

pytestmark = pytest.mark.integration


async def test_extractor_surfaces_cast_change() -> None:
    gateway = MockLLMGateway()
    gateway.queue_response(
        "extractor",
        {
            "cast_changes": [
                {
                    "character_id": "reyes",
                    "change": "enter",
                    "evidence": "strides in",
                    "confidence": 0.9,
                }
            ]
        },
    )
    config = ExtractorConfig(parallel_strategies=("structured_llm",))
    service = ExtractorService(gateway=gateway, config=config)

    scene = Scene(
        id="scene-1",
        campaign_id="cmp-1",
        ordinal=1,
        slug="dock",
        file_path="/tmp/dock.md",
        title="The Dock",
        present_character_refs=[],
        present_pc_refs=["pc"],
    )
    snapshot = StateSnapshot(campaign_id="cmp-1", scene_id="scene-1")

    result = await service.extract(
        response_text="Captain Reyes strides onto the dock.",
        scene=scene,
        campaign_id="cmp-1",
        prior_state_snapshot=snapshot,
    )

    refs = {c.character_ref for c in result.cast_changes}
    assert "reyes" in refs


async def test_resolution_queues_then_confirm_updates_scene(tmp_path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(stamp_migrated_db(tmp_path / "campaigns.sqlite"), pool_size=2)
    await db.connect()
    try:
        store = StateStore(db, data_root)
        library = LibraryService(store)
        mech_root = tmp_path / "mechanics"
        mech_root.mkdir()
        mechanics = MechanicsService(config=MechanicsConfig(root=mech_root), state_store=store)
        characters = CharactersService(library, mechanics)
        scenes = SceneManager(data_root, state_store=store, cast_change_store=CastChangeStore(db))

        # Seed a world + campaign + known NPC.
        await store.write_library_file(
            library_id="worlds/harbor/world/harbor",
            frontmatter={"id": "harbor", "name": "harbor", "version": 1},
            body="",
            source="test:seed",
        )
        await store.upsert_campaign(campaign_id="cmp-1", name="cmp-1")
        await store.upsert_world_ref(
            campaign_id="cmp-1", world_id="harbor", priority=1, include=None, track_latest=True
        )
        await characters.create(
            "harbor", CharacterData(id="reyes", name="Reyes", role=CharacterRole.MAJOR_NPC)
        )

        scene = await scenes.start_scene(SceneInit(campaign_id="cmp-1", title="The Dock"))

        from grimoire.types.extraction import ExtractionResult
        from grimoire.types.scene import CastChange, CastChangeProposal

        extraction = ExtractionResult(
            cast_changes=[
                CastChangeProposal(character_ref="reyes", change=CastChange.ENTER, confidence=0.9)
            ]
        )
        queued = await resolve_cast_changes(
            extraction=extraction,
            scene=scene,
            campaign_id="cmp-1",
            turn_id="t1",
            characters=characters,
            scenes=scenes,
        )
        assert len(queued) == 1

        pending = await scenes.list_pending_cast_changes(scene.id)
        assert len(pending) == 1
        assert pending[0].character_ref == "library:worlds/harbor/characters/reyes"

        await scenes.confirm_cast_change(scene.id, pending[0].id)

        updated = await scenes.get_scene(scene.id)
        assert "library:worlds/harbor/characters/reyes" in updated.present_character_refs
        assert await scenes.list_pending_cast_changes(scene.id) == []
    finally:
        await db.close()
