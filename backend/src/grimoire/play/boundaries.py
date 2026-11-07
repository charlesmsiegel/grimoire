"""016's session zero in play (035 milestone 2): read a chat's stored configuration, present by it, change it.

The configuration is the chat's play scope's ``session_zero`` (107 keeps it verbatim; ``grimoire.core.
boundaries_codec`` reads it). Every call that shows narration to the table (``begin``, ``generate``, ``label``
and their landing, ``keep``) asks ``check_presented`` with the very lines it is about to show, through 016's own
``decide_presentation``, for the table's authenticated audience (read again under the store lock where it
commits): anything but ``present`` holds it (``held_by_boundary``). No theme labeller exists yet, so every line
is an unlabelled 016 segment, and 016 holds an unlabelled segment whenever any limit stricter than ``allow`` is
in force for the audience. A configuration that cannot be read (its scope, its codec, its context, or a sync
conflict's other version of it) is ``session_zero_unreadable``: nothing is shown or written, never a run with an empty
configuration.

A boundary change comes only from the player-input channel: ``change`` takes 016's ``BoundaryChange`` from an
authenticated table member, who must be the change's ``requested_by``, applies it with 016's
``SessionZero.apply`` (its ownership rules) to the configuration and the table read under the store lock, and
writes the scope by compare-and-swap with the next revision. Beside 016's rules, the library's owner may withdraw
a group boundary whose setter is not at the table, so a departed setter never locks the chat. See
docs/SCENES.md#session-zero-016.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from grimoire.chats import ChatCodecError, scope_from_payload
from grimoire.chatstore import ChatLibrary
from grimoire.core.boundaries import (
    Boundary, BoundaryAuthorityError, BoundaryChange, BoundaryContext, NarrationSegment, SessionZero, VisibleBoundary,
    decide_presentation, owner_boundaries, visible_boundaries,
)
from grimoire.core.boundaries_codec import (
    SessionZeroCodecError, StoredSessionZero, session_zero_from_payload, session_zero_to_payload,
)
from grimoire.core.identity import ChatRef
from grimoire.peersync.pairing import pairing_ended
from grimoire.scene import SceneLine, SceneRefused
from grimoire.store import RecordNotFound, StaleWrite
from grimoire.store.layout import address_parts
from grimoire.store.replica import parse_exact
from grimoire.sync.turns import open_conflicts

from .scene import _refuse
from .tracing import span

HELD = "held_by_boundary"
UNREADABLE = "session_zero_unreadable"
#: 016's caller duty: a boundary id names nothing (a fresh ``set`` of a taken id is refused, so a guessable id
#: would confirm another player's boundary). A new boundary set through the runner has an id of this form.
OPAQUE_ID = re.compile(r"b-[0-9a-f]{32}")


def new_boundary_id() -> str:
    """A fresh opaque boundary id (128 random bits), the only kind ``change`` accepts in a ``set``."""
    return f"b-{secrets.token_hex(16)}"


@dataclass(frozen=True, slots=True)
class _InForce:
    """One version of the chat's configuration (this device's, or a sync conflict's other one) and its world."""

    config: SessionZero
    world_id: str | None


@dataclass(frozen=True, slots=True, repr=False)
class Boundaries:
    """What one table member may see of the chat's configuration: every boundary in force for the table,
    anonymised (016's ``visible_boundaries``), the member's own in full (``owner_boundaries``, private notes
    included), the stored ``revision`` (0 with none), and whether narration is ``held`` for the table now."""

    revision: int
    visible: tuple[VisibleBoundary, ...]
    own: tuple[Boundary, ...]
    held: bool

    def __repr__(self) -> str:  # never a private note
        return f"Boundaries(revision={self.revision}, {len(self.visible)} visible, {len(self.own)} own)"


def _unreadable(message: str):
    return _refuse(UNREADABLE, message)


def _decoded(value: object) -> StoredSessionZero:
    try:
        return session_zero_from_payload(value)
    except SessionZeroCodecError as error:
        raise _unreadable(f"the chat's session zero cannot be read ({error.reason}); nothing is shown") from None


def _scope_path(chat: ChatRef) -> str:
    return "/".join(address_parts(chat.scope))


def _own(runner, chat: ChatRef):
    """This device's scope (``None`` for a chat with no scope record: no configuration, as before 107), its etag,
    and its decoded configuration (``None`` when it has none)."""
    try:
        found = runner.store.read(chat.scope)
    except RecordNotFound:
        return None, None, None
    except Exception:  # the store cannot read it: whatever it holds is unknown
        raise _unreadable("the chat's play scope cannot be read; nothing is shown") from None
    try:
        scope = scope_from_payload(chat.scope, found.record.schema, found.record.payload)
    except ChatCodecError:
        raise _unreadable("the chat's play scope cannot be read; nothing is shown") from None
    stored = None if scope.session_zero is None else _decoded(scope.session_zero)
    return scope, found.etag, stored


def _world(scope) -> str | None:
    return None if scope is None or scope.world is None else scope.world.source


def in_force(runner, chat: ChatRef) -> tuple[_InForce, ...]:
    """Every version of the chat's configuration that may be in force: this device's, and the other device's
    version of the scope that sync kept beside it while a keep-both conflict on it stands (019: each device reads
    its own until it is resolved, so the stricter of the two must hold here too). Empty with none.
    ``session_zero_unreadable`` when any of them cannot be read, names a world boundary while its chat is in no
    world, or this device's sync records cannot say."""
    scope, _, stored = _own(runner, chat)
    found = [] if stored is None else [_InForce(stored.config, _world(scope))]
    path = _scope_path(chat)
    # the scope file alone: its campaign's turns are 028's (``turn_conflict``), and their kept bytes are not read here
    kept, complete = open_conflicts(runner.store, path, [path], ended=lambda peer: pairing_ended(runner.store, peer),
                                    require_kept=True)
    if not complete:
        raise _unreadable("this device's sync records cannot be read, or a version they keep is gone, so another "
                          "version of the chat's session zero may stand; nothing is shown")
    for data in kept.get(path, ()):
        try:
            record = parse_exact(tuple(path.split("/")), data)
            other = scope_from_payload(chat.scope, record.schema, record.payload)
        except Exception:  # damaged, or a format this version cannot read: fail closed
            raise _unreadable("another device's version of the chat's play scope cannot be read") from None
        if other.session_zero is not None:
            found.append(_InForce(_decoded(other.session_zero).config, _world(other)))
    for version in found:  # 016 resolves no world boundary of a chat in no world (and no setting exists yet)
        if any(b.scope == "setting" or (b.scope == "world" and version.world_id is None)
               for b in version.config.boundaries):
            raise _unreadable("the chat's session zero names a world or setting it is not in; nothing is shown")
    return tuple(found)


def _segments(chat: ChatRef, lines: Iterable[SceneLine] | None) -> tuple[NarrationSegment, ...]:
    """016 segments for ``lines``, each unlabelled (no theme labeller yet) and proposed; ``None`` is a probe for
    narration not yet written (before any call is billed)."""
    if lines is None:
        return (NarrationSegment(chat.campaign_id, "probe", "probe", None, "proposed"),)
    return tuple(NarrationSegment(chat.campaign_id, line.line_id, line.text, None, "proposed") for line in lines)


def _held(versions: tuple[_InForce, ...], chat: ChatRef, audience: frozenset[str],
          lines: Iterable[SceneLine] | None, complete: bool = True) -> bool:
    """Whether any version holds ``lines`` for ``audience``. A table that could not be read in full (``complete``
    false) may seat anyone: every player with a boundary in that version is then taken as at the table."""
    segments = _segments(chat, lines)
    if not segments:
        return False
    for version in versions:
        table = audience if complete else audience | {b.scope_id for b in version.config.boundaries
                                                       if b.scope == "player"}
        try:
            context = BoundaryContext(chat.campaign_id, table, world_id=version.world_id)
            decisions = decide_presentation(version.config, context, segments)
        except ValueError:  # a world or setting boundary this chat is in no context for: 016 fails closed
            raise _unreadable("the chat's session zero names a world or setting it is not in; nothing is shown") \
                from None
        if any(decision.action != "present" for decision in decisions):
            return True
    return False


def check_presented(runner, chat: ChatRef, audience: frozenset[str],
                    lines: Iterable[SceneLine] | None = None, *, complete: bool = True) -> None:
    """Refuse ``held_by_boundary`` unless 016 presents every one of ``lines`` (``None``: any narration at all) to
    ``audience`` under every version of the chat's configuration; ``session_zero_unreadable`` when one cannot be
    read. Read from the store each time, so under the store lock it is the configuration a commit lands over.
    ``complete`` false: who is at the table could not be read in full, so it is never judged by ``audience``
    alone (``_held``)."""
    with span("read.session_zero"):
        held = _held(in_force(runner, chat), chat, audience, lines, complete)
    if held:
        raise _refuse(HELD, "a session-zero limit is in force for this table, and no theme labeller labels "
                            "narration yet: 016 holds unlabelled narration while any limit applies")


def view(runner, chat: ChatRef, principal: str, audience: frozenset[str], complete: bool = True) -> Boundaries:
    """``Boundaries`` for ``principal`` at a table of ``audience`` (read in full, or not: ``complete``), from this
    device's configuration."""
    scope, _, stored = _own(runner, chat)
    config = SessionZero() if stored is None else stored.config
    try:
        context = BoundaryContext(chat.campaign_id, audience, world_id=_world(scope))
        visible = visible_boundaries(config, context)
    except ValueError:
        raise _unreadable("the chat's session zero names a world or setting it is not in") from None
    try:
        held = _held(in_force(runner, chat), chat, audience, None, complete)
    except SceneRefused:  # another version cannot be read: narration is held (fail closed), the view still shows
        held = True
    return Boundaries(0 if stored is None else stored.revision, visible, owner_boundaries(config, principal), held)


def _abandoned(config: SessionZero, requested: BoundaryChange, context: BoundaryContext, owner: bool,
               complete: bool) -> SessionZero | None:
    """The owner's withdrawal of a group boundary whose setter is not at the table (as ``resume`` lifts a pause
    whose pauser left): ``config`` without it, else ``None``. Only while the table could be read in full, and
    never a player boundary, which stays its player's wherever they are."""
    if not (owner and complete and requested.kind == "withdraw"):
        return None
    found = next((b for b in config.boundaries if b.boundary_id == requested.boundary_id), None)
    if found is None or found.scope == "player" or found.set_by in context.audience or found.scope_id != {
            "campaign": context.campaign_id, "world": context.world_id}.get(found.scope):
        return None
    return SessionZero(tuple(b for b in config.boundaries if b is not found))


def change(runner, chat: ChatRef, principal: str, table: Callable[[], tuple[frozenset[str], bool]],
           requested: BoundaryChange) -> Boundaries:
    """Apply one participant's 016 change to the chat's configuration and store it (see the module docstring).
    ``table`` reads the table's audience afresh, and whether it could be read in full; it is called under the
    store lock, so the change is judged for the table as it stands when it is written."""
    if type(requested) is not BoundaryChange:
        raise _refuse("invalid_field", "a boundary change is a BoundaryChange")
    if requested.requested_by != principal:  # 016's player-input channel: the authenticated speaker, never another
        raise _refuse("not_your_boundary", "not your boundary")
    if requested.kind == "set" and OPAQUE_ID.fullmatch(requested.boundary.boundary_id) is None:
        # checked on every set, before anything stored is read, so its answer depends on nothing hidden
        raise _refuse("invalid_field", "a boundary set here has an opaque id (new_boundary_id())")
    if requested.kind == "set" and requested.boundary.scope == "setting":
        raise _refuse("invalid_field", "no setting exists for a chat to be in yet; set a campaign or world boundary")
    library = ChatLibrary(runner.store, runner.handle, clock=_no_clock, new_id=_no_id, owner=runner.owner)
    with runner.store.lock:  # read, apply and write as one step: no other change lands between them
        scope, etag, stored = _own(runner, chat)
        if scope is None:
            raise _refuse("no_scope", "this chat has no play scope to hold a session zero")
        config = SessionZero() if stored is None else stored.config
        audience, complete = table()
        try:
            context = BoundaryContext(chat.campaign_id, audience, world_id=_world(scope))
            changed = config.apply(requested, context)
        except BoundaryAuthorityError:
            changed = _abandoned(config, requested, context, principal in runner.authority.owners, complete)
            if changed is None:
                raise _refuse("not_your_boundary", "not your boundary") from None
        except ValueError as error:  # 016's shape checks; its messages name no owner or id
            raise _refuse("invalid_field", str(error)[:200]) from None
        try:
            payload = session_zero_to_payload(changed, 1 if stored is None else stored.revision + 1)
        except SessionZeroCodecError as error:
            raise _refuse(error.reason, str(error)) from None
        try:
            library.set_session_zero(chat.scope, payload, expected=etag).commit()
        except StaleWrite:  # written by another process since this read: nothing landed
            raise _refuse("scope_changed", "the chat's play scope changed meanwhile; ask again") from None
    return view(runner, chat, principal, *table())


def _no_clock() -> str:  # a scope change records no time
    raise AssertionError("a session-zero change reads no clock")


def _no_id(prefix: str) -> str:
    raise AssertionError("a session-zero change allocates no id")


__all__ = ["HELD", "OPAQUE_ID", "UNREADABLE", "Boundaries", "change", "check_presented", "in_force",
           "new_boundary_id", "view"]
