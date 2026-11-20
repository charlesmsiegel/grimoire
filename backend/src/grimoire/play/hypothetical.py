"""A hypothetical reply (073): how one present NPC *might* answer a line the player has not posted, saved nowhere.

A player wonders how a character would take a confession before risking it at the table. ``imagine`` reads the
line exactly as ``post`` would read it next (008's routing, 003's authority, 032's audience), briefs the one NPC
asked as ``generate`` briefs it for that next turn (its own knowledge, only what it heard, the line included,
inside the context budget, with the configured secrets screened; only the extra calls below are left out), asks the app's runtime for one
``npc_generation`` call under the runner's own policy, and screens the reply as a take's line is screened. What
it holds to:

- **Nothing is written.** No turn, take, decision, labelling, effect, knowledge or continuity record: the line is
  never posted, the reply is never a proposed take, and the chat reads afterwards exactly as it did before. The
  answer is the only place the reply exists; the page offers to copy it or discard it, and the scene goes on
  from where it stood. Nobody in the story heard either line, and no later prompt is given them.
- **It is the next turn's question, asked of the line as it stands.** The line follows the chat's last turn. A
  turn still open (a post waiting for an answer, takes on offer) is refused ``turn_open``, as ``post`` is: which
  take the table keeps would change what the character heard, so there is no next turn to imagine yet. A typed
  table request ("Grimoire, skip") is refused ``table_request``: it would act on the table, not be heard.
- **The same limits as a real reply.** A paused chat, an unseated cast, a cast that cannot be read, a speaker who
  is not a present director NPC on stage, a configured session-zero limit (016 holds narration before any call
  and again on the screened lines, against the table as it stands when the reply arrives) and the runtime's own
  refusals all apply. The speaker must be in the line's
  audience (``not_heard``): a whisper to someone else is never what this character answers, because it is never
  what this character heard.
- **One call, routed as play routes it.** Only the speaker's ``npc_generation`` call is planned: no decision call
  (an NPC with an agenda is briefed with 039's deterministic choice, unrecorded), no presentation suggestion
  (068) and no continuity review, so where the policy allows those, the brief lacks the decision's pick and the
  suggestion a real turn would carry. Its attempts and usage are in the answer, since no take holds them. Its
  lines carry the ids the next turn's first take would; they are never written, and the app answers without them.

See docs/CHAT_PLAY.md#a-hypothetical-reply.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.core.inference import TurnUsageLedger
from grimoire.generation import GenerationError, plan_generation_turn
from grimoire.play.boundaries import check_presented
from grimoire.play.runner import _hearing, _held_back, _here, _table, _table_command
from grimoire.play.scene import _declined, _follows, _refuse, _room, _table_audience, _table_known
from grimoire.play.together import read_post
from grimoire.providers.meter import TurnMeter
from grimoire.scene import CallRecord, Post, SceneRefused, Take, TurnRecord, turn_id

from .tracing import record_calls, span, traced

#: The plan id suffix a hypothetical's one call is planned and metered under: never a take's ``<turn_id>.<n>``,
#: so no usage booked for it can be read as a take's.
PLAN_SUFFIX = "hypothetical"


@dataclass(frozen=True, slots=True)
class Hypothetical:
    """A hypothetical reply, held only by the caller. ``post`` is the line as ``post`` would have read it and
    ``take`` the finished, screened run of the speaker's call: its lines (none when the call failed, was refused
    or was held back) and its call with every attempt. Neither was written anywhere. ``names`` are the cast's
    names as the chat read them, to show both lines by."""

    chat: ChatRef
    speaker: InstanceRef
    post: Post
    take: Take
    names: Mapping[InstanceRef, str]


@traced("imagine")
def imagine(runner, chat: ChatRef, by, speaker: InstanceRef, text: object,  # type: ignore[no-untyped-def]
            *, mode: str = "auto") -> Hypothetical:
    """``speaker``'s possible reply, on ``runner`` (a ``SceneRunner``), to ``text`` said or done by the chat's player
    character in ``mode`` (``auto``, ``speech`` or ``action``) as the next turn; ``by`` is the player's trusted
    handle. Refused with the runner's own reasons, plus ``table_request``, ``turn_open`` and ``not_heard``; nothing
    is written either way. Traced as the runner's public calls are (``imagine``)."""
    context = runner._context(chat)
    scene = context.scene
    principal = runner._principal(scene, by)
    if _table_command(scene, text, mode) is not None:
        raise _refuse("table_request", "a table request acts on the table: post it to make it")
    runner._ready(scene)
    if runner.runtime is None:
        raise _refuse("no_runtime", "no model is configured")
    persona = scene.persona
    if persona is None:
        raise _refuse("no_persona", "this chat has no player character to say the line")
    if persona not in scene.cast:
        raise _refuse("persona_absent", "the player character is not present in this scene")
    if persona not in scene.on_stage:
        raise _refuse("not_on_stage", "the player character has left the scene's stage")
    last = scene.last
    if last is not None and last.open:
        raise _refuse("turn_open", "the last turn is still open: keep a take, or pass it, before imagining the next")
    audience, known = _table_audience(scene, principal), _table_known(scene)
    check_presented(runner, chat, audience, complete=known)  # 016, before any call is billed
    if context.party_problems:
        raise _refuse("party_unreadable", "who is present cannot be read in full")
    unseated = [str(actor) for actor in scene.cast if scene.controller(actor) is None]
    if unseated:
        raise _refuse("unassigned_actor", f"nobody controls {unseated}; seat the chat first")
    chosen = runner._speakers(context, (speaker,))  # a present director NPC, on stage, with a voice
    number = 1 if last is None else last.number + 1
    device = runner.scenes.tag_for(scene.turns, number)
    tid = turn_id(chat, number, device)
    post = read_post(scene, chat, principal, persona, text, mode, tid=tid, number=number, contribution_id=tid)
    if speaker not in post.audience:
        raise _refuse("not_heard", "this character would not hear that line, so it has nothing to answer")
    # the next turn as it would stand once posted: built to brief the speaker, never written
    record = TurnRecord(chat, number, "post", post, device=device, follows=_follows(last))
    rooms = {speaker: _room(scene, speaker)}
    try:
        with span("plan"):
            plan = plan_generation_turn(chat.campaign_id, f"{tid}.{PLAN_SUFFIX}",
                                        present=[actor.instance_id for actor in scene.on_stage],
                                        speakers=[speaker.instance_id], controls=scene.controls,
                                        policy=runner.policy)
    except GenerationError as error:
        raise _refuse(error.reason, "the reply cannot be planned") from None
    if not plan.calls:
        raise _refuse("nothing_to_generate", "the inference policy plans no reply for this character")
    try:
        with runner.store.lock:
            briefs, _ = runner._briefs(context, chosen, (*scene.records, record), 1)
    except GenerationError as error:
        raise _refuse(error.reason, "a brief cannot be built") from None
    planned = tuple(CallRecord(call.call_id.phase, call.stage, call.call_id.actor_id, call.call_id.ordinal,
                               "planned") for call in plan.calls)
    running = Take(1, "running", "generated", scene.cast, calls=planned, declined=_declined(plan), by=principal,
                   on_stage=_here(scene), players=_table(scene))
    meter = TurnMeter(TurnUsageLedger(plan))
    silent = tuple(voice for actor, voice in runner._voices(context) if actor != speaker)
    try:
        try:
            with span("provider", calls=len(plan.calls)):
                outcome = runner.runtime.run_turn(meter, briefs, controls=scene.controls, scene_voices=silent,
                                                  hears=_hearing(scene, rooms))
        except Exception as error:  # the attempts are on the meter: the answer reports them, as a take would
            outcome, failure = None, getattr(error, "reason", "generation_failed")
        else:
            failure = None
        with span("review"):
            take = runner._finished(record, running, plan, meter, outcome, failure, scene, (), rooms)
    finally:
        record_calls(meter)
    if take.lines:
        try:  # 016 again, on the screened lines, against the table and its configuration as they stand now
            runner._check_now(chat, principal, take.lines)
        except SceneRefused as refused:
            take = _held_back(take, refused.reason)
    return Hypothetical(chat, speaker, post, take, dict(scene.names))


__all__ = ["PLAN_SUFFIX", "Hypothetical", "imagine"]
