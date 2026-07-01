from grimoire.store import absorb, campaigns, playstate, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    return campaigns.create_campaign("Run", wid)


def _char(root, name):
    from grimoire.store import characters
    card = characters.blank_card(name)
    card["data"]["personality"] = "aloof"
    return characters.create_character(root, name, "main", card)[0]  # (cid, vid) -> cid


def test_build_prompt_includes_facts_transcript_and_state():
    msgs = absorb.build_prompt("**You:** hi",
                               {"location": "The Crypt", "date": "2026-01-01", "cast": ["characters/seraphine"]},
                               {"Seraphine": "Wary of the party."})
    assert msgs[0]["role"] == "system"
    user = msgs[1]["content"]
    assert "The Crypt" in user and "seraphine" in user and "**You:** hi" in user
    assert "Seraphine" in user and "Wary of the party." in user


def test_parse_output_extracts_summary_and_edit_lists():
    text = ('```json\n{"one_line": "x", "summary": "y", "keywords": ["k"],'
            ' "timeline_events": [{"date": "d", "text": "t"}],'
            ' "character_state_edits": [{"id": "seraphine", "current_state": "hurt"}],'
            ' "lore_edits": [{"id": "salt-cathedral", "append": "now flooded"}],'
            ' "authored_edits": [{"id": "seraphine", "field": "personality", "text": "colder"}]}\n```')
    out = absorb.parse_output(text)
    assert out["one_line"] == "x" and out["timeline_events"] == [{"date": "d", "text": "t"}]
    assert out["character_state_edits"] == [
        {"id": "seraphine", "current_state": "hurt", "knows": "", "suspects": ""}]
    assert out["lore_edits"] == [{"id": "salt-cathedral", "append": "now flooded"}]
    assert out["authored_edits"] == [{"id": "seraphine", "field": "personality", "text": "colder"}]


def test_parse_output_extracts_knowledge_fields():
    text = ('{"one_line": "", "summary": "", "keywords": [], "timeline_events": [],'
            ' "character_state_edits": [{"id": "seraphine", "current_state": "hurt",'
            '   "knows": "map is fake", "suspects": "elara lies"}],'
            ' "lore_edits": [], "authored_edits": []}')
    out = absorb.parse_output(text)
    assert out["character_state_edits"] == [
        {"id": "seraphine", "current_state": "hurt", "knows": "map is fake", "suspects": "elara lies"}]


def test_parse_output_tolerates_garbage():
    assert absorb.parse_output("no json") == {
        "one_line": "", "summary": "", "keywords": [], "timeline_events": [],
        "character_state_edits": [], "lore_edits": [], "authored_edits": [],
        "relationship_deltas": [], "bond_changes": []}


def test_materialize_builds_before_after(monkeypatch, tmp_path):
    from grimoire.store import appearances, characters, entities, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    entities.create_entity(croot, "lore", "Salt Cathedral", body="A ruined cathedral.")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, "Wary of the party.")
    parsed = {
        "character_state_edits": [{"id": ch, "current_state": "Now travels with them."}],
        "lore_edits": [{"id": "salt-cathedral", "append": "Now flooded."}],
        "authored_edits": [{"id": ch, "field": "personality", "text": "guardedly loyal"}],
    }
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    cs = edits[f"character_state:{ch}"]
    assert cs["kind"] == "character_state" and cs["before"] == "Wary of the party." \
        and cs["after"] == "Now travels with them." and cs["authored"] is False
    lore = edits["lore:salt-cathedral"]
    assert lore["before"] == "A ruined cathedral." and lore["after"].endswith("Now flooded.")
    auth = edits[f"authored:{ch}:personality"]
    assert auth["authored"] is True and auth["before"] == "aloof" and auth["after"] == "guardedly loyal"


def test_materialize_skips_unknown_targets(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import scenes
    sid = scenes.create_scene(cid, "S")
    parsed = {"character_state_edits": [{"id": "ghost", "current_state": "x"}],
              "lore_edits": [{"id": "nope", "append": "y"}],
              "authored_edits": [{"id": "ghost", "field": "personality", "text": "z"}]}
    assert absorb.materialize(cid, sid, parsed) == []


def test_apply_edits_writes_each_kind(monkeypatch, tmp_path):
    from grimoire.store import appearances, characters, entities, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    entities.create_entity(croot, "lore", "Salt Cathedral", body="Ruined.")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    applied = absorb.apply_edits(cid, [
        {"id": f"character_state:{ch}", "kind": "character_state",
         "target": {"kind": "characters", "id": ch}, "field": "current_state", "after": "Loyal now."},
        {"id": "lore:salt-cathedral", "kind": "lore",
         "target": {"kind": "lore", "id": "salt-cathedral"}, "field": "body", "after": "Flooded."},
        {"id": f"authored:{ch}:personality", "kind": "authored",
         "target": {"kind": "characters", "id": ch}, "field": "personality", "after": "guarded"},
    ])
    assert set(applied) == {f"character_state:{ch}", "lore:salt-cathedral", f"authored:{ch}:personality"}
    assert playstate.read_state(croot, ch)["current_state"] == "Loyal now."
    assert entities.read_entity(croot, "lore", "salt-cathedral")["body"].strip() == "Flooded."
    assert characters.read_card(croot, ch, "main")["data"]["personality"] == "guarded"


def test_apply_edits_skips_missing_target(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    applied = absorb.apply_edits(cid, [
        {"id": "lore:nope", "kind": "lore",
         "target": {"kind": "lore", "id": "nope"}, "field": "body", "after": "x"}])
    assert applied == []


def test_apply_edits_authored_rejects_non_card_field(monkeypatch, tmp_path):
    from grimoire.store import appearances, characters, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    applied = absorb.apply_edits(cid, [
        {"id": f"authored:{ch}:name", "kind": "authored",
         "target": {"kind": "characters", "id": ch}, "field": "name", "after": "Hacked"}])
    assert applied == []
    assert characters.read_card(croot, ch, "main")["data"]["name"] == "Seraphine"


def test_parse_output_relationship_and_bond_lists():
    text = ('{"one_line": "", "summary": "", "keywords": [], "timeline_events": [],'
            ' "character_state_edits": [], "lore_edits": [], "authored_edits": [],'
            ' "relationship_deltas": [{"from": "characters:a", "to": "characters:b",'
            '   "trust": 9, "affection": 2, "tension": 1, "note": "warm"}],'
            ' "bond_changes": [{"a": "characters:a", "b": "characters:b", "type": "allies"}]}')
    out = absorb.parse_output(text)
    assert out["relationship_deltas"] == [{"from": "characters:a", "to": "characters:b",
                                           "trust": 5, "affection": 2, "tension": 1, "note": "warm"}]  # 9 clamped
    assert out["bond_changes"] == [{"a": "characters:a", "b": "characters:b", "type": "allies"}]


def test_relationships_snapshot_renders_present(monkeypatch, tmp_path):
    from grimoire.store import appearances, relationships, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    a = _char(croot, "Ann")
    b = _char(croot, "Bo")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", a, "main", "npc")
    appearances.appear(cid, sid, "characters", b, "main", "npc")
    relationships.set_feeling(cid, f"characters:{a}", f"characters:{b}", 4, 3, 1, "warm")
    snap = absorb.relationships_snapshot(cid, sid)
    assert "Ann → Bo: trust 4" in snap


def test_state_snapshot_includes_knowledge(monkeypatch, tmp_path):
    from grimoire.store import appearances, playstate, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, playstate.compose_body("Hurt.", "map is fake", "elara lies"))
    snap = absorb.state_snapshot(cid, sid)
    assert "Hurt." in snap["Seraphine"]
    assert "Knows: map is fake" in snap["Seraphine"]
    assert "Suspects: elara lies" in snap["Seraphine"]


def test_materialize_relationship_and_bond(monkeypatch, tmp_path):
    from grimoire.store import appearances, relationships, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    a = _char(croot, "Ann")
    b = _char(croot, "Bo")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", a, "main", "npc")
    appearances.appear(cid, sid, "characters", b, "main", "npc")
    relationships.set_feeling(cid, f"characters:{a}", f"characters:{b}", 1, 1, 3, "wary")
    relationships.set_feeling(cid, f"characters:{b}", f"characters:{a}", 2, 2, 2, "keep")
    parsed = {
        "relationship_deltas": [
            {"from": f"characters:{a}", "to": f"characters:{b}", "trust": 4, "affection": 3, "tension": 1, "note": "warm"},
            {"from": f"characters:{b}", "to": f"characters:{a}", "trust": 2, "affection": 2, "tension": 2, "note": "keep"},
            {"from": "characters:ghost", "to": f"characters:{b}", "trust": 5, "affection": 0, "tension": 0, "note": ""}],
        "bond_changes": [{"a": f"characters:{a}", "b": f"characters:{b}", "type": "allies"}],
    }
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    rel = edits[f"feeling:characters:{a}->characters:{b}"]
    assert rel["kind"] == "relationship" and rel["before"].startswith("trust 1, affection 1, tension 3") \
        and rel["after"].startswith("trust 4, affection 3, tension 1") and rel["payload"]["trust"] == 4
    assert f"feeling:characters:{b}->characters:{a}" not in edits  # no-op dropped
    assert not any(k.startswith("feeling:characters:ghost") for k in edits)  # unknown dropped
    bond = edits[f"bond:characters:{a}|characters:{b}"]
    assert bond["kind"] == "bond" and bond["after"] == "allies" and bond["payload"]["type"] == "allies"


def test_apply_edits_writes_relationships(monkeypatch, tmp_path):
    from grimoire.store import relationships
    cid = _campaign(monkeypatch, tmp_path)
    applied = absorb.apply_edits(cid, [
        {"id": "feeling:characters:a->characters:b", "kind": "relationship",
         "target": {"kind": "relationships", "id": "characters:a->characters:b"}, "field": "feeling",
         "after": "…", "payload": {"from": "characters:a", "to": "characters:b",
                                    "trust": 4, "affection": 3, "tension": 1, "note": "warm"}},
        {"id": "bond:characters:a|characters:b", "kind": "bond",
         "target": {"kind": "relationships", "id": "characters:a|characters:b"}, "field": "bond",
         "after": "allies", "payload": {"a": "characters:a", "b": "characters:b", "type": "allies"}}])
    assert set(applied) == {"feeling:characters:a->characters:b", "bond:characters:a|characters:b"}
    assert relationships.get_feeling(cid, "characters:a", "characters:b")["trust"] == 4
    assert relationships.get_bond(cid, "characters:a", "characters:b")["type"] == "allies"


def test_relationships_snapshot_tolerates_garbled(monkeypatch, tmp_path):
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    (campaigns.campaign_root(cid) / "relationships.json").write_text("{ not json", encoding="utf-8")
    assert absorb.relationships_snapshot(cid, sid) == ""  # must not raise
