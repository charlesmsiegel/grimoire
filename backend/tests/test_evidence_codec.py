"""The 020 payload codec: one canonical, strict, text-free serialization per 012 record."""

from __future__ import annotations

import traceback

import pytest

from grimoire.core import (
    Belief, ChatRef, Disclosure, Event, InstanceRef, KnowledgeRef, Moment, Perception, Proposition, Report, Ruling,
    Subject,
)
from grimoire.evidence import (
    BATCH_SCHEMA, LABEL_LIMIT, SCHEMAS, TEXT_LIMIT, BatchEntry, EvidenceCodecError, chain_key, decode, decode_batch,
    encode, encode_batch,
)

C = "c1"
MARA, INES, DUKE = (InstanceRef(C, "character", name) for name in ("mara", "ines", "duke"))
CHAT = ChatRef(C, "t-1")
SENTINEL = "SENTINEL-PAYLOAD-TEXT"
ETAG = "sha256:" + "a" * 64


def m(sequence, scene=None, turn=None):
    return Moment(sequence, scene, turn)


FULL = [
    Event(C, "e1", "a charter changes hands", m(1, "s1", "t1"), "director", participants=(MARA, DUKE)),
    Proposition(C, "p1", "the duke sold the charter", m(1), "director",
                about=(Subject("event", "e1"), Subject("instance", str(DUKE)))),
    Ruling(C, "r1", "p1", "established", m(2), "director", grounds=(Subject("event", "e1"),)),
    Perception(C, "v1", MARA, "e1", "witnessed", "a sealed charter in the chapel", m(1), "director", confirmed=True),
    Report(C, "v2", INES, "p1", "written", m(5, "s2"), "director", confirmed=False, sender=DUKE,
           attributed_to=MARA, addressed=True, sent_at=m(3)),
    Belief(C, "b1", MARA, "p1", "suspected", m(2), "director", ("v1",)),
    Belief(C, "b2", MARA, "p1", "believed", m(4, "s1", "t9"), "player-1", ("v1",), supersedes="b1",
           contribution_id="contrib-1"),
    Disclosure(C, "d1", "e1", m(6), "director"),
]
MINIMAL = [
    Event(C, "e2", "rain", m(0), "director"),
    Proposition(C, "p2", "it rained", m(0), "director"),
    Ruling(C, "r2", "p2", "unknown", m(0), "director"),
    Report(C, "v3", INES, "p2", "overheard", m(0), "director", confirmed=True),
    Belief(C, "b3", INES, "p2", "rejected", m(0), "director", ()),
]


@pytest.mark.parametrize("record", FULL + MINIMAL, ids=lambda r: f"{type(r).__name__}")
def test_every_record_round_trips_exactly(record):
    ref, schema, payload = encode(record, CHAT)
    assert schema == SCHEMAS[type(record)]
    assert decode(ref, schema, payload) == (record, CHAT)
    assert decode(ref, schema, encode(record)[2]) == (record, None)
    assert "campaign_id" not in payload and ref.record_id not in payload.values()


def test_encoding_is_deterministic_and_omits_defaults():
    _, _, minimal = encode(MINIMAL[3])
    assert minimal == {"holder": str(INES), "proposition_id": "p2", "channel": "overheard",
                       "moment": {"sequence": 0}, "provenance": "director", "confirmed": True}
    assert encode(FULL[4]) == encode(FULL[4])
    assert encode(MINIMAL[4])[2]["evidence_ids"] == []  # required even when empty


def test_list_order_is_kept_as_given():
    swapped = Event(C, "e1", "x", m(1), "director", participants=(DUKE, MARA))
    assert encode(swapped)[2]["participants"] == [str(DUKE), str(MARA)]
    ref, schema, payload = encode(swapped)
    assert decode(ref, schema, payload)[0] == swapped


def _refused(ref, schema, payload) -> EvidenceCodecError:
    with pytest.raises(EvidenceCodecError) as caught:
        decode(ref, schema, payload)
    return caught.value


@pytest.mark.parametrize("change, reason", [
    ({"campaign_id": C}, "identity_in_payload"),
    ({"evidence_id": "v2"}, "identity_in_payload"),
    ({"established": True}, "unknown_field"),
    ({"standing": "established"}, "unknown_field"),
    ({"confirmed": None}, "invalid_field"),
    ({"confirmed": "false"}, "invalid_field"),
    ({"confirmed": 0}, "invalid_field"),
    ({"addressed": False}, "invalid_sent_at"),  # 012: sent_at needs addressed
    ({"addressed": 1}, "invalid_field"),
    ({"moment": {"sequence": True}}, "invalid_field"),
    ({"moment": {"sequence": 5.0}}, "invalid_field"),
    ({"moment": {"sequence": 5, "scene_id": None}}, "invalid_field"),
    ({"moment": {"sequence": 5, "extra": 1}}, "invalid_field"),
    ({"moment": {"sequence": -1}}, "invalid_record"),
    ({"holder": "campaign:c2/character/ines"}, "campaign_mismatch"),
    ({"holder": "ines"}, "invalid_field"),
    ({"sender": None}, "invalid_field"),
    ({"channel": "x" * (LABEL_LIMIT + 1)}, "invalid_field"),
    ({"chat": "campaign:c2#chat/t"}, "campaign_mismatch"),
    ({"chat": "not a chat"}, "invalid_field"),
    ({"sent_at": None}, "invalid_field"),
])
def test_strict_decoding_names_the_reason(change, reason):
    ref, schema, payload = encode(FULL[4], CHAT)
    assert _refused(ref, schema, {**payload, **change}).reason == reason


def test_missing_and_mismatched_fields():
    ref, schema, payload = encode(FULL[3])
    assert _refused(ref, schema, {k: v for k, v in payload.items() if k != "confirmed"}).reason == "missing_field"
    assert _refused(ref, "grimoire.perception/9", payload).reason == "unsupported_schema"
    assert _refused(ref, SCHEMAS[Event], payload).reason == "schema_mismatch"
    assert _refused(ref, SCHEMAS[Report], payload).reason in ("missing_field", "unknown_field")
    assert _refused(ref, schema, ["not", "a", "mapping"]).reason == "invalid_field"
    event_ref, event_schema, event = encode(FULL[0])
    assert _refused(event_ref, event_schema, {**event, "participants": []}).reason == "non_canonical"
    assert _refused(event_ref, event_schema, {**event, "summary": "x" * (TEXT_LIMIT + 1)}).reason == "invalid_field"


def test_explicit_defaults_are_not_canonical():
    ref, schema, payload = encode(MINIMAL[3])
    for change in ({"addressed": False}, {"sender": None}):
        expected = "non_canonical" if change == {"addressed": False} else "invalid_field"
        assert _refused(ref, schema, {**payload, **change}).reason == expected


def test_012_construction_reasons_pass_through():
    ref, schema, payload = encode(FULL[2])
    assert _refused(ref, schema, {k: v for k, v in payload.items() if k != "grounds"}).reason == "ungrounded_ruling"
    ref, schema, payload = encode(FULL[4])
    assert _refused(ref, schema, {k: v for k, v in payload.items() if k != "sent_at"}).reason == "invalid_sent_at"


def test_encoding_refuses_what_could_not_be_read_back():
    long_text = Perception(C, "v9", MARA, "e1", "witnessed", "x" * (TEXT_LIMIT + 1), m(1), "director", confirmed=True)
    long_label = Event(C, "e9", "x", m(1, "s" * (LABEL_LIMIT + 1)), "director")
    for record in (long_text, long_label):
        with pytest.raises(EvidenceCodecError) as caught:
            encode(record)
        assert caught.value.reason == "too_long" and caught.value.record in ("evidence:v9", "event:e9")
    with pytest.raises(EvidenceCodecError) as caught:
        encode(FULL[0], ChatRef("c2", "t"))
    assert caught.value.reason == "campaign_mismatch"


def test_errors_never_carry_payload_text():
    ref, schema, payload = encode(FULL[3])
    bad = {**payload, "percept": SENTINEL, "confirmed": "yes"}
    error = _refused(ref, schema, bad)
    rendered = "".join(traceback.format_exception(error))
    assert SENTINEL not in str(error) and SENTINEL not in repr(error) and SENTINEL not in rendered
    assert error.__cause__ is None and error.__context__ is None
    for change in ({"holder": SENTINEL}, {"chat": SENTINEL}, {SENTINEL: 1}, {"moment": {"sequence": SENTINEL}}):
        error = _refused(ref, schema, {**payload, **change})
        assert SENTINEL not in "".join(traceback.format_exception(error))
        assert error.__context__ is None


def test_chain_keys():
    assert chain_key(FULL[2]) == ("ruling", "p1")
    assert chain_key(FULL[5]) == ("belief", str(MARA), "p1")
    assert chain_key(FULL[7]) == ("disclosure", "e1")
    assert chain_key(FULL[0]) is None and chain_key(FULL[3]) is None


# -- the batch log ----------------------------------------------------------------------------

ENTRIES = {"event:e1": BatchEntry(ETAG), "ruling:r1": BatchEntry(ETAG, proposition="p1"),
           "belief:b1": BatchEntry(ETAG, proposition="p1", holder=str(MARA)),
           "disclosure:d1": BatchEntry(ETAG, event="e1")}


def test_batches_round_trip():
    ref, schema, payload = encode_batch(C, 1, None, ENTRIES)
    assert schema == BATCH_SCHEMA and "previous" not in payload and ref == KnowledgeRef.batch(C, 1)
    batch = decode_batch(ref, schema, payload)
    assert batch.seq == 1 and batch.previous is None and dict(batch.entries) == ENTRIES
    ref2, _, payload2 = encode_batch(C, 2, ETAG, {"event:e2": BatchEntry(ETAG)})
    assert decode_batch(ref2, BATCH_SCHEMA, payload2).previous == ETAG
    assert ENTRIES["belief:b1"].key("belief") == ("belief", str(MARA), "p1")


@pytest.mark.parametrize("seq, previous, entries, reason", [
    (1, ETAG, ENTRIES, "invalid_field"),                                    # batch 1 has no previous
    (2, None, ENTRIES, "invalid_field"),                                    # later batches do
    (1, None, {}, "invalid_field"),                                         # at least one record
    (1, None, {"event:e1": BatchEntry("sha256:short")}, "invalid_field"),
    (1, None, {"ruling:r1": BatchEntry(ETAG)}, "invalid_field"),            # a ruling needs its proposition
    (1, None, {"belief:b1": BatchEntry(ETAG, proposition="p1")}, "invalid_field"),
    (1, None, {"disclosure:d1": BatchEntry(ETAG)}, "invalid_field"),
    (1, None, {"event:e1": BatchEntry(ETAG, proposition="p1")}, "invalid_field"),
    (1, None, {"batch:0000000001": BatchEntry(ETAG)}, "invalid_field"),
    (1, None, {"belief:b1": BatchEntry(ETAG, proposition="p1", holder="campaign:c2/character/x")},
     "campaign_mismatch"),
    (1, None, {"chat:t": BatchEntry(ETAG)}, "invalid_field"),
    (1, None, {"ruling:r1": BatchEntry(ETAG, proposition="NOT AN ID !")}, "invalid_field"),  # chain keys are ids
    (0, None, ENTRIES, "invalid_field"),                                    # the ref itself, still a reason
    (1, None, {"event:e1": "not an entry"}, "invalid_field"),
    (2, "garbage", ENTRIES, "invalid_field"),                                # previous must be an etag
])
def test_bad_batches_are_never_written(seq, previous, entries, reason):
    with pytest.raises(EvidenceCodecError) as caught:
        encode_batch(C, seq, previous, entries)
    assert caught.value.reason == reason


def test_bad_batches_are_never_read():
    ref, schema, payload = encode_batch(C, 1, None, ENTRIES)
    for bad_schema, bad, reason in (("grimoire.knowledge-batch/2", payload, "unsupported_schema"),
                                    (schema, {**payload, "seq": 2}, "invalid_field"),
                                    (schema, {**payload, "extra": 1}, "unknown_field"),
                                    (schema, {**payload, "previous": None}, "invalid_field"),
                                    (schema, {**payload, "seq": True}, "invalid_field")):
        with pytest.raises(EvidenceCodecError) as caught:
            decode_batch(ref, bad_schema, bad)
        assert caught.value.reason == reason
    later, _, later_payload = encode_batch(C, 2, ETAG, ENTRIES)
    with pytest.raises(EvidenceCodecError) as caught:  # a seq that disagrees with its file name
        decode_batch(later, schema, {**later_payload, "seq": 3})
    assert caught.value.reason == "invalid_field"
    with pytest.raises(EvidenceCodecError):
        decode(ref, schema, payload)  # a batch is not a record
