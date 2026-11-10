"""Read the campaign hub's JSON arguments into domain values, or refuse them (``invalid_field``).

A proposal's operations and holdings lines come from the client exactly as the person built them; the domain
types check every rule they own (021's ``Op``, 023's ``Line``, 035's ``Waiver``) and their refusals are passed
on with their own reason. Nothing here decides who may propose what: the runner does.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.effects import Op
from grimoire.effects.parts import ActedLine, EffectHoldings
from grimoire.holdings import Line
from grimoire.scene import Waiver
from grimoire.textapi import ApiRefused

#: at most this many operations, lines or waivers are read from one request: the domain caps are lower
MAX_ITEMS = 64


def field(args: Mapping[str, object], key: str, kind: type, *, required: bool = True, default=None):  # type: ignore[no-untyped-def]
    value = args.get(key, default)
    if value is None and not required:
        return default
    if type(value) is not kind:
        raise ApiRefused("invalid_field", f"{key} must be a {kind.__name__}")
    return value


def parsed(parse: Callable[[str], object], value: object, label: str):  # type: ignore[no-untyped-def]
    if type(value) is not str:
        raise ApiRefused("invalid_field", f"{label} must be a reference")
    try:
        return parse(value)
    except (ValueError, TypeError):
        raise ApiRefused("invalid_field", f"{label} is not a valid reference") from None


def chat_of(args: Mapping[str, object]) -> ChatRef:
    return parsed(ChatRef.parse, args.get("chat"), "chat")


def actor_of(value: object, label: str, campaign: str) -> InstanceRef:
    actor = parsed(InstanceRef.parse, value, label)
    if actor.campaign_id != campaign:
        raise ApiRefused("invalid_field", f"{label} is not of this chat's campaign")
    return actor


def _optional_actor(item: Mapping[str, object], key: str, campaign: str) -> InstanceRef | None:
    value = item.get(key)
    return None if value is None else actor_of(value, key, campaign)


def _optional_text(item: Mapping[str, object], key: str) -> str | None:
    value = item.get(key)
    if value is not None and type(value) is not str:
        raise ApiRefused("invalid_field", f"{key} must be text")
    return value


def _items(args: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    value = args.get(key, [])
    if value is None:
        return []
    if type(value) is not list or len(value) > MAX_ITEMS or not all(isinstance(item, dict) for item in value):
        raise ApiRefused("invalid_field", f"{key} must be a list of at most {MAX_ITEMS} objects")
    return value


def _build(make: Callable[[], object], label: str):  # type: ignore[no-untyped-def]
    try:
        return make()
    except ApiRefused:
        raise
    except (ValueError, TypeError) as error:  # the domain type's own refusal, with its reason where it has one
        reason = getattr(error, "reason", None)
        raise ApiRefused(reason if isinstance(reason, str) and reason else "invalid_field",
                         f"{label}: {error}") from None


def operations(args: Mapping[str, object], campaign: str) -> tuple[Op, ...]:
    """``operations``: each ``{kind, predicate, subject, object, agent?, backed_by?}``."""
    found = []
    for n, item in enumerate(_items(args, "operations"), 1):
        kind, predicate = item.get("kind"), item.get("predicate")
        subject = actor_of(item.get("subject"), f"operation {n} subject", campaign)
        other = actor_of(item.get("object"), f"operation {n} object", campaign)
        agent = _optional_actor(item, "agent", campaign)
        backed = _optional_text(item, "backed_by")
        found.append(_build(lambda: Op(kind, predicate, subject, other, agent, backed), f"operation {n}"))
    return tuple(found)


def _line(item: Mapping[str, object], n: int, campaign: str) -> Line:
    kind = item.get("kind")
    holder = actor_of(item.get("holder"), f"line {n} holder", campaign)
    target = _optional_actor(item, "target", campaign)
    numbers = {}
    for key in ("amount", "seen", "to"):
        value = item.get(key)
        if value is not None and (type(value) is not int):
            raise ApiRefused("invalid_field", f"line {n} {key} must be a whole number")
        numbers[key] = value
    return _build(lambda: Line(kind, holder, item.get("item"), amount=numbers["amount"], seen=numbers["seen"],
                               to=numbers["to"], target=target), f"line {n}")


def holdings(args: Mapping[str, object], campaign: str) -> EffectHoldings | None:
    """``holdings``: ``{lines: [{kind, holder, item, amount | seen and to, target?, agent?, backed_by?}], note?}``."""
    value = args.get("holdings")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ApiRefused("invalid_field", "holdings must be an object")
    lines = []
    for n, item in enumerate(_items(value, "lines"), 1):
        line = _line(item, n, campaign)
        agent = _optional_actor(item, "agent", campaign)
        backed = _optional_text(item, "backed_by")
        lines.append(_build(lambda: ActedLine(line, agent, backed), f"line {n}"))
    note = _optional_text(value, "note")
    return _build(lambda: EffectHoldings(tuple(lines), note), "holdings")


def waivers(args: Mapping[str, object]) -> tuple[Waiver, ...]:
    """``waive``: each ``{change, reason}``: a due change of the turn's obstacle attempt the director waives."""
    return tuple(_build(lambda: Waiver(item.get("change"), item.get("reason")), f"waiver {n}")
                 for n, item in enumerate(_items(args, "waive"), 1))


def names(args: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = args.get(key, [])
    if value is None:
        return ()
    if type(value) is not list or len(value) > MAX_ITEMS or not all(type(item) is str for item in value):
        raise ApiRefused("invalid_field", f"{key} must be a list of ids")
    return tuple(value)


__all__ = ["MAX_ITEMS", "actor_of", "chat_of", "field", "holdings", "names", "operations", "parsed", "waivers"]
