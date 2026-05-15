"""Shared dataclasses and enums used across the Continuity module.

These types track the campaign's fact ledger, commitment ledger, knowledge
state and time bookkeeping. They are deliberately self-contained: the wider
shared-types module (task #2) is not yet in place, so InGameTime and Duration
live here as minimal placeholders. When the shared types land they should be
swapped in by re-export from this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

FactId = str
CommitmentId = str
ContradictionReportId = str
CharacterId = str
PostId = str


# ---------------------------------------------------------------------------
# Time placeholders (until shared types from task #2 land)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class InGameTime:
    """Canonical in-game timestamp.

    `day_count` is the only field used for comparison so the Continuity
    module never has to know about per-world calendars. World-specific
    calendars convert their representation to a day count when they hand
    timestamps to Continuity.
    """

    day_count: int
    label: str = ""

    def __add__(self, other: Duration) -> InGameTime:
        return InGameTime(day_count=self.day_count + other.days)

    def __sub__(self, other: InGameTime | Duration) -> Duration | InGameTime:
        if isinstance(other, InGameTime):
            return Duration(days=self.day_count - other.day_count)
        return InGameTime(day_count=self.day_count - other.days)


@dataclass(frozen=True, order=True)
class Duration:
    """Coarse in-game duration in whole days."""

    days: int

    @classmethod
    def months(cls, n: int) -> Duration:
        return cls(days=n * 30)

    @classmethod
    def years(cls, n: int) -> Duration:
        return cls(days=n * 365)


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


class FactSource(StrEnum):
    NARRATOR = "narrator"
    CHARACTER_TESTIMONY = "character_testimony"
    INFERRED = "inferred"
    USER_DECLARED = "user_declared"


class RetirementReason(StrEnum):
    REFUTED = "refuted"
    SUPERSEDED = "superseded"
    RETCONNED = "retconned"


@dataclass
class FactSubject:
    character_ids: list[str] = field(default_factory=list)
    location_ids: list[str] = field(default_factory=list)
    faction_ids: list[str] = field(default_factory=list)
    item_ids: list[str] = field(default_factory=list)
    scope: str = "public"  # private | household | public | world


@dataclass
class Fact:
    id: FactId
    text: str
    established_in_post: PostId
    established_at_in_game: InGameTime
    confidence: float
    source: FactSource
    about: FactSubject = field(default_factory=FactSubject)
    speaker_id: CharacterId | None = None
    keywords: list[str] = field(default_factory=list)
    embedding: list[float] | None = None
    retired: bool = False
    retired_in_post: PostId | None = None
    retired_reason: RetirementReason | None = None
    contradicts: list[FactId] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Commitments
# ---------------------------------------------------------------------------


class CommitmentKind(StrEnum):
    PROMISE = "promise"
    THREAT = "threat"
    FORESHADOW = "foreshadow"
    OBLIGATION = "obligation"
    MYSTERY = "mystery"


class CommitmentStatus(StrEnum):
    OPEN = "open"
    PAID = "paid"
    BROKEN = "broken"
    OVERDUE = "overdue"
    STALE = "stale"


# Terminal statuses do not age further.
TERMINAL_STATUSES: frozenset[CommitmentStatus] = frozenset(
    {CommitmentStatus.PAID, CommitmentStatus.BROKEN}
)


@dataclass
class Commitment:
    id: CommitmentId
    kind: CommitmentKind
    text: str
    created_in_post: PostId
    in_game_created_at: InGameTime
    weight: int = 1  # 1-5: significance
    from_id: str | None = None
    to_id: str | None = None
    due_by: InGameTime | None = None
    status: CommitmentStatus = CommitmentStatus.OPEN
    resolved_in_post: PostId | None = None
    last_activity_at: InGameTime | None = None
    tags: list[str] = field(default_factory=list)
    related_fact_ids: list[FactId] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Knowledge state
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeEntry:
    fact_id: FactId
    character_id: CharacterId
    knows: bool = True
    learned_in_post: PostId | None = None
    # free-text attribution: "told by winifred", "witnessed", "deduced", ...
    source: str = ""


# ---------------------------------------------------------------------------
# Contradictions
# ---------------------------------------------------------------------------


class ContradictionVerdict(StrEnum):
    CONFLICT = "conflict"
    NO_CONFLICT = "no_conflict"
    UNCERTAIN = "uncertain"


@dataclass
class ContradictionCandidate:
    """One existing fact that may conflict with a proposed new fact."""

    existing_fact: Fact
    similarity: float
    verdict: ContradictionVerdict
    confidence: float
    rationale: str = ""


@dataclass
class ContradictionReport:
    id: ContradictionReportId
    candidate_fact: Fact
    conflicts: list[ContradictionCandidate]
    resolved: bool = False
    resolution: dict | None = None


class ContradictionResolutionAction(StrEnum):
    KEEP_EXISTING = "keep_existing"  # drop new
    REPLACE_EXISTING = "replace_existing"  # retire existing, add new
    BOTH_TRUE = "both_true"  # no conflict, add new
    EDIT_NEW = "edit_new"  # add new with a patch
    EDIT_EXISTING = "edit_existing"  # update existing with a patch


# ---------------------------------------------------------------------------
# Aging
# ---------------------------------------------------------------------------


@dataclass
class AgingReport:
    from_time: InGameTime
    to_time: InGameTime
    became_overdue: list[Commitment] = field(default_factory=list)
    became_stale: list[Commitment] = field(default_factory=list)


__all__ = [
    "TERMINAL_STATUSES",
    "AgingReport",
    "CharacterId",
    "Commitment",
    "CommitmentId",
    "CommitmentKind",
    "CommitmentStatus",
    "ContradictionCandidate",
    "ContradictionReport",
    "ContradictionReportId",
    "ContradictionResolutionAction",
    "ContradictionVerdict",
    "Duration",
    "Fact",
    "FactId",
    "FactSource",
    "FactSubject",
    "InGameTime",
    "KnowledgeEntry",
    "PostId",
    "RetirementReason",
]
