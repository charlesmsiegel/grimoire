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

from grimoire.chats import ChatCodecError, ChatModelError, ChatRecord, Launch
from grimoire.chatstore import ChatLibrary, InvalidBinding
from grimoire.core.identity import ChatRef, ScopeRef, SourceRef
from grimoire.play import SceneRunner
from grimoire.play.runner import Scene
from grimoire.scene import SceneRefused
from grimoire.security import is_human
from grimoire.store import RecordNotFound, StaleWrite, StoreError, TakenIdsUnlisted, WriteDenied

from .model import SELECTION_REFS, LaunchOptions, Opening, QuickStartRefused, Repair, Selection, Started

#: 035 refusals of ``keep`` after which the opening is proposed again for the cast as it is now.
_ASK_AGAIN = frozenset({"cast_changed", "not_proposed"})


class QuickStart:
    """Start Chat over one open library. Trusted application code, never handed to a worker.

    ``chats`` and ``runner`` must be over the same store, and ``chats`` must write with a trusted handle
    the runner's authority issued, as the runner does. The player acts with their own handle (``by``)."""

    def __init__(self, chats: ChatLibrary, runner: SceneRunner) -> None:
        if not isinstance(chats, ChatLibrary) or not isinstance(runner, SceneRunner):
            raise QuickStartRefused("invalid_field", "Start Chat needs a ChatLibrary and a SceneRunner")
        if chats.store is not runner.store:
            raise QuickStartRefused("invalid_field", "the chat library and the runner must share one store")
        if not (runner.authority.issued(chats.handle) and getattr(chats.handle, "trusted", False) is True):
            # an untrusted chat library would be refused at every commit, after Start Chat had decided to write
            raise QuickStartRefused("invalid_field", "the chat library writes with a trusted handle of this library")
        self.chats = chats
        self.runner = runner
        self.store = runner.store

    # -- before anything is written -----------------------------------------------------------------

    def options(self, selection: Selection) -> LaunchOptions:
        """What ``selection`` offers: the pinned sources it would bind, its openings, the card's own
        lorebook and the chats already started from this source in the same role. Reads only; no model
        runs. A bundle whose card embeds no lorebook is offered with no lorebook (``start`` refuses it
        ``no_lorebook`` unless one is chosen)."""
        if not isinstance(selection, Selection):
            raise QuickStartRefused("invalid_selection", "options need a Selection")
        sources, payload = self._sources(selection, strict=False)
        openings = _offered(selection, payload)
        card = payload.get("card")
        lorebook = _embedded_lorebook(card) if selection.kind != "scenario" and isinstance(card, Mapping) else None
        related: tuple[ChatRef, ...] = ()
        if selection.ref is not None:  # a persona's chats are not this character's: match the launch role
            related = self.chats.navigation().by_source(selection.ref.unpinned(),
                                                        role=SELECTION_REFS[selection.kind])
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
        _check_opening(opening)
        if persona is None and player not in self.runner.authority.owners:
            # with no persona, only the library owner is at this table (035): refuse before writing
            raise QuickStartRefused("not_at_table", "pick a persona to play, or start as the library owner")
        sources, payload = self._sources(selection, strict=True)
        offered = _offered(selection, payload)
        if opening is None:
            opening = _default(offered)  # the first one that is not group-only; none: the premise, or empty
        elif opening not in {o.opening_id for o in offered}:
            raise QuickStartRefused("unknown_opening", "that greeting or opening is not offered here")
        pinned_persona = self._pin(persona, "persona") if persona is not None else None
        if selection.kind == "premise":
            launch = Launch("premise", premise=selection.premise)
        else:
            launch = Launch(SELECTION_REFS[selection.kind] if selection.kind == "bundle" else selection.kind,
                            sources[0])
        attach = [(ref, "lorebook") for ref in sources[1:]]
        chat = _committed(lambda: self.chats.start_chat(launch, attach=attach, persona=pinned_persona,
                                                        label=label)).chat
        assert chat is not None  # start_chat always names its chat
        return self._launch(chat, by, player, opening)

    def continue_chat(self, chat: ChatRef, by, *, opening: str | None = None) -> Started:
        """Open an existing chat to play on: never a second chat.

        Only the library owner or the player of the chat's persona may (``not_at_table``, nothing written);
        a persona nobody controls yet is seated only by the owner. A launch that stopped after its chat
        was written (``Started.unfinished``) is finished: control is seated for actors with none, a chat
        with no turn is opened as ``start`` would, and an opening still open is kept (proposed again for
        the cast as it is now if the cast changed). A chat with an imported history (026, 036) and no turn
        yet is not given a greeting after it; a chat under way is left as it is, and choosing an
        ``opening`` for it is refused ``already_begun``."""
        _check_chat(chat)
        player = self._player(by)
        _check_opening(opening)
        scene = self._scene(chat)
        self._at_table(scene, player)
        records = scene.records
        begun = bool(records) and not (len(records) == 1 and records[0].origin == "opening" and records[0].open)
        if begun and opening is not None:
            raise QuickStartRefused("already_begun", "the chat is under way; it opens only once")
        if opening is not None and opening not in self._chat_openings(self._read(chat)[0]):
            raise QuickStartRefused("unknown_opening", "that greeting or opening is not offered here")
        if begun:
            first = records[0]
            return Started(chat, first if first.origin == "opening" else None)
        if not records and self._imported(chat):
            if opening is not None:
                raise QuickStartRefused("already_begun", "an imported conversation is this chat's beginning")
            # 036/026's pages are the history before turn 1: a fresh greeting would follow the conversation
            return self._seat(chat, player) or Started(chat)
        return self._launch(chat, by, player, opening)

    def _launch(self, chat: ChatRef, by, player: str, opening: str | None) -> Started:
        """Seat, then open and keep: each a 035 commit of its own, so a refusal leaves a chat to continue."""
        unfinished = self._seat(chat, player)
        if unfinished is not None:
            return unfinished
        try:
            scene = self.runner.open(chat)
            pending = scene.records[0] if scene.records else None
            proposed = [take for take in pending.takes if take.state == "proposed"] if pending else []
            if pending is None or opening is not None or not proposed:
                # a take for the chosen opening (or the default) for the cast as it is now
                try:
                    pending = self.runner.begin(chat, by, opening=opening)
                except SceneRefused as error:
                    if error.reason == "no_opening" and opening is None and not scene.records:
                        return Started(chat)  # nothing to open with: the player speaks first
                    raise
                proposed = [pending.takes[-1]]
            try:
                return Started(chat, self.runner.keep(chat, by, proposed[-1].take))
            except SceneRefused as error:
                if error.reason not in _ASK_AGAIN:
                    raise
            # the cast changed since that take was written: ask once more, for the cast as it is now
            pending = self.runner.begin(chat, by, opening=opening)
            return Started(chat, self.runner.keep(chat, by, pending.takes[-1].take))
        except SceneRefused as error:
            return Started(chat, unfinished=error.reason)

    def _seat(self, chat: ChatRef, player: str) -> Started | None:
        try:
            self.runner.seat(chat, player)
        except SceneRefused as error:
            return Started(chat, unfinished=error.reason)
        return None

    # -- while playing ------------------------------------------------------------------------------

    def add_card(self, chat: ChatRef, ref: SourceRef, by, *, role: str = "character") -> Started:
        """Add a character, lorebook or scenario to a chat under way, pinned. Only the owner or the
        persona's player may (``not_at_table``). A new character is a new actor that witnessed nothing
        (107's ``not_witnessed``; 035 lets it hear only what is said from now on), seated with the
        director. The chat's launch, premise and earlier turns are unchanged."""
        _check_chat(chat)
        if role not in ("character", "lorebook", "scenario"):
            raise QuickStartRefused("invalid_field", "add a character, lorebook or scenario")
        player = self._player(by)
        self._at_table(self._scene(chat), player)
        pinned = self._pin(ref, role)
        _record, etag = self._read(chat)
        _committed(lambda: self.chats.attach_source(chat, pinned, role, expected=etag))
        return self._seat(chat, player) or Started(chat)

    def name_campaign(self, chat: ChatRef, title: str | None, by) -> ScopeRef:
        """Name the chat's play scope as a campaign (or clear the name): presentation only. Its chat id,
        actor ids, launch and history are unchanged (107)."""
        _check_chat(chat)
        self._at_table(self._scene(chat), self._player(by))
        try:
            scope, etag = self.chats.read_scope(chat.scope)
        except (RecordNotFound, ChatCodecError, StoreError):
            raise QuickStartRefused("no_chat", f"{chat}'s scope cannot be read") from None
        _committed(lambda: self.chats.name_scope(scope.ref, title, expected=etag))
        return scope.ref

    # -- a source gone ------------------------------------------------------------------------------

    def repairs(self, chat: ChatRef) -> tuple[Repair, ...]:
        """Every source the chat binds that does not resolve now, with the player's choices. Reads only."""
        _check_chat(chat)
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
                choices = ("detach",)
            else:
                choices = ()  # the launch (provenance) and a missing actor: reported, never rebound
            found.append(Repair(broken.field, broken.text, broken.reason, role, choices))
        return tuple(found)

    def detach_broken(self, chat: ChatRef, repair: Repair, by) -> None:
        """Take the choice ``detach`` for a broken binding or world: a tombstone (107), so the chat plays on
        without it. Its actor and every turn stay; nothing is bound in its place."""
        _check_chat(chat)
        if not isinstance(repair, Repair) or "detach" not in repair.choices:
            raise QuickStartRefused("invalid_field", "only a broken binding or world is detached here")
        self._at_table(self._scene(chat), self._player(by))
        if (repair.field, repair.ref) not in {(r.field, r.ref) for r in self.repairs(chat)}:
            raise QuickStartRefused("not_broken", "that source resolves now, or was already detached")
        if repair.field == "world":
            try:
                _scope, etag = self.chats.read_scope(chat.scope)
            except (RecordNotFound, ChatCodecError, StoreError):
                raise QuickStartRefused("no_chat", f"{chat}'s scope cannot be read") from None
            _committed(lambda: self.chats.detach_world(chat.scope, expected=etag))
            return
        _record, etag = self._read(chat)
        assert repair.role is not None  # a binding's field names its role
        _committed(lambda: self.chats.detach_source(chat, SourceRef.parse(repair.ref), repair.role,
                                                    expected=etag))

    # -- helpers ------------------------------------------------------------------------------------

    def _player(self, by) -> str:
        authority = self.runner.authority
        if not (authority.issued(by) and getattr(by, "trusted", False) is True):
            raise QuickStartRefused("not_authenticated", "a player acts with a trusted handle this library issued")
        principal = getattr(by, "principal", None)
        if not isinstance(principal, str) or not is_human(principal):
            raise QuickStartRefused("not_authenticated", "only a human player starts a chat")
        return principal

    def _scene(self, chat: ChatRef) -> Scene:
        try:
            return self.runner.open(chat)
        except SceneRefused as error:
            raise QuickStartRefused(error.reason, f"{chat} cannot be opened") from None

    def _at_table(self, scene: Scene, player: str) -> None:
        """Before any write: the library owner, or the player who controls the chat's persona. A persona
        nobody controls yet is the owner's to seat, so another player never claims it by continuing."""
        if player in self.runner.authority.owners:
            return
        if scene.persona is not None and scene.controller(scene.persona) == player:
            return
        raise QuickStartRefused("not_at_table", "only the owner or this chat's player may do that")

    def _read(self, chat: ChatRef) -> tuple[ChatRecord, str]:
        try:
            return self.chats.read_chat(chat)
        except (RecordNotFound, ChatCodecError, StoreError):
            raise QuickStartRefused("no_chat", f"{chat} cannot be read") from None

    def _imported(self, chat: ChatRef) -> bool:
        pages = self.store.chat_transcript(chat)
        return bool(pages.records or pages.problems)

    def _chat_openings(self, record: ChatRecord) -> set[str]:
        """Every opening id 035's ``begin`` could find for this chat: its scenarios' and its launch card's."""
        ids: set[str] = set()
        refs = [(b.ref, "scenario") for b in record.active_bindings("scenario")]
        if record.launch.kind == "character" and record.launch.ref is not None:
            refs.append((record.launch.ref, "character"))
        for ref, kind in refs:
            found = self.store.check_definition(ref)
            payload = found.envelope.payload if not isinstance(found, str) else None
            payload = payload if isinstance(payload, Mapping) else {}
            ids |= {o.opening_id for o in _offered(Selection(kind, ref) if kind == "scenario"
                                                   else Selection.character(ref), payload)}
        return ids

    def _pin(self, ref: SourceRef, role: str) -> SourceRef:
        """``ref`` at the revision it resolves to now, or a refusal with 010's reason."""
        if not isinstance(ref, SourceRef):
            raise QuickStartRefused("invalid_field", f"a {role} is a definition reference")
        found = self.store.check_definition(ref)
        if isinstance(found, str):
            raise QuickStartRefused(found, f"{ref.unpinned()} cannot be bound: {found}")
        return found.ref

    def _sources(self, selection: Selection, *, strict: bool) -> tuple[tuple[SourceRef, ...], Mapping[str, object]]:
        """The pinned refs a selection binds, and its launch definition's payload. Not ``strict``, a bundle
        with no lorebook to bind is given with its character alone (for a preview)."""
        if selection.ref is None:
            return (), {}
        found = self.store.check_definition(selection.ref)
        if isinstance(found, str):
            raise QuickStartRefused(found, f"{selection.ref.unpinned()} cannot be bound: {found}")
        launch, payload = found.ref, found.envelope.payload
        payload = payload if isinstance(payload, Mapping) else {}
        if selection.kind != "bundle":
            return (launch,), payload
        lorebook = selection.lorebook
        if lorebook is None:
            card = payload.get("card")
            lorebook = _embedded_lorebook(card if isinstance(card, Mapping) else {})
            if lorebook is None:
                if not strict:
                    return (launch,), payload
                raise QuickStartRefused("no_lorebook", "this card embeds no lorebook; choose one to bundle")
        return (launch, self._pin(lorebook, "lorebook")), payload


def _committed(change):
    """Build and commit one 107 change, refusing with its reason; nothing is written on a refusal."""
    try:
        return change().commit()
    except (InvalidBinding, ChatModelError) as error:
        raise QuickStartRefused(getattr(error, "reason", "invalid_binding"), str(error)[:200]) from None
    except StaleWrite:
        raise QuickStartRefused("changed", "the library changed meanwhile; try again") from None
    except TakenIdsUnlisted:
        raise QuickStartRefused("taken_ids_unlisted", "the campaign's taken ids cannot be listed") from None
    except WriteDenied:
        raise QuickStartRefused("denied", "the library refused the write") from None
    except (RecordNotFound, ChatCodecError):
        raise QuickStartRefused("no_chat", "the chat cannot be read") from None
    except (StoreError, ValueError) as error:
        raise QuickStartRefused("store_error", f"{type(error).__name__}") from None


def _check_chat(chat: object) -> None:
    if type(chat) is not ChatRef:
        raise QuickStartRefused("invalid_field", "a chat is a ChatRef")


def _check_opening(opening: object) -> None:
    if opening is not None and (type(opening) is not str or not opening):
        raise QuickStartRefused("invalid_field", "an opening is chosen by its id")


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
