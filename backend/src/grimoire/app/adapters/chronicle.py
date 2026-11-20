"""Sprint 115's adapter ops: a campaign's clock (055) and its characters' sourced memory (053). Called as
``chronicle.<op>``.

Only Sprint 115 edits this module. Each op is ``handler(ctx, args) -> dict`` over an existing domain service; see
``grimoire.app.adapter`` and docs/CHRONICLE.md. Nothing here keeps a record of its own.

**Clock.** ``clock`` reads 055's ``CalendarLibrary`` for one campaign's scope, as the owner with the app's trusted
handle. The calendar is director-facing canon (docs/CALENDARS.md), so every clock op asks for ``audience: director``
and the table is refused. A write is made in two calls the person makes separately: ``clock_plan`` builds the
operation against the etag the page last read and answers what it would do (the moment before and after, the
deadlines it would flag, the files it would write) without writing; ``clock_commit`` builds the same operation
again, with the same operation id and the same expected etag, and commits it. Anything that changed the campaign
in between makes the commit stale (``changed``), so it never applies to what the person did not review. The
operation id is the client's, kept across a retry: a commit retried after a lost answer finds its own receipt and
writes nothing again (``already_applied``). A flagged deadline is only a flag: nothing else happens.

**Memory.** ``memory`` reads one cast actor's 053 dossier in the play collection (``play``), the one the scene runner
packs for an NPC, as of now; ``remember`` records one authored account through ``MemoryLibrary.summarize``. The
editor is always the actor's present controller: the director's (the text API's own ``app:memory`` key) for an NPC,
and the owner's own for a character they play. The table reads and writes only the characters it plays; an NPC's
memory is the director's alone (``audience: director``). An account may cite only evidence the editor is shown for
that actor (the same perspective the campaign hub's ``knowledge`` op shows), so the table never cites, or learns of,
evidence beyond its own characters' knowledge. Nothing generates or rewrites the text.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from grimoire.app.adapter import Adapter, AdapterContext, Handler
from grimoire.calendars import CalendarDefinition, CalendarError, Duration
from grimoire.calendarstore import CalendarLibrary
from grimoire.campaignapi import parse
from grimoire.chats.codec import ChatCodecError
from grimoire.core import DIRECTOR
from grimoire.core.authority import AuthorityError
from grimoire.core.identity import ScopeRef, validate_segment
from grimoire.core.knowledge import Moment
from grimoire.memory import MAX_SUMMARY_TEXT, PLAY_COLLECTION, MemorySummary
from grimoire.memorystore import MemoryLibrary
from grimoire.play.scene import NOW
from grimoire.store.errors import LedgerInvalid
from grimoire.textapi import ApiRefused

from .campaign import hub_of

#: Built-in calendars a campaign's clock can start on from this page. A custom calendar (055's portable month
#: list) is a valid definition, but no screen authors one yet.
PROVIDERS = ("gregorian", "hebrew")
DEADLINE_KINDS = ("deadline", "reminder", "trigger")
#: Advancing at most this many days in one operation from this page (a typing slip of extra zeros is refused).
MAX_ADVANCE_DAYS = 36600
#: At most this many evidence items are offered to cite, and cited, in one account (053's own cap is 500).
MAX_CITED = 100
#: The longest history of a dossier shown beside its standing accounts (053's ``limit``).
HISTORY = 50
_MEMORY_REASON = re.compile(r"[a-z][a-z_]{1,63}")


# -- shared ----------------------------------------------------------------------------------------------------
def _director(args: Mapping[str, object], what: str) -> None:
    audience = parse.field(args, "audience", str, required=False, default="table")
    if audience not in ("table", "director"):
        raise ApiRefused("invalid_field", "audience must be table or director")
    if audience != "director":
        raise ApiRefused("director_only", f"{what} is the director's")


def _campaign(args: Mapping[str, object]) -> ScopeRef:
    value = args.get("campaign")
    if type(value) is not str:
        raise ApiRefused("invalid_field", "campaign must be a campaign id")
    try:
        validate_segment(value, "campaign")
    except ValueError:
        raise ApiRefused("invalid_field", "campaign is not a valid campaign id") from None
    return ScopeRef(value)


def _text(args: Mapping[str, object], key: str, *, maximum: int = 200) -> str:
    value = args.get(key)
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ApiRefused("invalid_field", f"{key} must be text of at most {maximum} characters")
    return value


def _whole(args: Mapping[str, object], key: str, low: int, high: int, *, default: int | None = None) -> int:
    value = args.get(key, default)
    if type(value) is not int or not low <= value <= high:
        raise ApiRefused("invalid_field", f"{key} must be a whole number from {low} to {high}")
    return value


def _segment(args: Mapping[str, object], key: str) -> str:
    value = args.get(key)
    if type(value) is not str:
        raise ApiRefused("invalid_field", f"{key} must be an id")
    try:
        validate_segment(value, key)
    except ValueError:
        raise ApiRefused("invalid_field", f"{key} is not a valid id") from None
    return value


# -- clock (055) -----------------------------------------------------------------------------------------------
def _moment(calendar: CalendarDefinition, moment) -> dict:  # type: ignore[no-untyped-def]
    hours, minutes = divmod(moment.minute, 60)
    return {"date": calendar.format(moment.fixed_day), "time": f"{hours:02d}:{minutes:02d}",
            "fixed_day": moment.fixed_day, "minute": moment.minute}


def _clock_view(view) -> dict:  # type: ignore[no-untyped-def]
    state = view.state
    answer = {"campaign": view.scope.campaign_id, "etag": view.etag, "started": state is not None}
    if state is None:
        return {**answer, "calendar": None, "now": None, "deadlines": [], "operations": 0}
    calendar = state.calendar
    deadlines = [{"deadline": item.deadline_id, "label": item.label, "kind": item.kind,
                  "due": _moment(calendar, item.due), "flagged_by": item.triggered_by}
                 for item in sorted(state.deadlines, key=lambda item: (item.due, item.deadline_id))]
    return {**answer, "calendar": {"provider": calendar.provider, "custom": calendar.custom_id},
            "now": _moment(calendar, state.now), "deadlines": deadlines, "operations": len(state.receipts)}


def _clock_failure(error: CalendarError | ChatCodecError) -> ApiRefused:
    return ApiRefused(error.reason, str(error))


def _calendar(handler: Handler) -> Handler:
    """A clock op whose campaign scope 055 refuses to read (a sync conflict, an unknown or invalid scope payload)
    answers that refusal and its reason, never a server error."""
    def guarded(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
        try:
            return handler(ctx, args)
        except (CalendarError, ChatCodecError) as error:
            raise _clock_failure(error) from None
    return guarded


def _library(ctx: AdapterContext) -> CalendarLibrary:
    return CalendarLibrary(ctx.store, ctx.handle)


def _clock(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    _director(args, "the campaign clock")
    scope = _campaign(args)
    try:
        return _clock_view(_library(ctx).read(scope))
    except CalendarError as error:
        raise _clock_failure(error) from None


def _planned(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    """The 055 plan the request names, built against its ``expected`` etag (nothing is written)."""
    _director(args, "the campaign clock")
    scope = _campaign(args)
    operation = args.get("operation")
    if operation not in ("initialize", "schedule", "advance"):
        raise ApiRefused("invalid_field", "operation must be initialize, schedule or advance")
    operation_id = _segment(args, "operation_id")
    expected = args.get("expected")
    if type(expected) is not str:
        raise ApiRefused("review_required", "read the clock before changing it")
    library = _library(ctx)
    try:
        if operation == "initialize":
            provider = args.get("provider", "gregorian")
            if provider not in PROVIDERS:
                raise ApiRefused("invalid_field", "the calendar must be gregorian or hebrew")
            return library.initialize(scope, operation_id, CalendarDefinition(provider), _text(args, "date", maximum=64),
                                      minute=_whole(args, "minute", 0, 1439, default=0), expected=expected)
        if operation == "schedule":
            kind = args.get("kind", "deadline")
            if kind not in DEADLINE_KINDS:
                raise ApiRefused("invalid_field", "kind must be deadline, reminder or trigger")
            return library.schedule(scope, operation_id, _segment(args, "deadline"), _text(args, "date", maximum=64),
                                    minute=_whole(args, "minute", 0, 1439, default=0),
                                    label=_text(args, "label", maximum=200), kind=kind, expected=expected)
        minutes = _whole(args, "minutes", 1, MAX_ADVANCE_DAYS * 1440)
        return library.advance(scope, operation_id, Duration(minutes), expected=expected)
    except CalendarError as error:
        raise _clock_failure(error) from None


def _receipt(change, view) -> dict:  # type: ignore[no-untyped-def]
    """What the planned operation does, in the calendar it is planned on."""
    state = view.state
    receipt = change.receipt
    calendar = getattr(receipt.operation, "calendar", None) or state.calendar
    labels = {} if state is None else {item.deadline_id: item.label for item in state.deadlines}
    return {"operation": receipt.operation.operation_id, "already_applied": change.already_applied,
            "before": None if receipt.before is None else _moment(calendar, receipt.before),
            "after": _moment(calendar, receipt.after),
            "flags": [{"deadline": item, "label": labels.get(item)} for item in receipt.triggered]}


def _clock_plan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """What the operation would do, as reviewed against ``expected``; nothing is written."""
    change = _planned(ctx, args)
    view = _library(ctx).read(change.scope)
    return {"plan": {**_receipt(change, view), "files": sorted(change.preview())}, "clock": _clock_view(view)}


def _clock_commit(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The operation reviewed with ``clock_plan``, built again with the same id and etag, committed once."""
    change = _planned(ctx, args)
    before = _library(ctx).read(change.scope)
    done = _receipt(change, before)
    try:
        view = change.commit()
    except CalendarError as error:
        raise _clock_failure(error) from None
    return {"done": done, "clock": _clock_view(view)}


# -- memory (053) ----------------------------------------------------------------------------------------------
def _memory_failure(error: Exception) -> ApiRefused:
    """053's refusals carry their reason as the message (``unavailable_evidence``, ``backdated``...)."""
    reason = getattr(error, "reason", None) or (str(error.args[0]) if error.args else "")
    if isinstance(error, AuthorityError):
        return ApiRefused("not_authorized", reason or "this actor's memory is not yours to edit")
    if isinstance(error, LedgerInvalid):
        return ApiRefused("memory_unreadable", "the memory ledger cannot be read; nothing was written")
    return ApiRefused(reason if _MEMORY_REASON.fullmatch(reason) else "invalid_memory", reason or type(error).__name__)


def _actor(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    """The chat, its cast actor named by ``actor``, whose memory it is (``npc`` or ``player``) and the audience."""
    hub = hub_of(ctx)
    chat = parse.chat_of(args)
    audience = parse.field(args, "audience", str, required=False, default="table")
    if audience not in ("table", "director"):
        raise ApiRefused("invalid_field", "audience must be table or director")
    actor = parse.actor_of(args.get("actor"), "actor", chat.campaign_id)
    scene = hub.runner.open(chat)
    if actor not in scene.cast:
        raise ApiRefused("no_actor", f"{actor} is not in this chat's cast")
    controller = scene.controller(actor)
    if controller == DIRECTOR:
        if audience != "director":
            raise ApiRefused("director_only", "an NPC's memory is the director's")
        return hub, chat, actor, "npc", audience, scene.names.get(actor)
    if controller != ctx.owner:
        raise ApiRefused("not_your_character", "only the character's present controller keeps its memory")
    return hub, chat, actor, "player", audience, scene.names.get(actor)


def _memories(ctx: AdapterContext, whose: str) -> tuple[MemoryLibrary, str]:
    """The 053 library and requester for the actor's present controller: the director's key or the owner's own."""
    if whose == "npc":
        return ctx.api.memory, DIRECTOR
    return MemoryLibrary(ctx.store, ctx.authority, ctx.handle), ctx.owner


def _citable(hub, chat, actor, audience) -> list:  # type: ignore[no-untyped-def]
    """The evidence the editor is shown for ``actor``: the hub's own perspective of it, newest first."""
    known = hub.call("knowledge", {"chat": str(chat), "actor": str(actor), "audience": audience})
    (shown,) = known["actors"]
    found = [{"evidence": item["evidence"], "type": item["type"], "channel": item["channel"],
              "content": item["content"], "moment": item["moment"]} for item in shown["observations"]]
    found.sort(key=lambda item: (item["moment"] or {}).get("sequence", 0), reverse=True)
    return found


def _item(item) -> dict:  # type: ignore[no-untyped-def]
    return {"account": item.operation_id, "topic": item.topic, "text": item.text, "status": item.status,
            "at": item.moment.sequence, "basis": item.basis, "scenes": list(item.scenes),
            "supersedes": item.supersedes, "superseded_by": list(item.superseded_by),
            "obsolete": list(item.obsolete_ids), "replacements": list(item.replacement_ids),
            "unavailable": list(item.unavailable_ids),
            "evidence": [{"evidence": known.evidence_id, "type": known.kind, "channel": known.channel,
                          "content": known.content, "at": known.moment.sequence} for known in item.evidence]}


def _dossier(ctx: AdapterContext, hub, chat, actor, whose, audience, name) -> dict:  # type: ignore[no-untyped-def]
    library, requester = _memories(ctx, whose)
    try:
        dossier = library.dossier(actor, PLAY_COLLECTION, as_of=NOW, limit=HISTORY, requested_by=requester)
    except (AuthorityError, LedgerInvalid, ValueError) as error:
        raise _memory_failure(error) from None
    citable = _citable(hub, chat, actor, audience)
    return {"chat": str(chat), "actor": str(actor), "name": name, "whose": whose,
            "editor": "director" if dossier.editor == DIRECTOR else "you",
            "topics": [{"topic": topic.topic, "current": [_item(item) for item in topic.current],
                        "history": [_item(item) for item in topic.history]} for topic in dossier.topics],
            "omitted": dossier.omitted, "corrected": len(dossier.suppressed),
            "citable": citable[:MAX_CITED], "citable_total": len(citable), "max_text": MAX_SUMMARY_TEXT}


def _recorded_at(library: MemoryLibrary, requester: str, actor, operation_id: str) -> int | None:  # type: ignore[no-untyped-def]
    """The moment the editor's own account ``operation_id`` of ``actor`` was recorded at, or None if it was not."""
    dossier = library.dossier(actor, PLAY_COLLECTION, as_of=NOW, limit=1000, requested_by=requester)
    for topic in dossier.topics:
        for item in (*topic.current, *topic.history):
            if item.operation_id == operation_id:
                return int(item.moment.sequence)
    return None


def _memory(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    hub, chat, actor, whose, audience, name = _actor(ctx, args)
    return _dossier(ctx, hub, chat, actor, whose, audience, name)


def _remember(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Record one authored account of ``topic``, citing ``evidence`` the editor is shown, as of the newest
    evidence shown for the actor; ``supersedes`` names the editor's own earlier account of the same topic it replaces."""
    hub, chat, actor, whose, audience, name = _actor(ctx, args)
    operation_id = _segment(args, "operation_id")
    topic = _segment(args, "topic")
    text = args.get("text")
    if type(text) is not str or not text.strip() or len(text) > MAX_SUMMARY_TEXT:
        raise ApiRefused("invalid_field", f"the account must be text of at most {MAX_SUMMARY_TEXT} characters")
    cited = args.get("evidence")
    if (type(cited) is not list or not 1 <= len(cited) <= MAX_CITED or len(set(map(str, cited))) != len(cited)
            or not all(type(item) is str for item in cited)):
        raise ApiRefused("invalid_field", f"cite from 1 to {MAX_CITED} distinct evidence items")
    supersedes = None if args.get("supersedes") is None else _segment(args, "supersedes")
    shown = {item["evidence"]: item for item in _citable(hub, chat, actor, audience)}
    unknown = [item for item in cited if item not in shown]
    if unknown:  # never cite (or probe for) evidence beyond what the editor is shown of this actor
        raise ApiRefused("unavailable_evidence", "an account may cite only evidence shown for this character")
    # Recorded as of the newest evidence the editor is shown for this actor, not as of the evidence it cites: 053
    # keeps an editor's records in order, and an account citing older evidence than the last one is still new.
    # Its basis (what it rests on) is worked out from the evidence it cites.
    # A retry of an account already recorded keeps the moment it was recorded at, so newer evidence arriving in
    # between does not turn the same request into a conflict (053 accepts a repeated id only as the same record).
    library, requester = _memories(ctx, whose)
    try:
        recorded = _recorded_at(library, requester, actor, operation_id)
        at = recorded if recorded is not None else max((item["moment"] or {}).get("sequence", 0)
                                                       for item in shown.values())
        summary = MemorySummary(operation_id, actor, PLAY_COLLECTION, topic, text, tuple(cited), Moment(at),
                                supersedes=supersedes)
        library.summarize(summary, requested_by=requester)
    except (AuthorityError, LedgerInvalid, ValueError) as error:
        raise _memory_failure(error) from None
    return {"done": {"account": operation_id, "topic": topic}, "memory": _dossier(ctx, hub, chat, actor, whose,
                                                                                 audience, name)}


# -- what the rest of Wave 8 offers on screen ------------------------------------------------------------------
#: Every user-facing Wave 8 first slice (049-072), where its controls are, or what it waits on. ``here`` is this
#: page; ``screen`` names another page's route (``campaign``: its address is this campaign's, for the same chat, in
#: the director's view); ``unavailable`` is said plainly, never offered as a button. The
#: purely internal and evaluation slices (066, 068, 069, 071) have no screen and are not listed. docs/CHRONICLE.md
#: keeps the same table with each one's test.
FEATURES = (
    {"slice": "053", "title": "Sourced memory", "status": "here", "where": "this page"},
    {"slice": "055", "title": "Campaign clock and deadlines", "status": "here", "where": "this page"},
    {"slice": "061", "title": "Private local voice", "status": "screen", "route": "voice",
     "where": "Voice, in Settings: an installed local recognizer and its measurements"},
    {"slice": "065", "title": "Scene workspace", "status": "screen", "route": "chats",
     "where": "Chats: every chat from one library view, with saved filters"},
    {"slice": "067", "title": "Voice accessibility", "status": "screen", "route": "chats",
     "where": "in a chat: captions, read-aloud and hands-free controls"},
    {"slice": "070", "title": "Circling a spot on a sketch", "status": "screen", "route": "chats",
     "where": "in a chat: a circled spot carried with the post"},
    {"slice": "072", "title": "VN cues", "status": "screen", "route": "chats",
     "where": "in a chat: the cue timeline, its blocking, focus and transitions"},
    {"slice": "059", "title": "Places and routes", "status": "screen", "route": "world", "campaign": True,
     "where": "World, the director's: places, roads blocked in this campaign and routes between exact places"},
    {"slice": "063", "title": "Rumors", "status": "screen", "route": "world", "campaign": True,
     "where": "World, the director's: who told whom, when it arrives, and the recipient's belief admitted"},
    {"slice": "057", "title": "Faction decisions", "status": "screen", "route": "world", "campaign": True,
     "where": "World, the director's: a leader's choice previewed with its dissent; nothing is saved"},
    {"slice": "060", "title": "What an NPC thinks others think", "status": "screen", "route": "world",
     "campaign": True, "where": "World, the director's: an NPC's hypotheses and its tactic previewed; nothing is saved"},
    {"slice": "049", "title": "Reconciling divergent offline scenes", "status": "screen", "route": "story",
     "campaign": True,
     "where": "Story, for the director: both kept versions of a scene, why keeping one side is refused, retained forks"},
    {"slice": "054", "title": "Reviewing a fork or retcon", "status": "screen", "route": "story",
     "campaign": True,
     "where": "Story, for the director: what a retcon or fork would touch, and recording what rests on what"},
    {"slice": "050", "title": "Captured companions", "status": "screen", "route": "campaign",
     "where": "a campaign's hub, for the director: who trains whom, and a reviewed capture"},
    {"slice": "051", "title": "Several NPCs answering at once", "status": "screen", "route": "chats",
     "where": "in a chat's Details, for the director: a previewed exchange (running or accepting one is not "
              "available yet)"},
    {"slice": "056", "title": "Source catalogs", "status": "screen", "route": "prep", "campaign": True,
     "where": "Prep, in the director's view: the catalogs, each chat's pinned bindings and their context cost"},
    {"slice": "058", "title": "Two players at one screen", "status": "screen", "route": "chats",
     "where": "in a chat: the shared turn, each local player drafting their own character's part"},
    {"slice": "062", "title": "Ruleset action contracts", "status": "screen", "route": "prep", "campaign": True,
     "where": "Prep: the four reference contracts, and an action checked as a proposal only"},
    {"slice": "064", "title": "Scenario preparation", "status": "screen", "route": "prep", "campaign": True,
     "where": "Prep, in the director's view: one scene prepared from the library's evidence, after an approved estimate"},
)


def _coverage(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    return {"features": [dict(item) for item in FEATURES]}


ADAPTER = Adapter("chronicle", owner="115",
                  ops={"clock": _calendar(_clock), "clock_plan": _calendar(_clock_plan),
                       "clock_commit": _calendar(_clock_commit),
                       "memory": _memory, "remember": _remember, "coverage": _coverage},
                  refusals={CalendarError: "calendar_refused"})

__all__ = ["ADAPTER", "FEATURES", "PROVIDERS"]
