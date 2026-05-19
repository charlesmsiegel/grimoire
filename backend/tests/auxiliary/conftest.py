"""Fixtures for auxiliary-task orchestrator tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from grimoire.event_bus import EventBus
from grimoire.orchestrator.config import OrchestratorConfig
from grimoire.orchestrator.service import OrchestratorService
from grimoire.scenes.manager import SceneManager, SceneManagerConfig
from grimoire.scenes.types import (
    AuthorKind,
    Post,
    Scene,
    SceneInit,
)
from grimoire.types.context import AssembledPrompt
from grimoire.types.extraction import ExtractionResult
from grimoire.types.llm import (
    CompletionChunk,
    CompletionRequest,
    Message,
    MessageRole,
    ModelParams,
)


@dataclass
class _Row:
    data: dict

    def __getitem__(self, key: str) -> Any:
        return self.data[key]


@dataclass
class FakeDB:
    campaigns: set[str] = field(default_factory=set)
    pcs: dict[str, set[str]] = field(default_factory=dict)

    async def fetchone(self, sql: str, params: tuple) -> _Row | None:
        sql = sql.strip().lower()
        if sql.startswith("select id from campaigns"):
            cid = params[0]
            return _Row({"id": cid}) if cid in self.campaigns else None
        if sql.startswith("select character_ref from campaign_pcs"):
            cid, pc = params[:2]
            if pc in self.pcs.get(cid, set()):
                return _Row({"character_ref": pc})
            # No PC filter: fall back to any.
            if len(params) == 1:
                for ref in self.pcs.get(cid, set()):
                    return _Row({"character_ref": ref})
            return None
        return None


@dataclass
class FakeStateStore:
    db: FakeDB = field(default_factory=FakeDB)
    applied_sets: list[dict] = field(default_factory=list)
    rewound: list[str] = field(default_factory=list)

    async def apply_delta_set(self, **kwargs: Any) -> None:
        self.applied_sets.append(kwargs)

    async def swap_delta_set(self, **kwargs: Any) -> None:
        self.applied_sets.append({"swap": True, **kwargs})

    async def rewind_delta_set(
        self,
        delta_set_id: str,
        *,
        campaign_id: str,
        branch_id: str,
    ) -> None:
        self.rewound.append(delta_set_id)

    async def set_current_alternate_delta_set(self, **kwargs: Any) -> None:
        pass

    async def mark_pc_played(self, *, campaign_id: str, character_ref: str) -> None:
        pass

    async def get_delta_log(self, **kwargs: Any) -> list[Any]:
        return []


class FakeRouter:
    """Stand-in for `RouteResolver`. Returns a Route-like object."""

    def __init__(self, routes: dict[str, str] | None = None) -> None:
        self._routes = routes or {}

    def resolve(self, task: str, campaign_id: str | None = None):
        from grimoire.llm_gateway.errors import RouteNotFoundError

        raw = self._routes.get(task)
        if raw is None:
            raise RouteNotFoundError(task)

        @dataclass
        class _Route:
            raw: str
            provider_id: str
            model: str

        provider, _, model = raw.partition(".")
        return _Route(raw=raw, provider_id=provider, model=model)


@dataclass
class FakeGateway:
    """Streams a canned response token-by-token."""

    chunks: list[str] = field(default_factory=lambda: ["Hello", " ", "world."])
    seen_tasks: list[str] = field(default_factory=list)
    seen_requests: list[CompletionRequest] = field(default_factory=list)
    fail_after: int | None = None
    _router: FakeRouter = field(
        default_factory=lambda: FakeRouter(
            {
                "main": "anthropic.claude-opus-4-7",
                "auxiliary.brainstorm": "anthropic.claude-sonnet-4-6",
                "auxiliary.impersonate_pc": "anthropic.claude-opus-4-7",
                "auxiliary.rewrite_post": "anthropic.claude-opus-4-7",
                "auxiliary.continue_as": "anthropic.claude-opus-4-7",
                "auxiliary.what_would_x_say": "anthropic.claude-sonnet-4-6",
                "auxiliary.edit_prose": "anthropic.claude-sonnet-4-6",
                "auxiliary.translate": "anthropic.claude-haiku-4-5",
            }
        )
    )

    async def _gen(self) -> AsyncIterator[CompletionChunk]:
        for idx, c in enumerate(self.chunks):
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
        return self._gen()


@dataclass
class FakeContextBuilder:
    """A builder that honours `auxiliary_task` and produces a trivial prompt."""

    calls: list[dict] = field(default_factory=list)
    _characters: Any = None

    async def build(
        self,
        player_input: str = "",
        campaign_id: str = "",
        mechanics_results: list[Any] | None = None,
        extra: str | None = None,
        *,
        branch_id: str | None = None,
        pc_ref: str | None = None,
        turn_id: str | None = None,
        extractor_mode: Any = None,
        auxiliary_task: Any = None,
    ) -> AssembledPrompt:
        self.calls.append(
            {
                "campaign_id": campaign_id,
                "auxiliary_task": auxiliary_task,
                "extractor_mode": extractor_mode,
            }
        )
        text = "aux prompt"
        if auxiliary_task is not None:
            text = f"aux:{auxiliary_task.kind.value}"
        return AssembledPrompt(
            messages=[Message(role=MessageRole.SYSTEM, content=text)],
            params=ModelParams(),
            budget_used={},
        )


@dataclass
class FakeExtractor:
    deltas: list[Any] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    async def extract(
        self,
        response_text: str,
        scene: Any,
        campaign_id: str,
        snapshot: Any,
        *,
        turn_id: str | None = None,
        **_: Any,
    ) -> ExtractionResult:
        self.calls.append({"text": response_text, "campaign_id": campaign_id, "turn_id": turn_id})
        return ExtractionResult(deltas=list(self.deltas), flags=[])


class WSCollector:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict]] = []

    async def __call__(self, campaign_id: str, message: dict) -> None:
        self.messages.append((campaign_id, message))


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def scene_manager(tmp_path: Path) -> SceneManager:
    return SceneManager(tmp_path, config=SceneManagerConfig(running_summary_every_n_posts=0))


@pytest.fixture
def fake_store() -> FakeStateStore:
    return FakeStateStore()


@pytest.fixture
def fake_gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def fake_context_builder() -> FakeContextBuilder:
    return FakeContextBuilder()


@pytest.fixture
def fake_extractor() -> FakeExtractor:
    return FakeExtractor()


@pytest.fixture
def ws() -> WSCollector:
    return WSCollector()


@dataclass
class SeededState:
    campaign_id: str
    pc_ref: str
    scene: Scene
    posts: list[Post]


@pytest.fixture
async def seeded_state(scene_manager: SceneManager, fake_store: FakeStateStore) -> SeededState:
    campaign_id = "camp_aux"
    pc_ref = "pc_florence"
    fake_store.db.campaigns.add(campaign_id)
    fake_store.db.pcs[campaign_id] = {pc_ref}
    scene = await scene_manager.start_scene(
        SceneInit(
            campaign_id=campaign_id,
            branch_id="main",
            title="The Tower",
            slug="tower",
            pov_character_ref=pc_ref,
            present_character_refs=[pc_ref, "npc_crow"],
            present_pc_refs=[pc_ref],
        )
    )
    p1 = Post(
        id="p_0001",
        scene_id=scene.id,
        order_in_scene=1,
        author_kind=AuthorKind.NPC,
        body="The crow lit on the wall.",
        is_player=False,
        created_at=datetime.now(UTC),
        turn_id="t_0001",
        author_npc_ref="npc_crow",
    )
    await scene_manager.append_post(scene.id, p1)
    return SeededState(campaign_id=campaign_id, pc_ref=pc_ref, scene=scene, posts=[p1])


@pytest.fixture
def orchestrator(
    event_bus: EventBus,
    scene_manager: SceneManager,
    fake_gateway: FakeGateway,
    fake_context_builder: FakeContextBuilder,
    fake_extractor: FakeExtractor,
    fake_store: FakeStateStore,
    ws: WSCollector,
) -> OrchestratorService:
    return OrchestratorService(
        event_bus=event_bus,
        scene_manager=scene_manager,
        llm_gateway=fake_gateway,
        context_builder=fake_context_builder,
        extractor=fake_extractor,
        state_store=fake_store,
        ws_push=ws,
        config=OrchestratorConfig(main_llm_task="main"),
    )
