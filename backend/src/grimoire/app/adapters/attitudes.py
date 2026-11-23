"""Sprint 116's attitude ops (078): the director checks one NPC's attitude toward another actor or a group against
that NPC's own evidence. Called as ``attitudes.<op>``; see docs/ATTITUDES_PAGE.md and docs/RELATIONSHIPS.md.

Only Sprint 116 edits this module. 078 is a pure, in-memory contract with **no store**: nothing here is saved, and
no brief or NPC reads an attitude yet. ``check`` is a check, not a write:

- **The director's.** An attitude is director-authored NPC interiority: every op refuses ``director_only`` unless
  ``audience`` is ``director``. A player character's feelings are its player's: 078 refuses them
  (``pc_interiority``), and the page never offers a player character as a holder.
- **The holder's own grounds.** ``grounds`` answers the holder's own evidence and current beliefs exactly as the
  campaign hub shows them for that actor (the hub's ``knowledge`` perspective, as the Chronicle offers evidence to
  cite), never another actor's. ``check`` admits only grounds among those, and 078 checks them again itself.
- **A client-held ledger, re-validated from scratch.** The page keeps, in memory, the attitudes this check has
  admitted while the page is open (``held``). Every ``check`` admits them again in order into a new, empty 078
  ledger against the library as it is now, then the new attitude; nothing the client sends is trusted as admitted.
  A held one no longer admitted is ``held_not_admitted`` (078's reason and its id in ``detail``): the page drops
  what it holds only then. The answer is a verdict (``admitted``, or 078's refusal reason, with the admitted
  attitude as the page holds it), the holder's current attitudes in that ledger and the knowledge digest before and
  after, which is unchanged.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.campaignapi import parse
from grimoire.core import DIRECTOR
from grimoire.core.entity import EntityInstance
from grimoire.core.identity import InstanceRef, SourceRef, validate_segment
from grimoire.core.knowledge import Moment
from grimoire.relationships import (BIPOLAR, DIMENSIONS, MAX_DEGREE, MAX_GROUNDS, TEXT_CHARS, Attitude,
                                    AttitudeLedger, RelationshipError, admit, attitudes_of)
from grimoire.textapi import ApiRefused

from .campaign import hub_of
from .world.cast import Cast, cast_of
from .world.common import knowledge as campaign_knowledge

#: At most this many attitudes are held on the page and checked again together (078 retains 256).
MAX_HELD = 32
PROVENANCE = "director's check on the Attitudes page"
#: What the page says instead of a control, for what 078 does not do yet.
NOT_AVAILABLE = {
    "save": "078 has no store yet: an attitude checked here is kept only on this page while it is open, and is "
            "never written to the library",
    "play": "no NPC plays from an attitude yet: the per-NPC brief (027) and goals (039) do not read dispositions",
    "reputation": "public reputation held by a community, and a change of allegiance, are 078's later milestones",
}


def _director(args: Mapping[str, object]) -> None:
    audience = parse.field(args, "audience", str, required=False, default="table")
    if audience not in ("table", "director"):
        raise ApiRefused("invalid_field", "audience must be table or director")
    if audience != "director":
        raise ApiRefused("director_only", "an NPC's attitudes are the director's")


def _high(knowledge) -> int:  # type: ignore[no-untyped-def]
    """The campaign's current sequence, as 060's preview takes it: its latest recorded moment."""
    records = (*knowledge.events, *knowledge.propositions, *knowledge.rulings, *knowledge.evidence,
               *knowledge.beliefs, *knowledge.disclosures)
    return max((item.moment.sequence for item in records), default=0)


def _digest(ctx: AdapterContext, campaign: str) -> str:
    scan = ctx.store.campaign_knowledge(campaign)
    return hashlib.sha256(repr((scan.entries, scan.problems)).encode()).hexdigest()


def _groups(ctx: AdapterContext, campaign: str) -> dict[InstanceRef, str | None]:
    """The campaign's groups (instances of kind ``group``), by the name their definition or record gives them."""
    view = ctx.store.load()
    found: dict[InstanceRef, str | None] = {}
    for ref, stored in view.instances.items():
        if ref.campaign_id != campaign or ref.kind != "group":
            continue
        name = None
        try:
            name = view.index.resolve(SourceRef.parse(stored.envelope.derived_from)).name
        except (TypeError, ValueError, LookupError):
            payload = getattr(stored.envelope, "payload", None)
            local = payload.get("local_name") if isinstance(payload, Mapping) else None
            name = local if isinstance(local, str) else getattr(stored.envelope, "name", None)
        found[ref] = name if isinstance(name, str) else None
    return dict(sorted(found.items(), key=lambda item: str(item[0])))


def _member(member) -> dict:  # type: ignore[no-untyped-def]
    holds = member.actor.kind == "character" and member.role == "npc" and member.agent
    why = None if holds else ("a player character's feelings are its player's" if member.role == "player"
                              else "nobody plays them" if member.role == "unseated"
                              else "not a character agent")
    return {**member.view(), "may_hold": holds, "why_not": why}


def _setting(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    _director(args)
    chat = parse.chat_of(args)
    cast = cast_of(ctx, chat)
    return chat, cast, _groups(ctx, chat.campaign_id)


def _cast(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Who may hold an attitude here (the chat's director-controlled NPCs), whom it may point at (the cast and the
    campaign's groups), and 078's dimensions and limits."""
    chat, cast, groups = _setting(ctx, args)
    knowledge = campaign_knowledge(ctx, chat.campaign_id)
    return {"chat": str(chat), "cast": [_member(member) for member in cast.members],
            "groups": [{"actor": str(ref), "name": name} for ref, name in groups.items()],
            "dimensions": list(DIMENSIONS), "bipolar": sorted(BIPOLAR), "max_degree": MAX_DEGREE,
            "max_grounds": MAX_GROUNDS, "max_text": TEXT_CHARS, "max_held": MAX_HELD, "now": _high(knowledge),
            "saved": False, "not_available": dict(NOT_AVAILABLE)}


def _shown(ctx: AdapterContext, chat, cast: Cast, holder: InstanceRef) -> dict:  # type: ignore[no-untyped-def]
    """The holder's own evidence and current beliefs, exactly as the campaign hub shows them for that actor."""
    if cast.get(holder) is None:
        raise ApiRefused("no_actor", f"{holder} is not in this chat's cast")
    known = hub_of(ctx).call("knowledge", {"chat": str(chat), "actor": str(holder), "audience": "director"})
    (view,) = known["actors"]
    evidence = [{"evidence": item["evidence"], "type": item["type"], "channel": item["channel"],
                 "content": item["content"], "at": (item["moment"] or {}).get("sequence"),
                 "attributed_to": item["attributed_to"]} for item in view["observations"]]
    beliefs = [{"belief": item["belief"], "text": item["text"], "stance": item["stance"],
                "at": (item["moment"] or {}).get("sequence")} for item in view["beliefs"]]
    evidence.sort(key=lambda item: (-(item["at"] or 0), item["evidence"]))
    beliefs.sort(key=lambda item: (-(item["at"] or 0), item["belief"]))
    return {"evidence": evidence, "beliefs": beliefs}


def _grounds(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    chat, cast, _ = _setting(ctx, args)
    holder = parse.actor_of(args.get("holder"), "holder", chat.campaign_id)
    member = cast.get(holder)
    if member is not None and member.role == "player":  # 078's own reason: never shown as the director's to use
        raise ApiRefused("pc_interiority", "a player character's feelings are its player's, not the director's")
    return {"chat": str(chat), "holder": None if member is None else member.view(),
            **_shown(ctx, chat, cast, holder)}


def _ids(value: object, key: str) -> tuple[str, ...]:
    if type(value) is not list or len(value) > MAX_GROUNDS or not all(type(item) is str for item in value):
        raise ApiRefused("invalid_field", f"{key} must be a list of at most {MAX_GROUNDS} ids")
    return tuple(value)


def _attitude(item: object, campaign: str, now: int) -> Attitude:
    """One attitude as the page wrote it; 078's own shape refusals are kept as verdicts by the caller."""
    if not isinstance(item, Mapping):
        raise ApiRefused("invalid_field", "each attitude is an object")
    attitude_id = item.get("id")
    if type(attitude_id) is not str:
        raise ApiRefused("invalid_field", "an attitude's id is text")
    try:
        validate_segment(attitude_id, "id")
    except ValueError:
        raise ApiRefused("invalid_field", "an attitude's id is not a valid id") from None
    degree = item.get("degree")
    if degree is not None and type(degree) is not int:
        raise ApiRefused("invalid_field", "degree is a whole number, or none")
    at = item.get("at", now)
    if type(at) is not int or at < 0:
        raise ApiRefused("invalid_field", "at is a moment of the campaign (a whole number)")
    for key in ("dimension", "account"):
        if type(item.get(key)) is not str:
            raise ApiRefused("invalid_field", f"an attitude's {key} is text")
    supersedes = item.get("supersedes")
    if supersedes is not None and type(supersedes) is not str:
        raise ApiRefused("invalid_field", "supersedes names an earlier attitude's id")
    holder = parse.actor_of(item.get("holder"), "holder", campaign)
    toward = parse.actor_of(item.get("toward"), "toward", campaign)
    return Attitude(attitude_id, holder, toward, item["dimension"], item["account"], Moment(at), PROVENANCE,
                    evidence_ids=_ids(item.get("evidence", []), "evidence"),
                    belief_ids=_ids(item.get("beliefs", []), "beliefs"), degree=degree, supersedes=supersedes)


class HeldNotAdmitted(ApiRefused):
    """An attitude the page held is no longer admitted (the library changed under it): the page drops what it holds.
    Its own reason is 078's, kept in ``detail`` with the attitude's id."""

    def __init__(self, attitude_id: str, reason: str) -> None:
        super().__init__("held_not_admitted", f"{attitude_id}, held on this page, is no longer admitted ({reason})")
        self.attitude_id, self.held_reason = attitude_id, reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.attitude_id, self.held_reason))

    def to_json(self) -> dict:
        return {**super().to_json(), "detail": {"attitude": self.attitude_id, "reason": self.held_reason}}


def _admit(ledger: AttitudeLedger, attitude: Attitude, shown_of, cast: Cast,  # type: ignore[no-untyped-def]
           groups: Mapping[InstanceRef, str | None], knowledge, now: int) -> AttitudeLedger:
    """078's admission, then the page's own rule: every ground is among what the hub shows for the holder."""
    roster = cast.roster(*(EntityInstance(ref, local_name=name) for ref, name in groups.items()
                           if cast.get(ref) is None))
    ledger = admit(ledger, attitude, knowledge=knowledge, roster=roster, definitions=cast.definitions(),
                   controls=cast.controls, authored_by=DIRECTOR, now=now, expected_revision=ledger.revision)
    shown = shown_of(attitude.holder)
    if not (set(attitude.evidence_ids) <= {item["evidence"] for item in shown["evidence"]}
            and set(attitude.belief_ids) <= {item["belief"] for item in shown["beliefs"]}):
        raise RelationshipError("unavailable_grounds")  # 078 admits only the holder's own; so does this page
    return ledger


def _name(cast: Cast, groups: Mapping[InstanceRef, str | None], ref: InstanceRef) -> str | None:
    return cast.name(ref) if cast.get(ref) is not None else groups.get(ref)


def _head(attitude: Attitude, cast: Cast, groups: Mapping[InstanceRef, str | None]) -> dict:
    return {"id": attitude.attitude_id, "holder": str(attitude.holder), "toward": str(attitude.toward),
            "toward_name": _name(cast, groups, attitude.toward), "dimension": attitude.dimension,
            "account": attitude.account, "degree": attitude.degree, "at": attitude.moment.sequence,
            "evidence": list(attitude.evidence_ids), "beliefs": list(attitude.belief_ids),
            "supersedes": attitude.supersedes}


def _check(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Admit the held attitudes again, in order, into a new empty ledger, then ``attitude``; answer 078's verdict
    on it. Nothing is written: the ledger lives for this one answer."""
    chat, cast, groups = _setting(ctx, args)
    campaign = chat.campaign_id
    before = _digest(ctx, campaign)
    knowledge = campaign_knowledge(ctx, campaign)
    now = _high(knowledge)
    held = args.get("held", [])
    if type(held) is not list or len(held) > MAX_HELD:
        raise ApiRefused("invalid_field", f"held is a list of at most {MAX_HELD} attitudes this page admitted")
    seen: dict[InstanceRef, dict] = {}

    def shown_of(holder: InstanceRef) -> dict:  # what the hub shows a holder, read once per check
        if holder not in seen:
            seen[holder] = _shown(ctx, chat, cast, holder)
        return seen[holder]

    ledger = AttitudeLedger(campaign)
    for item in held:
        held_attitude = _attitude(item, campaign, now)
        try:
            ledger = _admit(ledger, held_attitude, shown_of, cast, groups, knowledge, now)
        except RelationshipError as error:  # the library changed under a held one: keep 078's reason
            raise HeldNotAdmitted(held_attitude.attitude_id, error.reason) from None
        except ApiRefused as error:
            if error.reason != "no_actor":
                raise
            raise HeldNotAdmitted(held_attitude.attitude_id, error.reason) from None
    attitude = None
    try:  # 078's verdict on the new one, a shape refusal (``invalid_text``, ``self_attitude``...) included
        attitude = _attitude(args.get("attitude"), campaign, now)
        ledger = _admit(ledger, attitude, shown_of, cast, groups, knowledge, now)
        verdict = {"admitted": True, "reason": None, "attitude": _head(attitude, cast, groups)}
    except RelationshipError as error:
        verdict = {"admitted": False, "reason": error.reason, "attitude": None}
    member = None if attitude is None else cast.get(attitude.holder)
    current: list[dict] = []
    if member is not None and member.role == "npc":
        try:
            current = [_head(item, cast, groups)
                       for item in attitudes_of(ledger, attitude.holder, as_of=now, controls=cast.controls)]
        except RelationshipError:
            current = []
    after = campaign_knowledge(ctx, campaign)
    return {"chat": str(chat), "saved": False, "now": now, "verdict": verdict,
            "held": [item.attitude_id for item in ledger.records], "current": current,
            "unchanged": _digest(ctx, campaign) == before and after == knowledge,
            "not_available": dict(NOT_AVAILABLE)}


ADAPTER = Adapter("attitudes", owner="116", ops={"cast": _cast, "grounds": _grounds, "check": _check},
                  refusals={RelationshipError: "attitude_refused"})

__all__ = ["ADAPTER", "MAX_HELD", "NOT_AVAILABLE"]
