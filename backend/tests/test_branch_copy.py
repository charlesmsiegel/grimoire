"""The per-module copy helpers a scene branch is built from (play controls III).

Each record a scene owns is copied by the module that owns it, so the branch
primitive (`store/branch.py`) never reaches into another module's file format.
These pin each helper on its own; `test_branch_store.py` pins the assembly.
"""

import json
import uuid

import pytest

from grimoire import routes, store
from grimoire.store import campaigns, dice, rolls, worlds
from grimoire.store.appearances import paths as appearances_paths
from grimoire.store.audit import baselines
from grimoire.store.tracker import paths as tracker_paths
from grimoire.store.tracker import records
from tests.llm_fakes import FakeLLM


def seed(client, module=None):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid, module=module)
    sid = store.scenes.create_scene(cid, "Mara")
    for name in ("Mara", "Winifred"):
        response = client.post(f"/api/campaigns/{cid}/characters", json={"name": name})
        assert response.status_code == 200, response.text
        actor = response.json()["character"]
        response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor})
        assert response.status_code == 200, response.text
    return cid, sid


def _turn(client, cid, sid, content="Hello"):
    fake = FakeLLM([['Mara answers.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                    json={"content": content, "speaker_ref": "characters:mara"})
    assert r.status_code == 200, r.text


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Saltmarch", wid)


def _hex():
    return uuid.uuid4().hex


# --- responses ---------------------------------------------------------------


def _cloned(client):
    cid, sid = seed(client)
    _turn(client, cid, sid)
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    dst = store.scenes.create_scene(cid, "Mara (branch)")
    return cid, sid, rid, dst, _hex()


def test_cloned_response_has_its_own_id_and_snapshot(client):
    cid, sid, rid, dst, new = _cloned(client)
    store.responses.clone_for_branch(cid, sid, dst, {rid: new}, set())
    src = store.responses.get(cid, sid, rid, private=True)
    out = store.responses.get(cid, dst, new, private=True)
    assert out["id"] == new
    assert out["snapshot_ref"].startswith(f"{new}/")
    assert out["snapshot"] == src["snapshot"]
    assert [v["id"] for v in out["variants"]] == [v["id"] for v in src["variants"]]
    assert not out.get("mechanically_locked")


def test_a_clone_is_locked_only_when_named(client):
    cid, sid, rid, dst, new = _cloned(client)
    store.responses.clone_for_branch(cid, sid, dst, {rid: new}, {new})
    assert store.responses.get(cid, dst, new)["mechanically_locked"] is True


def test_a_source_lock_is_not_inherited(client):
    cid, sid, rid, dst, new = _cloned(client)
    store.responses.mark_applied(cid, sid)
    store.responses.clone_for_branch(cid, sid, dst, {rid: new}, set())
    assert "mechanically_locked" not in store.responses.get(cid, dst, new)


def test_an_unfinished_round_is_superseded_in_the_clone(client):
    cid, sid, rid, dst, new = _cloned(client)
    round_id = store.responses.get(cid, sid, rid)["round_id"]
    store.responses.update_round(cid, sid, round_id, status="paused")
    store.responses.clone_for_branch(cid, sid, dst, {rid: new}, set())
    data = json.loads((campaigns.campaign_root(cid) / "responses.json").read_text())
    src_scope = data["scenes"][store.scenes.scene_identity(cid, sid)]
    dst_scope = data["scenes"][store.scenes.scene_identity(cid, dst)]
    assert dst_scope["rounds"][round_id]["status"] == "superseded"
    assert src_scope["rounds"][round_id]["status"] == "paused"
    assert dst_scope["rounds"][round_id]["pending_response"] in (new, None)


def test_an_unknown_response_id_is_skipped(client):
    cid, sid, rid, dst, new = _cloned(client)
    store.responses.clone_for_branch(cid, sid, dst, {rid: new, _hex(): _hex()}, set())
    data = json.loads((campaigns.campaign_root(cid) / "responses.json").read_text())
    assert set(data["scenes"][store.scenes.scene_identity(cid, dst)]["responses"]) == {new}


# --- tracker -----------------------------------------------------------------


def test_tracker_records_follow_the_new_response_ids(cid):
    src_ident, dst_ident = _hex(), _hex()
    old, new, vid, post = _hex(), _hex(), _hex(), _hex()
    snap = {"Mara": {"present": True, "fields": {"mood": "wary"}}}
    records.save(cid, src_ident, f"r-{old}-{vid}", snap, changed=[], fields_digest="d", model="m")
    records.save(cid, src_ident, f"p-{post}", snap, changed=[], fields_digest="d", model="m")
    layer = tracker_paths.scene_layer_path(cid, src_ident)
    layer.write_text('{"fields": []}', encoding="utf-8")
    before = records.read_index(cid, src_ident)
    records.clone(cid, src_ident, dst_ident, {old: new})
    assert set(records.read_index(cid, dst_ident)) == {f"r-{new}-{vid}", f"p-{post}"}
    assert (records.read_snapshot(cid, dst_ident, f"r-{new}-{vid}")
            == records.read_snapshot(cid, src_ident, f"r-{old}-{vid}"))
    assert records.read_index(cid, src_ident) == before
    assert tracker_paths.scene_layer_path(cid, dst_ident).read_text(encoding="utf-8") == \
        '{"fields": []}'


def test_a_tracker_record_for_an_unmapped_response_is_not_copied(cid):
    src_ident, dst_ident = _hex(), _hex()
    old, vid = _hex(), _hex()
    records.save(cid, src_ident, f"r-{old}-{vid}", {}, changed=[], fields_digest="d", model="m")
    records.clone(cid, src_ident, dst_ident, {})
    assert records.read_index(cid, dst_ident) == {}


def test_a_scene_with_no_tracker_copies_nothing(cid):
    dst_ident = _hex()
    records.clone(cid, _hex(), dst_ident, {})
    assert not tracker_paths.scene_dir(cid, dst_ident).exists()


# --- appearances -------------------------------------------------------------


def test_join_branch_seats_who_was_present_at_the_branch_point(cid):
    appearances_paths._write(cid, {
        "characters/mara": {"scenes": ["001--src"],
                            "presence": {"001--src": [{"start": 0, "end": None}]}},
        "characters/winifred": {"scenes": [],
                                "presence": {"001--src": [{"start": 0, "end": 2}]}},
        "characters/seraphine": {"scenes": ["001--src"]},          # legacy: no presence
        "pcs/realm": {"scenes": ["001--src"],
                      "presence": {"001--src": [{"start": 5, "end": None}]}},
    })
    appearances_paths.join_branch(cid, "001--src", "001--dst", through=3)
    data = appearances_paths.record(cid)
    assert "001--dst" in data["characters/mara"]["scenes"]
    assert "001--dst" not in data["characters/winifred"]["scenes"]
    assert data["characters/winifred"]["presence"]["001--dst"] == [{"start": 0, "end": 2}]
    assert "001--dst" in data["characters/seraphine"]["scenes"]
    assert "001--dst" not in data["pcs/realm"]["scenes"]       # arrived after the point
    assert "001--dst" not in data["pcs/realm"].get("presence", {})
    assert data["characters/mara"]["presence"]["001--src"] == [{"start": 0, "end": None}]


# --- rolls -------------------------------------------------------------------


def test_copy_for_branch_copies_the_named_entries_in_order(cid):
    r = dice.roll("1d20", 7)
    a = rolls.append(cid, "001--src", None, r)
    b = rolls.append(cid, "001--src", "Brawl", r, proposal="p1", tier="success")
    rolls.append(cid, "002--other", None, r)
    copies = rolls.copy_for_branch(cid, "001--src", "001--dst", [b["id"], a["id"]])
    assert [c["id"] for c in copies] == ["r4", "r5"]
    assert [c["label"] for c in copies] == ["Brawl", None]
    assert all(c["scene"] == "001--dst" and "proposal" not in c for c in copies)
    assert copies[0]["tier"] == "success"
    assert [e["id"] for e in rolls.read(cid)] == ["r1", "r2", "r3", "r4", "r5"]


def test_copy_for_branch_ignores_another_scenes_entry(cid):
    r = dice.roll("1d20", 7)
    other = rolls.append(cid, "002--other", None, r)
    assert rolls.copy_for_branch(cid, "001--src", "001--dst", [other["id"]]) == []
    assert len(rolls.read(cid)) == 1


# --- audit baseline ----------------------------------------------------------


def test_copy_baseline_copies_the_source_entry(cid):
    entry = {"module": "m", "schema": {"hash": "h", "mtime": 1}, "sheets": {}}
    baselines._write(cid, {"001--src": entry})
    baselines.copy_baseline(cid, "001--src", "001--dst")
    assert baselines.read_baselines(cid)["001--dst"] == entry
    baselines.drop_baseline(cid, "001--dst")
    assert "001--dst" not in baselines.read_baselines(cid)
    assert baselines.read_baselines(cid)["001--src"] == entry


def test_copy_baseline_without_a_source_entry_writes_nothing(cid):
    baselines.copy_baseline(cid, "001--src", "001--dst")
    assert "001--dst" not in baselines.read_baselines(cid)
