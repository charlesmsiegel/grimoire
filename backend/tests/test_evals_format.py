"""Strict parsing of the eval-only story-quality interchange format."""

import math

import pytest

from evals_helpers import minimal_run, minimal_scenario, mutate, parsed
from grimoire.evals.format import BenchmarkFormatError, parse_run, parse_scenario
from grimoire.evals.loader import loads_strict


def test_minimal_documents_parse_into_immutable_values():
    scenario, run = parsed(minimal_scenario(), minimal_run())
    assert scenario.director_ids() == ("npc-a",)
    assert scenario.is_player("pc") and not scenario.is_player("npc-a")
    assert scenario.choices[0].window == 0
    assert run.turns[0].contributions[1].call_id == "k1"
    assert run.conditions["model"] == "m"
    with pytest.raises(TypeError):
        run.conditions["model"] = "other"  # type: ignore[index]


def _set(path, value):
    def change(data):
        target = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return change


def _delete(path):
    def change(data):
        target = data
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
    return change


def _append(path, value):
    def change(data):
        target = data
        for key in path:
            target = target[key]
        target.append(value)
    return change


SCENARIO_ERRORS = [
    ("bad format", _set(["format"], "grimoire.story-quality/0"), "scenario.format"),
    ("unknown key", _set(["extra"], 1), "scenario: unknown key"),
    ("missing key", _delete(["choices"]), "scenario: missing key"),
    ("empty id", _set(["scenario_id"], " "), "scenario.scenario_id"),
    ("duplicate actor", _append(["actors"], {"actor_id": "pc", "name": "P2",
                                             "control": "player", "voice_samples": []}),
     "scenario.actors[2].actor_id"),
    ("bad control", _set(["actors", 0, "control"], "npc"), "scenario.actors[0].control"),
    ("director without samples", _set(["actors", 1, "voice_samples"], []),
     "scenario.actors[1].voice_samples"),
    ("duplicate fact", _append(["facts"], {"fact_id": "secret", "markers": ["x y"],
                                          "known_from": {}}), "scenario.facts[1].fact_id"),
    ("empty markers", _set(["facts", 0, "markers"], []), "scenario.facts[0].markers"),
    ("blank marker", _set(["facts", 0, "markers"], [" "]), "scenario.facts[0].markers[0]"),
    ("punctuation marker", _set(["facts", 0, "markers"], ["..."]),
     "scenario.facts[0].markers[0]"),
    ("known_from unknown actor", _set(["facts", 0, "known_from"], {"ghost": 1}),
     "scenario.facts[0].known_from.ghost"),
    ("known_from turn zero", _set(["facts", 0, "known_from"], {"npc-a": 0}),
     "scenario.facts[0].known_from.npc-a"),
    ("bool turn", _set(["facts", 0, "known_from"], {"npc-a": True}),
     "scenario.facts[0].known_from.npc-a"),
    ("float turn", _set(["established_facts", 0, "from_turn"], 1.0),
     "scenario.established_facts[0].from_turn"),
    ("empty contradiction markers", _set(["established_facts", 0, "contradiction_markers"], []),
     "scenario.established_facts[0].contradiction_markers"),
    ("duplicate choice", _append(["choices"], {"choice_id": "c1", "turn": 1, "actor_id": "pc",
                                              "text": "Again.", "effect_markers": ["again"]}),
     "scenario.choices[1].choice_id"),
    ("choice by director", _set(["choices", 0, "actor_id"], "npc-a"),
     "scenario.choices[0].actor_id"),
    ("choice by unknown actor", _set(["choices", 0, "actor_id"], "ghost"),
     "scenario.choices[0].actor_id"),
    ("choice on unscripted turn", _set(["choices", 0, "turn"], 2), "scenario.choices[0].turn"),
    ("negative window", _set(["choices", 0, "window"], -1), "scenario.choices[0].window"),
    ("empty effect markers", _set(["choices", 0, "effect_markers"], []),
     "scenario.choices[0].effect_markers"),
    ("script turn zero", _set(["script", 0, "turn"], 0), "scenario.script[0].turn"),
    ("non-increasing script", _append(["script"], {"turn": 1, "speakers": []}),
     "scenario.script[1].turn"),
    ("player script speaker", _set(["script", 0, "speakers"], ["pc"]),
     "scenario.script[0].speakers[0]"),
    ("unknown script speaker", _set(["script", 0, "speakers"], ["ghost"]),
     "scenario.script[0].speakers[0]"),
    ("blank authorship marker", _set(["pc_authorship_markers"], [""]),
     "scenario.pc_authorship_markers[0]"),
    ("not an object", lambda data: data.clear(), "scenario: missing key"),
]


@pytest.mark.parametrize(("name", "change", "location"), SCENARIO_ERRORS,
                         ids=[row[0] for row in SCENARIO_ERRORS])
def test_scenario_format_errors_are_located(name, change, location):
    with pytest.raises(BenchmarkFormatError) as error:
        parse_scenario(mutate(minimal_scenario(), change))
    assert location in str(error.value)


def test_scenario_must_be_an_object():
    with pytest.raises(BenchmarkFormatError, match="scenario: expected an object"):
        parse_scenario([])


def _contribution(index, key, value):
    return _set(["turns", 0, "contributions", index, key], value)


RUN_ERRORS = [
    ("bad format", _set(["format"], "other"), "run.format"),
    ("unknown key", _set(["score"], 1), "run: unknown key"),
    ("scenario mismatch", _set(["scenario_id"], "other"), "run.scenario_id"),
    ("bad provenance", _set(["provenance"], "guessed"), "run.provenance"),
    ("missing model condition", _set(["conditions"], {"budget": "b"}), "run.conditions.model"),
    ("missing budget condition", _set(["conditions"], {"model": "m"}), "run.conditions.budget"),
    ("non-string condition", _set(["conditions", "budget"], 5), "run.conditions.budget"),
    ("blank flag", _set(["enabled_flags"], [""]), "run.enabled_flags[0]"),
    ("turn zero", _set(["turns", 0, "turn"], 0), "run.turns[0].turn"),
    ("non-increasing turns", _append(["turns"], {"turn": 1, "contributions": []}),
     "run.turns[1].turn"),
    ("bad author", _contribution(1, "author", "director"), "run.turns[0].contributions[1].author"),
    ("model without call", _contribution(1, "call_id", None),
     "run.turns[0].contributions[1].call_id"),
    ("unknown call", _contribution(1, "call_id", "nope"),
     "run.turns[0].contributions[1].call_id"),
    ("player with call", _contribution(0, "call_id", "k1"),
     "run.turns[0].contributions[0].call_id"),
    ("player narration", _contribution(0, "speaker_id", None),
     "run.turns[0].contributions[0].speaker_id"),
    ("player for director", _contribution(0, "speaker_id", "npc-a"),
     "run.turns[0].contributions[0].speaker_id"),
    ("unknown speaker", _contribution(1, "speaker_id", "ghost"),
     "run.turns[0].contributions[1].speaker_id"),
    ("duplicate call", _append(["calls"], {
        "call_id": "k1", "turn": 1, "purpose": "narration", "input_tokens": 1,
        "cached_input_tokens": 0, "output_tokens": 1, "latency_ms": 1}), "run.calls[1].call_id"),
    ("call in another turn", _set(["calls", 0, "turn"], 2),
     "run.turns[0].contributions[1].call_id"),
    ("negative tokens", _set(["calls", 0, "input_tokens"], -1), "run.calls[0].input_tokens"),
    ("cached above input", _set(["calls", 0, "cached_input_tokens"], 11),
     "run.calls[0].cached_input_tokens"),
    ("bool tokens", _set(["calls", 0, "output_tokens"], False), "run.calls[0].output_tokens"),
    ("float latency", _set(["calls", 0, "latency_ms"], 1.5), "run.calls[0].latency_ms"),
    ("npc call without actor", _set(["calls", 0, "actor_id"], None), "run.calls[0].actor_id"),
    ("npc call for player", _set(["calls", 0, "actor_id"], "pc"), "run.calls[0].actor_id"),
    ("actor on narration call", _set(["calls", 0, "purpose"], "narration"),
     "run.calls[0].actor_id"),
    ("missing outcome", _set(["choice_outcomes"], {}), "run.choice_outcomes.c1"),
    ("unknown outcome value", _set(["choice_outcomes", "c1"], "sort of"),
     "run.choice_outcomes.c1"),
    ("outcome for unknown choice", _set(["choice_outcomes", "c9"], "honored"),
     "run.choice_outcomes.c9"),
    ("NaN judge score", _set(["judge_scores"], {"fluency": math.nan}), "run.judge_scores.fluency"),
    ("infinite judge score", _set(["judge_scores"], {"fluency": math.inf}),
     "run.judge_scores.fluency"),
    ("bool judge score", _set(["judge_scores"], {"fluency": True}), "run.judge_scores.fluency"),
]


@pytest.mark.parametrize(("name", "change", "location"), RUN_ERRORS,
                         ids=[row[0] for row in RUN_ERRORS])
def test_run_format_errors_are_located(name, change, location):
    scenario = parse_scenario(minimal_scenario())
    with pytest.raises(BenchmarkFormatError) as error:
        parse_run(mutate(minimal_run(), change), scenario)
    assert location in str(error.value)


def test_altered_player_text_is_a_candidate_defect_not_a_format_error():
    run = mutate(minimal_run(), _contribution(0, "text", "I kick the door down."))
    scenario, parsed_run = parsed(minimal_scenario(), run)
    assert parsed_run.turns[0].contributions[0].text == "I kick the door down."


@pytest.mark.parametrize("text", [
    '{"a": 1, "a": 2}',
    '{"a": NaN}',
    '{"a": Infinity}',
    '{"a": -Infinity}',
    '{"a": ',
])
def test_strict_json_rejects_ambiguous_documents(text):
    with pytest.raises(BenchmarkFormatError, match="sample.json"):
        loads_strict(text, "sample.json")
