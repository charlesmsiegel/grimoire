"""04-S9: the post-turn warm and `warm_paths`.

A landed turn stores `scene_turns` for the bytes it wrote, from its own
follow-up, so the shell read after the turn hashes the transcript and does not
parse it. A warm is best effort: whatever it meets -- a raising compute, a
missing scene, a failed player read, the cache off -- the turn's outcome is
unchanged and a failed warm is a later miss.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from grimoire.store import compiled, overview, readstats
from grimoire.store.overview import warm
from tests.overview_support import (
    cache_clock_forward,
    clear_memos,
    register_overview_kinds,
    restart,
)

from .llm_fakes import FakeLLM, decision_reply, from_entries


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.delenv("GRIMOIRE_COMPILED_CACHE", raising=False)
    importlib.reload(store)
    compiled.close()
    clear_memos()
    register_overview_kinds(monkeypatch)
    cache_clock_forward(monkeypatch)
    with TestClient(create_app()) as c:
        c.put("/api/llm-connections/openrouter", json={"api_key": "sk-test"})
        yield c
    compiled.close()


def _llm() -> FakeLLM:
    return from_entries([
        {"when": {"system_contains": "keeping a running summary"}, "reply": "A summary."},
        {"when": {"system_contains": "You answer closed questions"},
         "reply": decision_reply({"over": False}, rationales=["Mid-scene."])},
        {"when": {}, "reply": "The gate swings open."},
    ], "overview-warm")


def _scene(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Mara's Gate"}).json()["id"]
    return cid, sid


def _turn(client, cid, sid):
    client.app.dependency_overrides[routes.get_llm] = _llm
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "I knock."})
    assert r.status_code == 200
    subject = ("scene", cid, store.scenes.scene_identity(cid, sid))
    for run in client.app.state.runs.for_subject(subject):
        if run.cls == "turn":
            assert run.terminal.wait(timeout=10.0)
    return r


def test_the_shell_after_a_turn_reads_the_warmed_count(client):
    cid, sid = _scene(client)
    _turn(client, cid, sid)
    clear_memos()   # the in-process layer missed anyway: the file moved
    with readstats.collect() as counters:
        block = client.get("/api/shell", params={"campaign": cid}).json()["campaign"]
    assert block["open"][0]["turns"] == 1
    assert counters.get("compiled.hit.overview.scene_turns") == 1
    assert counters.get("transcript.parse") == 0


@pytest.mark.parametrize("failure", ["compute", "player_read", "cache_off", "missing_scene"])
def test_a_failed_warm_leaves_the_turn_unchanged(client, monkeypatch, failure):
    cid, sid = _scene(client)

    def boom(*args, **kwargs):
        raise OSError("boom")
    # The warm's own view of the modules it calls, so the turn and the read
    # path keep the real ones.
    if failure == "compute":
        monkeypatch.setattr(warm, "kinds", SimpleNamespace(scene_turns=boom))
    elif failure == "player_read":
        monkeypatch.setattr(warm, "cast", SimpleNamespace(player_names=boom))
    elif failure == "cache_off":
        monkeypatch.setenv("GRIMOIRE_COMPILED_CACHE", "0")
    _turn(client, cid, sid)
    messages = store.scenes.read.read_scene(cid, sid)["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    if failure == "missing_scene":
        overview.warm_scene(cid, "no-such-scene")   # never raises
    monkeypatch.delenv("GRIMOIRE_COMPILED_CACHE", raising=False)
    clear_memos()
    # A failed warm is a later miss: the shell still answers the right count.
    assert client.get("/api/shell", params={"campaign": cid}).json()["campaign"]["open"][0]["turns"] == 1


def test_warm_scene_never_raises(client, monkeypatch):
    cid, sid = _scene(client)

    def boom(*args, **kwargs):
        raise RuntimeError("compute raised")
    monkeypatch.setattr(warm, "_warm_scene", boom)
    assert overview.warm_scene(cid, sid) is None


def test_warm_scene_stores_no_sources_row(client, monkeypatch):
    cid, sid = _scene(client)
    staged = []
    real = compiled.Batch._stage_source
    monkeypatch.setattr(compiled.Batch, "_stage_source",
                        lambda self, *a: (staged.append(a), real(self, *a))[1])
    store.scenes.append_message(cid, sid, "assistant", "Rain.", speaker="Mara")
    overview.warm_scene(cid, sid)
    assert staged == []


def test_warm_paths_maps_each_path_class_and_coalesces(client, monkeypatch):
    cid, sid = _scene(client)
    wid = store.campaigns.read.read_campaign(cid)["meta"]["world"]
    root = store.worlds.world_root(wid)
    eid = store.entities.create_entity(root, "lore", "Saltmarch Tides", "Rain.")
    store.commitments.set_movement(cid, "maras-oath", "Mara's oath", "promise", "open",
                                   "2026-06-01", "Mara swore it.", sid)
    calls = []
    real = warm._warm
    monkeypatch.setattr(warm, "_warm", lambda kind, inst: (calls.append((kind, inst)), real(kind, inst))[1])
    paths = [
        f"worlds/{wid}/world.md",
        f"campaigns/{cid}/campaign.md",
        f"campaigns/{cid}/scenes/{sid}.md",
        *(f"campaigns/{cid}/{name}" for name in ("plot.json", "commitments.json", "events.json",
                                                   "continuity.json", "continuity_candidates.json")),
        f"worlds/{wid}/lore/{eid}.md",
        f"worlds/{wid}/characters/mara/character.md",
        f"campaigns/{cid}/scenes/../campaign.md",
    ]
    out = warm.warm_paths(paths)
    assert out[f"worlds/{wid}/world.md"] == "warmed"
    assert out[f"campaigns/{cid}/campaign.md"] == "warmed"
    assert out[f"campaigns/{cid}/scenes/{sid}.md"] == "warmed"
    assert all(out[f"campaigns/{cid}/{n}"] == "warmed"
               for n in ("plot.json", "commitments.json", "continuity_candidates.json"))
    assert out[f"worlds/{wid}/lore/{eid}.md"] == "none"
    assert out[f"worlds/{wid}/characters/mara/character.md"] == "none"
    assert out[f"campaigns/{cid}/scenes/../campaign.md"] == "none"
    assert sorted(calls) == sorted([("world_row", (wid,)), ("campaign_row", (cid,)),
                                    ("scene_turns", (cid, sid)), ("continuity_summary", (cid,))])
    # What was warmed is what the next read after a restart hits.
    restart()
    with readstats.collect() as counters:
        overview.continuity_summary(cid)
        overview.campaign_cards()
    assert counters.get("compiled.hit.overview.continuity_summary") == 1
    assert counters.get("overview.computed.continuity_summary") == 0


def test_warm_paths_reports_failed_and_none_without_raising(client, monkeypatch):
    cid, _sid = _scene(client)
    real = warm._warm

    def boom(kind, inst):
        raise OSError("boom")
    monkeypatch.setattr(warm, "_warm", boom)
    assert warm.warm_paths([f"campaigns/{cid}/campaign.md", "elsewhere/x.md"]) == {
        f"campaigns/{cid}/campaign.md": "failed", "elsewhere/x.md": "none"}
    # With the cache off nothing persisted can be warmed: "none", not "failed".
    monkeypatch.setattr(warm, "_warm", real)
    monkeypatch.setenv("GRIMOIRE_COMPILED_CACHE", "0")
    assert warm.warm_paths([f"campaigns/{cid}/campaign.md"]) == {f"campaigns/{cid}/campaign.md": "none"}
