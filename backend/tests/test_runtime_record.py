"""027 -> story-quality /3: a finished runtime turn recorded as a gateable v2-runtime run.

Every provider call is a scripted fake; no paid model is called. The calls in a recorded run are
the turn plan's calls, so a scenario-only turn shows one director reply and no NPC call, and a
two-NPC turn two NPC calls and no director call, even when its policy would allow one.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from generation_fixtures import BELLAMY, DIRECTOR_POLICY, ODA, Harness, brief, scene, world
from voice_fixtures import PC

from evals_helpers import FORMAT_V3_NAME, mutate
from grimoire.core.inference import InferencePolicy, InferenceStage, TurnUsageLedger
from grimoire.evals.format import BenchmarkFormatError, parse_scenario
from grimoire.evals.format3 import RunV3, parse_run_v3
from grimoire.evals.gate2 import compare_v2
from grimoire.evals.loader import load_any_run
from grimoire.evals.runtime_record import (
    RUNTIME_HARNESS, RecordedLine, RecordedTurn, RuntimeRecordError, record_runtime_run,
)
from grimoire.evals.story_quality import main
from grimoire.generation import GenerationError
from grimoire.providers.meter import TurnMeter
from grimoire.runtime import recorded_turn
from grimoire.testing.providers import Reply

PRESENT = ("npc-oda", "npc-bellamy", "npc-quill", PC)
ODA_LINE, BELLAMY_LINE = "Coin first. Then talk.", "Oh darling, how splendidly grim!"
RAIN = "Rain drums on the shutters as the hall falls quiet."
COIN, LISTEN = "I put a coin on the counter.", "I listen to the rain."
CONDITIONS = {"model": "model-a", "budget": "max_output_tokens=64"}


def say(text: str) -> Reply:
    # Cache reads reported as 0, so the gate can weigh cost instead of calling it incomplete.
    return Reply(deltas=(text,), usage={"prompt_tokens": 20, "completion_tokens": 3,
                                        "prompt_tokens_details": {"cached_tokens": 0}})


def scenario(*, cast: bool = True) -> dict:
    actors = [{"actor_id": PC, "name": "Wren", "control": "player", "voice_samples": []}]
    choices = [{"choice_id": "listen", "turn": 2 if cast else 1, "actor_id": PC, "text": LISTEN,
                "effect_markers": ["rain"]}]
    script = [{"turn": 2 if cast else 1, "speakers": []}]
    if cast:
        actors += [
            {"actor_id": "npc-oda", "name": "Oda", "control": "director",
             "voice_samples": ["Ledger's shut. Tally after dawn."]},
            {"actor_id": "npc-bellamy", "name": "Bellamy", "control": "director",
             "voice_samples": ["Oh, my dear, what a delicious little scandal!"]},
        ]
        choices.insert(0, {"choice_id": "coin", "turn": 1, "actor_id": PC, "text": COIN,
                           "effect_markers": ["coin"]})
        script.insert(0, {"turn": 1, "speakers": ["npc-oda", "npc-bellamy"]})
    return {"format": "grimoire.story-quality/1", "scenario_id": "hall", "actors": actors,
            "facts": [], "established_facts": [], "choices": choices, "script": script,
            "pc_authorship_markers": ["wren feels"]}


def two_npc_turn(controls, voices, *, policy=DIRECTOR_POLICY, number=1):
    harness = Harness([say(ODA_LINE), say(BELLAMY_LINE)])
    plan = harness.plan(controls, present=PRESENT, speakers=("npc-oda", "npc-bellamy"), policy=policy)
    meter = harness.meter(plan)
    outcome = harness.runtime.run_turn(meter, [brief(ODA, "Oda", voices), brief(BELLAMY, "Bellamy", voices)],
                                       controls=controls)
    assert outcome.status == "complete"
    return recorded_turn(number, meter, outcome, player_lines=[(PC, COIN)])


def scene_only_turn(controls, *, number=1, turn_id="t1"):
    harness = Harness([say(RAIN)])
    plan = harness.plan(controls, present=(PC,), speakers=(), director_reply=True, policy=DIRECTOR_POLICY,
                        turn=turn_id)
    meter = harness.meter(plan)
    outcome = harness.runtime.run_turn(meter, [], controls=controls, director=scene("A flooded hall.", controls))
    assert outcome.status == "complete"
    return recorded_turn(number, meter, outcome, player_lines=[(PC, LISTEN)])


def record(raw_scenario, turns, **kw):
    return record_runtime_run(parse_scenario(raw_scenario), turns, run_id="rec", label="recorded turn",
                              conditions=CONDITIONS, **kw)


def stages(run: RunV3) -> list[tuple[str, str | None]]:
    return [(call.stage.value, call.actor_id) for call in run.calls]


def test_a_scenario_only_turn_records_one_director_reply_and_no_npc_call():
    controls, _voices = world()
    raw_scenario = scenario(cast=False)
    document = record(raw_scenario, [scene_only_turn(controls)])
    run = parse_run_v3(document, parse_scenario(raw_scenario))
    assert document["format"] == FORMAT_V3_NAME
    assert document["source"] == {"kind": "v2-runtime", "harness": RUNTIME_HARNESS, "route": None}
    assert stages(run) == [("director_generation", None)]
    (player, narration) = run.turns[0].contributions
    assert (player.author, player.text) == ("player", LISTEN)
    assert (narration.speaker_id, narration.text, narration.call_id) == (None, RAIN, run.calls[0].call_id)
    assert run.usage.baseline.attempts == 0 and run.usage.optional.attempts == 1
    assert run.calls[0].latency_ms is None  # the meter times attempts, not the whole call
    report = compare_v2(parse_scenario(raw_scenario), run, run)
    assert report.passed, report.findings


def test_a_two_npc_turn_records_two_npc_calls_and_no_director_call():
    controls, voices = world()
    raw_scenario = mutate(scenario(), lambda s: (s["choices"].pop(), s["script"].pop()))
    document = record(raw_scenario, [two_npc_turn(controls, voices)])
    run = parse_run_v3(document, parse_scenario(raw_scenario))
    # The policy enables a director reply, but none was requested, so none was planned or paid.
    assert run.enabled_stages == ("director_generation",)
    assert stages(run) == [("npc_generation", "npc-oda"), ("npc_generation", "npc-bellamy")]
    assert InferenceStage.DIRECTOR_GENERATION not in run.usage.by_stage
    assert [(c.speaker_id, c.text) for c in run.turns[0].contributions[1:]] == [
        ("npc-oda", ODA_LINE), ("npc-bellamy", BELLAMY_LINE)]
    assert all(each.route for call in run.calls for each in call.attempts)
    report = compare_v2(parse_scenario(raw_scenario), run, run)
    assert report.passed, report.findings


def test_a_recorded_run_round_trips_through_its_file_and_the_cli(tmp_path, capsys):
    controls, voices = world()
    raw_scenario = scenario()
    document = record(raw_scenario, [two_npc_turn(controls, voices),
                                     scene_only_turn(controls, number=2, turn_id="t2")])
    scenario_path, run_path = tmp_path / "scenario.json", tmp_path / "run.json"
    scenario_path.write_text(json.dumps(raw_scenario), "utf-8")
    run_path.write_text(json.dumps(document), "utf-8")
    parsed = parse_scenario(raw_scenario)
    loaded = load_any_run(run_path, parsed)
    assert isinstance(loaded, RunV3) and loaded == parse_run_v3(document, parsed)
    assert stages(loaded) == [("npc_generation", "npc-oda"), ("npc_generation", "npc-bellamy"),
                              ("director_generation", None)]
    assert main([str(scenario_path), str(run_path), str(run_path)]) == 0
    assert "narration lines" in capsys.readouterr().out


def test_an_implicit_director_reply_cannot_be_added_to_a_recorded_npc_turn():
    controls, voices = world()
    raw_scenario = mutate(scenario(), lambda s: (s["choices"].pop(), s["script"].pop()))
    plain = InferencePolicy()  # what a world-based turn runs with: no director reply enabled
    document = record(raw_scenario, [two_npc_turn(controls, voices, policy=plain)])
    assert document["policy"]["per_turn_call_caps"] == {}

    def narrate(r):
        r["calls"].append({"call_id": "extra", "turn": 1, "stage": "director_generation",
                           "actor_id": None, "purpose": "director reply", "latency_ms": None,
                           "attempts": [dict(r["calls"][0]["attempts"][0], route=None)]})
        r["turns"][0]["contributions"].append(
            {"speaker_id": None, "author": "model", "text": RAIN, "call_id": "extra"})
    with pytest.raises(BenchmarkFormatError, match=r"^run\.calls\[2\]\.stage: .*disabled"):
        parse_run_v3(mutate(document, narrate), parse_scenario(raw_scenario))


def test_a_halted_turn_is_recorded_as_billed_and_gated_as_failed():
    controls, voices = world()
    raw_scenario = mutate(scenario(), lambda s: (s["choices"].pop(), s["script"].pop()))
    harness = Harness([say("Word is, the duke's betrayal came cheap."), say(BELLAMY_LINE)])
    plan = harness.plan(controls, present=PRESENT, speakers=("npc-oda", "npc-bellamy"))
    meter = harness.meter(plan)
    outcome = harness.runtime.run_turn(meter, [brief(ODA, "Oda", voices), brief(BELLAMY, "Bellamy", voices)],
                                       controls=controls)
    assert outcome.status == "halted"
    document = record(raw_scenario, [recorded_turn(1, meter, outcome, player_lines=[(PC, COIN)])])
    run = parse_run_v3(document, parse_scenario(raw_scenario))
    rejected, unsent = run.calls
    assert [each.outcome for each in rejected.attempts] == ["completed"] and unsent.attempts == ()
    report = compare_v2(parse_scenario(raw_scenario), run, run)
    found = {finding.check for finding in report.findings}
    assert {"orphan_generation", "script_incomplete", "usage_incomplete"} <= found


def test_turns_planned_under_different_policies_are_not_one_run():
    controls, voices = world()
    with pytest.raises(RuntimeRecordError) as caught:
        record(scenario(), [two_npc_turn(controls, voices, policy=InferencePolicy()),
                            scene_only_turn(controls, number=2, turn_id="t2")])
    assert caught.value.code == "policy_mismatch"


def test_an_outcome_from_another_turn_is_refused():
    controls, voices = world()
    harness = Harness([say(ODA_LINE), say(BELLAMY_LINE)])
    first = harness.plan(controls, present=PRESENT, speakers=("npc-oda", "npc-bellamy"))
    meter = harness.meter(first)
    outcome = harness.runtime.run_turn(meter, [brief(ODA, "Oda", voices), brief(BELLAMY, "Bellamy", voices)],
                                       controls=controls)
    other = harness.meter(harness.plan(controls, present=PRESENT, speakers=("npc-oda", "npc-bellamy"), turn="t2"))
    with pytest.raises(GenerationError) as caught:
        recorded_turn(1, other, outcome)
    assert caught.value.reason == "outcome_mismatch"
    with pytest.raises(GenerationError) as caught:  # an accepted call's line went missing
        recorded_turn(1, meter, replace(outcome, lines=outcome.lines[:1]))
    assert caught.value.reason == "outcome_mismatch"


def test_the_recorder_refuses_a_line_its_plan_did_not_produce():
    controls, voices = world()
    npc_turn = two_npc_turn(controls, voices)
    director = scene_only_turn(controls).lines[0]
    with pytest.raises(RuntimeRecordError) as caught:
        record(scenario(), [RecordedTurn(1, npc_turn.ledger, (director,), npc_turn.player_lines)])
    assert caught.value.code == "unplanned_line"
    oda = npc_turn.lines[0]
    for lines, code in (((RecordedLine(oda.call_id, None, oda.text),), "speaker_mismatch"),
                        ((oda, oda), "duplicate_line")):
        with pytest.raises(RuntimeRecordError) as caught:
            record(scenario(), [RecordedTurn(1, npc_turn.ledger, lines, npc_turn.player_lines)])
        assert caught.value.code == code


def test_a_meter_whose_booking_failed_is_not_recorded():
    controls, voices = world()
    harness = Harness([say(ODA_LINE), say(BELLAMY_LINE)])
    plan = harness.plan(controls, present=PRESENT, speakers=("npc-oda", "npc-bellamy"))
    meter = TurnMeter(TurnUsageLedger(plan), _fail_booking=lambda stage: True)
    outcome = harness.runtime.run_turn(meter, [brief(ODA, "Oda", voices), brief(BELLAMY, "Bellamy", voices)],
                                       controls=controls)
    assert meter.poisoned_call_ids  # a sent attempt may be missing from the ledger
    with pytest.raises(GenerationError) as caught:
        recorded_turn(1, meter, outcome)
    assert caught.value.reason == "poisoned_meter"
