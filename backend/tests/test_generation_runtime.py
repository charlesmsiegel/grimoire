"""027: one metered call per speaking NPC, each from its own brief; proposals only, nothing committed.

Acceptance scenarios A to D of the slice spec, the runtime's refusals, and its fence and cancel
rules. Every provider call is a scripted fake: no paid model is called.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from generation_fixtures import (
    BELLAMY, DIRECTOR_POLICY, ODA, PRIVATE, QUILL, TRUTH, Harness, brief, say, scene, view, world,
)
from portrayal_fixtures import BASE, MODERN_MARA, elderfen_index
from voice_fixtures import CAMPAIGN, PC, SECRETS

from grimoire.core import (
    ControlAssignment, Event, InstanceRef, Knowledge, Moment, Perception, actor_knowledge, snapshot_actor_view,
)
from grimoire.core.authority import DIRECTOR
from grimoire.core.entity import EntityInstance
from grimoire.core.inference import InferencePolicy, InferenceStage, OptionalRequest, TurnUsageLedger, plan_turn
from grimoire.core.snapshot import EffectiveSnapshot, SelectedSource, SourceSelection
from grimoire.generation import (
    GenerationError, npc_brief, plan_generation_turn, resolve_snapshot_voice,
)
from grimoire.providers import RouteChoice
from grimoire.providers.cancel import CancelToken
from grimoire.providers.meter import TurnMeter
from grimoire.runtime import NARRATOR, GenerationRuntime, RunFence
from grimoire.testing.providers import Reply, Status, Supersede, Truncate
from grimoire.voice import SecretFact, VoiceAnchor, VoiceBinding, VoiceCatalog, VoiceExample, VoiceProfile

PRESENT = ("npc-oda", "npc-bellamy", "npc-quill", PC)
ODA_LINE, BELLAMY_LINE = "Coin first. Then talk.", "Oh darling, how splendidly grim!"
LEAK = "Word is, the duke's betrayal came cheap."


def two_speakers(harness, controls, voices, **kw):
    plan = harness.plan(controls, present=PRESENT, speakers=("npc-oda", "npc-bellamy"), **kw)
    return plan, harness.meter(plan), [brief(ODA, "Oda", voices), brief(BELLAMY, "Bellamy", voices)]


# -- A. two-NPC exchange --------------------------------------------------------------------------

def test_a_two_npc_exchange_is_two_separately_voiced_calls():
    controls, voices = world()
    harness = Harness([say(ODA_LINE), say(BELLAMY_LINE)])
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    outcome = harness.runtime.run_turn(meter, briefs, controls=controls)

    assert outcome.status == "complete"
    assert [(line.actor, line.speaker, line.text) for line in outcome.lines] == [
        (ODA, "Oda", ODA_LINE), (BELLAMY, "Bellamy", BELLAMY_LINE)]
    oda_prompt, bellamy_prompt = harness.prompts()
    assert "Counting-house" in oda_prompt and "Darling" not in oda_prompt
    assert "Darling" in bellamy_prompt and "Counting-house" not in bellamy_prompt
    assert ODA_LINE in bellamy_prompt and ODA_LINE not in oda_prompt  # later speakers hear earlier lines
    assert "Quill" not in oda_prompt + bellamy_prompt  # the silent NPC costs nothing
    summary = meter.summary()
    assert summary.baseline.attempts == 2 and summary.optional.attempts == 0
    assert InferenceStage.DIRECTOR_GENERATION not in summary.by_stage
    assert set(summary.by_actor) == {"npc-oda", "npc-bellamy"}


# -- B. private observation -----------------------------------------------------------------------

def test_b_each_prompt_holds_only_its_own_actors_knowledge():
    controls, voices = world()
    harness = Harness([say(ODA_LINE), say(BELLAMY_LINE)])
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    harness.runtime.run_turn(meter, briefs, controls=controls)
    oda_prompt, bellamy_prompt = harness.prompts()
    assert PRIVATE in bellamy_prompt
    assert PRIVATE not in oda_prompt and TRUTH not in oda_prompt and TRUTH not in bellamy_prompt


def test_b_a_reply_stating_a_secret_the_speaker_never_learned_is_rejected_and_halts():
    controls, voices = world()
    harness = Harness([say(LEAK), say(BELLAMY_LINE)])
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    outcome = harness.runtime.run_turn(meter, briefs, controls=controls)
    assert outcome.status == "halted" and outcome.lines == ()
    assert [(c.status, c.reason) for c in outcome.calls] == [("rejected", "unlearned_secret"), ("not_issued", None)]
    assert len(harness.transport.sends) == 1  # Bellamy's call was never issued
    assert meter.summary().baseline.attempts == 1  # the rejected call is still billed


def test_b_a_secret_spoken_earlier_this_turn_may_be_repeated():
    controls, voices = world()
    knower = Knowledge(CAMPAIGN, events=[Event(CAMPAIGN, "betrayal", TRUTH, Moment(1), "director")],
                       evidence=[Perception(CAMPAIGN, "heard", ODA, "betrayal", "hearing",
                                            "the duke's betrayal, whispered in the cellar", Moment(1), "director",
                                            confirmed=True)])
    oda = npc_brief(replace(view(ODA, "Oda"), knowledge=actor_knowledge(knower, ODA, as_of=1)),
                    voices["npc-oda"], secrets=SECRETS)
    harness = Harness([say(LEAK), say("The duke's betrayal? Oh, how simply wicked!")])
    plan = harness.plan(controls, present=PRESENT, speakers=("npc-oda", "npc-bellamy"))
    outcome = harness.runtime.run_turn(harness.meter(plan), [oda, brief(BELLAMY, "Bellamy", voices)],
                                       controls=controls)
    assert outcome.status == "complete" and len(outcome.lines) == 2


def test_b_a_hidden_name_never_reaches_a_later_speaker_as_a_label():
    controls, voices = world()
    secrets = (*SECRETS, SecretFact("bellamy-identity", ("Bellamy",)))
    harness = Harness([say(BELLAMY_LINE), say(ODA_LINE)])
    plan = harness.plan(controls, present=PRESENT, speakers=("npc-bellamy", "npc-oda"))
    briefs = [brief(BELLAMY, "Bellamy", voices, secrets=secrets), brief(ODA, "Oda", voices, secrets=secrets)]
    outcome = harness.runtime.run_turn(harness.meter(plan), briefs, controls=controls)
    assert outcome.status == "complete"
    bellamy_prompt, oda_prompt = harness.prompts()
    assert "Bellamy" in bellamy_prompt  # its own name
    assert "Bellamy" not in oda_prompt and f"Someone: {BELLAMY_LINE}" in oda_prompt


# -- C. scenario-only chat ------------------------------------------------------------------------

def test_c_a_scenario_only_chat_gets_one_optional_director_reply():
    controls, _voices = world()
    harness = Harness([say("Rain drums on the shutters as the hall falls quiet.")])
    plan = harness.plan(controls, present=(PC,), speakers=(), director_reply=True, policy=DIRECTOR_POLICY)
    meter = harness.meter(plan)
    narration = scene("A guild dinner in a flooded hall.", controls)
    outcome = harness.runtime.run_turn(meter, [], controls=controls, director=narration)
    assert outcome.status == "complete"
    assert [(line.actor, line.speaker) for line in outcome.lines] == [(None, NARRATOR)]
    summary = meter.summary()
    assert summary.baseline.attempts == 0 and summary.optional.attempts == 1
    assert summary.by_stage[InferenceStage.DIRECTOR_GENERATION].attempts == 1
    assert "Never write their words, thoughts, feelings or decisions" in harness.prompts()[0]


def test_c_with_the_default_policy_the_director_reply_is_declined_and_nothing_is_sent():
    controls, _voices = world()
    harness = Harness([])
    plan = harness.plan(controls, present=(PC,), speakers=(), director_reply=True)
    assert [d.reason for d in plan.declined] == ["disabled"]
    outcome = harness.runtime.run_turn(harness.meter(plan), [], controls=controls)
    assert outcome.status == "complete" and outcome.lines == () and harness.transport.sends == []
    narration = scene("A hall.", controls)
    with pytest.raises(GenerationError) as caught:
        harness.runtime.run_turn(harness.meter(plan), [], controls=controls, director=narration)
    assert caught.value.reason == "director_mismatch"


def test_c_a_director_after_speakers_hears_their_lines():
    controls, voices = world()
    harness = Harness([say(ODA_LINE), say(BELLAMY_LINE), say("The candles gutter.")])
    _plan, meter, briefs = two_speakers(harness, controls, voices, director_reply=True, policy=DIRECTOR_POLICY)
    narration = scene("A guild dinner.", controls)
    outcome = harness.runtime.run_turn(meter, briefs, controls=controls, director=narration)
    assert [line.speaker for line in outcome.lines] == ["Oda", "Bellamy", NARRATOR]
    assert ODA_LINE in harness.prompts()[2] and BELLAMY_LINE in harness.prompts()[2]


def test_a_hearing_rule_decides_which_earlier_lines_each_later_call_is_given():
    controls, voices = world()
    asked = []

    def hears(listener, speaker):  # Bellamy is out of Oda's earshot; the director hears only Bellamy
        asked.append((listener, speaker))
        return speaker == BELLAMY if listener is None else False
    harness = Harness([say(ODA_LINE), say(BELLAMY_LINE), say("The candles gutter.")])
    _plan, meter, briefs = two_speakers(harness, controls, voices, director_reply=True, policy=DIRECTOR_POLICY)
    outcome = harness.runtime.run_turn(meter, briefs, controls=controls, director=scene("A guild dinner.", controls),
                                       hears=hears)
    assert [line.speaker for line in outcome.lines] == ["Oda", "Bellamy", NARRATOR]
    _oda, bellamy, director = harness.prompts()
    assert ODA_LINE not in bellamy and ODA_LINE not in director and BELLAMY_LINE in director
    assert asked == [(BELLAMY, ODA), (None, ODA), (None, BELLAMY)]
    with pytest.raises(GenerationError) as error:
        harness.runtime.run_turn(meter, briefs, controls=controls, hears="everyone")
    assert error.value.reason == "bad_hears"


# -- D. world-less Mara ---------------------------------------------------------------------------

BASE_VOICE = VoiceProfile(BASE, (VoiceAnchor("cadence", "cadence", "Answers questions with questions."),
                                 VoiceAnchor("diction", "diction", "Archive words: folio, ledger, margin.")),
                          (VoiceExample("e1", "Does the margin not say so already?"),))


def mara_turn(chat: str, source, binding=None, percept="A kettle whistled"):
    actor = InstanceRef(chat, "character", "mara-1")
    instance = EntityInstance(actor, source)
    snap = EffectiveSnapshot(elderfen_index(), SourceSelection(None, (SelectedSource(source, "character",
                                                                                     actor=actor),)),
                             instances=[(instance, None)])
    ledger = Knowledge(chat, events=[Event(chat, "e1", "Something happened", Moment(1), "director")],
                       evidence=[Perception(chat, "p1", actor, "e1", "hearing", percept, Moment(1), "director",
                                            confirmed=True)])
    view = snapshot_actor_view(instance, snap, actor_knowledge(ledger, actor, as_of=1))
    voice = resolve_snapshot_voice(actor, snap, VoiceCatalog([BASE_VOICE]), binding)
    controls = (ControlAssignment(chat, "mara-1", DIRECTOR),)
    harness = Harness([say("Is it not late for tea?")])
    plan = plan_generation_turn(chat, "t1", present=("mara-1",), speakers=("mara-1",), controls=controls)
    card = npc_brief(view, voice, secrets=(), sections=snap.effective(source).sections)
    outcome = harness.runtime.run_turn(harness.meter(plan), [card], controls=controls)
    return outcome, harness.prompts()


def test_d_base_and_modern_mara_are_separate_actors_with_inherited_voice():
    binding = VoiceBinding("chat-modern", "mara-1",
                           anchors=(VoiceAnchor("diction", "diction", "Library-science jargon, deadpan."),))
    base_out, (base_prompt,) = mara_turn("chat-base", BASE, percept="A raven tapped the archive window")
    modern_out, (modern_prompt,) = mara_turn("chat-modern", MODERN_MARA, binding, percept="A phone buzzed twice")
    assert base_out.status == modern_out.status == "complete"
    assert base_out.lines[0].actor != modern_out.lines[0].actor
    assert "Answers questions with questions." in base_prompt and "Archive words" in base_prompt
    assert "Answers questions with questions." in modern_prompt  # inherited through the pinned chain
    assert "Library-science jargon" in modern_prompt and "Archive words" not in modern_prompt
    assert "raven" in base_prompt and "raven" not in modern_prompt  # separate knowledge
    assert "phone" in modern_prompt and "phone" not in base_prompt
    assert "Data librarian." in modern_prompt and "Archivist." in base_prompt  # each one's composed card
    assert "Archivist." not in modern_prompt
    assert modern_out.lines[0].actor_revision == MODERN_MARA and modern_out.lines[0].voice_profile == BASE
    assert base_out.lines[0].actor_revision == base_out.lines[0].voice_profile == BASE


def test_d_a_suppressed_voice_item_does_not_return_through_the_card():
    binding = VoiceBinding("chat-base", "mara-1", suppress=("cadence",))
    outcome, (prompt,) = mara_turn("chat-base", BASE, binding)
    assert outcome.status == "complete"
    assert "Answers questions with questions." not in prompt and "Archive words" in prompt
    assert "Archivist." in prompt  # the rest of the card is still there


# -- refusals before any call ---------------------------------------------------------------------

def test_a_call_without_its_brief_or_a_brief_without_its_call_is_refused():
    controls, voices = world()
    harness = Harness([])
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    with pytest.raises(GenerationError) as caught:
        harness.runtime.run_turn(meter, briefs[:1], controls=controls)
    assert caught.value.reason == "missing_brief"
    with pytest.raises(GenerationError) as caught:
        harness.runtime.run_turn(meter, briefs + [brief(QUILL, "Quill", voices)], controls=controls)
    assert caught.value.reason == "extra_brief"
    with pytest.raises(GenerationError) as caught:
        harness.runtime.run_turn(meter, briefs + briefs[:1], controls=controls)
    assert caught.value.reason == "duplicate_brief"
    assert harness.transport.sends == []


def test_a_plan_with_another_stage_is_refused():
    controls, voices = world()
    harness = Harness([])
    plan = plan_turn(CAMPAIGN, "t1", present_actor_ids=PRESENT, speaking_npc_ids=["npc-oda"],
                     player_controlled_ids=[PC], policy=InferencePolicy({"planning": 1}),
                     optional_requests=[OptionalRequest(InferenceStage.PLANNING, None, "plan the scene")])
    with pytest.raises(GenerationError) as caught:
        harness.runtime.run_turn(TurnMeter(TurnUsageLedger(plan)), [brief(ODA, "Oda", voices)], controls=controls)
    assert caught.value.reason == "unsupported_stage"


def test_a_hand_built_plan_cannot_voice_a_player_character():
    controls, voices = world()
    harness = Harness([])
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    seized = tuple(c for c in controls if c.actor_id != "npc-oda")
    seized += (ControlAssignment(CAMPAIGN, "npc-oda", "human:bob"),)
    with pytest.raises(GenerationError) as caught:
        harness.runtime.run_turn(meter, briefs, controls=seized)
    assert caught.value.reason == "player_speaker"
    assert harness.transport.sends == []


def test_a_director_brief_for_other_player_characters_is_refused():
    controls, _voices = world()
    harness = Harness([])
    plan = harness.plan(controls, present=(PC,), speakers=(), director_reply=True, policy=DIRECTOR_POLICY)
    stale_cast = scene("A hall.", controls, present=())  # built before Wren joined
    with pytest.raises(GenerationError) as caught:
        harness.runtime.run_turn(harness.meter(plan), [], controls=controls, director=stale_cast)
    assert caught.value.reason == "director_mismatch"
    narration = scene("A hall.", controls)
    handed = tuple(c for c in controls if c.actor_id != PC) + (ControlAssignment(CAMPAIGN, PC, DIRECTOR),)
    with pytest.raises(GenerationError) as caught:  # control changed after the brief was built
        harness.runtime.run_turn(harness.meter(plan), [], controls=handed, director=narration)
    assert caught.value.reason == "director_mismatch"
    assert harness.transport.sends == []


def test_wrong_controls_cannot_drop_the_player_character_from_the_director_brief():
    controls, _voices = world()
    harness = Harness([say("Wren draws her blade.")])
    plan = harness.plan(controls, present=(PC,), speakers=(), director_reply=True, policy=DIRECTOR_POLICY)
    with pytest.raises(GenerationError) as caught:
        scene("A hall.", (), names={})
    assert caught.value.reason == "unassigned_actor"
    seized = tuple(c for c in controls if c.actor_id != PC) + (ControlAssignment(CAMPAIGN, PC, DIRECTOR),)
    blind = scene("A hall.", seized)  # names no player character
    for given, reason in (((), "unassigned_actor"), (seized, "director_mismatch")):
        with pytest.raises(GenerationError) as caught:
            harness.runtime.run_turn(harness.meter(plan), [], controls=given, director=blind)
        assert caught.value.reason == reason  # the plan's player character still counts
    assert harness.transport.sends == []


def test_a_reply_copying_another_speakers_example_is_rejected():
    controls, voices = world()
    copied = voices["npc-oda"].examples[0].text
    harness = Harness([say(ODA_LINE), say(copied)])
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    outcome = harness.runtime.run_turn(meter, briefs, controls=controls)
    assert outcome.status == "halted" and [line.speaker for line in outcome.lines] == ["Oda"]
    assert (outcome.calls[1].status, outcome.calls[1].reason) == ("rejected", "copied_example")


def test_a_reply_copying_a_silent_present_npcs_example_is_rejected():
    """014 asks for every scene voice, not only this turn's speakers (Wave 4 audit): Quill is present
    but silent, and Oda reciting Quill's authored example is still a copy."""
    controls, voices = world()
    copied = voices["npc-quill"].examples[0].text
    plan_args = dict(present=("npc-oda", "npc-bellamy", "npc-quill", PC), speakers=("npc-oda",))
    harness = Harness([say(copied)])
    outcome = harness.runtime.run_turn(harness.meter(harness.plan(controls, **plan_args)), [brief(ODA, "Oda", voices)],
                                       controls=controls, scene_voices=[voices["npc-quill"]])
    assert outcome.lines == () and (outcome.calls[0].status, outcome.calls[0].reason) == ("rejected", "copied_example")
    with pytest.raises(GenerationError) as caught:
        harness.runtime.run_turn(harness.meter(harness.plan(controls, turn="t2", **plan_args)),
                                 [brief(ODA, "Oda", voices)], controls=controls, scene_voices=["Quill"])
    assert caught.value.reason == "bad_voice"


def test_a_speaker_planned_after_the_director_reply_is_refused():
    controls, voices = world()
    harness = Harness([])
    plan = harness.plan(controls, present=PRESENT, speakers=("npc-oda",), director_reply=True,
                        policy=DIRECTOR_POLICY)
    meter = harness.meter(plan)
    meter.add_phase("second", speaking_npc_ids=("npc-bellamy",))
    narration = scene("A hall.", controls, present=PRESENT)
    with pytest.raises(GenerationError) as caught:
        harness.runtime.run_turn(meter, [brief(ODA, "Oda", voices), brief(BELLAMY, "Bellamy", voices)],
                                 controls=controls, director=narration)
    assert caught.value.reason == "director_not_last"
    assert harness.transport.sends == []


def test_a_meter_whose_calls_already_ran_is_refused():
    controls, voices = world()
    harness = Harness([say(ODA_LINE), say(BELLAMY_LINE)])
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    harness.runtime.run_turn(meter, briefs, controls=controls)
    with pytest.raises(GenerationError) as caught:
        harness.runtime.run_turn(meter, briefs, controls=controls)
    assert caught.value.reason == "call_already_run"


# -- lines that never land ------------------------------------------------------------------------

@pytest.mark.parametrize("step", [Truncate(1), Reply(finish_reason="length"), Status(400), say("  ... ")])
def test_a_call_that_does_not_end_with_a_screened_line_delivers_nothing_and_halts(step):
    controls, voices = world()
    harness = Harness([say(ODA_LINE), step])
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    outcome = harness.runtime.run_turn(meter, briefs, controls=controls)
    assert outcome.status == "halted" and [line.speaker for line in outcome.lines] == ["Oda"]
    assert outcome.calls[1].status != "accepted"


def test_an_overlong_reply_is_rejected_not_raised():
    controls, voices = world()
    harness = Harness([say("Coin first. " * 200), say(BELLAMY_LINE)])
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    outcome = harness.runtime.run_turn(meter, briefs, controls=controls)
    assert outcome.status == "halted" and outcome.lines == ()
    assert [(c.status, c.reason) for c in outcome.calls] == [("rejected", "too_long"), ("not_issued", None)]


def test_a_retry_never_duplicates_a_line():
    controls, voices = world()
    harness = Harness([Status(503), say(ODA_LINE), say(BELLAMY_LINE)], attempts=2)
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    outcome = harness.runtime.run_turn(meter, briefs, controls=controls)
    assert [line.text for line in outcome.lines] == [ODA_LINE, BELLAMY_LINE]
    assert meter.summary().baseline.attempts == 3


# -- fence and cancel -----------------------------------------------------------------------------

def test_a_stale_run_sends_nothing_and_returns_no_lines():
    controls, voices = world()
    harness = Harness([say(ODA_LINE)])
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    fence = RunFence()
    token = fence.claim("chat-1")
    fence.claim("chat-1")
    outcome = harness.runtime.run_turn(meter, briefs, controls=controls, token=token)
    assert outcome.status == "stale" and outcome.lines == () and harness.transport.sends == []


def test_a_newer_run_mid_stream_makes_the_whole_turn_deliver_nothing():
    controls, voices = world()
    harness = Harness([say(ODA_LINE), Supersede(1)])
    fence = RunFence()
    token = fence.claim("chat-1")
    harness.transport.on_supersede = lambda: fence.claim("chat-1")
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    outcome = harness.runtime.run_turn(meter, briefs, controls=controls, token=token)
    assert outcome.status == "stale" and outcome.lines == ()
    assert [c.status for c in outcome.calls] == ["accepted", "cancelled"]


def test_another_chat_key_does_not_stale_this_run():
    controls, voices = world()
    harness = Harness([say(ODA_LINE), say(BELLAMY_LINE)])
    fence = RunFence()
    token = fence.claim("chat-1")
    fence.claim("chat-2")
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    assert harness.runtime.run_turn(meter, briefs, controls=controls, token=token).status == "complete"


def test_a_cancelled_run_issues_nothing_more():
    controls, voices = world()
    harness = Harness([say(ODA_LINE)])
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    cancel = CancelToken()
    cancel.cancel()
    outcome = harness.runtime.run_turn(meter, briefs, controls=controls, cancel=cancel)
    assert outcome.status == "cancelled" and harness.transport.sends == []
    assert {c.status for c in outcome.calls} == {"not_issued"}


# -- handles --------------------------------------------------------------------------------------

def test_an_untrusted_handle_runs_with_per_attempt_approvals():
    controls, voices = world()
    harness = Harness([say(ODA_LINE), say(BELLAMY_LINE)], approve=True)
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    outcome = harness.runtime.run_turn(meter, briefs, controls=controls)
    assert outcome.status == "complete" and len(harness.approved) == 2


def test_an_approval_callback_that_raises_halts_and_keeps_earlier_lines():
    controls, voices = world()
    harness = Harness([say(ODA_LINE), say(BELLAMY_LINE)], approve=True)
    approve = harness.runtime._approvals
    calls = []

    def flaky(label, attempt, payload):
        calls.append(label)
        if len(calls) > 1:
            raise TimeoutError("nobody answered")
        return approve(label, attempt, payload)

    runtime = GenerationRuntime(harness.adapter, (RouteChoice("primary", "model-a"),), harness.handle,
                                approvals=flaky)
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    outcome = runtime.run_turn(meter, briefs, controls=controls)
    assert outcome.status == "halted" and [line.text for line in outcome.lines] == [ODA_LINE]
    assert (outcome.calls[1].status, outcome.calls[1].reason) == ("failed", "TimeoutError")


def test_an_untrusted_handle_without_approval_is_refused_and_halts():
    controls, voices = world()
    harness = Harness([], approve=True)
    runtime = GenerationRuntime(harness.adapter, (RouteChoice("primary", "model-a"),), harness.handle)
    _plan, meter, briefs = two_speakers(harness, controls, voices)
    outcome = runtime.run_turn(meter, briefs, controls=controls)
    assert outcome.status == "halted" and outcome.calls[0].status == "refused"
    assert harness.transport.sends == []
