import asyncio
import json as json_module
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ingest_scene  # noqa: E402
from grimoire.store import campaigns, worlds  # noqa: E402


def _world(monkeypatch, tmp_path) -> str:
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return worlds.create_world("ashgrove")


def test_ensure_campaign_creates_once(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    cid1 = ingest_scene.ensure_campaign("Silver Oath", wid)
    cid2 = ingest_scene.ensure_campaign("Silver Oath", wid)
    assert cid1 == cid2
    assert campaigns.read_campaign(cid1)["meta"]["world"] == wid


def test_manifest_round_trips(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    assert ingest_scene.load_manifest(cid) == {}
    ingest_scene.save_manifest(cid, {"file1-scene01": {"status": "done", "sid": "001--x"}})
    assert ingest_scene.load_manifest(cid) == {"file1-scene01": {"status": "done", "sid": "001--x"}}


def test_ensure_character_creates_once(monkeypatch, tmp_path):
    from grimoire.store import campaigns as campaigns_store
    wid = _world(monkeypatch, tmp_path)
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    croot = campaigns_store.campaign_root(cid)
    aid1 = ingest_scene.ensure_character(croot, {"name": "cassian", "personality": "wary, precise"})
    aid2 = ingest_scene.ensure_character(croot, {"name": "cassian"})
    assert aid1 == aid2 == "cassian"
    vid = ingest_scene.resolve_version(croot, "characters", aid1)
    from grimoire.store import characters
    assert characters.read_card(croot, aid1, vid)["data"]["personality"] == "wary, precise"


def test_ensure_location_creates_once(monkeypatch, tmp_path):
    from grimoire.store import campaigns as campaigns_store
    wid = _world(monkeypatch, tmp_path)
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    croot = campaigns_store.campaign_root(cid)
    eid1 = ingest_scene.ensure_location(croot, {"name": "Thornfield Manor", "notes": "Seat of corvin."})
    eid2 = ingest_scene.ensure_location(croot, {"name": "Thornfield Manor"})
    assert eid1 == eid2 == "thornfield-manor"


def test_resolve_version_for_pc(monkeypatch, tmp_path):
    from grimoire.store import campaigns as campaigns_store, pcs, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("ashgrove")
    wroot = worlds_store.world_root(wid)
    pcs.create_pc(wroot, "julian", [], "default")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    croot = campaigns_store.campaign_root(cid)
    vid = ingest_scene.resolve_version(croot, "pcs", "julian")
    assert vid == "default"


def test_build_scene_writes_transcript_cast_location_date(monkeypatch, tmp_path):
    from grimoire.store import appearances, campaigns as campaigns_store, scenes, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("ashgrove")
    wroot = worlds_store.world_root(wid)
    from grimoire.store import pcs
    pcs.create_pc(wroot, "julian", [], "default")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)

    scene = {
        "title": "The Reckoning",
        "date": "1818-05-15",
        "new_locations": [{"name": "winterbourne Manor", "notes": "Family seat."}],
        "location": "winterbourne-manor",
        "new_characters": [{"name": "marisol", "personality": "cruel, controlled"}],
        "characters": [{"kind": "pcs", "id": "julian"}, {"kind": "characters", "id": "marisol"}],
        "turns": [
            {"role": "assistant", "speaker": None, "content": "*The study is silent.*"},
            {"role": "assistant", "speaker": "marisol", "content": "\"You've grown bold.\""},
            {"role": "user", "speaker": "julian", "content": "\"I have.\""},
        ],
    }
    sid = ingest_scene.build_scene(cid, scene)

    read = scenes.read_scene(cid, sid)
    assert [m["content"] for m in read["messages"]] == [
        "*The study is silent.*", "\"You've grown bold.\"", "\"I have.\""]
    assert read["messages"][1]["speaker"] == "marisol"
    assert read["messages"][2]["role"] == "user"
    assert "1818-05-15" in sid  # first date-set stamps the filename
    cast = {(a["kind"], a["id"]) for a in appearances.scene_cast(cid, sid)}
    assert cast == {("pcs", "julian"), ("characters", "marisol")}


class FakeClient:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    async def complete(self, messages, model, key):
        self.calls.append((messages, model, key))
        return self.text


def test_run_absorb_and_apply_scene(monkeypatch, tmp_path):
    from grimoire.store import campaigns as campaigns_store, playstate, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("ashgrove")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    croot = campaigns_store.campaign_root(cid)
    ingest_scene.ensure_character(croot, {"name": "marisol"})

    scene = {
        "title": "The Reckoning",
        "characters": [{"kind": "characters", "id": "marisol"}],
        "turns": [{"role": "assistant", "speaker": "marisol", "content": "\"You've grown bold.\""}],
    }
    sid = ingest_scene.build_scene(cid, scene)

    fake_text = json_module.dumps({
        "one_line": "marisol needles julian.",
        "summary": "A tense study confrontation.",
        "keywords": ["study", "confrontation"],
        "timeline_events": [{"date": "1818-05-15", "text": "julian confronts marisol."}],
        "character_state_edits": [{"id": "marisol", "current_state": "wary of julian"}],
        "lore_edits": [], "authored_edits": [], "relationship_deltas": [],
        "bond_changes": [], "plot_movements": [],
    })
    client = FakeClient(fake_text)
    result = asyncio.run(ingest_scene.run_absorb(cid, sid, client, {"model": "test/model", "openrouter_key": "k"}))
    assert result["parsed"]["one_line"] == "marisol needles julian."
    assert any(e["kind"] == "character_state" for e in result["edits"])

    applied = ingest_scene.apply_scene(cid, sid, result["parsed"], result["edits"])
    assert applied
    st = playstate.read_state(croot, "marisol")
    assert "wary of julian" in st["current_state"]
    assert client.calls[0][1] == "test/model" and client.calls[0][2] == "k"


def test_ingest_one_scene_is_resumable(monkeypatch, tmp_path):
    from grimoire.store import campaigns as campaigns_store, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("ashgrove")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    croot = campaigns_store.campaign_root(cid)
    ingest_scene.ensure_character(croot, {"name": "marisol"})

    scene = {
        "key": "file1-scene01",
        "title": "The Reckoning",
        "characters": [{"kind": "characters", "id": "marisol"}],
        "turns": [{"role": "assistant", "speaker": "marisol", "content": "\"You've grown bold.\""}],
    }
    fake_text = json_module.dumps({
        "one_line": "marisol needles julian.", "summary": "s", "keywords": [],
        "timeline_events": [], "character_state_edits": [], "lore_edits": [],
        "authored_edits": [], "relationship_deltas": [], "bond_changes": [], "plot_movements": [],
    })
    client = FakeClient(fake_text)
    cfg = {"model": "test/model", "openrouter_key": "k"}

    first = asyncio.run(ingest_scene.ingest_one_scene(cid, scene, client, cfg))
    assert first["status"] == "done"
    assert len(client.calls) == 1

    second = asyncio.run(ingest_scene.ingest_one_scene(cid, scene, client, cfg))
    assert second["status"] == "skipped"
    assert second["sid"] == first["sid"]
    assert len(client.calls) == 1  # no second LLM call


def test_ingest_one_scene_resumes_after_build_then_crash(monkeypatch, tmp_path):
    """If build_scene succeeded but absorb/apply never ran (process died in between),
    a retry must reuse the recorded sid instead of minting a duplicate scene."""
    from grimoire.store import campaigns as campaigns_store, scenes, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("ashgrove")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    croot = campaigns_store.campaign_root(cid)
    ingest_scene.ensure_character(croot, {"name": "marisol"})

    scene = {
        "key": "file1-scene01",
        "title": "The Reckoning",
        "characters": [{"kind": "characters", "id": "marisol"}],
        "turns": [{"role": "assistant", "speaker": "marisol", "content": "\"You've grown bold.\""}],
    }

    # Simulate the crash: build_scene ran (creating the real scene on disk) but the
    # manifest was written as "in_progress" and the process died before absorb/apply.
    sid = ingest_scene.build_scene(cid, scene)
    manifest = ingest_scene.load_manifest(cid)
    manifest[scene["key"]] = {"status": "in_progress", "sid": sid}
    ingest_scene.save_manifest(cid, manifest)

    fake_text = json_module.dumps({
        "one_line": "marisol needles julian.", "summary": "s", "keywords": [],
        "timeline_events": [], "character_state_edits": [], "lore_edits": [],
        "authored_edits": [], "relationship_deltas": [], "bond_changes": [], "plot_movements": [],
    })
    client = FakeClient(fake_text)
    cfg = {"model": "test/model", "openrouter_key": "k"}

    result = asyncio.run(ingest_scene.ingest_one_scene(cid, scene, client, cfg))
    assert result["status"] == "done"
    assert result["sid"] == sid
    assert len(scenes.list_scenes(cid)) == 1


def test_two_scenes_accumulate_state_in_order(monkeypatch, tmp_path):
    """Scene 2's snapshot must see scene 1's applied character-state edit."""
    from grimoire.store import campaigns as campaigns_store, worlds as worlds_store
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds_store.create_world("ashgrove")
    cid = ingest_scene.ensure_campaign("Silver Oath", wid)
    croot = campaigns_store.campaign_root(cid)
    ingest_scene.ensure_character(croot, {"name": "marisol"})
    cfg = {"model": "test/model", "openrouter_key": "k"}

    scene1 = {
        "key": "file1-scene01", "title": "Scene One",
        "characters": [{"kind": "characters", "id": "marisol"}],
        "turns": [{"role": "assistant", "speaker": "marisol", "content": "\"You've grown bold.\""}],
    }
    text1 = json_module.dumps({
        "one_line": "a", "summary": "a", "keywords": [], "timeline_events": [],
        "character_state_edits": [{"id": "marisol", "current_state": "wary of julian"}],
        "lore_edits": [], "authored_edits": [], "relationship_deltas": [],
        "bond_changes": [], "plot_movements": [],
    })
    asyncio.run(ingest_scene.ingest_one_scene(cid, scene1, FakeClient(text1), cfg))

    captured = {}
    real_snapshot = ingest_scene.absorb.state_snapshot

    def spying_snapshot(cid_, sid_):
        snap = real_snapshot(cid_, sid_)
        captured.update(snap)
        return snap

    monkeypatch.setattr(ingest_scene.absorb, "state_snapshot", spying_snapshot)

    scene2 = {
        "key": "file1-scene02", "title": "Scene Two",
        "characters": [{"kind": "characters", "id": "marisol"}],
        "turns": [{"role": "assistant", "speaker": "marisol", "content": "\"Still bold, I see.\""}],
    }
    text2 = json_module.dumps({
        "one_line": "b", "summary": "b", "keywords": [], "timeline_events": [],
        "character_state_edits": [], "lore_edits": [], "authored_edits": [],
        "relationship_deltas": [], "bond_changes": [], "plot_movements": [],
    })
    asyncio.run(ingest_scene.ingest_one_scene(cid, scene2, FakeClient(text2), cfg))

    assert any("wary of julian" in v for v in captured.values())
