import json

from grimoire.store import absorb, audit, campaigns, changes, playstate, sheets, worlds


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
    # Omitted knows/suspects keys stay ABSENT (presence preserved for keep-on-omit).
    assert out["character_state_edits"] == [{"id": "seraphine", "current_state": "hurt"}]
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
        "character_state_edits": [], "group_state_edits": [], "lore_edits": [], "authored_edits": [],
        "relationship_deltas": [], "bond_changes": [], "plot_movements": [],
        "new_characters": [], "new_locations": [], "new_lore": []}


def test_parse_output_plot_movements():
    text = ('{"one_line": "", "summary": "", "keywords": [], "timeline_events": [],'
            ' "character_state_edits": [], "lore_edits": [], "authored_edits": [],'
            ' "plot_movements": [{"id": "the-map", "title": "The map", "status": "advanced",'
            '   "beat": "It is a forgery."},'
            '  {"title": "New thread", "status": "bogus", "beat": "starts"}]}')
    out = absorb.parse_output(text)
    assert out["plot_movements"] == [
        {"id": "the-map", "title": "The map", "status": "advanced", "beat": "It is a forgery."},
        {"id": "", "title": "New thread", "status": "open", "beat": "starts"}]  # bad status -> open


def test_parse_output_treats_null_id_as_new_thread_not_the_string_none():
    """A model emitting explicit JSON null for "id" (its natural way to say "no existing
    thread") must parse the same as omitting the key — not become the literal string
    "None", which materialize() would then treat as a real (bogus) thread id."""
    text = ('{"one_line": "", "summary": "", "keywords": [], "timeline_events": [],'
            ' "character_state_edits": [{"id": null, "current_state": "x"}],'
            ' "lore_edits": [], "authored_edits": [],'
            ' "plot_movements": [{"id": null, "title": "New thread", "status": "open",'
            '   "beat": "starts"}]}')
    out = absorb.parse_output(text)
    assert out["character_state_edits"][0]["id"] == ""
    assert out["plot_movements"][0]["id"] == ""


def test_plot_snapshot_renders_open_threads(monkeypatch, tmp_path):
    from grimoire.store import plot, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    plot.set_movement(cid, "the-map", "The map", "advanced", "It is a forgery.", "s12")
    plot.set_movement(cid, "done", "Done thread", "closed", "resolved", "s5")
    snap = absorb.plot_snapshot(cid)
    assert "the-map" in snap and "The map" in snap and "It is a forgery." in snap
    assert "Done thread" not in snap  # closed excluded


def test_plot_snapshot_tolerates_garbled(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "plot.json").write_text("{ not json", encoding="utf-8")
    assert absorb.plot_snapshot(cid) == ""


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


def test_materialize_character_state_edit_strips_kind_prefix(monkeypatch, tmp_path):
    """The model echoes ids from the "Present: characters/<id>, ..." context line, so
    character_state_edits arrives "characters/<id>"-prefixed far more often than bare —
    materialize must resolve that form, not just the bare id the model sometimes uses."""
    from grimoire.store import appearances, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    parsed = {"character_state_edits": [
        {"id": f"characters/{ch}", "current_state": "Now travels with them."}]}
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    cs = edits[f"character_state:{ch}"]
    assert cs["before"] == "" and cs["after"] == "Now travels with them."
    assert cs["target"] == {"kind": "characters", "id": ch}


def test_materialize_character_state_edit_not_dropped_for_inherited_character(monkeypatch, tmp_path):
    """A thin campaign never copies characters into the campaign root; a state edit
    for a cast member that's still purely inherited (never appeared/materialized)
    must not be silently dropped just because campaigns.campaign_root(cid) lacks
    the character dir."""
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    wid = campaigns.read_campaign(cid)["meta"]["world"]
    wroot = worlds.world_root(wid)
    ch = _char(wroot, "Seraphine")
    croot = campaigns.campaign_root(cid)
    assert not (croot / "characters" / ch).exists()   # never materialized
    sid = scenes.create_scene(cid, "S")
    parsed = {"character_state_edits": [{"id": ch, "current_state": "Now travels with them."}]}
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    assert f"character_state:{ch}" in edits
    cs = edits[f"character_state:{ch}"]
    assert cs["before"] == "" and cs["after"] == "Now travels with them."


def test_materialize_character_state_edit_label_uses_inherited_character_name(monkeypatch, tmp_path):
    """The staged edit's label must show the character's display NAME, not the raw
    slug, even when the character is still purely inherited (never materialized
    campaign-side) — the label helper must resolve through the overlay, not just
    the campaign's own copy."""
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    wid = campaigns.read_campaign(cid)["meta"]["world"]
    wroot = worlds.world_root(wid)
    ch = _char(wroot, "Seraphine")
    croot = campaigns.campaign_root(cid)
    assert not (croot / "characters" / ch).exists()   # never materialized
    sid = scenes.create_scene(cid, "S")
    parsed = {"character_state_edits": [{"id": ch, "current_state": "Now travels with them."}]}
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    cs = edits[f"character_state:{ch}"]
    assert cs["label"] == "Seraphine — current state"


def test_materialize_character_state_edit_ignores_pcs_prefix(monkeypatch, tmp_path):
    """playstate.py only tracks NPCs (see its module docstring) — a pcs-prefixed id must
    be dropped, not misfiled under the characters/ tree using the PC's id as if it were
    a character slug."""
    cid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import scenes
    sid = scenes.create_scene(cid, "S")
    parsed = {"character_state_edits": [
        {"id": "pcs/shia", "current_state": "Should not be applied."}]}
    assert absorb.materialize(cid, sid, parsed) == []


def test_materialize_composes_knowledge_blob(monkeypatch, tmp_path):
    from grimoire.store import appearances, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, "Wary of the party.")
    parsed = {"character_state_edits": [
        {"id": ch, "current_state": "Travels with them.", "knows": "map is fake", "suspects": ""}]}
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    cs = edits[f"character_state:{ch}"]
    assert cs["kind"] == "character_state" and cs["authored"] is False and "payload" not in cs
    assert cs["before"] == "Wary of the party."  # bare (no prior knowledge)
    assert "## Current state\nTravels with them." in cs["after"]
    assert "## Knows\nmap is fake" in cs["after"]
    assert "## Suspects" not in cs["after"]


def test_materialize_preserves_omitted_knowledge(monkeypatch, tmp_path):
    # An absorb that re-emits only current_state must NOT wipe stored knows/suspects.
    from grimoire.store import appearances, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, playstate.compose_body("Old mood.", "the map is fake", "elara lies"))
    parsed = {"character_state_edits": [{"id": ch, "current_state": "New mood."}]}  # no knowledge keys
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    cs = edits[f"character_state:{ch}"]
    assert "New mood." in cs["after"]
    assert "## Knows\nthe map is fake" in cs["after"]      # preserved
    assert "## Suspects\nelara lies" in cs["after"]        # preserved


def test_materialize_explicit_empty_clears_knowledge(monkeypatch, tmp_path):
    # An explicit "" for suspects DOES clear it (e.g. a suspicion resolved into knows).
    from grimoire.store import appearances, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, playstate.compose_body("Mood.", "old", "a hunch"))
    parsed = {"character_state_edits": [
        {"id": ch, "current_state": "Mood.", "knows": "confirmed", "suspects": ""}]}
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    cs = edits[f"character_state:{ch}"]
    assert "## Knows\nconfirmed" in cs["after"]
    assert "## Suspects" not in cs["after"]  # explicitly cleared


def test_materialize_drops_noop_state(monkeypatch, tmp_path):
    from grimoire.store import appearances, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, "Unchanged.")
    parsed = {"character_state_edits": [
        {"id": ch, "current_state": "Unchanged.", "knows": "", "suspects": ""}]}
    assert absorb.materialize(cid, sid, parsed) == []


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
    applied, _ = absorb.apply_edits(cid, [
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
    applied, _ = absorb.apply_edits(cid, [
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
    applied, _ = absorb.apply_edits(cid, [
        {"id": f"authored:{ch}:name", "kind": "authored",
         "target": {"kind": "characters", "id": ch}, "field": "name", "after": "Hacked"}])
    assert applied == []
    assert characters.read_card(croot, ch, "main")["data"]["name"] == "Seraphine"


def test_apply_edits_new_character_creates_and_casts_npc(monkeypatch, tmp_path):
    from grimoire.store import appearances, characters, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "S")
    applied, _ = absorb.apply_edits(cid, [
        {"id": "new_character:old-bram", "kind": "new_character",
         "target": {"kind": "characters", "id": ""}, "field": "description",
         "after": "[character(\"Old Bram\") { Occupation(\"innkeep\") }]\n\nBram kept the inn.",
         "payload": {"name": "Old Bram", "sd_prompt": "an old innkeeper",
                     "personality": "gruff but kind",
                     "mes_example": "<START>\n{{user}}: A room?\n{{char}}: Aye."}}], sid)
    assert applied == ["new_character:old-bram"]
    new_char = next(c for c in characters.list_characters(croot) if c["name"] == "Old Bram")
    card = characters.read_card(croot, new_char["id"], "default")
    expected_description = ("[character(\"Old Bram\") { Occupation(\"innkeep\") }]"
                            "\n\nBram kept the inn.\n\n## Play Provenance\nConfidence: thin")
    assert card["data"]["description"] == expected_description
    assert card["data"]["personality"] == "gruff but kind"
    # {{char}} is baked to the card's own name ({{user}} stays literal): a stored
    # {{char}} would expand to the whole present cast in multi-NPC scenes.
    assert card["data"]["mes_example"] == "<START>\n{{user}}: A room?\nOld Bram: Aye."
    assert card["data"]["extensions"]["sd_prompt"] == "an old innkeeper"
    assert appearances.is_appeared(cid, "characters", new_char["id"])
    # every card field written lands in the change log (baked), not just description
    from grimoire.store import changes
    logged = changes.read(cid)[f"characters/{new_char['id']}"]["fields"]
    assert [(f["field"], f["after"]) for f in logged] == [
        ("description", expected_description),
        ("personality", "gruff but kind"),
        ("mes_example", "<START>\n{{user}}: A room?\nOld Bram: Aye.")]


def test_apply_edits_new_character_without_sid_skips_casting(monkeypatch, tmp_path):
    from grimoire.store import characters
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    applied, _ = absorb.apply_edits(cid, [
        {"id": "new_character:old-bram", "kind": "new_character",
         "target": {"kind": "characters", "id": ""}, "field": "description", "after": "x",
         "payload": {"name": "Old Bram", "sd_prompt": ""}}])  # no sid
    assert applied == ["new_character:old-bram"]
    assert any(c["name"] == "Old Bram" for c in characters.list_characters(croot))


def test_apply_edits_new_location_auto_links_empty_scene(monkeypatch, tmp_path):
    from grimoire.store import entities, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "S")
    applied, _ = absorb.apply_edits(cid, [
        {"id": "new_location:the-crypt", "kind": "new_location",
         "target": {"kind": "locations", "id": ""}, "field": "body", "after": "A cold crypt.",
         "payload": {"name": "The Crypt", "keys": "crypt", "sd_prompt": "a dark crypt",
                     "current_setting": True}}], sid)
    assert applied == ["new_location:the-crypt"]
    got = entities.read_entity(croot, "locations", "the-crypt")
    assert got["meta"]["sd_prompt"] == "a dark crypt" and got["meta"]["keys"] == "crypt"
    assert scenes.get_location_history(cid, sid) == ["the-crypt"]


def test_apply_edits_new_location_leaves_existing_location_alone(monkeypatch, tmp_path):
    from grimoire.store import entities, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "S")
    entities.create_entity(croot, "locations", "Old Dock")
    scenes.set_location(cid, sid, "old-dock")
    absorb.apply_edits(cid, [
        {"id": "new_location:the-crypt", "kind": "new_location",
         "target": {"kind": "locations", "id": ""}, "field": "body", "after": "A cold crypt.",
         "payload": {"name": "The Crypt", "keys": "", "sd_prompt": "", "current_setting": True}}], sid)
    assert scenes.get_location_history(cid, sid) == ["old-dock"]  # untouched


def test_apply_edits_new_lore_creates_entity(monkeypatch, tmp_path):
    from grimoire.store import entities, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "S")
    applied, _ = absorb.apply_edits(cid, [
        {"id": "new_lore:salt-pact", "kind": "new_lore",
         "target": {"kind": "lore", "id": ""}, "field": "body", "after": "An old pact.",
         "payload": {"name": "Salt Pact", "keys": "pact"}}], sid)
    assert applied == ["new_lore:salt-pact"]
    got = entities.read_entity(croot, "lore", "salt-pact")
    assert got["body"].strip() == "An old pact." and got["meta"]["keys"] == "pact"


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


def test_parse_output_new_entities():
    text = ('{"one_line": "", "summary": "", "keywords": [], "timeline_events": [],'
            ' "character_state_edits": [], "lore_edits": [], "authored_edits": [],'
            ' "new_characters": [{"name": "Old Bram", "description": "W++ block",'
            '   "history": "Born at sea.", "personality": "gruff but kind",'
            '   "mes_example": "<START>\\n{{user}}: Hello\\n{{char}}: Hmph.",'
            '   "evidence": "Old Bram warned the party away from the locked pier.",'
            '   "confidence": "sketched", "open_questions": "Why he fears the pier.",'
            '   "sd_prompt": "an old man"}],'
            ' "new_locations": [{"name": "The Crypt", "body": "cold", "keys": "crypt",'
            '   "sd_prompt": "a dark crypt", "current_setting": true}],'
            ' "new_lore": [{"name": "Salt Pact", "body": "an old pact", "keys": "pact"}]}')
    out = absorb.parse_output(text)
    assert out["new_characters"] == [{"name": "Old Bram", "description": "W++ block",
                                      "history": "Born at sea.", "personality": "gruff but kind",
                                      "mes_example": "<START>\n{{user}}: Hello\n{{char}}: Hmph.",
                                      "evidence": "Old Bram warned the party away from the locked pier.",
                                      "confidence": "sketched",
                                      "open_questions": "Why he fears the pier.",
                                      "sd_prompt": "an old man"}]
    assert out["new_locations"] == [{"name": "The Crypt", "body": "cold", "keys": "crypt",
                                     "sd_prompt": "a dark crypt", "current_setting": True}]
    assert out["new_lore"] == [{"name": "Salt Pact", "body": "an old pact", "keys": "pact"}]


def test_parse_output_new_locations_current_setting_defaults_false():
    text = ('{"one_line": "", "summary": "", "keywords": [], "timeline_events": [],'
            ' "character_state_edits": [], "lore_edits": [], "authored_edits": [],'
            ' "new_locations": [{"name": "The Crypt", "body": "cold", "keys": ""}]}')
    out = absorb.parse_output(text)
    assert out["new_locations"] == [
        {"name": "The Crypt", "body": "cold", "keys": "", "sd_prompt": "", "current_setting": False}]


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


def test_materialize_relationship_label_uses_inherited_character_names(monkeypatch, tmp_path):
    """Same overlay-resolution requirement as the character_state label, but for
    relationship/bond edit labels: an inherited (never-materialized) actor's
    display name must still show, not the raw slug."""
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    wid = campaigns.read_campaign(cid)["meta"]["world"]
    wroot = worlds.world_root(wid)
    a = _char(wroot, "Ann")
    b = _char(wroot, "Bo")
    croot = campaigns.campaign_root(cid)
    assert not (croot / "characters" / a).exists() and not (croot / "characters" / b).exists()
    sid = scenes.create_scene(cid, "S")   # appear() would materialize the actor, so skip it here
    parsed = {
        "relationship_deltas": [
            {"from": f"characters:{a}", "to": f"characters:{b}", "trust": 4, "affection": 3, "tension": 1, "note": "warm"}],
        "bond_changes": [{"a": f"characters:{a}", "b": f"characters:{b}", "type": "allies"}],
    }
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    rel = edits[f"feeling:characters:{a}->characters:{b}"]
    assert rel["label"] == "Ann → Bo"
    bond = edits[f"bond:characters:{a}|characters:{b}"]
    assert bond["label"] == "Ann & Bo"


def test_materialize_plot_new_and_advance(monkeypatch, tmp_path):
    from grimoire.store import plot, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    plot.set_movement(cid, "the-map", "The map", "open", "Elara got it.", "s10")
    parsed = {"plot_movements": [
        {"id": "the-map", "title": "The map", "status": "advanced", "beat": "It is a forgery."},
        {"id": "", "title": "The Duke's debt", "status": "open", "beat": "A creditor asked after Doran."},
        {"id": "", "title": "", "status": "open", "beat": "no id no title"},   # dropped
        {"id": "x", "title": "X", "status": "open", "beat": ""}]}               # empty beat dropped
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    adv = edits["plot:the-map"]
    assert adv["kind"] == "plot" and adv["field"] == "beat" and adv["authored"] is False
    assert adv["before"].startswith("open — Elara got it.")
    assert adv["after"] == "It is a forgery."
    assert adv["payload"] == {"id": "the-map", "title": "The map", "status": "advanced", "scene": sid}
    new = edits["plot:the-duke-s-debt"]  # slugified from the title
    assert new["before"] == "" and new["payload"]["title"] == "The Duke's debt"
    assert "plot:x" not in edits              # empty beat dropped
    assert "plot:untitled" not in edits       # no-id no-usable-title dropped (not "untitled")


def test_materialize_plot_new_title_colliding_existing_id_merges(monkeypatch, tmp_path):
    from grimoire.store import plot, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    plot.set_movement(cid, "the-map", "The map", "open", "Elara got it.", "s10")
    # New thread (no id) whose title slugifies to the existing "the-map": must resolve to
    # the existing thread and honestly show its `before` (not masquerade as new).
    parsed = {"plot_movements": [
        {"id": "", "title": "The Map!", "status": "advanced", "beat": "It is a forgery."}]}
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    row = edits["plot:the-map"]
    assert row["before"].startswith("open — Elara got it.")   # resolved to existing
    assert row["payload"]["title"] == "The map"               # keeps the stored title


def test_materialize_plot_dedupes_same_pid(monkeypatch, tmp_path):
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    parsed = {"plot_movements": [
        {"id": "", "title": "The map", "status": "open", "beat": "first"},
        {"id": "", "title": "The map", "status": "advanced", "beat": "second"}]}
    plot_edits = [e for e in absorb.materialize(cid, sid, parsed) if e["kind"] == "plot"]
    assert len(plot_edits) == 1  # one edit per thread per scene (no duplicate ids)


def test_materialize_tolerates_garbled_plot(monkeypatch, tmp_path):
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    (campaigns.campaign_root(cid) / "plot.json").write_text("{ not json", encoding="utf-8")
    parsed = {"plot_movements": [
        {"id": "the-map", "title": "The map", "status": "advanced", "beat": "moved"}]}
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}  # must not raise
    assert edits["plot:the-map"]["before"] == ""  # garbled store treated as empty


def test_materialize_new_character_creates_staged_edit(monkeypatch, tmp_path):
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    parsed = {"new_characters": [
        {"name": "Old Bram", "description": "[character(\"Old Bram\") { Occupation(\"innkeep\") }]",
         "history": "Bram kept the inn for forty years.", "personality": "gruff but kind",
          "mes_example": "<START>\n{{user}}: A room?\n{{char}}: Aye.",
          "evidence": "Bram rented the party a room and warned them about the pier.",
          "confidence": "established", "open_questions": "Who pays Bram for rumors?",
          "sd_prompt": "an old innkeeper, weathered face"}]}
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    e = edits["new_character:old-bram"]
    assert e["kind"] == "new_character" and e["target"] == {"kind": "characters", "id": ""}
    assert e["label"] == "New character — Old Bram" and e["field"] == "description"
    assert e["before"] == "" and e["authored"] is False
    # history is folded into the reviewed description, after the W++ block
    assert e["after"] == ("[character(\"Old Bram\") { Occupation(\"innkeep\") }]\n\n"
                          "Bram kept the inn for forty years.")
    assert e["payload"] == {"name": "Old Bram", "sd_prompt": "an old innkeeper, weathered face",
                            "personality": "gruff but kind",
                            "mes_example": "<START>\n{{user}}: A room?\n{{char}}: Aye.",
                            "evidence": "Bram rented the party a room and warned them about the pier.",
                            "confidence": "established",
                            "open_questions": "Who pays Bram for rumors?"}


def test_materialize_new_character_without_history_keeps_description_bare(monkeypatch, tmp_path):
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    parsed = {"new_characters": [{"name": "Old Bram", "description": "W++ block", "sd_prompt": ""}]}
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    assert edits["new_character:old-bram"]["after"] == "W++ block"


def test_materialize_new_character_without_progressive_metadata_defaults_thin(monkeypatch, tmp_path):
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    parsed = {"new_characters": [{"name": "Old Bram", "description": "W++ block", "sd_prompt": ""}]}
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    assert edits["new_character:old-bram"]["payload"]["evidence"] == ""
    assert edits["new_character:old-bram"]["payload"]["confidence"] == "thin"
    assert edits["new_character:old-bram"]["payload"]["open_questions"] == ""


def test_new_character_invalid_confidence_normalizes_to_thin(monkeypatch, tmp_path):
    from grimoire.store import characters, dossiers, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    parsed = absorb.parse_output(json.dumps({
        "new_characters": [{
            "name": "Old Bram", "description": "W++ block",
            "confidence": "certain", "evidence": "Bram opened the gate.",
        }],
    }))

    edit = absorb.materialize(cid, sid, parsed)[0]
    assert edit["payload"]["confidence"] == "thin"
    applied, failures = absorb.apply_edits(cid, [edit], sid)

    assert applied == ["new_character:old-bram"]
    assert failures == []
    croot = campaigns.campaign_root(cid)
    assert "Confidence: thin" in characters.read_card(croot, "old-bram", "default")["data"]["description"]
    assert "as a thin emergent character" in dossiers.read(croot, "old-bram")


def test_materialize_new_character_drops_existing_name_collision(monkeypatch, tmp_path):
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    _char(croot, "Seraphine")
    sid = scenes.create_scene(cid, "S")
    parsed = {"new_characters": [{"name": "seraphine", "description": "x", "sd_prompt": ""}]}
    assert absorb.materialize(cid, sid, parsed) == []


def test_materialize_new_character_drops_world_inherited_name_collision(monkeypatch, tmp_path):
    """A character that only exists in the world (not yet materialized into the
    campaign) must still suppress a duplicate new_character proposal -- dedup
    has to see the overlay's merged namespace, not just the campaign copy."""
    import shutil
    from grimoire.store import scenes
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    aid = _char(wroot, "Seraphine")
    cid = campaigns.create_campaign("Run", wid)
    # already thin from creation (Seraphine is world-inherited only); tolerant
    # rmtree keeps this test valid regardless of when the campaign was created
    d = campaigns.campaign_root(cid) / "characters" / aid
    if d.exists():
        shutil.rmtree(d)
    manifest = campaigns.read_manifest(cid)
    manifest.pop(f"characters/{aid}", None)
    campaigns.write_manifest(cid, manifest)
    sid = scenes.create_scene(cid, "S")
    parsed = {"new_characters": [{"name": "Seraphine", "description": "x", "sd_prompt": ""}]}
    assert absorb.materialize(cid, sid, parsed) == []


def test_materialize_new_character_drops_blank_name_or_description(monkeypatch, tmp_path):
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    parsed = {"new_characters": [
        {"name": "", "description": "x", "sd_prompt": ""},
        {"name": "Nobody", "description": "", "sd_prompt": ""}]}
    assert absorb.materialize(cid, sid, parsed) == []


def test_materialize_new_locations_and_lore(monkeypatch, tmp_path):
    from grimoire.store import entities, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "locations", "Old Dock")
    sid = scenes.create_scene(cid, "S")
    parsed = {
        "new_locations": [
            {"name": "Old Dock", "body": "dup", "keys": "", "sd_prompt": "", "current_setting": False},
            {"name": "The Crypt", "body": "A cold crypt.", "keys": "crypt", "sd_prompt": "a dark crypt",
             "current_setting": True},
        ],
        "new_lore": [{"name": "Salt Pact", "body": "An old pact.", "keys": "pact"}],
    }
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    assert "new_location:old-dock" not in edits          # dedup: name collides with existing entity
    loc = edits["new_location:the-crypt"]
    assert loc["kind"] == "new_location" and loc["target"] == {"kind": "locations", "id": ""}
    assert loc["field"] == "body" and loc["after"] == "A cold crypt." and loc["authored"] is False
    assert loc["payload"] == {"name": "The Crypt", "keys": "crypt", "sd_prompt": "a dark crypt",
                              "current_setting": True}
    lore = edits["new_lore:salt-pact"]
    assert lore["kind"] == "new_lore" and lore["payload"] == {"name": "Salt Pact", "keys": "pact"}


def test_apply_edits_writes_relationships(monkeypatch, tmp_path):
    from grimoire.store import relationships
    cid = _campaign(monkeypatch, tmp_path)
    applied, _ = absorb.apply_edits(cid, [
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


def test_apply_edits_writes_plot(monkeypatch, tmp_path):
    from grimoire.store import plot
    cid = _campaign(monkeypatch, tmp_path)
    applied, _ = absorb.apply_edits(cid, [
        {"id": "plot:the-map", "kind": "plot",
         "target": {"kind": "plot", "id": "the-map"}, "field": "beat",
         "after": "It is a forgery.",
         "payload": {"id": "the-map", "title": "The map", "status": "advanced", "scene": "s12"}}])
    assert applied == ["plot:the-map"]
    t = plot.get(cid, "the-map")
    assert t["status"] == "advanced" and t["last_scene"] == "s12"
    assert t["beats"][-1] == {"scene": "s12", "text": "It is a forgery."}


def test_apply_new_character_seeds_progressive_metadata_and_dossier(monkeypatch, tmp_path):
    from grimoire.store import characters, dossiers, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    edit = {
        "id": "new_character:old-bram", "kind": "new_character",
        "target": {"kind": "characters", "id": ""}, "label": "New character — Old Bram",
        "field": "description", "before": "",
        "after": "[character(\"Old Bram\") { Occupation(\"innkeep\") }]\n\nBram kept the inn.",
        "authored": False,
        "payload": {
            "name": "Old Bram",
            "personality": "gruff but kind",
            "mes_example": "<START>\n{{user}}: A room?\n{{char}}: Aye.",
            "sd_prompt": "old man, weathered face",
            "evidence": "Bram rented the party a room and warned them about the pier.",
            "confidence": "sketched",
            "open_questions": "Who pays Bram for rumors?",
        },
    }

    applied, failures = absorb.apply_edits(cid, [edit], sid)

    assert applied == ["new_character:old-bram"]
    assert failures == []
    croot = campaigns.campaign_root(cid)
    card = characters.read_card(croot, "old-bram", "default")
    desc = card["data"]["description"]
    assert "## Play Provenance" in desc
    assert "Evidence: Bram rented the party a room and warned them about the pier." in desc
    assert "Confidence: sketched" in desc
    assert "Open questions: Who pays Bram for rumors?" in desc
    dossier = dossiers.read(croot, "old-bram")
    assert "Old Bram was introduced through play as a sketched emergent character." in dossier
    assert "Bram rented the party a room and warned them about the pier." in dossier


def test_relationships_snapshot_tolerates_garbled(monkeypatch, tmp_path):
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    (campaigns.campaign_root(cid) / "relationships.json").write_text("{ not json", encoding="utf-8")
    assert absorb.relationships_snapshot(cid, sid) == ""  # must not raise


def _campaign_with_group(monkeypatch, tmp_path):
    from grimoire.store import entities, scenes
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Saltmarch")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "groups", "Salt Circle", "A quiet cabal.")
    return cid, sid, croot


def test_parse_output_group_state_keeps_key_presence():
    parsed = absorb.parse_output(json.dumps({
        "group_state_edits": [{"id": "groups/salt-circle", "goals": "New goal.", "secrets": ""}]}))
    row = parsed["group_state_edits"][0]
    assert row["id"] == "groups/salt-circle"
    assert row["goals"] == "New goal."
    assert row["secrets"] == ""          # explicit "" carried (clears)
    assert "resources" not in row        # omitted key absent (keep-on-omit)


def test_group_state_materialize_merges_and_applies(monkeypatch, tmp_path):
    from grimoire.store import groupstate
    cid, sid, croot = _campaign_with_group(monkeypatch, tmp_path)
    groupstate.write_state(croot, "salt-circle", "## Goals\nOld goal.\n\n## Secrets\nThe abbot.")
    parsed = absorb.parse_output(json.dumps({
        "group_state_edits": [{"id": "groups/salt-circle", "goals": "New goal."}]}))
    edits = absorb.materialize(cid, sid, parsed)
    gs = [e for e in edits if e["kind"] == "group_state"]
    assert len(gs) == 1
    assert gs[0]["id"] == "group_state:salt-circle"
    assert "New goal." in gs[0]["after"]
    assert "The abbot." in gs[0]["after"]          # omitted secrets preserved
    assert "Old goal." in gs[0]["before"]
    absorb.apply_edits(cid, gs)
    st = groupstate.read_state(croot, "salt-circle")
    assert st["goals"] == "New goal."
    assert st["secrets"] == "The abbot."


def test_group_state_edit_for_unknown_group_dropped(monkeypatch, tmp_path):
    cid, sid, croot = _campaign_with_group(monkeypatch, tmp_path)
    parsed = absorb.parse_output(json.dumps({
        "group_state_edits": [{"id": "groups/no-such", "goals": "x"}]}))
    assert [e for e in absorb.materialize(cid, sid, parsed) if e["kind"] == "group_state"] == []


def test_group_snapshot_lists_ids_and_state(monkeypatch, tmp_path):
    from grimoire.store import groupstate
    cid, sid, croot = _campaign_with_group(monkeypatch, tmp_path)
    groupstate.write_state(croot, "salt-circle", "## Goals\nExpand.")
    snap = absorb.group_snapshot(cid)
    assert "groups/salt-circle" in snap
    assert "Salt Circle" in snap
    assert "Goals: Expand." in snap


# ---- "sheet" edit kind (Task 8) ----


def test_apply_edits_sheet_failures_reported(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    edits, _ = audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": "characters:mara", "field": "hp",
                          "value": {"current": live["current"] - 2}, "note": ""}]})
    applied, failures = absorb.apply_edits(cid, edits, sid)
    assert applied == [edits[0]["id"]] and failures == []
    applied, failures = absorb.apply_edits(cid, edits, sid)   # replay
    assert applied == [] and failures[0]["kind"] == "conflict"
    assert failures[0]["id"] == edits[0]["id"]


def test_apply_edits_sheet_not_in_changes_json(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    edits, _ = audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": "characters:mara", "field": "hp",
                          "value": {"current": live["current"] - 1}, "note": ""}]})
    absorb.apply_edits(cid, edits, sid)
    assert all(not ref.startswith("sheet") for ref in changes.read(cid))


def test_apply_edits_sheet_needs_sid():
    edit = {"id": "sheet:characters:mara:hp", "kind": "sheet",
            "target": {"kind": "characters", "id": "mara"}, "field": "hp",
            "payload": {"field": "hp", "value": 1, "expect": 1, "note": ""}}
    applied, failures = absorb.apply_edits("no-such-cid", [edit], None)
    assert applied == [] and failures == [
        {"id": "sheet:characters:mara:hp", "kind": "error",
         "reason": "sheet edits need a scene id"}]


def test_apply_edits_sheet_malformed_edit_reports_error(scene_with_sheeted_cast):
    cid, sid = scene_with_sheeted_cast
    malformed = {"id": "sheet:characters:mara:hp", "kind": "sheet",
                 "target": {"kind": "characters", "id": "mara"}, "field": "hp",
                 "payload": {"field": 123, "value": 1, "expect": 1, "note": ""}}
    applied, failures = absorb.apply_edits(cid, [malformed], sid)
    assert applied == [] and failures[0]["kind"] == "error"
    assert failures[0]["id"] == "sheet:characters:mara:hp"


def test_apply_edits_mixed_batch_still_applies_non_sheet(scene_with_sheeted_cast):
    from grimoire.store import entities
    cid, sid = scene_with_sheeted_cast
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Salt Cathedral", body="Ruined.")
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    sheet_edits, _ = audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": "characters:mara", "field": "hp",
                          "value": {"current": live["current"] - 2}, "note": ""}]})
    lore_edit = {"id": "lore:salt-cathedral", "kind": "lore",
                 "target": {"kind": "lore", "id": "salt-cathedral"}, "field": "body",
                 "after": "Flooded."}
    applied, failures = absorb.apply_edits(cid, [*sheet_edits, lore_edit], sid)
    assert failures == []
    assert set(applied) == {sheet_edits[0]["id"], "lore:salt-cathedral"}
    assert entities.read_entity(croot, "lore", "salt-cathedral")["body"].strip() == "Flooded."
    assert sheets.read(cid, "characters", "mara")["fields"]["hp"]["current"] == live["current"] - 2


def test_apply_edits_sheet_missing_id_rejected_before_apply(scene_with_sheeted_cast):
    from grimoire.store import entities
    cid, sid = scene_with_sheeted_cast
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Salt Cathedral", body="Ruined.")
    live = sheets.read(cid, "characters", "mara")["fields"]["hp"]
    sheet_edits, _ = audit.materialize(cid, sid, {"warnings": [], "dropped": [],
        "sheet_deltas": [{"id": "characters:mara", "field": "hp",
                          "value": {"current": live["current"] - 2}, "note": ""}]})
    del sheet_edits[0]["id"]
    lore_edit = {"id": "lore:salt-cathedral", "kind": "lore",
                 "target": {"kind": "lore", "id": "salt-cathedral"}, "field": "body",
                 "after": "Flooded."}
    applied, failures = absorb.apply_edits(cid, [sheet_edits[0], lore_edit], sid)
    assert applied == ["lore:salt-cathedral"]
    assert failures == [{"id": "", "kind": "error", "reason": "sheet edit missing id"}]
    # sheet mutation never landed -- rejected before apply_delta ran
    assert sheets.read(cid, "characters", "mara")["fields"]["hp"]["current"] == live["current"]
    assert entities.read_entity(croot, "lore", "salt-cathedral")["body"].strip() == "Flooded."


def test_apply_edits_skips_non_dict_batch_items(scene_with_sheeted_cast):
    from grimoire.store import entities
    cid, sid = scene_with_sheeted_cast
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Salt Cathedral", body="Ruined.")
    lore_edit = {"id": "lore:salt-cathedral", "kind": "lore",
                 "target": {"kind": "lore", "id": "salt-cathedral"}, "field": "body",
                 "after": "Flooded."}
    applied, failures = absorb.apply_edits(cid, ["not-a-dict", lore_edit], sid)
    assert failures == []
    assert applied == ["lore:salt-cathedral"]
    assert entities.read_entity(croot, "lore", "salt-cathedral")["body"].strip() == "Flooded."
