"""Who leads this turn (#29's group active-speaker layer).

With several NPCs cast, every card is concatenated into Character descriptions
and the model picks a speaker implicitly. In a two-hander that is fine; with
four in a room it produces the failure every group scene has — one character
monologues for three turns while the other three stand silently, or all four
answer the same question in sequence.

`cast.py` already looks at speakers, but only to MEASURE: `_drift_roster`
canonicalizes labels so `length_drift` can count them, and `_voice_notes`
carries correctives about how a character sounds. Neither decides who talks.

Derived, never stored
---------------------
A rotation counter would be a second source of truth about who has spoken, free
to disagree with the transcript the moment a post is undone, a turn is
regenerated, or the scene file is hand-edited — and `store/scenes` serializes
its whole mutator surface precisely to keep the transcript authoritative.
Deriving costs one pass over history already in memory, cannot drift, and makes
regenerate reproduce the same nomination rather than advancing a rotation the
reader never saw.

Pure: no I/O and no store reads. The caller passes the present NPCs' CARD names
— what the model is holding and what the transcript stamps — and the history.
"""

from __future__ import annotations

import re

from ..scenes import serialize as scenes_serialize


def _first_token(name: str) -> str:
    parts = name.split()
    return parts[0] if parts else ""


def _address_labels(names: list[str]) -> dict[str, str]:
    """label -> the one present NPC it can mean.

    A label two present actors answer to is absent from the result, so it
    addresses nobody. Full names and first names share one namespace, which is
    what makes a first name that is also another actor's whole name ambiguous
    rather than a match.

    The same rule `_voice_notes` applies, for the same reason: an instruction
    pointed at the wrong character is worse than no instruction at all.
    """
    owners: dict[str, list[str]] = {}
    for name in names:
        for label in {name, _first_token(name)}:
            if label:
                owners.setdefault(label.casefold(), []).append(name)
    return {label: who[0] for label, who in owners.items() if len(who) == 1}


def _named(text: str, names: list[str]) -> str | None:
    """The one present NPC this text names — None for nobody, and None for more
    than one, since a post naming two of them has not singled either out.

    Whole-word only: "the maraud was loud" does not summon Mara.

    NAMED, not addressed, and the distinction is real: "I remember what Mara
    said" mentions her without asking her anything, and this counts it. That is
    the deliberate trade — the precise reading needs the model, and a whole
    extra call per turn is the cost this layer exists to avoid. It is a
    nudge about who speaks, not a routing decision, and the section it feeds
    words it as such.
    """
    hits = {who for label, who in _address_labels(names).items()
            if re.search(rf"(?<!\w){re.escape(label)}(?!\w)", text, re.IGNORECASE)}
    return hits.pop() if len(hits) == 1 else None


def nominate(npc_names: list[str], history: list[dict],
             pending: str = "") -> dict | None:
    """Who should lead this turn, or None for "say nothing".

    `pending` is this turn's input when it is not in `history` yet — a director
    note, or an opener's prompt. Both are the turn's actual text and neither is
    ever persisted, which is exactly why `_assemble` already feeds them to
    world-info activation as `wi_seed`; naming a character in a director note
    and having the nomination ignore it would be the same bug that seam
    exists to prevent. It outranks the last stored post, being newer.

    Returns ``{"lead", "reason", "spoken", "silent_for", "quiet"}``:

    - ``lead`` — the NPC to carry the turn.
    - ``reason`` — ``"named"`` (the latest input names them) or
      ``"rotation"`` (they are the most overdue).
    - ``spoken`` / ``silent_for`` — whether the lead has spoken in this scene at
      all, and how many model blocks ago, so the section can say *why*.
    - ``quiet`` — everyone else present, most-overdue first.

    None below two present NPCs. Turn-taking is a group problem, and naming a
    lead in a two-hander is tokens spent telling the model what the cast list
    already said.
    """
    names = list(dict.fromkeys(n.strip() for n in npc_names
                               if isinstance(n, str) and n.strip()))
    if len(names) < 2:
        return None

    # Model blocks only. A player's post is not a turn anyone took, an
    # unstamped block belongs to no one in particular, and the synthetic
    # speakers are not characters -- counting any of them would move a real
    # character's silence.
    blocks = [m for m in history
              if m.get("role") == "assistant"
              and isinstance(m.get("speaker"), str)
              and m.get("speaker") not in scenes_serialize.SYNTHETIC_SPEAKERS]
    #: How far back each NPC's last block was, and how many they have taken.
    #: A label the transcript stamped short ("Winifred") canonicalizes to the
    #: cast name exactly as drift measurement does -- counting it as a stranger
    #: would nominate a talkative character as never having spoken. A speaker
    #: who is not in the present cast (a character who has since left, whose
    #: blocks are still in the transcript) is skipped rather than counted.
    last: dict[str, int] = {}
    said: dict[str, int] = {}
    for pos, m in enumerate(blocks):
        who = scenes_serialize.match_name(m["speaker"], names) or m["speaker"]
        if who not in names:
            continue
        last[who] = len(blocks) - 1 - pos
        said[who] = said.get(who, 0) + 1

    def silence(name: str) -> int:
        # Never spoken sorts ahead of everyone who has: one past the longest
        # silence the transcript could hold.
        return last[name] + 1 if name in last else len(blocks) + 1

    # Longest silence first, then whoever has said least, then cast order --
    # so a given transcript always nominates the same way.
    ranked = sorted(names, key=lambda n: (-silence(n), said.get(n, 0), names.index(n)))
    lead, reason = ranked[0], "rotation"

    # Being named outranks having been quiet: the input singled someone out,
    # and answering somebody else is the more visible failure.
    last_post = next((m for m in reversed(history) if m.get("role") == "user"), None)
    latest = pending if pending.strip() else (
        last_post["content"] if last_post and isinstance(last_post.get("content"), str)
        else "")
    if latest:
        named = _named(latest, names)
        if named:
            lead, reason = named, "named"

    return {"lead": lead, "reason": reason,
            "spoken": lead in last, "silent_for": last.get(lead, 0),
            "quiet": [n for n in ranked if n != lead]}
