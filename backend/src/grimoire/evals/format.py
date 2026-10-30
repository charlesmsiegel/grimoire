"""Eval-only story-quality interchange format (``grimoire.story-quality/1``).

This is a benchmark recording format, not V2 library storage. Parsing is strict and fails closed:
unknown keys, wrong types, non-finite numbers and dangling references raise
:class:`BenchmarkFormatError` with a location such as ``run.turns[0].contributions[1]``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from .text import has_words

FORMAT = "grimoire.story-quality/1"
CONTROLS = ("player", "director")
AUTHORS = ("player", "model")
PROVENANCES = ("synthetic", "measured")
OUTCOMES = ("honored", "complicated", "negated")
REQUIRED_CONDITIONS = ("model", "budget")
NPC_GENERATION = "npc_generation"
NARRATION = "narration"


class BenchmarkFormatError(ValueError):
    """A scenario or run cannot be scored because it is malformed."""


@dataclass(frozen=True, slots=True)
class Actor:
    actor_id: str
    name: str
    control: Literal["player", "director"]
    voice_samples: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Fact:
    """Secret knowledge: an actor may state it only from its ``known_from`` turn on."""

    fact_id: str
    markers: tuple[str, ...]
    known_from: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class EstablishedFact:
    """Continuity: from ``from_turn`` on, no model text may contradict it."""

    fact_id: str
    from_turn: int
    contradiction_markers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Choice:
    """A scripted player input that model text must give effect to."""

    choice_id: str
    turn: int
    actor_id: str
    text: str
    effect_markers: tuple[str, ...]
    negation_markers: tuple[str, ...]
    window: int


@dataclass(frozen=True, slots=True)
class ScriptTurn:
    turn: int
    speakers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    actors: tuple[Actor, ...]
    facts: tuple[Fact, ...]
    established_facts: tuple[EstablishedFact, ...]
    choices: tuple[Choice, ...]
    script: tuple[ScriptTurn, ...]
    pc_authorship_markers: tuple[str, ...]

    def actor(self, actor_id: str) -> Actor:
        for actor in self.actors:
            if actor.actor_id == actor_id:
                return actor
        raise KeyError(actor_id)

    def is_player(self, actor_id: str | None) -> bool:
        return actor_id is not None and any(
            actor.actor_id == actor_id and actor.control == "player" for actor in self.actors)

    def director_ids(self) -> tuple[str, ...]:
        return tuple(actor.actor_id for actor in self.actors if actor.control == "director")


@dataclass(frozen=True, slots=True)
class Contribution:
    speaker_id: str | None
    author: Literal["player", "model"]
    text: str
    call_id: str | None


@dataclass(frozen=True, slots=True)
class Turn:
    turn: int
    contributions: tuple[Contribution, ...]


@dataclass(frozen=True, slots=True)
class Call:
    call_id: str
    turn: int
    purpose: str
    actor_id: str | None
    feature_flag: str | None
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    latency_ms: int


@dataclass(frozen=True, slots=True)
class Run:
    run_id: str
    scenario_id: str
    label: str
    provenance: Literal["synthetic", "measured"]
    conditions: Mapping[str, str]
    enabled_flags: tuple[str, ...]
    turns: tuple[Turn, ...]
    calls: tuple[Call, ...]
    choice_outcomes: Mapping[str, str]
    judge_scores: Mapping[str, float]


# --- primitive readers -------------------------------------------------------------------------

def _fail(location: str, message: str) -> BenchmarkFormatError:
    return BenchmarkFormatError(f"{location}: {message}")


def _obj(data: object, location: str, required: tuple[str, ...],
         optional: Mapping[str, object] | None = None) -> dict:
    """Return ``data`` with optional defaults filled, rejecting missing or unknown keys."""
    optional = optional or {}
    if not isinstance(data, dict):
        raise _fail(location, "expected an object")
    unknown = sorted(set(data) - set(required) - set(optional))
    if unknown:
        raise _fail(location, f"unknown key {unknown[0]!r}")
    missing = [key for key in required if key not in data]
    if missing:
        raise _fail(location, f"missing key {missing[0]!r}")
    return {**optional, **data}


def _str(value: object, location: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise _fail(location, "expected a nonempty string")
    return value


def _opt_str(value: object, location: str) -> str | None:
    return None if value is None else _str(value, location)


def _int(value: object, location: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(location, "expected an integer")
    if value < minimum:
        raise _fail(location, f"must be >= {minimum}")
    return value


def _number(value: object, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _fail(location, "expected a finite number")
    return float(value)


def _choice(value: object, location: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise _fail(location, f"expected one of {', '.join(allowed)}")
    return value  # type: ignore[return-value]


def _list(value: object, location: str) -> list:
    if not isinstance(value, list):
        raise _fail(location, "expected a list")
    return value


def _strings(value: object, location: str) -> tuple[str, ...]:
    return tuple(_str(item, f"{location}[{index}]")
                 for index, item in enumerate(_list(value, location)))


def _markers(value: object, location: str, *, required: bool) -> tuple[str, ...]:
    markers = _list(value, location)
    if required and not markers:
        raise _fail(location, "needs at least one marker")
    result = []
    for index, marker in enumerate(markers):
        marker = _str(marker, f"{location}[{index}]")
        if not has_words(marker):
            raise _fail(f"{location}[{index}]", "marker has no word characters to match")
        result.append(marker)
    return tuple(result)


def _unique(seen: set[str], value: str, location: str) -> str:
    if value in seen:
        raise _fail(location, f"duplicate id {value!r}")
    seen.add(value)
    return value


def _frozen(mapping: dict) -> Mapping:
    return MappingProxyType(dict(mapping))


# --- scenario -----------------------------------------------------------------------------------

def parse_scenario(data: object) -> Scenario:
    loc = "scenario"
    data = _obj(data, loc, ("format", "scenario_id", "actors", "facts", "established_facts",
                            "choices", "script", "pc_authorship_markers"))
    if data["format"] != FORMAT:
        raise _fail(f"{loc}.format", f"expected {FORMAT!r}")
    scenario_id = _str(data["scenario_id"], f"{loc}.scenario_id")

    actors, actor_ids = [], set()
    for index, raw in enumerate(_list(data["actors"], f"{loc}.actors")):
        at = f"{loc}.actors[{index}]"
        raw = _obj(raw, at, ("actor_id", "name", "control", "voice_samples"))
        actor = Actor(
            _unique(actor_ids, _str(raw["actor_id"], f"{at}.actor_id"), f"{at}.actor_id"),
            _str(raw["name"], f"{at}.name"),
            _choice(raw["control"], f"{at}.control", CONTROLS),  # type: ignore[arg-type]
            _strings(raw["voice_samples"], f"{at}.voice_samples"),
        )
        if actor.control == "director" and not actor.voice_samples:
            raise _fail(f"{at}.voice_samples", "a director actor needs voice samples")
        actors.append(actor)
    controls = {actor.actor_id: actor.control for actor in actors}

    def actor_ref(value: object, at: str, control: str | None = None) -> str:
        actor_id = _str(value, at)
        if actor_id not in controls:
            raise _fail(at, f"unknown actor {actor_id!r}")
        if control is not None and controls[actor_id] != control:
            raise _fail(at, f"actor {actor_id!r} is not {control}-controlled")
        return actor_id

    facts, fact_ids = [], set()
    for index, raw in enumerate(_list(data["facts"], f"{loc}.facts")):
        at = f"{loc}.facts[{index}]"
        raw = _obj(raw, at, ("fact_id", "markers", "known_from"))
        known = raw["known_from"]
        if not isinstance(known, dict):
            raise _fail(f"{at}.known_from", "expected an object")
        known_from = {actor_ref(actor_id, f"{at}.known_from.{actor_id}"):
                      _int(turn, f"{at}.known_from.{actor_id}", 1)
                      for actor_id, turn in known.items()}
        facts.append(Fact(
            _unique(fact_ids, _str(raw["fact_id"], f"{at}.fact_id"), f"{at}.fact_id"),
            _markers(raw["markers"], f"{at}.markers", required=True),
            _frozen(known_from),
        ))

    established, established_ids = [], set()
    for index, raw in enumerate(_list(data["established_facts"], f"{loc}.established_facts")):
        at = f"{loc}.established_facts[{index}]"
        raw = _obj(raw, at, ("fact_id", "from_turn", "contradiction_markers"))
        established.append(EstablishedFact(
            _unique(established_ids, _str(raw["fact_id"], f"{at}.fact_id"), f"{at}.fact_id"),
            _int(raw["from_turn"], f"{at}.from_turn", 1),
            _markers(raw["contradiction_markers"], f"{at}.contradiction_markers", required=True),
        ))

    script, last_turn = [], 0
    for index, raw in enumerate(_list(data["script"], f"{loc}.script")):
        at = f"{loc}.script[{index}]"
        raw = _obj(raw, at, ("turn", "speakers"))
        turn = _int(raw["turn"], f"{at}.turn", 1)
        if turn <= last_turn:
            raise _fail(f"{at}.turn", "script turns must be strictly increasing")
        last_turn = turn
        speakers = _list(raw["speakers"], f"{at}.speakers")
        script.append(ScriptTurn(turn, tuple(
            actor_ref(speaker, f"{at}.speakers[{position}]", "director")
            for position, speaker in enumerate(speakers))))
    scripted_turns = {entry.turn for entry in script}

    choices, choice_ids = [], set()
    for index, raw in enumerate(_list(data["choices"], f"{loc}.choices")):
        at = f"{loc}.choices[{index}]"
        raw = _obj(raw, at, ("choice_id", "turn", "actor_id", "text", "effect_markers"),
                   {"negation_markers": [], "window": 0})
        choice = Choice(
            _unique(choice_ids, _str(raw["choice_id"], f"{at}.choice_id"), f"{at}.choice_id"),
            _int(raw["turn"], f"{at}.turn", 1),
            actor_ref(raw["actor_id"], f"{at}.actor_id", "player"),
            _str(raw["text"], f"{at}.text"),
            _markers(raw["effect_markers"], f"{at}.effect_markers", required=True),
            _markers(raw["negation_markers"], f"{at}.negation_markers", required=False),
            _int(raw["window"], f"{at}.window", 0),
        )
        if choice.turn not in scripted_turns:
            raise _fail(f"{at}.turn", f"turn {choice.turn} is not a scripted turn")
        choices.append(choice)

    return Scenario(
        scenario_id, tuple(actors), tuple(facts), tuple(established), tuple(choices),
        tuple(script),
        _markers(data["pc_authorship_markers"], f"{loc}.pc_authorship_markers", required=False),
    )


# --- run ----------------------------------------------------------------------------------------

def parse_run(data: object, scenario: Scenario) -> Run:
    loc = "run"
    data = _obj(data, loc, ("format", "run_id", "scenario_id", "label", "provenance", "conditions",
                            "turns", "calls", "choice_outcomes"),
                {"enabled_flags": [], "judge_scores": {}})
    if data["format"] != FORMAT:
        raise _fail(f"{loc}.format", f"expected {FORMAT!r}")
    run_id = _str(data["run_id"], f"{loc}.run_id")
    scenario_id = _str(data["scenario_id"], f"{loc}.scenario_id")
    if scenario_id != scenario.scenario_id:
        raise _fail(f"{loc}.scenario_id", f"does not match scenario {scenario.scenario_id!r}")
    label = _str(data["label"], f"{loc}.label")
    provenance = _choice(data["provenance"], f"{loc}.provenance", PROVENANCES)

    raw_conditions = data["conditions"]
    if not isinstance(raw_conditions, dict):
        raise _fail(f"{loc}.conditions", "expected an object")
    for key in REQUIRED_CONDITIONS:
        if key not in raw_conditions:
            raise _fail(f"{loc}.conditions.{key}", "required condition is missing")
    conditions = {_str(key, f"{loc}.conditions"): _str(value, f"{loc}.conditions.{key}")
                  for key, value in raw_conditions.items()}
    enabled_flags = _strings(data["enabled_flags"], f"{loc}.enabled_flags")
    directors = set(scenario.director_ids())
    known_actors = {actor.actor_id for actor in scenario.actors}

    calls, call_ids = [], set()
    for index, raw in enumerate(_list(data["calls"], f"{loc}.calls")):
        at = f"{loc}.calls[{index}]"
        raw = _obj(raw, at, ("call_id", "turn", "purpose", "input_tokens", "cached_input_tokens",
                             "output_tokens", "latency_ms"),
                   {"actor_id": None, "feature_flag": None})
        call = Call(
            _unique(call_ids, _str(raw["call_id"], f"{at}.call_id"), f"{at}.call_id"),
            _int(raw["turn"], f"{at}.turn", 1),
            _str(raw["purpose"], f"{at}.purpose"),
            _opt_str(raw["actor_id"], f"{at}.actor_id"),
            _opt_str(raw["feature_flag"], f"{at}.feature_flag"),
            _int(raw["input_tokens"], f"{at}.input_tokens"),
            _int(raw["cached_input_tokens"], f"{at}.cached_input_tokens"),
            _int(raw["output_tokens"], f"{at}.output_tokens"),
            _int(raw["latency_ms"], f"{at}.latency_ms"),
        )
        if call.cached_input_tokens > call.input_tokens:
            raise _fail(f"{at}.cached_input_tokens", "cached input cannot exceed input")
        if call.purpose == NPC_GENERATION:
            if call.actor_id not in directors:
                raise _fail(f"{at}.actor_id", "npc_generation needs a director actor")
        elif call.actor_id is not None:
            raise _fail(f"{at}.actor_id", "only npc_generation calls name an actor")
        calls.append(call)
    calls_by_id = {call.call_id: call for call in calls}

    turns, last_turn = [], 0
    for index, raw in enumerate(_list(data["turns"], f"{loc}.turns")):
        at = f"{loc}.turns[{index}]"
        raw = _obj(raw, at, ("turn", "contributions"))
        turn = _int(raw["turn"], f"{at}.turn", 1)
        if turn <= last_turn:
            raise _fail(f"{at}.turn", "turns must be strictly increasing")
        last_turn = turn
        contributions = []
        for position, item in enumerate(_list(raw["contributions"], f"{at}.contributions")):
            cat = f"{at}.contributions[{position}]"
            item = _obj(item, cat, ("speaker_id", "author", "text"), {"call_id": None})
            speaker = _opt_str(item["speaker_id"], f"{cat}.speaker_id")
            if speaker is not None and speaker not in known_actors:
                raise _fail(f"{cat}.speaker_id", f"unknown actor {speaker!r}")
            author = _choice(item["author"], f"{cat}.author", AUTHORS)
            text = _str(item["text"], f"{cat}.text", allow_empty=True)
            call_id = _opt_str(item["call_id"], f"{cat}.call_id")
            if author == "player":
                if call_id is not None:
                    raise _fail(f"{cat}.call_id", "player contributions are not model calls")
                if not scenario.is_player(speaker):
                    raise _fail(f"{cat}.speaker_id", "a player contribution needs a player actor")
            else:
                if call_id is None:
                    raise _fail(f"{cat}.call_id", "a model contribution needs its call")
                if call_id not in calls_by_id:
                    raise _fail(f"{cat}.call_id", f"unknown call {call_id!r}")
                if calls_by_id[call_id].turn != turn:
                    raise _fail(f"{cat}.call_id", f"call {call_id!r} belongs to another turn")
            contributions.append(Contribution(speaker, author, text, call_id))  # type: ignore[arg-type]
        turns.append(Turn(turn, tuple(contributions)))
    turn_numbers = {turn.turn for turn in turns}
    for index, call in enumerate(calls):
        if call.turn not in turn_numbers:
            raise _fail(f"{loc}.calls[{index}].turn", f"turn {call.turn} is not in the run")

    raw_outcomes = data["choice_outcomes"]
    if not isinstance(raw_outcomes, dict):
        raise _fail(f"{loc}.choice_outcomes", "expected an object")
    choice_ids = {choice.choice_id for choice in scenario.choices}
    for choice_id in sorted(set(raw_outcomes) - choice_ids):
        raise _fail(f"{loc}.choice_outcomes.{choice_id}", "unknown choice")
    outcomes = {}
    for choice in scenario.choices:
        at = f"{loc}.choice_outcomes.{choice.choice_id}"
        if choice.choice_id not in raw_outcomes:
            raise _fail(at, "every scripted choice needs an outcome")
        outcomes[choice.choice_id] = _choice(raw_outcomes[choice.choice_id], at, OUTCOMES)

    raw_scores = data["judge_scores"]
    if not isinstance(raw_scores, dict):
        raise _fail(f"{loc}.judge_scores", "expected an object")
    judge_scores = {_str(name, f"{loc}.judge_scores"): _number(value, f"{loc}.judge_scores.{name}")
                    for name, value in raw_scores.items()}

    return Run(run_id, scenario_id, label, provenance, _frozen(conditions),  # type: ignore[arg-type]
               enabled_flags, tuple(turns), tuple(calls), _frozen(outcomes), _frozen(judge_scores))
