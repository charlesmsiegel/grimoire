"""Exact JSON forms of what the obstacle ledger records (Sprint 040): obstacles and attempts.

Encoding is deterministic: sets are sorted lists, bearings are objects and every key is always present
(``None`` where a value is absent), so decoding a stored entry and encoding it again reproduces it. The
exceptions are an attempt's ``supersedes``, present only on a link that superseded a pending attempt, and a
cost's or stake's ``changes``, present only when it has some, so entries written before either existed read
back unchanged.
Decoding is strict: exact keys, exact JSON types, then the value's own constructor, which re-runs every
authoring check (a railroad read back is still refused). Anything else is an ``ObstacleError`` with
reason ``undecodable_entry``; the store-backed ledger turns that into ``LedgerInvalid`` and fails closed.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.core.identity import InstanceRef

from .changes import ACTOR, HoldingsChange, RelationChange
from .model import Approach, Cost, Obstacle, ObstacleError, ObstacleFact, ObstacleStake

OBSTACLE_KEYS = frozenset({"obstacle_id", "campaign_id", "goal", "actors", "facts", "stakes", "approaches",
                           "provenance"})
ATTEMPT_KEYS = frozenset({"obstacle_id", "attempt_id", "approach_id", "novel"})
_SUPERSEDING_KEYS = ATTEMPT_KEYS | {"supersedes"}
WITHDRAWAL_KEYS = frozenset({"obstacle_id", "attempt_id"})
_FACT_KEYS = frozenset({"fact_id", "kind", "text", "established", "source", "bearings"})
_STAKE_KEYS = frozenset({"stake_id", "text", "escalates"})
_APPROACH_KEYS = frozenset({"approach_id", "text", "risks", "costs"})
_COST_KEYS = frozenset({"cost_id", "text"})
#: ``changes`` is written only on a cost or stake that has some, so obstacles recorded before structured changes
#: existed read back, and encode again, exactly as written.
_CHANGES = "changes"
_RELATION_KEYS = frozenset({"change", "kind", "predicate", "subject", "object", "agent"})
_HOLDINGS_KEYS = frozenset({"change", "kind", "holder", "item", "amount", "target", "agent"})


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

def _who(value: InstanceRef | str | None) -> str | None:
    return None if value is None else str(value)  # ``ACTOR`` is the bare word; a reference is never one


def change_to_payload(change: RelationChange | HoldingsChange) -> dict[str, object]:
    if type(change) is RelationChange:
        return {"change": "relation", "kind": change.kind, "predicate": change.predicate,
                "subject": _who(change.subject), "object": _who(change.object), "agent": _who(change.agent)}
    return {"change": "holdings", "kind": change.kind, "holder": _who(change.holder), "item": change.item,
            "amount": change.amount, "target": _who(change.target), "agent": _who(change.agent)}


def _with_changes(payload: dict[str, object], changes) -> dict[str, object]:
    if changes:
        payload[_CHANGES] = [change_to_payload(change) for change in changes]
    return payload


def _cost(cost: Cost) -> dict[str, object]:
    return _with_changes({"cost_id": cost.cost_id, "text": cost.text}, cost.changes)


def approach_to_payload(approach: Approach) -> dict[str, object]:
    return {"approach_id": approach.approach_id, "text": approach.text, "risks": sorted(approach.risks),
            "costs": [_cost(cost) for cost in approach.costs]}


def obstacle_to_payload(obstacle: Obstacle) -> dict[str, object]:
    if type(obstacle) is not Obstacle:
        raise TypeError("expected an Obstacle")
    return {
        "obstacle_id": obstacle.obstacle_id, "campaign_id": obstacle.campaign_id, "goal": obstacle.goal,
        "actors": sorted(str(actor) for actor in obstacle.actors),
        "facts": [{"fact_id": f.fact_id, "kind": f.kind, "text": f.text, "established": f.established,
                   "source": f.source, "bearings": dict(f.bearings)} for f in obstacle.facts],
        "stakes": [_with_changes({"stake_id": s.stake_id, "text": s.text, "escalates": dict(s.escalates)}, s.changes)
                   for s in obstacle.stakes],
        "approaches": [approach_to_payload(a) for a in obstacle.approaches],
        "provenance": obstacle.provenance,
    }


def _changed(value: object, keys: frozenset[str], label: str) -> Mapping[str, object]:
    """An object with exactly ``keys``, or ``keys`` and ``changes``."""
    return _object(value, keys | {_CHANGES} if isinstance(value, Mapping) and _CHANGES in value else keys, label)


def _ref_or_actor(value: object, label: str, *, optional: bool = False) -> InstanceRef | str | None:
    if value is None and optional:
        return None
    text = _str(value, label)
    if text == ACTOR:
        return ACTOR
    try:
        return InstanceRef.parse(text)
    except ValueError:
        raise _bad(f"{label} is {ACTOR!r} or a reference: {text!r}") from None


def change_from_payload(value: object) -> RelationChange | HoldingsChange:
    shape = value.get("change") if isinstance(value, Mapping) else None
    if shape == "relation":
        data = _object(value, _RELATION_KEYS, "relation change")
        return _build(RelationChange, _str(data["kind"], "kind"), _str(data["predicate"], "predicate"),
                      _ref_or_actor(data["subject"], "subject"), _ref_or_actor(data["object"], "object"),
                      _ref_or_actor(data["agent"], "agent", optional=True))
    if shape == "holdings":
        data = _object(value, _HOLDINGS_KEYS, "holdings change")
        return _build(HoldingsChange, _str(data["kind"], "kind"), _ref_or_actor(data["holder"], "holder"),
                      _str(data["item"], "item"), _int(data["amount"], "amount"),
                      _ref_or_actor(data["target"], "target", optional=True),
                      _ref_or_actor(data["agent"], "agent", optional=True))
    raise _bad("a change is a relation or a holdings change")


def _changes_of(data: Mapping[str, object]) -> tuple[RelationChange | HoldingsChange, ...]:
    if _CHANGES not in data:
        return ()
    return tuple(change_from_payload(item) for item in _list(data[_CHANGES], _CHANGES))


def approach_from_payload(value: object) -> Approach:
    data = _object(value, _APPROACH_KEYS, "approach")
    costs = tuple(_build(Cost, _str(c["cost_id"], "cost_id"), _str(c["text"], "text"), _changes_of(c))
                  for c in (_changed(item, _COST_KEYS, "cost") for item in _list(data["costs"], "costs")))
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
               _bearings(s["escalates"], "escalates"), _changes_of(s))
        for s in (_changed(item, _STAKE_KEYS, "stake") for item in _list(data["stakes"], "stakes")))
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

def attempt_to_payload(obstacle_id: str, attempt_id: str, approach: Approach, listed: bool,
                       supersedes: str | None = None) -> dict[str, object]:
    """An attempt's link to its obstacle: a suggestion by id, or a novel approach in full, and the pending
    attempt it superseded, if any (the key is left out when there is none)."""
    payload: dict[str, object] = {"obstacle_id": obstacle_id, "attempt_id": attempt_id,
                                  "approach_id": approach.approach_id,
                                  "novel": None if listed else approach_to_payload(approach)}
    if supersedes is not None:
        payload["supersedes"] = supersedes
    return payload


def withdrawal_to_payload(obstacle_id: str, attempt_id: str) -> dict[str, object]:
    """A pending attempt withdrawn from its obstacle: it is ``void`` for good, as if a later link superseded it."""
    return {"obstacle_id": obstacle_id, "attempt_id": attempt_id}


def withdrawal_from_payload(value: object) -> tuple[str, str]:
    """``(obstacle id, attempt id)``."""
    data = _object(value, WITHDRAWAL_KEYS, "withdrawal")
    return _str(data["obstacle_id"], "obstacle_id"), _str(data["attempt_id"], "attempt_id")


def attempt_from_payload(value: object) -> tuple[str, str, str, Approach | None, str | None]:
    """``(obstacle id, attempt id, approach id, novel approach or None, superseded attempt id or None)``."""
    keys = _SUPERSEDING_KEYS if isinstance(value, Mapping) and "supersedes" in value else ATTEMPT_KEYS
    data = _object(value, keys, "attempt")
    novel = None if data["novel"] is None else approach_from_payload(data["novel"])
    approach_id = _str(data["approach_id"], "approach_id")
    if novel is not None and novel.approach_id != approach_id:
        raise _bad("a novel approach is linked under its own id")
    if novel is not None and approach_to_payload(novel) != _thaw(data["novel"]):
        raise _bad("the novel approach is not in its canonical form")
    supersedes = _str(data["supersedes"], "supersedes") if "supersedes" in data else None  # never null
    return (_str(data["obstacle_id"], "obstacle_id"), _str(data["attempt_id"], "attempt_id"), approach_id, novel,
            supersedes)
