"""Heuristic cast-change detection from the prose of the newest turn (#97, #98).

Three buckets, all *candidates* and never applied writes:

``enter``
    A campaign character the transcript just named who is not in this scene's
    cast. Confirming one is an ordinary ``POST .../cast`` (``transitions.appear``).
``leave``
    A cast member the prose just walked off stage. Confirming one is
    ``DELETE .../cast/{kind}/{id}`` (``transitions.leave``).
``unknown``
    A name the prose used that matches no record at all -- the emergent-character
    route's input (#98).

**Nothing here writes, and nothing may auto-apply.** Both transitions are
destructive in their own direction: a first appearance *locks* a version
(``versions._lock`` purges the actor's other versions campaign-side), and a
departure appends a narration line to a transcript that cannot be regenerated.
So the detector's job ends at "here is a name and where it came from"; the
player's confirm is what turns one into a transition.

The scan is a heuristic over one turn's text, deliberately, and it errs toward
offering: a wrong chip costs one dismissal, whereas a miss is invisible. That
trade is only sound *because* confirmation is mandatory -- read the two
paragraphs above before making anything here automatic.

It is also, unavoidably, an ENGLISH heuristic: the departure cues are English
verbs and the novel-name rule assumes a capitalised-name convention. A campaign
played in another language gets an empty ``leave`` bucket and a poor
``unknown`` one, and loses nothing else -- ``enter`` matches recorded names, so
it works in any language. Cast changes stay available by hand there, which is
the state every campaign was in before this module.

Cost: one scene read plus the campaign's character, PC and entity listings, per
call -- which the client makes once per scene read (a landed turn, a scene
switch). The listings are the same sweep ``context.world_state`` already runs
every turn, so this re-reads data the turn loop is reading anyway rather than
adding a new class of work.
"""

from __future__ import annotations

import re

from .. import overlay
from ..entities import ENTITY_KINDS
from ..paths import slugify
# Only the read/serialize leaves, never the `scenes` facade -- `scenes/read.py`
# imports this package's `cast.py`, so binding whole packages in both directions
# would close a cycle these file-level edges do not (same cut `transitions.py`
# makes).
from ..scenes import (read as scenes_read, serialize as scenes_serialize,
                      turns as scenes_turns)
from . import cast

#: How many unknown names one turn may offer. A turn that mentions a dozen
#: capitalized things is a turn the heuristic misread, and a wall of chips is
#: worse than a short list -- but the cap is silent about what it dropped, so it
#: is stated here rather than buried in a slice.
MAX_UNKNOWN = 6

# Departure cues, matched against one sentence at a time. Every entry is a verb
# phrase whose subject leaves; nouns that merely mention departure ("the leaving
# tide") are not cues.
#
# `left` carries the negative lookahead because it is the one cue with a common
# non-departure sense -- "her left hand", "the left of the door" -- and that
# sense is always followed by the thing being described.
_DEPARTURE = re.compile(r"""
    \b(?:
        leaves | leave
      | departs? | departed
      | exits | exited
      | withdraws | withdrew
      | vanishes | vanished
      | disappears | disappeared
      | (?: slips | slipped | steps | stepped | walks | walked | strides | strode
          | storms | stormed | heads | headed | hurries | hurried | ducks | ducked
          | rides | rode | runs | ran | drifts | drifted )
        \s+ (?: out | off | away )
      | (?: takes | took ) \s+ (?: his | her | their | its ) \s+ leave
      | (?: excuses | excused ) \s+ (?: himself | herself | themselves | themself )
      | (?: is | are ) \s+ gone
      | left (?! \s+ (?: hand | arm | side | eye | eyes | shoulder | leg | foot
                       | ear | cheek | hip | boot | glove | wrist | knee | pocket
                       | sleeve | flank | of ) \b )
    )\b
""", re.IGNORECASE | re.VERBOSE)

# A run of capitalized words: "Winifred", "Mara Vance", "Lady Winifred".
_CAP_RUN = re.compile(r"\b[A-Z][a-z]{2,}(?:['’‐-][A-Za-z][a-z]+)*"
                      r"(?:\s+[A-Z][a-z]{2,}(?:['’‐-][A-Za-z][a-z]+)*){0,2}\b")

#: Titles the prose puts in front of a name. Stripped from the front of a
#: candidate so "Lady Winifred" offers to create *Winifred*, and a bare title
#: with nothing behind it is not a name at all.
_HONORIFICS = frozenset({
    "captain", "commander", "count", "countess", "dame", "doctor", "duchess",
    "duke", "father", "general", "lady", "lord", "madam", "madame", "master",
    "miss", "mister", "mistress", "mother", "sergeant", "sir", "sister",
})

#: Capitalized words that turn up mid-sentence without naming anyone. Days,
#: months and the handful of interjections and titles-of-address that survive
#: the sentence-initial filter below.
_NOT_NAMES = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "god", "gods", "goddess", "lord", "lords", "heaven", "hell",
    "yes", "no", "oh", "ah", "well", "please", "thank", "thanks", "sorry",
    "and", "but", "the", "then", "there", "this", "that", "you", "your",
    "she", "her", "hers", "him", "his", "they", "them", "their", "these", "those",
    "what", "when", "where", "who", "why", "how", "not", "now", "still", "just",
})

#: Words that turn `leave(s)`/`left` into a noun or an adjective -- "the leaves
#: have fallen", "the dead leaves" ("her left hand" is caught by the lookahead
#: in `_DEPARTURE` instead). A determiner or possessive immediately in front of
#: the cue means it is not the verb this is looking for.
_DETERMINER_BEFORE = frozenset({
    "the", "a", "an", "his", "her", "their", "its", "my", "our", "your",
    "these", "those", "some", "no", "dead", "fallen", "autumn", "dry", "wet",
})
_TRAILING_WORD = re.compile(r"([A-Za-z']+)[^A-Za-z']*$")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])[\s\"”’*]+|\n+")
# What can sit between the end of a sentence and the first word of the next one
# without the word ceasing to be sentence-initial: quotes, markdown emphasis,
# whitespace.
_LEADING = " \t\"'*_“”‘’(["


def cast_changes(cid: str, scene_id: str) -> dict:
    """Cast-change candidates read out of the newest turn's prose.

    ``{"enter": [{kind, id, name, mentioned_by}],
       "leave": [{kind, id, name, quote}],
       "unknown": [{name, mentioned_by}]}``

    ``mentioned_by`` names the *speakers* whose posts carried the name, unlike
    ``transitions.suggestions``' character ids -- what is being cited here is a
    line of the transcript, and a line is attributable to a speaker label.
    """
    messages = _turn_messages(cid, scene_id, scenes_read.read_scene(cid, scene_id)["messages"])
    if not messages:
        # Before the listings below: a scene with nothing to read (no posts yet,
        # or a turn that ended on a dice roll) should not sweep every entity
        # kind to say so.
        return {"enter": [], "leave": [], "unknown": []}
    dismissed = set(scenes_read.get_dismissed(cid, scene_id))
    in_scene = cast.scene_cast(cid, scene_id)
    characters = overlay.list_characters(cid)

    seated = {(a["kind"], a["id"]) for a in in_scene}
    enter = []
    for c in characters:
        if ("characters", c["id"]) in seated or c["id"] in dismissed:
            continue
        by = _mentioned_by(messages, c["name"])
        if by:
            enter.append({"kind": "characters", "id": c["id"], "name": c["name"],
                          "mentioned_by": by})
    return {"enter": enter,
            "leave": _departures(messages, in_scene),
            "unknown": _unknown_names(messages, characters, cid, dismissed)}


def _turn_messages(cid: str, scene_id: str, messages: list[dict]) -> list[dict]:
    """The newest turn: this generation's posts, plus the player post it answers.

    The player's post, not just the reply, because a turn is one exchange -- a
    name the player introduced ("I look for Winifred") is as much this turn's
    news as one the model wrote.

    The generation's extent comes from ``turn_sizes``, the same boundary record
    reroll counts back through, rather than from "everything after the last
    player post". Those agree in a scene with a player in it and disagree
    completely in an OFFSCREEN one, which is all-assistant by construction: the
    player-post rule finds no boundary at all there and would fall back to a
    single message, so a turn that wrote one post per NPC would be read for
    cast changes through its last post only.

    Where this deliberately parts company with reroll is the DESYNCED case. A
    ``turn_sizes`` list that no longer fits the transcript makes reroll refuse
    outright (``TurnSizesDesynced``), because counting back through the wrong
    blocks deletes transcript nobody asked to lose. Nothing here deletes
    anything, so the same list falling out of step costs at most a window one
    turn too wide -- and refusing to suggest would be the worse answer. It falls
    back to the trailing model run, which is what reroll uses for a scene that
    was never tracked at all.

    Synthetic speakers are dropped, which is load-bearing rather than tidy:
    ``*Seraphine leaves the scene.*`` is this feature's own output, so reading
    it back would suggest re-seating the actor the player just dismissed off
    stage, forever.
    """
    # Trailing transitions sit ON TOP of the generation, exactly as they do for
    # reroll -- strip them before counting back (`alternates.reroll_target`).
    core = messages[:len(messages) - scenes_read.trailing_transitions(messages)]
    start = len(core) - _newest_generation(cid, scene_id, core)
    last_post = max((i for i, m in enumerate(core) if m.get("role") == "user"), default=None)
    # The post this generation answers sits immediately in front of it. An
    # OLDER player post does not: several model-only turns can follow one (a
    # multi-NPC scene continued without input), and reaching back to it would
    # re-offer every name since.
    if last_post is not None and last_post == start - 1:
        start = last_post
    return [m for m in core[start:]
            if m.get("speaker") not in scenes_serialize.SYNTHETIC_SPEAKERS
            and isinstance(m.get("content"), str)]


def _newest_generation(cid: str, scene_id: str, core: list[dict]) -> int:
    """How many of `core`'s trailing posts the newest generation wrote.

    `_tracked_suffix_fits` is what makes the count safe to use as a message
    index: it holds only when the last recorded generation sits contiguously at
    the tail, so the final `sizes[-1]` entries of `core` are model blocks and
    nothing else."""
    sizes = scenes_turns.get_turn_sizes(cid, scene_id)
    if sizes and scenes_turns._tracked_suffix_fits(core, sizes):
        return sizes[-1]
    return scenes_turns._trailing_model_run(core)


def _speaker(m: dict) -> str:
    """How the transcript labels this post: its own speaker, or -- for a post
    with none -- the role label the serializer writes in its place."""
    return m.get("speaker") or scenes_serialize.ROLE_TO_LABEL.get(m.get("role", ""), "")


def _mentioned_by(messages: list[dict], name: str) -> list[str]:
    """Speakers whose post names `name` as a whole word, case-insensitively."""
    if not name.strip():
        return []
    pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
    return sorted({_speaker(m) for m in messages if pattern.search(m["content"])})


def _departures(messages: list[dict], in_scene: list[dict]) -> list[dict]:
    """Cast members a departure cue just walked off stage, with the sentence.

    Only the NPC half of the cast. A player actor is never proposed for
    removal: which messages parse as player-side is derived from the scene's
    player names (``scenes.serialize._parse_messages``), so dropping one
    re-roles the transcript's own history -- far too much to hang on a verb.
    """
    npcs = [a for a in in_scene if a["role"] != "player"]
    if not npcs:
        return []
    seen, out = set(), []
    for m in messages:
        for sentence in _SENTENCE_SPLIT.split(m["content"]):
            cue = _credible_cue(sentence)
            if not cue:
                continue
            actor = _nearest_before(sentence[:cue.start()], npcs)
            # Nearest name BEFORE the cue, so "Mara watched as Seraphine slipped
            # out" reports Seraphine. A cue with no name in front of it is left
            # alone rather than guessed at from the rest of the sentence.
            if actor is None or (actor["kind"], actor["id"]) in seen:
                continue
            seen.add((actor["kind"], actor["id"]))
            out.append({"kind": actor["kind"], "id": actor["id"], "name": actor["name"],
                        "quote": sentence.strip()})
    return out


def _credible_cue(sentence: str) -> re.Match | None:
    """The first departure cue in `sentence` that is being used as a verb.

    `leave`/`leaves`/`left` are also nouns and adjectives, and a determiner or
    possessive in front of one settles it: "the leaves have fallen" is weather,
    not an exit. What this cannot tell apart is a transitive use with an object
    -- "Seraphine leaves the letter on the table" still reads as a departure.
    That is the trade the whole module makes: the chip quotes the sentence it
    read, and answering it costs one click.
    """
    for cue in _DEPARTURE.finditer(sentence):
        if not cue.group(0).lower().startswith(("leave", "left")):
            return cue
        before = _TRAILING_WORD.search(sentence[:cue.start()])
        if before is None or before.group(1).lower() not in _DETERMINER_BEFORE:
            return cue
    return None


def _nearest_before(text: str, actors: list[dict]) -> dict | None:
    """The actor whose name sits closest to the END of `text` -- i.e. the one
    the cue that follows most plausibly belongs to. None if no name is there."""
    best, best_at = None, -1
    for a in actors:
        if not a["name"].strip():
            continue
        matches = list(re.finditer(rf"\b{re.escape(a['name'])}\b", text, re.IGNORECASE))
        if matches and matches[-1].start() > best_at:
            best, best_at = a, matches[-1].start()
    return best


def _unknown_names(messages: list[dict], characters: list[dict], cid: str,
                   dismissed: set[str]) -> list[dict]:
    """Capitalized names in the turn that match no record this campaign has.

    Two filters do the work. Every token of every name the campaign already
    knows -- characters, PCs, and all five entity kinds -- is excluded, so
    "Lady Seraphine" and "the Saltmarch road" are not novel names. And a
    candidate has to appear at least once *mid-sentence*: a word that is only
    ever the first word of a sentence is far more often "Meanwhile" than a
    person, and the sentence-initial position cannot tell the two apart.
    """
    known = _known_words(characters, cid)
    hits: dict[str, tuple[str, set[str]]] = {}
    for m in messages:
        for match in _CAP_RUN.finditer(m["content"]):
            name = _strip_titles(match.group(0))
            if not name or not _is_novel(name, known) or slugify(name) in dismissed:
                continue
            _, speakers = hits.setdefault(name.casefold(), (name, set()))
            if not _sentence_initial(m["content"], match.start()):
                speakers.add(_speaker(m))
    return [{"name": name, "mentioned_by": sorted(speakers)}
            for name, speakers in sorted(hits.values(), key=lambda h: h[0])
            if speakers][:MAX_UNKNOWN]


def _known_words(characters: list[dict], cid: str) -> set[str]:
    """Every word of every name the campaign can already resolve, casefolded."""
    names = [c["name"] for c in characters]
    names += [p["name"] for p in overlay.list_pcs(cid)]
    for kind in ENTITY_KINDS:
        names += [e["name"] for e in overlay.list_entities(cid, kind)]
    return {w for name in names for w in re.findall(r"[A-Za-z']+", name.casefold())}


def _strip_titles(run: str) -> str:
    words = run.split()
    while words and words[0].casefold() in _HONORIFICS:
        words.pop(0)
    return " ".join(words)


def _is_novel(name: str, known: set[str]) -> bool:
    words = name.casefold().split()
    return not any(w in known or w in _NOT_NAMES for w in words)


def _sentence_initial(text: str, at: int) -> bool:
    i = at - 1
    while i >= 0 and text[i] in _LEADING:
        i -= 1
    return i < 0 or text[i] in ".!?…\n:;—-"
