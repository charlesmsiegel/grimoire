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
from grimoire.generation import MAX_HEARD, MAX_LINE_CHARS, HeardLine
from grimoire.play.scene import WRITE_RETRIES, _refuse
from grimoire.scene import SceneRefused, TurnRecord
from grimoire.store.errors import LedgerInvalid, StoreError

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


def _029(call, *args, **kwargs):
    """Run one 029 library call; every refusal or ledger failure comes back as a ``SceneRefused``."""
    try:
        return call(*args, **kwargs)
    except AdjudicationError as error:
        raise _refuse(error.reason, str(error)[:200]) from None
    except LedgerInvalid:
        raise _refuse("adjudication_unreadable", "the adjudication ledger cannot be trusted") from None
    except StoreError as error:
        raise _refuse("adjudication_unavailable", type(error).__name__) from None


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
    if post.private:
        raise _refuse("private_action", "a withheld action is not framed: the table never saw it")
    return scene, principal, record, post


def frame_of(record: TurnRecord, label) -> tuple[ActionFrame, PlayerContribution]:
    """The 029 frame a confirmed labelling records, and the contribution that backs it."""
    post, chat = record.post, record.chat
    contribution = PlayerContribution(chat.campaign_id, record.turn_id, chat.chat_id, record.turn_id,
                                      post.actor.instance_id, post.submitted_by, "action", post.text[:PROSE_MAX])
    frame = ActionFrame(record.turn_id, post.actor, chat, Moment(label.sequence, chat.chat_id, record.turn_id),
                        post.text[:PROSE_MAX], label.factors, label.stakes, PROVENANCE.format(label=label.label))
    return frame, contribution


def confirm_action(runner, chat: ChatRef, by) -> Outcome | None:
    """See ``SceneRunner.confirm``.

    The labelling is written ``framed`` on the turn first (compare-and-swap, so a relabel racing it wins or
    loses whole), and only then recorded in 029: once framed, the turn can no longer be relabelled, so a
    confirm interrupted at any step finishes on the next confirm, with the same frame.
    """
    library = _library(runner)
    scene, principal, record, post = _action(runner, chat, by)
    controller = scene.controller(post.actor)
    if principal != controller:
        raise _refuse("not_controller", "only the acting character's player confirms its labels")
    if post.submitted_by != controller:
        raise _refuse("control_changed", "the character changed hands since this action was posted")
    label = record.framed
    if label is None:
        label = record.labels[-1] if record.labels else None
        if label is None or label.state != "proposed":
            raise _refuse("not_proposed", "only the latest labelling, while proposed, is confirmed")
        record = _mark_framed(runner, chat, record, label.label)
        label = record.framed
    elif _029(library.outcome, chat.campaign_id, record.turn_id) is not None:
        raise _refuse("already_framed", "this action is framed and decided already")
    frame, contribution = frame_of(record, label)
    existing = _029(library.state, chat.campaign_id).frame(record.turn_id)
    if existing is None:
        _029(library.frame, frame, contribution=contribution, author=by)
    elif existing.frame != frame:
        raise _refuse("frame_mismatch", "029 holds another frame for this action")
    assessment = _029(library.assess, chat.campaign_id, record.turn_id)
    result = _BY_RULE.get(assessment.classification)
    if result is None:
        # uncertain: the director adjudicates, or the player consents to a setback. (``clarify`` comes only
        # from a framed ability, and these frames carry none.)
        return None
    cites = {factor_id for factor_id, factor in assessment.factors.items()
             if factor.bearing == ("prevents" if result == "failure" else "favours")}
    return _029(library.decide, chat.campaign_id, record.turn_id, Decision(result, "rule", frozenset(cites)))


def _mark_framed(runner, chat: ChatRef, record: TurnRecord, number: int) -> TurnRecord:
    """Write labelling ``number`` ``framed`` on the turn while it is still the latest and proposed; the turn."""
    for _ in range(WRITE_RETRIES):
        stored = next((s for s in runner.scenes.turns(chat).turns if s.record.ref == record.ref), None)
        if stored is None:
            break
        labels = stored.record.labels
        if stored.record.framed is not None:
            if stored.record.framed.label == number:  # a concurrent confirm framed it
                return stored.record
            break
        if not labels or labels[-1].label != number or labels[-1].state != "proposed":
            break  # relabelled meanwhile: the player confirms what they saw, or nothing
        try:
            framed = stored.record.with_label(replace(labels[-1], state="framed"))
            runner.scenes.write(framed, stored)
            return framed
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
    decision = _029(Decision, result, basis, _ids(cites, "cites"), _ids(consequences, "consequences"))
    return _029(library.decide, chat.campaign_id, record.turn_id, decision, consent=consent)


def _ids(values, field: str) -> frozenset[str]:
    """A set of factor or stake ids; a bare string is one id's letters, so it is refused."""
    if isinstance(values, (str, bytes)) or not all(isinstance(each, str) for each in _iterable(values, field)):
        raise _refuse("invalid_field", f"{field} is a collection of ids")
    return frozenset(values)


def _iterable(values, field: str):
    try:
        return tuple(values)
    except TypeError:
        raise _refuse("invalid_field", f"{field} is a collection of ids") from None


def outcome_of(runner, record: TurnRecord | None) -> Outcome | None:
    """The recorded outcome of ``record``'s framed action; ``None`` when there is none (or nothing framed)."""
    if record is None or record.framed is None or runner.adjudication is None:
        return None
    return _029(runner.adjudication.outcome, record.chat.campaign_id, record.turn_id)


def check_decided(runner, record: TurnRecord | None) -> Outcome | None:
    """Refuse narration for a framed, undecided action (``undecided_attempt``); its outcome otherwise."""
    if record is None or record.framed is None:
        return None
    _library(runner)  # a framed turn is read only with the key it was framed under
    outcome = outcome_of(runner, record)
    if outcome is None:
        raise _refuse("undecided_attempt", "this action is framed and not yet decided; decide it before narrating")
    return outcome


def with_outcome(heard, outcome: Outcome | None, names, *, table: bool) -> tuple[HeardLine, ...]:
    """``heard`` with the outcome after it, still within ``MAX_HEARD`` lines: the table's full line for the
    director (``table``), the bare result for an NPC."""
    if outcome is None:
        return tuple(heard)
    line = outcome_line(outcome, names) if table else heard_outcome(outcome)
    return (*heard, line)[-MAX_HEARD:]


def heard_outcome(outcome: Outcome) -> HeardLine:
    """The outcome as an NPC who saw the action knows it: the result alone. Its causes and stakes are the
    acting character's own facts and the labeller's words, never an NPC's; the action's own line says who."""
    return HeardLine(OUTCOME_SPEAKER, f"The action {_SAYS[outcome.decision.result]}.")


def outcome_line(outcome: Outcome, names) -> HeardLine:
    """The outcome as the table saw it at confirm: the result, what caused it and what came true, in the
    frame's words, cut to one heard line."""
    frame, decision = outcome.frame, outcome.decision
    who = names.get(frame.actor) or frame.actor.instance_id
    texts = {factor.factor_id: factor.text for factor in frame.factors}
    stakes = {stake.stake_id: stake.text for stake in frame.stakes}
    parts = [f"{who}'s action ({frame.intent[:200]}) {_SAYS[decision.result]}."]
    came = [stakes[each] for each in sorted(decision.consequences) if each in stakes]
    if came:
        parts.append("What comes of it: " + " ".join(came))
    because = [texts[each] for each in sorted(decision.cites) if each in texts]
    if because:
        parts.append("Because: " + " ".join(because))
    text = " ".join(parts)
    if len(text) > MAX_LINE_CHARS:
        text = text[:MAX_LINE_CHARS - 1].rstrip() + "…"
    return HeardLine(OUTCOME_SPEAKER, text)


__all__ = ["OUTCOME_SPEAKER", "PROVENANCE", "check_decided", "confirm_action", "decide_action", "frame_of",
           "heard_outcome", "outcome_line", "outcome_of", "with_outcome"]
