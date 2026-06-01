"""Protocols defining the seams around the Continuity module.

Continuity depends on:
  - a persistence layer (provided by the State Store, task #8)
  - an LLM judge for contradiction detection (provided by the LLM Gateway,
    task #13)

Both are abstracted behind Protocols so the module can be built, tested and
swapped without those modules being present. The in-memory store in
`grimoire.continuity.store` satisfies `ContinuityStore`; an always-uncertain
stub satisfies `ContradictionJudge`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from grimoire.continuity.types import (
    AgingReport,
    Commitment,
    CommitmentId,
    CommitmentStatus,
    ContradictionCandidate,
    ContradictionReport,
    ContradictionReportId,
    Duration,
    Fact,
    FactId,
    InGameTime,
    KnowledgeEntry,
    SceneBriefing,
)
from grimoire.types.common import TurnId


@runtime_checkable
class ContinuityStore(Protocol):
    """Persistent storage seam. State Store implements this in task #8."""

    # Facts
    async def put_fact(self, fact: Fact) -> None: ...
    async def get_fact(self, fact_id: FactId) -> Fact | None: ...
    async def list_facts(self, *, include_retired: bool = False) -> list[Fact]: ...

    # Commitments
    async def put_commitment(self, commitment: Commitment) -> None: ...
    async def get_commitment(self, cid: CommitmentId) -> Commitment | None: ...
    async def list_commitments(
        self, *, statuses: Iterable[CommitmentStatus] | None = None
    ) -> list[Commitment]: ...
    async def delete_commitment(self, cid: CommitmentId) -> None: ...

    # Knowledge
    async def put_knowledge(self, entry: KnowledgeEntry) -> None: ...
    async def get_knowledge(self, character_id: str, fact_id: FactId) -> KnowledgeEntry | None: ...
    async def knowledge_for_character(self, character_id: str) -> list[KnowledgeEntry]: ...
    async def knowledge_learned_in(self, post_id: str) -> list[KnowledgeEntry]: ...
    async def delete_knowledge(self, character_id: str, fact_id: FactId) -> None: ...

    # Contradictions
    async def put_contradiction_report(self, report: ContradictionReport) -> None: ...
    async def get_contradiction_report(
        self, report_id: ContradictionReportId
    ) -> ContradictionReport | None: ...
    async def list_contradiction_reports(
        self,
        *,
        resolved: bool | None = None,
        limit: int = 50,
    ) -> list[ContradictionReport]: ...


@runtime_checkable
class ContradictionJudge(Protocol):
    """LLM judge for pairwise fact contradiction.

    The real implementation calls the LLM Gateway with the configured
    `contradiction_check.model_route`. Tests can substitute a deterministic
    judge.
    """

    async def judge(
        self,
        candidate: Fact,
        existing: Fact,
        *,
        turn_id: TurnId | None = None,
    ) -> ContradictionCandidate: ...


@runtime_checkable
class FactSearchIndex(Protocol):
    """Top-K similarity search over the fact ledger.

    Two implementations are anticipated:
      - keyword/token overlap (the in-memory default)
      - vector search via sqlite-vec, populated by an embedding provider

    A search index that has no embedding for a fact falls back to keyword
    overlap on the fact text and stored keywords.
    """

    async def search(
        self, query: str, top_k: int, *, include_retired: bool = False
    ) -> list[tuple[Fact, float]]: ...


@runtime_checkable
class Continuity(Protocol):
    """Public Continuity API consumed by Context Builder, Extractor, etc."""

    # Fact writes
    async def add_fact(self, fact: Fact, source: str) -> FactId: ...
    async def retire_fact(self, fact_id: FactId, in_post: str, reason: str) -> None: ...
    async def update_fact(
        self, fact_id: FactId, patch: dict, in_post: str | None = None
    ) -> Fact: ...
    # Whether ``turn_id`` performed a FACT_UPDATE — cascade delete warns these
    # edits remain applied (no pre-image to reverse).
    def turn_had_fact_update(self, turn_id: str) -> bool: ...

    # Turn retraction (cascade delete / undo): retract continuity writes whose
    # establishing post/turn id matches ``turn_id``.
    async def retract_turn(self, turn_id: str) -> dict: ...
    # Whether ``turn_id`` made any continuity write — cascade delete uses this to
    # warn about straddling (partially deleted) turns it can't cleanly retract.
    async def turn_has_continuity_writes(self, turn_id: str) -> bool: ...

    # Fact reads
    async def get_fact(self, fact_id: FactId) -> Fact: ...
    async def facts_about(
        self,
        *,
        character_ids: list[str] | None = None,
        location_ids: list[str] | None = None,
        faction_ids: list[str] | None = None,
        item_ids: list[str] | None = None,
        limit: int = 50,
        include_retired: bool = False,
    ) -> list[Fact]: ...
    async def search_facts(self, query: str, top_k: int = 10) -> list[Fact]: ...
    async def recent_facts(self, since: InGameTime, limit: int = 50) -> list[Fact]: ...
    async def facts_known_by(
        self, character_id: str, *, limit: int = 50, include_retired: bool = False
    ) -> list[Fact]: ...
    async def facts_for_terms(self, terms: Iterable[str], *, limit: int = 10) -> list[Fact]: ...

    # Contradictions
    async def check_contradictions(
        self,
        candidate: Fact,
        *,
        turn_id: TurnId | None = None,
    ) -> ContradictionReport: ...
    async def resolve_contradiction(
        self, report_id: ContradictionReportId, resolution: dict
    ) -> None: ...
    async def pending_contradictions(self, limit: int = 20) -> list[ContradictionReport]: ...

    # Commitments
    async def add_commitment(self, c: Commitment, source: str) -> CommitmentId: ...
    async def resolve_commitment(
        self, cid: CommitmentId, status: CommitmentStatus, in_post: str
    ) -> None: ...
    async def get_commitment(self, cid: CommitmentId) -> Commitment: ...
    async def open_commitments(
        self,
        *,
        involving: list[str] | None = None,
        limit: int = 50,
    ) -> list[Commitment]: ...
    async def overdue_commitments(self, as_of: InGameTime) -> list[Commitment]: ...
    async def stale_commitments(self, threshold: Duration) -> list[Commitment]: ...
    async def reopen_commitment(self, cid: CommitmentId, in_post: str) -> Commitment: ...
    async def all_commitments(self) -> list[Commitment]: ...
    async def brief_for_scene(
        self,
        scene_id: str,
        pc_refs: list[str],
        *,
        as_of: InGameTime | None = None,
    ) -> SceneBriefing: ...

    # Knowledge
    async def knows(self, character_id: str, fact_id: FactId) -> bool: ...
    async def reveal(
        self,
        fact_id: FactId,
        to: list[str],
        in_post: str,
        source: str,
    ) -> None: ...
    async def secrets_of(self, character_id: str) -> list[Fact]: ...

    # Time
    async def age(self, to_time: InGameTime) -> AgingReport: ...


__all__ = [
    "Continuity",
    "ContinuityStore",
    "ContradictionJudge",
    "FactSearchIndex",
]
