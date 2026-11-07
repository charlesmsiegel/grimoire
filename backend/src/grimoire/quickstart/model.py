"""What one Start Chat action is: a single launch selection and the choices it offers (052).

Pure values: no files, clocks or providers. ``grimoire.quickstart.launcher`` is the store-backed service.
See docs/QUICKSTART.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.chats.model import MAX_PREMISE
from grimoire.core.identity import ChatRef, SourceRef
from grimoire.scene import TurnRecord

#: The four things a player can start a chat from. A lorebook alone is no launch here: 107 can bind one,
#: but there is nobody to talk to and nothing to open with.
SELECTION_KINDS = frozenset({"character", "scenario", "bundle", "premise"})
#: The definition kind each selection's ``ref`` names.
SELECTION_REFS = {"character": "character", "scenario": "scenario", "bundle": "character"}


class QuickStartRefused(ValueError):
    """Start Chat could not do what was asked; ``reason`` is machine-readable. Nothing was written unless
    the refusal says so (``Started.unfinished`` reports a chat that exists but did not open)."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, str(self)))


@dataclass(frozen=True, slots=True)
class Selection:
    """Exactly one launch selection. Build it with ``character``, ``scenario``, ``bundle`` or ``premise``.

    ``ref`` may be pinned or not: Start Chat pins it to the revision it resolves to now either way, so the
    chat updates only through 042's reviewed rebase. A bundle's ``lorebook`` is ``None`` for the card's own
    embedded lorebook (037 records it, pinned, under ``card.lorebook``)."""

    kind: str
    ref: SourceRef | None = None
    lorebook: SourceRef | None = None
    premise: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not str or self.kind not in SELECTION_KINDS:
            raise QuickStartRefused("invalid_selection", f"unknown selection kind {self.kind!r}")
        if self.kind == "premise":
            if self.ref is not None or self.lorebook is not None:
                raise QuickStartRefused("invalid_selection", "a premise names no source")
            if type(self.premise) is not str or not self.premise.strip() or len(self.premise) > MAX_PREMISE:
                raise QuickStartRefused("invalid_selection",
                                        f"a premise is nonblank text of at most {MAX_PREMISE} characters")
            return
        if self.premise is not None:
            raise QuickStartRefused("invalid_selection", "only a premise selection carries premise text")
        if not isinstance(self.ref, SourceRef) or self.ref.kind != SELECTION_REFS[self.kind]:
            raise QuickStartRefused("invalid_selection",
                                    f"a {self.kind} selection needs a {SELECTION_REFS[self.kind]} definition")
        if self.lorebook is not None and (self.kind != "bundle" or not isinstance(self.lorebook, SourceRef)
                                          or self.lorebook.kind != "lorebook"):
            raise QuickStartRefused("invalid_selection", "only a bundle names a lorebook, and it is a lorebook")

    @classmethod
    def character(cls, ref: SourceRef) -> Selection:
        return cls("character", ref)

    @classmethod
    def scenario(cls, ref: SourceRef) -> Selection:
        return cls("scenario", ref)

    @classmethod
    def bundle(cls, ref: SourceRef, lorebook: SourceRef | None = None) -> Selection:
        return cls("bundle", ref, lorebook)

    @classmethod
    def from_premise(cls, text: str) -> Selection:
        return cls("premise", premise=text)


@dataclass(frozen=True, slots=True)
class Opening:
    """One way the chat can open: a card greeting or a scenario opening, by its imported id."""

    opening_id: str
    text: str
    group_only: bool = False


@dataclass(frozen=True, slots=True)
class LaunchOptions:
    """What a selection offers before anything is written or any model runs.

    ``sources`` are the exact pinned refs Start Chat would bind (launch first, then a bundle's lorebook).
    ``openings`` are the choosable greetings or openings, in the card's order; ``default_opening`` is the
    one Start Chat opens with when none is chosen (``None``: the chat starts empty, or with its premise).
    ``lorebook`` is the character's own embedded lorebook, offered for a bundle. ``related`` are the chats
    already started from this source, newest first, for Continue."""

    selection: Selection
    sources: tuple[SourceRef, ...]
    openings: tuple[Opening, ...]
    default_opening: str | None
    lorebook: SourceRef | None
    related: tuple[ChatRef, ...]


@dataclass(frozen=True, slots=True)
class Started:
    """A chat Start Chat made (or Continue opened). ``opening`` is its kept opening turn, if it has one.

    ``unfinished`` names what stopped the launch after the chat was written (a 035 refusal reason, such as
    ``turns_unreadable``): the chat exists and ``continue_chat`` finishes the launch; it is never started
    twice."""

    chat: ChatRef
    opening: TurnRecord | None = None
    unfinished: str | None = None


@dataclass(frozen=True, slots=True)
class Repair:
    """A source the chat binds that does not resolve now, and what the player can do about it.

    The chat and its turns are kept as they are. ``choices`` is ``("detach",)`` for a binding; a broken
    launch cannot be detached (it is immutable provenance), so it offers nothing but re-importing the card.
    Nothing similarly named is ever bound in its place, and a missing pinned revision is never swapped for
    the head without 042's review."""

    field: str
    ref: str
    reason: str
    role: str | None
    choices: tuple[str, ...]


__all__ = ["LaunchOptions", "Opening", "QuickStartRefused", "Repair", "SELECTION_KINDS", "Selection", "Started"]
