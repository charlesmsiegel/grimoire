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
    def paused(self) -> bool:
        last = self.last
        return last is not None and last.paused

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

def _readable(scene: Scene) -> None:
    """Every write needs the chat's whole history: one unreadable turn file stops them all, and so does a fork
    (two turns of one number, every file readable), which only 028's turn merge can settle."""
    if scene.turns.complete:
        return
    if set(scene.turns.problems) == {(str(scene.chat), "turn_fork")}:
        raise _refuse("turn_fork", "this chat holds two devices' turns of one number; nothing is written until "
                                   "they are merged")
    raise _refuse("turns_unreadable", "a turn file of this chat cannot be read; nothing is written")


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


def _audience(message: Message, scene: Scene, chat: ChatRef, tid: str, number: int) -> tuple[InstanceRef, ...]:
    """Who hears a message: 032's receivers and the sender; in an empty room, the sender alone."""
    presence = Presence(chat.campaign_id, chat.chat_id, scene.cast)
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
