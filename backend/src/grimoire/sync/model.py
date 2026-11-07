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
#: ``blob``, ``image`` and ``placement`` are 018's art; ``transcript`` is 036's chat transcript pages; ``turn`` is
#: 035's played chat turns (028's turn merge: mutable, so ordinary rows; ``sync.turns`` names what one needs).
SYNCED_KINDS = frozenset({"definition", "manifest", "instance", "scope", "chat", "relation", "knowledge",
                          "blob", "image", "placement", "transcript", "turn"})
#: 018's art: own units, never source-coarse, never grouped with a definition. A blob is binary and
#: content-addressed (listed by name and size, hashed only when copied or first seen).
ASSET_KINDS = frozenset({"blob", "image", "placement"})
#: Campaign-local kinds: independent of sources and of each other at commit time.
CAMPAIGN_LOCAL = frozenset({"instance", "scope", "chat", "relation", "knowledge", "transcript", "turn"})
#: Families whose own stores commit several files as one unit and whose cross-device merge rule is
#: a later milestone (docs/EFFECTS.md, docs/HOLDINGS.md): never copied file by file, always reported
#: as skipped. 021's accepted events and wrap-ups and 023's holdings. 035's played turns left this set with
#: 028's turn merge: two devices' turn n are two files, and a forked chat waits for its owner's choice.
HELD_BACK_KINDS = frozenset({"accepted", "wrapup", "holdings"})
#: A campaign holding any record of this kind on either device has its 017 relations held back too,
#: because 021 changes relations in the same commit as an event: a relation must never arrive
#: without the event that changed it.
HOLDS_RELATIONS = "accepted"
#: Create-only kinds: copied only onto an absent file, and a changed one is a conflict, never a copy.
#: ``knowledge`` is 020's events, evidence and knowledge-log batches; ``image`` and ``blob`` are 018's;
#: ``transcript`` is 036's append-only transcript pages.
IMMUTABLE_KINDS = frozenset({"definition", "knowledge", "image", "blob", "transcript"})

#: Conflict codes, each named in docs/SYNC.md. ``held``: a held-back path whose two devices hold
#: different bytes (nothing is copied or kept for it; it ends only when both hold identical bytes).
CONFLICT_CODES = frozenset({
    "blocked", "missing_ancestor", "deleted", "edit_delete", "concurrent", "resurrected",
    "revision_modified", "head_dependency", "name_collision", "unapplicable", "denied", "held",
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
