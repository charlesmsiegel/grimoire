from grimoire.store import assets, campaigns, characters, overlay, pcs, relationships, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def test_read_missing_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert relationships.read(cid) == {"feelings": {}, "bonds": {}}


def test_feeling_directed_roundtrip(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    relationships.set_feeling(cid, "characters:a", "characters:b", 4, 3, 1, "grateful")
    assert relationships.get_feeling(cid, "characters:a", "characters:b") == {
        "trust": 4, "affection": 3, "tension": 1, "note": "grateful"}
    assert relationships.get_feeling(cid, "characters:b", "characters:a") is None  # asymmetric


def test_bond_key_is_canonical(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    relationships.set_bond(cid, "characters:b", "characters:a", "allies", since_scene="s1")
    assert relationships.get_bond(cid, "characters:a", "characters:b")["type"] == "allies"
    relationships.set_bond(cid, "characters:a", "characters:b", "rivals")  # reorder, no since
    data = relationships.read(cid)
    assert list(data["bonds"]) == ["characters:a|characters:b"]  # single canonical key
    assert data["bonds"]["characters:a|characters:b"] == {"type": "rivals", "since_scene": "s1"}


def test_render_present_lists_feelings_and_bonds(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    relationships.set_feeling(cid, "characters:a", "characters:b", 4, 3, 1, "warm")
    relationships.set_bond(cid, "characters:a", "characters:b", "allies")
    lines = relationships.render_present(cid, ["characters:a", "characters:b"], lambda t: t.split(":")[1].title())
    assert "A → B: trust 4, affection 3, tension 1 (warm)" in lines
    assert "A & B: allies" in lines


def test_actor_name_reads_meta_only(monkeypatch, tmp_path):
    """§25.3 / Decision 9: a name is read from the record's meta -- never from
    its card versions or its image directories -- and is the name the full
    read gave."""
    cid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(campaigns.read_campaign(cid)["meta"]["world"])
    mara, _ = characters.create_character(wroot, "Mara")
    winifred, _ = characters.create_character(wroot, "Winifred")
    overlay.materialize_actor(cid, "characters", winifred)
    characters.set_name(campaigns.campaign_root(cid), winifred, "Winifred of Saltmarch")
    seraphine, _ = pcs.create_pc(wroot, "Seraphine", [])
    shadow, shadow_v = characters.create_character(wroot, "Mara's shadow")
    (wroot / "characters" / shadow / f"{shadow_v}.json").unlink()
    calls: list[str] = []

    def _record(owner, name):
        real = getattr(owner, name)

        def recording(*args, **kwargs):
            calls.append(name)
            return real(*args, **kwargs)

        monkeypatch.setattr(owner, name, recording)

    for owner, name in ((characters, "read_card"), (characters, "read_character"),
                        (pcs, "read_pc"), (assets, "list_images")):
        _record(owner, name)

    assert relationships.actor_name(cid, f"characters:{mara}") == "Mara"
    # The campaign's own copy, not the world's: the overlay root is resolved.
    assert relationships.actor_name(cid, f"characters:{winifred}") == "Winifred of Saltmarch"
    assert relationships.actor_name(cid, f"pcs:{seraphine}") == "Seraphine"
    assert relationships.actor_name(cid, "characters:nobody") == "nobody"
    assert relationships.actor_name(cid, "pcs:nobody") == "nobody"
    # Every version file gone: not one addressable version, so the id, as
    # `read_character` answered it.
    assert relationships.actor_name(cid, f"characters:{shadow}") == shadow
    assert calls == []
