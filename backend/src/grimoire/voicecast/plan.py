"""A chat's performance plan (083): the table's accepted text, cue by cue, with the voice each cue speaks in.

``plan_performance`` reads a chat's played turns (its line, oldest first) exactly as the table saw them: every post
but a private one, and the shown lines of each kept take (a take played on a 041 stage only where a player
character of that turn heard the line, as ``table_lines(as_heard=True)`` decides). A proposal, an unkept take, a
withdrawn or struck line and a private message are never cued: nothing reaches the speaker that the table did not
already read. A chat's imported history (pages from before turn 1) is not cued in this slice.

Each cue keeps the accepted text **as it is** (``text``, with its digest) and, beside it, the ``script``: what a
voice would say. The script is derived, never written back: a character's ``*stage directions*`` are left unsaid
(they are shown, not spoken), and a narrator's asterisks are only dropped. A cue whose script says nothing, an
action post and a speaker nobody cast are **text only**: shown and captioned, never given another speaker's voice.

The voice is chosen by the line's own recorded identity, never by a name or the source's head now:

1. the campaign's own choice for the actor (``actor``),
2. the definition revision the line recorded its actor speaking as, exactly, then that character at any revision,
3. the voice profile the line was written with, when it is another revision's (a derived portrayal, such as an
   outfit, with no profile of its own inherits its base's: 022/014's ``_inherited_profile``), exactly, then that
   character at any revision,

and for narration the campaign's narrator, then every campaign's. So a line spoken in revision ``r1`` keeps ``r1``'s
voice after the source moves to ``r2``; an outfit (a derived portrayal) speaks with its own voice when one is cast
for it and with its base's otherwise. A post records no revision: its speaker is voiced by the actor's own entry,
then the character the actor plays (``sources``). Every cue lists each rule it considered (``considered``), so a
disputed choice shows where it came from. Pure: no store, engine, clock or I/O.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.scene import SceneLine, TurnRecord

from .model import CastEntry, Casting, CastingError

NARRATOR_LABEL = "Narrator"
#: Cue statuses and the reasons a cue is text only.
VOICED, TEXT_ONLY = "voiced", "text_only"
REASONS = ("not_cast", "action", "no_speech")
#: A stage direction opens with an asterisk that starts a word (not one inside a word, as in "5*3") and runs to the
#: next asterisk on its line; one never closed runs to the end of its line. Any asterisk left is only dropped.
_DIRECTION = re.compile(r"(?<!\w)\*[^*\n]*\*")
_UNCLOSED = re.compile(r"(?<!\w)\*[^\n]*")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Considered:
    """One rule the choice looked at, in order: what it names and the entry it found (``None``: nothing cast)."""

    rule: str
    names: str
    entry: CastEntry | None


@dataclass(frozen=True, slots=True)
class Cue:
    """One accepted line or post, in transcript order, and how it is performed."""

    index: int
    turn: str
    kind: str  # "line", "post" or "outcome" (a decided action's outcome, read by the narrator)
    item: str  # the line id, or the post's contribution id
    speaker: str
    actor: InstanceRef | None
    revision: SourceRef | None
    portrayal: SourceRef | None
    text: str
    digest: str
    script: str
    status: str
    reason: str | None
    chosen: CastEntry | None
    considered: tuple[Considered, ...]

    @property
    def voice_key(self) -> str | None:
        return None if self.chosen is None or self.status != VOICED else self.chosen.voice.key

    @property
    def identity(self) -> str:
        """Who is speaking, for telling voices apart: the actor, or the narrator."""
        return "narrator" if self.actor is None else str(self.actor)


@dataclass(frozen=True, slots=True)
class SharedVoice:
    """A voice two or more speakers would speak with, so they would not be told apart by ear."""

    voice: str
    speakers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Performance:
    cues: tuple[Cue, ...]
    shared: tuple[SharedVoice, ...]

    @property
    def fingerprint(self) -> str:
        """What the performance is: each cue's item, text digest, status and voice. A client that renders it in
        windows names this, so a scene that changed between two windows is refused, not played out of step."""
        rows = "\n".join(f"{cue.item}\t{cue.digest}\t{cue.status}\t{cue.voice_key or ''}" for cue in self.cues)
        return digest(rows)

    @property
    def voiced(self) -> tuple[Cue, ...]:
        return tuple(cue for cue in self.cues if cue.status == VOICED)


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def script_of(text: str, *, narrator: bool) -> str:
    """What a voice says for ``text``: a character's ``*stage directions*`` left unsaid, a narrator's asterisks
    dropped, and whitespace made single. The text itself is never changed."""
    spoken = text if narrator else _UNCLOSED.sub(" ", _DIRECTION.sub(" ", text))
    spoken = spoken.replace("*", " ")
    return _SPACE.sub(" ", spoken).strip()


def _choose(rules: list[tuple[str, str, tuple[str, str]]], lookup: Mapping[tuple[str, str], CastEntry]
            ) -> tuple[CastEntry | None, tuple[Considered, ...]]:
    considered = []
    chosen = None
    for rule, names, key in rules:
        entry = lookup.get(key)
        considered.append(Considered(rule, names, entry))
        if chosen is None and entry is not None:
            chosen = entry
    return chosen, tuple(considered)


def _character_rules(actor: InstanceRef | None, revision: SourceRef | None, portrayal: SourceRef | None
                     ) -> list[tuple[str, str, tuple[str, str]]]:
    """The actor's own entry; the recorded revision, then its character; the voice profile the line was written
    with (an inherited base's, when the revision has none of its own), then its character. The revision always
    ranks above the profile it inherits, so a derived portrayal cast on its own speaks in its own voice."""
    rules: list[tuple[str, str, tuple[str, str]]] = []
    if actor is not None:
        rules.append(("actor", str(actor), ("actor", str(actor))))
    seen: set[str] = set()

    def add(rule: str, ref: SourceRef | None) -> None:
        if ref is not None and str(ref) not in seen:
            seen.add(str(ref))
            rules.append((rule, str(ref), ("character", str(ref))))

    if revision is not None:
        if revision.pinned:  # a post's character may be unpinned: then only its any-revision entry applies
            add("revision", revision)
        add("character_any_revision", revision.unpinned())
    if portrayal is not None:
        add("voice_profile", portrayal)
        add("voice_profile_any_revision", portrayal.unpinned())
    return rules


def _narrator_rules(campaign: str) -> list[tuple[str, str, tuple[str, str]]]:
    return [("narrator", f"the narrator of {campaign}", ("narrator", campaign)),
            ("narrator_every_campaign", "the narrator of every campaign", ("narrator", ""))]


def _cue(index: int, turn: TurnRecord, kind: str, item: str, speaker: str, actor: InstanceRef | None,
         revision: SourceRef | None, portrayal: SourceRef | None, text: str, action: bool,
         lookup: Mapping[tuple[str, str], CastEntry]) -> Cue:
    narrator = kind == "outcome" or (kind == "line" and actor is None)
    rules = _narrator_rules(turn.chat.campaign_id) if narrator else _character_rules(actor, revision, portrayal)
    chosen, considered = _choose(rules, lookup)
    script = "" if action else script_of(text, narrator=narrator)
    if action:
        status, reason = TEXT_ONLY, "action"
    elif not script:
        status, reason = TEXT_ONLY, "no_speech"
    elif chosen is None:
        status, reason = TEXT_ONLY, "not_cast"
    else:
        status, reason = VOICED, None
    return Cue(index, turn.turn_id, kind, item, speaker, actor, revision, portrayal, text, digest(text), script,
               status, reason, chosen, considered)


def _table_lines(turn: TurnRecord) -> tuple[SceneLine, ...]:
    """The kept take's lines the table saw (``table_lines(as_heard=True)``'s rule): on a staged take only where a
    player character of that turn heard the line; a staged take that recorded none gives none (fail closed)."""
    kept = turn.kept
    lines = turn.visible(None)
    if kept is None or kept.on_stage is None:
        return lines
    table = frozenset(kept.players or ())
    return tuple(line for line in lines if table.intersection(line.audience))


def plan_performance(turns: Iterable[TurnRecord], casting: Casting, names: Mapping[InstanceRef, str] | None = None,
                     sources: Mapping[InstanceRef, SourceRef] | None = None,
                     outcomes: Mapping[str, tuple[str, str]] | None = None) -> Performance:
    """The performance of ``turns`` (one chat's line) under ``casting``. ``names`` labels posts' speakers.
    ``sources`` gives the character an actor's campaign instance plays now: what voices a post, and a line that
    recorded no revision (a card chat's opening greeting), since neither records one of its own. ``outcomes`` is
    each decided action's outcome line the table read, by turn id (``(speaker, text)``), cued after the turn's
    posts and read by the narrator."""
    if type(casting) is not Casting:
        raise CastingError("invalid_casting", "plan_performance needs a Casting")
    names = names or {}
    sources = {actor: ref for actor, ref in (sources or {}).items()
               if type(ref) is SourceRef and ref.kind == "character"}  # only a character definition voices one
    outcomes = outcomes or {}
    lookup = casting.lookup()
    ordered = sorted(turns, key=lambda turn: turn.number)
    if not all(type(turn) is TurnRecord for turn in ordered):
        raise CastingError("invalid_turns", "plan_performance reads TurnRecord values")
    if len({turn.chat for turn in ordered}) > 1:
        raise CastingError("invalid_turns", "a performance is of one chat's turns")
    cues: list[Cue] = []
    for turn in ordered:
        for number, post in enumerate(turn.posts, 1):
            if post.private:
                continue
            speaker = names.get(post.actor) or post.actor.instance_id
            cues.append(_cue(len(cues), turn, "post", turn.post_id(number), speaker, post.actor,
                             sources.get(post.actor), None, post.text,
                             post.kind == "action" or post.channel is None, lookup))
        outcome = outcomes.get(turn.turn_id) if turn.post is not None and not turn.post.private else None
        if outcome is not None:
            label, said = outcome
            cues.append(_cue(len(cues), turn, "outcome", f"{turn.turn_id}.outcome", label or NARRATOR_LABEL, None,
                             None, None, said, False, lookup))
        for line in _table_lines(turn):
            speaker = NARRATOR_LABEL if line.actor is None else line.speaker
            revision, profile = line.actor_revision, line.voice_profile
            if line.actor is not None and revision is None and profile is None:
                revision = sources.get(line.actor)  # an opening greeting records none: the character played now
            cues.append(_cue(len(cues), turn, "line", line.line_id, speaker, line.actor, revision, profile,
                             line.text, False, lookup))
    by_voice: dict[str, list[str]] = {}
    for cue in cues:
        key = cue.voice_key
        if key is not None and cue.identity not in by_voice.setdefault(key, []):
            by_voice[key].append(cue.identity)
    shared = tuple(SharedVoice(key, tuple(speakers)) for key, speakers in sorted(by_voice.items())
                   if len(speakers) > 1)
    return Performance(tuple(cues), shared)


_RULE_WORDS = {
    "actor": "the campaign's own choice for",
    "revision": "the revision",
    "character_any_revision": "the character at any revision",
    "voice_profile": "the voice profile it was written with",
    "voice_profile_any_revision": "that profile's character at any revision",
    "narrator": "",
    "narrator_every_campaign": "",
}
_REASON_WORDS = {
    "not_cast": "nobody cast a voice for it, so it stays text",
    "action": "it is an action, shown and not spoken",
    "no_speech": "it holds only stage directions, so nothing is spoken",
}


def explain(cue: Cue) -> str:
    """One plain sentence on why ``cue`` sounds as it does, naming every rule looked at."""
    looked = []
    for item in cue.considered:
        words = _RULE_WORDS.get(item.rule, item.rule)
        where = f"{words} {item.names}".strip()
        looked.append(f"{where}: {item.entry.voice.key if item.entry else 'not cast'}")
    trail = "; ".join(looked) if looked else "no rule applies"
    if cue.status == VOICED and cue.chosen is not None:
        return f"{cue.speaker} speaks with {cue.chosen.voice.key}, cast for {cue.chosen.describe()} ({trail})."
    return f"{cue.speaker} is text only: {_REASON_WORDS[cue.reason or 'not_cast']} ({trail})."


__all__ = ["NARRATOR_LABEL", "REASONS", "TEXT_ONLY", "VOICED", "Considered", "Cue", "Performance", "SharedVoice",
           "digest", "explain", "plan_performance", "script_of"]
