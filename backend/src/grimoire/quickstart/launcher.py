"""Start Chat (052): one action from one selection to an open, world-less 035 chat.

``QuickStart`` composes what already exists and adds no record family: 107's ``ChatLibrary`` writes the
scope, chat and actors in one transaction; 035's ``SceneRunner`` seats control and opens with the chosen
greeting; 107's navigation finds the chats a source already has, for Continue. No model is called here:
the opening is the card's or scenario's own text, and generation waits for the player.

Every source is bound **pinned** to the revision it resolves to now (the owner's Nov 6 call, "pin by
default"): a later import of the card reaches this chat only through 042's reviewed take-or-keep rebase.
See docs/QUICKSTART.md.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.chats import ChatCodecError, ChatModelError, Launch
from grimoire.chatstore import ChatLibrary, InvalidBinding
from grimoire.core.identity import ChatRef, ScopeRef, SourceRef
from grimoire.play import SceneRunner
from grimoire.scene import SceneRefused
from grimoire.security import is_human
from grimoire.store import RecordNotFound, StaleWrite, StoreError

from .model import LaunchOptions, Opening, QuickStartRefused, Repair, Selection, Started


class QuickStart:
    """Start Chat over one open library. Trusted application code, never handed to a worker.

    ``chats`` and ``runner`` must be over the same store; ``chats`` writes with the app's trusted handle,
    as the runner does. The player acts with their own handle (``by``), which the runner authenticates."""

    def __init__(self, chats: ChatLibrary, runner: SceneRunner) -> None:
        if not isinstance(chats, ChatLibrary) or not isinstance(runner, SceneRunner):
            raise QuickStartRefused("invalid_field", "Start Chat needs a ChatLibrary and a SceneRunner")
        if chats.store is not runner.store:
            raise QuickStartRefused("invalid_field", "the chat library and the runner must share one store")
        self.chats = chats
        self.runner = runner
        self.store = runner.store

    # -- before anything is written -----------------------------------------------------------------

    def options(self, selection: Selection) -> LaunchOptions:
        """What ``selection`` offers: the pinned sources it would bind, its openings, the card's own
        lorebook and the chats already started from it. Reads only; no model runs."""
        if not isinstance(selection, Selection):
            raise QuickStartRefused("invalid_selection", "options need a Selection")
        sources, payload = self._sources(selection)
        openings = _offered(selection, payload)
        card = payload.get("card")
        lorebook = _embedded_lorebook(card) if selection.kind != "scenario" and isinstance(card, Mapping) else None
        related = (self.chats.navigation().by_source(selection.ref.unpinned())
                   if selection.ref is not None else ())
        return LaunchOptions(selection, sources, openings, _default(openings), lorebook, related)

    # -- Start Chat ---------------------------------------------------------------------------------

    def start(self, selection: Selection, by, *, persona: SourceRef | None = None, opening: str | None = None,
              label: str | None = None) -> Started:
        """A new world-less chat from ``selection``, opened and ready for the player's first post.

        One 107 transaction writes the scope, the chat (every source pinned) and its actors; then 035 seats
        the persona with the player behind ``by`` and every other character with the director, and opens
        with ``opening`` (an id from ``options``; by default the card's first greeting or the scenario's
        first opening, else the premise), which is kept: it is the player's chosen text, not a generation.
        A selection with nothing to open with starts empty. Each call is a new chat: Continue is
        ``continue_chat`` on one of ``options(...).related``."""
        if not isinstance(selection, Selection):
            raise QuickStartRefused("invalid_selection", "Start Chat needs a Selection")
        player = self._player(by)
        if persona is None and player not in self.runner.authority.owners:
            # with no persona, only the library owner is at this table (035): refuse before writing
            raise QuickStartRefused("not_at_table", "pick a persona to play, or start as the library owner")
        sources, payload = self._sources(selection)
        offered = _offered(selection, payload)
        if opening is None:
            opening = _default(offered)  # the first one that is not group-only; none: the premise, or empty
        elif opening not in {o.opening_id for o in offered}:
            raise QuickStartRefused("unknown_opening", "that greeting or opening is not offered here")
        pinned_persona = self._pin(persona, "persona") if persona is not None else None
        if selection.kind == "premise":
            launch = Launch("premise", premise=selection.premise)
        else:
            launch = Launch(selection.kind if selection.kind != "bundle" else "character", sources[0])
        attach = [(ref, "lorebook") for ref in sources[1:]]
        try:
            change = self.chats.start_chat(launch, attach=attach, persona=pinned_persona, label=label)
            chat = change.commit().chat
        except (InvalidBinding, ChatModelError) as error:
            raise QuickStartRefused(getattr(error, "reason", "invalid_binding"), str(error)[:200]) from None
        except StaleWrite:
            raise QuickStartRefused("changed", "the library changed while the chat was being started") from None
        assert chat is not None  # start_chat always names its chat
        return self._open(chat, by, player, opening)

    def continue_chat(self, chat: ChatRef, by, *, opening: str | None = None) -> Started:
        """Open an existing chat to play on: never a second chat. A launch that stopped after its chat was
        written (``Started.unfinished``) is finished here: control is seated for actors with none, and a
        chat with no turn yet is opened as ``start`` would. A chat already under way is left as it is."""
        if type(chat) is not ChatRef:
            raise QuickStartRefused("invalid_field", "continue a ChatRef")
        return self._open(chat, by, self._player(by), opening)

    def _open(self, chat: ChatRef, by, player: str, opening: str | None) -> Started:
        try:
            self.runner.seat(chat, player)
            scene = self.runner.open(chat)
            if scene.turns.last is not None:
                first = scene.records[0]
                return Started(chat, first if first.origin == "opening" else None)
            try:
                record = self.runner.begin(chat, by, opening=opening)
            except SceneRefused as error:
                if error.reason == "no_opening" and opening is None:
                    return Started(chat)  # nothing to open with: the player speaks first
                raise
            kept = self.runner.keep(chat, by, record.takes[-1].take)
        except SceneRefused as error:
            return Started(chat, unfinished=error.reason)
        return Started(chat, kept)

    # -- while playing ------------------------------------------------------------------------------

    def add_card(self, chat: ChatRef, ref: SourceRef, by, *, role: str = "character") -> Started:
        """Add a character, lorebook or scenario to a chat under way, pinned. A new character is a new
        actor that witnessed nothing (107's ``not_witnessed``; 035 lets it hear only what is said from now
        on), seated with the director. The chat's launch, premise and earlier turns are unchanged."""
        if role not in ("character", "lorebook", "scenario"):
            raise QuickStartRefused("invalid_field", "add a character, lorebook or scenario")
        player = self._player(by)
        try:
            _record, etag = self.chats.read_chat(chat)
        except (RecordNotFound, ChatCodecError, StoreError):
            raise QuickStartRefused("no_chat", f"{chat} cannot be read") from None
        pinned = self._pin(ref, role)
        try:
            self.chats.attach_source(chat, pinned, role, expected=etag).commit()
        except (InvalidBinding, ChatModelError) as error:
            raise QuickStartRefused(getattr(error, "reason", "invalid_binding"), str(error)[:200]) from None
        except StaleWrite:
            raise QuickStartRefused("changed", "the chat changed meanwhile; try again") from None
        try:
            self.runner.seat(chat, player)
        except SceneRefused as error:
            return Started(chat, unfinished=error.reason)
        return Started(chat)

    def name_campaign(self, chat: ChatRef, title: str | None) -> ScopeRef:
        """Name the chat's play scope as a campaign (or clear the name): presentation only. Its chat id,
        actor ids, launch and history are unchanged (107)."""
        try:
            scope, etag = self.chats.read_scope(chat.scope)
            self.chats.name_scope(scope.ref, title, expected=etag).commit()
        except (RecordNotFound, ChatCodecError, StoreError):
            raise QuickStartRefused("no_chat", f"{chat}'s scope cannot be read") from None
        except (InvalidBinding, ChatModelError, ValueError) as error:
            raise QuickStartRefused(getattr(error, "reason", "invalid_field"), str(error)[:200]) from None
        return scope.ref

    # -- a source gone ------------------------------------------------------------------------------

    def repairs(self, chat: ChatRef) -> tuple[Repair, ...]:
        """Every source the chat binds that does not resolve now, with the player's choices. Reads only."""
        try:
            view = self.chats.open_chat(chat)
        except (RecordNotFound, ChatCodecError, StoreError):
            raise QuickStartRefused("no_chat", f"{chat} cannot be read") from None
        found = []
        for broken in view.broken:
            role = broken.field.split(".", 1)[1] if broken.field.startswith("bindings.") else None
            if role is not None:
                choices: tuple[str, ...] = ("detach",)
            elif broken.field == "world":
                choices = ("detach_world",)
            else:
                choices = ()  # the launch (provenance) and a missing actor: reported, never rebound
            found.append(Repair(broken.field, broken.text, broken.reason, role, choices))
        return tuple(found)

    def detach_broken(self, chat: ChatRef, repair: Repair) -> None:
        """Take the choice ``detach`` for a broken binding: a tombstone, so the chat plays on without it.
        Its actor and every turn stay; nothing is bound in its place."""
        if not isinstance(repair, Repair) or "detach" not in repair.choices or repair.role is None:
            raise QuickStartRefused("invalid_field", "only a broken binding is detached here")
        current = {(r.field, r.ref) for r in self.repairs(chat)}
        if (repair.field, repair.ref) not in current:
            raise QuickStartRefused("not_broken", "that source resolves now, or was already detached")
        try:
            _record, etag = self.chats.read_chat(chat)
            self.chats.detach_source(chat, SourceRef.parse(repair.ref), repair.role, expected=etag).commit()
        except StaleWrite:
            raise QuickStartRefused("changed", "the chat changed meanwhile; try again") from None
        except (InvalidBinding, ChatModelError) as error:
            raise QuickStartRefused(getattr(error, "reason", "invalid_binding"), str(error)[:200]) from None

    # -- helpers ------------------------------------------------------------------------------------

    def _player(self, by) -> str:
        authority = self.runner.authority
        if not (authority.issued(by) and getattr(by, "trusted", False) is True):
            raise QuickStartRefused("not_authenticated", "a player acts with a trusted handle this library issued")
        principal = getattr(by, "principal", None)
        if not isinstance(principal, str) or not is_human(principal):
            raise QuickStartRefused("not_authenticated", "only a human player starts a chat")
        return principal

    def _pin(self, ref: SourceRef, role: str) -> SourceRef:
        """``ref`` at the revision it resolves to now, or a refusal with 010's reason."""
        if not isinstance(ref, SourceRef):
            raise QuickStartRefused("invalid_field", f"a {role} is a definition reference")
        found = self.store.check_definition(ref)
        if isinstance(found, str):
            raise QuickStartRefused(found, f"{ref.unpinned()} cannot be bound: {found}")
        return found.ref

    def _sources(self, selection: Selection) -> tuple[tuple[SourceRef, ...], Mapping[str, object]]:
        """The pinned refs a selection binds, and its launch definition's payload."""
        if selection.ref is None:
            return (), {}
        launch = self._pin(selection.ref, selection.kind)
        found = self.store.check_definition(launch)
        payload = found.envelope.payload if not isinstance(found, str) else None
        payload = payload if isinstance(payload, Mapping) else {}
        if selection.kind != "bundle":
            return (launch,), payload
        lorebook = selection.lorebook
        if lorebook is None:
            card = payload.get("card")
            lorebook = _embedded_lorebook(card if isinstance(card, Mapping) else {})
            if lorebook is None:
                raise QuickStartRefused("no_lorebook", "this card embeds no lorebook; choose one to bundle")
        return (launch, self._pin(lorebook, "lorebook")), payload


def _offered(selection: Selection, payload: Mapping[str, object]) -> tuple[Opening, ...]:
    """The openings a selection offers: a scenario's openings, or the character card's greetings."""
    if selection.kind == "scenario":
        return _openings(payload.get("openings"))
    card = payload.get("card")
    return _openings(card.get("greetings")) if isinstance(card, Mapping) else ()


def _default(openings: tuple[Opening, ...]) -> str | None:
    return next((o.opening_id for o in openings if not o.group_only), None)


def _openings(items: object) -> tuple[Opening, ...]:
    if not isinstance(items, (list, tuple)):
        return ()
    found = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        opening_id, text = item.get("id"), item.get("text")
        if isinstance(opening_id, str) and opening_id and isinstance(text, str) and text.strip():
            found.append(Opening(opening_id, text, item.get("group_only") is True))
    return tuple(found)


def _embedded_lorebook(card: Mapping[str, object]) -> SourceRef | None:
    text = card.get("lorebook")
    if not isinstance(text, str):
        return None
    try:
        ref = SourceRef.parse(text)
    except ValueError:
        return None
    return ref if ref.kind == "lorebook" else None


__all__ = ["QuickStart"]

