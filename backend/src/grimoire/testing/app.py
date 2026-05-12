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

from grimoire.continuity.config import ContinuityConfig
from grimoire.continuity.service import ContinuityService
from grimoire.event_bus import EventBus
from grimoire.mechanics.config import MechanicsConfig
from grimoire.mechanics.service import MechanicsService
from grimoire.scenes.manager import SceneManager, SceneManagerConfig
from grimoire.state_store.store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.testing.mock_llm import MockLLMGateway

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

        # Lazy: populated in ``__aenter__``.
        self.state_store: StateStore | None = None
        self.mechanics: MechanicsService | None = None
        self.continuity: ContinuityService | None = None
        self.scene_manager: SceneManager | None = None

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
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.db.close()

    # ------------------------------------------------------------------ #
    # Construction helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def with_fixtures(
        cls,
        fixture: str | TestAppFixture,
        *,
        root: Path,
        registry: dict[str, TestAppFixture] | None = None,
    ) -> _TestAppBuilder:
        """Construct a builder that will load ``fixture`` on entry.

        ``fixture`` may be the name of a registered fixture (looked up
        in ``registry``) or a :class:`TestAppFixture` instance. The
        builder is itself an async context manager so call sites can do
        ``async with TestApp.with_fixtures(...) as app:``.
        """
        if isinstance(fixture, str):
            if registry is None or fixture not in registry:
                raise KeyError(f"unknown fixture {fixture!r} (no registry passed)")
            fixture = registry[fixture]
        return _TestAppBuilder(fixture, Path(root))


class _TestAppBuilder:
    def __init__(self, fixture: TestAppFixture, data_root: Path) -> None:
        self._fixture = fixture
        self._data_root = data_root
        self._app: TestApp | None = None

    async def __aenter__(self) -> TestApp:
        app = TestApp(self._data_root)
        if self._fixture.files_root is not None:
            _copy_tree(self._fixture.files_root, app.data_root)
        await app.__aenter__()
        if self._fixture.setup is not None:
            await self._fixture.setup(app)
        self._app = app
        return app

    async def __aexit__(self, *exc: Any) -> None:
        if self._app is not None:
            await self._app.__aexit__(*exc)


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
