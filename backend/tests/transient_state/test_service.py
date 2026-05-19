"""TransientStateService CRUD with supersession + lazy decay."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from grimoire.transient_state import TransientStateService
from grimoire.types.transient import EntityKind, Provenance


async def test_set_then_get_returns_value(service: TransientStateService, seeded_campaign: str):
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "char_florence",
        "mood",
        "guarded",
        provenance=Provenance.USER_EDIT,
    )
    v = await service.get(seeded_campaign, EntityKind.CHARACTER, "char_florence", "mood")
    assert v is not None
    assert v.value == "guarded"
    assert v.provenance == Provenance.USER_EDIT


async def test_get_unknown_returns_none(service: TransientStateService, seeded_campaign: str):
    v = await service.get(seeded_campaign, EntityKind.CHARACTER, "ghost", "mood")
    assert v is None


async def test_get_with_no_field_returns_bundle(
    service: TransientStateService, seeded_campaign: str
):
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "char_x",
        "mood",
        "guarded",
        provenance=Provenance.USER_EDIT,
    )
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "char_x",
        "intent",
        "hide letter",
        provenance=Provenance.USER_EDIT,
    )
    bundle = await service.get(seeded_campaign, EntityKind.CHARACTER, "char_x")
    assert isinstance(bundle, dict)
    assert bundle["mood"].value == "guarded"
    assert bundle["intent"].value == "hide letter"


async def test_user_outranks_extractor(service: TransientStateService, seeded_campaign: str):
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "x",
        "mood",
        "sad",
        provenance=Provenance.EXTRACTOR_AUTO,
        confidence=0.7,
        source_post_id="p_1",
    )
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "x",
        "mood",
        "happy",
        provenance=Provenance.USER_EDIT,
    )
    current = await service.get(seeded_campaign, EntityKind.CHARACTER, "x", "mood")
    assert current.value == "happy"
    assert current.provenance == Provenance.USER_EDIT


async def test_losing_extractor_write_preserved_as_history(
    service: TransientStateService, seeded_campaign: str
):
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "x",
        "mood",
        "happy",
        provenance=Provenance.USER_EDIT,
    )
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "x",
        "mood",
        "sad",
        provenance=Provenance.EXTRACTOR_AUTO,
        confidence=0.9,
        source_post_id="p_1",
    )
    current = await service.get(seeded_campaign, EntityKind.CHARACTER, "x", "mood")
    assert current.value == "happy"
    history = await service.history(seeded_campaign, EntityKind.CHARACTER, "x", "mood")
    assert {h.value for h in history} == {"happy", "sad"}


async def test_clear_single_field(service: TransientStateService, seeded_campaign: str):
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "x",
        "mood",
        "happy",
        provenance=Provenance.USER_EDIT,
    )
    await service.clear(seeded_campaign, EntityKind.CHARACTER, "x", field="mood")
    v = await service.get(seeded_campaign, EntityKind.CHARACTER, "x", "mood")
    assert v is None


async def test_clear_all_fields(service: TransientStateService, seeded_campaign: str):
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "x",
        "mood",
        "a",
        provenance=Provenance.USER_EDIT,
    )
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "x",
        "intent",
        "b",
        provenance=Provenance.USER_EDIT,
    )
    await service.clear(seeded_campaign, EntityKind.CHARACTER, "x")
    bundle = await service.get(seeded_campaign, EntityKind.CHARACTER, "x")
    assert bundle == {}


async def test_get_bulk_returns_keyed_dict(service: TransientStateService, seeded_campaign: str):
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "a",
        "mood",
        "happy",
        provenance=Provenance.USER_EDIT,
    )
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "b",
        "mood",
        "sad",
        provenance=Provenance.USER_EDIT,
    )
    bulk = await service.get_bulk(
        seeded_campaign, EntityKind.CHARACTER, ["a", "b", "missing"], fields=["mood"]
    )
    assert bulk["a"]["mood"].value == "happy"
    assert bulk["b"]["mood"].value == "sad"
    assert bulk["missing"] == {}


async def test_list_conflicts_returns_user_vs_extractor(
    service: TransientStateService, seeded_campaign: str
):
    # user sets first; extractor loses
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "x",
        "mood",
        "happy",
        provenance=Provenance.USER_EDIT,
    )
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "x",
        "mood",
        "sad",
        provenance=Provenance.EXTRACTOR_AUTO,
        confidence=0.9,
        source_post_id="p_1",
    )
    conflicts = await service.list_conflicts(seeded_campaign)
    assert len(conflicts) == 1
    assert conflicts[0].current.value == "happy"
    assert conflicts[0].losing.value == "sad"


async def test_list_conflicts_excludes_extractor_vs_extractor(
    service: TransientStateService, seeded_campaign: str
):
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "x",
        "mood",
        "calm",
        provenance=Provenance.EXTRACTOR_AUTO,
        confidence=0.7,
        source_post_id="p_1",
    )
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "x",
        "mood",
        "angry",
        provenance=Provenance.EXTRACTOR_REVIEWED,
        confidence=0.9,
        source_post_id="p_2",
    )
    conflicts = await service.list_conflicts(seeded_campaign)
    assert conflicts == []


async def test_lazy_decay_filters_expired_rows(
    service: TransientStateService, seeded_campaign: str
):
    past = datetime.now(UTC) - timedelta(seconds=1)
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "x",
        "mood",
        "expired",
        provenance=Provenance.USER_EDIT,
        expires_at=past,
    )
    v = await service.get(seeded_campaign, EntityKind.CHARACTER, "x", "mood")
    assert v is None
    # Future expiry: still visible
    future = datetime.now(UTC) + timedelta(hours=1)
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "x",
        "mood",
        "fresh",
        provenance=Provenance.USER_EDIT,
        expires_at=future,
    )
    v = await service.get(seeded_campaign, EntityKind.CHARACTER, "x", "mood")
    assert v is not None
    assert v.value == "fresh"


async def test_mechanics_outranks_extractor(service: TransientStateService, seeded_campaign: str):
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "x",
        "energy_level",
        "tired",
        provenance=Provenance.EXTRACTOR_AUTO,
        confidence=0.9,
        source_post_id="p_1",
    )
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "x",
        "energy_level",
        "exhausted",
        provenance=Provenance.mechanics("wod"),
    )
    current = await service.get(seeded_campaign, EntityKind.CHARACTER, "x", "energy_level")
    assert current.value == "exhausted"
    assert str(current.provenance) == "mechanics:wod"


async def test_set_returns_inserted_row(service: TransientStateService, seeded_campaign: str):
    v = await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "x",
        "mood",
        "calm",
        provenance=Provenance.USER_EDIT,
    )
    assert v.value == "calm"
    assert v.entity_id == "x"
    assert v.field == "mood"
    assert v.id > 0


async def test_history_orders_newest_first(service: TransientStateService, seeded_campaign: str):
    for i in range(3):
        await service.set(
            seeded_campaign,
            EntityKind.CHARACTER,
            "x",
            "mood",
            f"v{i}",
            provenance=Provenance.USER_EDIT,
        )
    history = await service.history(seeded_campaign, EntityKind.CHARACTER, "x", "mood")
    assert [h.value for h in history] == ["v2", "v1", "v0"]
