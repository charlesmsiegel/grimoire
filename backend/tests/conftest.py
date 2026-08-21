"""Shared fixtures: the route-test HTTP client, and the mechanics-Phase5
sheet/audit/absorb store fixtures."""

import importlib
import json
import threading
import time

import anyio
import pytest
import uvicorn
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from grimoire.store import appearances, campaigns, characters, modules, scenes, sheets, worlds
from tests.llm_fakes import FakeLLM, FakeOpenRouter


@pytest.fixture
def client(monkeypatch, tmp_path):
    """An app over a throwaway store, with the gateway faked.

    One copy, here, rather than the identical one three route-test files each
    carried: `store` is reloaded against this test's `GRIMOIRE_HOME`, so the
    fixture has to run *before* the app is built, and every route suite needs
    exactly that. A suite wanting a different fake overrides
    `routes.get_llm` again in the test itself, or declares its own `client`
    fixture, which still shadows this one.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["Hel", "lo"])
    return TestClient(app)


@pytest.fixture
def run_client(monkeypatch, tmp_path):
    """`client`, with the lifespan ENTERED.

    The bare `client` above never emits startup -- `TestClient(app)` without a
    `with` block does not -- so the portal and the reaper do not exist and a run
    cannot actually execute. Tests that only reserve a run and check what some
    other route does about it stay on `client`; tests where a run must RUN take
    this. When in doubt this one is never wrong, only slower.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["Hel", "lo"])
    with TestClient(app) as c:
        yield c


_WARRIOR_FIELDS = [
    {"key": "hp", "label": "Hit Points", "type": "resource", "max": 12},
    {"key": "xp", "label": "Experience", "type": "resource", "max": 999},
    {"key": "athletics", "label": "Athletics", "type": "number",
     "default": 2, "min": 0, "max": 5},
    {"key": "wounds", "label": "Wounds", "type": "track", "max": 5},
    {"key": "conditions", "label": "Conditions", "type": "list"},
    {"key": "notes", "label": "Notes", "type": "text"},
]

SHEETS_DEF = {
    "groups": {},
    "sheet_types": {
        "warrior": {
            "label": "Warrior",
            "kind": "characters",
            "groups": [],
            "fields": _WARRIOR_FIELDS,
        },
        # A second type sharing "warrior"'s shape -- exercises a type-change
        # write (sheets.write's "different sheet_type" path) without needing
        # a dedicated field set.
        "adventurer": {
            "label": "Adventurer",
            "kind": "characters",
            "groups": [],
            "fields": _WARRIOR_FIELDS,
        },
    },
}


@pytest.fixture
def user_pack_path(monkeypatch, tmp_path):
    """A module pack that lives in the user library (GRIMOIRE_HOME/modules),
    so tests can mutate sheets.json in place (schema_stamp mtime tests)."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    mid = modules.create_module("Test Pack")
    root = modules.user_dir() / mid
    (root / "sheets.json").write_text(json.dumps(SHEETS_DEF), encoding="utf-8")
    return root


@pytest.fixture
def cid_with_sheet(user_pack_path):
    mid = user_pack_path.name
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, module=mid)
    sheets.write(cid, "characters", "mara", "warrior",
                 {"hp": {"current": 12, "max": 12}}, expected=None)
    return cid


@pytest.fixture
def scene_with_sheeted_cast(user_pack_path):
    """A scene with one present, sheeted cast member (mara) whose baseline
    was captured at scene creation -- the ground every materialize test
    stands on."""
    mid = user_pack_path.name
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Mara", "default", characters.blank_card("Mara"))
    cid = campaigns.create_campaign("Run", wid, module=mid)
    sheets.write(cid, "characters", "mara", "warrior",
                 {"hp": {"current": 12, "max": 12}, "xp": {"current": 0, "max": 999},
                  "wounds": 0, "conditions": []}, expected=None)
    sid = scenes.create_scene(cid, "Landing")           # captures baseline
    appearances.appear(cid, sid, "characters", "mara", "default", "npc")
    return cid, sid


# --- a real server on a real socket ------------------------------------------
#
# `TestClient` buffers a streaming response to completion, which makes it
# useless for anything about WHEN bytes arrive: a disconnect injected by leaving
# its context manager happens after the stream already finished, and two
# "concurrent" requests through it run one after the other. Both properties are
# load-bearing for detached runs -- a subscriber dropping mid-generation, and
# two scenes generating at once -- so those tests take a real server.

class HeldProvider(FakeLLM):
    """A provider that stops after its first delta until the test lets it go.

    This is what makes "mid-generation" a defined moment. A `sleep` long enough
    to be safe is also long enough to let the whole turn finish, which passes
    vacuously -- the exact failure the disconnect test exists to avoid.
    """

    def __init__(self, reply: str = "The lamps are already lit.", *, per_scene=None):
        # Two deltas: the first arrives immediately, the second only after
        # `release()`, so a test can act between them.
        head, _, tail = reply.partition(" ")
        super().__init__([[head + " ", tail]])
        self.per_scene = per_scene or {}
        self._first = threading.Event()
        self._go = threading.Event()

    def await_first_delta(self, timeout: float = 5.0) -> None:
        assert self._first.wait(timeout), "the provider never produced a delta"

    def release(self) -> None:
        self._go.set()

    async def stream(self, messages, conn, usage=None):
        first = True
        async for delta in super().stream(messages, conn, usage):
            yield delta
            if first:
                first = False
                self._first.set()
                # A checkpoint per tick rather than `await`-ing an anyio Event:
                # `release()` is called from the TEST thread, and setting an
                # anyio event from off-loop is not safe. A threading.Event
                # polled here is, and the interval is invisible next to a
                # provider call.
                await anyio.to_thread.run_sync(self._go.wait)


class LiveServer:
    """uvicorn on an ephemeral port, sharing this test's store."""

    def __init__(self, app, url, campaign_scene, two_scenes):
        self.app = app
        self.url = url
        self.campaign_scene = campaign_scene
        self.two_scenes = two_scenes
        self._held = None

    def hold_provider(self, reply: str = "The lamps are already lit.") -> HeldProvider:
        held = HeldProvider(reply)
        self.app.dependency_overrides[routes.get_llm] = lambda: held
        self._held = held
        return held


@pytest.fixture
def live_server(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["Hel", "lo"])

    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    a = store.scenes.create_scene(cid, "Mara")
    b = store.scenes.create_scene(cid, "Winifred")
    # Without a key `_require_connection` answers 409 `missing_key` before any
    # streaming happens -- which looks exactly like a detach test failing for
    # the reason it was written to catch.
    with TestClient(app) as boot:
        boot.put("/api/llm-connections/openrouter", json={"api_key": "sk-test"})

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error",
                            lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "uvicorn never came up"
    port = server.servers[0].sockets[0].getsockname()[1]

    yield LiveServer(app, f"http://127.0.0.1:{port}", (cid, a), (cid, (a, b)))

    server.should_exit = True
    thread.join(timeout=10)
