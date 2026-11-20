"""Separate drafts for the player characters of one combined turn (058), before it is submitted.

A ``TurnDraft`` is one chat's pending combined turn: one ``Draft`` per player character, each written by the
principal who drafted it, in the order they will be posted. It is a pure value: putting, correcting, moving and
dropping a draft each give a new ``TurnDraft`` and write nothing. Nothing about a draft is checked against the
table here; who controls each character is read again when the drafts are submitted (``post_together``), so a
draft whose character changed hands meanwhile is refused then and nothing is posted.

What it holds to:

- **One draft a character, each its own drafter's.** Correcting a character's draft keeps its place in the order;
  a principal corrects, moves or drops only a draft they wrote, never another player's.
- **A defined order.** A new draft goes last unless placed; ``move`` places one draft at a position.
- **Bounded.** At most ``MAX_TOGETHER`` drafts, each at most ``MAX_DRAFT_CHARS`` characters.

Drafts live with the app that holds them, in memory: they do not survive a restart in this slice (milestone 2).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.play.together import MAX_TOGETHER
from grimoire.scene import SceneRefused
from grimoire.security import is_human

#: A draft is a post: the text API's own cap on one.
MAX_DRAFT_CHARS = 20_000
#: The lanes a player posts in (the app's own post modes): a table request is never part of a combined turn.
MODES = ("auto", "speech", "action")


def _refuse(reason: str, message: str) -> SceneRefused:
    return SceneRefused(reason, message)


@dataclass(frozen=True, slots=True)
class Draft:
    """What ``by`` drafted for ``actor``, and how 008 is to read it (``auto``, ``speech``, ``action``...)."""

    actor: InstanceRef
    by: str
    text: str
    mode: str = "auto"

    def __post_init__(self) -> None:
        if type(self.actor) is not InstanceRef:
            raise _refuse("invalid_field", "a draft names its character by reference")
        if not isinstance(self.by, str) or not is_human(self.by):
            raise _refuse("invalid_field", "a draft is written by a human player")
        if type(self.text) is not str or not self.text.strip():
            raise _refuse("no_content", "a draft needs text")
        if len(self.text) > MAX_DRAFT_CHARS:
            raise _refuse("invalid_field", f"a draft is at most {MAX_DRAFT_CHARS} characters")
        if self.mode not in MODES:
            raise _refuse("invalid_field", f"a draft's mode is one of {MODES}")


@dataclass(frozen=True, slots=True)
class TurnDraft:
    """One chat's drafted combined turn, in posting order."""

    chat: ChatRef
    drafts: tuple[Draft, ...] = ()

    def __post_init__(self) -> None:
        if type(self.chat) is not ChatRef:
            raise _refuse("invalid_field", "a turn draft is for a chat")
        drafts = tuple(self.drafts)
        if not all(type(draft) is Draft for draft in drafts):
            raise _refuse("invalid_field", "a turn draft holds Draft values")
        if len({draft.actor for draft in drafts}) != len(drafts):
            raise _refuse("invalid_field", "a turn draft holds one draft per character")
        if len(drafts) > MAX_TOGETHER:
            raise _refuse("too_many_drafts", f"a combined turn holds at most {MAX_TOGETHER} characters' posts")
        if any(draft.actor.campaign_id != self.chat.campaign_id for draft in drafts):
            raise _refuse("invalid_field", "a draft's character belongs to its chat's campaign")
        object.__setattr__(self, "drafts", drafts)

    def of(self, actor: InstanceRef) -> Draft | None:
        return next((draft for draft in self.drafts if draft.actor == actor), None)

    def _own(self, actor: InstanceRef, by: str) -> int:
        """The place of ``actor``'s draft, which ``by`` must have written."""
        found = self.of(actor)
        if found is None:
            raise _refuse("no_draft", f"there is no draft for {actor}")
        if found.by != by:
            raise _refuse("not_your_draft", "a player changes only a draft they wrote")
        return self.drafts.index(found)

    def put(self, draft: Draft, *, at: int | None = None) -> TurnDraft:
        """``draft`` added (last, or at ``at``), or correcting its character's earlier draft in its place."""
        if type(draft) is not Draft:
            raise _refuse("invalid_field", "put takes a Draft")
        drafts = list(self.drafts)
        if self.of(draft.actor) is not None:
            drafts[self._own(draft.actor, draft.by)] = draft
            found = replace(self, drafts=tuple(drafts))
            return found if at is None else found.move(draft.actor, draft.by, at)
        drafts.insert(len(drafts) if at is None else self._place(at, len(drafts) + 1), draft)
        return replace(self, drafts=tuple(drafts))

    def move(self, actor: InstanceRef, by: str, at: int) -> TurnDraft:
        """``actor``'s draft placed at position ``at`` (0 is first), the others keeping their order."""
        index = self._own(actor, by)
        drafts = list(self.drafts)
        moved = drafts.pop(index)
        drafts.insert(self._place(at, len(self.drafts)), moved)
        return replace(self, drafts=tuple(drafts))

    def drop(self, actor: InstanceRef, by: str) -> TurnDraft:
        """``actor``'s draft taken out."""
        index = self._own(actor, by)
        return replace(self, drafts=self.drafts[:index] + self.drafts[index + 1:])

    @staticmethod
    def _place(at: object, size: int) -> int:
        if type(at) is not int or not 0 <= at < size:
            raise _refuse("invalid_field", f"a draft's place is 0 to {size - 1}")
        return at


__all__ = ["MAX_DRAFT_CHARS", "Draft", "TurnDraft"]
