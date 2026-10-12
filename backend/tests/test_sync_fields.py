"""Incoming typed fields preserve stored comparisons and campaign names."""

import pytest

from grimoire.store import campaigns, characters, entities, entity_schema, overlay, sync, worlds
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    wid = worlds.create_world("Realm")
    root = worlds.world_root(wid)
    characters.create_character(root, "Mara")
    characters.create_character(root, "Seraphine")
    cid = campaigns.create_campaign("Saltmarch", wid)
    return cid, root


def test_members_only_incoming_keeps_raw_values_and_campaign_names(case):
    cid, root = case
    eid = entities.create_entity(root, "groups", "Salt Circle", body="The roster.", fields={"members": "characters:mara"})
    overlay.materialize_entity(cid, "groups", eid)
    entities.update_entity(root, "groups", eid, fields={"members": "characters:seraphine"})
    item = sync.incoming(cid)[0]
    world, mine = item["world"], item["mine"]
    assert world["name"] == mine["name"] == "Salt Circle"
    assert world["body"] == mine["body"] == "The roster."
    assert next(row for row in world["fields"] if row["key"] == "members") == {
        "key": "members", "label": "Members", "value": "characters:seraphine", "shown": "Seraphine"}
    assert next(row for row in mine["fields"] if row["key"] == "members")["shown"] == "Mara"


@pytest.mark.parametrize("kind,key,value", [("groups", "members", "Mara"), ("locations", "climate", "0"),
    ("items", "holder", "characters:seraphine"), ("creatures", "habitat", "locations:saltmarch")])
def test_one_sided_fields_are_aligned_in_declared_order(case, kind, key, value):
    cid, root = case
    eid = entities.create_entity(root, kind, "Salt Circle")
    overlay.materialize_entity(cid, kind, eid)
    entities.update_entity(root, kind, eid, fields={key: value})
    item = sync.incoming(cid)[0]
    actual = [row["key"] for row in item["world"]["fields"]]
    assert actual == [field["key"] for field in entity_schema.FIELDS[kind] if field["key"] in actual]
    assert actual == [row["key"] for row in item["mine"]["fields"]]
    assert next(row for row in item["world"]["fields"] if row["key"] == key)["value"] == value
    # Constructors may author an empty declared field; either way both sides pair it.
    assert next(row for row in item["mine"]["fields"] if row["key"] == key)["value"] == ""


def test_world_detached_actor_warning_does_not_change_mine(case):
    cid, root = case
    eid = entities.create_entity(root, "groups", "Salt Circle", fields={"members": "characters:mara"})
    overlay.materialize_entity(cid, "groups", eid)
    overlay.materialize_actor(cid, "characters", "mara")
    overlay.add_detached(cid, "characters/mara")
    entities.update_entity(root, "groups", eid, body="The roster changed.")
    item = sync.incoming(cid)[0]
    world = next(row for row in item["world"]["fields"] if row["key"] == "members")
    mine = next(row for row in item["mine"]["fields"] if row["key"] == "members")
    assert world["value"] == mine["value"] == "characters:mara"
    assert world["shown"] == "Mara (this campaign's own; not the library's)"
    assert mine["shown"] == "Mara"


def test_pending_batch_uses_one_view_and_one_needed_roster(case, monkeypatch):
    cid, root = case
    for name in ("Salt Circle", "Saltmarch"):
        eid = entities.create_entity(root, "groups", name, fields={"members": "characters:mara"})
        overlay.materialize_entity(cid, "groups", eid)
        entities.update_entity(root, "groups", eid, body="The roster changed.")
    views, rosters = [], []
    real_view, real_roster = overlay.view, overlay.character_roster

    def view(cid):
        value = real_view(cid)
        views.append(value)
        return value

    def roster(cid, *, v=None):
        rosters.append(v)
        return real_roster(cid, v=v)

    monkeypatch.setattr(overlay, "view", view)
    monkeypatch.setattr(overlay, "character_roster", roster)
    assert len(sync.incoming(cid)) == 2
    assert len(views) == 1 and rosters == views


def test_native_owned_alias_uses_campaign_name(case):
    cid, root = case
    eid = entities.create_entity(root, "groups", "Salt Circle", fields={"members": "characters:mara"})
    overlay.materialize_entity(cid, "groups", eid)
    overlay.materialize_actor(cid, "characters", "mara")
    overlay.add_detached(cid, "characters/mara")
    (root / "characters" / "mara").rename(root / "characters" / "MARA")
    actor_file = root / "characters" / "MARA" / "character.md"
    meta, body = parse_frontmatter(actor_file.read_text(encoding="utf-8"))
    meta["name"] = "Seraphine"
    actor_file.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    entities.update_entity(root, "groups", eid, fields={"members": "characters:MARA"}, body="The roster changed.")
    item = sync.incoming(cid)[0]
    row = next(r for r in item["world"]["fields"] if r["key"] == "members")
    croot = campaigns.campaign_root(cid)
    alias = (croot / "characters" / "MARA").exists()
    expected = "Mara (this campaign's own; not the library's)" if alias else "Seraphine"
    assert row["value"] == "characters:MARA"
    assert row["shown"] == expected


def test_exact_owned_actor_name_needs_no_native_alias_scan(case, monkeypatch):
    cid, _root = case
    overlay.materialize_actor(cid, "characters", "mara")

    def unnecessary(*_args):
        pytest.fail("exact owned ID performed native alias scan")

    monkeypatch.setattr(sync, "_incoming_identity", unnecessary)
    assert sync._incoming_names(cid, overlay.view(cid), {"characters"},
                                {"characters:mara", "Mara", "characters:../mara"})["characters:mara"] == "Mara"


def test_native_owned_flat_alias_uses_campaign_name(case):
    cid, root = case
    location = entities.create_entity(root, "locations", "Saltmarch")
    overlay.materialize_entity(cid, "locations", location)
    eid = entities.create_entity(root, "groups", "Salt Circle", fields={"headquarters": "locations:saltmarch"})
    overlay.materialize_entity(cid, "groups", eid)
    (root / "locations" / "saltmarch.md").rename(root / "locations" / "SALTMARCH.md")
    entities.update_entity(root, "locations", "SALTMARCH", name="Realm")
    entities.update_entity(root, "groups", eid, fields={"headquarters": "locations:SALTMARCH"})
    item = next(i for i in sync.incoming(cid) if i["ref"]["kind"] == "groups")
    row = next(r for r in item["world"]["fields"] if r["key"] == "headquarters")
    alias = (campaigns.campaign_root(cid) / "locations" / "SALTMARCH.md").exists()
    assert row["value"] == "locations:SALTMARCH"
    assert row["shown"] == ("Saltmarch" if alias else "Realm")
