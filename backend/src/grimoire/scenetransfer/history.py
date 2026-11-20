"""What a scene copy may carry: the origin chat's history as the table saw it, post by post, with who heard each.

Pure: no store, no clock. A copy is **transcript only** (084's first slice): the posts the origin's table saw, in
order, as new 036 transcript pages of the target chat. Each item keeps the origin actor that wrote it and the
origin actors that heard it, so the planner can refuse to give a target actor something its origin counterpart
never heard (the target runner's rule for imported history is that the cast a chat starts with heard every shown
post, ``grimoire.scene.transcript``).

The origin's history has two parts, read here exactly as the runner reads them:

- its **imported pages** (036/026): heard by the actors the chat was imported with (its sequence-0 roster), by
  ``cast_heard``'s rule; narration, a ``system`` notice and a hidden post are heard by no actor and travel as the
  table's only. Every variant a post held travels with it, as written; an unchosen one stays unchosen.
- its **played turns** (035) on the chat's line (a set-aside branch is not history): every post but a private one
  (a whisper, telepathy, a withheld action: ``Post.private``), heard by its ``audience``; and every shown line of the
  turn's kept take that the table saw (``table_lines``' ``as_heard`` rule: on a 041 stage only a line a player
  character of its take heard), heard by that line's ``audience``. Narration is the table's (no actor hears it).
  A proposal, a withdrawn or struck line and a take nobody kept were shown to no one and are not history.

Nothing here reads or copies state: an accepted effect, a ruling and a decided outcome line stay in the origin
campaign (reported as ``Left``), and so does every event, belief, relation, holding or memory, which turns never hold.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.scene import TurnRecord
from grimoire.scene.transcript import NARRATOR, cast_heard
from grimoire.transcripts import Post, Speaker, TranscriptPage, Variant

#: an item's ``heard_by`` when no actor heard it (narration, a notice, a hidden post): the table's only
TABLE_ONLY: frozenset[InstanceRef] | None = None


@dataclass(frozen=True)
class HistoryItem:
    """One post the origin's table saw, as a copy would write it (speaker still the origin's actor).

    ``where`` names it in the origin for a report: a page post (``page 1 p000003``), a turn's post
    (``<turn_id>``, ``<turn_id>.p2``) or a kept line (its line id). ``heard_by`` is the origin actors that heard
    it, or ``None`` when no actor did (the table's only); a copy gives it to a target listener only when every
    origin actor the copy carries over heard it."""

    where: str
    post: Post
    heard_by: frozenset[InstanceRef] | None


@dataclass(frozen=True)
class Left:
    """Something of the origin a copy leaves where it is: ``kind`` (``private_post``, ``effect``, ``ruling``)
    and where (a turn or post id). ``detail`` is an id worth naming (an accepted event), or ``None``."""

    kind: str
    where: str
    detail: str | None = None


def page_items(chat: ChatRef, pages: Sequence[TranscriptPage], imported_cast: Iterable[InstanceRef]
               ) -> tuple[HistoryItem, ...]:
    """Every post of the imported pages, in page and post order, with who heard it in the origin. Each keeps its
    speaker, variants, flags and extras as written; its ``source`` records where it was copied from, with the
    source's own provenance kept beneath."""
    cast = frozenset(imported_cast)
    found = []
    for page in sorted(pages, key=lambda each: each.ref.seq):
        for post in page.posts:
            copied = replace(post, source={"copied_from": {"chat": str(chat), "page": page.ref.seq,
                                                           "post": post.post_id}, "source": post.source})
            found.append(HistoryItem(f"page {page.ref.seq} {post.post_id}", copied,
                                     cast if cast_heard(post) else TABLE_ONLY))
    return tuple(found)


def _action(text: str) -> str:
    """An action post as a listener heard it: in asterisks (``grimoire.scene.transcript``'s ``_post_line``), so
    the copied history still reads as something done, not said."""
    return f"*{text.strip('*').strip() or text}*"


def turn_items(chat: ChatRef, turns: Sequence[TurnRecord], names: Mapping[InstanceRef, str]
               ) -> tuple[tuple[HistoryItem, ...], tuple[Left, ...]]:
    """The table's view of the played turns, in order, and what of them stays behind.

    ``turns`` is the chat's line as play reads it (``ChatTurns.turns``). ``names`` labels a player's post with
    its character's name (an actor it cannot name keeps its instance id, as the runner does)."""
    items: list[HistoryItem] = []
    left: list[Left] = []
    for turn in sorted(turns, key=lambda each: each.number):
        for index, post in enumerate(turn.posts, start=1):
            where = turn.post_id(index)
            if post.private:  # a whisper, telepathy or a withheld action: its audience's, never the table's
                left.append(Left("private_post", where))
                continue
            text = post.text if post.channel is not None else _action(post.text)
            label = names.get(post.actor) or post.actor.instance_id
            copied = Post("p", Speaker("user", post.actor, label), (Variant(text),), 0,
                          source={"copied_from": {"chat": str(chat), "turn": turn.turn_id, "post": index}})
            items.append(HistoryItem(where, copied, frozenset(post.audience)))
        kept = turn.kept
        if kept is not None:
            staged = kept.on_stage is not None
            table = frozenset(kept.players or ()) if staged else None
            for line in kept.shown():
                if table is not None and not table.intersection(line.audience):
                    continue  # no player character of its take heard it: the table never saw it
                narrated = line.actor is None
                speaker = (Speaker("narrator", None, NARRATOR) if narrated
                           else Speaker("character", line.actor, line.speaker))
                copied = Post("p", speaker, (Variant(line.text),), 0,
                              source={"copied_from": {"chat": str(chat), "turn": turn.turn_id, "line": line.line_id}})
                items.append(HistoryItem(line.line_id, copied, TABLE_ONLY if narrated else frozenset(line.audience)))
        for effect in turn.effects:  # 021's accepted canon: the origin campaign's, never the copy's
            if effect.state in ("accepting", "accepted"):
                left.append(Left("effect", turn.turn_id, effect.event_id))
        if turn.framed is not None:  # 029's frame and its decided outcome: the origin's ruling
            left.append(Left("ruling", turn.turn_id))
    return tuple(items), tuple(left)


def speakers(items: Iterable[HistoryItem]) -> frozenset[InstanceRef]:
    """Every origin actor that wrote a post of ``items``."""
    return frozenset(item.post.speaker.actor for item in items if item.post.speaker.actor is not None)


__all__ = ["TABLE_ONLY", "HistoryItem", "Left", "page_items", "speakers", "turn_items"]
