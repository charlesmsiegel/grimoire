"""Shared fakes and fixtures for orchestrator tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from grimoire.event_bus import EventBus
from grimoire.scenes.manager import SceneManager, SceneManagerConfig
from grimoire.types.context import AssembledPrompt
from grimoire.types.extraction import ExtractionFlag, ExtractionResult, FlagLevel
from grimoire.types.llm import CompletionChunk, CompletionRequest, Message, MessageRole, ModelParams
from grimoire.types.state import StateDelta


@dataclass
class FakeRow:
    data: dict

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


@dataclass
class FakeDB:
    """A tiny fake of ``state_store.db`` exposing ``fetchone``."""

    campaigns: set[str] = field(default_factory=set)
    pcs: dict[str, set[str]] = field(default_factory=dict)

    campaign_configs: dict[str, str] = field(default_factory=dict)

    async def fetchone(self, sql: str, params: tuple) -> FakeRow | None:
        sql = sql.strip().lower()
        if sql.startswith("select id from campaigns"):
            cid = params[0]
            if cid in self.campaigns:
                return FakeRow({"id": cid})
            return None
        if sql.startswith("select character_ref from campaign_pcs"):
            cid, pc = params
            if pc in self.pcs.get(cid, set()):
                return FakeRow({"character_ref": pc})
            return None
        if sql.startswith("select config from campaigns"):
            cid = params[0]
            if cid in self.campaigns:
                return FakeRow({"config": self.campaign_configs.get(cid)})
            return None
        return None


@dataclass
class FakeStateStore:
    """A duck-typed StateStore replacement.

    Records every call so tests can assert against the audit trail. Each
    ``apply_delta`` mints a unique id; ``get_delta_log`` returns the
    recorded deltas in application order; ``reverse_delta`` marks them
    reversed.
    """

    db: FakeDB = field(default_factory=FakeDB)
    applied: list[dict] = field(default_factory=list)
    reviewed: list[dict] = field(default_factory=list)
    reversed_ids: list[str] = field(default_factory=list)
    # Delta ids that are queued for review but unapplied; cascade delete must
    # skip these when reversing (they were never applied to their target).
    pending_delta_ids: set[str] = field(default_factory=set)
    # Review ids rejected via reject_review_item (cascade delete rejects the
    # review rows of fully-removed turns).
    rejected_review_ids: list[str] = field(default_factory=list)
    # When set, apply_delta raises on the Nth call (0-indexed) so tests can
    # simulate a mid-batch failure.
    fail_apply_on_call: int | None = None
    _apply_call_count: int = 0

    async def apply_delta(
        self,
        *,
        delta: Any,
        source: str = "",
        turn_id: str | None = None,
        campaign_id: str | None = None,
    ) -> str:
        idx = self._apply_call_count
        self._apply_call_count += 1
        if self.fail_apply_on_call is not None and idx == self.fail_apply_on_call:
            raise RuntimeError("apply_delta boom")
        did = f"d_{len(self.applied):04d}"
        self.applied.append(
            {
                "id": did,
                "delta": delta,
                "source": source,
                "turn_id": turn_id,
                "campaign_id": campaign_id,
            }
        )
        return did

    async def queue_for_review(
        self,
        *,
        delta: Any,
        source: str = "",
        campaign_id: str | None = None,
    ) -> str:
        rid = f"r_{len(self.reviewed):04d}"
        self.reviewed.append(
            {
                "id": rid,
                "delta": delta,
                "source": source,
                "campaign_id": campaign_id,
            }
        )
        return rid

    async def get_delta_log(
        self,
        *,
        campaign_id: str | None = None,
        turn_id: str | None = None,
        include_reversed: bool = True,
        limit: int | None = None,
    ) -> list[Any]:
        rows = []
        for entry in self.applied:
            if entry["id"] in self.reversed_ids and not include_reversed:
                continue
            if campaign_id and entry["campaign_id"] != campaign_id:
                continue
            if turn_id and entry["turn_id"] != turn_id:
                continue
            rows.append(_DeltaRow(entry))
        return rows

    async def reverse_delta(self, delta_id: str) -> None:
        self.reversed_ids.append(delta_id)

    async def pending_review_delta_ids(self, campaign_id: str) -> set[str]:
        return set(self.pending_delta_ids)

    async def pending_review_items(self, campaign_id: str) -> list[tuple[str, str | None]]:
        # One review row per pending delta, keyed off the applied log so the
        # turn id matches; review id derived from the delta id.
        out: list[tuple[str, str | None]] = []
        for entry in self.applied:
            if entry["id"] in self.pending_delta_ids and entry["campaign_id"] == campaign_id:
                out.append((f"rq_{entry['id']}", entry["turn_id"]))
        return out

    async def reject_review_item(self, review_id: str, *, notes: str = "") -> None:
        self.rejected_review_ids.append(review_id)

    async def get_campaign_row(self, campaign_id: str) -> dict | None:
        if campaign_id not in self.db.campaigns:
            return None
        return {"id": campaign_id, "config": self.db.campaign_configs.get(campaign_id)}


class _DeltaRow:
    def __init__(self, entry: dict) -> None:
        self.id = entry["id"]
        self.turn_id = entry["turn_id"]
        self.campaign_id = entry["campaign_id"]
        self.delta = entry["delta"]
        self.target_id = getattr(entry["delta"], "target_id", None)


@dataclass
class FakeContextBuilder:
    """Returns a canned ``AssembledPrompt`` and records every call."""

    calls: list[dict] = field(default_factory=list)

    async def build(
        self,
        player_input: str,
        campaign_id: str,
        mechanics_results: list[Any] | None = None,
        *,
        pc_ref: str | None = None,
        extra: str | None = None,
        turn_id: str | None = None,
        extractor_mode: Any = None,
        auxiliary_task: Any | None = None,
    ) -> AssembledPrompt:
        self.calls.append(
            {
                "player_input": player_input,
                "campaign_id": campaign_id,
                "pc_ref": pc_ref,
                "mechanics_results": list(mechanics_results or []),
                "extractor_mode": extractor_mode,
            }
        )
        return AssembledPrompt(
            messages=[
                Message(role=MessageRole.SYSTEM, content="ctx"),
                Message(role=MessageRole.USER, content=player_input),
            ],
            params=ModelParams(),
            budget_used={},
        )


@dataclass
class FakeGateway:
    """Streams a canned response token-by-token."""

    chunks: list[str] = field(default_factory=lambda: ["Hello", ", ", "world."])
    seen_requests: list[CompletionRequest] = field(default_factory=list)
    seen_tasks: list[str] = field(default_factory=list)
    chunk_delay: float = 0.0
    fail_after: int | None = None  # raise mid-stream after N chunks

    async def _stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        for idx, c in enumerate(self.chunks):
            if self.chunk_delay:
                await asyncio.sleep(self.chunk_delay)
            if self.fail_after is not None and idx >= self.fail_after:
                raise RuntimeError("gateway boom")
            yield CompletionChunk(delta=c, is_final=False)
        yield CompletionChunk(delta="", is_final=True)

    def stream(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: str | None = None,
        *,
        turn_id: str | None = None,
    ) -> AsyncIterator[CompletionChunk]:
        self.seen_tasks.append(task)
        self.seen_requests.append(request)
        return self._stream(request)


@dataclass
class FakeExtractor:
    """Returns a fixed ``ExtractionResult`` with the provided deltas."""

    deltas: list[StateDelta] = field(default_factory=list)
    seen: list[dict] = field(default_factory=list)
    raise_on_extract: BaseException | None = None
    # If non-empty, each call pops the next code from this list and emits an
    # ExtractionFlag with that code (in addition to the default deltas).
    # ``None`` entries yield a clean result.
    scripted_flag_codes: list[str | None] = field(default_factory=list)

    async def extract(
        self,
        response_text: str,
        scene: Any,
        campaign_id: str,
        snapshot: Any,
        *,
        pre_roll_resolved: bool = False,
        turn_id: str | None = None,
        mode: Any = None,
        together_tracker_text: str | None = None,
        tool_calls: Any | None = None,
    ) -> ExtractionResult:
        self.seen.append(
            {
                "text": response_text,
                "scene_id": getattr(scene, "id", None),
                "campaign_id": campaign_id,
                "mode": mode,
            }
        )
        if self.raise_on_extract is not None:
            raise self.raise_on_extract
        flags: list[ExtractionFlag] = []
        if self.scripted_flag_codes:
            code = self.scripted_flag_codes.pop(0)
            if code is not None:
                flags.append(ExtractionFlag(level=FlagLevel.WARNING, code=code, message=code))
        return ExtractionResult(deltas=list(self.deltas), flags=flags)

    async def extract_from_user_text(
        self,
        user_text: str,
        scene: Any,
        campaign_id: str,
        *,
        snapshot: Any | None = None,
        player_pc_ref: str | None = None,
        turn_id: str | None = None,
    ) -> ExtractionResult:
        return ExtractionResult(deltas=list(self.deltas))


class WSCollector:
    """Captures every ws_push call so tests can assert event order."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, dict]] = []

    async def __call__(self, campaign_id: str, message: dict) -> None:
        self.messages.append((campaign_id, message))


@pytest.fixture
def scene_manager(tmp_path: Path) -> SceneManager:
    return SceneManager(tmp_path, config=SceneManagerConfig(running_summary_every_n_posts=0))


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def fake_store() -> FakeStateStore:
    return FakeStateStore()


@pytest.fixture
def fake_gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def fake_extractor() -> FakeExtractor:
    return FakeExtractor()


@pytest.fixture
def fake_context_builder() -> FakeContextBuilder:
    return FakeContextBuilder()


@pytest.fixture
def ws() -> WSCollector:
    return WSCollector()


def fixed_clock(start: datetime | None = None):
    base = start or datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)

    def _now() -> datetime:
        return base

    return _now
