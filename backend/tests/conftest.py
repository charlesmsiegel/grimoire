"""Shared fixtures: the route-test HTTP client, and the mechanics-Phase5
sheet/audit/absorb store fixtures."""

import importlib
import json
import os
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from grimoire.routes import continuity as continuity_routes
from grimoire.store import (
    appearances,
    campaigns,
    characters,
    modules,
    paths,
    scenes,
    sheets,
    worlds,
)
from tests import phase_profile, route_memo
from tests.llm_fakes import FakeOpenRouter, HeldOpenRouter

# Before any app is built: FastAPI analyses each route once per process rather
# than once per app. Changes how often that work is done, not what it produces
# -- see tests/route_memo.py; GRIMOIRE_TEST_ROUTE_MEMO=0 turns it off.
route_memo.install()

# The inference layout switch stays OFF in the suite (`store.inference_keys
# .AUTOMIGRATE_ENV`): a fresh tmp store is not born at format 2, and nothing
# migrates on its own. Almost every test builds LEGACY settings on a fresh
# store, which a store born current would ignore. A test of the switch itself
# unsets it with `monkeypatch.delenv`; a migration test calls the migration.
# Set at import, not in a fixture, so a subprocess a test spawns inherits it.
os.environ["GRIMOIRE_INFERENCE_AUTOMIGRATE"] = "0"


@pytest.fixture(autouse=True, scope="session")
def _default_home_never_the_real_one(tmp_path_factory):
    """The floor under `_isolate_bootstrap_pointer`'s per-test `DEFAULT_HOME`.

    A test that calls `monkeypatch.undo()` mid-test (to lift a fault it
    injected) also undoes that per-test patch, and the rest of the test would
    then resolve the real `~/.grimoire`. Patched once for the session, outside
    every test's `monkeypatch`, what an undo restores is this directory.

    The bootstrap pointer gets the same floor: an undo also restores
    `paths.pointer_path`, and the real one names the developer's own library
    (`~/.grimoire.json`), which a stray `paths.home()` would then follow. A
    session pointer that is never written resolves nothing but this tree."""
    floor = tmp_path_factory.mktemp("default-home")
    pointer = tmp_path_factory.mktemp("bootstrap-pointer") / ".grimoire.json"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(paths, "DEFAULT_HOME", floor)
        mp.setattr(paths, "pointer_path", lambda: pointer)
        yield


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
    # The same reasoning one step further down `home()`'s chain: with neither
    # the env var nor a pointer, the root is `DEFAULT_HOME` -- the developer's
    # real `~/.grimoire`. Since images go into the global content-addressed
    # store (`store.image_store`, rooted at `home()` rather than at the record
    # root a test passes in), a store-level test that writes an image without
    # setting `GRIMOIRE_HOME` would otherwise put its blob in that real library.
    monkeypatch.setattr(paths, "DEFAULT_HOME", tmp_path / "default-home")


def pytest_addoption(parser):
    phase_profile.add_option(parser)


def pytest_configure(config):
    # Opt-in only: without `--phase-profile` nothing is registered, so a normal
    # run carries none of the profiler's hooks or counters.
    phase_profile.register(config)
    config.addinivalue_line(
        "markers", "tracker: keep the scene tracker's shipped default (on) for this test")
    config.addinivalue_line(
        "markers",
        "reconcile: keep the automatic continuity sweep after End Scene on for this test")


@pytest.fixture(autouse=True)
def _tracker_off_unless_asked(request, monkeypatch):
    """The scene tracker is shipped ON, and in the suite it is OFF unless a test
    asks for it with `@pytest.mark.tracker`.

    On, every post schedules a background `tracker-update` LLM call beside the
    turn. A scripted fake answers by call order, so those calls would consume
    the turns a test scripted and inflate every `calls ==` it asserts -- the
    tracker would be under test in every suite that sends a message, racing the
    turn it describes. The tracker's own suites opt in; the shipped default is
    pinned by `test_tracker_settings.py`, which opts in too.
    """
    if request.node.get_closest_marker("tracker") is None:
        monkeypatch.setattr(store.config, "DEFAULT_TRACKER", "off")


@pytest.fixture(autouse=True)
def _auto_reconcile_off_unless_asked(request, monkeypatch):
    """The continuity sweep after End Scene is shipped ON, and in the suite it is
    OFF unless a test asks for it with `@pytest.mark.reconcile` -- the
    `_tracker_off_unless_asked` precedent.

    On, every `PUT /chronicle` starts a lifespan-hosted `background` run. A fake
    scripted by call order would hand it the turns a test scripted for
    something else; it writes the candidate cache and moves the campaign's
    write token after the response the test is asserting on; and while it is
    live it refuses `PUT /config/data-dir`. The sweep's own suite opts in.
    """
    if request.node.get_closest_marker("reconcile") is None:
        monkeypatch.setattr(continuity_routes, "AUTO_RECONCILE", False)


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
    # Without a key `require_inference` answers 409 `missing_key` before any
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
