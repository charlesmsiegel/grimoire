"""Quick replies: entry validation, the world and campaign sets on disk, their
layering, and the routes (store/quick_replies.py, routes/quick_replies.py)."""

import json
import re

import pytest

from grimoire import store
from grimoire.store import campaigns, fork, world_bundle, worlds
from grimoire.store import quick_replies as qr


def test_send_is_normalised_to_its_fields_and_gets_an_id():
    out = qr.normalize({"label": " Look around ", "kind": "send", "text": "I take in the room.",
                        "notation": "2d6", "task": None}, campaign=False)
    assert set(out) == {"id", "label", "kind", "text", "mode"}
    assert out["label"] == "Look around" and out["mode"] == "send"
    assert re.fullmatch(r"[0-9a-f]{32}", out["id"])


def test_text_is_stored_unstripped_and_a_given_id_is_kept():
    out = qr.normalize({"id": "keep-me_1", "label": "L", "kind": "direct",
                        "text": "  steer gently\n", "mode": "insert"}, campaign=False)
    assert out == {"id": "keep-me_1", "label": "L", "kind": "direct",
                   "text": "  steer gently\n", "mode": "insert"}


def test_roll_label_is_absent_when_empty_and_collapsed_when_given():
    out = qr.normalize({"label": "Roll", "kind": "roll", "notation": " 2d6+3 "}, campaign=False)
    assert "roll_label" not in out and out["notation"] == "2d6+3"
    assert qr.normalize({"label": "Roll", "kind": "roll", "notation": "1d20",
                         "roll_label": "Perception\n\ncheck"}, campaign=False)["roll_label"] == "Perception check"


def test_task_and_opener_carry_only_their_fields():
    assert set(qr.normalize({"label": "T", "kind": "task", "task": "scene_break", "text": "x"},
                            campaign=False)) == {"id", "label", "kind", "task"}
    assert set(qr.normalize({"label": "O", "kind": "opener", "mode": "send"},
                            campaign=False)) == {"id", "label", "kind"}


@pytest.mark.parametrize("bad", [
    {"label": "x" * 41, "kind": "send", "text": "hi"},
    {"label": "", "kind": "send", "text": "hi"},
    {"label": "   ", "kind": "send", "text": "hi"},
    {"label": 7, "kind": "send", "text": "hi"},
    {"label": "Hi", "kind": "send", "text": "x" * 2001},
    {"label": "Hi", "kind": "direct", "text": "   "},
    {"label": "Hi", "kind": "send"},
    {"label": "Hi", "kind": "send", "text": "hi", "mode": "later"},
    {"label": "Hi", "kind": "roll", "notation": "two dice"},
    {"label": "Hi", "kind": "roll"},
    {"label": "Hi", "kind": "roll", "notation": "1d6", "roll_label": "x" * 81},
    {"label": "Hi", "kind": "task", "task": "absorb"},
    {"label": "Hi", "kind": "teleport"},
    {"label": "Hi"},
    {"id": "has space", "label": "Hi", "kind": "opener"},
    {"id": 5, "label": "Hi", "kind": "opener"},
    "not an object",
])
def test_rule_violations_are_quick_reply_errors(bad):
    with pytest.raises(qr.QuickReplyError) as exc:
        qr.normalize(bad, campaign=False)
    assert exc.value.code == "invalid_quick_reply"


def test_plugin_is_refused_with_its_own_code():
    with pytest.raises(qr.QuickReplyError) as exc:
        qr.normalize({"label": "Run", "kind": "plugin", "command": "x"}, campaign=False)
    assert exc.value.code == "plugin_api_unavailable"


def test_hide_entries_are_exact_and_campaign_only():
    assert qr.normalize({"id": "abc", "hidden": True}, campaign=True) == {"id": "abc", "hidden": True}
    with pytest.raises(qr.QuickReplyError):
        qr.normalize({"id": "abc", "hidden": True, "label": "x"}, campaign=True)
    with pytest.raises(qr.QuickReplyError):
        qr.normalize({"hidden": True}, campaign=True)
    with pytest.raises(qr.QuickReplyError):
        qr.normalize({"id": "abc", "hidden": True}, campaign=False)


def test_set_rules():
    one = {"id": "a", "label": "A", "kind": "opener"}
    with pytest.raises(qr.QuickReplyError):
        qr.validate_set([one, dict(one)], campaign=False)          # duplicate id
    with pytest.raises(qr.QuickReplyError):
        qr.validate_set([{"label": f"R{i}", "kind": "opener"} for i in range(51)], campaign=False)
    with pytest.raises(qr.QuickReplyError):
        qr.validate_set({"replies": []}, campaign=False)            # not a list
    assert len(qr.validate_set([{"label": f"R{i}", "kind": "opener"} for i in range(50)],
                               campaign=False)) == 50


# --- sets on disk -------------------------------------------------------------

@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    return wid, cid


def test_sets_round_trip_and_mint_ids(home):
    wid, cid = home
    empty = qr.world_set(wid)
    assert empty["replies"] == [] and empty["version"] == 1
    saved = qr.write_world(wid, [{"label": "Look around", "kind": "send", "text": "I take in the room."}],
                           empty["digest"])
    assert saved["replies"][0]["text"] == "I take in the room." and len(saved["replies"][0]["id"]) == 32
    assert qr.world_set(wid) == saved
    on_disk = json.loads(qr.world_path(wid).read_text(encoding="utf-8"))
    assert on_disk == {"version": 1, "replies": saved["replies"]}
    assert qr.campaign_set(cid)["replies"] == []


def test_effective_appends_replaces_in_place_and_hides(home):
    wid, cid = home
    qr.write_world(wid, [{"id": "a", "label": "A", "kind": "opener"},
                         {"id": "b", "label": "B", "kind": "send", "text": "b"},
                         {"id": "c", "label": "C", "kind": "send", "text": "c"}],
                   qr.world_set(wid)["digest"])
    qr.write_campaign(cid, [{"id": "b", "label": "B2", "kind": "direct", "text": "steer"},
                            {"id": "c", "hidden": True},
                            {"id": "d", "label": "D", "kind": "task", "task": "scene_break"}],
                      qr.campaign_set(cid)["digest"])
    eff = qr.effective(cid)
    assert [(r["id"], r["label"]) for r in eff] == [("a", "A"), ("b", "B2"), ("d", "D")]
    assert eff[1]["kind"] == "direct"
    assert all(not r.get("hidden") for r in eff)


def test_campaign_with_no_world_has_only_its_own(home, monkeypatch):
    wid, cid = home
    qr.write_world(wid, [{"id": "a", "label": "A", "kind": "opener"}], qr.world_set(wid)["digest"])
    qr.write_campaign(cid, [{"id": "z", "label": "Z", "kind": "opener"}], qr.campaign_set(cid)["digest"])
    monkeypatch.setattr(store.campaigns.read, "read_campaign", lambda c: {"meta": {"world": ""}})
    assert [r["id"] for r in qr.effective(cid)] == ["z"]


def test_garbled_file_reads_empty_and_one_bad_entry_costs_itself(home):
    wid, _ = home
    qr.world_path(wid).write_text("{not json", encoding="utf-8")
    assert qr.world_set(wid)["replies"] == []
    qr.world_path(wid).write_text(json.dumps({"version": 2, "replies": []}), encoding="utf-8")
    assert qr.world_set(wid)["replies"] == []
    qr.world_path(wid).write_text(json.dumps({"version": 1, "replies": [
        {"id": "a", "label": "x" * 99, "kind": "send", "text": "t"},
        {"label": "no id", "kind": "opener"},
        {"id": "b", "label": "B", "kind": "opener"},
        {"id": "b", "label": "B again", "kind": "opener"},
        "junk"]}), encoding="utf-8")
    assert [r["label"] for r in qr.world_set(wid)["replies"]] == ["B"]


def test_a_world_file_cannot_smuggle_a_hide_entry(home):
    wid, _ = home
    qr.world_path(wid).write_text(json.dumps({"version": 1, "replies": [
        {"id": "a", "hidden": True}, {"id": "b", "label": "B", "kind": "opener"}]}), encoding="utf-8")
    assert [r["id"] for r in qr.world_set(wid)["replies"]] == ["b"]


def test_unknown_kind_is_hidden_but_survives_a_put(home):
    wid, _ = home
    future = {"id": "p", "label": "Ping", "kind": "plugin", "command": "/ping"}
    qr.world_path(wid).write_text(json.dumps({"version": 1, "replies": [
        future, {"id": "b", "label": "B", "kind": "opener"}]}), encoding="utf-8")
    seen = qr.world_set(wid)
    assert [r["id"] for r in seen["replies"]] == ["b"]
    qr.write_world(wid, [{"id": "c", "label": "C", "kind": "opener"}], seen["digest"])
    stored = json.loads(qr.world_path(wid).read_text(encoding="utf-8"))["replies"]
    assert stored == [{"id": "c", "label": "C", "kind": "opener"}, future]


def test_a_preserved_entry_still_holds_its_id(home):
    wid, _ = home
    future = {"id": "p", "label": "Ping", "kind": "newer"}
    qr.world_path(wid).write_text(json.dumps({"version": 1, "replies": [future]}), encoding="utf-8")
    with pytest.raises(qr.QuickReplyError):
        qr.write_world(wid, [{"id": "p", "label": "C", "kind": "opener"}], qr.world_set(wid)["digest"])


def test_a_stale_expect_is_refused_and_writes_nothing(home):
    wid, cid = home
    first = qr.campaign_set(cid)["digest"]
    qr.write_campaign(cid, [{"id": "a", "label": "A", "kind": "opener"}], first)
    with pytest.raises(qr.SetChanged):
        qr.write_campaign(cid, [{"id": "z", "hidden": True}], first)
    assert [r["id"] for r in qr.campaign_set(cid)["replies"]] == ["a"]
    with pytest.raises(qr.SetChanged):
        qr.write_world(wid, [], "")                # an expect nobody read never matches


def test_a_hide_naming_no_world_reply_is_dropped_on_save(home):
    wid, cid = home
    qr.write_world(wid, [{"id": "a", "label": "A", "kind": "opener"}], qr.world_set(wid)["digest"])
    saved = qr.write_campaign(cid, [{"id": "a", "hidden": True}, {"id": "gone", "hidden": True}],
                              qr.campaign_set(cid)["digest"])
    assert saved["replies"] == [{"id": "a", "hidden": True}]


def test_writers_refuse_a_missing_world_or_campaign(home):
    with pytest.raises(store.worlds.WorldNotFound):
        qr.write_world("no-such-world", [], qr.digest([]))
    with pytest.raises(store.campaigns.CampaignNotFound):
        qr.write_campaign("no-such-campaign", [], qr.digest([]))
    with pytest.raises(store.campaigns.CampaignNotFound):
        qr.campaign_set("no-such-campaign")
    assert qr.world_set("no-such-world")["replies"] == []


def test_fork_and_bundle_carry_the_sets(home, tmp_path):
    wid, cid = home
    w = qr.write_world(wid, [{"id": "a", "label": "A", "kind": "opener"}], qr.world_set(wid)["digest"])
    c = qr.write_campaign(cid, [{"id": "a", "hidden": True}, {"id": "b", "label": "B", "kind": "opener"}],
                          qr.campaign_set(cid)["digest"])
    child = fork.fork_campaign(cid, "Branch")["id"]
    assert qr.campaign_set(child)["replies"] == c["replies"]
    world_bundle.write_bundle(wid, tmp_path / "b.zip")
    new = world_bundle.import_bundle(tmp_path / "b.zip")
    assert qr.world_set(new)["replies"] == w["replies"]


# --- routes -------------------------------------------------------------------

def _world_and_campaign():
    wid = store.worlds.create_world("Realm")
    return wid, store.campaigns.create_campaign("Saltmarch", wid)


def test_routes_round_trip_and_layer(client):
    wid, cid = _world_and_campaign()
    w = client.get(f"/api/worlds/{wid}/quick-replies").json()
    assert w["replies"] == [] and w["version"] == 1
    r = client.put(f"/api/worlds/{wid}/quick-replies", json={"expect": w["digest"], "replies": [
        {"id": "a", "label": "Look around", "kind": "send", "text": "I take in the room."}]})
    assert r.status_code == 200 and r.json()["replies"][0]["mode"] == "send"
    c = client.get(f"/api/campaigns/{cid}/quick-replies").json()
    assert c["replies"] == [] and [x["id"] for x in c["inherited"]] == ["a"]
    assert client.get(f"/api/campaigns/{cid}/quick-replies/effective").json()["replies"][0]["id"] == "a"
    r = client.put(f"/api/campaigns/{cid}/quick-replies",
                   json={"expect": c["digest"], "replies": [{"id": "a", "hidden": True}]})
    assert r.status_code == 200
    assert r.json()["inherited"][0]["id"] == "a" and r.json()["replies"] == [{"id": "a", "hidden": True}]
    assert client.get(f"/api/campaigns/{cid}/quick-replies/effective").json() == {"replies": []}


@pytest.mark.parametrize("entry,kind", [
    ({"label": "x" * 41, "kind": "send", "text": "t"}, "invalid_quick_reply"),
    ({"label": "R", "kind": "roll", "notation": "2q6"}, "invalid_quick_reply"),
    ({"label": "R", "kind": "nope"}, "invalid_quick_reply"),
    ({"label": "R", "kind": "plugin"}, "plugin_api_unavailable"),
])
def test_bad_entries_are_400_not_422(client, entry, kind):
    wid, cid = _world_and_campaign()
    digest = client.get(f"/api/worlds/{wid}/quick-replies").json()["digest"]
    r = client.put(f"/api/worlds/{wid}/quick-replies", json={"expect": digest, "replies": [entry]})
    assert r.status_code == 400 and r.json()["kind"] == kind and r.json()["detail"]
    digest = client.get(f"/api/campaigns/{cid}/quick-replies").json()["digest"]
    r = client.put(f"/api/campaigns/{cid}/quick-replies", json={"expect": digest, "replies": [entry]})
    assert r.status_code == 400 and r.json()["kind"] == kind


def test_stale_expect_is_409_set_changed(client):
    wid, cid = _world_and_campaign()
    for path in (f"/api/worlds/{wid}/quick-replies", f"/api/campaigns/{cid}/quick-replies"):
        digest = client.get(path).json()["digest"]
        body = {"expect": digest, "replies": [{"id": "a", "label": "A", "kind": "opener"}]}
        assert client.put(path, json=body).status_code == 200
        r = client.put(path, json={**body, "replies": []})
        assert r.status_code == 409 and r.json()["kind"] == "set_changed"


def test_unknown_ids_are_404(client):
    body = {"expect": "", "replies": []}
    assert client.get("/api/worlds/nope/quick-replies").status_code == 404
    assert client.put("/api/worlds/nope/quick-replies", json=body).status_code == 404
    assert client.get("/api/campaigns/nope/quick-replies").status_code == 404
    assert client.put("/api/campaigns/nope/quick-replies", json=body).status_code == 404
    assert client.get("/api/campaigns/nope/quick-replies/effective").status_code == 404
