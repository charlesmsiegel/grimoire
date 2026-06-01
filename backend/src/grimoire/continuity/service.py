"""Concrete Continuity implementation.

Wires together a `ContinuityStore`, a `FactSearchIndex` and a
`ContradictionJudge`. Pure asyncio; no I/O of its own. Time enters from the
outside via `age(to_time)` calls (the Time Engine, task #21).
"""

from __future__ import annotations

import dataclasses
import logging
import re
from collections.abc import Iterable

from grimoire import events
from grimoire.continuity.config import ContinuityConfig
from grimoire.continuity.errors import (
    CommitmentNotFoundError,
    ConfidenceFloorError,
    ContradictionReportNotFoundError,
    FactNotFoundError,
)
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
    AGES_LIKE_OPEN,
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
    SceneBriefing,
)
from grimoire.event_bus import Event, EventBus
from grimoire.types.common import TurnId
from grimoire.util import new_id

logger = logging.getLogger(__name__)

_TERM_RE = re.compile(r"\w+", re.UNICODE)

__all__ = [
    "CommitmentNotFoundError",
    "ConfidenceFloorError",
    "ContradictionReportNotFoundError",
    "FactNotFoundError",
]


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
        event_bus: EventBus | None = None,
        campaign_id: str | None = None,
    ) -> None:
        self._store = store or InMemoryContinuityStore()
        self._config = config or ContinuityConfig()
        self._search = search_index or KeywordFactSearchIndex(
            self._store,
            min_keyword_length=self._config.keyword_retrieval.min_keyword_length,
        )
        self._judge = judge or StubContradictionJudge()
        self._event_bus = event_bus
        self._campaign_id = campaign_id

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    async def _emit(self, event_type: str, payload: dict) -> None:
        """Best-effort event emission. Swallows handler exceptions so
        Continuity writes never fail because of a bus subscriber bug."""
        if self._event_bus is None:
            return
        body = dict(payload)
        if self._campaign_id is not None and "campaign_id" not in body:
            body["campaign_id"] = self._campaign_id
        try:
            await self._event_bus.emit(Event(type=event_type, payload=body))
        except Exception:
            logger.exception("continuity event emit failed for %s", event_type)

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
            fact = dataclasses.replace(fact, id=new_id("fact"))
        # Tag attribution into `tags` if not already present. Build a new
        # list so callers reusing the same Fact dataclass don't accumulate
        # src: tags from prior writes.
        if source:
            src_tag = f"src:{source}"
            if src_tag not in fact.tags:
                fact = dataclasses.replace(fact, tags=[*fact.tags, src_tag])
        await self._store.put_fact(fact)
        await self._emit(
            events.FACT_RECORDED,
            {"fact_id": fact.id, "source": source},
        )
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

    async def retract_turn(self, turn_id: str) -> dict:
        """Retract continuity writes made in ``turn_id`` (cascade delete / undo).

        Continuity facts/commitments/knowledge bypass the reversible delta log
        (the DeltaApplier routes them straight here), so deleting a turn's posts
        leaves them applied unless we undo them explicitly. This reverses every
        such write attributable to the turn:

          - facts *established* in the turn are retired (RETCONNED);
          - commitments *created* in the turn are deleted;
          - facts *retired* in the turn (but established earlier) are un-retired,
            so a FACT_RETIRE from the deleted turn no longer hides a surviving
            fact;
          - commitments *resolved* in the turn (but created earlier) are reopened
            to OPEN, reversing a COMMITMENT_RESOLVE from the deleted turn;
          - knowledge *revealed* in the turn is removed, reversing KNOWLEDGE_REVEAL.

        FACT_UPDATE patches are *not* reversed: no pre-image is stored, so a field
        edited during the turn keeps its new value. Returns the ids touched.
        """
        retired_facts: list[str] = []
        unretired_facts: list[str] = []
        for fact in await self._store.list_facts(include_retired=True):
            if fact.established_in_post == turn_id:
                if not fact.retired:
                    await self.retire_fact(fact.id, in_post=turn_id, reason="retconned")
                retired_facts.append(fact.id)
            elif fact.retired and fact.retired_in_post == turn_id:
                restored = dataclasses.replace(
                    fact, retired=False, retired_in_post=None, retired_reason=None
                )
                await self._store.put_fact(restored)
                unretired_facts.append(fact.id)
        removed_commitments: list[str] = []
        reopened_commitments: list[str] = []
        for commitment in await self._store.list_commitments():
            if commitment.created_in_post == turn_id:
                await self._store.delete_commitment(commitment.id)
                removed_commitments.append(commitment.id)
            elif commitment.resolved_in_post == turn_id:
                restored = dataclasses.replace(
                    commitment, status=CommitmentStatus.OPEN, resolved_in_post=None
                )
                await self._store.put_commitment(restored)
                reopened_commitments.append(commitment.id)
        removed_knowledge: list[str] = []
        learned_in = getattr(self._store, "knowledge_learned_in", None)
        delete_knowledge = getattr(self._store, "delete_knowledge", None)
        if learned_in is not None and delete_knowledge is not None:
            for entry in await learned_in(turn_id):
                await delete_knowledge(entry.character_id, entry.fact_id)
                removed_knowledge.append(f"{entry.character_id}:{entry.fact_id}")
        return {
            "retired_facts": retired_facts,
            "removed_commitments": removed_commitments,
            "unretired_facts": unretired_facts,
            "reopened_commitments": reopened_commitments,
            "removed_knowledge": removed_knowledge,
        }

    async def turn_has_continuity_writes(self, turn_id: str) -> bool:
        """Whether ``turn_id`` made any continuity write that bypasses the
        reversible delta log (a fact established or retired, a commitment created
        or resolved, or knowledge revealed).

        Read-only. Cascade delete uses this to *warn* about a straddling turn:
        such a turn keeps its continuity writes (a post survives) but those
        writes are attributed to the whole turn, not individual posts, so we
        can't tell which were established by the deleted segment. We surface a
        warning rather than guess.
        """
        for fact in await self._store.list_facts(include_retired=True):
            if fact.established_in_post == turn_id or (
                fact.retired and fact.retired_in_post == turn_id
            ):
                return True
        for commitment in await self._store.list_commitments():
            if commitment.created_in_post == turn_id or commitment.resolved_in_post == turn_id:
                return True
        learned_in = getattr(self._store, "knowledge_learned_in", None)
        if learned_in is None:
            return False
        return bool(await learned_in(turn_id))

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

    async def facts_known_by(
        self,
        character_id: str,
        *,
        limit: int = 50,
        include_retired: bool = False,
    ) -> list[Fact]:
        """Return the facts a given character knows about.

        Used by Context Builder to enforce POV: a fact the active PC
        doesn't know shouldn't seed model reactions. Public facts (i.e.
        anything with ``about.scope == "public"`` and no narrow audience
        in ``about.character_ids``) are returned unconditionally so the
        narrator can still describe what's in the air.
        """
        entries = await self._store.knowledge_for_character(character_id)
        known_ids = {e.fact_id for e in entries if e.knows}
        all_facts = await self._store.list_facts(include_retired=include_retired)
        out: list[Fact] = []
        for fact in all_facts:
            if fact.id in known_ids:
                out.append(fact)
                continue
            subject = fact.about
            # No narrow audience and scope is world-level public — everyone
            # is presumed to be able to know it. Private/household scope
            # requires explicit knowledge.
            if subject.scope in ("public", "world") and not subject.character_ids:
                out.append(fact)
                continue
            # Subject explicitly lists this PC — they know about facts
            # about themselves.
            if character_id in subject.character_ids:
                out.append(fact)
        out.sort(key=lambda f: f.established_at_in_game, reverse=True)
        return out[:limit]

    async def facts_for_terms(
        self,
        terms: Iterable[str],
        *,
        limit: int = 10,
    ) -> list[Fact]:
        """Return facts whose text or keywords match any of ``terms``.

        Honours ``config.keyword_retrieval.case_insensitive`` and
        ``min_keyword_length`` so the prose-driven retrieval path agrees
        with the existing search index on what counts as a token.
        """
        cfg = self._config.keyword_retrieval
        normalised: set[str] = set()
        for raw in terms:
            for tok in _TERM_RE.findall(raw):
                if len(tok) < cfg.min_keyword_length:
                    continue
                normalised.add(tok.lower() if cfg.case_insensitive else tok)
        if not normalised:
            return []
        all_facts = await self._store.list_facts(include_retired=False)
        matched: list[tuple[Fact, int]] = []
        for fact in all_facts:
            haystack: set[str] = set()
            for tok in _TERM_RE.findall(fact.text):
                if len(tok) < cfg.min_keyword_length:
                    continue
                haystack.add(tok.lower() if cfg.case_insensitive else tok)
            for kw in fact.keywords:
                if not kw:
                    continue
                haystack.add(kw.lower() if cfg.case_insensitive else kw)
            overlap = len(normalised & haystack)
            if overlap > 0:
                matched.append((fact, overlap))
        matched.sort(
            key=lambda pair: (-pair[1], -pair[0].established_at_in_game.day_count),
        )
        return [fact for fact, _ in matched[:limit]]

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
            id=new_id("contra"),
            candidate_fact=candidate,
            conflicts=[c for c in conflicts if c.verdict == ContradictionVerdict.CONFLICT],
        )
        await self._store.put_contradiction_report(report)
        if report.conflicts:
            await self._emit(
                events.CONTRADICTION_DETECTED,
                {"report_id": report.id, "conflict_count": len(report.conflicts)},
            )
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
                id=candidate.id or new_id("fact"),
                contradicts=[*candidate.contradicts, target_existing.id],
            )
            await self._store.put_fact(new_fact)
        elif action == ContradictionResolutionAction.BOTH_TRUE:
            new_fact = (
                candidate if candidate.id else dataclasses.replace(candidate, id=new_id("fact"))
            )
            await self._store.put_fact(new_fact)
        elif action == ContradictionResolutionAction.EDIT_NEW:
            patch = resolution.get("patch", {})
            edited = _patch_dataclass(candidate, patch)
            edited = edited if edited.id else dataclasses.replace(edited, id=new_id("fact"))
            await self._store.put_fact(edited)
        elif action == ContradictionResolutionAction.EDIT_EXISTING:
            if target_existing is None:
                raise ValueError("edit_existing requires an existing fact")
            patch = resolution.get("patch", {})
            edited = _patch_dataclass(target_existing, patch)
            await self._store.put_fact(edited)

        resolved_report = dataclasses.replace(report, resolved=True, resolution=resolution)
        await self._store.put_contradiction_report(resolved_report)

    async def pending_contradictions(self, limit: int = 20) -> list[ContradictionReport]:
        """Return unresolved contradiction reports for the campaign ledger UI."""
        return await self._store.list_contradiction_reports(resolved=False, limit=limit)

    # ------------------------------------------------------------------
    # Commitments
    # ------------------------------------------------------------------

    async def add_commitment(self, c: Commitment, source: str) -> CommitmentId:
        if not c.id:
            c = dataclasses.replace(c, id=new_id("com"))
        if source:
            src_tag = f"src:{source}"
            if src_tag not in c.tags:
                c = dataclasses.replace(c, tags=[*c.tags, src_tag])
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
        # A commitment that is *already* terminal was resolved by an earlier
        # (surviving) turn; keep that turn's resolved_in_post so a later
        # duplicate resolution doesn't reattribute it. Otherwise retract_turn,
        # which reopens commitments whose resolved_in_post matches the deleted
        # turn, would erase a resolution that still stands. A fresh resolution
        # (existing was OPEN/STALE/…) records this turn as before.
        already_terminal = (
            existing.status in TERMINAL_STATUSES and existing.resolved_in_post is not None
        )
        if already_terminal:
            resolved_in_post = existing.resolved_in_post
        elif status in TERMINAL_STATUSES:
            resolved_in_post = in_post
        else:
            resolved_in_post = existing.resolved_in_post
        updated = dataclasses.replace(
            existing,
            status=status,
            resolved_in_post=resolved_in_post,
        )
        await self._store.put_commitment(updated)
        if status == CommitmentStatus.PAID:
            await self._emit(
                events.COMMITMENT_PAID_OFF,
                {"commitment_id": cid, "in_post": in_post},
            )
        elif status == CommitmentStatus.BROKEN:
            await self._emit(
                events.COMMITMENT_BROKEN,
                {"commitment_id": cid, "in_post": in_post},
            )

    async def reopen_commitment(
        self,
        cid: CommitmentId,
        in_post: str,
    ) -> Commitment:
        """Mark a STALE (or terminal) commitment as relevant again.

        Returns the updated record so callers don't have to refetch.
        """
        existing = await self._store.get_commitment(cid)
        if existing is None:
            raise CommitmentNotFoundError(cid)
        updated = dataclasses.replace(
            existing,
            status=CommitmentStatus.REOPENED,
            # last_activity_at is what aging uses to decide "is this stale
            # again?"; bumping it on reopen prevents an immediate flip
            # back to STALE on the next age() pass.
            last_activity_at=existing.last_activity_at,
            resolved_in_post=None,
        )
        await self._store.put_commitment(updated)
        await self._emit(
            events.COMMITMENT_REOPENED,
            {"commitment_id": cid, "in_post": in_post},
        )
        return updated

    async def all_commitments(self) -> list[Commitment]:
        """Return every commitment in the store (any status).

        Used by the EPUB export appendix which renders the full ledger.
        """
        rows = await self._store.list_commitments()
        rows.sort(key=lambda c: c.in_game_created_at)
        return rows

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
            statuses=[
                CommitmentStatus.OPEN,
                CommitmentStatus.OVERDUE,
                CommitmentStatus.REOPENED,
            ]
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

    async def brief_for_scene(
        self,
        scene_id: str,
        pc_refs: list[str],
        *,
        as_of: InGameTime | None = None,
    ) -> SceneBriefing:
        """Compact "active threads involving these PCs" bundle.

        Scene Manager calls this when opening a scene so the narrator's
        first beat lands on threads the cast already cares about. The
        returned ``commitments`` list keeps OPEN + REOPENED rows; the
        ``overdue`` field separates those past their due date, and
        ``facts`` is the most recent ledger entries about (or known by)
        the requested PCs.
        """
        commitments = await self.open_commitments(involving=pc_refs, limit=20)
        overdue: list[Commitment] = []
        if as_of is not None:
            all_overdue = await self.overdue_commitments(as_of)
            wanted = set(pc_refs)
            overdue = [c for c in all_overdue if not wanted or _involves(c, wanted)]
        facts: list[Fact] = []
        if pc_refs:
            facts = await self.facts_about(character_ids=list(pc_refs), limit=10)
        return SceneBriefing(
            scene_id=scene_id,
            pc_refs=list(pc_refs),
            facts=facts,
            commitments=commitments,
            overdue=overdue,
        )

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
            # A character can't *re-learn* something they already know: keep the
            # original learning attribution (learned_in_post / source) rather
            # than overwriting it with this turn's. Otherwise a duplicate /
            # confirming reveal in a later turn would re-stamp learned_in_post,
            # and cascade-deleting that turn (retract_turn → knowledge_learned_in
            # → delete_knowledge) would then drop knowledge the character had
            # actually learned from surviving evidence.
            existing = await self._store.get_knowledge(character_id, fact_id)
            if existing is not None and existing.knows:
                continue
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
            # OPEN / REOPENED + due_by passes -> OVERDUE
            if c.status in AGES_LIKE_OPEN and c.due_by is not None and c.due_by < to_time:
                new_status = CommitmentStatus.OVERDUE

            # OPEN / REOPENED + inactivity > threshold AND no due_by -> STALE
            elif c.status in AGES_LIKE_OPEN and c.due_by is None:
                last_active = c.last_activity_at or c.in_game_created_at
                if to_time - last_active >= stale_threshold:
                    new_status = CommitmentStatus.STALE

            if new_status != c.status:
                updated = dataclasses.replace(c, status=new_status)
                await self._store.put_commitment(updated)
                if new_status == CommitmentStatus.OVERDUE:
                    became_overdue.append(updated)
                    await self._emit(
                        events.COMMITMENT_OVERDUE,
                        {"commitment_id": updated.id},
                    )
                elif new_status == CommitmentStatus.STALE:
                    became_stale.append(updated)
                    await self._emit(
                        events.COMMITMENT_STALE,
                        {"commitment_id": updated.id},
                    )

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
