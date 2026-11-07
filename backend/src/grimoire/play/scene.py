"""One chat as the runner reads it (035): the ``Scene`` value and the pure helpers the runner's calls share.

Split out of ``runner.py`` to keep each module readable; nothing here writes. See docs/SCENES.md.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from grimoire.comms import Message, Presence, TransmissionError, transmit
from grimoire.context import Budget
from grimoire.contextstore import stored_record
from grimoire.core import DIRECTOR
from grimoire.core.authority import ControlAssignment
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.party import PartyRefused, SceneCast, stage
from grimoire.scene import ImportedHistory, SceneLine, SceneRefused, Take, TurnRecord
from grimoire.scenestore import ChatTurns, StoredTurn
from grimoire.security import is_human
from grimoire.staging import Stage, StagingRefused, hears, sees, where
from grimoire.staging import presence as stage_presence

#: 033 relations are sequenced by the campaign's moments; membership "now" is after every one of them.
NOW = 2**62
#: Room for a turn's context: each speaker's pack, and the director's lore.
DEFAULT_BUDGET = Budget(1500)
#: How often a finished take retries its write when another run changed the turn meanwhile.
WRITE_RETRIES = 3
_MACRO = {"char": re.compile(r"\{\{\s*char\s*\}\}", re.IGNORECASE), "user": re.compile(r"\{\{\s*user\s*\}\}",
                                                                                       re.IGNORECASE)}


def _refuse(reason: str, message: str = "") -> SceneRefused:
    return SceneRefused(reason, message)


@dataclass(frozen=True)
class Scene:
    """One chat as it stands: its turns, control as recorded, who is present, and what is wrong.

    ``pending`` is the open turn a generation would answer (a post saved before its generation ran is
    still here after a restart). ``interrupted`` is true when that turn holds a ``running`` take: a run
    that never finished, whose usage is unknown; the next generation supersedes it. ``problems`` are
    ``(code, detail)`` pairs from the snapshot, the turn files, the party and the voices, never hidden.
    ``staged`` is 033's ``SceneCast`` the cast was staged as (``cast`` is its ``present``), or ``None`` when
    no cast could be staged (membership unreadable): then nobody is present and play waits.
    ``imported`` is the chat's imported history (036/026's transcript pages), heard before turn 1 by the cast
    the chat was imported with; ``None`` when the chat has none, or when its pages cannot all be read (then
    nobody heard any of it, and ``problems`` says why).
    ``stage`` is the chat's latest 041 ``Stage`` as the runner's stage source gave it, once it checked against
    ``staged`` (``None`` when the runner has no source, the source has no stage for the chat, or the stage was
    refused: then ``problems`` holds a ``stage`` entry and play waits). With a stage, who is physically here is
    ``on_stage`` and who hears or sees what follows the stage, not the cast alone.
    """

    chat: ChatRef
    turns: ChatTurns
    controls: tuple[ControlAssignment, ...]
    cast: tuple[InstanceRef, ...]
    persona: InstanceRef | None
    names: Mapping[InstanceRef, str]
    problems: tuple[tuple[str, str], ...]
    staged: SceneCast | None = None
    imported: ImportedHistory | None = None
    stage: Stage | None = None

    @property
    def on_stage(self) -> tuple[InstanceRef, ...]:
        """The cast members physically in the scene: the cast less the stage's absences (the cast with no stage)."""
        if self.stage is None:
            return self.cast
        here = set(self.stage.on_stage())
        return tuple(actor for actor in self.cast if actor in here)

    @property
    def records(self) -> tuple[TurnRecord, ...]:
        return self.turns.records()

    @property
    def last(self) -> TurnRecord | None:
        found = self.turns.last
        return None if found is None else found.record

    @property
    def pending(self) -> TurnRecord | None:
        last = self.last
        return last if last is not None and last.open else None

    @property
    def interrupted(self) -> bool:
        pending = self.pending
        return pending is not None and pending.running is not None

    @property
    def paused(self) -> bool:  # any open pause: on the line, set aside (028), or kept beside a turn by sync
        return self.turns.paused

    def controller(self, actor: InstanceRef) -> str | None:
        found = {c.controller_id for c in self.controls
                 if c.campaign_id == actor.campaign_id and c.actor_id == actor.instance_id}
        return next(iter(found)) if len(found) == 1 else None

    @property
    def players(self) -> tuple[InstanceRef, ...]:
        """The present player characters: actors in the cast a human controls now."""
        return tuple(actor for actor in self.cast if self.controller(actor) not in (None, DIRECTOR))

    @property
    def npcs(self) -> tuple[InstanceRef, ...]:
        return tuple(actor for actor in self.cast if self.controller(actor) == DIRECTOR)


@dataclass
class _Context:
    """Everything one call reads, from one ``LibraryView``."""

    scene: Scene
    view: object
    captured: object  # 022's ChatSnapshot
    voices: object  # SnapshotVoices
    party_problems: tuple[str, ...]


def _readable(scene: Scene, *, pausing: bool = False) -> None:
    """Every write needs the chat's whole history: one unreadable turn file stops them all, and so does a fork
    (two turns of one number after one turn, every file readable), until the owner chooses a branch (028), and
    a turn sync left in a keep-both conflict (each device reads its own version), until it is resolved; a pause
    still goes on then (016)."""
    if not scene.turns.complete:
        if set(scene.turns.problems) == {(str(scene.chat), "turn_fork")}:
            raise _refuse("turn_fork", "this chat holds two devices' turns of one number; nothing is written "
                                       "until the owner chooses which goes on")
        raise _refuse("turns_unreadable", "a turn file of this chat cannot be read; nothing is written")
    if scene.turns.conflicted and not pausing:
        raise _refuse("turn_conflict", "a turn of this chat is in a sync conflict; nothing is played until it is "
                                       "resolved")


def _follows(last: TurnRecord | None) -> str | None:
    """The turn a new turn is played after (028: two devices' branches stay apart once one is chosen)."""
    return None if last is None else last.turn_id


class _Stale(Exception):
    """The run's fence went stale before its take was written."""


def _stage(party, chat: ChatRef, candidates: Sequence[InstanceRef]
           ) -> tuple[SceneCast | None, list[tuple[str, str]]]:
    """033's staged cast of ``candidates`` (the chat's bound actors that are neither reserved nor uncertain),
    with each member ``stage`` refuses (``missing_actor``, ``instance_id_conflict``...) left out and reported by
    its reason. ``None`` (reported ``cast_unstaged``) when the cast as a whole cannot be staged, such as while a
    relation file cannot be read: then nobody is present."""
    chosen = sorted(set(candidates), key=str)
    problems: list[tuple[str, str]] = []
    while True:
        try:
            return stage(party.state, chat.chat_id, chosen, instances=party.instances), problems
        except PartyRefused as refused:
            members = [member for member in refused.members if member in chosen]
            if not members:
                problems.append(("cast_unstaged", refused.reason))
                return None, problems
            problems += [(refused.reason, str(member)) for member in members]
            chosen = [actor for actor in chosen if actor not in members]


def _has_imported(scene: Scene) -> bool:
    return any(code == "imported_history_unreadable" for code, _ in scene.problems)


def _no_clock() -> str:
    raise AssertionError("the runner only reads party membership")


def _no_id(prefix: str) -> str:
    raise AssertionError("the runner only reads party membership")


def _assigned(scene: Scene, actor: InstanceRef) -> bool:
    return any(c.campaign_id == actor.campaign_id and c.actor_id == actor.instance_id for c in scene.controls)


def _human_of(scene: Scene, actor: InstanceRef) -> str | None:
    controller = scene.controller(actor)
    return controller if controller not in (None, DIRECTOR) else None


def _declined(plan) -> tuple[str, ...]:
    """The optional calls the policy declined, as ``<phase>:<stage>:<actor or scene>:<reason>``."""
    return tuple(f"{d.phase}:{d.request.stage.value}:{d.request.actor_id or 'scene'}:{d.reason}" for d in plan.declined)


def _latest(knowledge) -> int:
    """The newest sequence in a campaign's knowledge (0 for none): what every actor knows "now"."""
    latest = 0
    for family in (knowledge.events, knowledge.propositions, knowledge.rulings, knowledge.evidence,
                   knowledge.beliefs, knowledge.disclosures):
        for record in family:
            moment = getattr(record, "moment", None)
            sequence = getattr(moment, "sequence", None)
            if type(sequence) is int and sequence > latest:
                latest = sequence
    return latest


def _name_index(scene: Scene) -> dict[str, InstanceRef]:
    """Display names a player may address, for 032's reader. Two actors sharing a name both stay
    reachable under it (032 then reads the name as ambiguous): the keys differ only in trailing space,
    which the reader folds away."""
    index: dict[str, InstanceRef] = {}
    for actor in scene.cast:
        name = scene.names.get(actor)
        if not name or not name.strip():
            continue
        key = name
        while key in index:
            key += " "
        index[key] = actor
    return index


#: How a refused stage stops play: the source's own failures by name, every refusal of ``check_cast`` as a mismatch.
_STAGE_REFUSALS = {"stale_stage": "stale_stage", "stage_diverged": "stage_diverged",
                   "stage_unreadable": "stage_unreadable", "stage_lost": "stage_lost"}


def _stage_ok(scene: Scene) -> None:
    """Fail closed on a stage the runner refused: who hears whom cannot be told, so nothing is spoken or kept."""
    for code, detail in scene.problems:
        if code == "stage":
            raise _refuse(_STAGE_REFUSALS.get(detail, "stage_mismatch"),
                          f"this chat's stage cannot be used ({detail}); stage the scene again")


def _staged(answers, *chains: tuple[str, ...]) -> None:
    """Who hears or sees a line decides what each actor later knows: never on a guess (041's rule)."""
    if any(answer.assumed for answer in answers) or any(chains):
        raise _refuse("rests_on_assumption", "who would hear or see this rests on assumed staging; establish it "
                                             "first")


def _off_stage(stage: Stage, actor: InstanceRef) -> bool:
    return where(stage, actor).status == "off_stage"


def _room(scene: Scene, speaker: InstanceRef) -> tuple[InstanceRef, ...]:
    """Who hears ``speaker`` speak to the room: the cast with no stage; with one, the speaker and every actor in
    earshot (its area, and the next through an open way). An actor off stage speaks to no room (``not_on_stage``)."""
    stage = scene.stage
    if stage is None:
        return scene.cast
    if speaker not in stage.actors or _off_stage(stage, speaker):
        raise _refuse("not_on_stage", f"{speaker} is not on stage")
    answers = [(actor, hears(stage, actor, speaker, "speech")) for actor in stage.actors if actor != speaker]
    _staged([answer for _, answer in answers], where(stage, speaker).assumed)
    return tuple(sorted({speaker, *(actor for actor, answer in answers if answer.ok)}, key=str))


def _seen(scene: Scene, actor: InstanceRef) -> tuple[InstanceRef, ...]:
    """Who sees ``actor``'s visible action: everyone present with no stage; with one, the actor and every actor
    who can see it (041's sight rule: not through a shut door, not inside a thing)."""
    stage = scene.stage
    if stage is None:
        return scene.cast
    if actor not in stage.actors or _off_stage(stage, actor):
        raise _refuse("not_on_stage", f"{actor} is not on stage")
    answers = [(viewer, sees(stage, viewer, actor)) for viewer in stage.actors if viewer != actor]
    _staged([answer for _, answer in answers], where(stage, actor).assumed)
    return tuple(sorted({actor, *(viewer for viewer, answer in answers if answer.ok)}, key=str))


def _perceived(scene: Scene, actor: InstanceRef) -> tuple[InstanceRef, ...]:
    """The cast ``actor`` perceives as present: the whole cast with no stage; with one, the actor and every actor it
    can see by 041's sight rule (its own area, the next through an open way, nobody inside a thing, nobody off
    stage). So someone behind a shut door is not present to it: not in its pack's ``present``, not in 039's cast.
    An actor off stage perceives only itself. Never on a guess (``rests_on_assumption``)."""
    stage = scene.stage
    if stage is None:
        return scene.cast
    if actor not in stage.actors or _off_stage(stage, actor):
        return (actor,)
    answers = [(other, sees(stage, actor, other)) for other in stage.actors if other != actor]
    _staged([answer for _, answer in answers], where(stage, actor).assumed)
    return tuple(sorted({actor, *(other for other, answer in answers if answer.ok)}, key=str))


def _audience(message: Message, scene: Scene, chat: ChatRef, tid: str, number: int) -> tuple[InstanceRef, ...]:
    """Who hears a message: 032's receivers and the sender; in an empty room, the sender alone. With a stage,
    032's presence is the stage's (041's ``presence``): only those within earshot on the message's own channel,
    so a whisper to someone behind a shut door is ``addressee_absent``."""
    if scene.stage is None:
        presence = Presence(chat.campaign_id, chat.chat_id, scene.cast)
    else:
        try:
            presence = stage_presence(scene.stage, message)
        except StagingRefused as refused:
            raise _refuse(refused.reason, "the message cannot be placed on this chat's stage") from None
    try:
        sent = transmit(message, presence, sequence=number, provenance=f"chat:{chat.chat_id}", turn_id=tid)
    except TransmissionError as error:
        if error.reason == "no_audience" and not message.addressees:
            return (message.sender,)
        raise _refuse(error.reason, "the message cannot reach anyone here") from None
    return tuple(sorted({*sent.receivers, message.sender}, key=str))


def _payload(snapshot, ref, role: str) -> Mapping[str, object]:
    try:
        found = stored_record(snapshot.resolve(ref, role))
    except Exception:  # 022's refusals: a source that cannot be read gives nothing to play from
        return {}
    return found[1] if found is not None and isinstance(found[1], Mapping) else {}


def _first_text(items: object, *, wanted: str | None = None, chosen: bool = False) -> str | None:
    """The first usable text, or the one ``wanted`` by id. A group-only greeting is never a default, but one
    the player ``chosen`` by its id is theirs to open with."""
    if not isinstance(items, (list, tuple)):
        return None
    for item in items:
        if not isinstance(item, Mapping) or (item.get("group_only") and not chosen):
            continue
        if wanted is not None and item.get("id") != wanted:
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            return text
    return None


def _macros(text: str, char: str | None, user: str | None) -> str:
    text = _MACRO["char"].sub(lambda _m: char or "the narrator", text)
    return _MACRO["user"].sub(lambda _m: user or "you", text)


def _scenario_text(context: _Context) -> str:
    """The director's scenario: a bound scenario's premise, setting and instructions, else the chat's premise."""
    chat = context.captured.chat
    for binding in chat.active_bindings("scenario"):
        payload = _payload(context.captured.snapshot, binding.ref, "scenario")
        parts = []
        premise = payload.get("premise")
        if isinstance(premise, str) and premise.strip():
            parts.append(premise.strip())
        texts = payload.get("texts")
        setting = texts.get("setting") if isinstance(texts, Mapping) else None
        if isinstance(setting, str) and setting.strip():
            parts.append(f"Setting: {setting.strip()}")
        instructions = payload.get("instructions")
        if isinstance(instructions, str) and instructions.strip():
            parts.append(f"Instructions: {instructions.strip()}")
        if parts:
            return "\n\n".join(parts)
    premise = chat.launch.premise
    return premise.strip() if isinstance(premise, str) else ""


def _find_line(scene: Scene, line_id: object) -> tuple[StoredTurn, Take, SceneLine]:
    if not isinstance(line_id, str):
        raise _refuse("unknown_line", "a line id is text")
    for stored in scene.turns.turns:
        for take in stored.record.takes:
            for line in take.lines:
                if line.line_id == line_id:
                    return stored, take, line
    raise _refuse("unknown_line", f"no line {line_id!r} in this chat")


def _mark(take: Take, line_ids: set[str], state: str, principal: str) -> Take:
    lines = tuple(replace(line, state=state, by=principal) if line.line_id in line_ids and line.shown else line
                  for line in take.lines)
    changed = replace(take, lines=lines)
    if changed.state == "proposed" and not changed.shown():
        changed = replace(changed, state="withdrawn", by=principal)
    return changed


def _table_audience(scene: Scene, principal: str) -> frozenset[str]:
    """016's ``BoundaryContext.audience``, authenticated: every human 003's ledger records as controlling a present
    player character (on stage or not: the table, not the room), and the requester, whom ``_principal``
    authenticated as one of them or the library owner. Never a name from a post, a reply or the caller."""
    controllers = {scene.controller(pc) for pc in scene.players}
    return frozenset(each for each in controllers if isinstance(each, str) and is_human(each)) | {principal}
