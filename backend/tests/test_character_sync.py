from grimoire.store import appearances as ap
from grimoire.store import campaigns, characters, sync, worlds


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                characters.blank_card("Seraphine"))
    cid = campaigns.create_campaign("Run", wid)
    ap.appear(cid, "s1", "characters", "seraphine", "default", "npc")
    return wid, cid


def _edit_world(wid, desc):
    wroot = worlds.world_root(wid)
    card = characters.read_card(wroot, "seraphine", "default")
    card["data"]["description"] = desc
    characters.update_version(wroot, "seraphine", "default", card)


def _edit_mine(cid, desc):
    croot = campaigns.campaign_root(cid)
    card = characters.read_card(croot, "seraphine", "default")
    card["data"]["description"] = desc
    characters.update_version(croot, "seraphine", "default", card)


def test_clean_has_no_incoming(monkeypatch, tmp_path):
    _wid, cid = _setup(monkeypatch, tmp_path)
    assert sync.incoming(cid) == []


def test_world_edit_is_update(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    _edit_world(wid, "moved")
    pend = sync.incoming(cid)
    assert [p["status"] for p in pend] == ["update"]
    assert pend[0]["ref"] == {"kind": "characters", "id": "seraphine"}


def test_both_edit_is_conflict(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    _edit_world(wid, "world")
    _edit_mine(cid, "mine")
    assert [p["status"] for p in sync.incoming(cid)] == ["conflict"]


def test_new_world_version_is_ignored(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    characters.create_version(worlds.world_root(wid), "seraphine", "Corrupted",
                              characters.blank_card("Seraphine"))
    assert sync.incoming(cid) == []  # locked to 'default'; new version irrelevant


def test_accept_copies_and_clears(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    _edit_world(wid, "moved")
    sync.accept(cid, [{"kind": "characters", "id": "seraphine"}])
    assert sync.incoming(cid) == []
    mine = characters.read_card(campaigns.campaign_root(cid), "seraphine", "default")
    assert mine["data"]["description"] == "moved"


def test_reject_keeps_mine(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    _edit_world(wid, "moved")
    sync.reject(cid, [{"kind": "characters", "id": "seraphine"}])
    assert sync.incoming(cid) == []
    mine = characters.read_card(campaigns.campaign_root(cid), "seraphine", "default")
    assert mine["data"]["description"] == ""  # unchanged
    # a further world edit re-surfaces as conflict (base advanced past mine)
    _edit_world(wid, "moved-again")
    assert [p["status"] for p in sync.incoming(cid)] == ["conflict"]


def test_push_counts_include_characters(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    _edit_world(wid, "moved")
    rows = sync.campaigns_for_world(wid)
    assert rows[0]["pending"] == {"new": 0, "update": 1, "conflict": 0}


def test_pc_sync_update(monkeypatch, tmp_path):
    from grimoire.store import pcs
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    pcs.create_pc(worlds.world_root(wid), "Elara", [])
    cid = campaigns.create_campaign("Run", wid)
    ap.appear(cid, "s1", "pcs", "elara", "default", "player")
    # edit the world PC
    persona = pcs.read_persona(worlds.world_root(wid), "elara", "default")
    persona["description"] = "moved"
    pcs.update_version(worlds.world_root(wid), "elara", "default", persona)
    pend = sync.incoming(cid)
    assert [(p["ref"], p["status"]) for p in pend] == [({"kind": "pcs", "id": "elara"}, "update")]
    sync.accept(cid, [{"kind": "pcs", "id": "elara"}])
    assert sync.incoming(cid) == []
    assert pcs.read_persona(campaigns.campaign_root(cid), "elara", "default")["description"] == "moved"


def test_pc_sync_conflict_and_reject_keeps_mine(monkeypatch, tmp_path):
    from grimoire.store import pcs
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    pcs.create_pc(worlds.world_root(wid), "Elara", [])
    cid = campaigns.create_campaign("Run", wid)
    ap.appear(cid, "s1", "pcs", "elara", "default", "player")
    # both sides diverge from base -> conflict
    wp = pcs.read_persona(worlds.world_root(wid), "elara", "default")
    wp["description"] = "world-edit"
    pcs.update_version(worlds.world_root(wid), "elara", "default", wp)
    mp = pcs.read_persona(campaigns.campaign_root(cid), "elara", "default")
    mp["description"] = "my-edit"
    pcs.update_version(campaigns.campaign_root(cid), "elara", "default", mp)
    assert [p["status"] for p in sync.incoming(cid)] == ["conflict"]
    sync.reject(cid, [{"kind": "pcs", "id": "elara"}])
    assert sync.incoming(cid) == []  # base advanced, no re-nag
    assert pcs.read_persona(campaigns.campaign_root(cid), "elara", "default")["description"] == "my-edit"
