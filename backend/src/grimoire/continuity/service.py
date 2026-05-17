"""Concrete Continuity implementation.

Wires together a `ContinuityStore`, a `FactSearchIndex` and a
`ContradictionJudge`. Pure asyncio; no I/O of its own. Time enters from the
outside via `age(to_time)` calls (the Time Engine, task #21).
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterable

from grimoire.continuity.config import ContinuityConfig
from grimoire.continuity.protocols import (
    Continuity,
    ContinuityStore,
    ContradictionJudge,
    FactSearchIndex,
)
from grimoire.continuity.store import (
    InMemoryContinuityStore,
    KeywordFactSearchIndex,
    StubContradictionJudge,
)
from grimoire.continuity.types import (
    TERMINAL_STATUSES,
    AgingReport,
    Commitment,
    CommitmentId,
    CommitmentStatus,
    ContradictionCandidate,
    ContradictionReport,
    ContradictionReportId,
    ContradictionResolutionAction,
    ContradictionVerdict,
    Duration,
    Fact,
    FactId,
    FactSubject,
    InGameTime,
    KnowledgeEntry,
    RetirementReason,
)
from grimoire.types.common import TurnId


class FactNotFoundError(KeyError):
    pass


class CommitmentNotFoundError(KeyError):
    pass


class ContradictionReportNotFoundError(KeyError):
    pass


class ConfidenceFloorError(ValueError):
    """Raised when a fact is rejected because its confidence is too low."""


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _patch_dataclass(obj, patch: dict):
    """Return a copy of `obj` with `patch` applied to allowed fields."""
    field_names = {f.name for f in dataclasses.fields(obj)}
    unknown = set(patch) - field_names
    if unknown:
        raise ValueError(f"unknown fields in patch: {sorted(unknown)}")
    return dataclasses.replace(obj, **patch)


def _involves(commitment: Commitment, refs: Iterable[str]) -> bool:
    refs = set(refs)
    if not refs:
        return True
    return bool(refs & {commitment.from_id, commitment.to_id} - {None})


def _matches_subject(fact: Fact, **filters) -> bool:
    """Check whether a fact's subject overlaps the requested ids."""
    subj = fact.about
    matched_any = False
    any_filter = False
    for attr in ("character_ids", "location_ids", "faction_ids", "item_ids"):
        wanted = filters.get(attr)
        if not wanted:
            continue
        any_filter = True
        if set(wanted) & set(getattr(subj, attr)):
            matched_any = True
    return matched_any if any_filter else True


class ContinuityService(Continuity):
    def __init__(
        self,
        *,
        store: ContinuityStore | None = None,
        search_index: FactSearchIndex | None = None,
        judge: ContradictionJudge | None = None,
        config: ContinuityConfig | None = None,
    ) -> None:
        self._store = store or InMemoryContinuityStore()
        self._config = config or ContinuityConfig()
        self._search = search_index or KeywordFactSearchIndex(
            self._store,
            min_keyword_length=self._config.keyword_retrieval.min_keyword_length,
        )
        self._judge = judge or StubContradictionJudge()

    # ------------------------------------------------------------------
    # Fact writes
    # ------------------------------------------------------------------

    async def add_fact(self, fact: Fact, source: str) -> FactId:
        if fact.confidence < self._config.fact_confidence_floor:
            raise ConfidenceFloorError(
                f"fact confidence {fact.confidence} below floor "
                f"{self._config.fact_confidence_floor}"
            )
        if not fact.id:
            fact = dataclasses.replace(fact, id=_new_id("fact"))
        # Tag attribution into `tags` if not already present.
        if source:
            src_tag = f"src:{source}"
            if src_tag not in fact.tags:
                fact.tags.append(src_tag)
        await self._store.put_fact(fact)
        return fact.id

    async def retire_fact(self, fact_id: FactId, in_post: str, reason: str) -> None:
        fact = await self._store.get_fact(fact_id)
        if fact is None:
            raise FactNotFoundError(fact_id)
        try:
            reason_enum = RetirementReason(reason)
        except ValueError as e:
            raise ValueError(
                f"unknown retirement reason: {reason!r}; "
                f"expected one of {[r.value for r in RetirementReason]}"
            ) from e
        retired = dataclasses.replace(
            fact,
            retired=True,
            retired_in_post=in_post,
            retired_reason=reason_enum,
        )
        await self._store.put_fact(retired)

    async def update_fact(self, fact_id: FactId, patch: dict) -> Fact:
        fact = await self._store.get_fact(fact_id)
        if fact is None:
            raise FactNotFoundError(fact_id)
        # Subject patches need special handling since `about` is a nested dataclass.
        about_patch = patch.pop("about", None)
        new_fact = _patch_dataclass(fact, patch)
        if about_patch is not None:
            if isinstance(about_patch, FactSubject):
                new_fact = dataclasses.replace(new_fact, about=about_patch)
            else:
                new_fact = dataclasses.replace(
                    new_fact, about=_patch_dataclass(fact.about, about_patch)
                )
        await self._store.put_fact(new_fact)
        return new_fact

    # ------------------------------------------------------------------
    # Fact reads
    # ------------------------------------------------------------------

    async def get_fact(self, fact_id: FactId) -> Fact:
        fact = await self._store.get_fact(fact_id)
        if fact is None:
            raise FactNotFoundError(fact_id)
        return fact

    async def facts_about(
        self,
        *,
        character_ids: list[str] | None = None,
        location_ids: list[str] | None = None,
        faction_ids: list[str] | None = None,
        item_ids: list[str] | None = None,
        limit: int = 50,
        include_retired: bool = False,
    ) -> list[Fact]:
        all_facts = await self._store.list_facts(include_retired=include_retired)
        matched = [
            f
            for f in all_facts
            if _matches_subject(
                f,
                character_ids=character_ids,
                location_ids=location_ids,
                faction_ids=faction_ids,
                item_ids=item_ids,
            )
        ]
        matched.sort(key=lambda f: f.established_at_in_game, reverse=True)
        return matched[:limit]

    async def search_facts(self, query: str, top_k: int = 10) -> list[Fact]:
        results = await self._search.search(query, top_k=top_k)
        return [fact for fact, _score in results]

    async def recent_facts(self, since: InGameTime, limit: int = 50) -> list[Fact]:
        all_facts = await self._store.list_facts(include_retired=False)
        recent = [f for f in all_facts if f.established_at_in_game >= since]
        recent.sort(key=lambda f: f.established_at_in_game, reverse=True)
        return recent[:limit]

    # ------------------------------------------------------------------
    # Contradictions
    # ------------------------------------------------------------------

    async def check_contradictions(
        self,
        candidate: Fact,
        *,
        turn_id: TurnId | None = None,
    ) -> ContradictionReport:
        cfg = self._config.contradiction_check
        conflicts: list[ContradictionCandidate] = []
        if cfg.enabled:
            similar = await self._search.search(candidate.text, top_k=cfg.top_k_similar)
            for existing, similarity in similar:
                if existing.id and existing.id == candidate.id:
                    continue
                verdict = await self._judge.judge(candidate, existing, turn_id=turn_id)
                # Use the search-index similarity if the judge didn't supply one.
                if verdict.similarity == 0.0 and similarity > 0.0:
                    verdict = dataclasses.replace(verdict, similarity=similarity)
                if (
                    verdict.verdict
                    in (
                        ContradictionVerdict.CONFLICT,
                        ContradictionVerdict.UNCERTAIN,
                    )
                    and verdict.confidence > 0.0
                ):
                    conflicts.append(verdict)
        # Keep only real conflicts (UNCERTAIN with 0 confidence is filtered above).
        report = ContradictionReport(
            id=_new_id("contra"),
            candidate_fact=candidate,
            conflicts=[c for c in conflicts if c.verdict == ContradictionVerdict.CONFLICT],
        )
        await self._store.put_contradiction_report(report)
        return report

    async def resolve_contradiction(
        self, report_id: ContradictionReportId, resolution: dict
    ) -> None:
        """Apply a user's resolution to a contradiction report.

        Resolution shape:
            {
              "action": "keep_existing" | "replace_existing" | "both_true"
                        | "edit_new" | "edit_existing",
              "in_post": <post_id>,
              "patch": <optional dict, for edit_new / edit_existing>,
              "target_fact_id": <optional, when multiple conflicts>,
            }
        """
        report = await self._store.get_contradiction_report(report_id)
        if report is None:
            raise ContradictionReportNotFoundError(report_id)
        if report.resolved:
            raise ValueError(f"report {report_id} already resolved")

        action = ContradictionResolutionAction(resolution["action"])
        in_post = resolution.get("in_post", "")
        target_id = resolution.get("target_fact_id")
        target_existing: Fact | None = None
        if report.conflicts:
            if target_id is not None:
                for c in report.conflicts:
                    if c.existing_fact.id == target_id:
                        target_existing = c.existing_fact
                        break
                if target_existing is None:
                    raise ValueError(f"target_fact_id {target_id} not in conflicts")
            else:
                target_existing = report.conflicts[0].existing_fact

        candidate = report.candidate_fact

        if action == ContradictionResolutionAction.KEEP_EXISTING:
            # Drop the new fact (do not write it).
            pass
        elif action == ContradictionResolutionAction.REPLACE_EXISTING:
            if target_existing is None:
                raise ValueError("replace_existing requires an existing fact")
            await self.retire_fact(target_existing.id, in_post, RetirementReason.REFUTED.value)
            new_fact = dataclasses.replace(
                candidate,
                id=candidate.id or _new_id("fact"),
                contradicts=[*candidate.contradicts, target_existing.id],
            )
            await self._store.put_fact(new_fact)
        elif action == ContradictionResolutionAction.BOTH_TRUE:
            new_fact = (
                candidate if candidate.id else dataclasses.replace(candidate, id=_new_id("fact"))
            )
            await self._store.put_fact(new_fact)
        elif action == ContradictionResolutionAction.EDIT_NEW:
            patch = resolution.get("patch", {})
            edited = _patch_dataclass(candidate, patch)
            edited = edited if edited.id else dataclasses.replace(edited, id=_new_id("fact"))
            await self._store.put_fact(edited)
        elif action == ContradictionResolutionAction.EDIT_EXISTING:
            if target_existing is None:
                raise ValueError("edit_existing requires an existing fact")
            patch = resolution.get("patch", {})
            edited = _patch_dataclass(target_existing, patch)
            await self._store.put_fact(edited)

        resolved_report = dataclasses.replace(report, resolved=True, resolution=resolution)
        await self._store.put_contradiction_report(resolved_report)

    # ------------------------------------------------------------------
    # Commitments
    # ------------------------------------------------------------------

    async def add_commitment(self, c: Commitment, source: str) -> CommitmentId:
        if not c.id:
            c = dataclasses.replace(c, id=_new_id("com"))
        if source:
            src_tag = f"src:{source}"
            if src_tag not in c.tags:
                c.tags.append(src_tag)
        if c.last_activity_at is None:
            c = dataclasses.replace(c, last_activity_at=c.in_game_created_at)
        await self._store.put_commitment(c)
        return c.id

    async def resolve_commitment(
        self,
        cid: CommitmentId,
        status: CommitmentStatus,
        in_post: str,
    ) -> None:
        existing = await self._store.get_commitment(cid)
        if existing is None:
            raise CommitmentNotFoundError(cid)
        if status == CommitmentStatus.OPEN:
            raise ValueError("cannot resolve a commitment back to OPEN")
        updated = dataclasses.replace(
            existing,
            status=status,
            resolved_in_post=in_post if status in TERMINAL_STATUSES else existing.resolved_in_post,
        )
        await self._store.put_commitment(updated)

    async def get_commitment(self, cid: CommitmentId) -> Commitment:
        c = await self._store.get_commitment(cid)
        if c is None:
            raise CommitmentNotFoundError(cid)
        return c

    async def open_commitments(
        self,
        *,
        involving: list[str] | None = None,
        limit: int = 50,
    ) -> list[Commitment]:
        rows = await self._store.list_commitments(
            statuses=[CommitmentStatus.OPEN, CommitmentStatus.OVERDUE]
        )
        if involving:
            rows = [c for c in rows if _involves(c, involving)]
        rows.sort(
            key=lambda c: (
                -c.weight,
                c.due_by or InGameTime(day_count=10**9),
                c.in_game_created_at,
            )
        )
        return rows[:limit]

    async def overdue_commitments(self, as_of: InGameTime) -> list[Commitment]:
        rows = await self._store.list_commitments()
        out = []
        for c in rows:
            if c.status in TERMINAL_STATUSES:
                continue
            if c.status == CommitmentStatus.STALE:
                continue
            if c.due_by is not None and c.due_by < as_of:
                out.append(c)
        out.sort(key=lambda c: (c.due_by or as_of, -c.weight))
        return out

    async def stale_commitments(self, threshold: Duration) -> list[Commitment]:
        rows = await self._store.list_commitments(statuses=[CommitmentStatus.STALE])
        rows.sort(key=lambda c: c.in_game_created_at)
        # Threshold is taken as advisory: stale flagging is done in age();
        # callers can also use this to surface long-stale items.
        del threshold
        return rows

    # ------------------------------------------------------------------
    # Knowledge
    # ------------------------------------------------------------------

    async def knows(self, character_id: str, fact_id: FactId) -> bool:
        entry = await self._store.get_knowledge(character_id, fact_id)
        return bool(entry and entry.knows)

    async def reveal(
        self,
        fact_id: FactId,
        to: list[str],
        in_post: str,
        source: str,
    ) -> None:
        fact = await self._store.get_fact(fact_id)
        if fact is None:
            raise FactNotFoundError(fact_id)
        for character_id in to:
            await self._store.put_knowledge(
                KnowledgeEntry(
                    fact_id=fact_id,
                    character_id=character_id,
                    knows=True,
                    learned_in_post=in_post,
                    source=source,
                )
            )

    async def secrets_of(self, character_id: str) -> list[Fact]:
        """Facts the given character knows that are tagged `secret`."""
        entries = await self._store.knowledge_for_character(character_id)
        out: list[Fact] = []
        for entry in entries:
            if not entry.knows:
                continue
            fact = await self._store.get_fact(entry.fact_id)
            if fact is None or fact.retired:
                continue
            if "secret" in fact.tags:
                out.append(fact)
        return out

    # ------------------------------------------------------------------
    # Aging
    # ------------------------------------------------------------------

    async def age(self, to_time: InGameTime) -> AgingReport:
        rows = await self._store.list_commitments()
        if not rows:
            return AgingReport(from_time=to_time, to_time=to_time)

        from_time = min(
            (
                c.last_activity_at or c.in_game_created_at
                for c in rows
                if c.status not in TERMINAL_STATUSES
            ),
            default=to_time,
        )

        stale_threshold = self._config.commitment_stale_threshold
        became_overdue: list[Commitment] = []
        became_stale: list[Commitment] = []

        for c in rows:
            if c.status in TERMINAL_STATUSES:
                continue
            new_status = c.status
            # OPEN + due_by passes -> OVERDUE
            if c.status == CommitmentStatus.OPEN and c.due_by is not None and c.due_by < to_time:
                new_status = CommitmentStatus.OVERDUE

            # OPEN + inactivity > threshold AND no due_by -> STALE
            elif c.status == CommitmentStatus.OPEN and c.due_by is None:
                last_active = c.last_activity_at or c.in_game_created_at
                if to_time - last_active >= stale_threshold:
                    new_status = CommitmentStatus.STALE

            if new_status != c.status:
                updated = dataclasses.replace(c, status=new_status)
                await self._store.put_commitment(updated)
                if new_status == CommitmentStatus.OVERDUE:
                    became_overdue.append(updated)
                elif new_status == CommitmentStatus.STALE:
                    became_stale.append(updated)

        return AgingReport(
            from_time=from_time,
            to_time=to_time,
            became_overdue=became_overdue,
            became_stale=became_stale,
        )


__all__ = [
    "CommitmentNotFoundError",
    "ConfidenceFloorError",
    "ContinuityService",
    "ContradictionReportNotFoundError",
    "FactNotFoundError",
]
