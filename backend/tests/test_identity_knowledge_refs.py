"""Knowledge record references (Sprint 020): their own family, never parsed as another."""

from __future__ import annotations

import pytest

from grimoire.core.identity import KNOWLEDGE_FAMILIES, ChatRef, KnowledgeRef, ScopeRef, parse_ref


@pytest.mark.parametrize("family", [f for f in KNOWLEDGE_FAMILIES if f != "batch"])
def test_knowledge_refs_round_trip_and_name_one_record(family):
    ref = KnowledgeRef("c1", family, "x-7")
    assert str(ref) == f"campaign:c1#{family}/x-7"
    assert KnowledgeRef.parse(str(ref)) == ref
    assert ref.name == f"{family}:x-7"
    assert KnowledgeRef.from_name("c1", ref.name) == ref


def test_batch_refs_are_ten_digit_sequence_numbers_from_one():
    ref = KnowledgeRef.batch("c1", 12)
    assert str(ref) == "campaign:c1#batch/0000000012" and ref.seq == 12
    assert KnowledgeRef.parse(str(ref)) == ref
    for bad in ("1", "0000000000", "00000000001", "000000001a"):
        with pytest.raises(ValueError):
            KnowledgeRef("c1", "batch", bad)
    for bad in (0, -1, True, 1.0):
        with pytest.raises(ValueError):
            KnowledgeRef.batch("c1", bad)
    with pytest.raises(ValueError):
        KnowledgeRef("c1", "event", "e1").seq


@pytest.mark.parametrize("text", [
    "campaign:c1#chat/t", "campaign:c1#Event/e1", "campaign:c1#event/E1", "campaign:c1#event/",
    "campaign:c1#event/e1/x", " campaign:c1#event/e1", "campaign:C1#event/e1", "campaign:c1/event/e1",
    "campaign:c1#perception/e1", "campaign:c1#report/e1",
])
def test_knowledge_ref_parsing_rejects_rather_than_normalizes(text):
    with pytest.raises(ValueError):
        KnowledgeRef.parse(text)


def test_knowledge_refs_never_parse_as_another_family():
    text = str(KnowledgeRef("c1", "event", "e1"))
    for parse in (ChatRef.parse, ScopeRef.parse, parse_ref):
        with pytest.raises(ValueError):
            parse(text)
    with pytest.raises(ValueError):
        KnowledgeRef.parse(str(ChatRef("c1", "t1")))


@pytest.mark.parametrize("name", ["event", "event:e1:x", "chat:t1", "event:E1", 7, None])
def test_from_name_is_strict(name):
    with pytest.raises(ValueError):
        KnowledgeRef.from_name("c1", name)


def test_segment_limits_apply_to_both_ids():
    longest = KnowledgeRef("c" * 128, "disclosure", "d" * 128)
    assert KnowledgeRef.parse(str(longest)) == longest
    for campaign, record in (("c" * 129, "d"), ("c", "d" * 129)):
        with pytest.raises(ValueError):
            KnowledgeRef(campaign, "event", record)
