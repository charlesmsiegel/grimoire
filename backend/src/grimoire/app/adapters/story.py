"""081's story explorer ops: find a thread of the story and follow it to its outcome. Called as ``story.<op>``.

``threads`` searches a campaign's ledger and ``thread`` reads one proposition's thread (``grimoire.story``), both
for one chat of the campaign (its cast names the actors, its controls say whose the table's characters are) and
for one audience, as the campaign hub does:

- ``director``: every record of the ledger that the thread links (020's director read), graded confirmed,
  belief, proposal or revoked, with canon's standing, the ruling that decided it and its contradictions;
- ``table``: only what the library owner's own characters received, sent and concluded (012's
  ``player_knowledge`` over the same read). No standing, ruling, event, sender or other character's evidence, and
  a proposition none of their characters knows of is not found at all.

Both read only, and call no model. Each entry carries its source: the chat 020 recorded it from and, when that
turn is on the chat's line, the turn's number, so the page links to it (``#/chat/<chat>?turn=<number>``); a turn
a fork choice set aside is said to be, never linked as if it were played. A ledger that cannot be read whole is
said (``knowledge_damaged``), never hidden: what is shown is then a subset. See docs/STORY.md.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.campaignapi import parse
from grimoire.campaignapi.views import moment_view
from grimoire.core import DIRECTOR
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.core.knowledge import KnowledgeError
from grimoire.core.perspective import player_knowledge
from grimoire.play.scene import NOW
from grimoire.store import StoreError
from grimoire.story import MAX_LISTED, MAX_QUERY, search, table_search, table_thread, thread
from grimoire.textapi import ApiRefused

from .campaign import hub_of

AUDIENCES = ("table", "director")
#: the most chats one answer reads turn numbers from; entries of any others are shown without a number
MAX_CHATS = 32
MAX_ID = 300


class _Read:
    """One chat's names and controls, and one director read of its campaign's ledger, for one audience."""

    def __init__(self, ctx: AdapterContext, args: Mapping[str, object]) -> None:
        self.ctx = ctx
        self.chat = parse.chat_of(args)
        self.audience = parse.field(args, "audience", str, required=False, default="table")
        if self.audience not in AUDIENCES:
            raise ApiRefused("invalid_field", "audience must be table or director")
        self.hub = hub_of(ctx)
        self.scene = self.hub.runner.open(self.chat)
        self.campaign = self.chat.campaign_id
        self.names = self.scene.names.get
        self.problems: list[dict] = []
        value = args.get("actor")
        self.actor_filter = None if value is None else parse.actor_of(value, "actor", self.campaign)
        self.stored = None
        try:
            self.stored = self.hub.evidence.read(self.campaign, requested_by=DIRECTOR)
        except (ValueError, StoreError) as error:
            self.problems.append({"code": "knowledge_unreadable",
                                  "detail": getattr(error, "reason", type(error).__name__)})
        if self.stored is not None and (self.stored.quarantined or self.stored.problems):
            # what each projection keeps is a subset of the whole ledger's (020): shown, and said to be partial
            detail = (f"{len(self.stored.quarantined)} quarantined, {len(self.stored.problems)} problems"
                      if self.director else None)
            self.problems.append({"code": "knowledge_damaged", "detail": detail})
        self.player = None
        if self.stored is not None and not self.director:
            self.player = player_knowledge(self.stored.knowledge, self.ctx.owner, self.scene.controls, as_of=NOW)
            if not self.player.actors:  # the owner plays no character here: the table knows nothing of the ledger
                self.problems.append({"code": "no_character", "detail": None})

    @property
    def director(self) -> bool:
        return self.audience == "director"

    def revoked(self) -> frozenset[str]:
        """The ids of events whose 021 accepted effect was revoked (their 020 bridge is still stored)."""
        try:
            continuity = self.hub.effects.continuity(self.campaign, as_of=NOW, requested_by=DIRECTOR)
        except (ValueError, StoreError) as error:
            self.problems.append({"code": "effects_unreadable",
                                  "detail": getattr(error, "reason", type(error).__name__)})
            return frozenset()
        return frozenset(event.ref.event_id for event in continuity.revoked)

    def actor(self, ref: InstanceRef | None) -> dict | None:
        return None if ref is None else {"ref": str(ref), "name": self.names(ref)}

    def cast(self) -> list[dict]:
        return [{"actor": str(actor), "name": self.names(actor)} for actor in self.scene.cast]


def _turns(read: _Read, chats: set[ChatRef]) -> dict[ChatRef, tuple[dict[str, int], set[str]] | None]:
    """Each chat's turn numbers on its line and the turns a fork choice set aside, or ``None`` (unread)."""
    found: dict[ChatRef, tuple[dict[str, int], set[str]] | None] = {}
    for chat in sorted(chats, key=str):
        if len(found) >= MAX_CHATS:
            found[chat] = None
            continue
        try:
            turns = read.hub.runner.scenes.turns(chat)
        except (ValueError, LookupError, StoreError):
            found[chat] = None
            continue
        found[chat] = ({stored.record.turn_id: stored.record.number for stored in turns.turns},
                       {stored.record.turn_id for stored in turns.set_aside})
    if any(value is None for value in found.values()):
        read.problems.append({"code": "turns_unread", "detail": None})
    return found


def _source(entry, turns) -> dict | None:  # type: ignore[no-untyped-def]
    if entry.chat is None:
        return None
    known = turns.get(entry.chat)
    turn = entry.moment.turn_id
    number = None if known is None or turn is None else known[0].get(turn)
    set_aside = known is not None and turn is not None and turn in known[1]
    return {"chat": str(entry.chat), "turn": turn, "number": number, "set_aside": set_aside}


def _entry(read: _Read, entry, turns) -> dict:  # type: ignore[no-untyped-def]
    view = {"kind": entry.kind, "record": entry.record, "grade": entry.grade, "moment": moment_view(entry.moment),
            "text": entry.text, "actors": [read.actor(actor) for actor in entry.actors],
            "source": _source(entry, turns)}
    if entry.kind in ("report", "sent"):
        view.update({"holder": read.actor(entry.holder), "attributed_to": read.actor(entry.attributed_to),
                     "confirmed": entry.confirmed})
        if read.director:  # who really sent it is the director's; the holder has only the attribution
            view.update({"sender": read.actor(entry.sender),
                         "sent_at": None if entry.sent_at is None else moment_view(entry.sent_at)})
    elif entry.kind == "belief":
        view.update({"holder": read.actor(entry.holder), "stance": entry.stance, "current": entry.current,
                     "inherited": bool(entry.inherited), "supersedes": entry.supersedes})
    elif entry.kind == "ruling":
        view.update({"standing": entry.standing, "grounds": list(entry.grounds)})
    return view


def _thread_view(read: _Read, found) -> dict:  # type: ignore[no-untyped-def]
    turns = _turns(read, {entry.chat for entry in found.entries if entry.chat is not None})
    entries = [_entry(read, entry, turns) for entry in found.entries]
    by_record = {item["record"]: item for item in entries}
    scenes = [{"chat": None if scene.chat is None else str(scene.chat), "scene": scene.scene,
               "first": scene.first, "last": scene.last, "turns": list(scene.turns), "records": list(scene.records),
               "actors": [read.actor(actor) for actor in scene.actors]}
              for scene in found.scenes]
    outcome = found.outcome
    view = {"proposition": found.proposition.proposition_id, "text": found.proposition.text,
            "entries": entries, "scenes": scenes, "actors": [read.actor(actor) for actor in found.actors],
            "outcome": None if outcome is None else {
                "standing": outcome.standing, "ruling": outcome.ruling,
                "moment": None if outcome.moment is None else moment_view(outcome.moment),
                "grounds": [{"subject": ground, "entry": by_record.get(ground)} for ground in outcome.grounds],
                "revoked_grounds": list(outcome.revoked_grounds)},
            "contradictions": [{"kind": item.kind, "records": list(item.records), "moment": moment_view(item.moment)}
                               for item in found.contradictions]}
    if read.director:
        view.update({"stated": moment_view(found.proposition.moment), "provenance": found.proposition.provenance})
    return view


def _summary(read: _Read, item) -> dict:  # type: ignore[no-untyped-def]
    return {"proposition": item.proposition.proposition_id, "text": item.proposition.text,
            "first": moment_view(item.first), "last": item.last, "standing": item.standing,
            "decided": None if item.decided is None else moment_view(item.decided), "entries": item.entries,
            "scenes": item.scenes, "contradictions": item.contradictions,
            "actors": [read.actor(actor) for actor in item.actors]}


def _query(args: Mapping[str, object]) -> str | None:
    value = args.get("query")
    if value is not None and type(value) is not str:
        raise ApiRefused("invalid_field", "query must be text")
    return value


def _limit(args: Mapping[str, object]) -> int:
    value = parse.field(args, "limit", int, required=False, default=50)
    if not 1 <= value <= MAX_LISTED:
        raise ApiRefused("invalid_field", f"limit must be from 1 to {MAX_LISTED}")
    return value


def _answer(read: _Read, **found) -> dict:  # type: ignore[no-untyped-def]
    return {"chat": str(read.chat), "campaign": read.campaign, "audience": read.audience, "cast": read.cast(),
            "actor": read.actor(read.actor_filter), **found, "problems": read.problems}


def threads(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The threads of the chat's campaign that match ``query`` and involve ``actor``, oldest first."""
    read = _Read(ctx, args)
    query, limit = _query(args), _limit(args)
    hits: tuple = ()
    total = 0
    try:
        if read.stored is not None and read.director:
            hits, total = search(read.stored.knowledge, as_of=NOW, query=query, actor=read.actor_filter,
                                 origins=read.stored.origins, revoked=read.revoked(), limit=limit)
        elif read.player is not None:
            hits, total = table_search(read.stored.knowledge, read.player, query=query, actor=read.actor_filter,
                                       origins=read.stored.origins, limit=limit)
    except ValueError:  # the query is too long (the limit was checked above)
        raise ApiRefused("invalid_field", f"query must be at most {MAX_QUERY} characters") from None
    return _answer(read, threads=[_summary(read, item) for item in hits], total=total)


def thread_op(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """One proposition's thread (``proposition``: its id, as ``threads`` named it)."""
    read = _Read(ctx, args)
    wanted = args.get("proposition")
    if type(wanted) is not str or not wanted or len(wanted) > MAX_ID:
        raise ApiRefused("invalid_field", "proposition must be an id as the threads named it")
    try:
        if read.stored is not None and read.director:
            found = thread(read.stored.knowledge, wanted, as_of=NOW, origins=read.stored.origins,
                           revoked=read.revoked())
        elif read.player is not None:
            found = table_thread(read.stored.knowledge, read.player, wanted, origins=read.stored.origins)
        else:
            found = None
    except KnowledgeError:
        found = None
    if found is None:  # the same answer for an id the ledger lacks and one the table may not know of
        raise ApiRefused("no_thread", "no thread of this campaign has that id for this view")
    return _answer(read, thread=_thread_view(read, found))


ADAPTER = Adapter("story", owner="081", ops={"threads": threads, "thread": thread_op})

__all__ = ["ADAPTER", "AUDIENCES", "MAX_CHATS"]
