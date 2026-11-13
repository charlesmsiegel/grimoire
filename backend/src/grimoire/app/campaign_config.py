"""113's read-only startup inputs for the existing 039/041 first slices.

These authored values are configuration, never a new canonical record family. Restart reloads them;
no transcript, model response or UI request is allowed to supply this file or replace its values.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.goals import Agenda, Approach, Goal, Holds, Limit, Present
from grimoire.play.stages import StageBook
from grimoire.staging import Absence, Area, Placement, Stage, Way

from .config import ModelConfig, parse_model_config

MAX_CONFIG_BYTES = 1024 * 1024
MAX_CONFIG_ENTRIES = 256


class CampaignConfigError(ValueError):
    """Safe diagnostic: no authored text, secrets or provider response is echoed."""


@dataclass(frozen=True)
class CampaignConfig:
    stages: StageBook
    agendas: tuple[Agenda, ...] = ()
    labelling: ModelConfig | None = None


def _object(raw: object, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if type(raw) is not dict or set(raw) - allowed or required - set(raw):
        raise ValueError("invalid object")
    return raw


def _array(raw: object, limit: int = 1024) -> list:
    if type(raw) is not list or len(raw) > limit:
        raise ValueError("invalid array")
    return raw


def _ref(raw: object) -> InstanceRef:
    if type(raw) is not str:
        raise ValueError("invalid reference")
    return InstanceRef.parse(raw)


def _condition(raw: object):  # type: ignore[no-untyped-def]
    raw = _object(raw, {"present", "holds", "stances"})
    if set(raw) == {"present"}:
        return Present(_ref(raw["present"]))
    if "holds" in raw and "present" not in raw:
        return Holds(raw["holds"], frozenset(_array(raw.get("stances", ["believed"]))))
    raise ValueError("invalid condition")


def _conditions(raw: object) -> tuple:
    return tuple(_condition(item) for item in _array(raw))


def _agenda(raw: object) -> Agenda:
    raw = _object(raw, {"actor", "goals", "limits"}, {"actor", "goals"})
    actor = _ref(raw["actor"])
    goals = []
    for item in _array(raw["goals"]):
        item = _object(item, {"goal_id", "aim", "priority", "approaches"}, {"goal_id", "aim", "priority", "approaches"})
        approaches = []
        for approach in _array(item["approaches"]):
            approach = _object(approach, {"approach_id", "action", "needs", "tags"}, {"approach_id", "action"})
            approaches.append(Approach(approach["approach_id"], approach["action"],
                                       _conditions(approach.get("needs", [])),
                                       frozenset(_array(approach.get("tags", [])))))
        goals.append(Goal(item["goal_id"], item["aim"], item["priority"], tuple(approaches)))
    limits = []
    for item in _array(raw.get("limits", [])):
        item = _object(item, {"limit_id", "rule", "forbids", "when"}, {"limit_id", "rule", "forbids"})
        limits.append(Limit(item["limit_id"], item["rule"], frozenset(_array(item["forbids"])),
                            _conditions(item.get("when", []))))
    return Agenda(actor.campaign_id, actor, tuple(goals), tuple(limits))


def _stage(raw: object) -> tuple[ChatRef, Stage]:
    raw = _object(raw, {"chat", "as_of", "areas", "ways", "actors", "placements", "absences"},
                  {"chat", "as_of", "areas"})
    chat = ChatRef.parse(raw["chat"])
    areas = [Area(**_object(item, {"area_id", "label"}, {"area_id"})) for item in _array(raw["areas"])]
    ways = []
    for item in _array(raw.get("ways", [])):
        item = dict(_object(item, {"way_id", "sides", "state", "key", "basis", "since"}, {"way_id", "sides"}))
        item["sides"] = tuple(_array(item["sides"]))
        if item.get("key") is not None:
            item["key"] = _ref(item["key"])
        ways.append(Way(**item))
    placements = []
    for item in _array(raw.get("placements", [])):
        item = dict(_object(item, {"who", "area", "holder", "basis", "since"}, {"who"}))
        item["who"] = _ref(item["who"])
        if item.get("holder") is not None:
            item["holder"] = _ref(item["holder"])
        placements.append(Placement(**item))
    absences = []
    for item in _array(raw.get("absences", [])):
        item = dict(_object(item, {"actor", "via", "basis", "since"}, {"actor"}))
        item["actor"] = _ref(item["actor"])
        absences.append(Absence(**item))
    return chat, Stage(chat.campaign_id, chat.chat_id, raw["as_of"], tuple(areas), tuple(ways),
                       tuple(_ref(actor) for actor in _array(raw.get("actors", []))), tuple(placements), tuple(absences))


def parse_campaign_config(raw: object) -> CampaignConfig:
    """Decode only this startup schema, delegating semantic checks to 039 and 041."""
    try:
        raw = _object(raw, {"version", "stages", "agendas", "labelling"}, {"version"})
        if type(raw["version"]) is not int or raw["version"] != 1:
            raise ValueError("unsupported version")
        stages, seen = StageBook(), set()
        for value in _array(raw.get("stages", []), MAX_CONFIG_ENTRIES):
            chat, stage = _stage(value)
            if chat in seen:
                raise ValueError("duplicate stage")
            seen.add(chat)
            stages.put(chat, stage)
        agendas = tuple(_agenda(value) for value in _array(raw.get("agendas", []), MAX_CONFIG_ENTRIES))
        if len({agenda.actor for agenda in agendas}) != len(agendas):
            raise ValueError("duplicate agenda")
        model = None if raw.get("labelling") is None else parse_model_config(raw["labelling"])
        return CampaignConfig(stages, agendas, model)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise CampaignConfigError("invalid campaign configuration: check version, fields and domain references") from None


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ValueError("nonfinite JSON")


def load_campaign_config(path: Path | None) -> CampaignConfig:
    """Read at most 1 MiB; an absent option supplies no stages, agendas or paid labeling."""
    if path is None:
        return CampaignConfig(StageBook())
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(MAX_CONFIG_BYTES + 1)
        if len(data) > MAX_CONFIG_BYTES:
            raise ValueError("too large")
        raw = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise CampaignConfigError("cannot read campaign configuration: expected bounded UTF-8 JSON") from None
    return parse_campaign_config(raw)
