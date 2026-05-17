"""Fixtures and fakes for Extractor tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from grimoire.extractor.protocols import ResolvedEntity
from grimoire.types.common import CampaignId, EntityKind, Scope, ValidationResult
from grimoire.types.llm import CompletionRequest, CompletionResponse, TokenUsage
from grimoire.types.mechanics import NarratedEvent
from grimoire.types.scene import Scene, SceneContext
from grimoire.types.state import StateSnapshot


@dataclass
class FakeGateway:
    """Records calls and returns a queued payload as JSON in the response text.

    `queue` is a FIFO of payloads or strings; queued strings are returned
    verbatim, dicts are JSON-serialized first. An empty queue raises so
    tests fail loudly if the extractor calls the gateway more than
    expected.
    """

    queue: list = field(default_factory=list)
    seen: list[tuple[str, CompletionRequest, CampaignId | None]] = field(default_factory=list)
    raise_on_next: BaseException | None = None

    async def complete(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: CampaignId | None = None,
    ) -> CompletionResponse:
        self.seen.append((task, request, campaign_id))
        if self.raise_on_next is not None:
            err = self.raise_on_next
            self.raise_on_next = None
            raise err
        if not self.queue:
            raise AssertionError("FakeGateway queue exhausted")
        payload = self.queue.pop(0)
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return CompletionResponse(
            text=text,
            model=request.model or "fake-model",
            finish_reason="stop",
            usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
            cost_estimate_usd=0.0,
            latency_ms=5,
        )


@dataclass
class FakeMechanics:
    """A `MechanicsValidator` that returns canned `ValidationResult`s."""

    results: list[ValidationResult] = field(default_factory=list)
    seen: list[tuple[CampaignId, NarratedEvent, SceneContext]] = field(default_factory=list)

    async def validate_narrated_event(
        self,
        campaign_id: CampaignId,
        event: NarratedEvent,
        scene: SceneContext,
    ) -> ValidationResult:
        self.seen.append((campaign_id, event, scene))
        if self.results:
            return self.results.pop(0)
        return ValidationResult(valid=True)


@dataclass
class FakeContradictionChecker:
    """A `ContradictionChecker` that returns canned conflict lists."""

    conflicts_for: dict[str, list[str]] = field(default_factory=dict)
    seen: list[tuple[CampaignId, str, dict]] = field(default_factory=list)
    raise_on_next: BaseException | None = None

    async def check(
        self,
        campaign_id: CampaignId,
        fact_text: str,
        about: dict[str, list[str]],
    ) -> list[str]:
        self.seen.append((campaign_id, fact_text, about))
        if self.raise_on_next is not None:
            err = self.raise_on_next
            self.raise_on_next = None
            raise err
        return list(self.conflicts_for.get(fact_text, []))


@dataclass
class FakeEntityResolver:
    """An `EntityResolver` that returns canned `(scope, card)` tuples.

    `entries` is keyed by `(entity_ref, EntityKind)`; absent keys resolve
    to `None` so the drift step degrades the same way it would against a
    missing library asset.
    """

    entries: dict[tuple[str, EntityKind], ResolvedEntity] = field(default_factory=dict)
    seen: list[tuple[CampaignId, str, EntityKind]] = field(default_factory=list)
    raise_on_next: BaseException | None = None

    async def resolve(
        self,
        campaign_id: CampaignId,
        entity_ref: str,
        kind: EntityKind,
    ) -> ResolvedEntity | None:
        self.seen.append((campaign_id, entity_ref, kind))
        if self.raise_on_next is not None:
            err = self.raise_on_next
            self.raise_on_next = None
            raise err
        return self.entries.get((entity_ref, kind))


def make_library_entity(card: dict) -> ResolvedEntity:
    return ResolvedEntity(scope=Scope.LIBRARY, card=dict(card))


def make_campaign_entity(card: dict) -> ResolvedEntity:
    return ResolvedEntity(scope=Scope.CAMPAIGN_LOCAL, card=dict(card))


@pytest.fixture
def scene() -> Scene:
    return Scene(
        id="scene-1",
        campaign_id="camp-1",
        branch_id="main",
        ordinal=1,
        slug="cliff-top",
        file_path="/tmp/cliff-top.md",
        title="Cliff Top",
        location_ref="campaign:locations/cliff-top",
        present_character_refs=["winifred", "vivienne"],
        present_pc_refs=["julian"],
        pov_character_ref="julian",
    )


@pytest.fixture
def snapshot() -> StateSnapshot:
    return StateSnapshot(
        campaign_id="camp-1",
        branch_id="main",
        scene_id="scene-1",
    )
