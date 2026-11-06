"""The ``turns/`` family over one open ``LibraryStore`` (035): read a chat's turns, create one, update one.

A turn is created once (create-only, so two posts can never both become turn n) and then changed only by
compare-and-swap against the etag it was read at. A turn file that cannot be read or decoded makes the
chat's turns incomplete: every write to that chat is refused (``turns_unreadable``) rather than renumber
or overwrite history it cannot see.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.scene import TURN_SCHEMA, SceneRefused, TurnRecord, turn_from_payload, turn_to_payload
from grimoire.store import LibraryStore, ScopeRecord, StaleWrite, TakenIdsUnlisted
from grimoire.store.layout import address_parts


@dataclass(frozen=True)
class StoredTurn:
    """A turn as stored: its record, the etag it was read at, and its envelope's attribution and unknown
    fields, which a rewrite keeps."""

    record: TurnRecord
    etag: str
    owner: str | None = None
    extra: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ChatTurns:
    """One chat's turns in order, and the problems that make the list incomplete (paths and reasons)."""

    chat: ChatRef
    turns: tuple[StoredTurn, ...]
    problems: tuple[tuple[str, str], ...]

    @property
    def complete(self) -> bool:
        return not self.problems

    @property
    def last(self) -> StoredTurn | None:
        return self.turns[-1] if self.turns else None

    def records(self) -> tuple[TurnRecord, ...]:
        return tuple(stored.record for stored in self.turns)


def _chat_of(turn_id: str) -> str:
    return turn_id.rsplit(".", 1)[0]


def _other_chat(path: str, chat: ChatRef) -> bool:
    """Whether an unreadable file is plainly another chat's turn (``<other chat>.<6 digits>.json``). Anything
    else (the folder itself, a name that does not parse) counts against every chat: fail closed."""
    name = path.rsplit("/", 1)[-1]
    if "/turns/" not in path or not name.endswith(".json"):
        return False
    stem = name[:-len(".json")]
    other, _, number = stem.rpartition(".")
    return bool(other) and len(number) == 6 and number.isdigit() and other != chat.chat_id


class SceneLibrary:
    """Turn records of one open library. ``handle`` must be able to write ``campaigns/``."""

    def __init__(self, store: LibraryStore, handle: object, *, owner: str | None = None) -> None:
        self.store = store
        self.handle = handle
        self.owner = owner

    def turns(self, chat: ChatRef) -> ChatTurns:
        found = self.store.campaign_turns(chat.campaign_id)
        problems = [(problem.path, problem.code) for problem in found.problems if not _other_chat(problem.path, chat)]
        turns = []
        for ref, stored in found.records.items():
            if _chat_of(ref.turn_id) != chat.chat_id:
                continue
            try:
                record = turn_from_payload(ref, stored.record.schema, stored.record.payload)
            except SceneRefused as error:
                problems.append((str(ref), error.reason))
                continue
            turns.append(StoredTurn(record, stored.etag, stored.record.owner, stored.record.extra))
        turns.sort(key=lambda stored: stored.record.number)
        numbers = [stored.record.number for stored in turns]
        if numbers != list(range(1, len(numbers) + 1)):
            problems.append((str(chat), "turn_gap"))
        return ChatTurns(chat, tuple(turns), tuple(sorted(problems)))

    def write(self, record: TurnRecord, base: StoredTurn | None, *, after: StoredTurn | None = None,
              before_commit: Callable[[], None] | None = None) -> StoredTurn:
        """Create (``base=None``) or compare-and-swap one turn over ``base``, as read; the turn as now stored.

        A rewrite keeps ``base``'s envelope owner and unknown fields. ``after`` is the chat's previous turn as
        the caller read it: a new turn is created only while that turn is unchanged (a pause written on it
        meanwhile refuses the create), so no turn is ever played over a change it did not see.
        ``before_commit`` runs under the store lock just before the commit point (the runner re-checks its
        fence there): raising from it writes nothing.
        """
        payload = turn_to_payload(record)
        if base is None:
            envelope = ScopeRecord(record.ref, TURN_SCHEMA, payload, owner=self.owner)
        else:
            envelope = ScopeRecord(record.ref, TURN_SCHEMA, payload, owner=base.owner, extra=base.extra)
        with self.store.lock:
            if after is not None:
                current = self.store.campaign_turns(record.chat.campaign_id).records.get(after.record.ref)
                if current is None or current.etag != after.etag:
                    raise SceneRefused("turn_changed", "the chat's last turn changed since it was read; read it again")
            if before_commit is not None:
                before_commit()
            txn = self.store.transaction()
            txn.put(envelope, None if base is None else base.etag)
            try:
                result = txn.commit(self.handle)
            except StaleWrite:
                raise SceneRefused("turn_changed", "the turn changed since it was read; read it again") from None
        etag = result.etags["/".join(address_parts(record.ref))]
        return StoredTurn(record, etag, envelope.owner, envelope.extra)


def turn_instance_ids(store: LibraryStore, campaign_id: str) -> frozenset[str]:
    """Instance ids named by the campaign's turn files (posts, audiences, casts, lines).

    Like 021's ``accepted_instance_ids``, every allocator treats them as taken, so a deleted actor's id never
    goes to another individual that would inherit what it heard. Raw payloads are scanned without decoding,
    and a file the store cannot read raises ``TakenIdsUnlisted`` (fail closed).
    """
    found = store.campaign_turns(campaign_id)
    if found.problems:
        paths = tuple(sorted({problem.path for problem in found.problems}))
        raise TakenIdsUnlisted(f"turn files of {campaign_id} cannot all be read ({', '.join(paths)}), so the "
                               "instance ids they name cannot be reserved", paths)
    taken: set[str] = set()
    for stored in found.records.values():
        _collect(stored.record.payload, campaign_id, taken)
    return frozenset(taken)


def _collect(value: object, campaign_id: str, into: set[str]) -> None:
    if isinstance(value, str):
        try:
            ref = InstanceRef.parse(value)
        except ValueError:
            return
        if ref.campaign_id == campaign_id:
            into.add(ref.instance_id)
    elif isinstance(value, Mapping):
        for item in value.values():
            _collect(item, campaign_id, into)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect(item, campaign_id, into)
