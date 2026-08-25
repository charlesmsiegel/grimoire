"""Shared fixtures: the route-test HTTP client, and the mechanics-Phase5
sheet/audit/absorb store fixtures."""

import importlib
import json
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from grimoire.store import (appearances, campaigns, characters, modules, paths, scenes,
                            sheets, worlds)
from tests.llm_fakes import FakeOpenRouter, HeldOpenRouter


@pytest.fixture(autouse=True)
def _isolate_bootstrap_pointer(monkeypatch, tmp_path):
    """Keep every test off the developer's REAL `~/.grimoire.json`.

    `GRIMOIRE_HOME` isolates the data root, and almost everything here sets it
    -- but the bootstrap pointer is not under that root, by construction: it is
    what NAMES the root, so it lives beside it at a fixed path in the user's
    home. `PUT /config/data-dir` writes it. Three tests call that route and
    expect it to succeed, so a plain `pytest` run rewrote the pointer of
    whoever ran it and their app opened an empty library on next launch. The
    data was never touched and the symptom is invisible from inside the suite,
    which is what let it stand.

    Autouse rather than a helper each test remembers: `test_data_dir` and
    `test_where` already isolate it correctly and are unaffected (a test's own
    `monkeypatch.setattr` runs later and wins). The failure mode being fixed is
    precisely a test that does not know it needs to.
    """
    monkeypatch.setattr(paths, "pointer_path",
                        lambda: tmp_path / "bootstrap" / ".grimoire.json")


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
    # `with`, so the LIFESPAN RUNS. It did not used to, and nothing needed it
    # to; now every producing route hands its work to a runner that lives on
    # the lifespan's event loop, so a client without one cannot drive a turn at
    # all -- 59 existing route tests said so the moment `post_chat` was
    # migrated. Two fixtures, one of which is required for most of the suite,
    # is worse than one that always works.
    with TestClient(app) as c:
        yield c


@pytest.fixture
def run_client(client):
    """Kept as a name for tests that specifically mean "a run can execute here".

    `client` now always enters the lifespan, so this is the same object; the
    separate name stays where a test's point is that the runtime is live, since
    that is not obvious from `client` alone.
    """
    return client


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

class LiveServer:
    """uvicorn on an ephemeral port, sharing this test's store."""

    def __init__(self, app, url, campaign_scene, two_scenes):
        self.app = app
        self.url = url
        self.campaign_scene = campaign_scene
        self.two_scenes = two_scenes
        self._held: list = []

    def hold_provider(self, replies="The lamps are already lit.") -> HeldOpenRouter:
        """A provider held after its first delta. `replies` may be a marker ->
        reply mapping, so two concurrent turns are distinguishable."""
        held = HeldOpenRouter(replies)
        self.app.dependency_overrides[routes.get_llm] = lambda: held
        self._held.append(held)
        return held

    def set_provider(self, provider) -> None:
        """Install a plain (unheld) provider -- for the setup a test needs
        before the moment it wants to hold."""
        self.app.dependency_overrides[routes.get_llm] = lambda: provider

    def release_all(self) -> None:
        """Let every held provider finish.

        Called from teardown as well as by tests. A test that fails between
        `hold_provider()` and `release()` would otherwise leave a worker thread
        blocked on the hold -- `anyio.to_thread.run_sync` does not abandon its
        worker on cancellation -- so the lifespan never completes, the server
        thread outlives the test, and the original failure gets buried under
        whatever that breaks next.
        """
        for held in self._held:
            held.release()


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

    live = LiveServer(app, f"http://127.0.0.1:{port}", (cid, a), (cid, (a, b)))
    try:
        yield live
    finally:
        live.release_all()
        server.should_exit = True
        thread.join(timeout=10)
