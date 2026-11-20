"""083's voice performance, headless: a casting binds voices to the narrator, characters (any revision or one) and
campaign actors; a chat's accepted text is planned cue by cue without changing a word; rendering gives each voiced
cue its own synthesis call and timing, and anything that cannot be voiced stays text.

Proving scenario: a scene read aloud keeps the right voices after an outfit (revision) switch, reads as text with
audio off, and every cue exposes the rules that chose its voice. Synthetic content only; no engine is needed (a
test synthesizer writes tones), and no network, provider or bill is involved.
"""

from __future__ import annotations

import dataclasses
import io
import math
import struct
import wave

import pytest

from grimoire.core.identity import ChatRef, InstanceRef, SourceRef
from grimoire.scene import Post, SceneLine, Take, TurnRecord
from grimoire.voicecast import (
    CastEntry, Casting, CastingError, SpeechVoice, casting_from_document, casting_to_document, explain,
    plan_performance, render_performance, script_of, wav_seconds,
)

CHAT = ChatRef("quay", "night")
WREN = InstanceRef("quay", "character", "wren-1")
ODO = InstanceRef("quay", "character", "odo-1")
PC = InstanceRef("quay", "character", "sam-1")
WREN_R1 = SourceRef("world:harbor", "character", "wren", "r1")
WREN_R2 = SourceRef("world:harbor", "character", "wren", "r2")  # Wren's new outfit
ODO_R1 = SourceRef("world:harbor", "character", "odo", "r1")
DUCHESS = SourceRef("world:harbor", "character", "masked-duchess", "r1")  # an alternate portrayal
TABLE = (PC, WREN, ODO)


def voice(name: str, engine: str = "tone", **rights) -> SpeechVoice:
    return SpeechVoice(engine, name, **rights)


def line(line_id: str, actor, text: str, revision=None, portrayal=None, *, audience=TABLE, state="shown", by=None):
    speaker = {WREN: "Wren", ODO: "Odo", None: "Narrator"}[actor]
    return SceneLine(line_id, actor, speaker, text, audience if actor is not None else (PC,), state, by,
                     revision, portrayal if portrayal is not None else revision)


SHORT: dict[str, str] = {}  # a line's full id -> the short name a test calls it by


def ids(number: int, lines) -> tuple[SceneLine, ...]:
    """Give each line its real id, ``<turn_id>.<take>.<n>``, remembering the short name it was written with."""
    named = []
    for n, item in enumerate(lines, 1):
        full = f"{CHAT.chat_id}.{number:06d}.1.{n}"
        SHORT[full] = item.line_id
        named.append(dataclasses.replace(item, line_id=full))
    return tuple(named)


def turn(number: int, *lines, post: Post | None = None, state: str = "kept"):
    takes = (Take(1, state, "generated", TABLE, ids(number, lines)),)
    return TurnRecord(CHAT, number, "post" if post else "prompt", post, takes)


def speech(text: str, *, channel: str = "speech", kind: str = "speech", audience=TABLE, addressees=()):
    return Post("player-1", PC, kind, text, audience, channel, addressees)


def scene() -> tuple[TurnRecord, ...]:
    return (
        turn(1, line("l1", None, "*Fog rolls over the quay.*"),
             line("l2", WREN, "*She tips her hat.* Evening, stranger.", WREN_R1),
             line("l3", ODO, "Mind the nets.", ODO_R1),
             post=speech("Who keeps the customs house?")),
        turn(2, line("l4", WREN, "Nobody you'd like.", WREN_R2),  # the same Wren, after her outfit change
             line("l5", ODO, "*shrugs*", ODO_R1),
             line("l6", WREN, "A withdrawn line.", WREN_R2, state="withdrawn", by="player-1"),
             post=speech("*I study the ledger.*", channel=None, kind="action")),
        turn(3, line("l7", WREN, "Never seen.", WREN_R2), state="proposed"),  # a proposal the table never kept
    )


CAST = Casting((
    CastEntry("narrator", voice("slt")),
    CastEntry("character", voice("kal"), WREN_R1.unpinned()),
    CastEntry("character", voice("rms"), ODO_R1.unpinned()),
))


def tone_wav(seconds: float, pitch: float) -> bytes:
    rate = 8000
    frames = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * pitch * i / rate)))
                      for i in range(int(seconds * rate)))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(frames)
    return buffer.getvalue()


class Tones:
    """A test synthesizer: each voice is a pitch, each word a tenth of a second. Records every call."""

    engine = "tone"
    PITCH = {"slt": 440.0, "kal": 220.0, "rms": 330.0, "awb": 550.0}

    def __init__(self, *, fail_on: str | None = None, garbage_on: str | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_on, self.garbage_on = fail_on, garbage_on

    def voices(self):
        return tuple(self.PITCH)

    def synthesize(self, script: str, name: str) -> bytes:
        self.calls.append((script, name))
        if self.fail_on and self.fail_on in script:
            raise RuntimeError(f"engine choked on {script}")  # the message quotes the line: never kept
        if self.garbage_on and self.garbage_on in script:
            return b"not a wav"
        return tone_wav(0.1 * len(script.split()), self.PITCH[name])


def by_item(performance):
    return {SHORT.get(cue.item, cue.item): cue for cue in performance.cues}


# -- the plan --------------------------------------------------------------------------------------------------
def test_two_npcs_and_a_narrator_speak_in_their_own_voices_from_the_accepted_text_only():
    turns = scene()
    plan = plan_performance(turns, CAST, {PC: "Sam"})
    items = [SHORT.get(cue.item, cue.item) for cue in plan.cues]
    # posts first in each turn, then the kept take's shown lines; no withdrawn line and no proposal
    assert items == [turns[0].turn_id, "l1", "l2", "l3", turns[1].turn_id, "l4", "l5"]
    cues = by_item(plan)
    assert (cues["l1"].voice_key, cues["l2"].voice_key, cues["l3"].voice_key) == ("tone/slt", "tone/kal", "tone/rms")
    assert {cue.voice_key for cue in plan.voiced} == {"tone/slt", "tone/kal", "tone/rms"}
    assert plan.shared == ()  # three speakers, three voices: each can be told apart by ear
    # the accepted text is kept exactly; only the script a voice says is derived
    assert cues["l2"].text == "*She tips her hat.* Evening, stranger." and cues["l2"].script == "Evening, stranger."
    assert cues["l1"].script == "Fog rolls over the quay."  # narration is read, its asterisks only dropped
    # a PC nobody cast, an action post and a line of stage directions are text, never another speaker's voice
    post = cues[turns[0].turn_id]
    assert (post.speaker, post.status, post.reason, post.chosen) == ("Sam", "text_only", "not_cast", None)
    assert (cues[turns[1].turn_id].status, cues[turns[1].turn_id].reason) == ("text_only", "action")
    assert (cues["l5"].status, cues["l5"].reason, cues["l5"].chosen.voice.key) == ("text_only", "no_speech", "tone/rms")


def test_an_outfit_switch_keeps_the_characters_voice_and_a_revision_cast_on_its_own_is_honoured():
    plan = by_item(plan_performance(scene(), CAST))
    assert plan["l2"].revision == WREN_R1 and plan["l4"].revision == WREN_R2
    assert plan["l2"].voice_key == plan["l4"].voice_key == "tone/kal"
    assert [item.rule for item in plan["l4"].considered] == ["actor", "revision", "character_any_revision"]
    # a voice cast for revision r2 only (her disguise) applies to r2's lines and to nothing spoken at r1
    disguised = CAST.with_entry(CastEntry("character", voice("awb"), WREN_R2))
    plan = by_item(plan_performance(scene(), disguised))
    assert (plan["l2"].voice_key, plan["l4"].voice_key) == ("tone/kal", "tone/awb")
    assert plan["l4"].chosen.describe() == "world:harbor/character/wren at revision r2 only"
    # the campaign's own choice for the actor outranks every definition-level choice
    override = disguised.with_entry(CastEntry("actor", voice("rms"), WREN))
    plan = plan_performance(scene(), override)
    cues = by_item(plan)
    assert cues["l2"].voice_key == cues["l4"].voice_key == "tone/rms"
    assert [(shared.voice, shared.speakers) for shared in plan.shared] == [("tone/rms", (str(WREN), str(ODO)))]


def test_a_line_keeps_the_revision_it_was_spoken_in_whatever_the_source_says_now():
    # the plan reads only what each line recorded: casting r1 alone voices r1's lines and leaves r2's as text
    only_r1 = Casting((CastEntry("character", voice("kal"), WREN_R1),))
    cues = by_item(plan_performance(scene(), only_r1))
    assert cues["l2"].voice_key == "tone/kal"
    assert (cues["l4"].status, cues["l4"].reason) == ("text_only", "not_cast")


def test_an_alternate_portrayal_speaks_in_the_portrayals_voice():
    turns = (turn(1, line("l1", WREN, "Charmed, I'm sure.", WREN_R1, DUCHESS)),)
    cast = CAST.with_entry(CastEntry("character", voice("awb"), DUCHESS.unpinned()))
    cue = plan_performance(turns, cast).cues[0]
    assert cue.voice_key == "tone/awb"
    assert [item.rule for item in cue.considered] == [
        "actor", "portrayal", "portrayal_any_revision", "revision", "character_any_revision"]
    # without a voice for the portrayal, the actor's own character voice is used
    assert plan_performance(turns, CAST).cues[0].voice_key == "tone/kal"


def test_a_disputed_choice_names_every_rule_it_looked_at():
    cast = CAST.with_entry(CastEntry("character", voice("awb"), WREN_R2))
    cue = by_item(plan_performance(scene(), cast))["l4"]
    assert explain(cue) == (
        "Wren speaks with tone/awb, cast for world:harbor/character/wren at revision r2 only "
        "(the campaign's own choice for campaign:quay/character/wren-1: not cast; "
        "the revision world:harbor/character/wren@r2: tone/awb; "
        "the character at any revision world:harbor/character/wren: tone/kal).")
    narration = by_item(plan_performance(scene(), cast))["l1"]
    assert explain(narration).startswith("Narrator speaks with tone/slt, cast for the narrator of every campaign")
    # a campaign's own narrator outranks every campaign's
    local = cast.with_entry(CastEntry("narrator", voice("awb"), campaign="quay"))
    narration = by_item(plan_performance(scene(), local))["l1"]
    assert narration.voice_key == "tone/awb" and [item.entry is not None for item in narration.considered] == [True, True]
    uncast = by_item(plan_performance(scene(), Casting()))["l3"]
    assert explain(uncast).startswith("Odo is text only: nobody cast a voice for it, so it stays text")


def test_private_messages_and_lines_no_player_character_heard_are_never_cued():
    whisper = speech("Psst, the key is under the barrel.", channel="whisper", audience=(PC, WREN), addressees=(WREN,))
    staged = Take(1, "kept", "generated", TABLE,
                  ids(1, (line("l1", WREN, "Heard by Odo alone.", WREN_R1, audience=(WREN, ODO)),
                          line("l2", ODO, "Heard by everyone.", ODO_R1))),
                  on_stage=TABLE, players=(PC,))
    turns = (TurnRecord(CHAT, 1, "post", whisper, (staged,)),)
    assert [SHORT[cue.item] for cue in plan_performance(turns, CAST).cues] == ["l2"]


def test_planning_changes_no_text_and_refuses_what_is_not_one_chat():
    turns = scene()
    before = [repr(turn) for turn in turns]
    plan = plan_performance(turns, CAST)
    assert [repr(turn) for turn in turns] == before
    assert all(cue.digest.startswith("sha256:") for cue in plan.cues)
    other = TurnRecord(ChatRef("quay", "other"), 1, "prompt")
    with pytest.raises(CastingError) as refused:
        plan_performance((*turns, other), CAST)
    assert refused.value.reason == "invalid_turns"
    assert script_of("*nods* *smiles*", narrator=False) == ""


# -- the casting -----------------------------------------------------------------------------------------------
def test_a_casting_records_rights_and_refuses_an_unrecorded_licence_or_consent():
    assert SpeechVoice("flite", "kal").rights == "engine"
    for rights in ("licensed", "consented"):
        with pytest.raises(CastingError) as refused:
            SpeechVoice("piper", "jo", rights)
        assert refused.value.reason == "rights_unrecorded"
        with pytest.raises(CastingError):
            SpeechVoice("piper", "jo", rights, "   ")
    consented = SpeechVoice("piper", "jo", "consented", "Jo recorded these samples for this table, 2026-10")
    entry = CastEntry("actor", consented, ODO)
    cast = Casting((entry, CastEntry("narrator", voice("slt"))))
    document = casting_to_document(cast)
    assert casting_from_document(document) == cast
    assert document["cast"][0] == {"target": "actor", "ref": str(ODO), "campaign": None, "engine": "piper",
                                   "voice": "jo", "rights": "consented",
                                   "note": "Jo recorded these samples for this table, 2026-10"}
    for bad, reason in (({**document, "format": "x"}, "unsupported_format"),
                        ({**document, "extra": 1}, "unknown_field"),
                        ({**document, "cast": [*document["cast"], document["cast"][0]]}, "duplicate_entry"),
                        ({**document, "cast": [{**document["cast"][0], "rights": "consented", "note": None}]},
                         "rights_unrecorded"),
                        ({**document, "cast": [{**document["cast"][0], "target": "character"}]}, "invalid_target")):
        with pytest.raises(CastingError) as refused:
            casting_from_document(bad)
        assert refused.value.reason == reason
    with pytest.raises(CastingError):
        CastEntry("character", voice("kal"), SourceRef("world:harbor", "item", "lamp"))
    with pytest.raises(CastingError):
        CastEntry("narrator", voice("kal"), WREN)
    with pytest.raises(CastingError):
        SpeechVoice("flite", "Kal; rm -rf")
    with pytest.raises(CastingError) as refused:
        Casting().without("actor", WREN)
    assert refused.value.reason == "not_cast"


# -- rendering -------------------------------------------------------------------------------------------------
def test_each_voiced_cue_is_its_own_synthesis_call_laid_end_to_end_with_its_digest():
    plan = plan_performance(scene(), CAST)
    tones = Tones()
    rendering = render_performance(plan, [tones], gap=0.25)
    assert tones.calls == [("Fog rolls over the quay.", "slt"), ("Evening, stranger.", "kal"),
                           ("Mind the nets.", "rms"), ("Nobody you'd like.", "kal")]
    audio = [segment for segment in rendering.segments if segment.status == "audio"]
    assert [segment.voice for segment in audio] == ["tone/slt", "tone/kal", "tone/rms", "tone/kal"]
    assert [segment.cue for segment in rendering.segments] == list(range(len(plan.cues)))
    assert [round(segment.start, 2) for segment in audio] == [0.0, 0.75, 1.2, 1.75]
    assert [round(segment.duration, 2) for segment in audio] == [0.5, 0.2, 0.3, 0.3]
    assert all(segment.digest == plan.cues[segment.cue].digest for segment in rendering.segments)
    assert all(wav_seconds(segment.audio) == segment.duration for segment in audio)
    assert rendering.next is None and round(rendering.duration, 2) == 2.05


def test_with_audio_off_or_no_engine_the_scene_reads_as_text():
    plan = plan_performance(scene(), CAST)
    tones = Tones()
    off = render_performance(plan, [tones], audio=False)
    assert tones.calls == [] and all(segment.audio is None for segment in off.segments)
    assert {segment.reason for segment in off.segments if segment.voice} == {"audio_off"}
    none = render_performance(plan, [])
    assert {segment.reason for segment in none.segments if segment.voice} == {"engine_unavailable"}


def test_a_failing_or_missing_voice_leaves_that_cue_text_and_the_rest_still_speak():
    plan = plan_performance(scene(), CAST.with_entry(CastEntry("character", voice("nobody"), ODO_R1.unpinned())))
    tones = Tones(fail_on="Evening", garbage_on="Nobody")
    rendering = render_performance(plan, [tones])
    reasons = {SHORT.get(plan.cues[segment.cue].item): (segment.status, segment.reason) for segment in rendering.segments}
    assert reasons["l1"] == ("audio", None)
    assert reasons["l2"] == ("text_only", "synthesis_failed")
    assert reasons["l3"] == ("text_only", "voice_unavailable")
    assert reasons["l4"] == ("text_only", "invalid_audio")
    assert ("Mind the nets.", "nobody") not in tones.calls  # a voice the engine lacks is never asked for
    assert "choked" not in repr(rendering)  # the engine's message, which quotes the line, is not kept


def test_rendering_goes_window_by_window_within_its_bounds():
    plan = plan_performance(scene(), CAST)
    first = render_performance(plan, [Tones()], count=3)
    assert [segment.cue for segment in first.segments] == [0, 1, 2] and first.next == 3
    rest = render_performance(plan, [Tones()], start=first.next)
    assert rest.segments[0].cue == 3 and rest.next is None and rest.segments[0].start == 0.0
    # a script budget stops before the cue that would pass it (one cue alone always renders)
    tight = render_performance(plan, [Tones()], max_chars=30)
    assert [segment.cue for segment in tight.segments] == [0, 1] and tight.next == 2
    for bad in ({"start": -1}, {"start": 99}, {"count": 0}, {"count": 33}):
        with pytest.raises(CastingError):
            render_performance(plan, [Tones()], **bad)
    with pytest.raises(CastingError):
        render_performance(plan, [Tones(), Tones()])


def test_wav_seconds_refuses_what_is_not_a_whole_recording():
    good = tone_wav(0.5, 300)
    assert wav_seconds(good) == 0.5
    assert wav_seconds(good[:60]) is None  # the header promises samples the file does not hold
    assert wav_seconds(b"RIFF" + b"\0" * 60) is None and wav_seconds("text") is None
    assert wav_seconds(tone_wav(0, 300)) is None
