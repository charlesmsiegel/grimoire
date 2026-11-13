"""Director-authored, device-local tactics in model settings (112).

This codec constructs 039 values; it never interprets model replies as configuration.
The simple editor changes one actor only and refuses to flatten a richer authored policy.
"""
from __future__ import annotations

from grimoire.core.identity import InstanceRef
from grimoire.goals import Agenda, Approach, Goal, Holds, Limit, Present

MAX_AGENDAS = 64


def _object(raw: object, keys: set[str], required: set[str]) -> dict:
    if not isinstance(raw, dict) or set(raw) - keys or required - set(raw):
        raise ValueError("invalid agenda fields")
    return raw


def _list(raw: object, limit: int) -> list:
    if not isinstance(raw, list) or len(raw) > limit:
        raise ValueError("invalid agenda list")
    return raw


def _conditions(raw: object) -> tuple:
    result = []
    for item in _list(raw, 8):
        if isinstance(item, dict) and set(item) == {"present"}:
            result.append(Present(InstanceRef.parse(item["present"])))
        else:
            item = _object(item, {"holds", "stances"}, {"holds", "stances"})
            result.append(Holds(item["holds"], frozenset(_list(item["stances"], 4))))
    return tuple(result)


def parse_agendas(raw: object) -> tuple[Agenda, ...]:
    """Strict, bounded JSON -> existing typed agenda values; no unknown fields discarded."""
    result = []
    try:
        for item in _list(raw, MAX_AGENDAS):
            item = _object(item, {"actor", "goals", "limits"}, {"actor", "goals"})
            actor = InstanceRef.parse(item["actor"])
            goals = []
            for goal in _list(item["goals"], 16):
                goal = _object(goal, {"id", "aim", "priority", "approaches"}, {"id", "aim", "priority", "approaches"})
                approaches = []
                for approach in _list(goal["approaches"], 8):
                    approach = _object(approach, {"id", "action", "needs", "tags"}, {"id", "action"})
                    approaches.append(Approach(approach["id"], approach["action"],
                        _conditions(approach.get("needs", [])), frozenset(_list(approach.get("tags", []), 8))))
                goals.append(Goal(goal["id"], goal["aim"], goal["priority"], tuple(approaches)))
            limits = []
            for limit in _list(item.get("limits", []), 16):
                limit = _object(limit, {"id", "rule", "forbids", "when"}, {"id", "rule", "forbids"})
                limits.append(Limit(limit["id"], limit["rule"], frozenset(_list(limit["forbids"], 8)),
                                    _conditions(limit.get("when", []))))
            result.append(Agenda(actor.campaign_id, actor, tuple(goals), tuple(limits)))
        if len({agenda.actor for agenda in result}) != len(result):
            raise ValueError("duplicate actor")
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        # All authored text stays in the settings, never in an error or diagnostic.
        raise ValueError("agendas must be distinct NPC policies with valid typed goals, approaches and limits") from error
    return tuple(result)


def _condition_json(condition: Present | Holds) -> dict:
    return {"present": str(condition.actor)} if isinstance(condition, Present) else {
        "holds": condition.proposition_id, "stances": sorted(condition.stances)}


def agendas_json(agendas: tuple[Agenda, ...]) -> list[dict]:
    return [{"actor": str(agenda.actor), "goals": [
        {"id": goal.goal_id, "aim": goal.aim, "priority": goal.priority, "approaches": [
            {"id": a.approach_id, "action": a.action, "needs": [_condition_json(c) for c in a.needs],
             "tags": sorted(a.tags)} for a in goal.approaches]} for goal in agenda.goals],
        "limits": [{"id": limit.limit_id, "rule": limit.rule, "forbids": sorted(limit.forbids),
                    "when": [_condition_json(c) for c in limit.when]} for limit in agenda.limits]} for agenda in agendas]


def simple_agenda(actor: InstanceRef, aim: object, actions: object) -> Agenda:
    """The UI's one goal, ordered actions; domain constructors validate all authored text."""
    try:
        actions = _list(actions, 8)
        return Agenda(actor.campaign_id, actor, (Goal("tactics", aim, 50, tuple(
            Approach(f"action-{index + 1}", action) for index, action in enumerate(actions))),))
    except (ValueError, TypeError) as error:
        raise ValueError("use a short goal and one to eight nonblank, single-line actions (at most 400 characters each)") from error


def simple_view(agenda: Agenda | None) -> dict:
    if agenda is None:
        return {"editable": True, "aim": "", "actions": []}
    simple = len(agenda.goals) == 1 and not agenda.limits
    goal = agenda.goals[0]
    simple = simple and goal.goal_id == "tactics" and goal.priority == 50 and all(
        a.approach_id == f"action-{index + 1}" and not a.needs and not a.tags
        for index, a in enumerate(goal.approaches))
    if not simple:
        return {"editable": False, "reason": "This policy has authored conditions, limits or priorities; edit its settings file to preserve them."}
    return {"editable": True, "aim": goal.aim, "actions": [a.action for a in goal.approaches]}
