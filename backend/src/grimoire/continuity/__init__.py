"""Continuity — the campaign's memory of facts, commitments, and knowledge.

See spec `specs/11-continuity.md`. Public surface:

- `Continuity` protocol (what callers depend on)
- `ContinuityService` (default implementation)
- `ContinuityStore`, `FactSearchIndex`, `ContradictionJudge` protocols
  (persistence / retrieval / LLM judgment seams)
- `InMemoryContinuityStore`, `KeywordFactSearchIndex`,
  `StubContradictionJudge` (default in-process implementations)
- Dataclasses: `Fact`, `Commitment`, `KnowledgeEntry`,
  `ContradictionReport`, `AgingReport`, `InGameTime`, `Duration`
- Enums: `FactSource`, `CommitmentKind`, `CommitmentStatus`,
  `ContradictionVerdict`, `ContradictionResolutionAction`,
  `RetirementReason`
- Config: `ContinuityConfig`
"""

from grimoire.continuity.config import (
    ContinuityConfig,
    ContradictionCheckConfig,
    KeywordRetrievalConfig,
)
from grimoire.continuity.protocols import (
    Continuity,
    ContinuityStore,
    ContradictionJudge,
    FactSearchIndex,
)
from grimoire.continuity.service import (
    CommitmentNotFoundError,
    ConfidenceFloorError,
    ContinuityService,
    ContradictionReportNotFoundError,
    FactNotFoundError,
)
from grimoire.continuity.store import (
    InMemoryContinuityStore,
    KeywordFactSearchIndex,
    StubContradictionJudge,
)
from grimoire.continuity.types import (
    AgingReport,
    Commitment,
    CommitmentId,
    CommitmentKind,
    CommitmentStatus,
    ContradictionCandidate,
    ContradictionReport,
    ContradictionReportId,
    ContradictionResolutionAction,
    ContradictionVerdict,
    Duration,
    Fact,
    FactId,
    FactSource,
    FactSubject,
    InGameTime,
    KnowledgeEntry,
    RetirementReason,
)

__all__ = [
    "AgingReport",
    "Commitment",
    "CommitmentId",
    "CommitmentKind",
    "CommitmentNotFoundError",
    "CommitmentStatus",
    "ConfidenceFloorError",
    "Continuity",
    "ContinuityConfig",
    "ContinuityService",
    "ContinuityStore",
    "ContradictionCandidate",
    "ContradictionCheckConfig",
    "ContradictionJudge",
    "ContradictionReport",
    "ContradictionReportId",
    "ContradictionReportNotFoundError",
    "ContradictionResolutionAction",
    "ContradictionVerdict",
    "Duration",
    "Fact",
    "FactId",
    "FactNotFoundError",
    "FactSearchIndex",
    "FactSource",
    "FactSubject",
    "InGameTime",
    "InMemoryContinuityStore",
    "KeywordFactSearchIndex",
    "KeywordRetrievalConfig",
    "KnowledgeEntry",
    "RetirementReason",
    "StubContradictionJudge",
]
