"""Copy a scene into another campaign, transcript only, with every actor's identity reviewed (Sprint 084). Adapter.

``SceneTransfer.scan(origin, target)`` reads the origin chat and the target campaign and reports what a copy would
need decided: the origin's cast, each with the target actors that *are* that individual (an instance of the same
definition entity) and the target actors that only share its name (``namesakes``), which a name never makes one.
It writes nothing and calls no provider.

``SceneTransfer.plan(origin, target, cast=...)`` copies the history **only as the caller maps it**. Every actor of
the origin's roster must be decided: reused as one named target actor of the same definition entity, created
``NEW`` in the target from the definition the origin played (pinned to that revision), or ``LEAVE``-d out (its posts
keep their label and lose their actor). Nothing is matched by name, and an alternate portrayal (a separate entity
joined by ``alternate_portrayal``) is never the same individual, however it is named. The plan is one 107
``ChatChange``: a new chat in the target campaign with its bindings and new actors, and the copied history as 036
transcript pages of that chat, committed in one transaction. ``preview()`` shows the bytes; ``commit(approvals)``
writes them, after checking that the origin is still exactly what was copied.

The target's runner hears a chat's transcript pages by one rule: the cast the chat starts with heard every shown
post (``grimoire.scene.transcript``). So a post is copied only when every origin actor the copy carries over heard
it in the origin; the rest is reported as omitted, with who missed it, and a private post (a whisper) is never
copied. Accepted state stays where it was: an accepted effect, a ruling and every event, belief, relation,
holding and memory of the origin are reported (``left``) and never written to the target. The origin is never
written. See docs/SCENE_TRANSFER.md.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from grimoire.archaeologystore.library import PAGE_BUDGET, measure_post
from grimoire.chats import ChatCodecError, ChatRecord, Launch, chat_from_payload, role_rule, scope_from_payload
from grimoire.chatstore import ChatChange, ChatCommit, ChatLibrary, InvalidBinding
from grimoire.core.identity import ChatRef, InstanceRef, ScopeRef, SourceRef, TranscriptRef
from grimoire.core.index import name_key
from grimoire.effective import chat_snapshot
from grimoire.play.history import history_pages, imported_cast
from grimoire.scenestore import SceneLibrary
from grimoire.store import InvalidEnvelope, LibraryStore, RecordNotFound, ScopeRecord, StaleWrite, StoreError
from grimoire.store.jsonfmt import render
from grimoire.transcripts import (
    TRANSCRIPT_SCHEMA, Post, Speaker, TranscriptError, TranscriptPage, page_to_payload, paginate,
)

from .history import TABLE_ONLY, HistoryItem, Left, page_items, turn_items

#: a cast decision: create the actor in the target from the definition the origin played
NEW = "new"
#: a cast decision: carry no actor over; its posts keep their label and name no actor
LEAVE = "leave"
FORMAT = "grimoire-scene-transfer"
#: the premise a copy launches from when it carries over no character and no source (display only)
COPIED_PREMISE = "Scene copied from another campaign"
#: rendered bytes of page 1's origin at most (its cast table); page 1's posts get the rest of PAGE_BUDGET
ORIGIN_BUDGET = PAGE_BUDGET // 2
_SOURCE_ROLES = ("scenario", "lorebook")


class TransferRefused(ValueError):
    """A copy this service refuses; nothing was written. ``reason`` is machine-readable; ``actor`` names the
    origin actor a cast refusal is about, or ``None``."""

    def __init__(self, message: str, reason: str, actor: InstanceRef | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.actor = actor

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason, self.actor))


@dataclass(frozen=True)
class CastMember:
    """One actor of the origin chat's roster, as a reviewer decides it.

    ``definition`` is the definition the origin bound it to (its latest binding, pinned as the origin pinned it),
    or ``None`` when the origin names none. ``candidates`` are the target's instances of that same definition
    entity, the only actors it may be reused as. ``namesakes`` are target actors of *another* entity whose name
    matches (``name_key``): shown so a reviewer sees them, never offered. ``problem`` is why the origin cannot
    show it (``missing_actor``, ``undecodable_actor``, ``missing_definition``), or ``None``."""

    actor: InstanceRef
    role: str
    definition: SourceRef | None
    name: str
    active: bool
    candidates: tuple[InstanceRef, ...]
    namesakes: tuple[InstanceRef, ...]
    problem: str | None = None


@dataclass(frozen=True)
class SourceChoice:
    """One scenario or lorebook the origin has bound now: copied with the chat unless the caller leaves it out.
    ``broken`` is why it does not resolve in this library, or ``None``."""

    ref: SourceRef
    role: str
    broken: str | None = None


@dataclass(frozen=True)
class TransferScan:
    """What a copy of ``origin`` into ``target`` needs decided, read now; nothing was written.

    ``history`` counts what the origin holds (``pages``, ``page_posts``, ``turns``); ``history_problems`` are why
    it cannot be copied whole now (a damaged page, a fork, a sync conflict), and ``plan`` refuses while any
    stands. ``notes`` are what the reviewer should know (``world_differs``, an origin snapshot problem)."""

    origin: ChatRef
    target: ScopeRef
    cast: tuple[CastMember, ...]
    sources: tuple[SourceChoice, ...]
    history: Mapping[str, int]
    history_problems: tuple[tuple[str, str], ...]
    notes: tuple[tuple[str, str], ...]
    #: provider calls a copy makes, ever: scanning and copying are text only
    provider_calls: int = 0

    def member(self, actor: InstanceRef) -> CastMember | None:
        return next((member for member in self.cast if member.actor == actor), None)


@dataclass(frozen=True)
class Omitted:
    """An origin post the copy leaves out: ``reason`` ``unheard`` (``missed`` names the carried-over origin actors
    that never heard it) or ``private`` (a whisper, telepathy or a withheld action, never the table's)."""

    where: str
    reason: str
    missed: tuple[InstanceRef, ...] = ()


@dataclass
class TransferPlan:
    """A planned copy: the new ``chat`` in the target, how each origin actor maps (``None``: left out), the pages
    it writes, which origin post each copied post came from, what it omits and what it leaves in the origin."""

    origin: ChatRef
    chat: ChatRef
    mapping: Mapping[InstanceRef, InstanceRef | None]
    pages: tuple[TranscriptPage, ...]
    copied: tuple[tuple[str, str], ...]
    omitted: tuple[Omitted, ...]
    left: tuple[Left, ...]
    notes: tuple[tuple[str, str], ...]
    change: ChatChange
    _origin_state: tuple[object, ...] = field(repr=False, default=())
    _read_origin: Callable[[], tuple[object, ...]] | None = field(repr=False, default=None)
    provider_calls: int = 0

    def preview(self) -> dict[str, bytes]:
        return self.change.preview()

    def commit(self, approvals: Mapping[str, str] | None = None) -> ChatCommit:
        """Write the copy, if the origin is still what was copied (``StaleWrite`` otherwise: plan again)."""
        store = self.change.library.store
        with store.lock:  # the origin cannot change between this check and the commit
            if self._read_origin is not None and self._read_origin() != self._origin_state:
                raise StaleWrite([(str(self.origin), "changed since the copy was planned")])
            return self.change.commit(approvals)


class SceneTransfer:
    """Cross-campaign scene copy over one open, writable ``LibraryStore`` (trusted application code)."""

    def __init__(self, store: LibraryStore, handle: object, *, clock: Callable[[], str],
                 new_id: Callable[[str], str], owner: str | None = None) -> None:
        self.store = store
        self.handle = handle
        self._clock = clock
        self._new_id = new_id
        self._owner = owner
        self._scenes = SceneLibrary(store, handle, owner=owner)

    # -- reading ---------------------------------------------------------------------------------

    def scan(self, origin: ChatRef, target: ScopeRef) -> TransferScan:
        """What copying ``origin`` into ``target`` needs decided. ``TransferRefused`` for a chat or campaign that
        cannot be read, or a target that is the origin's own campaign (that is a fork, not a transfer)."""
        return self._scan(origin, target)[0]

    def _scan(self, origin: ChatRef, target: ScopeRef) -> tuple[TransferScan, ChatRecord, Mapping[InstanceRef, str]]:
        if type(origin) is not ChatRef or type(target) is not ScopeRef:
            raise TransferRefused("origin is a ChatRef and target a ScopeRef", "invalid_field")
        if origin.campaign_id == target.campaign_id:
            raise TransferRefused("a copy goes to another campaign; within one campaign, fork the chat",
                                  "same_campaign")
        if self.store.campaign_presence(target.campaign_id) != "present":
            raise TransferRefused(f"{target} does not exist", "missing_scope")
        view = self.store.load()
        try:
            captured = chat_snapshot(view, origin)
        except RecordNotFound:
            raise TransferRefused(f"{origin} does not exist", "missing_chat") from None
        except ChatCodecError:
            raise TransferRefused(f"{origin} cannot be read by this version", "origin_unreadable") from None
        record, snapshot = captured.chat, captured.snapshot
        names: dict[InstanceRef, str] = {}
        targets = _target_actors(view, target.campaign_id)
        cast = []
        for entry in record.roster:
            definition = _definition(record, entry.actor) or entry.source
            problem = _snapshot_problem(captured.problems, entry.actor)
            try:  # an actor of a detached binding is outside the snapshot's selection: its definition names it
                name = snapshot.entity(entry.actor).name
            except Exception:  # 022's refusals
                name = _definition_name(view, definition)
            if problem is None and (definition is None or _definition_name(view, definition) is None):
                problem = "missing_definition"
            if name is not None:
                names[entry.actor] = name
            name = name or entry.actor.instance_id
            key = None if definition is None else definition.entity_key
            candidates = tuple(sorted((ref for ref, (source, _) in targets.items()
                                       if key is not None and source is not None and source.entity_key == key
                                       and ref.kind == entry.actor.kind), key=str))
            namesakes = tuple(sorted((ref for ref, (source, label) in targets.items()
                                      if ref not in candidates and label is not None
                                      and name_key(label) == name_key(name)), key=str))
            active = any(binding.actor == entry.actor for binding in record.active_bindings())
            cast.append(CastMember(entry.actor, entry.role, definition, name, active, candidates, namesakes, problem))
        sources = tuple(SourceChoice(binding.ref, binding.role, _broken(self.store, binding.ref))
                        for role in _SOURCE_ROLES for binding in record.active_bindings(role))
        pages, page_problems = history_pages(self.store, origin)
        turns = self._scenes.turns(origin)
        history_problems = [problem for problem in page_problems if problem[0] == "imported_history_unreadable"]
        history_problems += [("turns", f"{path}:{code}") for path, code in turns.problems]
        if turns.fork is not None:
            history_problems.append(("fork", turns.fork.describe()))
        history_problems += [("conflicted", turn_id) for turn_id in turns.conflicted]
        notes = [("snapshot", problem) for problem in captured.problems]
        world = _world(view, target)
        if (captured.scope.world.source if captured.scope and captured.scope.world else None) != world:
            notes.append(("world_differs", "the target campaign plays another world, or none"))
        history = {"pages": len(pages), "page_posts": sum(len(page.posts) for page in pages),
                   "turns": len(turns.turns)}
        scan = TransferScan(origin, target, tuple(cast), sources, history, tuple(history_problems), tuple(notes))
        return scan, record, names

    # -- planning --------------------------------------------------------------------------------

    def plan(self, origin: ChatRef, target: ScopeRef, *, cast: Mapping[InstanceRef, InstanceRef | str],
             sources: Sequence[SourceRef] | None = None, label: str | None = None) -> TransferPlan:
        """Plan the copy. ``cast`` maps **every** origin roster actor to a target actor of the same definition
        entity (reused), ``NEW`` or ``LEAVE``. ``sources`` are the origin's scenario and lorebook bindings to copy
        (all of them by default, as the origin pins them). ``label`` names the new chat."""
        scan, record, names = self._scan(origin, target)
        if scan.history_problems:
            raise TransferRefused("the origin's history cannot all be read now: "
                                  + "; ".join(f"{kind} {detail}" for kind, detail in scan.history_problems),
                                  "history_incomplete")
        if label is not None and (type(label) is not str or len(label) > 1000):
            raise TransferRefused("label must be text of at most 1000 characters", "invalid_field")
        mapping = self._decide(scan, cast)
        chosen = self._sources(scan, sources)
        launch, attach, persona, reuse, actors = self._bindings(scan, mapping, chosen)
        library = ChatLibrary(self.store, self.handle, clock=self._clock, new_id=self._new_id, owner=self._owner)
        try:
            change = library.start_chat(launch, attach=attach, persona=persona, label=label, scope=target,
                                        reuse_actors=reuse, actors=actors)
        except InvalidBinding as error:
            raise TransferRefused(str(error), error.reason) from None
        planned = _planned_record(change)
        targets = self._mapped(scan, mapping, planned)
        state = self._origin_state(origin)
        pages_found, _ = history_pages(self.store, origin)
        turns = self._scenes.turns(origin)
        items = page_items(origin, pages_found, imported_cast(record))
        played, left = turn_items(origin, turns.records(), names)
        carried = frozenset(actor for actor, decided in targets.items() if decided is not None)
        copy, omitted = _select((*items, *played), carried, left)
        posts, copied = _remap(copy, targets)
        notes = [*scan.notes, *_revision_notes(scan, mapping, planned)]
        pages = self._pages(change.chat, posts, origin, targets)
        puts = tuple((ScopeRecord(page.ref, TRANSCRIPT_SCHEMA, page_to_payload(page), owner=self._owner), None)
                     for page in pages)
        change = dataclasses.replace(change, puts=change.puts + puts)
        assert change.chat is not None  # start_chat always plans a chat
        return TransferPlan(origin, change.chat, targets, pages, copied, omitted,
                            tuple(item for item in left if item.kind != "private_post"), tuple(notes), change,
                            state, lambda: self._origin_state(origin))

    def _decide(self, scan: TransferScan, cast: Mapping[InstanceRef, InstanceRef | str]
                ) -> dict[InstanceRef, InstanceRef | str]:
        """The caller's decision for every roster actor, checked: nothing undecided, nothing merged, no
        reuse of an actor that is not the same individual, nothing created that cannot be."""
        if not isinstance(cast, Mapping):
            raise TransferRefused("cast maps each origin actor to a decision", "invalid_field")
        members = {member.actor: member for member in scan.cast}
        for actor in cast:
            if actor not in members:
                raise TransferRefused(f"{actor} is not an actor of {scan.origin}", "unknown_actor", actor)
        decided: dict[InstanceRef, InstanceRef | str] = {}
        reused: dict[InstanceRef, InstanceRef] = {}
        for member in scan.cast:
            if member.actor not in cast:
                raise TransferRefused(f"{member.actor} ({member.name}) has no reviewed decision: map it to a "
                                      f"target actor, {NEW!r} or {LEAVE!r}", "unreviewed_actor", member.actor)
            choice = cast[member.actor]
            if choice == LEAVE and type(choice) is str:
                decided[member.actor] = LEAVE
                continue
            if choice == NEW and type(choice) is str:
                if member.definition is None or member.problem is not None:
                    raise TransferRefused(f"{member.actor} cannot be created: the origin cannot show it "
                                          f"({member.problem})", "cannot_create", member.actor)
                if role_rule(member.role) is None or role_rule(member.role).by_instance:
                    raise TransferRefused(f"{member.actor} is an existing creature (033): reuse a target "
                                          "instance or leave it out", "cannot_create", member.actor)
                decided[member.actor] = NEW
                continue
            if type(choice) is not InstanceRef:
                raise TransferRefused(f"{member.actor} maps to a target actor, {NEW!r} or {LEAVE!r}",
                                      "invalid_field", member.actor)
            if choice.campaign_id != scan.target.campaign_id:
                raise TransferRefused(f"{choice} is not an actor of {scan.target}", "foreign_actor", member.actor)
            if choice not in member.candidates:
                reason = "different_individual" if choice in member.namesakes else "actor_mismatch"
                raise TransferRefused(f"{choice} is not an instance of {member.actor}'s definition; a name or an "
                                      "alternate portrayal is never the same individual", reason, member.actor)
            if choice in reused:
                raise TransferRefused(f"{reused[choice]} and {member.actor} both map to {choice}; two individuals "
                                      "of the origin are never merged", "merged_actors", member.actor)
            reused[choice] = member.actor
            decided[member.actor] = choice
        return decided

    def _sources(self, scan: TransferScan, sources: Sequence[SourceRef] | None) -> tuple[SourceChoice, ...]:
        if sources is None:
            return scan.sources
        if isinstance(sources, (str, bytes)) or not all(isinstance(ref, SourceRef) for ref in sources):
            raise TransferRefused("sources are SourceRefs the origin binds", "invalid_field")
        bound = {choice.ref: choice for choice in scan.sources}
        missing = [ref for ref in sources if ref not in bound]
        if missing:
            raise TransferRefused(f"the origin binds no {missing[0]} as a scenario or lorebook now", "unknown_source")
        return tuple(bound[ref] for ref in dict.fromkeys(sources))

    def _bindings(self, scan: TransferScan, mapping: Mapping[InstanceRef, InstanceRef | str],
                  sources: Sequence[SourceChoice]):  # type: ignore[no-untyped-def]
        """107's start: the launch, attachments, persona, reuse map and creature actors the decisions make.

        A new actor binds the very revision the origin pinned. A reused actor binds its definition unpinned, so
        107 pins it to the revision *that* actor plays in the target (042's rule), never one nobody reviewed
        for it there."""
        characters: list[SourceRef] = []
        persona: SourceRef | None = None
        reuse: dict[tuple[str, tuple[str, str, str]], InstanceRef] = {}
        creatures: list[tuple[SourceRef, InstanceRef]] = []
        seen: dict[tuple[str, tuple[str, str, str]], InstanceRef] = {}
        for member in scan.cast:
            choice = mapping[member.actor]
            if choice == LEAVE and type(choice) is str:
                continue
            definition = member.definition
            if definition is None:  # unreachable for a candidate (it shares the definition): kept as a guard
                raise TransferRefused(f"{member.actor} has no definition in the origin to bind", "cannot_create",
                                      member.actor)
            ref = definition if choice == NEW else definition.unpinned()
            rule = role_rule(member.role)
            if rule is not None and rule.by_instance:
                creatures.append((ref, choice))  # type: ignore[arg-type]
                continue
            key = (member.role, definition.entity_key)
            if key in seen:
                raise TransferRefused(f"{seen[key]} and {member.actor} are one {member.role} binding of "
                                      f"{definition.unpinned()}; a chat binds it once", "same_definition_twice",
                                      member.actor)
            seen[key] = member.actor
            if member.role == "persona":
                if persona is not None:
                    raise TransferRefused("two origin personas are carried over; a chat has one: leave one out",
                                          "second_persona", member.actor)
                persona = ref
            else:
                characters.append(ref)
            if type(choice) is InstanceRef:
                reuse[key] = choice
        attach: list[tuple[SourceRef, str]] = [(ref, "character") for ref in characters[1:]]
        attach += [(choice.ref, choice.role) for choice in sources]
        if characters:
            launch = Launch("character", characters[0])
        elif sources:
            launch = Launch(sources[0].role, sources[0].ref)
            attach = attach[1:]
        else:
            launch = Launch("premise", premise=COPIED_PREMISE)
        return launch, attach, persona, reuse, creatures

    def _mapped(self, scan: TransferScan, mapping: Mapping[InstanceRef, InstanceRef | str], planned: ChatRecord
                ) -> dict[InstanceRef, InstanceRef | None]:
        """Each origin actor's target actor as 107 planned it (a new actor's allocated id), or ``None``."""
        found: dict[InstanceRef, InstanceRef | None] = {}
        bound = {(binding.role, binding.ref.entity_key): binding.actor for binding in planned.bindings
                 if binding.actor is not None}
        for member in scan.cast:
            choice = mapping[member.actor]
            if choice == LEAVE and type(choice) is str:
                found[member.actor] = None
            elif type(choice) is InstanceRef:
                found[member.actor] = choice
            else:
                assert member.definition is not None
                found[member.actor] = bound[(member.role, member.definition.entity_key)]
        return found

    def _pages(self, chat: ChatRef, posts: Sequence[Post], origin: ChatRef,
               targets: Mapping[InstanceRef, InstanceRef | None]) -> tuple[TranscriptPage, ...]:
        if not posts:
            return ()
        head = {"format": FORMAT, "from": str(origin),
                "cast": {str(actor): None if mapped is None else str(mapped) for actor, mapped in targets.items()},
                "posts": len(posts)}
        try:
            first = len(render({"origin": {**head, "pages": 10**9}}))
        except InvalidEnvelope as error:
            raise TransferRefused(f"the copy's cast table cannot be stored: {error}", "origin_too_large") from None
        if first > ORIGIN_BUDGET:
            raise TransferRefused(f"the copy's cast table takes more than {ORIGIN_BUDGET} bytes", "origin_too_large")
        try:
            groups = paginate(list(posts), measure_post, PAGE_BUDGET, first_budget=PAGE_BUDGET - first)
        except TranscriptError as error:
            raise TransferRefused(str(error), error.reason) from None
        except InvalidEnvelope:
            raise TransferRefused("a post is too large for one transcript page", "post_too_large") from None
        head["pages"] = len(groups)
        return tuple(TranscriptPage(TranscriptRef(chat.campaign_id, chat.chat_id, seq), tuple(group),
                                    head if seq == 1 else {"format": FORMAT, "from": str(origin)})
                     for seq, group in enumerate(groups, start=1))

    def _origin_state(self, origin: ChatRef) -> tuple[object, ...]:
        """What the copy rests on, as etags: the origin chat, its pages and its turns (set-aside ones too)."""
        try:
            chat = self.store.read(origin).etag
        except (RecordNotFound, StoreError):
            chat = None
        found = self.store.chat_transcript(origin)
        pages = tuple(sorted((str(ref), stored.etag) for ref, stored in found.records.items()))
        turns = self._scenes.turns(origin)
        played = tuple(sorted((stored.record.turn_id, stored.etag) for stored in (*turns.turns, *turns.set_aside)))
        problems = (tuple(str(problem) for problem in found.problems), turns.problems, turns.conflicted)
        return chat, pages, played, problems


# -- helpers -------------------------------------------------------------------------------------

def _select(items: Iterable[HistoryItem], carried: frozenset[InstanceRef], left: Iterable[Left]
            ) -> tuple[tuple[HistoryItem, ...], tuple[Omitted, ...]]:
    """The items every carried-over origin actor heard (or no actor heard: the table's only), in order, and the
    rest, omitted with who missed them. A private post is omitted too, as ``turn_items`` left it."""
    copy, omitted = [], [Omitted(item.where, "private") for item in left if item.kind == "private_post"]
    for item in items:
        if item.heard_by is TABLE_ONLY:
            copy.append(item)
            continue
        missed = carried - item.heard_by
        if missed:
            omitted.append(Omitted(item.where, "unheard", tuple(sorted(missed, key=str))))
        else:
            copy.append(item)
    return tuple(copy), tuple(omitted)


def _remap(items: Sequence[HistoryItem], targets: Mapping[InstanceRef, InstanceRef | None]
           ) -> tuple[tuple[Post, ...], tuple[tuple[str, str], ...]]:
    """Each copied post with new ids in order (``p000001``...), its speaker's actor mapped (or named by its label
    alone), and where in the origin it came from."""
    posts, copied = [], []
    for number, item in enumerate(items, start=1):
        post = item.post
        speaker = post.speaker
        actor = targets.get(speaker.actor) if speaker.actor is not None else None
        post_id = f"p{number:06d}"
        posts.append(dataclasses.replace(post, post_id=post_id, speaker=Speaker(speaker.role, actor, speaker.label)))
        copied.append((item.where, post_id))
    return tuple(posts), tuple(copied)


def _revision_notes(scan: TransferScan, mapping: Mapping[InstanceRef, InstanceRef | str], planned: ChatRecord
                    ) -> list[tuple[str, str]]:
    """A reused actor that plays another revision of its definition in the target than the origin played."""
    notes = []
    bound = {binding.actor: binding.ref for binding in planned.bindings if binding.actor is not None}
    for member in scan.cast:
        choice = mapping[member.actor]
        if type(choice) is InstanceRef and member.definition is not None:
            plays = bound.get(choice)
            if plays is not None and plays != member.definition:
                notes.append(("revision_differs", f"{member.actor} played {member.definition}; {choice} plays {plays}"))
    return notes


def _definition(record: ChatRecord, actor: InstanceRef) -> SourceRef | None:
    """The definition the chat bound ``actor`` to: its active binding's, else its latest binding's."""
    bindings = [binding for binding in record.bindings if binding.actor == actor]
    active = [binding for binding in bindings if binding.active]
    found = (active or bindings)
    return max(found, key=lambda binding: binding.introduced_seq).ref if found else None


def _target_actors(view, campaign_id: str) -> dict[InstanceRef, tuple[SourceRef | None, str | None]]:  # type: ignore[no-untyped-def]
    """Every instance of the target campaign: the definition it derives from and that definition's name."""
    found: dict[InstanceRef, tuple[SourceRef | None, str | None]] = {}
    for ref, stored in view.instances.items():
        if ref.campaign_id != campaign_id:
            continue
        try:
            source = SourceRef.parse(stored.envelope.derived_from)
        except (TypeError, ValueError):
            found[ref] = (None, None)
            continue
        found[ref] = (source, _definition_name(view, source))
    return found


def _world(view, scope: ScopeRef) -> str | None:  # type: ignore[no-untyped-def]
    stored = view.scopes.get(scope)
    if stored is None:
        return None
    try:
        found = scope_from_payload(scope, stored.record.schema, stored.record.payload)
    except ChatCodecError:
        return None
    return found.world.source if found.world is not None else None


def _broken(store: LibraryStore, ref: SourceRef) -> str | None:
    found = store.check_definition(ref)
    return found if isinstance(found, str) else None


def _snapshot_problem(problems: Iterable[str], actor: InstanceRef) -> str | None:
    """What the origin's snapshot says is wrong with ``actor``'s own file (``missing_actor``...), or ``None``."""
    for problem in problems:
        code, _, named = problem.partition(":")
        if named == str(actor):
            return code
    return None


def _definition_name(view, ref: SourceRef | None) -> str | None:  # type: ignore[no-untyped-def]
    if ref is None:
        return None
    try:
        name = view.index.resolve(ref).name
    except LookupError:
        return None
    return name if isinstance(name, str) else None


def _planned_record(change: ChatChange) -> ChatRecord:
    for envelope, _ in change.puts:
        if isinstance(envelope, ScopeRecord) and envelope.address == change.chat:
            return chat_from_payload(change.chat, envelope.schema, envelope.payload)  # type: ignore[arg-type]
    raise StaleWrite([("chat", "missing from the planned change")])  # pragma: no cover - start_chat writes it


__all__ = ["COPIED_PREMISE", "FORMAT", "LEAVE", "NEW", "CastMember", "Omitted", "SceneTransfer", "SourceChoice",
           "TransferPlan", "TransferRefused", "TransferScan"]
