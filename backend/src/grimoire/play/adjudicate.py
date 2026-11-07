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
from grimoire.generation import MAX_LINE_CHARS, HeardLine
from grimoire.play.scene import WRITE_RETRIES, _mark, _refuse
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

    029 checks the frame first (writing nothing). The labelling is then written ``framed`` on the turn
    (compare-and-swap, so a relabel racing it wins or loses whole), and only then recorded in 029: once framed,
    the turn can no longer be relabelled, so a confirm cut short by a failing ledger finishes on the next
    confirm, with the same frame. If 029 refuses the frame for good after the mark (the actor's card deleted
    meanwhile), the labelling is written ``refused`` with 029's reason: the action goes back to unframed play.
    """
    library = _library(runner)
    scene, principal, record, post = _action(runner, chat, by)
    controller = scene.controller(post.actor)
    marked = record.framed
    if marked is None:
        label = record.labels[-1] if record.labels else None
        if label is None or label.state != "proposed":
            raise _refuse("not_proposed", "only the latest labelling, while proposed, is confirmed")
    else:
        label = marked
        if _029(library.outcome, chat.campaign_id, record.turn_id) is not None:
            raise _refuse("already_framed", "this action is framed and decided already")
    if principal != controller:
        raise _refuse("not_controller", "only the acting character's player confirms its labels")
    frame, contribution = _029(frame_of, record, label)
    existing = _029(library.state, chat.campaign_id).frame(record.turn_id)
    if existing is not None and existing.frame != frame:
        raise _refuse("frame_mismatch", "029 holds another frame for this action")
    if existing is None:
        if post.submitted_by != controller:
            _unframe(runner, chat, record, label.label, "control_changed")
            raise _refuse("control_changed", "the character changed hands since this action was posted")
        try:
            _029(library.check, frame, contribution=contribution, author=by)
        except SceneRefused as error:
            if error.reason not in _UNAVAILABLE:
                _unframe(runner, chat, record, label.label, error.reason)
            raise
        if marked is None:
            record = _mark_framed(runner, chat, record, label.label, principal)
        try:
            _029(library.frame, frame, contribution=contribution, author=by)
        except SceneRefused as error:
            current = _029(library.state, chat.campaign_id).frame(record.turn_id)
            if current is None or current.frame != frame:  # a concurrent confirm did not record it either
                if error.reason not in _UNAVAILABLE:
                    _unframe(runner, chat, record, label.label, error.reason)
                raise
    decided = _029(library.outcome, chat.campaign_id, record.turn_id)
    if decided is not None:  # a concurrent confirm decided it meanwhile
        return decided
    assessment = _029(library.assess, chat.campaign_id, record.turn_id)
    result = _BY_RULE.get(assessment.classification)
    if result is None:
        # uncertain: the director adjudicates, or the player consents to a setback. (``clarify`` comes only
        # from a framed ability, and these frames carry none.)
        return None
    cites = {factor_id for factor_id, factor in assessment.factors.items()
             if factor.bearing == ("prevents" if result == "failure" else "favours")}
    try:
        return _029(library.decide, chat.campaign_id, record.turn_id, Decision(result, "rule", frozenset(cites)))
    except SceneRefused as error:
        decided = _029(library.outcome, chat.campaign_id, record.turn_id)
        if error.reason == "already_decided" and decided is not None:
            return decided
        raise


#: Refusals that say 029's ledger could not be used now, not that it refused the frame: a confirm retries.
_UNAVAILABLE = frozenset({"adjudication_unreadable", "adjudication_unavailable"})


def _unframe(runner, chat: ChatRef, record: TurnRecord, number: int, reason: str) -> None:
    """Write labelling ``number`` ``refused`` with ``reason`` if it is the turn's framed one and 029 never
    recorded it, so the action goes back to unframed play; best effort (the refusal is raised either way).

    029 is read again, and the turn written, under the store lock 029 records a frame under: a concurrent
    confirm that recorded the frame meanwhile keeps its turn marker."""
    library = _library(runner)
    for _ in range(WRITE_RETRIES):
        with runner.store.lock:
            try:
                if library.state(chat.campaign_id).frame(record.turn_id) is not None:
                    return  # recorded after all: the turn stays framed
            except (AdjudicationError, StoreError):
                return  # 029 cannot be read: leave the mark, the next confirm settles it
            stored = next((s for s in runner.scenes.turns(chat).turns if s.record.ref == record.ref), None)
            label = stored.record.framed if stored is not None else None
            if label is None or label.label != number:
                return
            try:
                runner.scenes.write(stored.record.with_label(
                    replace(label, state="refused", factors=(), stakes=(), reason=reason[:200])), stored)
                return
            except SceneRefused as error:
                if error.reason != "turn_changed":
                    return


def _mark_framed(runner, chat: ChatRef, record: TurnRecord, number: int, principal: str) -> TurnRecord:
    """Write labelling ``number`` ``framed`` on the turn while it is still the latest and proposed; the turn.
    The same write withdraws every proposed take and supersedes a running one: each was written without the
    outcome this frame will settle, so none may be kept once it is decided (a run in flight lands stale)."""
    for _ in range(WRITE_RETRIES):
        turns = runner.scenes.turns(chat)
        if turns.paused:  # a pause landed while 029 checked the frame: nothing is framed over it
            raise _refuse("paused", "play was paused while the action was confirmed")
        stored = next((s for s in turns.turns if s.record.ref == record.ref), None)
        if stored is None:
            break
        labels = stored.record.labels
        if not stored.record.open:  # a take was kept, or the turn passed, meanwhile: nothing is left to frame
            raise _refuse("no_open_turn", "the turn closed while its action was confirmed")
        if stored.record.framed is not None:
            if stored.record.framed.label == number:  # a concurrent confirm framed it
                return stored.record
            break
        if not labels or labels[-1].label != number or labels[-1].state != "proposed":
            break  # relabelled meanwhile: the player confirms what they saw, or nothing
        try:
            framed = stored.record.with_label(replace(labels[-1], state="framed"))
            for take in framed.takes:
                if take.state == "running":
                    framed = framed.with_take(replace(take, state="superseded"))
                elif take.state == "proposed":
                    framed = framed.with_take(_mark(take, {line.line_id for line in take.lines}, "withdrawn",
                                                    principal))
            runner.scenes.write(framed, stored, unpaused=True)  # a pause written meanwhile refuses it
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
    items = () if isinstance(values, (str, bytes)) else _iterable(values, field)
    if isinstance(values, (str, bytes)) or not all(isinstance(each, str) for each in items):
        raise _refuse("invalid_field", f"{field} is a collection of ids")
    return frozenset(items)


def _iterable(values, field: str):
    try:
        return tuple(values)
    except TypeError:
        raise _refuse("invalid_field", f"{field} is a collection of ids") from None


def outcome_of(runner, record: TurnRecord | None) -> Outcome | None:
    """The recorded outcome of ``record``'s framed action; ``None`` when there is none (or nothing framed)."""
    if record is None or record.framed is None:
        return None
    return _029(_library(runner).outcome, record.chat.campaign_id, record.turn_id)


def check_decided(runner, record: TurnRecord | None) -> Outcome | None:
    """Refuse narration for a framed, undecided action (``undecided_attempt``); its outcome otherwise."""
    if record is None or record.framed is None:
        return None
    outcome = outcome_of(runner, record)  # a framed turn is read only with the key it was framed under
    if outcome is None:
        raise _refuse("undecided_attempt", "this action is framed and not yet decided; decide it before narrating")
    return outcome


def outcome_lines(runner, turns, names) -> tuple[dict[str, HeardLine], dict[str, HeardLine]]:
    """Every decided action's outcome among ``turns``, by turn id: the table's full line (for the director)
    and the bare result (for an NPC or the acting character), heard where the action was. A framed turn is
    read only with the key it was framed under (``no_adjudication`` without one)."""
    framed = [record for record in turns if record.framed is not None]
    if not framed:
        return {}, {}
    state = _029(_library(runner).state, framed[0].chat.campaign_id)
    table: dict[str, HeardLine] = {}
    bare: dict[str, HeardLine] = {}
    for record in framed:
        entry = state.decisions.get(record.turn_id)
        if entry is not None:
            table[record.turn_id] = outcome_line(entry.outcome, names)
            bare[record.turn_id] = heard_outcome(entry.outcome)
    return table, bare


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
           "heard_outcome", "outcome_line", "outcome_lines", "outcome_of"]
