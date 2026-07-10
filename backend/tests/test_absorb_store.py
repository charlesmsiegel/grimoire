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
        "character_state_edits": [], "lore_edits": [], "authored_edits": [],
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


def test_parse_output_new_entities():
    text = ('{"one_line": "", "summary": "", "keywords": [], "timeline_events": [],'
            ' "character_state_edits": [], "lore_edits": [], "authored_edits": [],'
            ' "new_characters": [{"name": "Old Bram", "description": "W++ block", "sd_prompt": "an old man"}],'
            ' "new_locations": [{"name": "The Crypt", "body": "cold", "keys": "crypt",'
            '   "sd_prompt": "a dark crypt", "current_setting": true}],'
            ' "new_lore": [{"name": "Salt Pact", "body": "an old pact", "keys": "pact"}]}')
    out = absorb.parse_output(text)
    assert out["new_characters"] == [{"name": "Old Bram", "description": "W++ block", "sd_prompt": "an old man"}]
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


def test_apply_edits_writes_plot(monkeypatch, tmp_path):
    from grimoire.store import plot
    cid = _campaign(monkeypatch, tmp_path)
    applied = absorb.apply_edits(cid, [
        {"id": "plot:the-map", "kind": "plot",
         "target": {"kind": "plot", "id": "the-map"}, "field": "beat",
         "after": "It is a forgery.",
         "payload": {"id": "the-map", "title": "The map", "status": "advanced", "scene": "s12"}}])
    assert applied == ["plot:the-map"]
    t = plot.get(cid, "the-map")
    assert t["status"] == "advanced" and t["last_scene"] == "s12"
    assert t["beats"][-1] == {"scene": "s12", "text": "It is a forgery."}


def test_relationships_snapshot_tolerates_garbled(monkeypatch, tmp_path):
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    (campaigns.campaign_root(cid) / "relationships.json").write_text("{ not json", encoding="utf-8")
    assert absorb.relationships_snapshot(cid, sid) == ""  # must not raise
