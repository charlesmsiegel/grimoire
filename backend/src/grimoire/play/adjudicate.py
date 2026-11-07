"""Confirming a labelled action into a 029 frame, and deciding it (035 milestone 2).

The requester's decision (Wave 6): **the player confirms a labelled action unedited** before 029 records
it; a relabel is the only fix. So ``confirm`` frames exactly the latest proposed labelling, backed by the
acting player's own contribution and given with their own trusted handle (029 refuses any other), and the
adjudication key is the app's own trusted handle, never the labeller's and never the player's.

An action the rules decide (``straightforward``: success; ``impossible``: failure) is decided at once, by
rule. An ``uncertain`` one waits: the director (the library owner) adjudicates it, or its player consents
to a setback. Until it is decided, no narration is generated or kept for its turn (``undecided_attempt``).
"""

from __future__ import annotations

from dataclasses import replace

from grimoire.adjudication import PROSE_MAX, ActionFrame, AdjudicationError, Decision, Outcome
from grimoire.core.authority import PlayerContribution
from grimoire.core.identity import ChatRef
from grimoire.core.knowledge import Moment
from grimoire.generation import MAX_HEARD, HeardLine
from grimoire.play.scene import WRITE_RETRIES, _refuse
from grimoire.scene import SceneRefused, TurnRecord

#: How the runner names what a frame came from (attribution only).
PROVENANCE = "035 labelling {label}, confirmed by the acting player"
#: The heard label for an outcome: what the table saw happen, never a character's words.
OUTCOME_SPEAKER = "Outcome"
#: How an outcome line says each result.
_SAYS = {"success": "succeeds", "partial": "succeeds at a cost", "failure": "fails"}
#: How the rules decide a classification on their own.
_BY_RULE = {"straightforward": "success", "impossible": "failure"}


def _library(runner):
    if runner.adjudication is None:
        raise _refuse("no_adjudication", "no adjudication key is configured")
    return runner.adjudication


def _action(runner, chat: ChatRef, by):
    """The open turn's framable action, its scene and the asking principal; refusals name what is missing."""
    context = runner._context(chat)
    scene = context.scene
    principal = runner._principal(scene, by)
    runner._ready(scene)
    record = scene.pending
    post = record.post if record is not None else None
    if post is None or post.kind != "action":
        raise _refuse("not_an_action", "only the open turn's action post is framed")
    return scene, principal, record, post


def _adjudication_error(error: AdjudicationError) -> SceneRefused:
    return _refuse(error.reason, str(error)[:200])


def frame_of(record: TurnRecord, label) -> tuple[ActionFrame, PlayerContribution]:
    """The 029 frame a confirmed labelling records, and the contribution that backs it."""
    post, chat = record.post, record.chat
    contribution = PlayerContribution(chat.campaign_id, record.turn_id, chat.chat_id, record.turn_id,
                                      post.actor.instance_id, post.submitted_by, "action", post.text[:PROSE_MAX])
    frame = ActionFrame(record.turn_id, post.actor, chat, Moment(label.sequence, chat.chat_id, record.turn_id),
                        post.text[:PROSE_MAX], label.factors, label.stakes, PROVENANCE.format(label=label.label))
    return frame, contribution


def confirm_action(runner, chat: ChatRef, by) -> Outcome | None:
    """See ``SceneRunner.confirm``."""
    library = _library(runner)
    scene, principal, record, post = _action(runner, chat, by)
    if record.framed is not None:
        raise _refuse("already_framed", "this action is framed already")
    label = record.labels[-1] if record.labels else None
    if label is None or label.state != "proposed":
        raise _refuse("not_proposed", "only the latest labelling, while proposed, is confirmed")
    controller = scene.controller(post.actor)
    if principal != controller:
        raise _refuse("not_controller", "only the acting character's player confirms its labels")
    if post.submitted_by != controller:
        raise _refuse("control_changed", "the character changed hands since this action was posted")
    frame, contribution = frame_of(record, label)
    existing = library.state(chat.campaign_id).frame(record.turn_id)
    if existing is None or existing.frame != frame:  # a confirm interrupted after 029 recorded it resumes here
        try:
            library.frame(frame, contribution=contribution, author=by)
        except AdjudicationError as error:
            raise _adjudication_error(error) from None
    _mark_framed(runner, chat, record, label.label)
    assessment = library.assess(chat.campaign_id, record.turn_id)
    result = _BY_RULE.get(assessment.classification)
    if result is None:
        # uncertain: the director adjudicates, or the player consents to a setback. (``clarify`` comes only
        # from a framed ability, and these frames carry none.)
        return None
    cites = {factor_id for factor_id, factor in assessment.factors.items()
             if factor.bearing == ("prevents" if result == "failure" else "favours")}
    try:
        return library.decide(chat.campaign_id, record.turn_id, Decision(result, "rule", frozenset(cites)))
    except AdjudicationError as error:
        raise _adjudication_error(error) from None


def _mark_framed(runner, chat: ChatRef, record: TurnRecord, number: int) -> None:
    """Write the labelling ``framed`` on the turn, re-reading it if it changed meanwhile."""
    for _ in range(WRITE_RETRIES):
        stored = next((s for s in runner.scenes.turns(chat).turns if s.record.ref == record.ref), None)
        if stored is None:
            break
        label = next((each for each in stored.record.labels if each.label == number), None)
        if label is None or label.state not in ("proposed", "framed"):
            break
        if label.state == "framed":
            return
        try:
            runner.scenes.write(stored.record.with_label(replace(label, state="framed")), stored)
            return
        except SceneRefused as error:
            if error.reason != "turn_changed":
                raise
    raise _refuse("turn_changed", "the turn changed while it was framed; confirm again")


def decide_action(runner, chat: ChatRef, by, result: str, *, basis: str = "adjudicated", cites=(),
                  consequences=()) -> Outcome:
    """See ``SceneRunner.decide``."""
    library = _library(runner)
    scene, principal, record, post = _action(runner, chat, by)
    if record.framed is None:
        raise _refuse("not_framed", "only a confirmed (framed) action is decided")
    if basis == "adjudicated":
        if principal not in runner.authority.owners:
            raise _refuse("not_director", "the director (the library owner) adjudicates an uncertain action")
        consent = None
    elif basis == "consent":
        if principal != scene.controller(post.actor):
            raise _refuse("not_controller", "only the acting character's player consents to a setback")
        consent = by
    else:
        raise _refuse("invalid_field", "basis is adjudicated or consent; the rules decide the rest at confirm")
    try:
        decision = Decision(result, basis, frozenset(cites), frozenset(consequences))
        return library.decide(chat.campaign_id, record.turn_id, decision, consent=consent)
    except AdjudicationError as error:
        raise _adjudication_error(error) from None


def outcome_of(runner, record: TurnRecord | None) -> Outcome | None:
    """The recorded outcome of ``record``'s framed action; ``None`` when there is none (or nothing framed)."""
    if record is None or record.framed is None or runner.adjudication is None:
        return None
    return runner.adjudication.outcome(record.chat.campaign_id, record.turn_id)


def check_decided(runner, record: TurnRecord | None) -> Outcome | None:
    """Refuse narration for a framed, undecided action (``undecided_attempt``); its outcome otherwise."""
    if record is None or record.framed is None:
        return None
    outcome = outcome_of(runner, record)
    if outcome is None:
        raise _refuse("undecided_attempt", "this action is framed and not yet decided; decide it before narrating")
    return outcome


def with_outcome(heard, outcome: Outcome | None, names) -> tuple[HeardLine, ...]:
    """``heard`` with the outcome after it, still within ``MAX_HEARD`` lines."""
    if outcome is None:
        return tuple(heard)
    return (*heard, outcome_line(outcome, names))[-MAX_HEARD:]


def outcome_line(outcome: Outcome, names) -> HeardLine:
    """The outcome as the table saw it: the result, what caused it and what came true, in the frame's words."""
    frame, decision = outcome.frame, outcome.decision
    who = names.get(frame.actor) or frame.actor.instance_id
    texts = {factor.factor_id: factor.text for factor in frame.factors}
    stakes = {stake.stake_id: stake.text for stake in frame.stakes}
    parts = [f"{who}'s action ({frame.intent[:200]}) {_SAYS[decision.result]}."]
    because = [texts[each] for each in sorted(decision.cites) if each in texts]
    if because:
        parts.append("Because: " + " ".join(because))
    came = [stakes[each] for each in sorted(decision.consequences) if each in stakes]
    if came:
        parts.append("What comes of it: " + " ".join(came))
    return HeardLine(OUTCOME_SPEAKER, " ".join(parts))


__all__ = ["OUTCOME_SPEAKER", "PROVENANCE", "check_decided", "confirm_action", "decide_action", "frame_of",
           "outcome_line", "outcome_of", "with_outcome"]
