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
    shown post written by the player, a character or a source's generated reply (V0.2.1's ``assistant``,
    which does not say whether a character spoke or the narrator did). ``table`` is the director's view:
    those posts and the narrator's. A narrator post is never heard by an NPC (as in play), and a source's
    own ``system`` notice (a V0.2.1 synthetic post, a dice roll), a ``hidden`` post, an unchosen variant
    and a post with no chosen text are in neither. ``posts`` counts the posts the pages hold.
    """

    listeners: frozenset[InstanceRef]
    heard: tuple[HeardLine, ...]
    table: tuple[HeardLine, ...]
    posts: int


#: who an imported post's role says wrote it, and whether an NPC of the imported cast heard it
_HEARD_ROLES = frozenset({"user", "character", "assistant"})
_TABLE_ROLES = _HEARD_ROLES | {"narrator"}


def _imported_line(post: ImportedPost, names: Mapping[InstanceRef, str]) -> HeardLine | None:
    if post.hidden or post.active is None or post.speaker.role not in _TABLE_ROLES:
        return None
    actor = post.speaker.actor
    if post.speaker.role == "narrator":
        speaker = NARRATOR
    elif actor is not None and names.get(actor):
        speaker = names[actor]
    else:  # a label is display text, kept as the source wrote it; it never names an actor
        speaker = (post.speaker.label or "").strip() or post.speaker.role.capitalize()
    return _heard(speaker, post.variants[post.active].text, actor)


def imported_history(pages: Iterable[TranscriptPage], listeners: Iterable[InstanceRef],
                     names: Mapping[InstanceRef, str]) -> ImportedHistory:
    """The history ``pages`` hold, in page and post order, heard by ``listeners`` (the imported cast)."""
    heard: list[HeardLine] = []
    table: list[HeardLine] = []
    count = 0
    for page in sorted(pages, key=lambda found: found.ref.seq):
        for post in page.posts:
            count += 1
            line = _imported_line(post, names)
            if line is None:
                continue
            table.append(line)
            if post.speaker.role in _HEARD_ROLES:
                heard.append(line)
    return ImportedHistory(frozenset(listeners), tuple(heard), tuple(table), count)


def heard_for(turns: Iterable[TurnRecord], actor: InstanceRef, names: Mapping[InstanceRef, str], *,
              limit: int = MAX_HEARD, before: ImportedHistory | None = None) -> tuple[HeardLine, ...]:
    """What ``actor`` heard, oldest first: posts in its audience and kept lines it heard (the last ``limit``).

    ``before`` is the chat's imported history: an actor it was imported with heard it before turn 1.
    """
    heard: list[HeardLine | None] = []
    if before is not None and actor in before.listeners:
        heard.extend(before.heard)
    for turn in _ordered(turns):
        if turn.post is not None and actor in turn.post.audience:
            heard.append(_post_line(turn.post, names))
        heard.extend(_heard(line.speaker, line.text, line.actor) for line in turn.visible(actor))
    found = [line for line in heard if line is not None]
    return tuple(found[-limit:]) if limit else ()


def table_lines(turns: Iterable[TurnRecord], names: Mapping[InstanceRef, str], *,
                limit: int = MAX_HEARD, before: ImportedHistory | None = None) -> tuple[HeardLine, ...]:
    """What the whole table saw (the director's view): every post but a private one, and every kept line,
    after the chat's imported history (``before``)."""
    seen: list[HeardLine | None] = list(before.table) if before is not None else []
    for turn in _ordered(turns):
        if turn.post is not None and not turn.post.private:
            seen.append(_post_line(turn.post, names))
        seen.extend(_heard(line.speaker, line.text, line.actor) for line in turn.visible(None))
    found = [line for line in seen if line is not None]
    return tuple(found[-limit:]) if limit else ()


def render(lines: Iterable[HeardLine]) -> str:
    """Plain text for 031's ``scene_text``/``table_text``: one ``speaker: text`` line each."""
    return "\n".join(f"{line.speaker}: {line.text}" for line in lines)


def segments(turn: TurnRecord) -> tuple[NarrationSegment, ...]:
    """016 segments for every line still shown on a turn's proposed or kept takes.

    No line is accepted canon yet (there are no events), so every one is a ``proposed``, unlabelled
    segment: a request may withdraw any of them, and a kept one is then struck.
    """
    found = []
    for take in turn.takes:
        if take.state in ("proposed", "kept"):
            found.extend(NarrationSegment(turn.chat.campaign_id, line.line_id, line.text, None, "proposed")
                         for line in take.shown())
    return tuple(found)


__all__ = ["NARRATOR", "ImportedHistory", "audible", "heard_for", "imported_history", "render", "segments",
           "table_lines"]
