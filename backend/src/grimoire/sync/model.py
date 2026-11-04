"""Values of the sync decision core: one path's side states, its base and the decision."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SideState = Literal["absent", "present", "blocked"]
BaseKind = Literal["first", "none", "uncertain", "agreed", "deleted"]
Action = Literal["agreed", "copy", "conflict"]

#: The record kinds sync owns (the store's ``classify`` kinds); later families extend this set,
#: and archive and pending documents accept exactly these paths. ``scope`` and ``chat`` are
#: 107's campaign-local play-scope and chat records; ``relation`` is 017's typed relation record;
#: ``accepted`` and ``wrapup`` are 021's accepted events and scene wrap-ups (an effect's relation
#: changes are synced together with the event that made them).
SYNCED_KINDS = frozenset({"definition", "manifest", "instance", "scope", "chat", "relation", "knowledge",
                          "accepted", "wrapup"})
#: Campaign-local kinds: independent of sources and of each other at commit time.
CAMPAIGN_LOCAL = frozenset({"instance", "scope", "chat", "relation", "knowledge", "accepted", "wrapup"})
#: Create-only kinds: copied only onto an absent file, and a changed one is a conflict, never a copy.
#: ``knowledge`` is 020's events, evidence and knowledge-log batches.
IMMUTABLE_KINDS = frozenset({"definition", "knowledge"})

#: Conflict codes, each named in docs/SYNC.md.
CONFLICT_CODES = frozenset({
    "blocked", "missing_ancestor", "deleted", "edit_delete", "concurrent", "resurrected",
    "revision_modified", "head_dependency", "name_collision", "unapplicable", "denied",
})


@dataclass(frozen=True)
class Side:
    """One device's file at a path: absent, present (``etag``), or blocked (``reason``)."""

    state: SideState
    etag: str | None = None
    previous: str | None = None  # the ``previous`` the file declares (one-hop store lineage)
    reason: str | None = None

    @classmethod
    def absent(cls) -> Side:
        return cls("absent")

    @classmethod
    def present(cls, etag: str, previous: str | None = None) -> Side:
        return cls("present", etag, previous)

    @classmethod
    def blocked(cls, reason: str) -> Side:
        return cls("blocked", reason=reason)


@dataclass(frozen=True)
class Base:
    """What both devices last verifiably agreed on for a path."""

    kind: BaseKind
    etag: str | None = None

    def __post_init__(self) -> None:
        if (self.kind in ("agreed", "deleted")) != (self.etag is not None):
            raise ValueError(f"a {self.kind} base {'needs' if self.etag is None else 'has no'} etag")


FIRST, NONE, UNCERTAIN = Base("first"), Base("none"), Base("uncertain")


@dataclass(frozen=True)
class Decision:
    """``copy`` names the side whose bytes travel (``origin``); a conflict names its ``code``."""

    action: Action
    code: str | None = None
    origin: Literal["left", "right"] | None = None

    def mirrored(self) -> Decision:
        flip = {"left": "right", "right": "left", None: None}
        return Decision(self.action, self.code, flip[self.origin])  # type: ignore[arg-type]
