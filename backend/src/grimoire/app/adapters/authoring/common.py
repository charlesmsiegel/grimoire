"""What the Authoring ops share (116): reading a source and plain values from a request, the app's in-memory
proposals and audits, and a domain refusal as an ``ApiRefused`` with the domain's own reason.

The page is the library owner's: these are authoring tools over a source's own records, not campaign play, so there
is no table and director split (no op takes ``audience``). Every write is the owner's, through the app's one trusted
handle (``ctx.handle``) and the domain service that owns it.

**What is kept in memory.** 087 keeps a proposal in memory only, and 082's report is an in-memory value, so this
adapter keeps both in the app's memory, under a token the server chose, for as long as the app runs: a restart loses
every proposal not yet accepted and every audit report. Nothing here is a record: an accepted proposal is written by
087 as one World Change Set, and an audit writes nothing. The memory is bounded (``MAX_PROPOSALS``, ``MAX_AUDITS``):
past that the oldest entry is forgotten, and an op naming it is answered ``unknown_proposal`` or ``unknown_audit``.
"""

from __future__ import annotations

import weakref
from collections import OrderedDict
from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext
from grimoire.core.identity import validate_segment, validate_source_id
from grimoire.textapi import ApiRefused

#: Proposals (open, drafted, applied or rejected) the app keeps in memory across every source, oldest forgotten first.
MAX_PROPOSALS = 32
#: Audit reports the app keeps in memory, oldest forgotten first.
MAX_AUDITS = 16
#: Suggestion requests (and the calls they spent) remembered for replay; past that the oldest is forgotten, and
#: asking it again needs a new estimate and a new approval like any new request.
MAX_REQUESTS = 256
_REASON = "abcdefghijklmnopqrstuvwxyz_0123456789"


def reason_of(text: object, default: str) -> str:
    """A domain refusal code as a machine-readable reason (``incomplete:length`` is ``incomplete_length``)."""
    found = str(text).replace(":", "_").replace("-", "_")
    return found if found and found[0].isalpha() and all(c in _REASON for c in found) and len(found) <= 32 \
        else default


def refused(error: Exception, default: str, message: str | None = None) -> ApiRefused:
    """``error``'s own reason (an ``AuthoringError``, ``InvestigationError``, ``SourceUpgradeError``)."""
    reason = getattr(error, "reason", None)
    return ApiRefused(reason_of(reason, default) if isinstance(reason, str) else default,
                      message if message is not None else str(error))


def source_of(args: Mapping[str, object]) -> str:
    value = args.get("source")
    if type(value) is not str:
        raise ApiRefused("invalid_field", "source must be a source id, such as world:harbor")
    try:
        validate_source_id(value)
    except ValueError:
        raise ApiRefused("invalid_field", "source is not a source id") from None
    return value


def world_only(source: str, what: str) -> None:
    """087 writes only world records, and the page audits only worlds: any other source is refused."""
    if not source.startswith("world:"):
        raise ApiRefused("not_a_world", f"{what} is for a world source; {source} is not one")


def text_of(args: Mapping[str, object], key: str, maximum: int, *, required: bool = True, default: str = "") -> str:
    value = args.get(key, None if required else default)
    if type(value) is not str or len(value) > maximum or (required and not value.strip()):
        raise ApiRefused("invalid_field", f"{key} must be text of at most {maximum} characters")
    return value


def whole(args: Mapping[str, object], key: str, low: int, high: int, default: int) -> int:
    value = args.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise ApiRefused("invalid_field", f"{key} must be a whole number from {low} to {high}")
    return value


def segment(args: Mapping[str, object], key: str, *, required: bool = True) -> str | None:
    value = args.get(key)
    if value is None and not required:
        return None
    if type(value) is not str:
        raise ApiRefused("invalid_field", f"{key} must be an id")
    try:
        validate_segment(value, key)
    except ValueError:
        raise ApiRefused("invalid_field", f"{key} is not a valid id: lower case letters, digits, '.', '_' and '-'") \
            from None
    return value


def keys_of(args: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = args.get(key, [])
    if type(value) is not list or len(value) > 64 or any(type(item) is not str or len(item) > 128 for item in value):
        raise ApiRefused("invalid_field", f"{key} must be a list of candidate keys")
    return tuple(value)


class Memory:
    """The proposals and audit reports this app holds for its open library, in memory only."""

    def __init__(self) -> None:
        self.proposals: OrderedDict[str, object] = OrderedDict()
        self.audits: OrderedDict[str, object] = OrderedDict()
        #: a suggestion's request id -> what it was asked with and the proposal it gave (a retry sends nothing)
        self.requests: OrderedDict[str, tuple[tuple, str]] = OrderedDict()
        #: every suggestion call sent, refused replies included
        self.spent: list[dict] = []

    def spend(self, entry: dict) -> None:
        self.spent.append(entry)
        del self.spent[:-MAX_REQUESTS]

    def keep(self, table: OrderedDict, key: str, value: object, limit: int) -> None:
        table[key] = value
        table.move_to_end(key)
        while len(table) > limit:
            table.popitem(last=False)


_memories: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def memory(ctx: AdapterContext) -> Memory:
    """This open library's memory (one per text API, so a second library served later starts empty)."""
    found = _memories.get(ctx.api)
    if found is None:
        found = _memories[ctx.api] = Memory()
    return found


__all__ = ["MAX_AUDITS", "MAX_PROPOSALS", "MAX_REQUESTS", "Memory", "keys_of", "memory", "reason_of", "refused", "segment",
           "source_of", "text_of", "whole", "world_only"]
