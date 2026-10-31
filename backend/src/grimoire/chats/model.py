"""World-optional play scopes and chats: pure, frozen values and their transitions (Sprint 107).

A **chat** is one conversation thread with a stable ``ChatRef``. It lives in exactly one
**play scope**, the campaign namespace that gives its actors their ``InstanceRef``s. A world
is optional (``world=None`` is the default, not an error).

- The launch is immutable provenance.
- Source bindings are mutable, versioned associations. Detaching one writes a tombstone and never
  deletes the binding.
- The roster only grows: detaching a source never erases an actor's identity or history.

Labels and titles are display text, never identity. Nothing here reads files, clocks or
randomness: timestamps and IDs are supplied by the caller.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from grimoire.core.identity import ChatRef, InstanceRef, ScopeRef, SourceRef, validate_segment, validate_source_id
from grimoire.core.json_value import JsonObject, JsonValueError, freeze_object

#: role -> the definition kind it binds. Only character and persona roles create actors.
ROLE_KINDS: Mapping[str, str] = {"character": "character", "persona": "character", "scenario": "scenario",
                                 "lorebook": "lorebook"}
ACTOR_ROLES = frozenset({"character", "persona"})
LAUNCH_KINDS = frozenset({"character", "scenario", "lorebook", "premise"})
NOT_WITNESSED = "not_witnessed"  # a new actor saw none of the chat's earlier history
CAMPAIGN_CONTINUITY = "campaign_continuity"  # a reused campaign actor keeps only its own history
PRIOR_HISTORY = frozenset({NOT_WITNESSED, CAMPAIGN_CONTINUITY})
MAX_BINDINGS = 256  # including tombstones
MAX_ROSTER = 256
MAX_LABEL = 200
MAX_PREMISE = 4000
MAX_DEPTH = 64  # nesting of opaque JSON (extras, session zero)
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_SAFE_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ChatModelError(ValueError):
    """A chat or scope value this model refuses; ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


class BindingLimit(ChatModelError):
    """A chat would exceed its binding or roster bound."""

    def __init__(self, message: str, reason: str = "binding_limit") -> None:
        super().__init__(message, reason)


def _fail(message: str, reason: str = "invalid_field") -> ChatModelError:
    return ChatModelError(message, reason)


def check_timestamp(value: object, label: str) -> None:
    if type(value) is not str or not _TIMESTAMP.fullmatch(value):
        raise _fail(f"{label} must be a UTC timestamp YYYY-MM-DDTHH:MM:SSZ: {value!r}")


def _display(value: object, label: str, limit: int) -> None:
    if value is None:
        return
    if type(value) is not str or not value.strip() or len(value) > limit or _SAFE_TEXT.search(value):
        raise _fail(f"{label} must be nonblank text of at most {limit} characters without control characters")


def _seq(value: object, label: str) -> None:
    if type(value) is not int or value < 0:
        raise _fail(f"{label} must be a nonnegative integer")


def _too_deep(value: object, limit: int) -> bool:
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if isinstance(current, Mapping):
            children = list(current.values())
        elif isinstance(current, (list, tuple)):
            children = list(current)
        else:
            continue
        if depth > limit:
            return True
        stack.extend((child, depth + 1) for child in children)
    return False


def opaque_object(value: object, label: str, reserved: frozenset[str] = frozenset()) -> JsonObject:
    """A frozen JSON object of authored data this model keeps but does not interpret."""
    if not isinstance(value, Mapping):
        raise _fail(f"{label} must be a JSON object")
    if _too_deep(value, MAX_DEPTH):
        raise _fail(f"{label} is nested more than {MAX_DEPTH} levels deep")
    clash = sorted(set(value) & reserved) if all(type(key) is str for key in value) else []
    if clash:
        raise _fail(f"{label} may not use reserved keys {clash}")
    try:
        return freeze_object(value)
    except JsonValueError as error:
        raise _fail(f"{label}: {error}") from None


# -- chat parts ---------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Launch:
    """How the chat began: a character, scenario or lorebook source, or a bare premise."""

    kind: str
    ref: SourceRef | None = None
    premise: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not str or self.kind not in LAUNCH_KINDS:
            raise _fail(f"unknown launch kind {self.kind!r}", "launch_kind")
        if self.kind == "premise":
            if self.ref is not None:
                raise _fail("a premise launch names no source", "launch_kind")
            if type(self.premise) is not str or not self.premise.strip() or len(self.premise) > MAX_PREMISE:
                raise _fail(f"a premise is nonblank text of at most {MAX_PREMISE} characters")
            return
        if self.premise is not None:
            raise _fail("only a premise launch carries premise text", "launch_kind")
        if not isinstance(self.ref, SourceRef):
            raise _fail(f"a {self.kind} launch needs a source reference", "launch_kind")
        if self.ref.kind != ROLE_KINDS[self.kind]:
            raise _fail(f"a {self.kind} launch needs a {ROLE_KINDS[self.kind]} definition, not {self.ref}",
                        "role_kind")


@dataclass(frozen=True, slots=True)
class SourceBinding:
    """One source attached to a chat under one role, with when and what it meant then."""

    ref: SourceRef
    role: str
    resolved_revision: str
    introduced_seq: int
    introduced_at: str
    actor: InstanceRef | None = None
    detached_seq: int | None = None
    detached_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ref, SourceRef):
            raise _fail("a binding needs a SourceRef")
        if type(self.role) is not str or self.role not in ROLE_KINDS:
            raise _fail(f"unknown role {self.role!r}", "role_kind")
        if self.ref.kind != ROLE_KINDS[self.role]:
            raise _fail(f"role {self.role} binds {ROLE_KINDS[self.role]} definitions, not {self.ref}", "role_kind")
        try:
            validate_segment(self.resolved_revision, "resolved_revision")
        except ValueError as error:
            raise _fail(str(error)) from None
        if self.ref.pinned and self.ref.revision != self.resolved_revision:
            raise _fail(f"a pinned binding resolves to its own revision, not {self.resolved_revision}")
        _seq(self.introduced_seq, "introduced_seq")
        check_timestamp(self.introduced_at, "introduced_at")
        if (self.actor is not None) != (self.role in ACTOR_ROLES):
            raise _fail(f"a {self.role} binding {'needs' if self.role in ACTOR_ROLES else 'has no'} actor")
        if self.actor is not None and (not isinstance(self.actor, InstanceRef) or self.actor.kind != "character"):
            raise _fail("a binding's actor is a character instance")
        if (self.detached_seq is None) != (self.detached_at is None):
            raise _fail("detached_seq and detached_at come together")
        if self.detached_seq is not None:
            _seq(self.detached_seq, "detached_seq")
            check_timestamp(self.detached_at, "detached_at")
            if self.detached_seq <= self.introduced_seq:
                raise _fail("a binding is detached after it was introduced")

    @property
    def active(self) -> bool:
        return self.detached_seq is None

    @property
    def key(self) -> tuple[str, tuple[str, str, str]]:
        return (self.role, self.ref.entity_key)


@dataclass(frozen=True, slots=True)
class RosterEntry:
    """A campaign actor that has taken part in this chat; never removed."""

    actor: InstanceRef
    role: str
    introduced_seq: int
    introduced_at: str
    source: SourceRef | None = None
    prior_history: str = NOT_WITNESSED

    def __post_init__(self) -> None:
        if not isinstance(self.actor, InstanceRef) or self.actor.kind != "character":
            raise _fail("a roster actor is a character instance")
        if type(self.role) is not str or self.role not in ACTOR_ROLES:
            raise _fail(f"a roster entry's role is one of {sorted(ACTOR_ROLES)}", "role_kind")
        if self.source is not None and not isinstance(self.source, SourceRef):
            raise _fail("a roster source is a SourceRef")
        _seq(self.introduced_seq, "introduced_seq")
        check_timestamp(self.introduced_at, "introduced_at")
        if type(self.prior_history) is not str or self.prior_history not in PRIOR_HISTORY:
            raise _fail(f"prior_history must be one of {sorted(PRIOR_HISTORY)}")


@dataclass(frozen=True, slots=True)
class NewBinding:
    """What a caller resolved before binding: the source, its revision then, and its actor."""

    ref: SourceRef
    role: str
    resolved_revision: str
    actor: InstanceRef | None = None
    prior_history: str = NOT_WITNESSED


#: Payload keys that name identity; the envelope owns them, so a payload may never repeat them.
IDENTITY_KEYS = frozenset({"schema", "address", "campaign_id", "chat_id", "ref"})
CHAT_FIELDS = frozenset({"launch", "bindings", "roster", "label", "created_at", "last_activity", "seq"})
SCOPE_FIELDS = frozenset({"title", "world", "session_zero", "created_at"})


@dataclass(frozen=True, slots=True)
class ChatRecord:
    """One chat thread's canonical metadata (no transcript: scenes and posts live elsewhere)."""

    ref: ChatRef
    launch: Launch
    created_at: str
    last_activity: str
    seq: int = 0
    bindings: tuple[SourceBinding, ...] = ()
    roster: tuple[RosterEntry, ...] = ()
    label: str | None = None
    extras: JsonObject = field(default_factory=JsonObject)

    def __post_init__(self) -> None:
        if not isinstance(self.ref, ChatRef):
            raise _fail("a chat's ref is a ChatRef")
        if not isinstance(self.launch, Launch):
            raise _fail("a chat needs a Launch")
        check_timestamp(self.created_at, "created_at")
        check_timestamp(self.last_activity, "last_activity")
        if self.last_activity < self.created_at:
            raise _fail("last_activity precedes created_at")
        _seq(self.seq, "seq")
        _display(self.label, "label", MAX_LABEL)
        object.__setattr__(self, "bindings", tuple(self.bindings))
        object.__setattr__(self, "roster", tuple(self.roster))
        object.__setattr__(self, "extras", opaque_object(self.extras, "extras", CHAT_FIELDS | IDENTITY_KEYS))
        self._check()

    def _check(self) -> None:
        if not all(isinstance(b, SourceBinding) for b in self.bindings):
            raise _fail("bindings must be SourceBinding values")
        if not all(isinstance(r, RosterEntry) for r in self.roster):
            raise _fail("roster entries must be RosterEntry values")
        if len(self.bindings) > MAX_BINDINGS:
            raise BindingLimit(f"a chat holds at most {MAX_BINDINGS} bindings, tombstones included")
        if len(self.roster) > MAX_ROSTER:
            raise BindingLimit(f"a chat's roster holds at most {MAX_ROSTER} actors", "roster_limit")
        campaign = self.ref.campaign_id
        roster: dict[InstanceRef, RosterEntry] = {}
        for entry in self.roster:
            if entry.actor in roster:
                raise _fail(f"{entry.actor} is on the roster twice", "duplicate_actor")
            if entry.actor.campaign_id != campaign:
                raise _fail(f"{entry.actor} belongs to another campaign", "foreign_actor")
            roster[entry.actor] = entry
        active: set[tuple[str, tuple[str, str, str]]] = set()
        active_actors: set[InstanceRef] = set()
        personas = 0
        latest = 0
        for binding in self.bindings:
            latest = max(latest, binding.introduced_seq, binding.detached_seq or 0)
            if binding.active:
                if binding.key in active:
                    raise _fail(f"{binding.ref.unpinned()} is already bound as {binding.role}", "duplicate_binding")
                active.add(binding.key)
                personas += binding.role == "persona"
            if binding.actor is not None:
                entry = roster.get(binding.actor)
                if entry is None or entry.role != binding.role or entry.source is None or (
                        entry.source.entity_key != binding.ref.entity_key):
                    raise _fail(f"{binding.actor} must be on the roster for {binding.ref}", "roster_mismatch")
                if binding.active:
                    if binding.actor in active_actors:
                        raise _fail(f"{binding.actor} is bound twice", "duplicate_actor")
                    active_actors.add(binding.actor)
        if personas > 1:
            raise _fail("a chat has at most one active persona", "second_persona")
        latest = max([latest, *(entry.introduced_seq for entry in self.roster)])
        if self.seq < latest:
            raise _fail("seq precedes a recorded change")
        launch = self.launch
        if launch.ref is not None and not any(
                b.introduced_seq == 0 and b.role == launch.kind and b.ref == launch.ref for b in self.bindings):
            raise _fail("the launch source is bound when the chat starts", "launch_unbound")

    # -- transitions (each returns a new record; ``ref``, ``created_at`` and ``launch`` never change) --

    @classmethod
    def start(cls, ref: ChatRef, launch: Launch, at: str, bindings: tuple[NewBinding, ...] = (),
              label: str | None = None) -> ChatRecord:
        check_timestamp(at, "at")
        bound, roster = [], []
        for new in bindings:
            bound.append(SourceBinding(new.ref, new.role, new.resolved_revision, 0, at, new.actor))
            if new.actor is not None and all(entry.actor != new.actor for entry in roster):
                roster.append(RosterEntry(new.actor, new.role, 0, at, new.ref, new.prior_history))
        return cls(ref, launch, at, at, 0, tuple(bound), tuple(roster), label)

    def _bind(self, new: NewBinding, at: str, seq: int) -> ChatRecord:
        binding = SourceBinding(new.ref, new.role, new.resolved_revision, seq, at, new.actor)
        roster = self.roster
        if new.actor is not None:
            existing = next((entry for entry in roster if entry.actor == new.actor), None)
            if existing is None:
                roster += (RosterEntry(new.actor, new.role, seq, at, new.ref, new.prior_history),)
        return replace(self, bindings=self.bindings + (binding,), roster=roster, seq=seq,
                       last_activity=max(at, self.last_activity))

    def attach(self, new: NewBinding, at: str) -> ChatRecord:
        return self._advance(at)._bind(new, at, self.seq + 1)

    def detach(self, ref: SourceRef, role: str, at: str) -> ChatRecord:
        key = (role, ref.entity_key)
        position = next((i for i, b in enumerate(self.bindings) if b.active and b.key == key), None)
        if position is None:
            raise _fail(f"{ref.unpinned()} is not bound as {role}", "not_bound")
        seq = self.seq + 1
        bindings = list(self.bindings)
        bindings[position] = replace(bindings[position], detached_seq=seq, detached_at=at)
        return replace(self._advance(at), bindings=tuple(bindings), seq=seq)

    def relabel(self, label: str | None, at: str) -> ChatRecord:
        return replace(self._advance(at), label=label, seq=self.seq + 1)

    def _advance(self, at: str) -> ChatRecord:
        check_timestamp(at, "at")
        return replace(self, last_activity=max(at, self.last_activity))

    def active_bindings(self, role: str | None = None) -> tuple[SourceBinding, ...]:
        return tuple(b for b in self.bindings if b.active and (role is None or b.role == role))

    @property
    def persona(self) -> SourceBinding | None:
        return next(iter(self.active_bindings("persona")), None)

    def roster_entry(self, actor: InstanceRef) -> RosterEntry | None:
        return next((entry for entry in self.roster if entry.actor == actor), None)


# -- play scope -------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WorldBinding:
    """An optional world source. ``observed_manifest_etag`` only detects drift; it pins nothing."""

    source: str
    observed_manifest_etag: str
    attached_at: str

    def __post_init__(self) -> None:
        try:
            validate_source_id(self.source)
        except ValueError as error:
            raise _fail(str(error)) from None
        if type(self.observed_manifest_etag) is not str or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", self.observed_manifest_etag):
            raise _fail("observed_manifest_etag must be a sha256 etag")
        check_timestamp(self.attached_at, "attached_at")


@dataclass(frozen=True, slots=True)
class PlayScope:
    """A chat's campaign namespace. Unnamed (``title=None``) it is shown as a chat, not a campaign."""

    ref: ScopeRef
    created_at: str
    title: str | None = None
    world: WorldBinding | None = None
    #: 016's configuration, kept verbatim and never interpreted here (035 executes it).
    session_zero: JsonObject | None = None
    extras: JsonObject = field(default_factory=JsonObject)

    def __post_init__(self) -> None:
        if not isinstance(self.ref, ScopeRef):
            raise _fail("a scope's ref is a ScopeRef")
        check_timestamp(self.created_at, "created_at")
        _display(self.title, "title", MAX_LABEL)
        if self.world is not None and not isinstance(self.world, WorldBinding):
            raise _fail("world must be a WorldBinding or None")
        if self.session_zero is not None:
            object.__setattr__(self, "session_zero", opaque_object(self.session_zero, "session_zero"))
        object.__setattr__(self, "extras", opaque_object(self.extras, "extras", SCOPE_FIELDS | IDENTITY_KEYS))

    @property
    def is_campaign(self) -> bool:
        """Whether the player has named this scope as a visible campaign."""
        return self.title is not None

    def named(self, title: str | None) -> PlayScope:
        return replace(self, title=title)

    def with_world(self, world: WorldBinding | None) -> PlayScope:
        return replace(self, world=world)

    def with_session_zero(self, value: Mapping[str, object] | None) -> PlayScope:
        return replace(self, session_zero=value)  # type: ignore[arg-type]
