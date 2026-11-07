"""The ``turns/`` family over one open ``LibraryStore`` (035): read a chat's turns, create one, update one.

A turn is created once (create-only, so two posts can never both become turn n) and then changed only by
compare-and-swap against the etag it was read at. A turn file that cannot be read or decoded makes the
chat's turns incomplete: every write to that chat is refused (``turns_unreadable``) rather than renumber
or overwrite history it cannot see. Two devices' turns of one number make it forked (028's turn merge,
``grimoire.scene.branches``): ``choose`` sets the other branches aside, and play goes on.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from grimoire.core.identity import ChatRef, InstanceRef, TurnRef
from grimoire.scene import (
    TURN_SCHEMA, Fork, SceneRefused, SetAside, TurnRecord, arrange, parse_turn_id, turn_from_payload, turn_id,
    turn_to_payload,
)
from grimoire.scene.branches import is_mine, tag_for
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
    """One chat's turns in order, and the problems that make the list incomplete (paths and reasons).

    ``turns`` is the chat's line as play reads it: up to its first fork, then every branch not set aside (028).
    ``set_aside`` holds the turns a choice set aside (kept, heard by no one); ``fork`` the first fork, if any.
    """

    chat: ChatRef
    turns: tuple[StoredTurn, ...]
    problems: tuple[tuple[str, str], ...]
    set_aside: tuple[StoredTurn, ...] = ()
    fork: Fork | None = None

    @property
    def complete(self) -> bool:
        return not self.problems

    @property
    def last(self) -> StoredTurn | None:
        return self.turns[-1] if self.turns else None

    def records(self) -> tuple[TurnRecord, ...]:
        return tuple(stored.record for stored in self.turns)

    @property
    def pauses(self) -> tuple[StoredTurn, ...]:
        """Every turn that holds an open pause, on the line or set aside (016's pause is a safety tool: a turn a
        fork choice set aside keeps its pause, so a merge never lifts one, 028). Play waits while any stands."""
        return tuple(stored for stored in (*self.turns, *self.set_aside) if stored.record.paused)

    def etags(self) -> dict[str, str]:
        return {stored.record.turn_id: stored.etag for stored in (*self.turns, *self.set_aside)}


def _chat_of(turn_id: str) -> str:
    return turn_id.rsplit(".", 1)[0]


def _other_chat(path: str, chat: ChatRef) -> bool:
    """Whether an unreadable file is plainly another chat's turn (``<other chat>.<6 digits>[-<device>].json``).
    Anything else (the folder itself, a name that does not parse) counts against every chat: fail closed."""
    name = path.rsplit("/", 1)[-1]
    if "/turns/" not in path or not name.endswith(".json"):
        return False
    parsed = parse_turn_id(name[:-len(".json")])
    return parsed is not None and parsed[0] != chat.chat_id


class SceneLibrary:
    """Turn records of one open library. ``handle`` must be able to write ``campaigns/``."""

    def __init__(self, store: LibraryStore, handle: object, *, owner: str | None = None) -> None:
        self.store = store
        self.handle = handle
        self.owner = owner

    def turns(self, chat: ChatRef) -> ChatTurns:
        return self._view(chat, self.store.campaign_turns(chat.campaign_id))

    def _view(self, chat: ChatRef, found) -> ChatTurns:  # type: ignore[no-untyped-def]
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
        by_id = {stored.record.turn_id: stored for stored in turns}
        lineage = arrange(stored.record for stored in turns)  # in one order on every device
        if lineage.fork is not None:
            # two devices' turn n after one turn (sync brought both): both are kept and read, never one silently
            # dropped, and every write waits until someone chooses which goes on (``choose``)
            problems.append((str(chat), "turn_fork"))
        if lineage.orphans:  # a turn whose predecessor is missing or cannot be told
            problems.append((str(chat), "turn_gap"))
        return ChatTurns(chat, tuple(by_id[turn.turn_id] for turn in lineage.line), tuple(sorted(problems)),
                         tuple(by_id[turn.turn_id] for turn in lineage.set_aside), lineage.fork)

    def fork_problems(self, turns: ChatTurns) -> list[tuple[str, str]]:
        """``("turn_fork", <which turns, which devices, from which number>)`` for a forked chat, else nothing."""
        if turns.fork is None:
            return []
        try:
            own = self.device()
        except SceneRefused:  # a damaged identity: no branch is called this device's
            return [("turn_fork", turns.fork.describe())]
        replays = len(turns.turns) + len(turns.set_aside)
        return [("turn_fork", turns.fork.describe(lambda device: is_mine(own, device, replays)))]

    def tag_for(self, turns: ChatTurns, number: int) -> str | None:
        """The device tag a new turn ``number`` of ``turns.chat`` takes here: this library's own (``device``), or,
        where this device's turn ``number`` is set aside (it chose another device's branch), the first free
        replay tag (``grimoire.scene.branches.replay_tag``), so a turn id is never used twice."""
        taken = [stored.record.turn_id for stored in (*turns.turns, *turns.set_aside)]
        return tag_for(self.device(), taken, lambda tag: turn_id(turns.chat, number, tag))

    def device(self) -> str | None:
        """This library's device tag for new turns (028's ``device_tag``, without its ``-``), or ``None`` for a
        library that was never given a device identity. A damaged identity refuses (``device_identity``), so it
        never quietly brings back ids that collide across devices."""
        from grimoire.chatstore import DeviceIdentityUnusable, device_tag  # chatstore imports this package
        try:
            tag = device_tag(self.store)
        except DeviceIdentityUnusable as error:
            raise SceneRefused("device_identity", str(error)[:200]) from None
        return tag[1:] or None

    def write(self, record: TurnRecord, base: StoredTurn | None, *, after: StoredTurn | None = None,
              last: bool = False, before_commit: Callable[[], None] | None = None,
              on_commit: Callable[[], None] | None = None) -> StoredTurn:
        """Create (``base=None``) or compare-and-swap one turn over ``base``, as read; the turn as now stored.

        A rewrite keeps ``base``'s envelope owner and unknown fields. ``after`` is the chat's previous turn as
        the caller read it: a new turn is created only while that turn is unchanged (a pause written on it
        meanwhile refuses the create), so no turn is ever played over a change it did not see. ``last`` writes
        only while no later turn of the chat exists (a pause belongs on the turn play would go on from).
        ``before_commit`` runs under the store lock just before the commit point (the runner re-checks its
        fence there): raising from it writes nothing. ``on_commit`` runs under the same lock once the write
        has committed (the runner claims its fence there, so publishing a run and claiming are one step).
        """
        payload = turn_to_payload(record)
        if base is None:
            envelope = ScopeRecord(record.ref, TURN_SCHEMA, payload, owner=self.owner)
        else:
            envelope = ScopeRecord(record.ref, TURN_SCHEMA, payload, owner=base.owner, extra=base.extra)
        with self.store.lock:
            view = (self._view(record.chat, self.store.campaign_turns(record.chat.campaign_id))
                    if base is None or last or after is not None else None)  # one read for every check
            line = view.turns if view is not None else ()  # turns set aside take no number (028)
            if view is not None and view.problems:
                # a file of this chat that cannot be read (a peer's newer turn, say) or a fork arrived since the
                # caller read the chat: no turn is created or paused over what this version cannot see
                raise SceneRefused("turn_changed", "the chat's turns changed since they were read; read them again")
            if base is None:
                assert view is not None
                if view.pauses:  # a pause written meanwhile, on any turn (a peer's, set aside): never played over
                    raise SceneRefused("turn_changed", "play was paused since the chat was read; read it again")
                if any(stored.record.number == record.number for stored in line):
                    # this library already holds turn n under another device's tag (made before identify)
                    raise SceneRefused("turn_changed", "this turn was played since it was read; read it again")
                if record.device != self.tag_for(view, record.number):
                    # identified (or re-identified) since the turn was planned, or its id was taken meanwhile
                    raise SceneRefused("turn_changed", "this library's device identity changed; read it again")
                if record.follows is not None and (view.fork is not None or view.last is None
                                                   or view.last.record.turn_id != record.follows):
                    raise SceneRefused("turn_changed", "the chat's line changed since it was read; read it again")
            if last and any(stored.record.number > record.number for stored in line):
                raise SceneRefused("turn_changed", "a later turn was played since this one was read; read it again")
            if after is not None:
                current = next((stored for stored in line if stored.record.ref == after.record.ref), None)
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
            if on_commit is not None:
                on_commit()
        etag = result.etags["/".join(address_parts(record.ref))]
        return StoredTurn(record, etag, envelope.owner, envelope.extra)

    def choose(self, turns: ChatTurns, chosen: str, by: str) -> ChatTurns:
        """028: at the chat's fork, as read in ``turns``, the branch headed by ``chosen`` goes on; the chat's turns
        afterwards. Every other branch head is marked ``set_aside`` (naming ``chosen``) and a mark on ``chosen``
        is cleared, in one commit, each file by compare-and-swap: two devices making the same choice write the
        same bytes. Refused (nothing written): ``not_forked``, ``not_a_branch``, or ``turn_changed`` when any
        turn of the chat changed since ``turns`` was read."""
        fork = turns.fork
        if fork is None:
            raise SceneRefused("not_forked", "this chat is not forked")
        heads = [branch.head for branch in fork.branches]
        if chosen not in heads:
            raise SceneRefused("not_a_branch", "choose the first turn of one of the fork's branches")
        by_id = {stored.record.turn_id: stored for stored in (*turns.turns, *turns.set_aside)}
        changes = []
        for head in heads:
            record = replace(by_id[head].record, set_aside=None if head == chosen else SetAside(chosen, by))
            if record != by_id[head].record:
                changes.append((record, by_id[head]))
        # only marks are written, never the chosen branch: a pause anywhere stays where it is (``pauses``)
        return self._rewrite(turns, changes)

    def resume(self, turns: ChatTurns, by: str, *, owner: bool) -> ChatTurns:
        """016: lift the open pauses ``by`` may lift (its own; every one for the library's ``owner``), on whichever
        turns hold them, line or set aside, in one commit; the chat's turns afterwards. Another player's pause
        stays until they or the owner resume. Refused (nothing written): ``not_paused``, ``not_yours``, or
        ``turn_changed`` when any turn of the chat changed since ``turns`` was read."""
        if not turns.pauses:
            raise SceneRefused("not_paused", "play is not paused")
        lifted = [stored for stored in turns.pauses if owner or stored.record.paused_by == by]
        if not lifted:
            raise SceneRefused("not_yours", "only the player who paused, or the owner, may resume")
        return self._rewrite(turns, [(replace(stored.record, paused=False, paused_by=None), stored)
                                     for stored in lifted])

    def _rewrite(self, turns: ChatTurns, changes: list[tuple[TurnRecord, StoredTurn]]) -> ChatTurns:
        """Rewrite ``changes`` (each a record over the stored turn it replaces) in one commit, each by
        compare-and-swap, only while every turn of the chat is as ``turns`` read it (a pause or a mark landing
        meanwhile, anywhere in the chat, refuses it: ``turn_changed``)."""
        with self.store.lock:
            now = self.turns(turns.chat)
            if now.etags() != turns.etags() or now.problems != turns.problems:
                raise SceneRefused("turn_changed", "the chat's turns changed since they were read; read them again")
            if not changes:
                return now
            txn = self.store.transaction()
            for record, stored in changes:
                txn.put(ScopeRecord(record.ref, TURN_SCHEMA, turn_to_payload(record), owner=stored.owner,
                                    extra=stored.extra), stored.etag)
            try:
                txn.commit(self.handle)
            except StaleWrite:
                raise SceneRefused("turn_changed", "a turn of the chat changed since it was read") from None
            return self.turns(turns.chat)


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
