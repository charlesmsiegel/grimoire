"""SillyTavern chat reading and the transcript model (Sprint 036): preserve, never reinterpret. Pure.

Every export here is synthetic. No provider is imported.
"""

from __future__ import annotations

import pytest

from grimoire.archaeology import StError, read_st_chat, speaker_id
from grimoire.core.identity import InstanceRef, TranscriptRef
from grimoire.transcripts import (
    Post, Speaker, TranscriptError, TranscriptPage, Variant, page_from_payload, page_to_payload, paginate,
)

HEADER = {"user_name": "Sam", "character_name": "Mara", "create_date": "2026-01-02@10h00m00s",
          "chat_metadata": {"note_prompt": "Keep it tense.", "integrity": "abc"}}


def lines(*messages, header=HEADER):
    rows = ([header] if header is not None else []) + list(messages)
    return [(number, row) for number, row in enumerate(rows, start=1)]


def mara(mes, **fields):
    return {"name": "Mara", "is_user": False, "mes": mes, **fields}


def test_every_message_is_one_post_in_order_with_its_exact_text():
    chat = read_st_chat(lines(mara("Hello.\n\n  *waves*  "), {"name": "Sam", "is_user": True, "mes": "Hi."}))
    assert [p.text for p in chat.posts] == ["Hello.\n\n  *waves*  ", "Hi."]
    assert [p.post_id for p in chat.posts] == ["p000001", "p000002"]
    assert [p.source["line"] for p in chat.posts] == [2, 3]
    assert [p.speaker.role for p in chat.posts] == ["character", "user"]
    assert all(p.audience == "unknown" and p.speaker.actor is None for p in chat.posts)
    assert chat.header["chat_metadata"]["note_prompt"] == "Keep it tense."
    kept = {(loss.path, loss.reason) for loss in chat.losses}
    assert {("chat_metadata.note_prompt", "kept_not_interpreted"), ("chat_metadata.integrity", "kept_not_interpreted")} <= kept


def test_swipes_are_variants_and_the_shown_one_is_active():
    info = [{"send_date": "a", "gen_started": "x"}, {"send_date": "b"}]
    chat = read_st_chat(lines(mara("Two", swipes=["One", "Two"], swipe_id=1, swipe_info=info)))
    post = chat.posts[0]
    assert [v.text for v in post.variants] == ["One", "Two"] and post.active == 1 and post.text == "Two"
    assert [v.text for v in post.unchosen] == ["One"]
    assert [v.at for v in post.variants] == ["a", "b"] and post.variants[0].extras["gen_started"] == "x"
    assert [v.source for v in post.variants] == [{"swipe": 0}, {"swipe": 1}]
    assert chat.losses == tuple(loss for loss in chat.losses if loss.line == 1)  # nothing lost or guessed


def test_an_edited_message_keeps_both_texts_and_shows_the_edit():
    chat = read_st_chat(lines(mara("One, edited", swipes=["One", "Two"], swipe_id=0)))
    post = chat.posts[0]
    assert [v.text for v in post.variants] == ["One", "Two", "One, edited"]
    assert post.text == "One, edited" and post.variants[2].source == {"field": "mes"}
    assert ("mes", "edited_text_differs") in {(loss.path, loss.reason) for loss in chat.losses}


@pytest.mark.parametrize("swipe_id", [5, -1, "0", None, True])
def test_an_invalid_swipe_id_is_reported_and_the_shown_text_decides(swipe_id):
    chat = read_st_chat(lines(mara("Two", swipes=["One", "Two"], swipe_id=swipe_id)))
    post = chat.posts[0]
    assert post.text == "Two" and len(post.variants) == 2
    assert ("swipe_id", "invalid_swipe_id") in {(loss.path, loss.reason) for loss in chat.losses}
    assert post.extras["swipe_id"] == swipe_id  # the source value, verbatim


def test_malformed_swipes_keep_the_message_and_the_raw_swipes():
    chat = read_st_chat(lines(mara("Hi", swipes=["Hi", 3], swipe_id=0)))
    post = chat.posts[0]
    assert [v.text for v in post.variants] == ["Hi"] and post.extras["swipes"] == ("Hi", 3)
    assert ("swipes", "invalid_swipes") in {(loss.path, loss.reason) for loss in chat.losses}


def test_unknown_fields_are_kept_verbatim():
    chat = read_st_chat(lines(mara("Hi", extra={"bias": "x", "api": "y"}, gen_started="t", title="z")))
    extras = chat.posts[0].extras
    assert extras["extra"] == {"bias": "x", "api": "y"} and extras["gen_started"] == "t" and extras["title"] == "z"
    assert "mes" not in extras


def test_hidden_narrator_and_system_messages():
    chat = read_st_chat(lines(
        mara("A secret aside.", is_system=True),
        {"name": "SillyTavern System", "is_user": False, "is_system": False, "mes": "Fog.", "extra": {"type": "narrator"}},
        {"name": "SillyTavern System", "is_user": False, "is_system": True, "mes": "Help.", "extra": {"type": "help"}},
        {"name": "Sam", "is_user": True, "is_system": True, "mes": "Hidden too."}))
    assert [(p.speaker.role, p.hidden) for p in chat.posts] == [
        ("character", True), ("narrator", False), ("system", True), ("user", True)]


def test_same_named_speakers_with_different_avatars_stay_distinct():
    chat = read_st_chat(lines(mara("I am Mara.", original_avatar="mara.png"),
                              mara("So am I.", original_avatar="mara-other.png"),
                              mara("Me again.", original_avatar="mara.png")))
    assert len(chat.speakers) == 2
    assert chat.speaker_ids[0] == chat.speaker_ids[2] != chat.speaker_ids[1]
    assert [s.posts for s in chat.speakers] == [2, 1]
    assert [(loss.line, loss.reason) for loss in chat.losses if loss.path == "name"] == [
        (2, "same_label_speakers"), (3, "same_label_speakers")]
    assert chat.speaker_ids[0] == speaker_id("character", "mara.png", "Mara")  # stable across runs


def test_a_file_without_a_header_is_read_from_its_first_line():
    chat = read_st_chat(lines(mara("Hi."), header=None))
    assert chat.header is None and chat.posts[0].source == {"line": 1}


@pytest.mark.parametrize("bad", [{"name": "Mara"}, {"name": "Mara", "mes": 3}, mara("x", is_user="yes"),
                                 mara("x", is_system=1)])
def test_a_malformed_message_refuses_the_whole_file_with_its_line(bad):
    with pytest.raises(StError) as caught:
        read_st_chat(lines(mara("fine"), bad))
    assert (caught.value.reason, caught.value.line) == ("invalid_message", 3)
    assert "fine" not in str(caught.value)


def test_an_export_with_no_messages_is_refused():
    with pytest.raises(StError) as caught:
        read_st_chat(lines())
    assert caught.value.reason == "no_messages"


@pytest.mark.parametrize("names", [("A" * 1001, "B" * 1001), (5, [1])])
def test_names_the_label_cannot_hold_still_tell_speakers_apart(names):
    chat = read_st_chat(lines(*({"name": name, "is_user": False, "mes": "x"} for name in names)))
    assert len(chat.speakers) == 2 and all(s.label is None for s in chat.speakers)


@pytest.mark.parametrize("fields,kept", [
    ({"swipes": None}, "swipes"),
    ({"swipes": ["Hi"], "swipe_id": 0, "swipe_info": None}, "swipe_info"),
    ({"swipes": ["Hi"], "swipe_id": 0, "swipe_info": []}, "swipe_info"),
    ({"swipes": ["Hi", "Yo"], "swipe_id": 0, "swipe_info": [None, {}]}, "swipe_info"),
])
def test_a_swipe_field_the_variants_cannot_reproduce_is_kept_verbatim(fields, kept):
    extras = read_st_chat(lines(mara("Hi", **fields))).posts[0].extras
    value = fields[kept]
    assert extras[kept] == (tuple(value) if isinstance(value, list) else value)


def test_a_first_line_with_message_fields_is_a_message_not_the_header():
    with pytest.raises(StError) as caught:
        read_st_chat([(1, {"name": "M", "is_user": False, "create_date": "x"}), (2, mara("hi"))])
    assert (caught.value.reason, caught.value.line) == ("invalid_message", 1)


# -- the stored page model ------------------------------------------------------------------------

REF = TranscriptRef("quick-1", "t-1", 1)
ACTOR = InstanceRef("quick-1", "character", "mara-1")


def test_a_page_round_trips_through_its_payload():
    chat = read_st_chat(lines(mara("Two", swipes=["One", "Two"], swipe_id=1, original_avatar="m.png")))
    post = chat.posts[0]
    original = TranscriptPage(REF, (Post(post.post_id, Speaker("character", ACTOR, "Mara"), post.variants, 1,
                                         source=post.source, extras=post.extras),), {"format": "x"}, chat.header)
    payload = page_to_payload(original)
    assert page_from_payload(REF, "grimoire.transcript/0", payload) == original


@pytest.mark.parametrize("change,reason", [
    (lambda p: p.update(seq=1), "identity_in_payload"),
    (lambda p: p["posts"][0].update(color="red"), "unknown_field"),
    (lambda p: p["posts"][0]["speaker"].update(actor=str(InstanceRef("other", "character", "x"))), "foreign_actor"),
    (lambda p: p["posts"][0].update(audience="everyone"), "audience"),
    (lambda p: p["posts"][0].update(active=4), "invalid_field"),
    (lambda p: p["posts"].append(dict(p["posts"][0])), "duplicate_post"),
    (lambda p: p["posts"][0].pop("variants"), "missing_field"),
])
def test_a_payload_this_schema_cannot_read_is_refused_with_a_reason(change, reason):
    payload = page_to_payload(TranscriptPage(REF, (Post("p1", Speaker("user"), (Variant("hi"),), 0),)))
    change(payload)
    with pytest.raises(TranscriptError) as caught:
        page_from_payload(REF, "grimoire.transcript/0", payload)
    assert caught.value.reason == reason


def test_another_schema_is_refused():
    with pytest.raises(TranscriptError) as caught:
        page_from_payload(REF, "grimoire.transcript/1", {"posts": []})
    assert caught.value.reason == "unsupported_schema"


def test_only_the_first_page_carries_the_header():
    post = Post("p1", Speaker("user"), (Variant("hi"),), 0)
    with pytest.raises(TranscriptError) as caught:
        TranscriptPage(TranscriptRef("quick-1", "t-1", 2), (post,), header={"a": 1})
    assert caught.value.reason == "header_not_first"


def test_paginate_splits_by_count_and_size_in_order():
    posts = [Post(f"p{i}", Speaker("user"), (Variant("x" * i),), 0) for i in range(1, 8)]
    assert [len(g) for g in paginate(posts, lambda p: 1, 10, max_posts=3)] == [3, 3, 1]
    groups = paginate(posts, lambda p: len(p.text), 7)
    assert [[p.post_id for p in g] for g in groups] == [["p1", "p2", "p3"], ["p4"], ["p5"], ["p6"], ["p7"]]
    with pytest.raises(TranscriptError) as caught:
        paginate(posts, lambda p: len(p.text), 6)
    assert caught.value.reason == "post_too_large"
