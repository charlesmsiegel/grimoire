"""The rendering contract: one invocation per NPC speaker, each seeing only its own brief."""

from dataclasses import FrozenInstanceError, fields
from types import MappingProxyType

import pytest

from grimoire.core import ControlAssignment, InstanceRef
from grimoire.core.authority import DIRECTOR, AuthorityError
from grimoire.voice import (
    ImportedVoiceSource, InvalidVoice, ResolvedVoice, SecretFact, SpeakerRequest, SpokenLine,
    VoiceAnchor, VoiceExample, VoiceMismatch, render_speakers, speaker_fragment,
)

CAMPAIGN = "c-ember"
BETRAYAL = SecretFact("duke-betrayal", ("duke sold the guild",))


def voice(actor_id, *, imported=None):
    examples = (VoiceExample("e1", f"{actor_id} speaks plainly."),
                VoiceExample("overheard", "The duke sold the guild!", ("duke-betrayal",)))
    anchors = (VoiceAnchor("a1", "cadence", f"{actor_id} cadence."),)
    return ResolvedVoice(InstanceRef(CAMPAIGN, "character", actor_id), actor_id.title(), None, None,
                         anchors, examples,
                         MappingProxyType({i.item_id: "source" for i in (*anchors, *examples)}), imported)


def frag(actor_id, **kwargs):
    return speaker_fragment(voice(actor_id, **kwargs), secrets=(BETRAYAL,), known=())


CONTROLS = (ControlAssignment(CAMPAIGN, "oda", DIRECTOR), ControlAssignment(CAMPAIGN, "bellamy", DIRECTOR),
            ControlAssignment(CAMPAIGN, "wren", "human:alice"))


class Spy:
    def __init__(self, reply=lambda request: f"{request.actor.instance_id} replies.", fail_on=None):
        self.requests: list[SpeakerRequest] = []
        self.reply, self.fail_on = reply, fail_on

    def __call__(self, request):
        self.requests.append(request)
        if self.fail_on == len(self.requests):
            raise RuntimeError("provider failed")
        return self.reply(request)


def test_each_speaker_gets_exactly_one_call_with_only_its_own_brief():
    spy = Spy()
    oda, bellamy = frag("oda"), frag("bellamy")
    lines = render_speakers([(oda, "cue one"), (bellamy, "cue two")], spy, CONTROLS)
    assert [r.actor.instance_id for r in spy.requests] == ["oda", "bellamy"]
    assert spy.requests[0] == SpeakerRequest(oda.actor, oda.text, "cue one")
    assert spy.requests[1] == SpeakerRequest(bellamy.actor, bellamy.text, "cue two")
    assert "bellamy" not in spy.requests[0].fragment_text.casefold()
    assert lines == (SpokenLine(oda.actor, None, "oda replies.", 0),
                     SpokenLine(bellamy.actor, None, "bellamy replies.", 1))


def test_a_request_carries_nothing_about_what_was_screened_out():
    assert {f.name for f in fields(SpeakerRequest)} == {"actor", "fragment_text", "cue"}
    spy = Spy()
    imported = ImportedVoiceSource("chara_card_v3", example_messages="ZQX-imported-example",
                                   anchor_text="ZQX-imported-anchor", extensions={"k": "ZQX-ext"})
    fragment = frag("oda", imported=imported)
    assert [o.item_id for o in fragment.omitted] == ["overheard"]
    render_speakers([(fragment, "cue")], spy, CONTROLS)
    seen = repr(spy.requests).casefold()
    for absent in ("duke", "overheard", "zqx", "unknown_fact"):
        assert absent not in seen
    with pytest.raises(FrozenInstanceError):
        spy.requests[0].cue = "changed"


@pytest.mark.parametrize("controls", [
    CONTROLS + (ControlAssignment(CAMPAIGN, "pc", "human:alice"),),
    CONTROLS,  # unassigned
    CONTROLS + (ControlAssignment(CAMPAIGN, "pc", DIRECTOR), ControlAssignment(CAMPAIGN, "pc", "human:bob")),
])
def test_player_unassigned_or_ambiguous_speakers_are_refused_before_any_call(controls):
    spy = Spy()
    with pytest.raises(AuthorityError):
        render_speakers([(frag("oda"), "c"), (frag("pc"), "c")], spy, controls)
    assert spy.requests == []


def test_a_speaker_appears_once_per_batch():
    spy = Spy()
    with pytest.raises(VoiceMismatch) as raised:
        render_speakers([(frag("oda"), "a"), (frag("oda"), "b")], spy, CONTROLS)
    assert raised.value.reason == "duplicate_speaker" and spy.requests == []


@pytest.mark.parametrize("speakers", [
    [frag("oda")],
    [(frag("oda"), None)],
    [("not a fragment", "cue")],
    [(frag("oda"), "cue", "extra")],
])
def test_malformed_batches_are_refused_before_any_call(speakers):
    spy = Spy()
    with pytest.raises(InvalidVoice):
        render_speakers(speakers, spy, CONTROLS)
    assert spy.requests == []


def test_a_failing_renderer_returns_nothing():
    spy = Spy(fail_on=2)
    with pytest.raises(RuntimeError):
        render_speakers([(frag("oda"), "a"), (frag("bellamy"), "b")], spy, CONTROLS)
    assert len(spy.requests) == 2


@pytest.mark.parametrize("reply, reason", [
    (lambda request: None, "renderer_result"),
    (lambda request: b"bytes", "renderer_result"),
    (lambda request: "   ...  ", "silent_render"),
    (lambda request: "", "silent_render"),
])
def test_unusable_renderer_output_is_refused(reply, reason):
    with pytest.raises(InvalidVoice) as raised:
        render_speakers([(frag("oda"), "a")], Spy(reply), CONTROLS)
    assert raised.value.reason == reason


def test_generators_and_empty_batches():
    spy = Spy()
    lines = render_speakers(((f, "c") for f in [frag("oda"), frag("bellamy")]), spy,
                            (c for c in CONTROLS))
    assert len(lines) == 2 == len(spy.requests)
    assert render_speakers([], spy, CONTROLS) == ()
    assert len(spy.requests) == 2


@pytest.mark.parametrize("speakers, controls, reason", [
    (5, CONTROLS, "bad_speaker"),
    (None, CONTROLS, "bad_speaker"),
    ([], 5, "bad_controls"),
])
def test_non_collection_render_inputs_fail_closed(speakers, controls, reason):
    spy = Spy()
    with pytest.raises(InvalidVoice) as raised:
        render_speakers(speakers, spy, controls)
    assert raised.value.reason == reason and raised.value.__cause__ is None and spy.requests == []
