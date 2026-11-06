"""035's pure turn model: the payload round trip, what each listener heard, and every refusal.

All content is synthetic. No store, provider or clock.
"""

from __future__ import annotations

import copy

import pytest

from grimoire.core.identity import ChatRef, InstanceRef, SourceRef, TurnRef
from grimoire.core.inference import CallAttempt, CallId, InferenceStage, TokenUsage
from grimoire.generation import MAX_HEARD, MAX_LINE_CHARS
from grimoire.scene import (
    MAX_CHAT_ID, MAX_TAGGED_CHAT_ID, MAX_TAKES, NARRATOR, TURN_SCHEMA, CallRecord, Post, SceneLine, SceneRefused, Take, TurnRecord,
    heard_for, parse_turn_id, render, segments, table_lines, turn_from_payload, turn_id, turn_to_payload,
    usage_totals,
)

C = "camp"
CHAT = ChatRef(C, "chat-1")
PC, MARA, SERA, LATE = (InstanceRef(C, "character", name) for name in ("pc", "mara", "sera", "late"))
NAMES = {PC: "Wren", MARA: "Mara", SERA: "Seraphine", LATE: "Tamsin"}
REV = SourceRef("world:w", "character", "mara", "r1")


def tid(number: int = 1) -> str:
    return turn_id(CHAT, number)


def attempt(number: int, take: int, actor: str | None = "mara", tokens: int | None = 10) -> CallAttempt:
    stage = InferenceStage.NPC_GENERATION if actor else InferenceStage.DIRECTOR_GENERATION
    call = CallId(C, f"{tid(number)}.{take}", "main", stage, actor, 1)
    return CallAttempt(call, 1, TokenUsage(input_tokens=tokens, output_tokens=3), "synthetic", "completed", "primary")


def line(number: int, take: int, index: int, actor=MARA, text="The ledger sank.", audience=(PC, MARA, SERA), **kw):
    speaker = NAMES[actor] if actor else NARRATOR
    return SceneLine(f"{tid(number)}.{take}.{index}", actor, speaker, text, audience, **kw)


def spoken(number: int, text: str = "Where is it?", audience=(PC, MARA, SERA), **kw) -> Post:
    return Post("human:ana", PC, "speech", text, audience, "speech", **kw)


def kept(number: int, *lines: SceneLine, post: Post | None = None, calls=()) -> TurnRecord:
    take = Take(1, "kept", "generated", (PC, MARA, SERA), lines=lines, calls=calls, outcome="complete", by="human:ana")
    return TurnRecord(CHAT, number, "post" if post else "prompt", post, (take,))


def full_turn() -> TurnRecord:
    call = CallRecord("main", InferenceStage.NPC_GENERATION, "mara", 1, "accepted", None, (attempt(2, 1),))
    proposed = Take(1, "withdrawn", "generated", (PC, MARA, SERA),
                    lines=(line(2, 1, 1, state="withdrawn", by="human:ana"),), calls=(call,), outcome="complete",
                    direction="Gentler, please.", by="human:ana")
    call2 = CallRecord("main", InferenceStage.NPC_GENERATION, "mara", 1, "accepted", None, (attempt(2, 2, tokens=None),))
    narration = CallRecord("main", InferenceStage.DIRECTOR_GENERATION, None, 1, "accepted", None, (attempt(2, 2, None),))
    second = Take(2, "kept", "generated", (PC, MARA, SERA),
                  lines=(line(2, 2, 1, actor_revision=REV, voice_profile=REV),
                         line(2, 2, 2, actor=None, text="Rain.", audience=(PC,), state="struck", by="human:ana")),
                  calls=(call2, narration), outcome="halted", declined=("main:director_generation:scene:policy",),
                  unscreened=("card:x/lorebook/y@r1#Harbor",), by="human:ana")
    whisper = Post("human:ana", PC, "speech", "Trust no one.", (PC, SERA), "whisper", (SERA,))
    return TurnRecord(CHAT, 2, "post", whisper, (proposed, second), extras={"x-later": {"kept": True}})


# -- the payload ------------------------------------------------------------------------------------

def test_a_turn_round_trips_exactly_with_its_usage_and_unknown_keys():
    record = full_turn()
    payload = turn_to_payload(record)
    again = turn_from_payload(record.ref, TURN_SCHEMA, copy.deepcopy(payload))
    assert again == record and again.extras == {"x-later": {"kept": True}}
    assert turn_to_payload(again) == payload
    assert again.takes[1].attempts()[0].usage.input_tokens is None  # unreported stays unreported
    totals = usage_totals(again.takes[1].attempts())
    assert totals["output_tokens"] == 6 and totals["unreported_input_tokens"] == 1 and totals["input_tokens"] == 10


@pytest.mark.parametrize("change, reason", [
    (lambda p: p.update(schema="x"), "identity_in_payload"),
    (lambda p: p.update(number=3), "address_mismatch"),
    (lambda p: p.update(number="2"), "invalid_field"),
    (lambda p: p["takes"][0].update(colour="red"), "invalid_field"),
    (lambda p: p["takes"][0]["lines"][0].update(review="director"), "invalid_field"),
    (lambda p: p["takes"][0]["calls"][0].update(stage="dreaming"), "invalid_field"),
    (lambda p: p["takes"][0]["calls"][0]["attempts"][0]["usage"].pop("input_tokens"), "invalid_field"),
    (lambda p: p["post"].update(submitted_by="director"), "invalid_field"),
    (lambda p: p["post"].pop("channel"), "invalid_field"),
    (lambda p: p.update(paused=True), "invalid_field"),  # paused needs paused_by
    (lambda p: p["takes"][0].update(state="kept"), "invalid_field"),
])
def test_a_malformed_payload_is_refused_never_dropped(change, reason):
    record = full_turn()
    payload = copy.deepcopy(turn_to_payload(record))
    change(payload)
    with pytest.raises(SceneRefused) as refused:
        turn_from_payload(record.ref, TURN_SCHEMA, payload)
    assert refused.value.reason == reason


def test_an_unknown_schema_is_never_read():
    record = full_turn()
    with pytest.raises(SceneRefused) as refused:
        turn_from_payload(record.ref, "grimoire.scene-turn/2", turn_to_payload(record))
    assert refused.value.reason == "unsupported_schema"


def test_a_call_billed_under_another_turn_is_refused():
    record = full_turn()
    payload = turn_to_payload(record)
    with pytest.raises(SceneRefused, match="address_mismatch"):
        turn_from_payload(TurnRef(C, tid(3)), TURN_SCHEMA, payload)
    stray = CallRecord("main", InferenceStage.NPC_GENERATION, "mara", 1, "accepted", None, (attempt(5, 1),))
    with pytest.raises(SceneRefused, match="planned under"):
        record.with_take(Take(3, "proposed", "generated", (PC,), calls=(stray,)))


# -- ids and limits ----------------------------------------------------------------------------------

def test_turn_ids_are_the_chat_and_a_six_digit_number():
    assert tid(7) == "chat-1.000007"
    assert turn_id(CHAT, 7, "3f2a91c0") == "chat-1.000007-3f2a91c0"  # made on a library with a device identity
    assert parse_turn_id("chat-1.000007-3f2a91c0") == ("chat-1", 7, "3f2a91c0")
    assert parse_turn_id("chat-1.000007") == ("chat-1", 7, None)
    for text in ("chat-1.7", "chat-1.000007-3F2A91C0", "chat-1.000007-3f2a", "000007", "chat-1.000007-"):
        assert parse_turn_id(text) is None
    with pytest.raises(SceneRefused, match="chat_id_too_long"):
        turn_id(ChatRef(C, "x" * (MAX_CHAT_ID + 1)), 1)
    # 032's message id limit (104) holds untagged at 97 characters and tagged at 88
    assert len(turn_id(ChatRef(C, "x" * MAX_CHAT_ID), 999_999)) == 104
    assert len(turn_id(ChatRef(C, "x" * MAX_TAGGED_CHAT_ID), 999_999, "0" * 8)) == 104
    with pytest.raises(SceneRefused, match="chat_id_too_long"):
        turn_id(ChatRef(C, "x" * (MAX_TAGGED_CHAT_ID + 1)), 1, "0" * 8)
    with pytest.raises(SceneRefused, match="too_many_turns"):
        turn_id(CHAT, 1_000_000)
    for device in ("", "3F2A91C0", "3f2a91c", "-3f2a91c0", 7):
        with pytest.raises(SceneRefused, match="invalid_field"):
            turn_id(CHAT, 1, device)


def test_a_turn_keeps_its_device_and_a_turn_written_without_one_still_reads():
    tagged = TurnRecord(CHAT, 2, "prompt", device="3f2a91c0")
    assert tagged.turn_id == "chat-1.000002-3f2a91c0" and tagged.ref.turn_id == tagged.turn_id
    payload = turn_to_payload(tagged)
    assert payload["device"] == "3f2a91c0"
    assert turn_from_payload(tagged.ref, TURN_SCHEMA, payload) == tagged
    untagged = TurnRecord(CHAT, 2, "prompt")
    old = turn_to_payload(untagged)
    del old["device"]  # written before turn ids carried a device tag
    read = turn_from_payload(untagged.ref, TURN_SCHEMA, old)
    assert read == untagged and read.device is None and "device" not in read.extras
    with pytest.raises(SceneRefused, match="address_mismatch"):  # the tag is part of the address
        turn_from_payload(untagged.ref, TURN_SCHEMA, payload)
    with pytest.raises(SceneRefused, match="address_mismatch"):
        turn_from_payload(tagged.ref, TURN_SCHEMA, old)
    with pytest.raises(SceneRefused, match="invalid_field"):
        turn_from_payload(untagged.ref, TURN_SCHEMA, {**old, "device": 7})


def test_an_untagged_turn_of_a_chat_id_longer_than_the_tagged_cap_still_reads():
    # turns saved before device tags, under the 97-character cap they were written with
    long_chat = ChatRef(C, "x" * MAX_CHAT_ID)
    untagged = TurnRecord(long_chat, 3, "prompt")
    old = turn_to_payload(untagged)
    del old["device"]
    assert turn_from_payload(untagged.ref, TURN_SCHEMA, old) == untagged
    with pytest.raises(SceneRefused, match="chat_id_too_long"):  # but it takes no tagged turn
        TurnRecord(long_chat, 4, "prompt", device="3f2a91c0")


@pytest.mark.parametrize("build", [
    lambda: Post("human:ana", PC, "speech", "Hi", (PC,)),  # speech without a channel
    lambda: Post("human:ana", PC, "speech", "Hi", (MARA,), "speech"),  # the speaker does not hear herself
    lambda: Post("human:ana", PC, "shout", "Hi", (PC,), "speech"),
    lambda: Post("human:ana", PC, "speech", "  ", (PC,), "speech"),
    lambda: Post("human:ana", PC, "speech", "Hi", (PC, InstanceRef("other", "character", "x")), "speech"),
    lambda: Post("human:ana", PC, "speech", "Hi", (PC, MARA), "shout"),  # not one of 032's channels
    lambda: Post("human:ana", PC, "speech", "Hi", (PC, MARA), "whisper"),  # private, yet addressed to no one
    lambda: Post("human:ana", PC, "action", "I nod.", (PC, MARA), withheld=True),  # withheld, yet heard by others
    lambda: Post("human:ana", PC, "speech", "Hi.", (PC,), "speech", withheld=True),
    lambda: TurnRecord(CHAT, 1, "prompt", takes=(Take(1, "proposed", "generated", (InstanceRef("zz", "character", "x"),)),)),
    lambda: TurnRecord(CHAT, 1, "prompt", takes=(Take(1, "proposed", "generated", (PC,), lines=(
        line(1, 1, 1, audience=(InstanceRef("zz", "character", "x"),)),)),)),
    lambda: SceneLine("chat-1.000001.1.1", MARA, "Mara", "Hi", (PC,), state="struck"),  # struck by no one
    lambda: SceneLine("chat-1.000001.1.1", MARA, "Mara", "Hi", (PC,), by="human:ana"),  # shown, yet by someone
    lambda: SceneLine("chat-1.000001.1.1", MARA, "Mara", "Hi", (PC,), actor_revision=REV.unpinned()),
    lambda: Take(MAX_TAKES + 1, "proposed", "generated", ()),
    lambda: Take(1, "running", "generated", (), lines=(line(1, 1, 1),)),
    lambda: TurnRecord(CHAT, 1, "post"),  # a post turn without its post
    lambda: TurnRecord(CHAT, 1, "prompt", takes=(Take(2, "proposed", "generated", ()),)),
    lambda: TurnRecord(CHAT, 1, "prompt", takes=(Take(1, "proposed", "generated", (), lines=(line(2, 1, 1),)),)),
    lambda: TurnRecord(CHAT, 1, "prompt", paused=True),
    lambda: kept(1, line(1, 1, 1)).__class__(CHAT, 1, "prompt", (None), kept(1, line(1, 1, 1)).takes,
                                             passed_by="human:ana"),
])
def test_invalid_values_are_refused(build):
    with pytest.raises(SceneRefused) as refused:
        build()
    assert refused.value.reason == "invalid_field"


# -- who heard what ----------------------------------------------------------------------------------

def test_each_listener_hears_only_what_reached_it_and_narration_only_the_table():
    one = kept(1, line(1, 1, 1, text="Mara speaks aloud."),
               line(1, 1, 2, actor=None, text="Rain on the roof.", audience=(PC,)),
               post=spoken(1, "Hello, all."))
    two = kept(2, line(2, 1, 1, actor=SERA, text="Seraphine answers.", audience=(PC, MARA, SERA)),
               post=Post("human:ana", PC, "speech", "Trust no one.", (PC, SERA), "whisper", (SERA,)))
    three = TurnRecord(CHAT, 3, "post", Post("human:ana", PC, "action", "I light a lamp.", (PC, MARA, SERA, LATE)))
    turns = (three, one, two)  # any order: read by number
    assert [h.text for h in heard_for(turns, MARA, NAMES)] == [
        "Hello, all.", "Mara speaks aloud.", "Seraphine answers.", "*I light a lamp.*"]
    assert [h.text for h in heard_for(turns, SERA, NAMES)] == [
        "Hello, all.", "Mara speaks aloud.", "Trust no one.", "Seraphine answers.", "*I light a lamp.*"]
    # an actor seated later was in no earlier audience: it hears from when it arrived
    assert [h.text for h in heard_for(turns, LATE, NAMES)] == ["*I light a lamp.*"]
    # the table (the director) never sees a whisper, but sees narration
    assert render(table_lines(turns, NAMES)).splitlines() == [
        "Wren: Hello, all.", "Mara: Mara speaks aloud.", "Narrator: Rain on the roof.",
        "Seraphine: Seraphine answers.", "Wren: *I light a lamp.*"]


def test_proposals_withdrawn_and_struck_lines_reach_no_one():
    proposed = TurnRecord(CHAT, 1, "prompt", takes=(Take(1, "proposed", "generated", (PC, MARA),
                                                          lines=(line(1, 1, 1, text="Not kept yet."),)),))
    struck = kept(2, line(2, 1, 1, text="Struck.", state="struck", by="human:ana"),
                  line(2, 1, 2, text="Withdrawn.", state="withdrawn", by="human:ana"), line(2, 1, 3, text="Shown."))
    for listener in (PC, MARA):
        assert [h.text for h in heard_for((proposed, struck), listener, NAMES)] == ["Shown."]
    assert [h.text for h in table_lines((proposed, struck), NAMES)] == ["Shown."]
    # the request segments are every line still shown on a proposed or kept take, all proposals
    found = segments(proposed) + segments(struck)
    assert [(s.segment_id, s.status) for s in found] == [(f"{tid(1)}.1.1", "proposed"), (f"{tid(2)}.1.3", "proposed")]


def test_the_heard_window_keeps_the_newest_lines_each_clipped():
    turns = [kept(n, line(n, 1, 1, text=f"line {n} " + "x" * (MAX_LINE_CHARS + 50))) for n in range(1, MAX_HEARD + 6)]
    heard = heard_for(turns, PC, NAMES)
    assert len(heard) == MAX_HEARD and heard[0].text.startswith("line 6 ") and heard[-1].text.startswith(
        f"line {MAX_HEARD + 5} ")
    assert all(len(h.text) == MAX_LINE_CHARS and h.text.endswith("…") for h in heard)


def test_a_wordless_line_is_heard_by_no_one_and_never_crowds_out_the_window():
    turns = [kept(1, line(1, 1, 1, text="Words first.")), kept(2, line(2, 1, 1, text="...")),
             kept(3, line(3, 1, 1, text="*—*"), post=spoken(3, "?!"))]
    assert [h.text for h in heard_for(turns, PC, NAMES)] == ["Words first."]
    assert [h.text for h in table_lines(turns, NAMES)] == ["Words first."]


def test_a_withheld_action_round_trips_and_reaches_only_its_sender():
    post = Post("human:ana", PC, "action", "I slip a note under the door.", (PC,), withheld=True)
    turn = kept(1, line(1, 1, 1, text="Mara speaks."), post=post)
    assert turn_from_payload(turn.ref, TURN_SCHEMA, turn_to_payload(turn)) == turn
    assert [h.text for h in heard_for((turn,), PC, NAMES)] == ["*I slip a note under the door.*", "Mara speaks."]
    assert [h.text for h in heard_for((turn,), MARA, NAMES)] == ["Mara speaks."]
    assert [h.text for h in table_lines((turn,), NAMES)] == ["Mara speaks."]
