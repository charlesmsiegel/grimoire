"""§3d — Extractor identifies a commitment.

Drive ``ExtractorService`` directly with a structured-LLM response that
declares a commitment. Assert the resulting delta set includes a
``COMMITMENT_ADD`` kind, which is the public ``commitment_added``
signal in the delta vocabulary.
"""

from __future__ import annotations

import pytest

from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.service import ExtractorService
from grimoire.testing import MockLLMGateway
from grimoire.types.scene import Scene
from grimoire.types.state import DeltaKind, StateSnapshot

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_extractor_emits_commitment_add_delta() -> None:
    gateway = MockLLMGateway()
    gateway.queue_response(
        "extractor",
        {
            "commitments": [
                {
                    "kind": "promise",
                    "text": "Garrick will forge a sword for the player",
                    "from": "garrick",
                    "to": "pc",
                    "confidence": 0.9,
                }
            ]
        },
    )
    # Only run the structured-LLM strategy so the test is deterministic
    # under just the queued mock response.
    config = ExtractorConfig(parallel_strategies=("structured_llm",))
    service = ExtractorService(gateway=gateway, config=config)

    scene = Scene(
        id="scene-1",
        campaign_id="cmp-ironhold-1",
        ordinal=1,
        slug="forge",
        file_path="/tmp/forge.md",
        title="The Forge",
        location_ref="campaign:locations/forge",
        present_character_refs=["garrick"],
        present_pc_refs=["pc"],
        pov_character_ref="pc",
    )
    snapshot = StateSnapshot(campaign_id="cmp-ironhold-1", scene_id="scene-1")

    result = await service.extract(
        response_text="Garrick promises to forge a blade.",
        scene=scene,
        campaign_id="cmp-ironhold-1",
        prior_state_snapshot=snapshot,
    )

    kinds = {delta.kind for delta in result.deltas}
    assert DeltaKind.COMMITMENT_ADD in kinds, (
        f"expected COMMITMENT_ADD in extracted deltas; got {sorted(k.value for k in kinds)}"
    )
