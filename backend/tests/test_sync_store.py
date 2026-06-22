import pytest

from grimoire.store import campaigns, entities, sync, worlds


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
