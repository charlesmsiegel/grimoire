"""Fact ledger tests."""

from __future__ import annotations

import pytest

from grimoire.continuity import (
    ConfidenceFloorError,
    ContinuityConfig,
    ContinuityService,
    FactNotFoundError,
    FactSource,
    InGameTime,
    RetirementReason,
)


async def test_add_fact_assigns_id_when_blank(service, fact_factory):
    fid = await service.add_fact(fact_factory(text="julian is a baker."), source="user")
    assert fid
    fact = await service.get_fact(fid)
    assert fact.text == "julian is a baker."
    assert any(t.startswith("src:user") for t in fact.tags)


async def test_add_fact_preserves_supplied_id(service, fact_factory):
    fid = await service.add_fact(fact_factory(fact_id="fact-custom", text="x"), source="extractor")
    assert fid == "fact-custom"


async def test_confidence_floor_rejects_low_confidence(fact_factory):
    svc = ContinuityService(config=ContinuityConfig(fact_confidence_floor=0.7))
    with pytest.raises(ConfidenceFloorError):
        await svc.add_fact(fact_factory(confidence=0.3), source="extractor")


async def test_retire_fact_marks_retired_with_reason(service, fact_factory):
    fid = await service.add_fact(fact_factory(text="winifred is in Sion."), source="x")
    await service.retire_fact(fid, in_post="post-7", reason=RetirementReason.SUPERSEDED.value)
    fact = await service.get_fact(fid)
    assert fact.retired
    assert fact.retired_in_post == "post-7"
    assert fact.retired_reason == RetirementReason.SUPERSEDED


async def test_retire_unknown_fact_raises(service):
    with pytest.raises(FactNotFoundError):
        await service.retire_fact("missing", in_post="p", reason="refuted")


async def test_retire_with_unknown_reason_raises(service, fact_factory):
    fid = await service.add_fact(fact_factory(), source="x")
    with pytest.raises(ValueError):
        await service.retire_fact(fid, in_post="p", reason="banana")


async def test_update_fact_applies_patch(service, fact_factory):
    fid = await service.add_fact(fact_factory(text="old"), source="x")
    updated = await service.update_fact(fid, {"text": "new", "confidence": 0.99})
    assert updated.text == "new"
    assert updated.confidence == 0.99


async def test_update_fact_rejects_unknown_field(service, fact_factory):
    fid = await service.add_fact(fact_factory(), source="x")
    with pytest.raises(ValueError):
        await service.update_fact(fid, {"unknown_field": 1})


async def test_update_fact_patches_subject(service, fact_factory):
    fid = await service.add_fact(fact_factory(characters=["winifred"]), source="x")
    updated = await service.update_fact(fid, {"about": {"character_ids": ["winifred", "julian"]}})
    assert set(updated.about.character_ids) == {"winifred", "julian"}


async def test_facts_about_filters_by_subject(service, fact_factory):
    f1 = await service.add_fact(
        fact_factory(text="A about winifred", characters=["winifred"]), source="x"
    )
    _f2 = await service.add_fact(
        fact_factory(text="B about julian", characters=["julian"]), source="x"
    )
    f3 = await service.add_fact(
        fact_factory(text="C about orchard", locations=["orchard"]), source="x"
    )
    by_char = await service.facts_about(character_ids=["winifred"])
    assert [f.id for f in by_char] == [f1]
    by_loc = await service.facts_about(location_ids=["orchard"])
    assert [f.id for f in by_loc] == [f3]


async def test_facts_about_excludes_retired_by_default(service, fact_factory):
    fid = await service.add_fact(fact_factory(text="x", characters=["winifred"]), source="x")
    await service.retire_fact(fid, in_post="p", reason="refuted")
    visible = await service.facts_about(character_ids=["winifred"])
    assert visible == []
    with_retired = await service.facts_about(character_ids=["winifred"], include_retired=True)
    assert [f.id for f in with_retired] == [fid]


async def test_facts_about_orders_by_time_desc(service, fact_factory):
    a = await service.add_fact(fact_factory(text="a", characters=["c"], day=1), source="x")
    b = await service.add_fact(fact_factory(text="b", characters=["c"], day=10), source="x")
    c = await service.add_fact(fact_factory(text="c", characters=["c"], day=5), source="x")
    results = await service.facts_about(character_ids=["c"])
    assert [f.id for f in results] == [b, c, a]


async def test_recent_facts_filters_by_since(service, fact_factory):
    a = await service.add_fact(fact_factory(text="early", day=1), source="x")
    b = await service.add_fact(fact_factory(text="middle", day=5), source="x")
    c = await service.add_fact(fact_factory(text="late", day=10), source="x")
    recent = await service.recent_facts(since=InGameTime(day_count=5))
    assert {f.id for f in recent} == {b, c}
    assert recent[0].id == c  # ordered desc
    _ = a  # silence unused


async def test_search_facts_returns_by_keyword_overlap(service, fact_factory):
    a = await service.add_fact(
        fact_factory(text="winifred promised julian the orchard.", keywords=["winifred", "julian"]),
        source="x",
    )
    _b = await service.add_fact(fact_factory(text="The weather in Sion is cold today."), source="x")
    hits = await service.search_facts("orchard winifred", top_k=5)
    assert hits
    assert hits[0].id == a


async def test_facts_about_no_filters_returns_all_nonretired(service, fact_factory):
    a = await service.add_fact(fact_factory(text="a"), source="x")
    b = await service.add_fact(fact_factory(text="b"), source="x")
    result = await service.facts_about(limit=100)
    assert {f.id for f in result} == {a, b}


async def test_source_tag_only_added_once(service, fact_factory):
    fid = await service.add_fact(fact_factory(tags=["src:extractor"]), source="extractor")
    fact = await service.get_fact(fid)
    assert fact.tags.count("src:extractor") == 1


async def test_user_declared_source_enum_persists(service, fact_factory):
    fid = await service.add_fact(fact_factory(source=FactSource.USER_DECLARED), source="user")
    fact = await service.get_fact(fid)
    assert fact.source == FactSource.USER_DECLARED
