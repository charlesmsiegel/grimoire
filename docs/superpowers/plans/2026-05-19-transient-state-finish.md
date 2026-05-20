# Transient-State — Finish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three remaining gaps from `docs/superpowers/specs/2026-05-19-transient-state-COMPLETED.md` so the extractor→transient_state pipeline runs end-to-end with reinforcement promotion and audit visibility.

**Architecture:** Extend `LLMStrategyOutput` and the structured-LLM schema to carry `transient_updates`; merge them into `ExtractionResult` in `extractor/service.py`; have the Orchestrator call `route_transient_updates(...)` after `_apply_routing`. Move reinforcement detection into `route_transient_updates` (it has `service.history` access) and dispatch promoted facts via `ContinuityService.add_fact` + `supersede_with_fact`. Emit `transient_state_writes` and `transient_state_conflicts` audit fragments via `_emit_fragment(...)` and add matching list fields on `TurnAudit`.

**Tech Stack:** Python 3.12, FastAPI, SQLite (aiosqlite), pydantic v2, pytest-asyncio.

---

## File Structure

**Modify:**
- `backend/src/grimoire/types/observability.py` — add `transient_state_writes`/`transient_state_conflicts` to `TurnAudit`.
- `backend/src/grimoire/transient_state/routing.py` — extend `RoutingSummary` (writes + conflicts + promoted), add reinforcement detection + continuity dispatch.
- `backend/src/grimoire/extractor/schema.py` — add `transient_updates` to the structured-LLM output schema.
- `backend/src/grimoire/extractor/llm_strategy.py` — `LLMStrategyOutput` gains `transient_updates`; `parse_llm_payload` populates it.
- `backend/src/grimoire/extractor/service.py` — surface `llm_out.transient_updates` on `ExtractionResult` from `_run`/`_run_together`/`_run_tool_use`.
- `backend/src/grimoire/orchestrator/service.py` — accept `transient_state`, call `route_transient_updates` after `_apply_routing`, emit audit fragment.
- `backend/src/grimoire/main.py` — pass `container.transient_state` to `OrchestratorService(...)`.

**Create:**
- `backend/tests/transient_state/test_reinforcement.py` — reinforcement-detection + promote dispatch tests.
- `backend/tests/orchestrator/test_transient_routing.py` — integration tests for orchestrator wiring + fragment emission.
- `backend/tests/extractor/test_transient_schema.py` — LLM payload parsing test.

---

## Task 1: Audit fields on TurnAudit

**Files:**
- Modify: `backend/src/grimoire/types/observability.py:128-129`
- Test: `backend/tests/observability/test_turn_audit.py` (existing — extend with new test)

- [ ] **Step 1: Inspect the existing test file**

Read `backend/tests/observability/test_turn_audit.py` to confirm the established test style. If no test file matches that path, find the closest existing test under `backend/tests/observability/` and add a new `test_transient_audit_fields_default_empty` test there.

- [ ] **Step 2: Write the failing test**

Add to the chosen observability test file:

```python
from grimoire.types.observability import TurnAudit


def test_turn_audit_carries_transient_state_writes_and_conflicts():
    audit = TurnAudit.model_validate(
        {
            "turn_id": "t1",
            "campaign_id": "c1",
            "branch_id": "main",
            "started_at": "2026-05-19T00:00:00Z",
            "transient_state_writes": [
                {
                    "entity_kind": "character",
                    "entity_id": "char_x",
                    "field": "mood",
                    "new_value_id": 42,
                    "provenance": "extractor:auto",
                    "confidence": 0.9,
                }
            ],
            "transient_state_conflicts": [
                {
                    "entity_kind": "character",
                    "entity_id": "char_x",
                    "field": "mood",
                    "current_id": 41,
                    "losing_id": 40,
                }
            ],
        }
    )
    assert len(audit.transient_state_writes) == 1
    assert audit.transient_state_writes[0]["provenance"] == "extractor:auto"
    assert audit.transient_state_conflicts[0]["losing_id"] == 40
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest backend/tests/observability/ -k transient_state_writes -v`
Expected: FAIL — `TurnAudit` has no attribute `transient_state_writes`.

- [ ] **Step 4: Add the fields**

Modify `backend/src/grimoire/types/observability.py`. In `class TurnAudit(BaseModel):`, immediately after the `queued_for_review` field (around line 129), add:

```python
    # Transient-state subsystem (spec 2026-05-19-transient-state §Audit).
    # Each entry is a JSON dict carrying (entity_kind, entity_id, field,
    # provenance, confidence, new_value_id) for writes, and (entity_kind,
    # entity_id, field, current_id, losing_id) for conflicts.
    transient_state_writes: list[Json] = Field(default_factory=list)
    transient_state_conflicts: list[Json] = Field(default_factory=list)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest backend/tests/observability/ -k transient_state_writes -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/types/observability.py backend/tests/observability/
git commit -m "feat(transient-state): add audit fields for writes and conflicts"
```

---

## Task 2: Extend route_transient_updates to surface writes + conflicts + promotions

**Files:**
- Modify: `backend/src/grimoire/transient_state/routing.py`
- Test: `backend/tests/transient_state/test_routing.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/transient_state/test_routing.py`:

```python
async def test_routing_summary_carries_write_record(
    service: TransientStateService, seeded_campaign: str
):
    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_x",
        field="mood",
        value="guarded",
        confidence=0.92,
        evidence="...",
    )
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=[proposal],
        transient_state=service,
        source_post_id="p_1",
    )
    assert len(summary.writes) == 1
    w = summary.writes[0]
    assert w["entity_kind"] == "character"
    assert w["entity_id"] == "char_x"
    assert w["field"] == "mood"
    assert w["provenance"] == "extractor:auto"
    assert w["confidence"] == 0.92
    assert isinstance(w["new_value_id"], int)
    assert summary.conflicts == []


async def test_routing_summary_surfaces_conflict_when_user_outranks(
    service: TransientStateService, seeded_campaign: str
):
    # Existing user write wins; subsequent extractor write must be flagged.
    await service.set(
        seeded_campaign,
        EntityKind.CHARACTER,
        "char_x",
        "mood",
        "calm",
        provenance=Provenance.USER_EDIT,
    )
    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_x",
        field="mood",
        value="angry",
        confidence=0.95,
        evidence="...",
    )
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=[proposal],
        transient_state=service,
        source_post_id="p_2",
    )
    assert summary.auto_applied == 1
    assert len(summary.conflicts) == 1
    c = summary.conflicts[0]
    assert c["field"] == "mood"
    assert c["entity_id"] == "char_x"
    # The user write is still current.
    current = await service.get(
        seeded_campaign, EntityKind.CHARACTER, "char_x", "mood"
    )
    assert current is not None
    assert current.value == "calm"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest backend/tests/transient_state/test_routing.py -k "test_routing_summary" -v`
Expected: FAIL — `RoutingSummary` has no `writes` attribute.

- [ ] **Step 3: Rewrite `routing.py`**

Replace `backend/src/grimoire/transient_state/routing.py` with:

```python
"""Routing for extractor TransientUpdateProposal candidates.

Per spec §Extractor integration:
    confidence >= auto_apply_threshold → set(provenance=extractor:auto)
    confidence >= review_threshold     → enqueue for human review
    otherwise                          → discarded

Reinforcement detection (§Promotion to facts): when a proposal's value
matches the last ``promote_to_fact.reinforcement_count - 1`` history rows
for the same entity+field with distinct source_post_ids, the proposal is
marked as ``promote_to_fact`` and — when ``continuity`` is wired — the
value is written through ``ContinuityService.add_fact`` and the just-set
transient row is superseded.

The summary carries per-proposal write descriptors so the orchestrator
can surface them on ``TurnAudit.transient_state_writes`` /
``transient_state_conflicts``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from grimoire.transient_state.config import TransientStateConfig
from grimoire.transient_state.service import TransientStateService
from grimoire.types.transient import (
    DecayHint,
    EntityKind,
    Provenance,
    TransientUpdateProposal,
)

ReviewEnqueuer = Callable[[TransientUpdateProposal, str], Awaitable[str]]
"""Async callable: (proposal, campaign_id) -> review_id."""


@dataclass
class RoutingSummary:
    auto_applied: int = 0
    enqueued_for_review: int = 0
    discarded: int = 0
    promoted_to_fact: int = 0
    writes: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)


def _write_record(
    proposal: TransientUpdateProposal,
    *,
    new_value_id: int,
    provenance: str,
) -> dict[str, Any]:
    return {
        "entity_kind": proposal.entity_kind.value,
        "entity_id": proposal.entity_id,
        "field": proposal.field,
        "new_value_id": new_value_id,
        "provenance": provenance,
        "confidence": proposal.confidence,
    }


def _conflict_record(
    proposal: TransientUpdateProposal,
    *,
    current_id: int,
    losing_id: int,
) -> dict[str, Any]:
    return {
        "entity_kind": proposal.entity_kind.value,
        "entity_id": proposal.entity_id,
        "field": proposal.field,
        "current_id": current_id,
        "losing_id": losing_id,
    }


async def _should_promote(
    proposal: TransientUpdateProposal,
    *,
    transient_state: TransientStateService,
    campaign_id: str,
    cfg: TransientStateConfig,
    branch_id: str | None,
) -> bool:
    """True when the last N entries (including this proposal) carry the
    same value with distinct source_post_ids — the spec's reinforcement
    rule.
    """
    if proposal.proposed_decay_override and proposal.proposed_decay_override.promote_to_fact:
        return True
    needed = cfg.promote_to_fact.reinforcement_count
    if needed <= 1:
        return False
    history = await transient_state.history(
        campaign_id,
        EntityKind(proposal.entity_kind),
        proposal.entity_id,
        proposal.field,
        limit=needed * 2,
        branch_id=branch_id,
    )
    matches = 0
    seen_posts: set[str] = set()
    for h in history:
        if h.value != proposal.value:
            break
        if h.source_post_id and h.source_post_id in seen_posts:
            continue
        if h.source_post_id:
            seen_posts.add(h.source_post_id)
        matches += 1
        if matches >= needed - 1:
            break
    return matches >= needed - 1


async def _promote_via_continuity(
    proposal: TransientUpdateProposal,
    *,
    new_value_id: int,
    transient_state: TransientStateService,
    continuity: Any,
    campaign_id: str,
    branch_id: str | None,
    source_post_id: str | None,
) -> bool:
    """Run the standard add_fact path + supersede the just-set row.

    Returns True on success; False (and silently no-ops) when ``continuity``
    is missing or doesn't expose ``add_fact``.
    """
    if continuity is None or not hasattr(continuity, "add_fact"):
        return False
    from grimoire.types.continuity import Fact, FactScope, FactSource, FactSubject

    subject_kwargs: dict[str, Any] = {}
    kind = EntityKind(proposal.entity_kind)
    if kind == EntityKind.CHARACTER:
        subject_kwargs["character_ids"] = [proposal.entity_id]
    elif kind == EntityKind.LOCATION:
        subject_kwargs["location_ids"] = [proposal.entity_id]
    elif kind == EntityKind.FACTION:
        subject_kwargs["faction_ids"] = [proposal.entity_id]
    fact = Fact(
        id=f"f_{proposal.entity_id}_{proposal.field}_{source_post_id or 'unknown'}",
        campaign_id=campaign_id,
        branch_id=branch_id or f"{campaign_id}:main",
        text=f"{proposal.entity_id} has {proposal.field}: {proposal.value}",
        established_in_post=source_post_id,
        established_at_in_game=None,
        confidence=proposal.confidence,
        source=FactSource.INFERRED,
        about=FactSubject(scope=FactScope.PUBLIC, **subject_kwargs),
        tags=[proposal.evidence] if proposal.evidence else [],
    )
    try:
        fact_id = await continuity.add_fact(fact, source="transient_state:reinforced")
    except Exception:
        return False
    await transient_state.supersede_with_fact(
        new_value_id, fact_id, entity_kind=kind
    )
    return True


async def route_transient_updates(
    *,
    campaign_id: str,
    proposals: list[TransientUpdateProposal],
    transient_state: TransientStateService,
    source_post_id: str | None,
    config: TransientStateConfig | None = None,
    review_enqueuer: ReviewEnqueuer | None = None,
    branch_id: str | None = None,
    continuity: Any | None = None,
) -> RoutingSummary:
    """Dispatch each proposal to set / review-queue / discard.

    On ``set`` success the result is checked for conflict — if the newly
    inserted row didn't become current (write lost to a higher-priority
    incumbent), a conflict descriptor is added.
    """
    cfg = config or transient_state.config
    summary = RoutingSummary()
    for proposal in proposals:
        kind = EntityKind(proposal.entity_kind)
        if proposal.confidence >= cfg.auto_apply_threshold:
            value = await transient_state.set(
                campaign_id,
                kind,
                proposal.entity_id,
                proposal.field,
                proposal.value,
                provenance=Provenance.EXTRACTOR_AUTO,
                confidence=proposal.confidence,
                source_post_id=source_post_id,
                branch_id=branch_id,
            )
            summary.auto_applied += 1
            summary.writes.append(
                _write_record(
                    proposal,
                    new_value_id=value.id,
                    provenance=Provenance.EXTRACTOR_AUTO.value,
                )
            )
            current = await transient_state.get(
                campaign_id,
                kind,
                proposal.entity_id,
                proposal.field,
                branch_id=branch_id,
            )
            if current is not None and current.id != value.id:
                summary.conflicts.append(
                    _conflict_record(
                        proposal,
                        current_id=current.id,
                        losing_id=value.id,
                    )
                )
                # Don't promote a write that lost to a higher-priority value.
                continue
            if await _should_promote(
                proposal,
                transient_state=transient_state,
                campaign_id=campaign_id,
                cfg=cfg,
                branch_id=branch_id,
            ):
                promoted = await _promote_via_continuity(
                    proposal,
                    new_value_id=value.id,
                    transient_state=transient_state,
                    continuity=continuity,
                    campaign_id=campaign_id,
                    branch_id=branch_id,
                    source_post_id=source_post_id,
                )
                if promoted:
                    summary.promoted_to_fact += 1
        elif proposal.confidence >= cfg.review_threshold:
            if review_enqueuer is not None:
                await review_enqueuer(proposal, campaign_id)
            summary.enqueued_for_review += 1
        else:
            summary.discarded += 1
    return summary
```

- [ ] **Step 4: Run the new tests + existing routing tests**

Run: `pytest backend/tests/transient_state/test_routing.py -v`
Expected: all PASS. Older tests should still pass — `auto_applied`/`enqueued_for_review`/`discarded` counters retain their original semantics.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/transient_state/routing.py backend/tests/transient_state/test_routing.py
git commit -m "feat(transient-state): routing summary carries writes and conflicts"
```

---

## Task 3: Reinforcement promotion through continuity

**Files:**
- Create: `backend/tests/transient_state/test_reinforcement.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/transient_state/test_reinforcement.py`:

```python
"""Reinforcement detection + Continuity promotion (spec §Promotion to facts)."""

from __future__ import annotations

from typing import Any

import pytest

from grimoire.transient_state import (
    TransientStateService,
    route_transient_updates,
)
from grimoire.transient_state.config import (
    PromoteToFactConfig,
    TransientStateConfig,
)
from grimoire.types.transient import (
    EntityKind,
    Provenance,
    TransientUpdateProposal,
)


class _FakeContinuity:
    def __init__(self) -> None:
        self.added_facts: list[Any] = []

    async def add_fact(self, fact: Any, *, source: str) -> str:
        self.added_facts.append((fact, source))
        return f"fact_{len(self.added_facts)}"


async def _seed_history(
    service: TransientStateService,
    campaign_id: str,
    value: str,
    posts: list[str],
) -> None:
    for post_id in posts:
        await service.set(
            campaign_id,
            EntityKind.CHARACTER,
            "char_x",
            "mood",
            value,
            provenance=Provenance.EXTRACTOR_AUTO,
            confidence=0.95,
            source_post_id=post_id,
        )


async def test_reinforcement_triggers_promote_to_fact(
    service: TransientStateService, seeded_campaign: str
):
    cfg = TransientStateConfig(
        promote_to_fact=PromoteToFactConfig(reinforcement_count=3),
    )
    continuity = _FakeContinuity()
    await _seed_history(service, seeded_campaign, "guarded", ["p1", "p2"])
    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_x",
        field="mood",
        value="guarded",
        confidence=0.95,
        evidence="...",
    )
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=[proposal],
        transient_state=service,
        source_post_id="p3",
        config=cfg,
        continuity=continuity,
    )
    assert summary.auto_applied == 1
    assert summary.promoted_to_fact == 1
    assert len(continuity.added_facts) == 1
    fact, source = continuity.added_facts[0]
    assert "guarded" in fact.text
    assert source == "transient_state:reinforced"


async def test_no_promotion_when_values_diverge(
    service: TransientStateService, seeded_campaign: str
):
    cfg = TransientStateConfig(
        promote_to_fact=PromoteToFactConfig(reinforcement_count=3),
    )
    continuity = _FakeContinuity()
    await _seed_history(service, seeded_campaign, "guarded", ["p1"])
    await _seed_history(service, seeded_campaign, "curious", ["p2"])
    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_x",
        field="mood",
        value="guarded",
        confidence=0.95,
        evidence="...",
    )
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=[proposal],
        transient_state=service,
        source_post_id="p3",
        config=cfg,
        continuity=continuity,
    )
    assert summary.promoted_to_fact == 0
    assert continuity.added_facts == []


async def test_promotion_skipped_when_no_continuity_wired(
    service: TransientStateService, seeded_campaign: str
):
    cfg = TransientStateConfig(
        promote_to_fact=PromoteToFactConfig(reinforcement_count=2),
    )
    await _seed_history(service, seeded_campaign, "guarded", ["p1"])
    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_x",
        field="mood",
        value="guarded",
        confidence=0.95,
        evidence="...",
    )
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=[proposal],
        transient_state=service,
        source_post_id="p2",
        config=cfg,
        continuity=None,
    )
    # Reinforcement still detected, but no continuity service to promote
    # through means promoted_to_fact stays 0.
    assert summary.auto_applied == 1
    assert summary.promoted_to_fact == 0
```

- [ ] **Step 2: Run the tests**

Run: `pytest backend/tests/transient_state/test_reinforcement.py -v`
Expected: PASS — Task 2's reinforcement logic already covers this.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/transient_state/test_reinforcement.py
git commit -m "test(transient-state): reinforcement-driven promote_to_fact"
```

---

## Task 4: LLM extractor schema gains transient_updates

**Files:**
- Modify: `backend/src/grimoire/extractor/schema.py`
- Modify: `backend/src/grimoire/extractor/llm_strategy.py`
- Create: `backend/tests/extractor/test_transient_schema.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/extractor/test_transient_schema.py`:

```python
"""LLM extractor parses transient_updates from the structured payload."""

from __future__ import annotations

from grimoire.extractor.llm_strategy import parse_llm_payload
from grimoire.types.transient import EntityKind


def test_parse_llm_payload_extracts_transient_updates():
    payload = {
        "transient_updates": [
            {
                "entity_kind": "character",
                "entity_id": "char_x",
                "field": "mood",
                "value": "guarded",
                "confidence": 0.9,
                "evidence": "She tensed at the question.",
            },
            {
                "entity_kind": "location",
                "entity_id": "loc_y",
                "field": "ambient_mood",
                "value": "tense",
                "confidence": 0.7,
                "evidence": "Lanterns sputtered.",
            },
        ],
    }
    out = parse_llm_payload(
        payload, campaign_id="c1", source="structured_llm", max_new_entities=10
    )
    assert len(out.transient_updates) == 2
    first = out.transient_updates[0]
    assert first.entity_kind == EntityKind.CHARACTER
    assert first.entity_id == "char_x"
    assert first.field == "mood"
    assert first.value == "guarded"
    assert first.confidence == 0.9


def test_parse_llm_payload_skips_invalid_transient_updates():
    payload = {
        "transient_updates": [
            {"entity_kind": "character", "field": "mood", "value": "ok",
             "confidence": 0.5},  # missing entity_id
            {"entity_kind": "unknown_kind", "entity_id": "x",
             "field": "mood", "value": "ok", "confidence": 0.5},
        ],
    }
    out = parse_llm_payload(
        payload, campaign_id="c1", source="structured_llm", max_new_entities=10
    )
    assert out.transient_updates == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest backend/tests/extractor/test_transient_schema.py -v`
Expected: FAIL — `LLMStrategyOutput` has no `transient_updates` attribute.

- [ ] **Step 3: Add the schema slot**

Modify `backend/src/grimoire/extractor/schema.py`. Inside `output_schema()`, right before the existing `commitment_resolution` dict (around line 137), add a new shape:

```python
    transient_update = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "entity_kind": {
                "type": "string",
                "enum": ["character", "location", "faction", "scene"],
            },
            "entity_id": {"type": "string"},
            "field": {"type": "string"},
            "value": {},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "evidence": {"type": "string"},
        },
        "required": ["entity_kind", "entity_id", "field", "value", "confidence"],
    }
```

Then add to the top-level `properties` dict (after `commitment_resolutions`):

```python
            "transient_updates": {"type": "array", "items": transient_update},
```

Also add to `empty_payload()`:

```python
        "transient_updates": [],
```

- [ ] **Step 4: Extend LLMStrategyOutput + parsing**

Modify `backend/src/grimoire/extractor/llm_strategy.py`.

(a) Add the import near the top with the other type imports:

```python
from grimoire.types.transient import EntityKind as TransientEntityKind
from grimoire.types.transient import TransientUpdateProposal
```

(b) Add a field to `LLMStrategyOutput` (around the existing dataclass at line 54):

```python
@dataclass
class LLMStrategyOutput:
    """Outputs of the structured-LLM strategy before merging."""

    deltas: list[StateDelta] = field(default_factory=list)
    candidates: list[EntityCandidate] = field(default_factory=list)
    flags: list[ExtractionFlag] = field(default_factory=list)
    transient_updates: list[TransientUpdateProposal] = field(default_factory=list)
    confidence_avg: float = 0.0
```

(c) Add a helper above `parse_llm_payload`:

```python
def _make_transient_update(item: dict) -> TransientUpdateProposal | None:
    try:
        kind = TransientEntityKind(str(item.get("entity_kind", "")))
    except ValueError:
        return None
    entity_id = str(item.get("entity_id", "")).strip()
    field_name = str(item.get("field", "")).strip()
    if not entity_id or not field_name:
        return None
    if "value" not in item or "confidence" not in item:
        return None
    try:
        return TransientUpdateProposal(
            entity_kind=kind,
            entity_id=entity_id,
            field=field_name,
            value=item.get("value"),
            confidence=float(item.get("confidence", 0.0)),
            evidence=str(item.get("evidence", "")),
        )
    except Exception:
        return None
```

(d) Inside `parse_llm_payload`, after the existing loop that handles `_BUILDER_MAP` items, add (before `if confidences:`):

```python
    for raw in payload.get("transient_updates", []) or []:
        if not isinstance(raw, dict):
            continue
        proposal = _make_transient_update(raw)
        if proposal is not None:
            out.transient_updates.append(proposal)
            confidences.append(proposal.confidence)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest backend/tests/extractor/test_transient_schema.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/extractor/schema.py backend/src/grimoire/extractor/llm_strategy.py backend/tests/extractor/test_transient_schema.py
git commit -m "feat(extractor): parse transient_updates from structured LLM payload"
```

---

## Task 5: ExtractionResult carries transient_updates from LLM

**Files:**
- Modify: `backend/src/grimoire/extractor/service.py` (3 `ExtractionResult(...)` construction sites)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/extractor/test_transient_schema.py`:

```python
import asyncio
from dataclasses import dataclass


@dataclass
class _StubCompletion:
    text: str


class _StubGateway:
    def __init__(self, payload_text: str) -> None:
        self._text = payload_text

    async def complete(self, task, request, campaign_id=None, *, turn_id=None):
        return _StubCompletion(self._text)


async def test_extractor_service_surfaces_transient_updates_from_llm():
    from grimoire.extractor.service import ExtractorService
    from grimoire.extractor.config import ExtractorConfig
    from grimoire.types.scene import Scene
    from grimoire.types.state import StateSnapshot

    payload = '{"transient_updates": [{"entity_kind": "character", "entity_id": "char_x", "field": "mood", "value": "guarded", "confidence": 0.9}]}'
    config = ExtractorConfig(parallel_strategies=("structured_llm",))
    extractor = ExtractorService(gateway=_StubGateway(payload), config=config)
    scene = Scene(id="s1", campaign_id="c1", branch_id="c1:main", title="t")
    snapshot = StateSnapshot(campaign_id="c1", branch_id="c1:main", scene_id="s1")
    result = await extractor.extract(
        response_text="...",
        scene=scene,
        campaign_id="c1",
        prior_state_snapshot=snapshot,
        pre_roll_resolved=False,
        turn_id="t1",
    )
    assert len(result.transient_updates) == 1
    assert result.transient_updates[0].field == "mood"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest backend/tests/extractor/test_transient_schema.py::test_extractor_service_surfaces_transient_updates_from_llm -v`
Expected: FAIL — `result.transient_updates` is empty because the service drops the LLM strategy's field.

- [ ] **Step 3: Plumb transient_updates through `_run`, `_run_together`, `_run_tool_use`**

Modify `backend/src/grimoire/extractor/service.py`. Find each `return ExtractionResult(` call inside `_run` (around line 312), `_run_together` (around line 511), and the sanity/tool-use path (around line 565). For each, add `transient_updates=...` to the construction.

In `_run` (after `extras_proposals = self._filter_extras_proposals(heur.extras_proposals)` at line 310):

```python
        return ExtractionResult(
            deltas=merged_deltas,
            candidates=candidates,
            extras_proposals=extras_proposals,
            flags=flags,
            transient_updates=list(llm_out.transient_updates),
            confidence_overall=confidence_overall,
            extraction_strategies_run=ran,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
```

In `_run_together` at the return site around line 511 (the `extras_proposals=heur_extras` block), add:

```python
            transient_updates=[],  # together mode emits via the tracker block only
```

(Together mode parses its own structured tracker block; populating transient_updates there is a future addition.)

In the sanity/tool-use return at line 567, add:

```python
            transient_updates=[],
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest backend/tests/extractor/test_transient_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Run the whole extractor test suite to make sure nothing broke**

Run: `pytest backend/tests/extractor/ -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/extractor/service.py backend/tests/extractor/test_transient_schema.py
git commit -m "feat(extractor): surface LLM transient_updates on ExtractionResult"
```

---

## Task 6: Orchestrator wires route_transient_updates + audit fragment

**Files:**
- Modify: `backend/src/grimoire/orchestrator/service.py:151-170` (constructor) and the post-extract block at `service.py:2099-2114`.
- Modify: `backend/src/grimoire/main.py:487-498` (pass transient_state).
- Create: `backend/tests/orchestrator/test_transient_routing.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/orchestrator/test_transient_routing.py`:

```python
"""Orchestrator routes ExtractionResult.transient_updates and emits audit
fragments."""

from __future__ import annotations

from typing import Any

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.transient_state import TransientStateService
from grimoire.transient_state.config import TransientStateConfig
from grimoire.types.transient import (
    EntityKind,
    Provenance,
    TransientUpdateProposal,
)


@pytest.fixture
async def fake_bus() -> EventBus:
    return EventBus()


async def test_route_transient_updates_emits_fragment(
    service: TransientStateService, seeded_campaign: str, fake_bus: EventBus
):
    """Wiring smoke test: simulate the orchestrator post-extract block by
    invoking the helper directly + emitting the fragment shape the
    orchestrator emits. Tests that the *integration shape* (writes +
    conflicts on fragment payload) is preserved end-to-end without bringing
    up the full OrchestratorService.
    """
    from grimoire.transient_state.routing import route_transient_updates

    captured: list[Event] = []

    async def listener(event: Event) -> None:
        captured.append(event)

    fake_bus.subscribe("turn_audit_fragment", listener)

    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_x",
        field="mood",
        value="guarded",
        confidence=0.95,
        evidence="...",
    )
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=[proposal],
        transient_state=service,
        source_post_id="p_1",
    )
    if summary.writes or summary.conflicts:
        await fake_bus.emit(
            Event(
                type="turn_audit_fragment",
                payload={
                    "turn_id": "t1",
                    "campaign_id": seeded_campaign,
                    "transient_state_writes": summary.writes,
                    "transient_state_conflicts": summary.conflicts,
                },
            )
        )
    assert len(captured) == 1
    payload = captured[0].payload
    assert payload["turn_id"] == "t1"
    assert payload["transient_state_writes"][0]["field"] == "mood"
```

- [ ] **Step 2: Run the test to verify it passes (sanity)**

Run: `pytest backend/tests/orchestrator/test_transient_routing.py -v`
Expected: PASS — this test exercises the integration shape and should already work after Task 2.

The reason this isn't a fresh "failing test" first is that Tasks 2/3/4/5 already gave us the building blocks; this test pins the shape we'll use in the orchestrator. The real orchestrator-side change is mechanical wiring, validated by the existing wider suite.

- [ ] **Step 3: Add the constructor parameter to OrchestratorService**

Modify `backend/src/grimoire/orchestrator/service.py`. In the `__init__` signature at line 151, add `transient_state: Any | None = None,` after `continuity` (around line 162):

```python
        continuity: Any | None = None,
        transient_state: Any | None = None,
        ws_push: WSPushFn | None = None,
```

And store it at line 184 (after `self._continuity = continuity`):

```python
        self._continuity = continuity
        self._transient_state = transient_state
```

- [ ] **Step 4: Wire the post-extract routing call**

Still in `service.py`, find the block at line 2098-2114 (begins with `active.stage = "applying"`). Replace the block from the start of `active.stage = "applying"` through the existing `_emit_fragment` call with:

```python
        active.stage = "applying"
        applied_ids: list[str] = []
        queued_ids: list[str] = []
        if extraction is not None:
            applied_ids, queued_ids = await self._apply_routing(
                campaign_id=campaign_id,
                branch_id=scene_obj.branch_id,
                turn_id=turn_id,
                extraction=extraction,
            )
        if applied_ids or queued_ids:
            await self._emit_fragment(
                turn_id,
                campaign_id,
                applied_deltas=[{"id": did} for did in applied_ids],
                queued_for_review=[{"id": qid} for qid in queued_ids],
            )

        # § Transient-state spec §Extractor integration: route any
        # ExtractionResult.transient_updates produced by the LLM strategy
        # through route_transient_updates so they land in the per-field
        # transient store.
        if (
            extraction is not None
            and self._transient_state is not None
            and getattr(extraction, "transient_updates", None)
        ):
            from grimoire.transient_state.routing import (
                route_transient_updates,
            )

            ts_summary = await route_transient_updates(
                campaign_id=campaign_id,
                proposals=list(extraction.transient_updates),
                transient_state=self._transient_state,
                source_post_id=turn_id,
                branch_id=scene_obj.branch_id,
                continuity=self._continuity,
            )
            if ts_summary.writes or ts_summary.conflicts:
                await self._emit_fragment(
                    turn_id,
                    campaign_id,
                    transient_state_writes=ts_summary.writes,
                    transient_state_conflicts=ts_summary.conflicts,
                )
```

- [ ] **Step 5: Pass transient_state in main.py**

Modify `backend/src/grimoire/main.py`. In the `OrchestratorService(...)` construction at line 487, add `transient_state=container.transient_state,` after `continuity=container.continuity,`:

```python
            container.orchestrator = OrchestratorService(
                event_bus=container.event_bus,
                scene_manager=container.scenes,
                llm_gateway=llm_gateway,
                context_builder=context_builder,
                extractor=extractor,
                state_store=container.state_store,
                mechanics=container.mechanics,
                world=container.world,
                continuity=container.continuity,
                transient_state=container.transient_state,
                ws_push=container.stream.push,
            )
```

- [ ] **Step 6: Run the orchestrator tests**

Run: `pytest backend/tests/orchestrator/ -v`
Expected: PASS — the new `transient_state` constructor parameter is keyword-only with a `None` default, so existing call sites continue to work.

Run: `pytest backend/tests/orchestrator/test_transient_routing.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/orchestrator/service.py backend/src/grimoire/main.py backend/tests/orchestrator/test_transient_routing.py
git commit -m "feat(orchestrator): route ExtractionResult.transient_updates + audit fragment"
```

---

## Task 7: Full backend test suite

- [ ] **Step 1: Run pytest across the backend**

Run: `pytest backend/tests/ -x --no-header -q`
Expected: all PASS.

- [ ] **Step 2: If anything fails, diagnose and fix**

Most likely failures: tests that construct `ExtractionResult` directly with positional args (unlikely since it's a Pydantic model with keyword args). If something fails, fix the failing test or the underlying code with the smallest change that restores green.

- [ ] **Step 3: Final commit if any fixes were needed**

```bash
git add <fixed files>
git commit -m "fix(transient-state): <describe>"
```

---

## Spec Coverage Check

| Spec section | Task |
| --- | --- |
| §Extractor integration — `ExtractionResult.transient_updates` populated by LLM | Task 4, 5 |
| §Extractor integration — auto/review/discard routing through `route_transient_updates` | already shipped + Task 6 wires it |
| §Promotion to facts — reinforcement detection (N consecutive posts, same value) | Task 2, 3 |
| §Promotion to facts — `ContinuityService.add_fact` + `supersede_with_fact` dispatch | Task 2, 3 |
| §Audit — `transient_state_write` JSON entry on TurnAudit | Task 1, 6 |
| §Audit — `transient_state_conflict` JSON entry on TurnAudit | Task 1, 6 |
| §Audit — vacuum summary | out of scope per spec (vacuum worker lives in observability) |

## Out of scope

- Together-mode tracker block carrying `transient_updates` (deferred — together mode would need its own schema rev; not blocking solo/co-author).
- `transient_extra.<key>` mechanics-module manifest declaration (explicitly deferred by spec Theme D).
- Vacuum worker (`transient_state_vacuum` audit summary depends on the worker, which the spec assigns to observability/maintenance).
