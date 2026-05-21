"""``TestApp`` — composes the modules that exist today into a unit harness.

Tests that exercise the orchestrator/extractor end-to-end will not build
until those modules land (tasks #19/#20/#22), but the harness already
wires up:

* in-process event bus
* SQLite Database + migrations on a temp file
* StateStore
* MechanicsService (with the null module by default)
* SceneManager
* MockLLMGateway

so unit and integration tests for the existing modules don't need to
hand-roll the same setup repeatedly. As later modules land they'll be
added to this composition.

Fixture loading is intentionally minimal: a directory of YAML/Markdown
files (campaigns, library content, scenes) gets copied into ``data_root``
on entry. Anything richer should be expressed as a Python factory
function passed to :meth:`with_fixtures`.
"""

from __future__ import annotations

import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.characters.service import CharactersService
from grimoire.continuity.config import ContinuityConfig
from grimoire.continuity.service import ContinuityService
from grimoire.event_bus import EventBus
from grimoire.extractor.config import ExtractorConfig
from grimoire.extractor.service import ExtractorService
from grimoire.library.service import LibraryService
from grimoire.mechanics.config import MechanicsConfig
from grimoire.mechanics.service import MechanicsService
from grimoire.orchestrator.config import OrchestratorConfig
from grimoire.orchestrator.service import OrchestratorService
from grimoire.scenes.manager import SceneManager, SceneManagerConfig
from grimoire.state_store.store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.testing.fixtures import (
    LibraryCampaignFixture,
    seed_library_campaign_fixture,
)
from grimoire.testing.mock_llm import MockLLMGateway
from grimoire.time_engine.config import TimeEngineConfig
from grimoire.time_engine.service import NpcTickFn, TimeEngineService
from grimoire.types.context import AssembledPrompt
from grimoire.types.llm import Message, MessageRole, ModelParams
from grimoire.world.service import WorldService

FixtureFactory = Callable[["TestApp"], Awaitable[None]]


@dataclass(slots=True)
class TestAppFixture:
    __test__ = False  # pytest: this is a fixture record, not a test class

    """A bundle of files + an optional Python setup hook.

    ``files_root`` is a directory whose subtree is copied into
    ``data_root`` on entry. ``setup`` runs after the files are in place
    and after the DB migrations are applied, so it can call into the
    State Store directly.
    """

    name: str
    files_root: Path | None = None
    setup: FixtureFactory | None = None
    overrides: dict[str, Any] = field(default_factory=dict)


class TestApp:
    __test__ = False  # pytest: prevent collection as a test class

    """Composable harness.

    Usage::

        async with TestApp() as app:
            app.llm.queue_response("primary", "She nods.")
            ...

    Or with a named fixture::

        async with TestApp.with_fixtures("simple_campaign", root=tmp_path) as app:
            await app.scene_manager.start_scene(...)
    """

    def __init__(
        self,
        data_root: Path,
        *,
        mechanics_config: MechanicsConfig | None = None,
        scene_config: SceneManagerConfig | None = None,
        continuity_config: ContinuityConfig | None = None,
        extractor_config: ExtractorConfig | None = None,
        time_engine_config: TimeEngineConfig | None = None,
        orchestrator_config: OrchestratorConfig | None = None,
        llm: MockLLMGateway | None = None,
    ) -> None:
        self.data_root = Path(data_root)
        self.data_root.mkdir(parents=True, exist_ok=True)

        self.event_bus = EventBus()
        self.db = Database(self.data_root / "grimoire.sqlite", pool_size=2)
        self.llm = llm or MockLLMGateway()

        self._mechanics_config = mechanics_config or MechanicsConfig(
            root=self.data_root / "mechanics",
        )
        self._scene_config = scene_config or SceneManagerConfig()
        self._continuity_config = continuity_config or ContinuityConfig()
        # Default the extractor to the structured_llm strategy only so tests
        # opt-in to a fully-mockable extraction path (heuristics + rules add
        # surprise deltas that drown out the queued response).
        self._extractor_config = extractor_config or ExtractorConfig(
            parallel_strategies=("structured_llm",),
        )
        self._time_engine_config = time_engine_config or TimeEngineConfig()
        self._orchestrator_config = orchestrator_config or OrchestratorConfig()

        # Lazy: populated in ``__aenter__``.
        self.state_store: StateStore | None = None
        self.mechanics: MechanicsService | None = None
        self.continuity: ContinuityService | None = None
        self.scene_manager: SceneManager | None = None
        self.library: LibraryService | None = None
        self.world: WorldService | None = None
        self.characters: CharactersService | None = None
        self.extractor: ExtractorService | None = None
        self.context_builder: _StubContextBuilder | None = None
        self.orchestrator: OrchestratorService | None = None
        self.time_engine: TimeEngineService | None = None
        # Raw family records seeded from a LibraryCampaignFixture. Empty
        # by default; the seeder populates this so integration tests can
        # assert on family membership without a dedicated service.
        self.character_families: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def __aenter__(self) -> TestApp:
        await self.db.connect()
        await apply_migrations(self.db)
        self.state_store = StateStore(self.db, self.data_root)
        self.mechanics = MechanicsService(self._mechanics_config, self.state_store)
        self.continuity = ContinuityService(config=self._continuity_config)
        self.scene_manager = SceneManager(
            self.data_root,
            config=self._scene_config,
        )
        self.library = LibraryService(self.state_store)
        self.world = WorldService(self.library)
        self.characters = CharactersService(self.library, self.mechanics)
        # The Extractor and turn-loop services are mock-backed: the
        # gateway is the MockLLMGateway, the context builder is a stub
        # that returns the player's input verbatim. Real wiring requires
        # the full Library/World/Characters/Composition stack which is
        # out of scope for integration tests that target the turn loop
        # itself.
        self.extractor = ExtractorService(
            gateway=self.llm,
            config=self._extractor_config,
        )
        self.context_builder = _StubContextBuilder()
        self.orchestrator = OrchestratorService(
            event_bus=self.event_bus,
            scene_manager=self.scene_manager,
            llm_gateway=self.llm,
            context_builder=self.context_builder,
            extractor=self.extractor,
            state_store=self.state_store,
            mechanics=self.mechanics,
            continuity=self.continuity,
            library=self.library,
            world=self.world,
            extractor_config=self._extractor_config,
            config=self._orchestrator_config,
        )
        self.time_engine = TimeEngineService(
            store=self.state_store,
            world=self.world,
            characters=self.characters,
            mechanics=self.mechanics,
            continuity=self.continuity,
            event_bus=self.event_bus,
            config=self._time_engine_config,
        )
        return self

    def install_npc_tick_fn(self, fn: NpcTickFn) -> None:
        """Swap the Time Engine's NPC-tick callable.

        Tests that want to observe which NPCs got ticked register a
        recording stub here instead of constructing their own
        TimeEngineService.
        """
        if self.time_engine is None:
            raise RuntimeError("install_npc_tick_fn requires the TestApp to be entered")
        self.time_engine._npc_tick_fn = fn

    async def __aexit__(self, *exc: Any) -> None:
        await self.db.close()

    # ------------------------------------------------------------------ #
    # Construction helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def with_fixtures(
        cls,
        fixture: str | TestAppFixture | LibraryCampaignFixture,
        *,
        root: Path,
        registry: dict[str, Any] | None = None,
    ) -> _TestAppBuilder:
        """Construct a builder that will load ``fixture`` on entry.

        ``fixture`` may be:

        * a :class:`TestAppFixture` — the file-copy / setup-hook shape;
        * a :class:`LibraryCampaignFixture` — bundle of library state
          and one or more campaigns (seeded in-code via the
          ``StateStore`` + ``LibraryService``);
        * a ``str`` — looked up in ``registry`` (if provided) or in the
          process-wide :mod:`grimoire.testing.fixtures_registry`.

        The builder is itself an async context manager so call sites do
        ``async with TestApp.with_fixtures(...) as app:``.
        """
        if isinstance(fixture, str):
            if registry is not None and fixture in registry:
                fixture = registry[fixture]
            else:
                # Fall back to the process-wide registry. Imported lazily
                # so the registry module's import of ``TestAppFixture``
                # doesn't trip a cycle.
                from grimoire.testing import fixtures_registry

                try:
                    fixture = fixtures_registry.get(fixture)
                except KeyError as exc:
                    raise KeyError(
                        f"unknown fixture {fixture!r} "
                        f"(not in passed registry, not in fixtures_registry)"
                    ) from exc
        return _TestAppBuilder(fixture, Path(root))


class _TestAppBuilder:
    def __init__(
        self,
        fixture: TestAppFixture | LibraryCampaignFixture,
        data_root: Path,
    ) -> None:
        self._fixture = fixture
        self._data_root = data_root
        self._app: TestApp | None = None

    async def __aenter__(self) -> TestApp:
        app = TestApp(self._data_root)
        if isinstance(self._fixture, TestAppFixture):
            if self._fixture.files_root is not None:
                _copy_tree(self._fixture.files_root, app.data_root)
            await app.__aenter__()
            if self._fixture.setup is not None:
                await self._fixture.setup(app)
        elif isinstance(self._fixture, LibraryCampaignFixture):
            await app.__aenter__()
            await seed_library_campaign_fixture(app, self._fixture)
        else:
            raise TypeError(f"unsupported fixture type: {type(self._fixture).__name__}")
        self._app = app
        return app

    async def __aexit__(self, *exc: Any) -> None:
        if self._app is not None:
            await self._app.__aexit__(*exc)


class _StubContextBuilder:
    """Minimal duck-typed Context Builder for turn-loop integration tests.

    Returns an :class:`AssembledPrompt` that threads the player input
    verbatim into a user message. Records every call so tests can assert
    the orchestrator invoked it with the expected arguments.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def build(
        self,
        player_input: str,
        campaign_id: str,
        mechanics_results: list[Any] | None = None,
        *,
        pc_ref: str | None = None,
        extra: str | None = None,
        branch_id: str | None = None,
        turn_id: str | None = None,
        extractor_mode: Any = None,
        auxiliary_task: Any | None = None,
    ) -> AssembledPrompt:
        self.calls.append(
            {
                "player_input": player_input,
                "campaign_id": campaign_id,
                "pc_ref": pc_ref,
                "turn_id": turn_id,
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


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy ``src`` into ``dst``, merging directories.

    ``shutil.copytree`` with ``dirs_exist_ok=True`` does what we need;
    this wrapper exists so callers don't have to remember the flag and
    so we can grow per-file overrides later.
    """
    if not src.exists():
        raise FileNotFoundError(f"fixture files_root {src} does not exist")
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)


__all__ = ["FixtureFactory", "TestApp", "TestAppFixture"]
