"""076's offscreen faction plans on the Time page: the adopted plans, their steps and due moments, the deadlines they
race, and the calendar advances not yet applied to them (``OffscreenView.pending``); applying one, with a relevance
budget; and adopting a plan under a leader's decision the director makes in the same reviewed flow.

**Adoption.** 076 adopts a plan only against a 057 ``InstitutionState`` holding the decision that authorized it, and
057 has no store. The director makes that decision here, as 115's World page previews one. The institution itself
(its leader, goals and policy, and any preferences stated) is typed fresh by the director for this one decision and
is not a record of the campaign; what is checked is only that it fits the campaign's real records: the group is one of the campaign's group instances, its members are the canonical ``member_of``
relations (017) that hold now between it and the chosen chat's cast, and its leader (and any member stating its own
preference) must be one of those members, an NPC the director controls (057's ``npc_authority``). The decision is
made fresh, in memory, and the plan is adopted against that state in the same write's review: nothing about the
group, its membership or control is invented, and nothing but the plan (with the decision's id and choice, as 076
keeps them) is saved. The same request committed again makes the same decision and finds 076's receipt.

**Skips.** A skip names a committed calendar advance (a Chronicle advance or a downtime pursuit) by its own id;
applying it flags every pending step due by its end, within the budget, in 076's deterministic order. A development
is director-facing: it writes no knowledge, event, report or rumor, and no character learns of it.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext
from grimoire.campaignapi import parse
from grimoire.core import DIRECTOR
from grimoire.core.entity import EntityInstance
from grimoire.core.knowledge import Moment
from grimoire.graph.codec import RelationCodecError, relation_from_payload
from grimoire.graph.model import Relation
from grimoire.institutions import (
    Institution,
    InstitutionState,
    MemberPreference,
    decide,
)
from grimoire.offscreen import (
    MAX_BUDGET,
    MAX_PLANS,
    MAX_STEPS,
    Advanced,
    FactionPlan,
    PlanStep,
)
from grimoire.offscreen.model import MAX_STEP_MINUTES, PRIORITY_MAX, PRIORITY_MIN
from grimoire.offscreenstore import OffscreenLibrary
from grimoire.play.scene import NOW
from grimoire.textapi import ApiRefused

from ..world.cast import cast_of
from . import common

#: The 057 proposal a plan's decision answers, and how it is recorded (in memory only; 057 has no store).
PROPOSAL = "offscreen-plan"
CHANNEL = "council"
DECIDED = "decided by the director on the Time page"
MAX_GOALS = 16
MAX_MEMBERS = 64


def library(ctx: AdapterContext) -> OffscreenLibrary:
    return OffscreenLibrary(ctx.store, ctx.handle)


def _deadline(calendar, deadline_id):  # type: ignore[no-untyped-def]
    if calendar is None or deadline_id is None:
        return None
    return next((item for item in calendar.deadlines if item.deadline_id == deadline_id), None)


def _deadline_view(calendar, deadline_id) -> dict | None:  # type: ignore[no-untyped-def]
    if deadline_id is None:
        return None
    found = _deadline(calendar, deadline_id)
    if found is None:
        return {"deadline": deadline_id, "found": False}
    return {"deadline": deadline_id, "found": True, "label": found.label, "due": common.moment(calendar, found.due),
            "passed": found.triggered_by is not None}


def _labels(state, plan_id: str) -> dict[str, str]:  # type: ignore[no-untyped-def]
    progress = None if state is None else state.progress(plan_id)
    return {} if progress is None else {step.step_id: step.label for step in progress.plan.steps}


def _skip_view(calendar, state, receipt: Advanced, *, already_applied: bool) -> dict:  # type: ignore[no-untyped-def]
    return {"advance": receipt.operation.operation_id, "budget": receipt.operation.budget,
            "already_applied": already_applied,
            "before": common.moment(calendar, receipt.before), "after": common.moment(calendar, receipt.after),
            "developments": [{"plan": item.plan_id, "step": item.step_id,
                              "label": _labels(state, item.plan_id).get(item.step_id),
                              "due": common.moment(calendar, item.due)} for item in receipt.developments],
            "deferred": list(receipt.deferred),
            "deadlines": [{"plan": item.plan_id, "deadline": item.deadline_id, "outcome": item.outcome,
                           "label": getattr(_deadline(calendar, item.deadline_id), "label", None)}
                          for item in receipt.deadlines]}


def _plan_view(calendar, progress, names: common.Names) -> dict:  # type: ignore[no-untyped-def]
    plan = progress.plan
    deadline = _deadline_view(calendar, plan.deadline_id)
    if deadline is not None and deadline.get("found"):
        due = _deadline(calendar, plan.deadline_id).due
        deadline["race"] = "met" if progress.dues[-1] <= due else "missed"
    return {"plan": plan.plan_id, "group": names.who(plan.group), "goal": plan.goal, "decision": plan.decision_id,
            "choice": plan.choice, "priority": plan.priority, "started": common.moment(calendar, progress.started),
            "steps": [{"step": step.step_id, "label": step.label, "minutes": step.minutes,
                       "due": common.moment(calendar, due), "flagged": index < progress.completed}
                      for index, (step, due) in enumerate(zip(plan.steps, progress.dues))],
            "completed": progress.completed, "complete": progress.complete, "deadline": deadline}


def _members(ctx: AdapterContext, campaign_id: str, cast) -> tuple[list[Relation], list]:  # type: ignore[no-untyped-def]
    """The campaign's canonical ``member_of`` relations between its groups and the chat's cast, and the problems
    reading them (an unreadable relation is said, never guessed around)."""
    scan = ctx.store.campaign_relations(campaign_id)
    problems = [{"code": "relations_unreadable", "detail": str(item.path)} for item in scan.problems]
    found = []
    actors = {member.actor for member in cast.members}
    for ref, stored in scan.records.items():
        try:
            relation = relation_from_payload(ref, stored.record.schema, stored.record.payload)
        except (RelationCodecError, ValueError):
            problems.append({"code": "relation_unreadable", "detail": str(ref)})
            continue
        if (isinstance(relation, Relation) and relation.predicate == "member_of" and relation.subject in actors
                and relation.object.kind == "group"):
            found.append(relation)
    return sorted(found, key=lambda item: str(item.ref)), problems


def _groups(ctx: AdapterContext, campaign_id: str, chat, names: common.Names) -> dict:  # type: ignore[no-untyped-def]
    """The campaign's groups and who of the chat's cast belongs to each now, as 057 checks a decision."""
    cast = cast_of(ctx, chat)
    relations, problems = _members(ctx, campaign_id, cast)
    groups = []
    for ref in common.campaign_actors(ctx, campaign_id):
        if ref.kind != "group":
            continue
        members = []
        for relation in relations:
            if relation.object != ref or not relation.holds_at(NOW):
                continue
            member = cast.get(relation.subject)
            members.append({**names.who(member.actor), "role": member.role, "agent": member.agent,
                            "may_decide": member.role == "npc" and member.agent})
        groups.append({**names.who(ref), "members": sorted(members, key=lambda item: item["actor"])})
    return {"groups": groups, "problems": problems}


def view(ctx: AdapterContext, scope, chat, names: common.Names) -> dict:  # type: ignore[no-untyped-def]
    found = library(ctx).read(scope)
    calendar, state = found.calendar, found.state
    receipts = {} if calendar is None else {item.operation.operation_id: item for item in calendar.receipts}
    pending = [{"advance": advance, "before": common.moment(calendar, receipts[advance].before),
                "after": common.moment(calendar, receipts[advance].after),
                "flags": common.flags(calendar, receipts[advance].triggered)} for advance in found.pending]
    skips = [] if state is None else [_skip_view(calendar, state, item, already_applied=True)
                                      for item in state.receipts if type(item) is Advanced]
    answer = {"etag": found.etag, "started": calendar is not None,
              "as_of": None if state is None else common.moment(calendar, state.as_of),
              "plans": [] if state is None else [_plan_view(calendar, item, names) for item in state.plans],
              "pending": pending, "skips": skips, "max_budget": MAX_BUDGET, "max_steps": MAX_STEPS,
              "priority": [PRIORITY_MIN, PRIORITY_MAX]}
    # adoption: whether the director can make the leader's decision here, and over which groups
    if calendar is None:
        answer["adoption"] = {"available": False, "reason": "uninitialized",
                              "detail": "A plan starts at the campaign clock's present moment: start the clock first."}
    elif chat is None:
        answer["adoption"] = {"available": False, "reason": "no_chat",
                              "detail": "The leader's decision is made by NPCs of a chat's cast: choose a chat."}
    elif state is not None and len(state.plans) >= MAX_PLANS:
        answer["adoption"] = {"available": False, "reason": "limit", "detail": f"At most {MAX_PLANS} plans are adopted."}
    else:
        laid = _groups(ctx, scope.campaign_id, chat, names)
        usable = [group for group in laid["groups"] if any(member["may_decide"] for member in group["members"])]
        if not laid["groups"]:
            adoption = {"available": False, "reason": "no_group",
                        "detail": "This campaign has no group (a faction or institution) among its records to adopt a plan for."}
        elif not usable:
            adoption = {"available": False, "reason": "no_leader",
                        "detail": "No group has a member in this chat's cast who is an NPC the director controls, so no "
                                  "leader can decide here."}
        else:
            adoption = {"available": True}
        if pending:
            adoption = {"available": False, "reason": "pending_skips",
                        "detail": "Apply the calendar's pending advances first: a plan starts at the plans' present moment."}
        answer["adoption"] = {**adoption, **laid}
    return answer


# -- adopting a plan (057 then 076) -----------------------------------------------------------------------------
def _decided(ctx: AdapterContext, args: Mapping[str, object], scope, chat, operation_id: str):  # type: ignore[no-untyped-def]
    """The leader's decision the request lays out, made fresh over the campaign's records: ``(state, decision,
    names)``. Refused with 057's own reason when the records do not authorize it."""
    campaign_id = scope.campaign_id
    cast = cast_of(ctx, chat)
    group = common.actor(args.get("group"), "group", campaign_id)
    if group.kind != "group" or group not in common.campaign_actors(ctx, campaign_id):
        raise ApiRefused("unknown_group", "the group must be one of this campaign's groups")
    leader = common.actor(args.get("leader"), "leader", campaign_id)
    goals = args.get("goals")
    if type(goals) is not list or not 1 <= len(goals) <= MAX_GOALS:
        raise ApiRefused("invalid_field", f"give from 1 to {MAX_GOALS} goals")
    goals = tuple(common.text({"goal": goal}, "goal") for goal in goals)
    policy = common.text(args, "policy")
    choice = common.text(args, "choice")
    preferences = args.get("preferences", [])
    if type(preferences) is not list or len(preferences) > MAX_MEMBERS or not all(isinstance(item, Mapping)
                                                                                for item in preferences):
        raise ApiRefused("invalid_field", f"preferences must be a list of at most {MAX_MEMBERS}")
    stated = []
    for item in preferences:
        member = parse.actor_of(item.get("actor"), "member", campaign_id)
        stated.append(MemberPreference(member, PROPOSAL, common.text(item, "choice"), DECIDED))
    relations, problems = _members(ctx, campaign_id, cast)
    if problems:
        raise ApiRefused("relations_unreadable", "a relation of this campaign cannot be read, so its membership "
                                                 "cannot be checked; nothing was written")
    names = common.Names(ctx, common.scene_of(ctx, chat))
    extra = () if cast.get(group) is not None else (EntityInstance(group, local_name=names.name(group)),)
    state = InstitutionState(Institution(group, leader, goals, policy))
    now = Moment(NOW)
    state, decision = decide(state, preferences=tuple(stated), proposal_id=PROPOSAL, choice=choice,
                             operation_id=f"{operation_id}-decision", expected_revision=0,
                             roster=cast.roster(*extra), definitions=cast.definitions(),
                             relations=tuple(item for item in relations if item.object == group), controls=cast.controls,
                             authored_by=DIRECTOR, moment=now, authorization_moment=now, audience=(), channel=CHANNEL,
                             provenance=DECIDED)
    return state, decision, names


def _steps(args: Mapping[str, object]) -> tuple[PlanStep, ...]:
    steps = args.get("steps")
    if type(steps) is not list or not 1 <= len(steps) <= MAX_STEPS or not all(isinstance(item, Mapping)
                                                                               for item in steps):
        raise ApiRefused("invalid_field", f"a plan has from 1 to {MAX_STEPS} steps")
    return tuple(PlanStep(f"step-{n}", common.text(item, "label"), common.whole(item, "minutes", 1, MAX_STEP_MINUTES))
                 for n, item in enumerate(steps, 1))


def _adoption(ctx: AdapterContext, args: Mapping[str, object], scope, operation_id: str, expected: str):  # type: ignore[no-untyped-def]
    found = common.chat(args, scope)
    institution, decision, names = _decided(ctx, args, scope, found, operation_id)
    deadline = common.segment(args, "deadline", maximum=128, required=False)
    plan = FactionPlan(common.segment(args, "plan", maximum=128), institution.institution.group, decision.operation_id,
                       decision.choice, common.text(args, "goal"), _steps(args),
                       priority=common.whole(args, "priority", PRIORITY_MIN, PRIORITY_MAX, default=50),
                       deadline_id=deadline)
    change = library(ctx).adopt(scope, operation_id, plan, institution=institution, expected=expected)
    return change, decision, names


def _adopt_view(calendar, change, decision, names: common.Names) -> dict:  # type: ignore[no-untyped-def]
    receipt = change.receipt
    plan = receipt.operation.plan
    dues, total = [], 0
    for step in plan.steps:
        total += step.minutes
        dues.append(receipt.at.advance(total))
    deadline = _deadline_view(calendar, plan.deadline_id)
    if deadline is not None and deadline.get("found"):
        deadline["race"] = "met" if dues[-1] <= _deadline(calendar, plan.deadline_id).due else "missed"
    return {"kind": "adopt", "operation": receipt.operation.operation_id, "already_applied": change.already_applied,
            "plan": plan.plan_id, "group": names.who(plan.group), "goal": plan.goal, "priority": plan.priority,
            "starts": common.moment(calendar, receipt.at),
            "steps": [{"step": step.step_id, "label": step.label, "minutes": step.minutes,
                       "due": common.moment(calendar, due)} for step, due in zip(plan.steps, dues)],
            "deadline": deadline,
            "decision": {"decision": decision.operation_id, "choice": decision.choice,
                         "leader": names.who(decision.request.institution.leader),
                         "dissent": [{**names.who(item.actor), "choice": item.choice} for item in decision.dissent],
                         "agreeing": [{**names.who(item.actor), "choice": item.choice}
                                      for item in decision.request.preferences if item not in decision.dissent]}}


def _planned(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    common.director(args, "offscreen plans")
    scope = common.campaign(args)
    operation = args.get("operation")
    if operation not in ("adopt", "skip"):
        raise ApiRefused("invalid_field", "operation must be adopt or skip")
    expected = common.expected(args)
    if operation == "skip":
        advance = common.segment(args, "advance", maximum=128)
        budget = common.whole(args, "budget", 1, MAX_BUDGET)
        return scope, "skip", library(ctx).advance(scope, advance, budget=budget, expected=expected), None, None
    change, decision, names = _adoption(ctx, args, scope, common.segment(args, "operation_id", maximum=100), expected)
    return scope, "adopt", change, decision, names


def _answer(ctx: AdapterContext, scope, kind, change, decision, names) -> dict:  # type: ignore[no-untyped-def]
    current = library(ctx).read(scope)
    if kind == "skip":
        return {**_skip_view(current.calendar, current.state, change.receipt, already_applied=change.already_applied),
                "kind": "skip"}
    return _adopt_view(current.calendar, change, decision, names)


def plan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """What adopting the plan, or applying the skip, would do; nothing is written."""
    scope, kind, change, decision, names = _planned(ctx, args)
    return {"plan": {**_answer(ctx, scope, kind, change, decision, names), "files": sorted(change.preview())},
            "etag": library(ctx).read(scope).etag}


def commit(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The adoption or skip reviewed with ``offscreen_plan``, built again as reviewed and committed once."""
    scope, kind, change, decision, names = _planned(ctx, args)
    change.commit()
    return {"done": _answer(ctx, scope, kind, change, decision, names)}


OPS = {"offscreen_plan": plan, "offscreen_commit": commit}
