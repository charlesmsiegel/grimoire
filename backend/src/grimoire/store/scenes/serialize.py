"""Transcript marshalling: the `**<Speaker>:**` marker grammar, both directions.

Parsing a stored body into messages, serializing messages back, and the
speaker labels the rest of the store reads (`ROLL_SPEAKER`,
`TRANSITION_SPEAKER`). `_numbering` lives here too — it reads scene *ids* off
disk, and `lifecycle.py` is its only caller.
"""

from __future__ import annotations

import re

from .. import scene_ids
from . import paths

# The body is a script: every message is `**<Speaker>:** content`. Role is not
# stored — a message is user-side iff its speaker is "You" or a role=player
# cast member's name (derived in read_scene). Reserved labels keep legacy
# files working; their parens sub-speaker form is read but never written.
RESERVED_LABELS = {"You": "user", "Grimoire": "assistant"}
ROLE_TO_LABEL = {"user": "You", "assistant": "Grimoire"}
# Manual dice rolls are appended as assistant-role messages (so they render
# like any other transcript line) but tagged with this speaker so reroll
# logic can tell them apart from an actual LLM reply — rerolling must never
# silently drop a roll line while its entry lives on in rolls.json. Prefixed
# with U+2063 (invisible separator, no visible glyph) so the marker can never
# collide with an ordinary typed speaker label or cast name — a real
# character or NPC named "Roll" round-trips as plain "Roll", not this.
ROLL_SPEAKER = "⁣Roll"
# Scene transitions (location change, time advance, cast join/leave) are
# appended as assistant-role messages so they render inline, but no model wrote
# them. Tagged with this speaker so drift measurement can treat them as turn
# SEPARATORS rather than counting them as model prose — untagged, a transition
# between two replies merges them into one apparently-oversized turn. Same
# U+2063 prefix as ROLL_SPEAKER, for the same anti-collision reason.
#
# It is INTERNAL METADATA and is never displayed: the app transcript, HTML and
# plain-text export and the EPUB all drop it, so a tagged transition renders as
# the unlabelled narration it was before tagging — identical to the untagged
# ones already sitting in every existing campaign.
TRANSITION_SPEAKER = "⁣Scene"
# Speakers that mark a message as not-model-output. Both are excluded from
# drift metrics and neither is ever consumed by reroll — a roll BLOCKS reroll
# (its transcript line must stay in lockstep with rolls.json), a trailing
# transition is stepped over and preserved.
# A director note: what the player typed to STEER a turn rather than to say in
# it. Stored so the turn it produced can be attributed to it -- `usage.record`'s
# `post` is a transcript index, and a note that is nowhere in the transcript has
# no index, which is why a director turn used to be the one generation in the
# app whose cost could not be shown beside the thing it bought.
#
# Same U+2063 prefix as the two above, and a SYNTHETIC SPEAKER for the same
# reason they are: it is not model output. The role follows from the format
# rather than from a preference -- `_speaker_and_role` derives the role from
# the label on read, and `You` is the only label that means user, so a marked
# message is assistant-role by construction. That turns out to be exactly
# right: everything that filters on `SYNTHETIC_SPEAKERS` should skip a note
# (reroll must not consume one, drift must not measure one, turn counting must
# not count one as a reply), and being in the tuple is what makes all of that
# true at once rather than seven times over.
#
# It is an INSTRUCTION, not story, and three rules follow from that:
#   * the model never sees it as history -- `context.story._project_history`
#     drops it, so a prompt is byte-identical to what it was before notes were
#     stored, and the note still reaches the model exactly once, as the final
#     user message `compose_director_turn` appends;
#   * no export and no absorb prompt contains it -- a book of the campaign is
#     what happened, not what the author asked for off-stage, and a chronicle
#     built from one would be summarising the reader's own instructions;
#   * the app hides it behind a per-scene toggle, because a transcript read
#     back later is prose and these are stage directions in the margin.
DIRECTOR_SPEAKER = "\u2063Note"


def is_director_note(m: dict) -> bool:
    """Whether this message is a stored director note rather than transcript.

    A function rather than a comparison at eight call sites: "the player typed
    this to steer rather than to say" is one idea, and the places that have to
    agree about it are in five different modules.
    """
    return m.get("speaker") == DIRECTOR_SPEAKER


# Speakers that mark a message as not-model-output. None is consumed by reroll,
# measured for drift or counted as a turn -- a roll BLOCKS reroll (its
# transcript line must stay in lockstep with rolls.json), a trailing transition
# is stepped over and preserved, and a director note is neither prose nor a
# reply so every one of those questions answers "not this".
SYNTHETIC_SPEAKERS = (ROLL_SPEAKER, TRANSITION_SPEAKER, DIRECTOR_SPEAKER)
# How a transition line reads for each of the scene's OWN moves. `moment.py`
# formats them and `write.delete_from` reads them back to work out how much of
# `location_history` / `time_history` a cut leaves standing (#75) — so the two
# live here, together, rather than as a literal in the writer and a guess in the
# reader. Actor join/leave lines carry this same speaker and are deliberately
# not here: they are `appearances`' business, not the scene's own setting.
LOCATION_MOVE = "*The scene moves to {name}.*"
TIME_ADVANCE = "*Time passes. It is now {friendly}.*"


def transition_kind(m: dict) -> str | None:
    """Which of the scene's own moves a transition line records — `"location"`,
    `"time"`, or None for anything else.

    Matched on the fixed head of each format above, since the tail is a name or
    a date. That is content sniffing, and the caller treats it as such: it only
    ACTS on the answer when the lines it finds account exactly for the history
    they would explain, so a scene written by an older build with different
    wording is left alone rather than mis-trimmed.
    """
    if m.get("speaker") != TRANSITION_SPEAKER:
        return None
    content = m.get("content", "")
    if not isinstance(content, str):
        return None
    if content.startswith(LOCATION_MOVE.split("{", 1)[0]):
        return "location"
    if content.startswith(TIME_ADVANCE.split("{", 1)[0]):
        return "time"
    return None
_MARKER = re.compile(r"^\*\*([^*\n]{1,64}?)(?: \(([^)\n]+)\))?:\*\*[ ]?", re.MULTILINE)
# `\Z`, not `$`: `$` also matches just before a trailing newline, so "Aese\n"
# satisfied this and `_label` emitted `**Aese\n:**` -- a marker split across two
# lines that `_MARKER` cannot match at all, folding the message into the
# previous speaker's content. `\r` is excluded for the same reason one step
# later: it survives the write, and universal-newline decoding turns it into
# "\n" on the way back in, breaking the marker only once it is already on disk.
_SAFE_LABEL = re.compile(r"^[^*\r\n]{1,64}\Z")


def speaker_base(speaker: str) -> str:
    """A speaker label without its sub-speaker parenthetical: "Mara (aside)" is
    Mara. Unchanged when there is no parenthetical.

    Split off GREEDILY, matching `_MARKER`'s backtracking: in "A (B) (C)" the
    LAST parenthetical is the sub, so the base is "A (B)". Shared with
    `label_preserved` below and with `absorb.routing`, whose citation check has
    to recognise a model that cites "Mara" for a line the transcript labelled
    "Mara (aside)" -- a second copy of this rule would let the two disagree
    about what a label's base is.
    """
    m = re.fullmatch(r"(.*) \(([^)\n]+)\)", speaker)
    return m.group(1) if m else speaker


def label_preserved(speaker: str | None) -> bool:
    """True when a message stored for `speaker` keeps that name as its
    transcript label, rather than falling back to the generic role label.

    The serializer silently substitutes the role label for a name it cannot
    write as a marker -- one holding `*` or a newline, longer than 64
    characters, or colliding with a reserved label. Anything that later reasons
    about a character BY their transcript label has to know that, or it ends up
    hunting for a name the transcript cannot contain (see the voice judge).

    A reserved label is rejected in its SUB-SPEAKER form too. `_MARKER` splits a
    trailing " (...)" off as the sub-speaker, and `_speaker_and_role` hands a
    reserved base's message to the sub -- so "Grimoire (Alice)" is stored and
    read back as plain "Alice", and "You (Bob)" comes back as "Bob" with the
    USER role, filing an NPC's dialogue under the player. The base is split off
    greedily, matching `_MARKER`'s backtracking: in "A (B) (C)" the LAST
    parenthetical is the sub."""
    if not speaker or not _SAFE_LABEL.match(speaker):
        return False
    return (speaker not in RESERVED_LABELS
            and speaker_base(speaker) not in RESERVED_LABELS)


def _label(role: str, speaker: str | None) -> str:
    return speaker if label_preserved(speaker) else ROLE_TO_LABEL[role]


def _markers(body: str) -> list[re.Match]:
    """Marker matches that actually start a message: at the top of the body or
    after a blank line (the serializer always writes blank lines between
    messages; this keeps bold-label lines inside a paragraph as content)."""
    return [m for m in _MARKER.finditer(body)
            if m.start() == 0 or body[max(0, m.start() - 2):m.start()] == "\n\n"]


def match_name(label: str, names) -> str | None:
    """The cast name `label` refers to, if unambiguous: exact match first
    (case-insensitive), else the single name the label is a word-boundary
    prefix of — "Winifred" names "Winifred Vance"; "Flo" names no one,
    and neither does "Winifred" with two Florences present."""
    low = label.strip().lower()
    if not low:
        return None
    exact = [n for n in names if n.lower() == low]
    if exact:
        return exact[0] if len(exact) == 1 else None
    # The boundary is checked in the LOWERCASED name, not the original. Casing
    # is not length-preserving -- "İ".lower() is two code points -- so indexing
    # the original by the normalized prefix's length lands past the boundary and
    # tests the wrong character. "İpek" then failed to name "İpek Yılmaz", which
    # reads as ambiguity and (via `confusable`) skips that character's voice
    # checks entirely, with no competing speaker anywhere.
    prefixed = []
    for n in names:
        nl = n.lower()
        if nl.startswith(low) and not nl[len(low)].isalnum():
            prefixed.append(n)
    return prefixed[0] if len(prefixed) == 1 else None


def _writable_label(name: str) -> bool:
    """Can `name` ever appear as a transcript label at all?

    Either the serializer writes it verbatim (`label_preserved`), or it IS one
    of the role labels it falls back to. A name that is neither never reaches
    the transcript -- its blocks are written as "Grimoire" or "You" -- so it
    owns no label of its own, and cannot make anyone else's ambiguous. Those
    two labels are seeded into the roster by every caller, so the fallback is
    still accounted for."""
    return name in RESERVED_LABELS or label_preserved(name)


def _labels(name: str) -> set[str]:
    """Every transcript label that could be written for `name`: the full name
    and each of its word-boundary prefixes. The model abbreviates -- it writes
    `**Winifred:**` for a character carded as "Winifred Vance" -- so a name owns
    more labels than itself.

    Only ever called on a name that passed `_writable_label`, which bounds it at
    64 characters. That bound is load-bearing, not incidental: this allocates a
    prefix per separator, so a card name of a few thousand alternating letters
    and spaces costs quadratic time and memory -- on the generation hot path,
    before anything has even checked whether the character has a drift flag."""
    return {label for label in
            {name} | {name[:i] for i in range(1, len(name)) if not name[i].isalnum()}
            if label.strip()}


def confusable(name: str, names) -> bool:
    """True when some transcript label that could mean `name` could also mean
    something else in `names`.

    Comparing whole names does NOT capture that. "Winifred Vance" and "Winifred
    Vale" are distinct strings, but a block labelled "Winifred" is a
    word-boundary prefix of both and belongs to neither in particular. So the
    question is asked of every label that could name this actor, and the actor
    is confusable unless all of them settle on it alone.

    Two directions, and BOTH are needed -- the second was missing, and no amount
    of care with the first would have covered it:

    - Every label that could name this actor must RESOLVE BACK to it. This is
      `match_name`, the same resolver that decides which cast member a written
      label refers to, so ambiguity is defined by the code that will actually do
      the reading rather than by a second opinion about it. It also rejects a
      name that is not in `names` at all, which is how a card name that differs
      from the actor's roster entry gets caught.

    - No label that resolves to this actor may be WRITABLE FOR SOMEONE ELSE.
      `match_name` breaks a tie by exact match, so with both "Mary" and "Mary
      Jane" on the roster the label "Mary" resolves to "Mary" -- cleanly, and
      wrongly, because the model writing `**Mary:**` may well have been
      shortening "Mary Jane". Direction one cannot see this: it only ever asks
      where a label lands, never who else could have written it.

    Deliberately conservative in both: an actor reachable by an ambiguous label
    is rejected even when its own full-name label would have been fine, because
    nothing downstream can tell which label a given block was written with. In
    the "Mary" / "Mary Jane" case that rejects both, which is correct -- "Mary"
    has no unambiguous label at all, and "Mary Jane" has one only if you assume
    the model never abbreviates.

    Used wherever a name has to identify exactly one actor: the voice judge
    (which is handed the transcript) and the voice corrective (which addresses
    the model by name).
    """
    if not isinstance(name, str) or not label_preserved(name):
        # A name the transcript cannot hold VERBATIM is not an identity: this
        # actor's blocks come out under a role label, so nothing downstream can
        # address them by name. Note the asymmetry with the bystander test
        # below, which is `_writable_label` -- a character carded "You" fails
        # here (their lines are written as "Grimoire"), while the reserved label
        # "You" passes there (it is exactly what the transcript writes for the
        # player). Target and bystander are different questions.
        #
        # Checked BEFORE any expansion, so an oversized name is rejected rather
        # than expanded (see `_labels`).
        return True
    mine = _labels(name)
    if any(match_name(label, names) != name for label in mine):
        return True
    lowered = {label.strip().lower() for label in mine}
    # `other != name` skips this actor's own entry. A DUPLICATE of it in `names`
    # is skipped here too, and deliberately: direction one already rejected that
    # (two exact matches make `match_name` return None), so letting it fall
    # through here would only re-derive the same answer.
    return any(lowered & {label.strip().lower() for label in _labels(other)}
               for other in names
               if isinstance(other, str) and other != name and _writable_label(other))


def _speaker_and_role(m: re.Match, players: frozenset[str]) -> tuple[str | None, str]:
    base, sub = m.group(1), m.group(2)
    if base in RESERVED_LABELS:
        return sub, RESERVED_LABELS[base]
    speaker = f"{base} ({sub})" if sub else base
    return speaker, "user" if match_name(speaker, players) else "assistant"


def _numbering(cid: str) -> tuple[int, int]:
    """(next number, current pad width) from the files on disk — no stored
    counter. Width starts at MIN_WIDTH and follows the widest number present;
    legacy (unmigrated) stems don't parse and are ignored."""
    top, width = 0, scene_ids.MIN_WIDTH
    d = paths._scenes_dir(cid)
    if d.exists():
        for p in d.glob("*.md"):
            parsed = scene_ids.parse_sid(p.stem)
            if parsed:
                top = max(top, parsed["number"])
                width = max(width, parsed["width"])
    return top + 1, width


def _parse_messages(body: str, players: frozenset[str]) -> list[dict]:
    matches = _markers(body)
    messages = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        speaker, role = _speaker_and_role(m, players)
        msg = {"role": role, "content": body[start:end].strip()}
        if speaker:
            msg["speaker"] = speaker
        messages.append(msg)
    return messages


def _block(role: str, speaker: str | None, content: str) -> str:
    return f"**{_label(role, speaker)}:** {content.strip()}\n"


def _append_block(body: str, block: str) -> str:
    return (body.rstrip() + "\n\n" + block) if body.strip() else block


def _serialize_messages(messages: list[dict]) -> str:
    body = ""
    for m in messages:
        body = _append_block(body, _block(m["role"], m.get("speaker"), m["content"]))
    return body
