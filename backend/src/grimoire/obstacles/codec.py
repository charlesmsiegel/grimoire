"""Exact JSON forms of what the obstacle ledger records (Sprint 040): obstacles and attempts.

Encoding is deterministic: sets are sorted lists, bearings are objects and every key is always present
(``None`` where a value is absent), so decoding a stored entry and encoding it again reproduces it.
Decoding is strict: exact keys, exact JSON types, then the value's own constructor, which re-runs every
authoring check (a railroad read back is still refused). Anything else is an ``ObstacleError`` with
reason ``undecodable_entry``; the store-backed ledger turns that into ``LedgerInvalid`` and fails closed.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.identity import InstanceRef

from .model import Approach, Cost, Obstacle, ObstacleError, ObstacleFact, ObstacleStake

OBSTACLE_KEYS = frozenset({"obstacle_id", "campaign_id", "goal", "actors", "facts", "stakes", "approaches",
                           "provenance"})
ATTEMPT_KEYS = frozenset({"obstacle_id", "attempt_id", "approach_id", "novel"})
_FACT_KEYS = frozenset({"fact_id", "kind", "text", "established", "source", "bearings"})
_STAKE_KEYS = frozenset({"stake_id", "text", "escalates"})
_APPROACH_KEYS = frozenset({"approach_id", "text", "risks", "costs"})
_COST_KEYS = frozenset({"cost_id", "text"})


def _bad(message: str) -> ObstacleError:
    return ObstacleError(message, "undecodable_entry")


def _object(value: object, keys: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise _bad(f"{label} must be an object with exactly {sorted(keys)}")
    return value


def _str(value: object, label: str) -> str:
    if type(value) is not str:
        raise _bad(f"{label} must be text")
    return value


def _int(value: object, label: str) -> int:
    if type(value) is not int:  # ``true`` is not 1
        raise _bad(f"{label} must be an integer")
    return value


def _list(value: object, label: str) -> list | tuple:
    if not isinstance(value, (list, tuple)):
        raise _bad(f"{label} must be a list")
    return value


def _strings(value: object, label: str) -> tuple[str, ...]:
    items = _list(value, label)
    if not all(type(item) is str for item in items):
        raise _bad(f"{label} must be a list of text")
    if list(items) != sorted(set(items)):
        raise _bad(f"{label} must be sorted and unique")
    return tuple(items)


def _bearings(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not all(type(k) is str and type(v) is str for k, v in value.items()):
        raise _bad(f"{label} must map approach ids to bearings")
    return dict(value)


def _build(factory, *args):
    try:
        return factory(*args)
    except ObstacleError as error:
        raise _bad(f"{getattr(factory, '__name__', factory)}: {error} ({error.reason})") from None
    except (ValueError, TypeError) as error:
        raise _bad(f"{getattr(factory, '__name__', factory)}: {error}") from None


def _thaw(value: object) -> object:
    """Plain JSON shapes: stored entries come back frozen (mapping proxies, tuples)."""
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(child) for child in value]
    return value


# --- obstacles -------------------------------------------------------------------------------

def approach_to_payload(approach: Approach) -> dict[str, object]:
    return {"approach_id": approach.approach_id, "text": approach.text, "risks": sorted(approach.risks),
            "costs": [{"cost_id": cost.cost_id, "text": cost.text} for cost in approach.costs]}


def obstacle_to_payload(obstacle: Obstacle) -> dict[str, object]:
    if type(obstacle) is not Obstacle:
        raise TypeError("expected an Obstacle")
    return {
        "obstacle_id": obstacle.obstacle_id, "campaign_id": obstacle.campaign_id, "goal": obstacle.goal,
        "actors": sorted(str(actor) for actor in obstacle.actors),
        "facts": [{"fact_id": f.fact_id, "kind": f.kind, "text": f.text, "established": f.established,
                   "source": f.source, "bearings": dict(f.bearings)} for f in obstacle.facts],
        "stakes": [{"stake_id": s.stake_id, "text": s.text, "escalates": dict(s.escalates)} for s in obstacle.stakes],
        "approaches": [approach_to_payload(a) for a in obstacle.approaches],
        "provenance": obstacle.provenance,
    }


def approach_from_payload(value: object) -> Approach:
    data = _object(value, _APPROACH_KEYS, "approach")
    costs = tuple(_build(Cost, _str(c["cost_id"], "cost_id"), _str(c["text"], "text"))
                  for c in (_object(item, _COST_KEYS, "cost") for item in _list(data["costs"], "costs")))
    return _build(Approach, _str(data["approach_id"], "approach_id"), _str(data["text"], "text"),
                  frozenset(_strings(data["risks"], "risks")), costs)


def obstacle_from_payload(campaign_id: str, value: object) -> Obstacle:
    data = _object(value, OBSTACLE_KEYS, "obstacle")
    actors = []
    for text in _strings(data["actors"], "actors"):
        try:
            actors.append(InstanceRef.parse(text))
        except ValueError:
            raise _bad(f"actor is not a reference: {text!r}") from None
    facts = tuple(
        _build(ObstacleFact, _str(f["fact_id"], "fact_id"), _str(f["kind"], "kind"), _str(f["text"], "text"),
               _int(f["established"], "established"), _str(f["source"], "source"), _bearings(f["bearings"], "bearings"))
        for f in (_object(item, _FACT_KEYS, "fact") for item in _list(data["facts"], "facts")))
    stakes = tuple(
        _build(ObstacleStake, _str(s["stake_id"], "stake_id"), _str(s["text"], "text"),
               _bearings(s["escalates"], "escalates"))
        for s in (_object(item, _STAKE_KEYS, "stake") for item in _list(data["stakes"], "stakes")))
    approaches = tuple(approach_from_payload(item) for item in _list(data["approaches"], "approaches"))
    obstacle = _build(Obstacle, _str(data["obstacle_id"], "obstacle_id"), _str(data["campaign_id"], "campaign_id"),
                      _str(data["goal"], "goal"), frozenset(actors), facts, stakes, approaches,
                      _str(data["provenance"], "provenance"))
    if obstacle.campaign_id != campaign_id:
        raise _bad("the obstacle belongs to another campaign")
    if obstacle_to_payload(obstacle) != _thaw(data):  # sorted, canonical: nothing a reader would re-order
        raise _bad("the obstacle is not in its canonical form")
    return obstacle


# --- attempts --------------------------------------------------------------------------------

def attempt_to_payload(obstacle_id: str, attempt_id: str, approach: Approach, listed: bool) -> dict[str, object]:
    """An attempt's link to its obstacle: a suggestion by id, or a novel approach in full."""
    return {"obstacle_id": obstacle_id, "attempt_id": attempt_id, "approach_id": approach.approach_id,
            "novel": None if listed else approach_to_payload(approach)}


def attempt_from_payload(value: object) -> tuple[str, str, str, Approach | None]:
    """``(obstacle id, attempt id, approach id, novel approach or None)``."""
    data = _object(value, ATTEMPT_KEYS, "attempt")
    novel = None if data["novel"] is None else approach_from_payload(data["novel"])
    approach_id = _str(data["approach_id"], "approach_id")
    if novel is not None and novel.approach_id != approach_id:
        raise _bad("a novel approach is linked under its own id")
    if novel is not None and approach_to_payload(novel) != _thaw(data["novel"]):
        raise _bad("the novel approach is not in its canonical form")
    return _str(data["obstacle_id"], "obstacle_id"), _str(data["attempt_id"], "attempt_id"), approach_id, novel
