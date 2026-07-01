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
    assert out["character_state_edits"] == [{"id": "seraphine", "current_state": "hurt"}]
    assert out["lore_edits"] == [{"id": "salt-cathedral", "append": "now flooded"}]
    assert out["authored_edits"] == [{"id": "seraphine", "field": "personality", "text": "colder"}]


def test_parse_output_tolerates_garbage():
    assert absorb.parse_output("no json") == {
        "one_line": "", "summary": "", "keywords": [], "timeline_events": [],
        "character_state_edits": [], "lore_edits": [], "authored_edits": []}


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
