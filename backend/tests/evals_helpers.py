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


# --- grimoire.story-quality/2 ------------------------------------------------------------------

FORMAT_V2_NAME = "grimoire.story-quality/2"


def attempt_v2(input_tokens=100, output_tokens=5, *, cache_read=0, attempt=1,
               outcome="completed", measurement="synthetic", cost=None) -> dict:
    entry = {
        "attempt": attempt, "outcome": outcome, "measurement": measurement,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cache_read_tokens": cache_read, "cache_write_tokens": None,
                  "reasoning_tokens": None},
    }
    if cost is not None:
        entry["cost"] = cost
    return entry


def minimal_run_v2() -> dict:
    """``minimal_run`` in the /2 shape: one NPC, one completed synthetic attempt."""
    return {
        "format": FORMAT_V2_NAME,
        "run_id": "r2",
        "scenario_id": "mini",
        "label": "minimal /2",
        "source": {"kind": "synthetic"},
        "conditions": {"model": "m", "budget": "b"},
        "policy": {"per_turn_call_caps": {}, "max_attempts_per_call": 1},
        "turns": [{"turn": 1, "contributions": [
            {"speaker_id": "pc", "author": "player", "text": "I knock."},
            {"speaker_id": "npc-a", "author": "model", "text": "You knock. Enter.",
             "call_id": "k1"},
        ]}],
        "calls": [{
            "call_id": "k1", "turn": 1, "stage": "npc_generation", "actor_id": "npc-a",
            "purpose": "speaking NPC contribution", "latency_ms": 100,
            "attempts": [attempt_v2()],
        }],
        "choice_outcomes": {"c1": "honored"},
    }


def live_source(**overrides) -> dict:
    source = {"kind": "v0.2.1-capture", "mode": "live",
              "v1_commit": "e4c4ec4abacd0beb87fc9f1cdd06e6787d5a88ce",
              "harness": "grimoire.capture/1", "evidence_sha256": "0" * 64,
              "profile": "two-call", "llm_retries": 0, "route": "openrouter:vendor-model"}
    source.update(overrides)
    return source


def v2_from_v1(run: dict) -> dict:
    """A /1 run as /2: narration dropped (no /2 narration stage), one attempt per call."""
    narration = {c["call_id"] for c in run["calls"] if c["purpose"] == "narration"}
    calls = []
    for c in run["calls"]:
        if c["call_id"] in narration:
            continue
        calls.append({
            "call_id": c["call_id"], "turn": c["turn"], "stage": c["purpose"],
            "actor_id": c["actor_id"], "purpose": "speaking NPC contribution",
            "latency_ms": c["latency_ms"],
            "attempts": [attempt_v2(c["input_tokens"], c["output_tokens"],
                                    cache_read=c["cached_input_tokens"])],
        })
    for entry in calls:
        entry["attempts"][0]["usage"]["cache_write_tokens"] = 0
    return {
        "format": FORMAT_V2_NAME,
        "run_id": run["run_id"].replace("synthetic", "synthetic-v2"),
        "scenario_id": run["scenario_id"],
        "label": "Synthetic V0.2.1-shaped baseline in /2: one RP call per speaking NPC",
        "source": {"kind": "synthetic"},
        "conditions": dict(run["conditions"]),
        "policy": {"per_turn_call_caps": {}, "max_attempts_per_call": 1},
        "turns": [{"turn": t["turn"], "contributions": [
            {k: v for k, v in entry.items() if not (k == "call_id" and v is None)}
            for entry in t["contributions"] if entry.get("call_id") not in narration]}
            for t in run["turns"]],
        "calls": calls,
        "choice_outcomes": dict(run["choice_outcomes"]),
        "judge_scores": dict(run.get("judge_scores", {})),
    }


def guild_run_v2() -> tuple[dict, dict]:
    """(scenario, /2 run) for the shipped guild_betrayal benchmark."""
    scenario, baseline = load_benchmark()
    return scenario, v2_from_v1(baseline)


# --- 0.2.1 capture evidence --------------------------------------------------------------------

V021_COMMIT = "e4c4ec4abacd0beb87fc9f1cdd06e6787d5a88ce"


def v021_row(response_id: str, prompt=100, completion=5, *, cache_read=0, **extra) -> dict:
    row = {"ts": "2026-10-31T00:00:00Z", "kind": "llm", "task": "chat", "campaign": "c-1",
           "scene": "s-1", "model": "vendor-model", "provider": "openrouter",
           "response_id": response_id, "prompt_tokens": prompt, "completion_tokens": completion,
           "cache_read_tokens": cache_read, "duration_ms": 50, "status": "ok"}
    row.update(extra)
    return row


def bundle(**overrides) -> dict:
    """A one-turn synthetic 0.2.1 evidence bundle for ``minimal_scenario``."""
    data = {
        "format": "grimoire.v021-capture-evidence/1",
        "scenario_id": "mini", "mode": "dry_run", "provider_kind": "openrouter",
        "v1_commit": V021_COMMIT, "profile": "two-call", "llm_retries": 2,
        "model": "vendor-model", "budget": "max_tokens=400",
        "campaign": "c-1", "scene": "s-1",
        "actor_map": {"characters:ana": "npc-a", "pcs:pat": "pc"},
        "choice_outcomes": {"c1": "honored"},
        "turns": [{"turn": 1, "player_text": "I knock.", "responses": [
            {"response_id": "r-1", "speaker_ref": "characters:ana", "text": "You knock. Enter."}]}],
        "usage_rows": [v021_row("r-1")],
    }
    data.update(overrides)
    return data
