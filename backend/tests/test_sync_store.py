import pytest

from grimoire.store import appearances as ap
from grimoire.store import campaigns, characters, entities, greetings, pcs, sync, worlds


def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def _setup(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    entities.create_entity(worlds.world_root(wid), "locations", "Seraphine", "v1")
    cid = campaigns.create_campaign("Run", wid)
    return wid, cid


def test_clean_campaign_has_no_incoming(monkeypatch, tmp_path):
    _wid, cid = _setup(monkeypatch, tmp_path)
    assert sync.incoming(cid) == []  # base == world, nothing to offer


def test_world_adds_new_entity(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "locations", "Library", "halls")
    pend = sync.incoming(cid)
    assert len(pend) == 1
    assert pend[0]["ref"] == {"kind": "locations", "id": "library"}
    assert pend[0]["status"] == "new"
    assert "mine" not in pend[0]


def test_world_update_unmodified_local_is_update(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="v2")
    pend = sync.incoming(cid)
    assert [p["status"] for p in pend] == ["update"]
    assert pend[0]["world"]["body"].strip() == "v2"
    assert pend[0]["mine"]["body"].strip() == "v1"


def test_both_changed_is_conflict(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="world-edit")
    entities.update_entity(campaigns.campaign_root(cid), "locations", "seraphine", body="my-edit")
    pend = sync.incoming(cid)
    assert [p["status"] for p in pend] == ["conflict"]
    assert pend[0]["world"]["body"].strip() == "world-edit"
    assert pend[0]["mine"]["body"].strip() == "my-edit"


def test_local_only_change_is_not_offered(monkeypatch, tmp_path):
    _wid, cid = _setup(monkeypatch, tmp_path)
    entities.update_entity(campaigns.campaign_root(cid), "locations", "seraphine", body="mine")
    assert sync.incoming(cid) == []  # world unchanged → nothing incoming


def test_accept_copies_and_clears(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="v2")
    sync.accept(cid, [{"kind": "locations", "id": "seraphine"}])
    assert sync.incoming(cid) == []
    mine = entities.read_entity(campaigns.campaign_root(cid), "locations", "seraphine")
    assert mine["body"].strip() == "v2"


def test_accept_new_creates_file(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "lore", "Salt Pact", "the pact")
    sync.accept(cid, [{"kind": "lore", "id": "salt-pact"}])
    assert sync.incoming(cid) == []
    assert entities.read_entity(campaigns.campaign_root(cid), "lore", "salt-pact")["body"].strip() == "the pact"


def test_reject_keeps_mine_and_does_not_renag(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="v2")
    sync.reject(cid, [{"kind": "locations", "id": "seraphine"}])
    # mine is untouched, and the change is no longer offered
    assert entities.read_entity(campaigns.campaign_root(cid), "locations", "seraphine")["body"].strip() == "v1"
    assert sync.incoming(cid) == []
    # A FURTHER world change re-surfaces it. Reject advanced base to v2 while the
    # campaign kept v1, so base(v2) != mine(v1): the next world edit is a conflict,
    # not a clean update — accepting it would overwrite the v1 we deliberately kept.
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="v3")
    assert [p["status"] for p in sync.incoming(cid)] == ["conflict"]


def test_reject_new_stays_absent_and_quiet(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "lore", "Salt Pact", "x")
    sync.reject(cid, [{"kind": "lore", "id": "salt-pact"}])
    assert sync.incoming(cid) == []
    with pytest.raises(entities.EntityNotFound):
        entities.read_entity(campaigns.campaign_root(cid), "lore", "salt-pact")


def test_accept_nonpending_is_noop(monkeypatch, tmp_path):
    _wid, cid = _setup(monkeypatch, tmp_path)
    sync.accept(cid, [{"kind": "locations", "id": "ghost"}])  # not in world
    assert sync.incoming(cid) == []


def test_accept_or_reject_already_synced_ref_does_not_bump(monkeypatch, tmp_path):
    from grimoire.store import frontmatter

    _wid, cid = _setup(monkeypatch, tmp_path)
    # force a known-old updated timestamp so any spurious touch() is detectable
    mp = campaigns.campaign_meta_path(cid)
    meta, body = frontmatter.parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["updated"] = "2000-01-01T00:00:00Z"
    mp.write_text(frontmatter.dump_frontmatter(meta, body), encoding="utf-8")
    # seraphine is already in sync (base == world): accept/reject are no-ops
    sync.accept(cid, [{"kind": "locations", "id": "seraphine"}])
    sync.reject(cid, [{"kind": "locations", "id": "seraphine"}])
    assert campaigns.read_campaign(cid)["meta"]["updated"] == "2000-01-01T00:00:00Z"


def test_campaigns_for_world_counts(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="v2")
    entities.create_entity(worlds.world_root(wid), "lore", "Pact", "p")
    rows = sync.campaigns_for_world(wid)
    assert len(rows) == 1
    assert rows[0]["id"] == cid
    assert rows[0]["pending"] == {"new": 1, "update": 1, "conflict": 0}


def test_incoming_missing_campaign_raises(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    with pytest.raises(campaigns.CampaignNotFound):
        sync.incoming("nope")


def _greeting_world(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    g = greetings.create_greeting(wroot, "Gala", "c", "v", body="Original.")
    greetings.set_edges(wroot, g, leads_to=[])
    cid = campaigns.create_campaign("Run", wid)
    return wid, wroot, cid, g


def test_incoming_world_greeting_edit_is_update(monkeypatch, tmp_path):
    wid, wroot, cid, g = _greeting_world(monkeypatch, tmp_path)
    greetings.update_greeting(wroot, g, body="Changed.")
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    item = items[("greetings", g)]
    assert item["status"] == "update"
    assert item["world"]["body"] == "Changed."
    sync.accept(cid, [{"kind": "greetings", "id": g}])
    croot = campaigns.campaign_root(cid)
    assert greetings.read_greeting(croot, g)["body"] == "Changed."
    assert sync.incoming(cid) == []


def test_incoming_greeting_conflict_and_reject(monkeypatch, tmp_path):
    wid, wroot, cid, g = _greeting_world(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    greetings.update_greeting(wroot, g, body="World edit.")
    greetings.update_greeting(croot, g, body="Campaign edit.")
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    assert items[("greetings", g)]["status"] == "conflict"
    sync.reject(cid, [{"kind": "greetings", "id": g}])
    assert greetings.read_greeting(croot, g)["body"] == "Campaign edit."
    assert sync.incoming(cid) == []


def test_incoming_plotmap_update_accept(monkeypatch, tmp_path):
    wid, wroot, cid, g = _greeting_world(monkeypatch, tmp_path)
    g2 = greetings.create_greeting(wroot, "Next", "c", "v")
    greetings.set_edges(wroot, g, leads_to=[g2])
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    assert items[("plotmap", "plotmap")]["status"] == "update"
    sync.accept(cid, [{"kind": "plotmap", "id": "plotmap"},
                      {"kind": "greetings", "id": g2}])
    croot = campaigns.campaign_root(cid)
    assert greetings.edges_of(greetings.read_plotmap(croot), g)["leads_to"] == [g2]
    assert (croot / "greetings" / f"{g2}.md").exists()


def _actor_world(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    char_id, _ = characters.create_character(wroot, "Mara", "young")
    characters.create_version(wroot, char_id, "veteran", characters.blank_card("Mara"))
    cid = campaigns.create_campaign("Run", wid)
    return wid, wroot, cid, char_id


def test_unpicked_actor_world_edit_is_update_and_accept_recopies(monkeypatch, tmp_path):
    wid, wroot, cid, char_id = _actor_world(monkeypatch, tmp_path)
    card = characters.blank_card("Mara")
    card["data"]["description"] = "changed"
    characters.update_version(wroot, char_id, "young", card)
    characters.delete_version(wroot, char_id, "veteran")
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    assert items[("characters", char_id)]["status"] == "update"
    sync.accept(cid, [{"kind": "characters", "id": char_id}])
    croot = campaigns.campaign_root(cid)
    assert characters.read_card(croot, char_id, "young")["data"]["description"] == "changed"
    assert not (croot / "characters" / char_id / "veteran.json").exists()  # deletion propagates
    assert sync.incoming(cid) == []


def test_unpicked_actor_conflict_and_reject(monkeypatch, tmp_path):
    wid, wroot, cid, char_id = _actor_world(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    characters.update_version(wroot, char_id, "young", characters.blank_card("W-Mara"))
    characters.update_version(croot, char_id, "young", characters.blank_card("C-Mara"))
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    assert items[("characters", char_id)]["status"] == "conflict"
    sync.reject(cid, [{"kind": "characters", "id": char_id}])
    assert characters.read_card(croot, char_id, "young")["data"]["name"] == "C-Mara"
    assert sync.incoming(cid) == []


def test_new_world_actor_is_new(monkeypatch, tmp_path):
    wid, wroot, cid, char_id = _actor_world(monkeypatch, tmp_path)
    new_id, _ = characters.create_character(wroot, "Rowan")
    items = {(i["ref"]["kind"], i["ref"]["id"]): i for i in sync.incoming(cid)}
    assert items[("characters", new_id)]["status"] == "new"
    sync.accept(cid, [{"kind": "characters", "id": new_id}])
    croot = campaigns.campaign_root(cid)
    assert (croot / "characters" / new_id / "character.md").exists()


def test_locked_actor_new_world_version_invisible(monkeypatch, tmp_path):
    wid, wroot, cid, char_id = _actor_world(monkeypatch, tmp_path)
    ap.pick_version(cid, "characters", char_id, "young")
    characters.create_version(wroot, char_id, "elder", characters.blank_card("Mara"))
    assert sync.incoming(cid) == []  # only the locked version's own edits would show


def test_incoming_reads_campaign_meta_and_manifest_once(monkeypatch, tmp_path):
    from pathlib import Path

    wid, cid = _setup(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Ada")  # exercise the actor passes too
    entities.update_entity(worlds.world_root(wid), "locations", "seraphine", body="v2")
    reads: list[str] = []
    orig = Path.read_text

    def counting(self, *args, **kwargs):
        reads.append(self.name)
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    pend = sync.incoming(cid)
    assert {p["ref"]["id"] for p in pend} == {"seraphine", "ada"}
    assert reads.count("campaign.md") == 1
    assert reads.count("sync.md") == 1
    assert reads.count("appearances.json") <= 1
