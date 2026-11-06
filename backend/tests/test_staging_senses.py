"""Sprint 041: reach, sight, hearing, 032 presences and 012 perceptions over a stage (pure, synthetic)."""

from __future__ import annotations

import pytest

from grimoire.comms import Message, TransmissionError, transmit
from grimoire.core import Event, InstanceRef, Moment, Perception
from grimoire.staging import (
    EVENT_ID_MAX, Absence, Answer, Placement, StagingRefused, Whereabouts, apply, audience, check_cast,
    custody_divergence, deliver, hears, leave, move, open_way, perception_id, perceptions, place, presence,
    reach, sees, set_way, take, unlock_way, where, witnesses,
)

from staging_fixtures import C, COIN, EMMA, FRONT_DOOR, KEY, LETTER, MARA, ODD, SATCHEL, SCENE, STUDY_DOOR, TOMA, ref, stage

P = "scene:the-study/t1"


def do(s, *changes):
    for change in changes:
        s = apply(s, change, sequence=s.as_of + 1, provenance=P)
    return s


def opened():
    return do(stage(), unlock_way(MARA, STUDY_DOOR), open_way(MARA, STUDY_DOOR))


def refused(reason, call, *args, **kwargs):
    with pytest.raises(StagingRefused) as caught:
        call(*args, **kwargs)
    assert caught.value.reason == reason


def test_where():
    s = stage()
    assert where(s, LETTER) == Whereabouts("in_area", area="study")
    assert where(s, COIN) == Whereabouts("held", area="study", holder=SATCHEL)
    assert where(s, ODD) == Whereabouts("off_stage", absent=ODD)
    assert where(s, ref("cup", "item")) == Whereabouts("untracked")
    gone = do(opened(), move(MARA, STUDY_DOOR), leave(MARA, FRONT_DOOR))
    assert where(gone, KEY) == Whereabouts("off_stage", holder=MARA, absent=MARA)
    refused("campaign_mismatch", where, s, InstanceRef("other", "item", "x"))
    refused("invalid_field", where, s, "campaign:c1/item/letter")


def test_reach():
    s = stage()
    assert reach(s, TOMA, LETTER) == Answer(True, "within_reach")
    assert reach(s, MARA, COIN) == Answer(True, "in_hand")
    assert reach(s, TOMA, COIN) == Answer(False, "held_by_other")
    assert reach(s, EMMA, LETTER) == Answer(False, "elsewhere")
    assert reach(s, ODD, LETTER) == Answer(False, "not_on_stage")
    assert reach(s, LETTER, KEY) == Answer(False, "not_actor")
    assert reach(do(stage(), place(LETTER, area="study", basis="assumed")), TOMA, LETTER) == Answer(
        True, "within_reach", (f"placement:{LETTER}",))
    chest = ref("chest", "item")
    hidden = do(stage(), place(chest, area="study"), place(EMMA, holder=chest, basis="assumed"))
    assert reach(hidden, MARA, chest) == Answer(False, "holds_actor", (f"placement:{EMMA}",))
    # a no that rests on a guess says so too
    assert reach(do(stage(), place(LETTER, area="hall", basis="assumed")), TOMA, LETTER) == Answer(
        False, "elsewhere", (f"placement:{LETTER}",))


def test_sight_follows_the_door():
    s = stage()
    assert sees(s, MARA, TOMA) == Answer(True, "same_area")
    assert sees(s, MARA, EMMA) == Answer(False, "door_shut")
    assert sees(s, MARA, KEY) == Answer(True, "same_area")          # in her hand
    assert sees(s, TOMA, COIN) == Answer(False, "contained")        # inside the satchel
    assert sees(s, MARA, MARA) == Answer(True, "is_self")
    assert sees(s, ODD, MARA) == Answer(False, "viewer_off_stage")
    assert sees(s, MARA, ODD) == Answer(False, "target_off_stage")
    assert sees(s, LETTER, MARA) == Answer(False, "not_actor")
    assert sees(s, MARA, ref("cup", "item")) == Answer(False, "untracked")
    assert sees(opened(), MARA, EMMA) == Answer(True, "through_open_way")
    assumed = do(stage(), set_way(STUDY_DOOR, "open", basis="assumed"))
    assert sees(assumed, MARA, EMMA) == Answer(True, "through_open_way", (f"way:{STUDY_DOOR}",))
    sack = ref("sack", "item")
    inside = do(stage(), place(sack, area="study"), place(TOMA, holder=sack))
    assert sees(inside, TOMA, MARA) == Answer(False, "viewer_contained")
    assert sees(inside, MARA, TOMA) == Answer(False, "contained")
    no_way = stage(ways=(), absences=(Absence(ODD),))
    assert sees(no_way, MARA, EMMA) == Answer(False, "elsewhere")


def test_hearing_by_channel():
    s = stage()
    assert hears(s, TOMA, MARA, "whisper") == Answer(True, "same_area")
    assert hears(s, EMMA, MARA, "speech") == Answer(False, "door_shut")
    assert hears(s, EMMA, MARA, "whisper") == Answer(False, "door_shut")
    assert hears(s, EMMA, MARA, "telepathy") == Answer(True, "no_barrier")
    o = opened()
    assert hears(o, EMMA, MARA, "speech") == Answer(True, "through_open_way")
    assert hears(o, EMMA, MARA, "whisper") == Answer(False, "out_of_earshot")
    assert hears(s, ODD, MARA, "telepathy") == Answer(False, "listener_off_stage")
    assert hears(s, MARA, ODD, "speech") == Answer(False, "speaker_off_stage")
    assert hears(s, MARA, MARA, "speech") == Answer(True, "is_self")
    assert hears(s, MARA, TOMA, "letter") == Answer(False, "unknown_channel")
    assert hears(s, KEY, TOMA, "speech") == Answer(False, "not_actor")
    assert audience(s, MARA, "speech") == (TOMA,)
    assert audience(o, MARA, "speech") == (EMMA, TOMA)
    assert audience(s, MARA, "telepathy") == (EMMA, TOMA)
    refused("not_on_stage", audience, s, ODD, "speech")
    refused("unknown_channel", audience, s, MARA, "radio")


def test_presence_is_built_from_the_message_channel():
    o = opened()
    whisper = Message("m-1", MARA, "whisper", "the key is mine", (EMMA,))
    assert presence(o, whisper).present == tuple(sorted((MARA, TOMA), key=str))
    with pytest.raises(TransmissionError) as caught:
        transmit(whisper, presence(o, whisper), sequence=o.as_of, provenance=P)
    assert caught.value.reason == "addressee_absent"
    tele = Message("m-2", MARA, "telepathy", "the key is mine", (EMMA,))
    assert presence(o, tele, telepaths=(MARA, ODD)).telepaths == (MARA,)
    assumed = do(stage(), set_way(STUDY_DOOR, "open", basis="assumed"))
    speech = Message("m-3", MARA, "speech", "hello")
    refused("rests_on_assumption", presence, assumed, speech)
    assert EMMA in presence(assumed, speech, allow_assumed=True).present
    refused("invalid_field", presence, o, speech, telepaths="mara")
    # a whisper reaches only its addressee, so a bystander's assumed staging does not matter
    guessed = do(stage(), place(EMMA, area="hall", basis="assumed"))
    to_toma = Message("m-5", MARA, "whisper", "now", (TOMA,))
    assert TOMA in presence(guessed, to_toma).present
    refused("rests_on_assumption", presence, guessed, Message("m-6", MARA, "speech", "now"))
    refused("not_on_stage", presence, o, Message("m-4", ODD, "speech", "hi"))


def test_room_speech_heard_through_a_door_is_overheard_not_told():
    o = opened()
    sent = deliver(o, Message("m-1", MARA, "speech", "who is there"), sequence=o.as_of, provenance=P)
    by_holder = {r.holder: r for r in sent.reports}
    assert set(by_holder) == {TOMA, EMMA}
    assert by_holder[TOMA].channel == "told" and by_holder[TOMA].addressed and by_holder[TOMA].sent_at is not None
    assert by_holder[EMMA].channel == "overheard" and not by_holder[EMMA].addressed and by_holder[EMMA].sent_at is None
    to_emma = deliver(o, Message("m-2", MARA, "speech", "Emma, come", (EMMA,)), sequence=o.as_of, provenance=P)
    assert {(r.holder, r.channel) for r in to_emma.reports} == {(EMMA, "told"), (TOMA, "overheard")}
    refused("stale_stage", deliver, o, Message("m-3", MARA, "speech", "x"), sequence=o.as_of + 1, provenance=P)
    assert deliver(o.at(o.as_of + 1), Message("m-3", MARA, "speech", "x"), sequence=o.as_of + 1, provenance=P)


def event(sequence, event_id="e-1", scene=SCENE):
    return Event(C, event_id, "a letter is burned", Moment(sequence, scene), "director")


def test_perceptions_go_to_witnesses_only():
    s = stage()
    got = perceptions(s, event(1), area="study", percept="smoke curls from the desk", provenance=P)
    assert [p.holder for p in got] == [MARA, TOMA]
    pid = perception_id("e-1", MARA, channel="witnessed", area="study", percept="smoke curls from the desk")
    assert got[0] == Perception(C, pid, MARA, "e-1", "witnessed",
                                "smoke curls from the desk", Moment(1, SCENE), P, confirmed=True)
    assert pid.startswith("e-1.w-") and len(pid) == len("e-1.w-") + 16
    for bad in (dict(channel=None, area="study", percept="x"), dict(channel="seen", area="study", percept="\ud800"),
                dict(channel="seen", area="", percept="x")):
        refused("invalid_field", perception_id, "e-1", MARA, **bad)
    # another channel or percept for the same event is another, storable perception
    heard = perceptions(s, event(1), area="study", percept="a crackle", provenance=P, channel="heard")
    assert not {p.evidence_id for p in got} & {p.evidence_id for p in heard}
    assert perceptions(s, event(1), area="study", percept="smoke curls from the desk", provenance=P) == got
    assert witnesses(opened(), "study") == (EMMA, MARA, TOMA)
    assert witnesses(stage(), "hall") == (EMMA,)
    refused("stale_stage", perceptions, stage(as_of=2), event(1), area="study", percept="x", provenance=P)
    refused("stale_stage", perceptions, stage(as_of=1), event(2), area="study", percept="x", provenance=P)
    refused("scene_mismatch", perceptions, stage(), event(1, scene="elsewhere"), area="study", percept="x", provenance=P)
    assert perceptions(stage(), Event(C, "e-2", "x", Moment(1), "d"), area="study", percept="x", provenance=P)
    refused("unknown_area", perceptions, stage(), event(1), area="attic", percept="x", provenance=P)
    refused("too_long", perceptions, stage(), event(1, "e" * (EVENT_ID_MAX + 1)), area="study", percept="x", provenance=P)
    assert perceptions(stage(), event(1, "e" * EVENT_ID_MAX), area="study", percept="x", provenance=P)
    refused("invalid_field", perceptions, stage(), event(1), area="study", percept=" ", provenance=P)
    assumed = do(stage(as_of=0), place(TOMA, area="study", basis="assumed"))
    refused("rests_on_assumption", perceptions, assumed, event(1), area="study", percept="x", provenance=P)
    assert len(perceptions(assumed, event(1), area="study", percept="x", provenance=P, allow_assumed=True)) == 2
    # an actor assumed to be inside a wardrobe is left out only on a guess: refused too
    wardrobe = ref("wardrobe", "item")
    boxed = do(do(stage(as_of=0), place(wardrobe, area="study")), place(TOMA, holder=wardrobe, basis="assumed"))
    refused("rests_on_assumption", perceptions, boxed, event(boxed.as_of), area="study", percept="x", provenance=P)
    assert [p.holder for p in perceptions(boxed, event(boxed.as_of), area="study", percept="x", provenance=P,
                                          allow_assumed=True)] == [MARA]
    assert sees(boxed, MARA, TOMA) == Answer(False, "contained", (f"placement:{TOMA}",))
    assert sees(boxed, TOMA, MARA) == Answer(False, "viewer_contained", (f"placement:{TOMA}",))


def test_custody_divergence_against_034_holders():
    s = stage()
    chest = ref("chest", "item")
    study = ref("the-study-room", "location")
    assert custody_divergence(s, {KEY: MARA, LETTER: study, COIN: SATCHEL}, here=(study,)) == ()
    assert custody_divergence(s, {KEY: TOMA, LETTER: MARA, ref("cup", "item"): MARA}) == (KEY, LETTER)
    # a holder that is not this scene's ground disagrees with an item on the desk
    assert custody_divergence(s, {LETTER: chest}) == (LETTER,)
    assert custody_divergence(s, {LETTER: ref("bob", "character")}, here=(study,)) == (LETTER,)
    assert custody_divergence(s, {KEY: study}, here=(study,)) == (KEY,)  # the stage has it in Mara's hand
    refused("invalid_field", custody_divergence, s, {}, here="x")


def test_check_cast_against_033():
    from grimoire.party import PartyState, stage as stage_cast
    state = PartyState.build(C, (), as_of=1, unreadable=False)
    cast = stage_cast(state, SCENE, (MARA, TOMA, EMMA, ODD), instances=(MARA, TOMA, EMMA, ODD))
    check_cast(stage(), cast)
    short = stage_cast(state, SCENE, (MARA, TOMA, EMMA), instances=(MARA, TOMA, EMMA))
    refused("cast_mismatch", check_cast, stage(), short)
    other_scene = stage_cast(state, "another-scene", (MARA, TOMA, EMMA, ODD), instances=(MARA, TOMA, EMMA, ODD))
    refused("cast_mismatch", check_cast, stage(), other_scene)
