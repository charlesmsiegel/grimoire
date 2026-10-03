"""Scene tracker field definitions: the built-in set, the world -> campaign ->
scene layering, validation, and the layer routes."""

import pytest

from grimoire import store
from grimoire.store import campaigns, scenes, worlds
from grimoire.store.tracker import fields, paths


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    sid = scenes.create_scene(cid, "Quay")
    return wid, cid, sid


def test_defaults_are_the_ten_fields_with_28_moods():
    assert [f["key"] for f in fields.DEFAULT_FIELDS] == [
        "clothing", "position", "pose", "holding", "condition", "visible_mood",
        "true_mood", "intent", "concealed", "attention"]
    assert len(fields.VISIBLE_MOODS) == 28


def test_world_then_campaign_then_scene_layering(home):
    wid, cid, sid = home
    fields.write_world_layer(wid, {"off": ["attention"],
                                   "change": {"pose": {"label": "Stance"}}})
    fields.write_campaign_layer(cid, {"fields": [
        {"key": "rage", "label": "Rage", "type": "text", "aware": "self", "hint": "h"}]})
    fields.write_scene_layer(cid, sid, {"off": ["clothing"]})
    eff = {f["key"]: f for f in fields.effective(cid, sid)}
    assert eff["attention"]["off"] and eff["clothing"]["off"]
    assert eff["pose"]["label"] == "Stance"
    assert "rage" in eff
    assert [f["key"] for f in fields.active(list(eff.values()))].count("rage") == 1
    # The built-ins are shipped constants: a layer works on a copy.
    assert fields.DEFAULT_FIELDS[2]["label"] == "Pose"
    assert "off" not in fields.DEFAULT_FIELDS[0]


def test_each_layer_sees_only_the_layers_above_it(home):
    wid, cid, sid = home
    fields.write_world_layer(wid, {"off": ["pose"]})
    assert {f["key"] for f in fields.campaign_fields(cid) if f.get("off")} == {"pose"}
    fields.write_campaign_layer(cid, {"off": ["holding"]})
    assert {f["key"] for f in fields.world_fields(wid) if f.get("off")} == {"pose"}
    assert {f["key"] for f in fields.effective(cid, sid) if f.get("off")} == {
        "pose", "holding"}


def test_scene_without_identity_has_no_scene_layer(home):
    _, cid, sid = home
    assert fields.effective(cid, sid) == fields.campaign_fields(cid)
    assert fields.effective(cid, "no-such-scene") == fields.campaign_fields(cid)


def test_scene_layer_may_not_redefine(home):
    _, cid, sid = home
    with pytest.raises(fields.FieldLayerError):
        fields.write_scene_layer(cid, sid, {"change": {"visible_mood": {"type": "text"}}})


def test_a_rejected_scene_layer_leaves_the_scene_file_alone(home):
    from grimoire.store.scenes import identity as scenes_identity
    from grimoire.store.scenes import paths as scenes_paths
    _, cid, sid = home
    # Strip the identity create_scene minted (test-only raw rewrite), so the
    # write has something to mint.
    p = scenes_paths._scene_path(cid, sid)
    p.write_bytes(scenes_identity._drop_identity_line(p.read_bytes()))
    assert scenes_identity.scene_identity(cid, sid) is None
    before = p.read_bytes()
    with pytest.raises(fields.FieldLayerError):
        fields.write_scene_layer(cid, sid, {"change": {"pose": {"label": "x"}}})
    assert scenes_identity.scene_identity(cid, sid) is None
    assert p.read_bytes() == before
    fields.write_scene_layer(cid, sid, {"off": ["pose"]})       # a valid one mints
    assert scenes_identity.scene_identity(cid, sid) is not None


def test_scene_layer_may_add_a_scene_only_field_but_not_shadow(home):
    _, cid, sid = home
    fields.write_scene_layer(cid, sid, {"fields": [
        {"key": "blindfolded", "label": "Blindfolded", "type": "text",
         "aware": "present", "hint": ""}]})
    assert "blindfolded" in {f["key"] for f in fields.effective(cid, sid)}
    fields.write_campaign_layer(cid, {"fields": [
        {"key": "rage", "label": "Rage", "type": "text", "aware": "self", "hint": ""}]})
    with pytest.raises(fields.FieldLayerError):
        fields.write_scene_layer(cid, sid, {"fields": [
            {"key": "rage", "label": "Rage", "type": "text", "aware": "self", "hint": ""}]})


def test_a_later_layer_may_not_add_what_an_earlier_one_defined(home):
    wid, cid, _ = home
    fields.write_world_layer(wid, {"fields": [
        {"key": "rage", "label": "Rage", "type": "text", "aware": "self", "hint": ""}]})
    with pytest.raises(fields.FieldLayerError):
        fields.write_campaign_layer(cid, {"fields": [
            {"key": "rage", "label": "Rage", "type": "text", "aware": "self", "hint": ""}]})


@pytest.mark.parametrize("bad", [
    {"fields": [{"key": "Bad Key", "label": "x", "type": "text", "aware": "self", "hint": ""}]},
    {"fields": [{"key": "x", "label": "x", "type": "number", "aware": "self", "hint": ""}]},
    {"fields": [{"key": "x", "label": "x", "type": "enum", "aware": "self", "hint": ""}]},
    {"fields": [{"key": "x", "label": "x", "type": "enum", "aware": "self", "hint": "",
                 "options": []}]},
    {"fields": [{"key": "x", "label": "x", "type": "text", "aware": "self", "hint": "",
                 "options": ["a"]}]},
    {"fields": [{"key": "x", "label": "x", "type": "text", "aware": "everyone", "hint": ""}]},
    {"fields": [{"key": "clothing", "label": "x", "type": "text", "aware": "self",
                 "hint": ""}]},
    {"fields": [{"key": "x", "label": "x", "type": "text", "aware": "self", "hint": ""},
                {"key": "x", "label": "y", "type": "text", "aware": "self", "hint": ""}]},
    {"change": {"pose": {"key": "stance"}}},
    {"change": {"visible_mood": {"type": "enum", "options": []}}},
    {"change": {"pose": {"type": "enum"}}},
    {"off": "clothing"},
    ["not", "a", "dict"],
])
def test_invalid_layers_rejected(bad):
    with pytest.raises(fields.FieldLayerError):
        fields.validate_layer(bad)


def test_validate_normalizes():
    out = fields.validate_layer({})
    assert out == {"version": 1, "fields": [], "change": {}, "off": []}


def test_changing_an_enum_to_text_drops_its_options(home):
    wid, _, _ = home
    fields.write_world_layer(wid, {"change": {"visible_mood": {"type": "text"}}})
    mood = next(f for f in fields.world_fields(wid) if f["key"] == "visible_mood")
    assert mood["type"] == "text" and "options" not in mood


def test_garbled_layer_reads_empty(home, caplog):
    import logging

    _, cid, _ = home
    p = paths.campaign_layer_path(cid)
    with caplog.at_level(logging.WARNING, logger="grimoire.store.tracker.fields"):
        assert fields.read_layer(p) == {}                 # missing
        assert not caplog.records, "a missing layer is the ordinary case, not news"
        p.write_text("{nope", encoding="utf-8")           # test-only raw write
        assert fields.read_layer(p) == {}
        p.write_text("[1, 2]", encoding="utf-8")
        assert fields.read_layer(p) == {}
    # A layer that is there and was dropped says so: definitions silently
    # reverting to the inherited ones would otherwise have no trace.
    assert len([r for r in caplog.records if "ignoring the field layer" in r.getMessage()]) == 2
    assert fields.campaign_fields(cid) == fields.world_fields(home[0])


def test_digest_tracks_the_field_set():
    base = [dict(f) for f in fields.DEFAULT_FIELDS]
    assert fields.digest(base) == fields.digest([dict(f) for f in fields.DEFAULT_FIELDS])
    assert len(fields.digest(base)) == 16
    assert fields.digest(base) != fields.digest(fields.apply_layer(base, {"off": ["pose"]}))


def test_keys():
    r, v, p = "a" * 32, "b" * 32, "c" * 32
    assert paths.response_key(r, v) == f"r-{r}-{v}"
    assert paths.post_key(p) == f"p-{p}"
    assert paths.valid_key(paths.response_key(r, v)) and paths.valid_key(paths.post_key(p))
    for bad in ("", "r-x-y", f"p-{p}\n", f"../p-{p}", f"p-{p.upper()}"):
        assert not paths.valid_key(bad)


def test_paths_live_under_the_owning_tree(home):
    wid, cid, _ = home
    ident = "d" * 32
    assert paths.world_layer_path(wid) == store.worlds.world_root(wid) / "tracker.json"
    assert paths.campaign_layer_path(cid) == store.campaigns.campaign_root(cid) / "tracker.json"
    assert paths.scene_layer_path(cid, ident) == (
        store.campaigns.campaign_root(cid) / "tracker" / ident / "fields.json")


def test_layer_routes_round_trip(client):
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Quay")
    got = client.get(f"/api/worlds/{wid}/tracker-fields").json()
    assert got["layer"] == {} and len(got["effective"]) == 10
    assert [f["key"] for f in got["inherited"]] == [f["key"] for f in fields.DEFAULT_FIELDS]

    r = client.put(f"/api/worlds/{wid}/tracker-fields", json={"off": ["attention"]})
    assert r.status_code == 200
    assert r.json()["layer"]["off"] == ["attention"]

    camp = client.get(f"/api/campaigns/{cid}/tracker-fields").json()
    assert camp["layer"] == {}
    assert next(f for f in camp["inherited"] if f["key"] == "attention")["off"] is True

    r = client.put(f"/api/campaigns/{cid}/tracker-fields", json={"fields": [
        {"key": "rage", "label": "Rage", "type": "text", "aware": "self", "hint": "h"}]})
    assert r.status_code == 200
    scene = client.get(f"/api/campaigns/{cid}/scenes/{sid}/tracker-fields").json()
    assert scene["layer"] == {}
    assert "rage" in [f["key"] for f in scene["inherited"]]

    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/tracker-fields",
                   json={"off": ["rage"]})
    assert r.status_code == 200
    scene = client.get(f"/api/campaigns/{cid}/scenes/{sid}/tracker-fields").json()
    assert next(f for f in scene["effective"] if f["key"] == "rage")["off"] is True
    assert scene["layer"]["off"] == ["rage"]


def test_layer_routes_reject_bad_bodies_and_unknown_ids(client):
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Quay")
    bad = {"fields": [{"key": "Bad Key", "label": "x", "type": "text",
                       "aware": "self", "hint": ""}]}
    r = client.put(f"/api/worlds/{wid}/tracker-fields", json=bad)
    assert r.status_code == 400 and "key" in r.json()["detail"]
    assert client.put(f"/api/campaigns/{cid}/tracker-fields", json=bad).status_code == 400
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/tracker-fields",
                      json={"change": {"pose": {"label": "x"}}}).status_code == 400
    assert client.get("/api/worlds/nowhere/tracker-fields").status_code == 404
    assert client.put("/api/worlds/nowhere/tracker-fields", json={}).status_code == 404
    assert client.get("/api/campaigns/nowhere/tracker-fields").status_code == 404
    assert client.put("/api/campaigns/nowhere/tracker-fields", json={}).status_code == 404
    assert client.get(f"/api/campaigns/{cid}/scenes/nope/tracker-fields").status_code == 404
    assert client.put(f"/api/campaigns/{cid}/scenes/nope/tracker-fields",
                      json={}).status_code == 404
