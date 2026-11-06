"""What each listener heard, what the table saw, and the segments a mid-session request names.

Only kept takes are heard: a proposal is heard by no one, so a reroll never hears the take it replaces.
A line's ``audience`` was fixed when it was spoken, so these are filters, never inferences: an actor
hears a post or a line only if it was in that audience. Narration's audience is the table's player
characters, so an NPC never hears it (031: ``scene_text`` holds no director narration). A private
message (a whisper) is in the table transcript of no one but its audience, and never the director's.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from grimoire.core.boundaries import NarrationSegment
from grimoire.core.identity import InstanceRef
from grimoire.generation import MAX_HEARD, MAX_LINE_CHARS, GenerationError, HeardLine

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


def heard_for(turns: Iterable[TurnRecord], actor: InstanceRef, names: Mapping[InstanceRef, str], *,
              limit: int = MAX_HEARD) -> tuple[HeardLine, ...]:
    """What ``actor`` heard, oldest first: posts in its audience and kept lines it heard (the last ``limit``)."""
    heard: list[HeardLine | None] = []
    for turn in _ordered(turns):
        if turn.post is not None and actor in turn.post.audience:
            heard.append(_post_line(turn.post, names))
        heard.extend(_heard(line.speaker, line.text, line.actor) for line in turn.visible(actor))
    found = [line for line in heard if line is not None]
    return tuple(found[-limit:]) if limit else ()


def table_lines(turns: Iterable[TurnRecord], names: Mapping[InstanceRef, str], *,
                limit: int = MAX_HEARD) -> tuple[HeardLine, ...]:
    """What the whole table saw (the director's view): every post but a private one, and every kept line."""
    seen: list[HeardLine | None] = []
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


__all__ = ["NARRATOR", "audible", "heard_for", "render", "segments", "table_lines"]
