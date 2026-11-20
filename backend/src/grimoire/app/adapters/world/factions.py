"""057's faction decision on the World page: the director lays out a group from this chat's cast, its members, each
member's own preference and the leader's choice, and sees what 057's ``decide`` answers: the decision, the dissent it
keeps and the agreement beside it. 057 is a pure policy with no store, so nothing is saved, and nothing executes the
choice or tells anyone of it: a dissenting member's help becomes canon only as a separate accepted event.

The group, its goals and the members' ``member_of`` relations are laid out for this one answer, in memory; the
members are the cast's own characters, with their recorded control (only director-controlled NPCs may decide or
prefer, 057's ``npc_authority``).
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext
from grimoire.core import DIRECTOR
from grimoire.core.entity import EntityInstance
from grimoire.core.identity import InstanceRef, RelationRef
from grimoire.core.knowledge import Moment
from grimoire.graph.model import Relation
from grimoire.institutions import (
    Institution,
    InstitutionState,
    MemberPreference,
    decide,
)
from grimoire.textapi import ApiRefused

from . import common
from .cast import cast_of

#: The laid-out group's id in the campaign (in memory only) and the one proposal it decides.
GROUP_ID = "world-page-group"
PROPOSAL = "proposal"
LAID_OUT = "laid out by the director on the World page"
MAX_MEMBERS = 64
MAX_GOALS = 16


def _cast(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The chat's cast as the faction and minds sections lay them out: ref, name, NPC or player, agent."""
    common.director(args)
    chat = common.chat(args)
    cast = cast_of(ctx, chat)
    return {"chat": str(chat), "cast": [member.view() for member in cast.members]}


def _preview(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    common.director(args)
    chat = common.chat(args)
    campaign = chat.campaign_id
    cast = cast_of(ctx, chat)
    group = InstanceRef(campaign, "group", GROUP_ID)
    if cast.get(group) is not None or any(m.actor.instance_id == GROUP_ID for m in cast.members):
        raise ApiRefused("invalid_field", "a cast member already has the laid-out group's id")
    name = common.text(args.get("group"), "group", maximum=200)
    leader = common.actor(args.get("leader"), "leader", campaign)
    goals = args.get("goals")
    if type(goals) is not list or not 1 <= len(goals) <= MAX_GOALS:
        raise ApiRefused("invalid_field", f"give from 1 to {MAX_GOALS} goals")
    goals = tuple(common.text(goal, "goal") for goal in goals)
    policy = common.text(args.get("policy"), "policy")
    proposal = common.text(args.get("proposal"), "proposal")
    choice = common.text(args.get("choice"), "choice")
    members = args.get("members")
    if type(members) is not list or len(members) > MAX_MEMBERS or not all(isinstance(m, Mapping) for m in members):
        raise ApiRefused("invalid_field", f"members must be a list of at most {MAX_MEMBERS}")
    preferences, joined = [], [leader]
    for item in members:
        member = common.actor(item.get("actor"), "member", campaign)
        if member in joined and member != leader:
            raise ApiRefused("invalid_field", "a member is named twice")
        if member not in joined:
            joined.append(member)
        if item.get("choice") is not None:
            preferences.append(MemberPreference(member, PROPOSAL, common.text(item.get("choice"), "choice"), LAID_OUT))
    for member in joined:
        if cast.get(member) is None:
            raise ApiRefused("no_actor", f"{member} is not in this chat's cast")
    told = common.actors(args.get("told", []), "told", campaign, maximum=64)
    relations = tuple(Relation(RelationRef(campaign, f"world-page-member-{n}"), "member_of", member, group,
                               Moment(0), LAID_OUT, "now") for n, member in enumerate(joined))
    state = InstitutionState(Institution(group, leader, goals, policy))
    _, decision = decide(state, preferences=tuple(preferences), proposal_id=PROPOSAL, choice=choice,
                         operation_id="world-page-preview", expected_revision=0,
                         roster=cast.roster(EntityInstance(group, local_name=name)), definitions=cast.definitions(),
                         relations=relations, controls=cast.controls, authored_by=DIRECTOR, moment=Moment(1),
                         authorization_moment=Moment(1), audience=told, channel="announcement", provenance=LAID_OUT)

    def who(ref: InstanceRef) -> dict:
        return {"actor": str(ref), "name": cast.name(ref)}

    dissent = decision.dissent
    return {"saved": False, "group": name, "goals": list(goals), "policy": policy, "proposal": proposal,
            "decision": {"choice": decision.choice, "leader": who(leader)},
            "members": [who(member) for member in joined],
            "dissent": [{**who(item.actor), "choice": item.choice} for item in dissent],
            "agreeing": [{**who(item.actor), "choice": item.choice} for item in decision.request.preferences
                         if item not in dissent],
            "told": [who(actor) for actor in decision.audience]}


OPS = {"cast": _cast, "faction_preview": _preview}
