"""``ScenarioApp`` — L5 end-to-end scenario harness (spec 17 §L5).

End-to-end scenario tests drive the real FastAPI app over its HTTP API,
with the LLM gateway swapped for :class:`RecordReplayLLM` in replay
mode so prose is deterministic. This module wires that up:

* spins :func:`grimoire.main.create_app` against a temp data root;
* pre-installs a :class:`RecordReplayLLM` into ``container.extras``
  before the lifespan runs, so all LLM-touching services use it;
* opens an in-process ``httpx.AsyncClient`` bound to the ASGI app
  (no sockets, no real network);
* tears down the lifespan + temp dir on exit.

The harness is intentionally thin. It is *not* a replacement for
:class:`grimoire.testing.app.TestApp` — that one composes services
directly for unit and integration tests. ``ScenarioApp`` always goes
through the HTTP surface, which is the whole point of L5.

Usage::

    async with ScenarioApp(tmp_path) as app:
        resp = await app.client.post("/api/campaigns", json={...})
        ...

If a scenario needs a pre-seeded library or campaign on disk, pass
``seed_library`` pointing at a directory tree that will be copied
into ``{data_root}/library`` before the lifespan starts. Frozen
SQLite snapshots from §4 can be dropped in via ``seed_db`` (a path
to a ``*.sqlite`` file that becomes the campaigns DB).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Any

import httpx

from grimoire.api.container import ServiceContainer
from grimoire.testing.record_replay import RecordReplayLLM, ReplayMode


class ScenarioApp:
    """Async context manager that boots the real FastAPI app for an L5 test.

    The app is created fresh on each entry. We monkey-patch
    :mod:`grimoire.config` settings to point at a tmp data root so the
    lifespan code constructs all services against an isolated tree;
    on exit we restore the original settings object and remove the
    tmp tree.
    """

    def __init__(
        self,
        data_root: Path | None = None,
        *,
        seed_library: Path | None = None,
        seed_db: Path | None = None,
        llm_fixture_dir: Path | None = None,
        replay_mode: ReplayMode = ReplayMode.REPLAY,
    ) -> None:
        # If the caller didn't provide a data_root we own a tmp dir and
        # clean it up on exit. Otherwise we leave it alone so callers
        # can introspect after the context closes.
        self._owns_tmp = data_root is None
        self._tmp_dir = tempfile.mkdtemp(prefix="grimoire-scenario-") if self._owns_tmp else None
        self.data_root: Path = Path(self._tmp_dir) if self._tmp_dir else Path(data_root)  # type: ignore[arg-type]
        self.data_root.mkdir(parents=True, exist_ok=True)

        self._seed_library = seed_library
        self._seed_db = seed_db
        # LLM fixtures live alongside the rest of the testing fixtures by
        # default. Tests that want to record fresh responses point this
        # at a writable directory and set ``replay_mode=RECORD``.
        # RecordReplayLLM appends "llm/by_hash/" internally, so we hand
        # it the parent `tests/fixtures/` directory.
        self._llm_fixture_dir = (
            Path(llm_fixture_dir)
            if llm_fixture_dir is not None
            else Path(__file__).resolve().parents[3] / "tests" / "fixtures"
        )
        self._replay_mode = replay_mode

        # Populated in __aenter__.
        self.app: Any | None = None
        self.client: httpx.AsyncClient | None = None
        self.container: ServiceContainer | None = None
        self._llm: RecordReplayLLM | None = None
        self._lifespan_cm: Any | None = None
        self._saved_settings: Any | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def __aenter__(self) -> ScenarioApp:
        # Seed library files first so the initial library scan picks
        # them up during lifespan startup.
        if self._seed_library is not None:
            self._copy_tree(self._seed_library, self.data_root / "library")

        # Seed the SQLite snapshot if provided. apply_migrations runs
        # inside the lifespan so the snapshot can lag behind current
        # head and still load.
        db_path = self.data_root / "campaigns.sqlite"
        if self._seed_db is not None:
            shutil.copy2(self._seed_db, db_path)

        # Swap settings to point at our tmp data root. Saved + restored
        # on exit so the surrounding test process isn't mutated.
        from grimoire import config as config_module

        self._saved_settings = config_module.settings
        config_module.settings = config_module.Settings(
            data_root=self.data_root,
            database_path=db_path,
        )

        # Build the app and pre-install a RecordReplayLLM in the
        # container before lifespan runs — the lifespan code checks
        # ``container.llm_gateway`` and respects an
        # existing value, so this is the supported way to inject.
        from grimoire.main import create_app

        self.app = create_app()
        self.container = ServiceContainer()
        self._llm = RecordReplayLLM(
            fixture_dir=self._llm_fixture_dir,
            mode=self._replay_mode,
        )
        self.container.llm_gateway = self._llm
        self.app.state.container = self.container

        # httpx's ASGITransport does not run ASGI lifespan events, so we
        # drive startup ourselves via FastAPI's ``lifespan_context``.
        # Holding the context across the test gives us a proper shutdown
        # call in ``__aexit__`` — without this, ``container.world`` and
        # other services are never constructed and every request 503s
        # with "<service> not configured".
        self._lifespan_cm = self.app.router.lifespan_context(self.app)
        await self._lifespan_cm.__aenter__()

        transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(
            transport=transport,
            base_url="http://scenario.local",
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self.client is not None:
                await self.client.aclose()
            if self._lifespan_cm is not None:
                # Pass the active exception through so lifespan shutdown
                # sees the same context the surrounding async with does.
                await self._lifespan_cm.__aexit__(exc_type, exc, tb)
        finally:
            # Restore the global settings object so other tests in the
            # same process see the original configuration.
            if self._saved_settings is not None:
                from grimoire import config as config_module

                config_module.settings = self._saved_settings
            if self._owns_tmp and self._tmp_dir is not None:
                shutil.rmtree(self._tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _copy_tree(src: Path, dst: Path) -> None:
        if not src.exists():
            raise FileNotFoundError(f"seed source {src} does not exist")
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)


__all__ = ["ScenarioApp"]
