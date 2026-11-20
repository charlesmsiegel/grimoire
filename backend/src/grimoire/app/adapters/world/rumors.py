"""063's rumors on the World page: who told whom, faithfully or not, by which route, when each arrives on the
campaign's 055 calendar, and whether its recipient's belief was admitted. Admitting that belief is the director's
own act (020's ``admit_belief``, through ``EvidenceLibrary.append``), reviewed before it is made.

The rumors are read from 063's own verified ``rumors/<campaign>`` ledger with ``grimoire.rumorledger.replay``, the
reader every actor projection already uses for the senders' dispatches; the page is the director's, so the whole
trace is shown, and only in the director's view. Nothing here sends, routes or delivers a rumor: those are
``RumorLibrary``'s acts on its own application key and store grant, which the app does not hold yet.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext
from grimoire.core import DIRECTOR, Belief
from grimoire.core.authority import AuthorityError, controller_of
from grimoire.core.identity import InstanceRef
from grimoire.core.knowledge import STANCES, Moment, Perception
from grimoire.evidencestore import EvidenceLibrary
from grimoire.ledgers import SovereigntyLedger
from grimoire.rumorledger import ledger_name, output_id, replay
from grimoire.textapi import ApiRefused

from . import common
from .cast import cast_of

#: A belief this page admits is named for the operation that admitted it, so a retry finds its own.
BELIEF_PREFIX = "admit-"


def _knowledge(ctx: AdapterContext, campaign: str):  # type: ignore[no-untyped-def]
    found = EvidenceLibrary(ctx.store).read(campaign, requested_by=DIRECTOR)
    return found.knowledge


def _rows(ctx: AdapterContext, campaign: str) -> dict:
    rows, _ = replay(campaign, ctx.store.read_ledger(ledger_name(campaign)).entries)
    return rows


def _content(knowledge, evidence_id: str) -> str | None:  # type: ignore[no-untyped-def]
    for item in knowledge.evidence:
        if item.evidence_id == evidence_id:
            if isinstance(item, Perception):
                return item.percept
            proposition = next((p for p in knowledge.propositions if p.proposition_id == item.proposition_id), None)
            return None if proposition is None else proposition.text
    return None


def _belief(knowledge, holder: InstanceRef, proposition: str):  # type: ignore[no-untyped-def]
    """The holder's standing belief in the rumor's proposition (the latest not superseded), or None."""
    held = [item for item in knowledge.beliefs if item.holder == holder and item.proposition_id == proposition]
    replaced = {item.supersedes for item in held}
    standing = [item for item in held if item.belief_id not in replaced]
    return max(standing, key=lambda item: (item.moment.sequence, item.belief_id)) if standing else None


def _who(ctx: AdapterContext, ref: InstanceRef, controls, names) -> dict:  # type: ignore[no-untyped-def]
    try:
        controller = controller_of(ref.campaign_id, ref.instance_id, controls)
    except AuthorityError:  # an ambiguous control is shown as no single controller, never guessed
        controller = None
    role = "unseated" if controller is None else "npc" if controller == DIRECTOR else "player"
    return {"actor": str(ref), "name": common.instance_name(ctx, ref, names), "role": role}


def _status(row: dict, now) -> str:  # type: ignore[no-untyped-def]
    if row["delivered"]:
        return "delivered"
    if not row["enqueued"]:
        return "not_sent"
    due = row["rumor"].due_on
    return "due" if now is not None and (due.fixed_day, due.minute) <= (now.fixed_day, now.minute) else "on_the_way"


def _names(ctx: AdapterContext, args: Mapping[str, object], campaign: str) -> dict:
    """The cast names of the chat the page is open on (022's), when it gives one: a card's portrayal is named
    there and nowhere in its campaign's records. The chat must be this campaign's."""
    if args.get("chat") is None:
        return {}
    chat = common.chat(args)
    if chat.campaign_id != campaign:
        raise ApiRefused("invalid_field", "chat must be one of this campaign's chats")
    return {member.actor: member.name for member in cast_of(ctx, chat).members if member.name}


def _scanned(ctx: AdapterContext, campaign: str) -> tuple:
    """The evidence ledger's revision as 020's append compares it: every file under it, and what is wrong there."""
    scan = ctx.store.campaign_knowledge(campaign)
    return scan.entries, scan.problems


def _view(ctx: AdapterContext, campaign: str, names: Mapping, scanned: tuple | None = None) -> dict:
    """The rumors as shown. Their etag covers the evidence ledger's revision too (``scanned``, read now unless
    given): an admission is appended to that ledger, so anything appended there since the review refuses it."""
    entries, problems = _scanned(ctx, campaign) if scanned is None else scanned
    rows = _rows(ctx, campaign)
    knowledge = _knowledge(ctx, campaign)
    controls = SovereigntyLedger(ctx.authority).load(campaign).controls
    state = common.calendar_state(ctx, campaign)
    calendar = None if state is None else state.calendar
    shown = []
    for rid in sorted(rows, key=lambda key: (rows[key]["rumor"].due_on, key)):
        row = rows[rid]
        rumor = row["rumor"]
        proposition = output_id(campaign, rid, "proposition")
        belief = _belief(knowledge, rumor.recipient, proposition)
        recipient = _who(ctx, rumor.recipient, controls, names)
        status = _status(row, None if state is None else state.now)
        shown.append({
            "rumor": rid, "sender": _who(ctx, rumor.sender, controls, names), "recipient": recipient,
            "attributed_to": None if rumor.attributed_to is None else _who(ctx, rumor.attributed_to, controls, names),
            "source": {"evidence": rumor.source_evidence_id, "content": _content(knowledge, rumor.source_evidence_id)},
            "text": rumor.text, "channel": rumor.channel, "confidence": rumor.confidence,
            "distortion": rumor.distortion, "faithful": rumor.distortion.strip().casefold() == "none",
            "provenance": rumor.provenance,
            "sent": common.moment_view(calendar, rumor.sent_on.fixed_day, rumor.sent_on.minute),
            "due": common.moment_view(calendar, rumor.due_on.fixed_day, rumor.due_on.minute),
            "route": {"proximity": rumor.route.proximity, "affiliation": rumor.route.affiliation,
                      "channel": rumor.route.channel, "attention": rumor.route.attention},
            "status": status, "report": output_id(campaign, rid, "report"),
            "belief": None if belief is None else {"belief": belief.belief_id, "stance": belief.stance},
            "can_admit": status == "delivered" and belief is None and recipient["role"] == "npc"})
    answer = {"campaign": campaign,
              "now": None if state is None else common.moment_view(calendar, state.now.fixed_day, state.now.minute),
              "rumors": shown}
    revision = {"entries": [list(item) for item in entries], "problems": sorted(f"{p.path}:{p.code}" for p in problems)}
    digest = hashlib.sha256(json.dumps({**answer, "evidence": revision}, sort_keys=True).encode()).hexdigest()
    return {**answer, "etag": "rumors:" + digest[:32]}


def _rumors(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    common.director(args)
    campaign = common.present(ctx, common.campaign(args))
    return _view(ctx, campaign, _names(ctx, args, campaign))


def _admission(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    """The belief the request names and the 020 append that would admit it (nothing is written), or, for an
    operation already made, the belief it admitted."""
    common.director(args)
    campaign = common.present(ctx, common.campaign(args))
    names = _names(ctx, args, campaign)
    rid = common.segment(args, "rumor")
    operation_id = common.segment(args, "operation_id")
    stance = args.get("stance")
    if stance not in STANCES:
        raise ApiRefused("invalid_field", "stance must be " + ", ".join(STANCES))
    expected = common.expected(args)
    rows = _rows(ctx, campaign)
    if rid not in rows:
        raise ApiRefused("no_rumor", "this campaign has no such rumor")
    rumor = rows[rid]["rumor"]
    proposition = output_id(campaign, rid, "proposition")
    report = output_id(campaign, rid, "report")
    belief_id = BELIEF_PREFIX + operation_id
    scanned = _scanned(ctx, campaign)  # first: the knowledge read after it, and the append built, must still match it
    knowledge = _knowledge(ctx, campaign)
    made = next((item for item in knowledge.beliefs if item.belief_id == belief_id), None)
    plan = {"operation": operation_id, "rumor": rid, "belief": belief_id, "stance": stance,
            "holder": str(rumor.recipient), "text": rumor.text}
    if made is not None:  # a retry: the same act is already made, another under this id is refused
        if (made.holder, made.proposition_id, made.stance, made.evidence_ids) != (rumor.recipient, proposition,
                                                                                   stance, (report,)):
            raise ApiRefused("operation_conflict", "this operation id already admitted another belief")
        return campaign, names, None, {**plan, "already_applied": True, "files": []}
    if _view(ctx, campaign, names, scanned)["etag"] != expected:
        raise ApiRefused("changed", "the rumors changed since they were reviewed; read them again")
    if not rows[rid]["delivered"]:
        raise ApiRefused("not_delivered", "a belief is admitted only once the rumor has reached its recipient")
    if _belief(knowledge, rumor.recipient, proposition) is not None:
        raise ApiRefused("already_admitted", "the recipient's belief in this rumor was already admitted")
    records = (*knowledge.events, *knowledge.propositions, *knowledge.rulings, *knowledge.evidence,
               *knowledge.beliefs, *knowledge.disclosures)
    at = Moment(max((item.moment.sequence for item in records), default=0) + 1)
    belief = Belief(campaign, belief_id, rumor.recipient, proposition, stance, at, DIRECTOR, (report,))
    controls = SovereigntyLedger(ctx.authority).load(campaign).controls
    change = EvidenceLibrary(ctx.store, ctx.handle, owner=ctx.owner).append(campaign, [belief], controls=controls)
    if change.snapshot != scanned:  # appended to since the revision checked: this is not the batch reviewed
        raise ApiRefused("changed", "the rumors changed since they were reviewed; read them again")
    return campaign, names, change, {**plan, "already_applied": False, "files": sorted(change.preview())}


def _belief_plan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """What admitting the recipient's belief would write; nothing is written."""
    campaign, names, _, plan = _admission(ctx, args)
    return {"plan": plan, "rumors": _view(ctx, campaign, names)}


def _belief_commit(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The admission reviewed with ``belief_plan``, built again against the same revision and made once."""
    campaign, names, change, plan = _admission(ctx, args)
    if change is not None:
        change.commit()
    return {"done": plan, "rumors": _view(ctx, campaign, names)}


OPS = {"rumors": _rumors, "belief_plan": _belief_plan, "belief_commit": _belief_commit}
