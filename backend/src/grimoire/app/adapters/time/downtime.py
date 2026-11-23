"""074's long activities on the Time page: begin one for a cast actor of the chosen chat, pursue it for an exact
span of campaign time (with less effort than the time that passes, if the director says so), or abandon it. Each
change is reviewed first (``downtime_plan`` writes nothing and answers the receipt: effort credited, whether it
completes the activity, the clock before and after, the deadlines the span crosses, the files written) and then
committed as reviewed (``downtime_commit``), with the same operation id and the scope's etag as reviewed.

``DowntimeLibrary`` keeps the activity and its calendar advance in one scope write, so a crash leaves neither or both
and a retried operation id answers its retained receipt without spending time or adding progress again. An estimate
is never offered: a pursuit's span is the exact number of minutes the director gives (074 refuses an unselected
range). A flagged deadline is only a flag: nothing happens by itself.

**Whose activity it is.** 074 does not check who chose an activity (DOWNTIME.md leaves "the actor's controller,
rather than the director, chose the activity" to a later milestone), so this page is strict instead, as 077 is: the
director begins, pursues or abandons an activity only for an actor the director controls (an NPC, or a character
no one is seated as) or for the owner's own character. A character another person plays is theirs, and every
operation on it refuses ``not_your_character``; an actor whose control is recorded twice refuses
``ambiguous_control``. Control is read from the chosen chat's records, so every new operation names a chat.
"""

from __future__ import annotations

from collections.abc import Mapping

from grimoire.app.adapter import AdapterContext
from grimoire.calendars import Duration
from grimoire.core import DIRECTOR
from grimoire.downtime import KINDS, Abandon, Activity, Begin, Pursue
from grimoire.downtimestore import DowntimeLibrary
from grimoire.textapi import ApiRefused

from . import common

OPERATIONS = ("begin", "pursue", "abandon")


def library(ctx: AdapterContext) -> DowntimeLibrary:
    return DowntimeLibrary(ctx.store, ctx.handle)


def _receipt(view, receipt, *, already_applied: bool) -> dict:  # type: ignore[no-untyped-def]
    """What one 074 operation does (or did), in the calendar it is measured against."""
    operation = receipt.operation
    calendar = view.calendar
    kind = "begin" if type(operation) is Begin else "pursue" if type(operation) is Pursue else "abandon"
    activity = operation.activity.activity_id if type(operation) is Begin else operation.activity_id
    answer = {"operation": operation.operation_id, "kind": kind, "activity": activity,
              "already_applied": already_applied, "credited": receipt.credited, "completes": receipt.completed,
              "before": common.moment(calendar, receipt.before), "after": common.moment(calendar, receipt.after),
              "flags": common.flags(calendar, receipt.triggered)}
    if type(operation) is Pursue:
        answer["elapsed"] = operation.elapsed.exact_minutes
        answer["effort"] = operation.effort_minutes if operation.effort_minutes is not None else answer["elapsed"]
        answer["note"] = operation.note
    elif type(operation) is Abandon:
        answer["note"] = operation.note
    return answer


def whose(ctx: AdapterContext, campaign_id: str, actor, controls) -> dict:  # type: ignore[no-untyped-def]
    """Whether this page may run an activity for ``actor``: ``{available, controller}``, with ``reason`` and
    ``detail`` when it may not."""
    who = common.controller(campaign_id, actor, controls)
    if who is None:
        return {"available": False, "controller": None, "reason": "ambiguous_control",
                "detail": "who plays this character is recorded twice; fix the campaign's control first"}
    if who not in (DIRECTOR, ctx.owner):
        return {"available": False, "controller": who, "reason": "not_your_character",
                "detail": f"a character {who} plays is theirs: what it spends its time on is their choice, made with "
                          "their own key, which this screen does not take"}
    return {"available": True, "controller": who}


def _sovereign(ctx: AdapterContext, campaign_id: str, actor, scene) -> None:  # type: ignore[no-untyped-def]
    found = whose(ctx, campaign_id, actor, common.controls_of(scene, campaign_id))
    if not found["available"]:
        raise ApiRefused(found["reason"], found["detail"])


def view(ctx: AdapterContext, scope, names: common.Names, scene=None) -> dict:  # type: ignore[no-untyped-def]
    """Every activity, its progress and status, whose it is to run, and each operation that made it so."""
    controls = common.controls_of(scene, scope.campaign_id)
    found = library(ctx).read(scope)
    state, calendar = found.state, found.calendar
    activities = []
    for item in state.activities:
        activity = item.activity
        log = [_receipt(found, receipt, already_applied=True) for receipt in state.receipts
               if (receipt.operation.activity.activity_id if type(receipt.operation) is Begin
                   else receipt.operation.activity_id) == activity.activity_id]
        activities.append({"activity": activity.activity_id, "actor": names.who(activity.actor),
                           "whose": None if scene is None else whose(ctx, scope.campaign_id, activity.actor, controls),
                           "kind": activity.kind,
                           "intent": activity.intent, "required": activity.required_minutes,
                           "progress": item.progress_minutes, "remaining": item.remaining_minutes,
                           "status": item.status, "outcome": item.outcome, "settled_by": item.settled_by,
                           "settled_at": common.moment(calendar, item.settled_at), "log": log})
    return {"etag": found.etag, "started": calendar is not None, "activities": activities, "kinds": sorted(KINDS)}


def _planned(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    """The 074 change the request names, built against its ``expected`` etag (nothing is written)."""
    common.director(args, "long activities")
    scope = common.campaign(args)
    operation = args.get("operation")
    if operation not in OPERATIONS:
        raise ApiRefused("invalid_field", "operation must be begin, pursue or abandon")
    operation_id = common.segment(args, "operation_id")
    expected = common.expected(args)
    downtime = library(ctx)
    state = downtime.read(scope).state
    # a retry is 074's to compare with what it recorded; a new operation is checked for whose actor it is
    retry = state.receipt(operation_id) is not None
    scene = None if retry else common.scene_of(ctx, common.chat(args, scope))
    if operation == "begin":
        actor = common.actor(args.get("actor"), "actor", scope.campaign_id)
        # a new activity is for one of the chosen chat's cast
        if not retry:
            if actor not in scene.cast:
                raise ApiRefused("no_actor", f"{actor} is not in this chat's cast")
            _sovereign(ctx, scope.campaign_id, actor, scene)
        kind = args.get("kind")
        if kind not in KINDS:
            raise ApiRefused("invalid_field", f"kind must be one of {', '.join(sorted(KINDS))}")
        activity = Activity(common.segment(args, "activity"), actor, kind, common.text(args, "intent"),
                            common.whole(args, "required", 1, common.MAX_MINUTES))
        return scope, downtime.begin(scope, operation_id, activity, expected=expected)
    activity_id = common.segment(args, "activity")
    current = next((item for item in state.activities if item.activity.activity_id == activity_id), None)
    if not retry and current is not None:  # an unknown activity is 074's to refuse
        _sovereign(ctx, scope.campaign_id, current.activity.actor, scene)
    if operation == "pursue":
        elapsed = common.whole(args, "minutes", 1, common.MAX_MINUTES)
        effort = None if args.get("effort") is None else common.whole(args, "effort", 1, elapsed)
        return scope, downtime.pursue(scope, operation_id, activity_id, Duration(elapsed), effort_minutes=effort,
                                      note=common.text(args, "note", empty=True), expected=expected)
    return scope, downtime.abandon(scope, operation_id, activity_id, note=common.text(args, "note"), expected=expected)


def plan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """What the change would do, as reviewed against ``expected``; nothing is written."""
    scope, change = _planned(ctx, args)
    current = library(ctx).read(scope)
    return {"plan": {**_receipt(current, change.receipt, already_applied=change.already_applied),
                     "files": sorted(change.preview())}, "etag": current.etag}


def commit(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The change reviewed with ``downtime_plan``, built again with the same id and etag, committed once."""
    _scope, change = _planned(ctx, args)
    after = change.commit()
    return {"done": _receipt(after, change.receipt, already_applied=change.already_applied)}


OPS = {"downtime_plan": plan, "downtime_commit": commit}
