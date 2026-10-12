"""04-S8: the persisted `scene_turns` and `continuity_summary` layers.

Registered (as these tests register them, whatever spec 03's measurement left
in `compiled.KINDS`), both answer from the compiled cache after a restart, and
every overview route answers byte-identically in all five cache states. An
input that cannot be read, a locked or corrupt or absent database each take
the live path and store nothing.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.store import compiled, overview, readstats
from grimoire.store.overview import read as overview_read
from tests.overview_support import (
    audited,
    cache_clock_forward,
    clear_memos,
    clock_forward,
    database_locked,
    register_overview_kinds,
    restart,
)


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.delenv("GRIMOIRE_COMPILED_CACHE", raising=False)
    importlib.reload(store)
    compiled.close()
    clear_memos()
    register_overview_kinds(monkeypatch)
    cache_clock_forward(monkeypatch)
    yield tmp_path
    compiled.close()


@pytest.fixture
def client(home):
    with TestClient(create_app()) as c:
        yield c


def _library(client):
    """Placeholder library with the spec's fixtures: CRLF and non-UTF-8 scene
    files, two byte-identical campaign.md files, a scene with no title, a
    campaign with no ledgers, and a garbled commitments.json."""
    wid = client.post("/api/worlds", json={"name": "Saltmarch"}).json()["id"]
    a = client.post("/api/campaigns", json={"name": "A Long Run", "world": wid}).json()["id"]
    b = client.post("/api/campaigns", json={"name": "Mara's Wake", "world": wid}).json()["id"]
    c = client.post("/api/campaigns", json={"name": "Winifred", "world": wid}).json()["id"]
    open_sid = store.scenes.create_scene(a, "Saltmarch Gate")
    store.scenes.append_message(a, open_sid, "user", "I knock.", speaker="You")
    store.scenes.append_message(a, open_sid, "assistant", "The gate opens.", speaker="Mara")
    crlf = store.scenes.create_scene(a, "Realm")
    store.scenes.append_message(a, crlf, "assistant", "Rain.", speaker="Seraphine")
    p = store.scenes.paths._scene_path(a, crlf)
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    bad = store.scenes.create_scene(a, "Winifred")
    p = store.scenes.paths._scene_path(a, bad)
    p.write_bytes(p.read_bytes() + b"**Mara:** " + b"Realm " * 2000 + b"\xff\n")
    closed = store.scenes.create_scene(a, "The Quay")
    store.scenes.append_message(a, closed, "assistant", "Fog.", speaker="Mara")
    store.scenes.mark_absorbed(a, closed, "done", "summary")
    untitled = store.scenes.create_scene(b, "x")
    p = store.scenes.paths._scene_path(b, untitled)
    p.write_text(p.read_text(encoding="utf-8").replace("title: x\n", ""), encoding="utf-8")
    store.commitments.set_movement(a, "maras-oath", "Mara's oath", "promise", "open",
                                   "2026-06-01", "Mara swore it.", open_sid)
    store.commitments._path(b).write_text("{not json", encoding="utf-8")
    mb = store.campaigns.paths.campaign_meta_path(c)
    mb.write_bytes(store.campaigns.paths.campaign_meta_path(b).read_bytes())
    return {"world": wid, "a": a, "b": b, "c": c, "open": open_sid, "closed": closed}


def _payloads(client, lib):
    out = {}
    for url in ("/api/worlds", "/api/campaigns", "/api/todo",
                *(f"/api/todo?campaign={lib[k]}" for k in ("a", "b", "c")),
                *(f"/api/shell?campaign={lib[k]}" for k in ("a", "b", "c"))):
        r = client.get(url)
        assert r.status_code == 200, (url, r.text)
        out[url] = r.json()
    return out


def test_every_route_answers_identically_in_all_five_states(client, home, monkeypatch):
    lib = _library(client)
    cold = _payloads(client, lib)
    assert _payloads(client, lib) == cold
    restart()
    assert _payloads(client, lib) == cold
    with database_locked(home):
        clear_memos()
        assert _payloads(client, lib) == cold
    monkeypatch.setenv("GRIMOIRE_COMPILED_CACHE", "0")
    clear_memos()
    assert _payloads(client, lib) == cold


def test_warm_after_restart_parses_no_unchanged_transcript_or_ledger(client):
    lib = _library(client)
    _payloads(client, lib)
    restart()
    with readstats.collect() as counters:
        client.get("/api/shell", params={"campaign": lib["a"]})
    assert counters.get("transcript.parse") == 0
    assert counters.get("compiled.hit.overview.scene_turns") >= 1
    with readstats.collect() as counters:
        client.get("/api/todo")
    assert counters.get("overview.computed.continuity_summary") == 0
    assert counters.get("compiled.hit.overview.continuity_summary") == 3


def test_an_unrelated_change_misses_no_overview_kind(client, monkeypatch):
    clock_forward(monkeypatch)
    lib = _library(client)
    root = store.worlds.world_root(lib["world"])
    eid = store.entities.create_entity(root, "lore", "Saltmarch Tides", "Rain.")
    _payloads(client, lib)
    restart()
    _payloads(client, lib)
    p = store.entities._entity_path(root, "lore", eid)
    p.write_text(p.read_text(encoding="utf-8").replace("Rain.", "Fog."), encoding="utf-8")
    with readstats.collect() as counters:
        client.get("/api/campaigns")
        for k in ("a", "b", "c"):
            client.get("/api/todo", params={"campaign": lib[k]})
    assert counters.total("compiled.miss.") == 0
    assert {k: v for k, v in counters.snapshot().items() if k.startswith("overview.computed.")} == {}


@pytest.mark.parametrize("state", ["cold", "warm", "restart", "locked", "off"])
def test_no_closed_scene_transcript_is_opened(client, home, monkeypatch, state):
    clock_forward(monkeypatch)
    lib = _library(client)
    if state != "cold":
        _payloads(client, lib)
    if state == "restart":
        restart()
    if state == "off":
        monkeypatch.setenv("GRIMOIRE_COMPILED_CACHE", "0")
        clear_memos()
    asked: list[str] = []
    real_turns = overview_read._turns
    monkeypatch.setattr(overview_read, "_turns", lambda cid, sid, p, players: (
        asked.append(sid), real_turns(cid, sid, p, players))[1])
    closed = str(store.scenes.paths._scene_path(lib["a"], lib["closed"]))
    with audited() as audit:
        if state == "locked":
            with database_locked(home):
                clear_memos()
                _payloads(client, lib)
        else:
            _payloads(client, lib)
    # Only open scenes' transcripts are read, and a closed scene is opened at
    # most once, head-only, when its head is not remembered in-process.
    assert lib["closed"] not in asked
    assert audit.opened.count(closed) <= (0 if state == "warm" else 1)


def test_a_failed_input_read_takes_the_live_path_and_stores_nothing(client, monkeypatch):
    lib = _library(client)
    real = compiled.Batch.derive

    def refuse(self, kind, inputs, **kwargs):
        raise compiled.InputUnavailable("refused")
    monkeypatch.setattr(compiled.Batch, "derive", refuse)
    with readstats.collect() as counters:
        payload = _payloads(client, lib)
    assert counters.get("overview.live_path.overview.continuity_summary") >= 1
    assert counters.get("overview.live_path.overview.scene_turns") >= 1
    assert counters.get("compiled.store.overview.continuity_summary") == 0
    monkeypatch.setattr(compiled.Batch, "derive", real)
    clear_memos()
    assert _payloads(client, lib) == payload


def test_a_corrupt_database_answers_through_the_live_path(client, home):
    lib = _library(client)
    cold = _payloads(client, lib)
    compiled.close()
    for path in (home / ".cache" / "compiled").glob("*.sqlite"):
        path.write_bytes(b"not a database" * 100)
    clear_memos()
    assert _payloads(client, lib) == cold


def test_an_absent_database_answers_identically(client, home, monkeypatch):
    lib = _library(client)
    cold = _payloads(client, lib)
    monkeypatch.setattr(compiled, "_sqlite", None)
    clear_memos()
    assert _payloads(client, lib) == cold


def test_content_determined_failures_are_cached(client):
    lib = _library(client)
    first = overview.continuity_summary(lib["b"])
    assert first["ledger_open"] is None
    restart()
    with readstats.collect() as counters:
        assert overview.continuity_summary(lib["b"]) == first
    assert counters.get("overview.computed.continuity_summary") == 0
