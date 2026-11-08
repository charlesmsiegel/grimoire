"""What each listener heard, what the table saw, and the segments a mid-session request names.

Only kept takes are heard: a proposal is heard by no one, so a reroll never hears the take it replaces.
A line's ``audience`` was fixed when it was spoken, so these are filters, never inferences: an actor
hears a post or a line only if it was in that audience. Narration's audience is the table's player
characters, so an NPC never hears it (031: ``scene_text`` holds no director narration). A private
message (a whisper) is in the table transcript of no one but its audience, and never the director's.

A chat's **imported history** (036/026's transcript pages) comes before its first played turn. Its pages
record who wrote each post, never who heard it, so who heard it is decided here, once, by one rule:
the actors the chat was imported with (its roster at sequence 0) heard every shown post; an actor
attached later heard none of it, exactly as a card attached mid-chat hears nothing said before it came.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from grimoire.core.boundaries import NarrationSegment
from grimoire.core.identity import InstanceRef
from grimoire.generation import MAX_HEARD, MAX_LINE_CHARS, GenerationError, HeardLine
from grimoire.transcripts import Post as ImportedPost
from grimoire.transcripts import TranscriptPage

from .model import Post, TurnRecord

NARRATOR = "Narrator"


def _clip(text: str) -> str:
    return text if len(text) <= MAX_LINE_CHARS else text[:MAX_LINE_CHARS - 1] + "…"


def _speaker(actor: InstanceRef, names: Mapping[InstanceRef, str]) -> str:
    return _clip(names.get(actor) or actor.instance_id)


def _heard(speaker: str, text: str, actor: InstanceRef | None) -> HeardLine | None:
    """A heard line, or ``None`` for one that says nothing a listener could hear (027 needs words)."""
    try:
        return HeardLine(_clip(speaker), _clip(text), actor)
    except GenerationError:
        return None


def _post_line(post: Post, names: Mapping[InstanceRef, str]) -> HeardLine | None:
    text = post.text if post.channel is not None else f"*{post.text.strip('*').strip() or post.text}*"
    return _heard(_speaker(post.actor, names), text, post.actor)


def audible(post: Post) -> bool:
    """Whether a listener would hear words in this post, as clipped and shown (027 needs words)."""
    return _post_line(post, {}) is not None


def _ordered(turns: Iterable[TurnRecord]) -> list[TurnRecord]:
    return sorted(turns, key=lambda turn: turn.number)


@dataclass(frozen=True)
class ImportedHistory:
    """A chat's imported posts as lines: what its imported cast heard, and what the table saw.

    ``listeners`` are the actors the chat was imported with. ``heard`` is what each of them heard: every
    shown post written by the player, a character, or a source's generated reply that names its speaker.
    ``table`` is the director's view: those posts and the narrator's. A narrator post is never heard by an
    NPC (as in play); V0.2.1 writes its narrator (the reserved ``**Grimoire:**`` speaker) as an
    ``assistant`` post with no label, so such a post is narration too. A source's own ``system`` notice
    (a V0.2.1 synthetic post, a dice roll), a ``hidden`` post, an unchosen variant and a post with no
    chosen text are in neither. Each keeps only its last ``MAX_HEARD`` lines, the most any window holds.
    ``posts`` counts the posts the pages hold.
    """

    listeners: frozenset[InstanceRef]
    heard: tuple[HeardLine, ...]
    table: tuple[HeardLine, ...]
    posts: int


#: who an imported post's role says wrote it, and whether an NPC of the imported cast heard it
_HEARD_ROLES = frozenset({"user", "character", "assistant"})
_TABLE_ROLES = _HEARD_ROLES | {"narrator"}
PLAYER = "Player"


def _narrated(post: ImportedPost) -> bool:
    """Whether a post is narration: the source says so, or it is V0.2.1's unlabelled narrator."""
    return post.speaker.role == "narrator" or (post.speaker.role == "assistant" and post.speaker.actor is None
                                               and not (post.speaker.label or "").strip())


def _imported_line(post: ImportedPost, names: Mapping[InstanceRef, str], player: str | None,
                   ) -> tuple[HeardLine, bool] | None:
    """A post's line and whether the imported cast heard it (``False``: the table's only)."""
    if post.hidden or post.active is None or post.speaker.role not in _TABLE_ROLES:
        return None
    text = post.variants[post.active].text
    if _narrated(post):
        line = _heard(NARRATOR, text, None)
        return None if line is None else (line, False)
    actor = post.speaker.actor
    fallback = (player or PLAYER) if post.speaker.role == "user" else post.speaker.role.capitalize()
    # a label is display text, kept as the source wrote it; it never names an actor. One with no words
    # (``???``, an emoji) cannot be a heard speaker, so the post falls back rather than vanish.
    for speaker in (names.get(actor) if actor is not None else None, (post.speaker.label or "").strip(), fallback):
        line = _heard(speaker, text, actor) if speaker else None
        if line is not None:
            return line, True
    return None


def imported_history(pages: Iterable[TranscriptPage], listeners: Iterable[InstanceRef],
                     names: Mapping[InstanceRef, str], *, player: str | None = None,
                     limit: int = MAX_HEARD) -> ImportedHistory:
    """The history ``pages`` hold, in page and post order, heard by ``listeners`` (the imported cast).

    ``player`` names the player's posts that carry neither an actor nor a label (V0.2.1's ``**You:**``).
    Posts are read newest first and only until both views hold ``limit`` lines: no window holds more, and a
    long SillyTavern chat need not become lines it never shows.
    """
    heard: list[HeardLine] = []
    table: list[HeardLine] = []
    ordered = sorted(pages, key=lambda found: found.ref.seq)
    count = sum(len(page.posts) for page in ordered)
    for page in reversed(ordered):
        for post in reversed(page.posts):
            if len(table) >= limit and len(heard) >= limit:
                break
            found = _imported_line(post, names, player)
            if found is None:
                continue
            line, cast_heard = found
            if len(table) < limit:
                table.append(line)
            if cast_heard and len(heard) < limit:
                heard.append(line)
    return ImportedHistory(frozenset(listeners), tuple(reversed(heard)), tuple(reversed(table)), count)


def imported_table(pages: Iterable[TranscriptPage], names: Mapping[InstanceRef, str], *,
                   player: str | None = None) -> tuple[tuple[str, str], ...]:
    """Every post of ``pages`` the table saw, oldest first, as ``(speaker, text)`` with nothing clipped: the
    posts ``imported_history``'s ``table`` holds, with no window, for a reader that shows the whole imported
    chat (044). Which posts and which speaker are ``imported_history``'s rule; only the text is the post's whole
    chosen text rather than a heard line's 2,000 characters."""
    found = []
    for page in sorted(pages, key=lambda each: each.ref.seq):
        for post in page.posts:
            line = _imported_line(post, names, player)
            if line is not None:
                found.append((line[0].speaker, post.variants[post.active].text))
    return tuple(found)


def heard_for(turns: Iterable[TurnRecord], actor: InstanceRef, names: Mapping[InstanceRef, str], *,
              limit: int = MAX_HEARD, before: ImportedHistory | None = None,
              narration: bool = True, outcomes: Mapping[str, HeardLine] | None = None) -> tuple[HeardLine, ...]:
    """What ``actor`` heard, oldest first: posts in its audience and kept lines it heard (the last ``limit``).

    ``before`` is the chat's imported history: an actor it was imported with heard it before turn 1. It holds
    only its own last ``MAX_HEARD`` lines, so a ``limit`` above that gets no more imported lines.

    A player character is in narration's audience because its player sits at the table, not because the
    character perceived it. ``narration=False`` is the character's own window: only spoken lines and posts,
    with no narrator line (imported narration is never in ``before.heard``).

    ``outcomes`` maps a turn id to its decided action's outcome line, heard right after the action's post by
    whoever heard the post (the caller decides what the line says to this listener).
    """
    heard: list[HeardLine | None] = []
    if before is not None and actor in before.listeners:
        heard.extend(before.heard)
    for turn in _ordered(turns):
        if turn.post is not None and actor in turn.post.audience:
            heard.append(_post_line(turn.post, names))
            heard.append((outcomes or {}).get(turn.turn_id))
        heard.extend(_heard(line.speaker, line.text, line.actor) for line in turn.visible(actor)
                     if narration or line.actor is not None)
    found = [line for line in heard if line is not None]
    return tuple(found[-limit:]) if limit else ()


def table_lines(turns: Iterable[TurnRecord], names: Mapping[InstanceRef, str], *,
                limit: int = MAX_HEARD, before: ImportedHistory | None = None,
                outcomes: Mapping[str, HeardLine] | None = None, as_heard: bool = False) -> tuple[HeardLine, ...]:
    """What the whole table saw (the director's view): every post but a private one, each decided action's
    outcome line (``outcomes``, by turn id) right after it, and every kept line, after the chat's imported
    history (``before``). With ``as_heard`` (035 asks it), a kept line of a take played on a 041 stage is in it
    only when a player character as of its turn (``Take.players``, by control when the take was written, never
    now) is in its audience, since the table sees only what a player character heard; a staged take that
    recorded no players gives none (fail closed). A take with no stage, or without ``as_heard``, gives every line."""
    seen: list[HeardLine | None] = list(before.table) if before is not None else []
    for turn in _ordered(turns):
        if turn.post is not None and not turn.post.private:
            seen.append(_post_line(turn.post, names))
            seen.append((outcomes or {}).get(turn.turn_id))
        kept = turn.kept
        staged = as_heard and kept is not None and kept.on_stage is not None
        table = frozenset(kept.players or ()) if staged else None
        seen.extend(_heard(line.speaker, line.text, line.actor) for line in turn.visible(None)
                    if table is None or table.intersection(line.audience))
    found = [line for line in seen if line is not None]
    return tuple(found[-limit:]) if limit else ()


def render(lines: Iterable[HeardLine]) -> str:
    """Plain text for 031's ``scene_text``/``table_text``: one ``speaker: text`` line each."""
    return "\n".join(f"{line.speaker}: {line.text}" for line in lines)


def segments(turn: TurnRecord) -> tuple[NarrationSegment, ...]:
    """016 segments for every line still shown on a turn's proposed or kept takes.

    A kept take's lines on a turn whose 021 effect is accepted (or being accepted: fail closed) depict accepted
    canon, so each is an ``accepted`` segment naming that effect's event: a skip passes over its depiction and a
    redirect leaves it alone (016). Every other line is a ``proposed``, unlabelled segment a request may withdraw.
    """
    canon = next((effect.event_id for effect in turn.effects if effect.state in ("accepting", "accepted")), None)
    found = []
    for take in turn.takes:
        if take.state in ("proposed", "kept"):
            status, event = ("accepted", canon) if take.state == "kept" and canon is not None else ("proposed", None)
            found.extend(NarrationSegment(turn.chat.campaign_id, line.line_id, line.text, None, status, event)
                         for line in take.shown())
    return tuple(found)


__all__ = ["NARRATOR", "ImportedHistory", "audible", "heard_for", "imported_history", "imported_table", "render",
           "segments", "table_lines"]
