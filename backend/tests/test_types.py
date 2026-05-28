"""Smoke tests for the shared types package.

These cover construction and the key invariants other modules will rely on:
- enums round-trip with their string values
- core dataclasses accept the documented fields
- runtime-checkable protocols accept duck-typed implementations
- `EntityRef.parse` understands the canonical "<scope>:<path>" form
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from grimoire import types as gt


def test_package_exports_are_resolvable() -> None:
    for name in gt.__all__:
        assert hasattr(gt, name), f"missing export: {name}"
        assert getattr(gt, name) is not None


def test_entity_ref_parse_library_character() -> None:
    ref = gt.EntityRef.parse("library:worlds/wod-london/characters/alistair-hyde-smythe")
    assert ref.scope is gt.Scope.LIBRARY
    assert ref.kind is gt.EntityKind.CHARACTER
    assert ref.world_id == "wod-london"
    assert ref.asset_id == "alistair-hyde-smythe"


def test_entity_ref_parse_unknown_falls_back_to_character() -> None:
    ref = gt.EntityRef.parse("campaign-local:emergent/the-bartender")
    assert ref.asset_id == "the-bartender"
    assert ref.kind is gt.EntityKind.CHARACTER


def test_scope_and_enums_are_str_enums() -> None:
    assert gt.Scope.LIBRARY == "library"
    assert gt.EntityKind.CHARACTER == "character"
    assert gt.AuthorKind.PC == "pc"
    assert gt.CommitmentStatus.OPEN == "open"
    assert gt.JobStatus.QUEUED == "queued"
    assert gt.HealthLevel.HEALTHY == "healthy"


def test_composition_round_trip() -> None:
    comp = gt.Composition(
        worlds=[
            gt.WorldRef(
                world_id="wod-london",
                priority=1,
                include=["characters", "locations"],
                bound_at_version=7,
                track_latest=False,
            )
        ],
        mechanics="wod-mechanics",
        style_guide_id="gothic-horror",
    )
    assert comp.worlds[0].world_id == "wod-london"
    assert comp.mechanics == "wod-mechanics"


def test_state_delta_construction() -> None:
    delta = gt.StateDelta(
        kind=gt.DeltaKind.FACT_ADD,
        target_scope=gt.Scope.CAMPAIGN_SQLITE,
        target_id="fact-001",
        after={"text": "winifred promised to teach julian to ride"},
        confidence=0.9,
        source="extractor",
    )
    applied = gt.AppliedDelta(
        id="d-001",
        delta=delta,
        campaign_id="by-night-london",
        turn_id="t-100",
        applied_at=datetime.now(UTC),
    )
    assert applied.delta.kind is gt.DeltaKind.FACT_ADD
    assert applied.delta.target_scope is gt.Scope.CAMPAIGN_SQLITE


def test_scene_and_post_construction() -> None:
    post = gt.Post(
        id="p-1",
        scene_id="s-0001",
        order_in_scene=1,
        author_kind=gt.AuthorKind.PC,
        body="I step into the room.",
        is_player=True,
        created_at=datetime.now(UTC),
        turn_id="t-1",
        author_pc_ref="alistair-hyde-smythe",
    )
    scene = gt.Scene(
        id="s-0001",
        campaign_id="by-night-london",
        ordinal=1,
        slug="elysium-opening",
        file_path="data/campaigns/by-night-london/scenes/0001-elysium-opening.md",
        present_pc_refs=["alistair-hyde-smythe"],
    )
    assert post.scene_id == scene.id
    assert scene.closed is False


def test_fact_and_commitment_construction() -> None:
    moment = gt.InGameTime(moment=datetime.now(UTC))
    fact = gt.Fact(
        id="f-1",
        campaign_id="by-night-london",
        text="winifred visited Sion as a child",
        established_in_post="p-9",
        established_at_in_game=moment,
        confidence=0.7,
        source=gt.FactSource.CHARACTER_TESTIMONY,
        about=gt.FactSubject(character_ids=["winifred"], scope=gt.FactScope.PRIVATE),
    )
    commitment = gt.Commitment(
        id="c-1",
        campaign_id="by-night-london",
        kind=gt.CommitmentKind.PROMISE,
        text="winifred promised to teach julian to ride",
        created_in_post="p-3",
        in_game_created_at=moment,
        from_id="winifred",
        to_id="julian",
        weight=3,
    )
    assert fact.source is gt.FactSource.CHARACTER_TESTIMONY
    assert commitment.status is gt.CommitmentStatus.OPEN


def test_capability_and_roll_types() -> None:
    cap = gt.Capability(
        id="wod.celerity.3",
        name="Celerity 3",
        kind="discipline",
        description="Extra action per turn for 1 Blood",
        cost=gt.ResourceCost(resource="blood", amount=1),
        effect="+1 dice action this turn",
    )
    roll = gt.Roll(id="r-1", kind="dice-pool", pool=5, seed=49271)
    result = gt.RollResult(roll_id="r-1", dice=[6, 7, 9, 3, 10], successes=3)
    assert cap.cost is not None
    assert cap.cost.resource == "blood"
    assert result.botched is False
    assert roll.pool == 5


def test_duration_and_in_game_time() -> None:
    duration = gt.Duration(iso8601="P2H", delta=timedelta(hours=2))
    moment = gt.InGameTime(moment=datetime(2024, 10, 31, 22, 0, tzinfo=UTC))
    assert duration.delta == timedelta(hours=2)
    assert moment.moment.year == 2024


def test_event_bus_protocol_accepts_minimal_impl() -> None:
    class _Bus:
        def subscribe(self, event_type, handler):
            return gt.Subscription(id="sub-1", event_type=event_type)

        def unsubscribe(self, subscription_id):
            return None

        async def emit(self, event):
            return None

    assert isinstance(_Bus(), gt.EventBus)


def test_runtime_checkable_protocols_reject_empty_class() -> None:
    class _Empty:
        pass

    assert not isinstance(_Empty(), gt.StateStoreProtocol)
    assert not isinstance(_Empty(), gt.EventBus)


def test_protocols_module_exposes_module_protocols() -> None:
    expected = [
        "OrchestratorProtocol",
        "ContextBuilderProtocol",
        "StateStoreProtocol",
        "ExtractorProtocol",
        "LibraryProtocol",
        "WorldProtocol",
        "CharactersProtocol",
        "SceneManagerProtocol",
        "ContinuityProtocol",
        "TimeEngineProtocol",
        "ImageGenProtocol",
        "ExportProtocol",
        "PluginsProtocol",
        "ObservabilityProtocol",
        "LLMGatewayProtocol",
        "MechanicsProtocol",
        "TurnReplayerProtocol",
        "HealthMonitorProtocol",
        "CostTrackerProtocol",
    ]
    for name in expected:
        assert hasattr(gt, name), name


def test_event_type_enum_has_documented_events() -> None:
    documented = {
        "turn_started",
        "context_built",
        "model_response_received",
        "deltas_extracted",
        "turn_complete",
        "scene_started",
        "scene_ended",
        "pc_post_appended",
        "advance_requested",
        "advance_disabled",
        "advance_enabled",
        "time_advanced",
        "npc_tick_complete",
        "drift_detected",
        "library_file_changed",
        "library_indexed",
        "library_ref_upgraded",
        "image_ready",
        "review_item_added",
    }
    values = {e.value for e in gt.EventType}
    missing = documented - values
    assert not missing, f"missing event types: {missing}"


def test_assembled_prompt_minimal_construction() -> None:
    prompt = gt.AssembledPrompt(
        messages=[gt.Message(role=gt.MessageRole.SYSTEM, content="hello")],
        params=gt.ModelParams(),
    )
    assert prompt.messages[0].role is gt.MessageRole.SYSTEM
    assert prompt.params.temperature == 1.0


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        (gt.AuthorKind.PC, "pc"),
        (gt.AuthorKind.NARRATOR, "narrator"),
        (gt.AuthorKind.NPC, "npc"),
        (gt.AuthorKind.SYSTEM, "system"),
    ],
)
def test_author_kind_string_values(kind: gt.AuthorKind, value: str) -> None:
    assert kind == value


def test_resolved_entity_carries_source_chain() -> None:
    src = gt.ResolutionSource(
        layer=gt.ResolutionLayer.LIBRARY_SNAPSHOT,
        scope=gt.Scope.LIBRARY,
        library_id="worlds/wod-london/characters/alistair-hyde-smythe",
        world_id="wod-london",
        version=7,
    )
    resolved = gt.ResolvedEntity(
        kind=gt.EntityKind.CHARACTER,
        asset_id="alistair-hyde-smythe",
        world_id="wod-london",
        name="Alistair Hyde-Smythe",
        frontmatter={"role": "pc"},
        body="...",
        source_chain=[src],
    )
    assert resolved.source_chain[0].layer is gt.ResolutionLayer.LIBRARY_SNAPSHOT
    assert resolved.source_chain[0].version == 7


# --------------------------------------------------------------------------- #
# Pydantic serialization round-trips
# --------------------------------------------------------------------------- #


def test_state_delta_json_round_trip() -> None:
    delta = gt.StateDelta(
        kind=gt.DeltaKind.FACT_ADD,
        target_scope=gt.Scope.CAMPAIGN_SQLITE,
        target_id="fact-001",
        after={"text": "winifred promised to teach julian to ride"},
        confidence=0.9,
        source="extractor",
    )
    payload = delta.model_dump_json()
    restored = gt.StateDelta.model_validate_json(payload)
    assert restored == delta


def test_scene_round_trip_via_dict() -> None:
    scene = gt.Scene(
        id="s-0001",
        campaign_id="by-night-london",
        ordinal=1,
        slug="elysium-opening",
        file_path="data/campaigns/by-night-london/scenes/0001-elysium-opening.md",
        present_pc_refs=["alistair-hyde-smythe"],
        in_game_start=gt.InGameTime(moment=datetime(2024, 10, 31, 22, 0, tzinfo=UTC)),
    )
    payload = scene.model_dump(mode="json")
    restored = gt.Scene.model_validate(payload)
    assert restored == scene
    assert restored.in_game_start is not None
    assert restored.in_game_start.moment.year == 2024


def test_turn_audit_nested_round_trip() -> None:
    """TurnAudit nests many other models; tests that the whole graph survives."""
    delta = gt.StateDelta(
        kind=gt.DeltaKind.FACT_ADD,
        target_scope=gt.Scope.CAMPAIGN_SQLITE,
        target_id="fact-001",
    )
    audit = gt.TurnAudit(
        turn_id="t-1",
        campaign_id="by-night-london",
        started_at=datetime.now(UTC),
        extracted_deltas=[delta],
        applied_deltas=[
            gt.AppliedDelta(
                id="d-1",
                delta=delta,
                campaign_id="by-night-london",
                turn_id="t-1",
                applied_at=datetime.now(UTC),
            )
        ],
    )
    payload = audit.model_dump_json()
    restored = gt.TurnAudit.model_validate_json(payload)
    assert restored.turn_id == "t-1"
    assert restored.extracted_deltas[0].kind is gt.DeltaKind.FACT_ADD
    assert restored.applied_deltas[0].delta.target_id == "fact-001"


def test_entity_ref_is_frozen_and_hashable() -> None:
    ref = gt.EntityRef.parse("library:worlds/wod-london/characters/alistair")
    assert hash(ref) == hash(ref)
    with pytest.raises((ValueError, TypeError)):
        ref.asset_id = "something-else"  # type: ignore[misc]


def test_composition_validates_field_types() -> None:
    """Pydantic should reject the wrong type for `priority`."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        gt.WorldRef(world_id="x", priority="not-an-int", include=[])  # type: ignore[arg-type]


def test_completion_chunk_round_trip_with_usage() -> None:
    chunk = gt.CompletionChunk(
        delta="",
        is_final=True,
        usage=gt.TokenUsage(input_tokens=100, output_tokens=42, total_tokens=142),
    )
    payload = chunk.model_dump()
    restored = gt.CompletionChunk.model_validate(payload)
    assert restored.usage is not None
    assert restored.usage.total_tokens == 142


def test_generation_request_bytes_round_trip() -> None:
    """bytes fields survive JSON round-trip via base64 encoding."""
    req = gt.GenerationRequest(prompt="oil painting, candlelit", init_image=b"\x00\x01\x02")
    payload = req.model_dump_json()
    restored = gt.GenerationRequest.model_validate_json(payload)
    assert restored.init_image == b"\x00\x01\x02"
