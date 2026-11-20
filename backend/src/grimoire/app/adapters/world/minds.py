"""060's social hypotheses on the World page: what an NPC thinks another character thinks (or thinks a third one
believes), authored by the director, each resting on evidence the NPC holds, and the tactic it proposes.

060 is an in-memory contract with no store: the director's hypotheses are admitted one by one (``admit``), in the
order written, into a ledger that lives for this one answer; ``recall`` and ``choose_tactic`` read it. Nothing is
saved and nothing is inferred: the claim stays the director's own words, and admission checks only that the NPC is
a director-controlled agent holding the cited evidence. The answer shows the director's truth and every cast
member's actual beliefs as the library holds them, read again after the preview, so the page can say they did not
change.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext
from grimoire.core import DIRECTOR
from grimoire.core.knowledge import Moment
from grimoire.core.perspective import actor_knowledge
from grimoire.evidencestore import EvidenceLibrary
from grimoire.social_hypotheses import MAX_RECORDS, Hypothesis, HypothesisLedger, admit, choose_tactic, recall
from grimoire.textapi import ApiRefused

from . import common
from .cast import Cast, cast_of

PROVENANCE = "director's conjecture on the World page"
#: The most recent hypotheses recall answers (060's own maximum).
RECALL = 8


def _high(knowledge) -> int:  # type: ignore[no-untyped-def]
    records = (*knowledge.events, *knowledge.propositions, *knowledge.rulings, *knowledge.evidence,
               *knowledge.beliefs, *knowledge.disclosures)
    return max((item.moment.sequence for item in records), default=0)


def _truth(knowledge, cast: Cast) -> dict:  # type: ignore[no-untyped-def]
    """The director's rulings and each cast member's own standing beliefs: what a hypothesis never changes."""
    texts = {item.proposition_id: item.text for item in knowledge.propositions}
    high = _high(knowledge)
    return {"rulings": [{"ruling": item.ruling_id, "proposition": item.proposition_id,
                         "text": texts.get(item.proposition_id), "standing": item.standing}
                        for item in sorted(knowledge.rulings, key=lambda item: item.ruling_id)],
            "beliefs": [{"actor": str(member.actor), "name": member.name,
                         "beliefs": [{"belief": item.belief_id, "proposition": item.proposition_id, "text": item.text,
                                      "stance": item.stance}
                                     for item in actor_knowledge(knowledge, member.actor, as_of=high).current]}
                        for member in cast.members]}


def _owner(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    common.director(args)
    chat = common.chat(args)
    cast = cast_of(ctx, chat)
    owner = common.actor(args.get("owner"), "owner", chat.campaign_id)
    member = cast.get(owner)
    if member is None:
        raise ApiRefused("no_actor", f"{owner} is not in this chat's cast")
    knowledge = EvidenceLibrary(ctx.store).read(chat.campaign_id, requested_by=DIRECTOR).knowledge
    return chat, cast, member, knowledge


def _mind(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The NPC's own confirmed evidence, newest first, and the truth beside it."""
    chat, cast, member, knowledge = _owner(ctx, args)
    held = actor_knowledge(knowledge, member.actor, as_of=_high(knowledge)).evidence
    return {"chat": str(chat), "owner": member.view(), "now": _high(knowledge),
            "evidence": [{"evidence": item.evidence_id, "kind": item.kind, "channel": item.channel,
                          "content": item.content, "at": item.moment.sequence}
                         for item in sorted(held, key=lambda item: (-item.moment.sequence, item.evidence_id))],
            "truth": _truth(knowledge, cast), "max": MAX_RECORDS}


def _ledger_digest(ctx: AdapterContext, campaign: str) -> str:
    scan = ctx.store.campaign_knowledge(campaign)
    return hashlib.sha256(repr((scan.entries, scan.problems)).encode()).hexdigest()


def _hypothesis(item: object, owner, campaign: str):  # type: ignore[no-untyped-def]
    if not isinstance(item, Mapping):
        raise ApiRefused("invalid_field", "each hypothesis must be an object")
    hid = common.segment(item, "id")
    about = common.actors(item.get("about"), "about", campaign, maximum=2)
    confidence = item.get("confidence")
    if type(confidence) not in (int, float):
        raise ApiRefused("invalid_field", "confidence must be a number from 0 to 1")
    evidence = item.get("evidence")
    if type(evidence) is not list or not all(type(value) is str for value in evidence):
        raise ApiRefused("invalid_field", "evidence must be a list of evidence ids")
    at = item.get("at")
    if type(at) is not int or at < 0:
        raise ApiRefused("invalid_field", "at must be a moment of the campaign (a whole number)")
    supersedes = None if item.get("supersedes") is None else common.segment(item, "supersedes")
    return Hypothesis(hid, owner, about, common.text(item.get("claim"), "claim"), confidence, tuple(evidence),
                      Moment(at), PROVENANCE, supersedes)


def _preview(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Admit the director's hypotheses in order into a ledger of this answer alone, and say what the NPC holds
    current and which tactic the chosen one proposes. Nothing is written; a refusal names the hypothesis."""
    chat, cast, member, knowledge = _owner(ctx, args)
    campaign = chat.campaign_id
    before = _ledger_digest(ctx, campaign)
    written = args.get("hypotheses")
    if type(written) is not list or not 1 <= len(written) <= MAX_RECORDS:
        raise ApiRefused("invalid_field", f"write from 1 to {MAX_RECORDS} hypotheses")
    ledger = HypothesisLedger(campaign)
    for n, item in enumerate(written):
        hypothesis = _hypothesis(item, member.actor, campaign)
        try:
            ledger = admit(ledger, hypothesis, knowledge=knowledge, roster=cast.roster(),
                           definitions=cast.definitions(), controls=cast.controls, authored_by=DIRECTOR,
                           expected_revision=n)
        except ValueError as error:  # say which one 060 refused, keeping its reason
            raise ApiRefused(common.reason_of(error, "hypothesis_refused"),
                             f"hypothesis {hypothesis.hypothesis_id}: {error}") from None
    as_of = max(item.moment.sequence for item in ledger.records)
    current = recall(ledger, member.actor, as_of=as_of, limit=RECALL)
    tactic = None
    asked = args.get("tactic")
    if asked is not None:
        if not isinstance(asked, Mapping):
            raise ApiRefused("invalid_field", "tactic must be an object")
        minimum = asked.get("minimum")
        if type(minimum) not in (int, float):
            raise ApiRefused("invalid_field", "minimum must be a number from 0 to 1")
        chosen = choose_tactic(ledger, member.actor, common.segment(asked, "hypothesis"), as_of=as_of,
                               minimum_confidence=minimum, credited_tactic=common.text(asked.get("credited"), "credited"),
                               fallback_tactic=common.text(asked.get("fallback"), "fallback"))
        tactic = {"hypothesis": chosen.hypothesis_id, "confidence": chosen.confidence, "tactic": chosen.tactic,
                  "credited": chosen.credited, "evidence": list(chosen.evidence_ids), "minimum": minimum}
    after = EvidenceLibrary(ctx.store).read(campaign, requested_by=DIRECTOR).knowledge
    return {"saved": False, "owner": member.view(), "as_of": as_of,
            "admitted": [item.hypothesis_id for item in ledger.records],
            "current": [{"id": item.hypothesis_id, "about": [{"actor": str(ref), "name": cast.name(ref)}
                                                             for ref in item.inferred_actors],
                         "claim": item.claim, "confidence": item.confidence, "evidence": list(item.evidence_ids),
                         "at": item.moment.sequence, "supersedes": item.supersedes} for item in current],
            "tactic": tactic, "truth": _truth(after, cast),
            "unchanged": _ledger_digest(ctx, campaign) == before and after == knowledge}


OPS = {"mind": _mind, "mind_preview": _preview}
