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
SYNTHETIC_SPEAKERS = (ROLL_SPEAKER, TRANSITION_SPEAKER)
_MARKER = re.compile(r"^\*\*([^*\n]{1,64}?)(?: \(([^)\n]+)\))?:\*\*[ ]?", re.MULTILINE)
# `\Z`, not `$`: `$` also matches just before a trailing newline, so "Aese\n"
# satisfied this and `_label` emitted `**Aese\n:**` -- a marker split across two
# lines that `_MARKER` cannot match at all, folding the message into the
# previous speaker's content. `\r` is excluded for the same reason one step
# later: it survives the write, and universal-newline decoding turns it into
# "\n" on the way back in, breaking the marker only once it is already on disk.
_SAFE_LABEL = re.compile(r"^[^*\r\n]{1,64}\Z")


def label_preserved(speaker: str | None) -> bool:
    """True when a message stored for `speaker` keeps that name as its
    transcript label, rather than falling back to the generic role label.

    The serializer silently substitutes the role label for a name it cannot
    write as a marker -- one holding `*` or a newline, longer than 64
    characters, or colliding with a reserved label. Anything that later reasons
    about a character BY their transcript label has to know that, or it ends up
    hunting for a name the transcript cannot contain (see the voice judge)."""
    return bool(speaker) and bool(_SAFE_LABEL.match(speaker)) \
        and speaker not in RESERVED_LABELS


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
    prefixed = [n for n in names
                if n.lower().startswith(low) and not n[len(low)].isalnum()]
    return prefixed[0] if len(prefixed) == 1 else None


def confusable(name: str, names) -> bool:
    """True when some transcript label that could mean `name` could also mean
    something else in `names`.

    `match_name` is the resolver that decides which cast member a written label
    refers to, so it also defines when a label is ambiguous -- and comparing
    whole names does NOT capture that. "Winifred Vance" and "Winifred Vale" are
    distinct strings, but a block labelled "Winifred" is a word-boundary prefix
    of both and belongs to neither in particular.

    So the test is applied to every label that could name this actor -- the full
    name and each of its word-boundary prefixes -- and the actor is confusable
    unless all of them resolve back to it. Deliberately conservative: a name
    reachable by an ambiguous label is rejected even when its own full-name
    label would have been fine, because nothing downstream can tell which label
    a given block was written with.

    Used wherever a name has to identify exactly one actor: the voice judge
    (which is handed the transcript) and the voice corrective (which addresses
    the model by name).
    """
    if not isinstance(name, str) or not name.strip():
        return True
    labels = {name} | {name[:i] for i in range(1, len(name)) if not name[i].isalnum()}
    return any(match_name(label, names) != name for label in labels if label.strip())


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
