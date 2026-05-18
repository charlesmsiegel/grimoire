"""Tests for the methods added in 2026-05-18 (§6, §7, §9, §10, §11)."""

from __future__ import annotations

import pytest

from grimoire.continuity import (
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    ContinuityService,
    InGameTime,
)
from grimoire.continuity.types import KnowledgeEntry, RetirementReason
from tests.continuity.conftest import make_fact

pytestmark = pytest.mark.asyncio


async def test_facts_known_by_filters_to_pov(service: ContinuityService) -> None:
    private_fact = make_fact(
        text="A secret about Alice",
        characters=["alice"],
        scope="private",
    )
    public_fact = make_fact(text="The sky is blue", scope="public")
    private_other = make_fact(
        text="A secret about Bob",
        characters=["bob"],
        scope="private",
    )
    private_alice_id = await service.add_fact(private_fact, source="narrator")
    public_id = await service.add_fact(public_fact, source="narrator")
    await service.add_fact(private_other, source="narrator")

    # Alice doesn't know yet, but the subject is herself -> visible.
    rows = await service.facts_known_by("alice", limit=50)
    texts = {f.text for f in rows}
    assert "A secret about Alice" in texts
    assert "The sky is blue" in texts  # public
    assert "A secret about Bob" not in texts

    # Reveal Bob's secret to Alice -> she now sees it.
    await service.reveal(
        next(f.id for f in await service.facts_about(limit=50) if f.text == "A secret about Bob"),
        ["alice"],
        in_post="p-7",
        source="witnessed",
    )
    rows = await service.facts_known_by("alice", limit=50)
    texts = {f.text for f in rows}
    assert "A secret about Bob" in texts
    # private_alice_id and public_id used to make sure ids are stable.
    assert any(f.id == private_alice_id for f in rows)
    assert any(f.id == public_id for f in rows)


async def test_facts_for_terms_keyword_match(service: ContinuityService) -> None:
    await service.add_fact(make_fact(text="The Tremere maintain the tower."), source="n")
    await service.add_fact(
        make_fact(text="winifred keeps an orchard.", keywords=["winifred", "orchard"]),
        source="n",
    )
    await service.add_fact(make_fact(text="No proper nouns here."), source="n")

    rows = await service.facts_for_terms(["Tremere"], limit=5)
    assert rows and rows[0].text.startswith("The Tremere")

    rows = await service.facts_for_terms(["winifred"], limit=5)
    assert rows and "winifred" in rows[0].text

    # Empty / too-short terms return nothing rather than raising.
    rows = await service.facts_for_terms([], limit=5)
    assert rows == []
    rows = await service.facts_for_terms(["a", "b"], limit=5)
    assert rows == []


async def test_facts_for_terms_respects_min_keyword_length() -> None:
    from grimoire.continuity.config import ContinuityConfig, KeywordRetrievalConfig

    config = ContinuityConfig(
        keyword_retrieval=KeywordRetrievalConfig(min_keyword_length=6),
    )
    service = ContinuityService(config=config)
    await service.add_fact(make_fact(text="Tower keeps secrets."), source="n")
    # "Tower" is 5 chars — below floor.
    assert await service.facts_for_terms(["Tower"], limit=5) == []
    # "Tremere" is 7 — passes.
    await service.add_fact(make_fact(text="Tremere watch silently."), source="n")
    rows = await service.facts_for_terms(["Tremere"], limit=5)
    assert rows


async def test_reopen_commitment_resets_status(service: ContinuityService) -> None:
    cid = await service.add_commitment(
        Commitment(
            id="",
            kind=CommitmentKind.MYSTERY,
            text="Find the witness",
            created_in_post="p-1",
            in_game_created_at=InGameTime(day_count=1),
        ),
        source="user",
    )
    # Age past the stale threshold so the commitment becomes STALE.
    await service.age(InGameTime(day_count=400))
    after_age = await service.get_commitment(cid)
    assert after_age.status == CommitmentStatus.STALE

    # Reopen — should flip back to REOPENED.
    updated = await service.reopen_commitment(cid, in_post="p-9")
    assert updated.status == CommitmentStatus.REOPENED
    assert (await service.get_commitment(cid)).status == CommitmentStatus.REOPENED

    # open_commitments() surfaces REOPENED items now.
    open_rows = await service.open_commitments(limit=50)
    assert any(c.id == cid for c in open_rows)


async def test_reopened_commitment_ages_like_open(service: ContinuityService) -> None:
    cid = await service.add_commitment(
        Commitment(
            id="",
            kind=CommitmentKind.MYSTERY,
            text="Look into this",
            created_in_post="p-1",
            in_game_created_at=InGameTime(day_count=1),
        ),
        source="user",
    )
    await service.age(InGameTime(day_count=400))
    assert (await service.get_commitment(cid)).status == CommitmentStatus.STALE
    await service.reopen_commitment(cid, in_post="p-9")
    # Age again WAY past threshold — should re-stale.
    await service.age(InGameTime(day_count=900))
    assert (await service.get_commitment(cid)).status == CommitmentStatus.STALE


async def test_pending_contradictions_returns_unresolved_only() -> None:
    from grimoire.continuity.protocols import ContradictionJudge
    from grimoire.continuity.types import ContradictionCandidate, ContradictionVerdict

    class ConflictJudge(ContradictionJudge):
        async def judge(self, candidate, existing, *, turn_id=None):
            return ContradictionCandidate(
                existing_fact=existing,
                similarity=0.9,
                verdict=ContradictionVerdict.CONFLICT,
                confidence=0.9,
                rationale="forced",
            )

    service = ContinuityService(judge=ConflictJudge())
    await service.add_fact(make_fact(text="apple"), source="n")
    report = await service.check_contradictions(make_fact(text="apple again"))
    assert report.conflicts

    pending = await service.pending_contradictions(limit=10)
    assert any(r.id == report.id for r in pending)

    await service.resolve_contradiction(
        report.id, {"action": "keep_existing", "in_post": "p-9"}
    )
    pending = await service.pending_contradictions(limit=10)
    assert not any(r.id == report.id for r in pending)


async def test_brief_for_scene_returns_threads_for_pcs(service: ContinuityService) -> None:
    await service.add_fact(
        make_fact(text="winifred knows about the orchard.", characters=["winifred"]),
        source="n",
    )
    cid = await service.add_commitment(
        Commitment(
            id="",
            kind=CommitmentKind.PROMISE,
            text="winifred will return the heirloom.",
            created_in_post="p-1",
            in_game_created_at=InGameTime(day_count=1),
            from_id="winifred",
            to_id="rosaline",
        ),
        source="user",
    )

    briefing = await service.brief_for_scene("scene-1", ["winifred"])
    assert briefing.scene_id == "scene-1"
    assert briefing.pc_refs == ["winifred"]
    assert any(f.text.startswith("winifred") for f in briefing.facts)
    assert any(c.id == cid for c in briefing.commitments)


async def test_all_commitments_returns_any_status(service: ContinuityService) -> None:
    cid_open = await service.add_commitment(
        Commitment(
            id="",
            kind=CommitmentKind.PROMISE,
            text="Open thread",
            created_in_post="p-1",
            in_game_created_at=InGameTime(day_count=1),
        ),
        source="user",
    )
    cid_paid = await service.add_commitment(
        Commitment(
            id="",
            kind=CommitmentKind.PROMISE,
            text="Paid thread",
            created_in_post="p-2",
            in_game_created_at=InGameTime(day_count=2),
        ),
        source="user",
    )
    await service.resolve_commitment(cid_paid, CommitmentStatus.PAID, "p-5")
    rows = await service.all_commitments()
    ids = {c.id for c in rows}
    assert cid_open in ids
    assert cid_paid in ids


async def test_in_memory_list_contradiction_reports() -> None:
    from grimoire.continuity import InMemoryContinuityStore
    from grimoire.continuity.types import ContradictionReport

    store = InMemoryContinuityStore()
    for i, resolved in enumerate([True, False, False]):
        report = ContradictionReport(
            id=f"r{i}",
            candidate_fact=make_fact(text=f"f{i}"),
            conflicts=[],
            resolved=resolved,
        )
        await store.put_contradiction_report(report)
    all_reports = await store.list_contradiction_reports()
    assert {r.id for r in all_reports} == {"r0", "r1", "r2"}
    unresolved = await store.list_contradiction_reports(resolved=False)
    assert {r.id for r in unresolved} == {"r1", "r2"}
    resolved_only = await store.list_contradiction_reports(resolved=True)
    assert {r.id for r in resolved_only} == {"r0"}


_ = KnowledgeEntry  # silence import warning
_ = RetirementReason
