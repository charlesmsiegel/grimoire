"""Synthetic story-quality fixtures shared by the evals tests."""

from __future__ import annotations

import copy
from collections.abc import Callable

from grimoire.evals.format import FORMAT, Run, Scenario, parse_run, parse_scenario
from grimoire.evals.loader import benchmark_file, loads_strict


def minimal_scenario() -> dict:
    return {
        "format": FORMAT,
        "scenario_id": "mini",
        "actors": [
            {"actor_id": "pc", "name": "Pat", "control": "player", "voice_samples": []},
            {"actor_id": "npc-a", "name": "Ana", "control": "director",
             "voice_samples": ["Plain words only."]},
        ],
        "facts": [{"fact_id": "secret", "markers": ["hidden key"], "known_from": {"npc-a": 2}}],
        "established_facts": [
            {"fact_id": "door", "from_turn": 1, "contradiction_markers": ["door is open"]},
        ],
        "choices": [{
            "choice_id": "c1", "turn": 1, "actor_id": "pc", "text": "I knock.",
            "effect_markers": ["knock"], "negation_markers": [], "window": 0,
        }],
        "script": [{"turn": 1, "speakers": ["npc-a"]}],
        "pc_authorship_markers": ["pat feels"],
    }


def minimal_run() -> dict:
    return {
        "format": FORMAT,
        "run_id": "r",
        "scenario_id": "mini",
        "label": "minimal",
        "provenance": "synthetic",
        "conditions": {"model": "m", "budget": "b"},
        "enabled_flags": [],
        "turns": [{"turn": 1, "contributions": [
            {"speaker_id": "pc", "author": "player", "text": "I knock.", "call_id": None},
            {"speaker_id": "npc-a", "author": "model", "text": "You knock. Enter.", "call_id": "k1"},
        ]}],
        "calls": [{
            "call_id": "k1", "turn": 1, "purpose": "npc_generation", "actor_id": "npc-a",
            "feature_flag": None, "input_tokens": 10, "cached_input_tokens": 0,
            "output_tokens": 5, "latency_ms": 100,
        }],
        "choice_outcomes": {"c1": "honored"},
    }


def load_benchmark(name: str = "guild_betrayal") -> tuple[dict, dict]:
    """Raw shipped benchmark dicts, read through the strict JSON loader."""
    scenario = loads_strict(benchmark_file(name, "scenario.json").read_text("utf-8"), "scenario")
    baseline = loads_strict(benchmark_file(name, "baseline.json").read_text("utf-8"), "baseline")
    return scenario, baseline


def mutate(data: dict, change: Callable[[dict], object]) -> dict:
    changed = copy.deepcopy(data)
    change(changed)
    return changed


def parsed(scenario: dict, run: dict) -> tuple[Scenario, Run]:
    parsed_scenario = parse_scenario(scenario)
    return parsed_scenario, parse_run(run, parsed_scenario)


def contribution(run: dict, turn: int, speaker_id: str | None) -> dict:
    """The first contribution by `speaker_id` (None = narration) on `turn`."""
    for item in run["turns"]:
        if item["turn"] == turn:
            for entry in item["contributions"]:
                if entry["speaker_id"] == speaker_id:
                    return entry
    raise KeyError((turn, speaker_id))


def call(run: dict, call_id: str) -> dict:
    return next(entry for entry in run["calls"] if entry["call_id"] == call_id)


def checks(findings) -> set[str]:
    return {finding.check for finding in getattr(findings, "findings", findings)}
