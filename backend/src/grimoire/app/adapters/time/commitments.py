"""077's quests and promises on the Time page: every commitment with its status, obligors and those owed, its 055
deadline as the calendar holds it now (found, passed, overdue, late), whether its history is certain and which
steps were skipped and why; offering one, recording a later step, and voiding one recorded in error.

``CommitmentLibrary`` is the director's, holding the outcome key (the app's trusted handle, checked against the
library's authorizer). The director records adjudicated outcomes (partly or wholly fulfilled, lapsed) and, when no
obligor is a player's, the obligors' decisions (accepted, refused, abandoned). A player-controlled obligor decides
for itself: 077 needs that player's own 003 contribution for the scene and turn the step's citations name, and
their endorsement of the exact step. This page takes **the owner's own key only**, for a character the owner plays:
the decision rests on the owner's own words in that turn of the chosen chat (a speech post, which covers 003's
``commitment`` domain), and the owner gives the key knowingly when making it (``endorse``). A decision for a
character another person plays is that person's and is not offered here. A void takes the same keys of every
player who backed what it withdraws.

Every step cites 1 to 16 of the campaign's 020 objective events. The page offers only events the director's ledger
holds and 077 reads as supported now (the newest first); 077 checks them again when the step is planned and when
it is committed (a retcon in between refuses it, ``citation_changed``). Commitment records are canon for the
director, never an NPC's memory.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext
from grimoire.calendars import Moment as ClockMoment
from grimoire.commitments import (
    DECISIONS,
    KINDS,
    MAX_CITES,
    NEXT,
    OUTCOMES,
    Commitment,
    Offer,
    Transition,
    Void,
    transition_fingerprint,
    void_fingerprint,
)
from grimoire.commitmentstore import CommitmentLibrary, Due, read_support
from grimoire.core import DIRECTOR
from grimoire.core.authority import CONTRIBUTION_DOMAINS
from grimoire.effectstore import Endorsement
from grimoire.evidencestore import EvidenceLibrary
from grimoire.play.scene import post_contributions
from grimoire.textapi import ApiRefused

from . import common

#: At most this many of the newest supported events are offered to cite (077 cites at most 16 per step).
MAX_CITABLE = 100
OPERATIONS = ("offer", "record", "void")


def library(ctx: AdapterContext) -> CommitmentLibrary:
    return CommitmentLibrary(ctx.store, ctx.handle, issued=ctx.authority.issued)


_controls = common.controls_of
_controller = common.controller


def _deciders(ctx: AdapterContext, commitment: Commitment, controls, names: common.Names, chat) -> dict:  # type: ignore[no-untyped-def]
    """Who decides for the obligors now, and whether this page can record their decision: the director's alone,
    or with the owner's own key for the owner's own characters; another person's character is theirs."""
    if chat is None:
        return {"available": False, "reason": "no_chat",
                "detail": "Choose a chat: control of the obligors is read from its campaign's records."}
    keys, others = [], []
    for ref in commitment.owed_by:
        controller = _controller(commitment.campaign_id, ref, controls)
        if controller is None:
            return {"available": False, "reason": "ambiguous_control",
                    "detail": f"Who plays {names.name(ref) or ref} is recorded twice; fix the campaign's control first."}
        if controller == ctx.owner:
            keys.append(names.who(ref))
        elif controller != DIRECTOR:
            others.append({**names.who(ref), "player": controller})
    if others:
        who = ", ".join(f"{item['name'] or item['actor']} ({item['player']})" for item in others)
        return {"available": False, "reason": "not_your_character", "others": others,
                "detail": f"{who}: a decision for a character someone else plays is that player's own, with their own "
                          "words and key. This screen takes only your own key."}
    return {"available": True, "keys": keys}


def _cited(item, support) -> list[dict]:  # type: ignore[no-untyped-def]
    events = support.events
    return [{"event": event_id, "support": value,
             "summary": events[event_id].summary if event_id in events else None,
             "at": events[event_id].moment.sequence if event_id in events else None}
            for event_id, value in item.cited]


def _step_view(step, support, calendar, names: common.Names, refs) -> dict:  # type: ignore[no-untyped-def]
    """One operation as 077's fold read it; ``refs`` are the obligors by instance id (a step records ids)."""
    op = step.operation
    answer = {"operation": op.operation_id, "status": "offered" if type(op) is Offer else op.status,
              "support": step.support, "outcome": step.outcome, "cited": _cited(step, support),
              "at": op.at.sequence, "scene": op.at.scene_id, "turn": op.at.turn_id,
              "clock_at": None if op.clock_at is None else common.moment(calendar, _clock(op.clock_at)), "note": ""}
    if type(op) is Transition:
        answer["note"] = op.note
        who_of = lambda actor: names.who(refs[actor]) if actor in refs else {"actor": actor, "name": None}
        answer["decided_by"] = [{**who_of(actor), "controller": who} for actor, who in op.controls]
        answer["backed_by"] = [{**who_of(item.actor_id), "player": item.submitted_by, "turn": item.turn_id}
                               for item in op.authorship]
    return answer


def _clock(value) -> ClockMoment:  # type: ignore[no-untyped-def]
    return ClockMoment(value.fixed_day, value.minute)


def _deadline_view(state, calendar) -> dict | None:  # type: ignore[no-untyped-def]
    found = state.deadline
    if found is None:
        return None
    return {"deadline": found.deadline_id, "found": found.found, "label": found.label, "passed": found.passed,
            "due": None if found.due is None else common.moment(calendar, _clock(found.due)),
            "flagged_by": found.triggered_by}


def _support_of(ctx: AdapterContext, campaign_id: str, ids):  # type: ignore[no-untyped-def]
    return read_support(ctx.store, campaign_id, ids, requested_by=DIRECTOR)


def _citable(ctx: AdapterContext, campaign_id: str, scene) -> tuple[list[dict], int]:  # type: ignore[no-untyped-def]
    """The campaign's 020 objective events the director's ledger holds and 077 reads as supported now, newest
    first, each with the turn of the chosen chat it is of (if it is of one)."""
    events = EvidenceLibrary(ctx.store).read(campaign_id, requested_by=DIRECTOR).knowledge.events
    support = _support_of(ctx, campaign_id, [event.event_id for event in events])
    turns = {} if scene is None else {record.turn_id: record.number for record in scene.records}
    chat_id = None if scene is None else scene.chat.chat_id
    found = [{"event": event.event_id, "summary": event.summary, "at": event.moment.sequence,
              "scene": event.moment.scene_id, "turn": event.moment.turn_id,
              "turn_number": turns.get(event.moment.turn_id) if event.moment.scene_id == chat_id else None}
             for event in events if support.values.get(event.event_id) == "supported"]
    found.sort(key=lambda item: (item["at"], item["event"]), reverse=True)
    return found[:MAX_CITABLE], len(found)


def view(ctx: AdapterContext, scope, chat, scene, names: common.Names) -> dict:  # type: ignore[no-untyped-def]
    campaign_id = scope.campaign_id
    found = library(ctx).read(scope, requested_by=DIRECTOR)
    calendar = found.calendar
    cited = {event_id for op in (found.ledger.operations if found.ledger else ()) for event_id in getattr(op, "cites", ())}
    support = _support_of(ctx, campaign_id, cited)
    controls = _controls(scene, campaign_id)
    commitments = []
    for state in found.commitments:
        commitment = state.commitment
        refs = {ref.instance_id: ref for ref in commitment.owed_by}
        backers = {}
        if found.ledger is not None:
            for op in (state.offer, *state.steps):
                if op.outcome in ("applied", "unsupported", "stranded"):
                    backers[op.operation.operation_id] = [{"actor": item.actor_id, "player": item.submitted_by}
                                                          for item in found.ledger.backers(op.operation.operation_id)]
        open_ = state.status in NEXT and state.certain
        commitments.append({
            "commitment": commitment.commitment_id, "kind": commitment.kind, "summary": commitment.summary,
            "owed_by": [{**names.who(ref), "controller": _controller(campaign_id, ref, controls)}
                        for ref in commitment.owed_by],
            "owed_to": [names.who(ref) for ref in commitment.owed_to],
            "status": state.status, "certain": state.certain, "overdue": state.overdue, "late": state.late,
            "at": state.moment.sequence, "deadline": _deadline_view(state, calendar),
            "offer": _step_view(state.offer, support, calendar, names, refs),
            "steps": [_step_view(step, support, calendar, names, refs) for step in state.steps],
            "next": sorted(NEXT[state.status]) if open_ else [],
            "deciders": _deciders(ctx, commitment, controls, names, chat),
            "voidable": [{"operation": key, "backers": value} for key, value in backers.items()],
        })
    citable, total = _citable(ctx, campaign_id, scene)
    return {"etag": found.etag, "commitments": commitments, "problems": list(found.problems),
            "citable": citable, "citable_total": total, "max_cites": MAX_CITES, "kinds": list(KINDS),
            "decisions": sorted(DECISIONS), "outcomes": sorted(OUTCOMES),
            "parties": [names.who(ref) for ref in common.campaign_actors(ctx, campaign_id)],
            "owner": ctx.owner}


# -- planning a change ------------------------------------------------------------------------------------------
def _cites(args: Mapping[str, object]) -> list[str]:
    value = args.get("cites")
    if (type(value) is not list or not 1 <= len(value) <= MAX_CITES or not all(type(item) is str for item in value)
            or len(set(value)) != len(value)):
        raise ApiRefused("invalid_field", f"cite from 1 to {MAX_CITES} distinct objective events")
    return sorted(value)


def _parties(args: Mapping[str, object], key: str, campaign_id: str) -> tuple:
    value = args.get(key)
    if type(value) is not list or not value:
        raise ApiRefused("invalid_field", f"{key} must list at least one of the campaign's actors")
    return tuple(common.actor(item, key, campaign_id) for item in value)


def _offer(ctx: AdapterContext, args: Mapping[str, object], scope, operation_id: str, expected: str):  # type: ignore[no-untyped-def]
    campaign_id = scope.campaign_id
    kind = args.get("kind")
    if kind not in KINDS:
        raise ApiRefused("invalid_field", f"kind must be one of {', '.join(KINDS)}")
    deadline = args.get("deadline")
    deadline_id, due = None, None
    if deadline is not None:
        if not isinstance(deadline, Mapping) or ("link" in deadline) == ("new" in deadline):
            raise ApiRefused("invalid_field", "deadline is either a link to one the calendar holds or a new one")
        if "link" in deadline:
            deadline_id = common.segment(deadline, "link", maximum=128)
        else:
            new = deadline.get("new")
            if not isinstance(new, Mapping):
                raise ApiRefused("invalid_field", "a new deadline has a date, a time and a label")
            deadline_id = common.segment(new, "deadline", maximum=100)
            due = Due(common.text(new, "date", maximum=64), common.text(new, "label", maximum=200),
                      common.whole(new, "minute", 0, 1439, default=0))
    commitment = Commitment(campaign_id, common.segment(args, "commitment", maximum=128), kind,
                            common.text(args, "summary", maximum=2000), _parties(args, "owed_by", campaign_id),
                            _parties(args, "owed_to", campaign_id), deadline_id)
    change = library(ctx).offer(scope, operation_id, commitment, cites=_cites(args), expected=expected, due=due)
    return change, [], {"due": due}


def _latest(ctx: AdapterContext, campaign_id: str, cites: list[str]):  # type: ignore[no-untyped-def]
    """The newest of the cited events, as 077 dates a step (``None`` when one is not on the ledger: 077 says why)."""
    support = _support_of(ctx, campaign_id, cites)
    if any(event_id not in support.events for event_id in cites):
        return None
    return max((support.events[event_id] for event_id in cites),
               key=lambda event: (event.moment.sequence, event.event_id)).moment


def _owner_backing(ctx: AdapterContext, args, scope, scene, needed, at, fingerprint):  # type: ignore[no-untyped-def]
    """The owner's own contribution for each of the owner's characters among ``needed``, from the turn the step's
    citations name, and the owner's endorsement of the exact step."""
    names = common.Names(ctx, scene)
    if at is None:
        return (), (), []  # a citation not on the ledger: 077 refuses it with its own reason
    if at.scene_id is None or at.turn_id is None:
        raise ApiRefused("no_turn", "your character's decision must cite an event of the scene turn where you made it")
    if scene is None or at.scene_id != scene.chat.chat_id:
        raise ApiRefused("other_chat", "the cited turn is in another chat: open this page for that chat")
    record = next((item for item in scene.records if item.turn_id == at.turn_id), None)
    if record is None:
        raise ApiRefused("unknown_turn", "this chat has no turn with the cited event's turn id")
    contributions, backing = [], []
    for ref in needed:
        post = next((item for item in post_contributions(record) if item.actor_id == ref.instance_id
                     and item.submitted_by == ctx.owner and "commitment" in CONTRIBUTION_DOMAINS.get(item.kind, ())),
                    None)
        if post is None:
            raise ApiRefused("no_backing", f"{names.name(ref) or ref} said nothing of yours in that turn for the "
                                           "decision to rest on (a speech post covers a commitment)")
        contributions.append(post)
        backing.append({**names.who(ref), "turn": record.turn_id, "turn_number": record.number, "text": post.text})
    endorsements = [Endorsement(item.contribution_id, ctx.handle, fingerprint) for item in contributions]
    return tuple(contributions), tuple(endorsements), backing


def _record(ctx: AdapterContext, args: Mapping[str, object], scope, operation_id: str, expected: str):  # type: ignore[no-untyped-def]
    campaign_id = scope.campaign_id
    commitment_id = common.segment(args, "commitment", maximum=128)
    status = args.get("status")
    if status not in DECISIONS | OUTCOMES:
        raise ApiRefused("invalid_field", "status must be a decision or an outcome after the offer")
    cites = _cites(args)
    note = common.text(args, "note", maximum=2000, empty=True)
    commitments = library(ctx)
    current = commitments.read(scope, requested_by=DIRECTOR)
    retry = current.ledger is not None and current.ledger.operation(operation_id) is not None
    if status in OUTCOMES or retry:
        # an outcome is the director's adjudication; a retry is 077's to compare with what it recorded
        return commitments.record(scope, operation_id, commitment_id, status, cites=cites, expected=expected,
                                  note=note), [], {}
    offer = None if current.ledger is None else current.ledger.offer(commitment_id)
    if offer is None:
        raise ApiRefused("unknown_commitment", "this campaign has no such commitment")
    found = common.chat(args, scope, required=False)
    scene = None if found is None else common.scene_of(ctx, found)
    controls = _controls(scene, campaign_id)
    if not controls:
        raise ApiRefused("no_controls", "choose a chat of this campaign: its control records say who decides")
    names = common.Names(ctx, scene)
    deciders = _deciders(ctx, offer.commitment, controls, names, found)
    if not deciders["available"]:
        raise ApiRefused(deciders["reason"], deciders["detail"])
    needed = [ref for ref in offer.commitment.owed_by if _controller(campaign_id, ref, controls) == ctx.owner]
    contributions, endorsements, backing = (), (), []
    if needed:
        fingerprint = transition_fingerprint(campaign_id, commitment_id, status, cites, note)
        contributions, endorsements, backing = _owner_backing(ctx, args, scope, scene, needed, _latest(ctx, campaign_id, cites),
                                                     fingerprint)
    change = commitments.record(scope, operation_id, commitment_id, status, cites=cites, expected=expected, note=note,
                                controls=controls, contributions=contributions, endorsements=endorsements)
    return change, [names.who(ref) for ref in needed], {"backing": backing}


def _void(ctx: AdapterContext, args: Mapping[str, object], scope, operation_id: str, expected: str):  # type: ignore[no-untyped-def]
    target = common.segment(args, "target", maximum=128)
    reason = common.text(args, "reason", maximum=2000)
    commitments = library(ctx)
    current = commitments.read(scope, requested_by=DIRECTOR)
    retry = current.ledger is not None and current.ledger.operation(operation_id) is not None
    backers = () if current.ledger is None or retry else current.ledger.backers(target)
    others = [item for item in backers if item.submitted_by != ctx.owner]
    if others:
        raise ApiRefused("not_your_character", "a player backed this step: voiding it needs their own key, and this "
                                               "screen takes only yours")
    fingerprint = void_fingerprint(scope.campaign_id, target, reason)
    endorsements = [Endorsement(item.contribution_id, ctx.handle, fingerprint) for item in backers]
    change = commitments.void(scope, operation_id, target, reason=reason, expected=expected, endorsements=endorsements)
    # whose key the void gives: each backer by name, as the offer's obligors name them (a step records ids)
    found = common.chat(args, scope, required=False)
    names = common.Names(ctx, None if found is None else common.scene_of(ctx, found))
    withdrawn = None if current.ledger is None else current.ledger.operation(target)
    offer = None if withdrawn is None else current.ledger.offer(withdrawn.commitment_id)
    refs = {} if offer is None else {ref.instance_id: ref for ref in offer.commitment.owed_by}
    return change, [{**(names.who(refs[item.actor_id]) if item.actor_id in refs
                        else {"actor": item.actor_id, "name": None}), "player": item.submitted_by} for item in backers], {}


def _planned(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    common.director(args, "commitments")
    scope = common.campaign(args)
    operation = args.get("operation")
    if operation not in OPERATIONS:
        raise ApiRefused("invalid_field", "operation must be offer, record or void")
    operation_id = common.segment(args, "operation_id", maximum=100)
    expected = common.expected(args)
    build = {"offer": _offer, "record": _record, "void": _void}[operation]
    change, keys, extra = build(ctx, args, scope, operation_id, expected)
    found = common.chat(args, scope, required=False)
    names = common.Names(ctx, None if found is None else common.scene_of(ctx, found))
    return scope, operation, change, keys, names, extra


def _decided_by(op, names: common.Names, current) -> list[dict]:  # type: ignore[no-untyped-def]
    offer = None if current.ledger is None else current.ledger.offer(op.commitment_id)
    refs = {} if offer is None else {ref.instance_id: ref for ref in offer.commitment.owed_by}
    return [{**(names.who(refs[actor]) if actor in refs else {"actor": actor, "name": None}), "controller": who}
            for actor, who in op.controls]


def _answer(ctx: AdapterContext, scope, operation, change, keys, names: common.Names, extra: dict) -> dict:  # type: ignore[no-untyped-def]
    current = library(ctx).read(scope, requested_by=DIRECTOR)
    calendar = current.calendar
    op = change.operation
    answer = {"kind": operation, "operation": op.operation_id, "already_applied": change.already_applied,
              "keys": keys}
    cites = getattr(op, "cites", ())
    if cites:
        support = _support_of(ctx, scope.campaign_id, cites)
        answer["cites"] = [{"event": event_id, "summary": getattr(support.events.get(event_id), "summary", None)}
                           for event_id in cites]
        answer["at"] = op.at.sequence
    if type(op) is Offer:
        commitment = op.commitment
        deadline = None
        if commitment.deadline_id is not None:
            due = None if calendar is None else next(
                (item for item in calendar.deadlines if item.deadline_id == commitment.deadline_id), None)
            planned = extra.get("due")  # the deadline this request schedules, as it laid it out
            if due is None and planned is not None and calendar is not None:
                due_view = common.moment(calendar, ClockMoment(calendar.calendar.parse(planned.native_date),
                                                               planned.minute))
                label = planned.label
            else:
                due_view = None if due is None else common.moment(calendar, due.due)
                label = None if due is None else due.label
            deadline = {"deadline": commitment.deadline_id, "schedules": op.schedules, "due": due_view, "label": label}
        answer.update({"commitment": commitment.commitment_id, "type": commitment.kind, "summary": commitment.summary,
                       "owed_by": [names.who(ref) for ref in commitment.owed_by],
                       "owed_to": [names.who(ref) for ref in commitment.owed_to], "deadline": deadline})
    elif type(op) is Transition:
        state = current.commitment(op.commitment_id)
        answer.update({"commitment": op.commitment_id, "status": op.status, "note": op.note,
                       "from": None if state is None else state.status,
                       "decided_by": _decided_by(op, names, current),
                       # who controlled each obligor as reviewed: the commit is refused if that has changed since
                       "reviewed_controls": [[actor, who] for actor, who in op.controls],
                       # the words of the owner's that the owner's key backs, recorded as the step's backing
                       "backing": extra.get("backing", [])})
    elif type(op) is Void:
        target = None if current.ledger is None else current.ledger.operation(op.target)
        answer.update({"target": op.target, "reason": op.reason, "withdraws": "commitment" if type(target) is Offer
                       else "step", "commitment": None if target is None else getattr(
                           target, "commitment_id", None)})
    return answer


def plan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """What the offer, step or void would record; nothing is written."""
    scope, operation, change, keys, names, extra = _planned(ctx, args)
    return {"plan": {**_answer(ctx, scope, operation, change, keys, names, extra), "files": sorted(change.preview())},
            "etag": library(ctx).read(scope, requested_by=DIRECTOR).etag}


def commit(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The change reviewed with ``commitment_plan``, built again as reviewed and committed once. A step that rests
    on the owner's own key is made only with ``endorse: true``: the owner gives it knowingly.

    Control of a character lives in the chat's records, not in the scope, so the etag cannot see it change: a
    decision is committed only with the controls it was reviewed under (``reviewed_controls``, as the plan answered
    them), and a key given for a step that no longer rests on it is refused, both as ``changed``."""
    scope, operation, change, keys, names, extra = _planned(ctx, args)
    if not change.already_applied:
        op = change.operation
        if type(op) is Transition and op.status in DECISIONS:
            reviewed = args.get("reviewed_controls")
            if reviewed is None:
                raise ApiRefused("review_required", "commit a decision with the controls its review answered")
            if reviewed != [[actor, who] for actor, who in op.controls]:
                raise ApiRefused("changed", "who controls the obligors changed since the review; nothing was written")
        if args.get("endorse") is True and not keys:
            raise ApiRefused("changed", "this no longer rests on your own key: review it again; nothing was written")
        if keys and args.get("endorse") is not True:
            raise ApiRefused("player_key_required", "this records your own character's decision as yours: give your "
                                                    "key to make it; nothing was written")
    change.commit()
    return {"done": _answer(ctx, scope, operation, change, keys, names, extra)}


OPS = {"commitment_plan": plan, "commitment_commit": commit}
