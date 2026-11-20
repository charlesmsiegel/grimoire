"""Several player characters in one turn (058): a local table where each PC has its own controller.

A combined turn holds the posts of two or more present player characters, in the order they were submitted,
and the cast answers all of them once: one turn, one generation, no reply in between. What it holds to:

- **Each post is its own controller's.** A contribution is submitted with the authenticated handle of the human
  who controls its character now (003's ``submit_contribution``), and its post records that principal: a post is
  never written by one player for another's character, and the director never writes one.
- **No character becomes a player character by being posted for.** A contribution for a character the director
  plays (an NPC), for one nobody controls, or for one not present is refused. A character is handed to a player
  only by the owner, explicitly (``seat_player``), and that change is recorded in 003's control history.
- **All or nothing.** Every contribution is read (008's routing, 032's transmission, 003's authority) before
  anything is written, and the turn is written in one compare-and-swap: a refused contribution writes no post of
  any character, so nothing is half-posted and nothing is answered in between. The same submission retried onto
  its own turn, before anything was generated, returns that turn.
- **Who heard what is each post's own.** Each post's audience is read for its own speaker and channel, exactly as
  a single post's is; a whisper in a combined turn is heard by its addressees and its speaker only.
- **An older build refuses it rather than lose a post.** A combined turn is written under its own schema
  (``grimoire.scene-turn/2``), so a build that reads one post a turn refuses the turn file and plays nothing in
  the chat, instead of answering, showing or rewriting it with the other players' posts missing.

A combined turn is not labelled, framed or given an effect in this slice: several characters' posts are not
one action to adjudicate, and whose key one effect would need is not settled (``combined_turn``).

See docs/LOCAL_MULTI_PC.md.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from grimoire.comms import TransmissionError, Unread, read_message
from grimoire.core import DIRECTOR
from grimoire.core.authority import AuthorityError, ControlAssignment, PlayerContribution, submit_contribution
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.intent import RoutingContext, Utterance, route
from grimoire.intent.text import wrapped
from grimoire.ledgers.codec import ControlChange
from grimoire.play.scene import Scene, _audience, _follows, _name_index, _refuse, _seen
from grimoire.scene import Post, SceneRefused, TurnRecord, audible, turn_from_payload, turn_id
from grimoire.security import is_human

#: The most player characters one combined turn holds: a local table, not a crowd. Each post is at most the text
#: API's 20,000 characters, so a full combined turn stays far below a turn file's limit.
MAX_TOGETHER = 8

#: What a player can do about a line 032 cannot read, by its reason: the refusal says how to post it so it reads.
UNREAD_HINTS = {
    "unresolved_private": "the line sounds meant for fewer ears than the room's: put the words in quotes to say "
                          "them aloud, or post an action such as I whisper to (name): \"...\"",
    "mixed_narration": "the line mixes the words said with narration: post the words alone, or in quotes, and the "
                       "rest as an action",
    "unknown_addressee": "use a name from the scene and set the words off after it, such as "
                         "I whisper to (name): \"...\"",
    "ambiguous_addressee": "more than one listener fits: name one, and put the words in quotes, such as "
                           "I whisper to (name): \"...\"",
    "conflicting_channel": "the line both whispers and speaks mind to mind: choose one, such as "
                           "I tell (name) telepathically: \"...\"",
    "indirect_speech": "say how it reaches them, such as I whisper to (name): \"...\" or "
                       "I tell (name) telepathically: \"...\", or post the words as speech",
    "unclear_content": "the words said are unclear: put the exact words in quotes after the name, such as "
                       "I whisper to (name): \"...\"",
    "no_content": "the line has no words for anyone to hear",
}


@dataclass(frozen=True, slots=True)
class Contribution:
    """One player character's part of a combined turn: what its controller wrote, with that controller's handle."""

    by: object  # a trusted handle the library's authority issued to the character's controller
    actor: InstanceRef
    text: str
    mode: str = "auto"


def read_post(scene: Scene, chat: ChatRef, principal: str, actor: InstanceRef, text: object, mode: str, *,
              tid: str, number: int, contribution_id: str) -> Post:
    """``text`` as ``principal``'s post for ``actor``, read as a post of turn ``tid`` would be: 008 routes it, 003
    accepts it only from the actor's controller, and 032 says who hears it. Writes nothing; refusals name why."""
    if not isinstance(text, str) or not text.strip():
        raise _refuse("no_content", "a post needs text")
    try:
        utterance = Utterance(text, mode)
        intent = route(utterance, RoutingContext(active_pc=actor.instance_id, can_direct=False))
    except ValueError as error:
        raise _refuse("invalid_field", str(error)[:200]) from None
    if intent.kind == "uncertain":
        raise _refuse("uncertain", intent.feedback)
    if intent.kind not in ("pc_speech", "pc_action"):
        raise _refuse("not_in_this_slice", f"{intent.kind} is not played yet")
    if intent.kind == "pc_speech" and intent.reason == "explicit_mode" and wrapped(text.strip(), '"', '"') is not None:
        # 008 takes the quotes off a wholly quoted line in the speech lane as in auto; 032 still reads its words as
        # quoted ones, said aloud privacy words and all, as it would had auto routed the same line
        intent = replace(intent, reason="quoted")
    kind = "speech" if intent.kind == "pc_speech" else "action"
    try:
        submit_contribution(PlayerContribution(chat.campaign_id, contribution_id, chat.chat_id, tid,
                                               actor.instance_id, principal, kind, intent.text), scene.controls)
    except AuthorityError:
        raise _refuse("not_your_character", f"{principal} does not control {actor}") from None
    except ValueError as error:
        raise _refuse("invalid_field", str(error)[:200]) from None
    try:
        # 032's message id is the turn's: a combined turn is one message moment, and its posts are told apart by
        # their speakers (one post per character)
        read = read_message(intent, actor, _name_index(scene), message_id=tid)
    except TransmissionError as error:
        raise _refuse(error.reason, "the post cannot be read") from None
    if isinstance(read, Unread):
        # 032: an Unread transmits nothing. An action that is no message at all is seen by everyone present;
        # one 032 cannot rule out as a private message (a note, an aside, a signal) is withheld: its sender
        # alone hears it. A message aimed at someone 032 cannot resolve is refused, never made public.
        if kind == "speech" or read.reason not in ("not_directed", "unresolved_private"):
            hint = UNREAD_HINTS.get(read.reason, "the line cannot be read as a message; say who it is for, and what")
            raise _refuse(read.reason, hint)
        withheld = read.reason == "unresolved_private"
        post = Post(principal, actor, "action", intent.text, (actor,) if withheld else _seen(scene, actor),
                    withheld=withheld)
    else:
        post = Post(principal, actor, kind, read.text, _audience(read, scene, chat, tid, number),
                    read.channel, read.addressees)
    if not audible(post):  # as clipped and shown
        raise _refuse("no_content", "a post needs words someone could hear")
    return post


def _player_character(scene: Scene, actor: object) -> InstanceRef:
    """``actor`` if it is a present player character: never an NPC promoted by being posted for."""
    if type(actor) is not InstanceRef:
        raise _refuse("invalid_field", "a contribution names its character by reference")
    if actor not in scene.cast:
        raise _refuse("not_present", f"{actor} is not in this scene")
    controller = scene.controller(actor)
    if controller is None:
        raise _refuse("unassigned_actor", f"nobody controls {actor} yet")
    if controller == DIRECTOR:
        raise _refuse("not_a_player_character", f"{actor} is played by the director; the owner hands a character to "
                                                "a player with seat_player before anyone posts for it")
    if actor not in scene.on_stage:  # 041: in the cast, but physically gone; it speaks in no room
        raise _refuse("not_on_stage", f"{actor} has left the scene's stage")
    return actor


def post_together(runner, chat: ChatRef, contributions: Iterable[Contribution]) -> TurnRecord:  # type: ignore[no-untyped-def]
    """See ``SceneRunner.post_together``."""
    items = tuple(contributions)
    if not all(type(item) is Contribution for item in items):
        raise _refuse("invalid_field", "a combined turn is made of Contribution values")
    if not 1 <= len(items) <= MAX_TOGETHER:
        raise _refuse("invalid_field", f"a combined turn holds 1 to {MAX_TOGETHER} contributions")
    context = runner._context(chat)
    scene = context.scene
    principals = [runner._principal(scene, item.by) for item in items]  # every handle, before anything is read
    runner._ready(scene)
    actors = [_player_character(scene, item.actor) for item in items]
    if len(set(actors)) != len(actors):
        raise _refuse("invalid_field", "a combined turn holds one contribution per player character")
    last = scene.last
    number = 1 if last is None else last.number + 1
    device = runner.scenes.tag_for(scene.turns, number)
    tid = turn_id(chat, number, device)
    # each post's 003 contribution id, as ``TurnRecord.post_id`` gives it: the turn id, then ``<turn_id>.p<n>``
    posts = tuple(read_post(scene, chat, principal, item.actor, item.text, item.mode, tid=tid, number=number,
                            contribution_id=tid if index == 1 else f"{tid}.p{index}")
                  for index, (item, principal) in enumerate(zip(items, principals), 1))
    if last is not None and last.open:
        if last.posts == posts and not last.takes:
            return last  # the same submission retried: it is already saved
        raise _refuse("turn_open", "the last turn is still open: keep a take, or pass it")
    record = TurnRecord(chat, number, "post", posts[0], device=device, follows=_follows(last), also=posts[1:])
    runner._write(record, None, after=scene.turns.last)
    return record


def _post_waits(runner, actor: InstanceRef) -> bool:  # type: ignore[no-untyped-def]
    """Whether a post for ``actor`` waits on an answer in an open turn of any chat of its campaign: 003's control is
    campaign-wide, and 107 lets one actor play in several chats of a scope. A turn file that cannot be read, or
    decoded, counts as one that might (fail closed); a branch head a fork choice set aside goes on in no chat."""
    found = runner.store.campaign_turns(actor.campaign_id)
    if found.problems:
        raise _refuse("turns_unreadable", "a turn of this campaign cannot be read, so whether a post for this "
                                          "character still waits cannot be told")
    for ref, stored in found.records.items():
        try:
            turn = turn_from_payload(ref, stored.record.schema, stored.record.payload)
        except SceneRefused:
            raise _refuse("turns_unreadable", f"turn {ref} cannot be read, so whether a post for this character "
                                              "still waits cannot be told") from None
        if turn.open and turn.set_aside is None and any(post.actor == actor for post in turn.posts):
            return True
    return False


def seat_player(runner, chat: ChatRef, by, actor: InstanceRef, controller: str  # type: ignore[no-untyped-def]
                ) -> tuple[ControlAssignment, ...]:
    """See ``SceneRunner.seat_player``."""
    context = runner._context(chat)
    scene = context.scene
    principal = runner._principal(scene, by)
    if principal not in runner.authority.owners:
        raise _refuse("not_owner", "only the library owner hands a character to a player")
    if controller != DIRECTOR and not (isinstance(controller, str) and is_human(controller)):
        raise _refuse("invalid_field", "a character is handed to a human player, or back to the director")
    runner._ready(scene)
    if type(actor) is not InstanceRef or actor not in scene.cast:
        raise _refuse("not_present", "only a character in this scene is handed to a player")
    if actor == scene.persona:
        raise _refuse("persona", "the chat's persona is its player's own character; it is seated with the chat")
    current = scene.controller(actor)
    if current is None:
        raise _refuse("unassigned_actor", "seat the chat first: this character has no controller yet")
    if current == controller:
        return tuple(scene.controls)
    if current != DIRECTOR and controller != DIRECTOR:
        raise _refuse("seated_elsewhere", f"another player controls {actor}; hand it back to the director first")
    if _post_waits(runner, actor):
        # its player's post is still waiting on the cast's answer: handed elsewhere now, the model would answer that
        # post as the character it was written for, and its player could no longer keep or pass it
        raise _refuse("turn_open", f"{actor}'s post is still open: keep a take, or pass it, first")
    if controller != DIRECTOR:
        # 003 tells a delegation's validity by its grantor controlling the actor now, so a delegation a player gave
        # while they held this character would revive were it handed back to them: never hand it to a grantor of an
        # unrevoked delegation naming it (the Wave 1 obligation: never revive a delegation on a control change)
        state = runner.sovereignty.load(chat.campaign_id)
        revoked = {(r.delegation_id, r.revoked_by) for r in state.revocations}
        if any(d.actor_id == actor.instance_id and d.granted_by == controller
               and (d.delegation_id, d.granted_by) not in revoked for d in state.delegations):
            raise _refuse("delegations_on_record", f"{controller} gave a delegation naming {actor} that is not "
                                                   "revoked; it would revive were the character handed back")
    try:
        runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, actor.instance_id, controller),
                                  by)
    except AuthorityError as error:
        raise _refuse("not_seated", str(error)[:200]) from None
    return tuple(runner.sovereignty.load(chat.campaign_id).controls)


__all__ = ["MAX_TOGETHER", "UNREAD_HINTS", "Contribution", "post_together", "read_post", "seat_player"]
