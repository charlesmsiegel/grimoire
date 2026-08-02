import json

import pytest

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
        "commitment_movements": [],
        "new_characters": [], "new_locations": [], "new_lore": [], "weather_edits": []}


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
    scenes.create_scene(cid, "S")
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
    from grimoire.store import appearances, entities, scenes
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


def test_apply_edits_new_character_does_not_narrate_into_a_messaged_scene(monkeypatch, tmp_path):
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "assistant", "*The tavern is loud.*")
    absorb.apply_edits(cid, [
        {"id": "new_character:old-bram", "kind": "new_character",
         "target": {"kind": "characters", "id": ""}, "field": "description",
         "after": "[character(\"Old Bram\") { Occupation(\"innkeep\") }]\n\nBram kept the inn.",
         "payload": {"name": "Old Bram"}}], sid)
    assert [m["content"] for m in scenes.read_scene(cid, sid)["messages"]] == \
        ["*The tavern is loud.*"]


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


def test_apply_edits_writes_dossier(monkeypatch, tmp_path):
    from grimoire.store import dossiers
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    applied, _ = absorb.apply_edits(cid, [
        {"id": f"dossier:{ch}", "kind": "dossier",
         "target": {"kind": "characters", "id": ch}, "field": "dossier",
         "before": "", "after": "Seraphine now walks with the party."}])
    assert applied == [f"dossier:{ch}"]
    assert dossiers.read(croot, ch) == "Seraphine now walks with the party."


def test_apply_edits_skips_a_stale_dossier(monkeypatch, tmp_path):
    """Staging the dossier instead of writing it at absorb time means the write
    order is now the SAVE order, which can invert the absorb order: two reviews
    open on the same NPC, the newer one saved first, and the older one would
    overwrite it with earlier-scene state. The staged `before` is the check."""
    from grimoire.store import dossiers
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    dossiers.write(croot, ch, "Seraphine rides with the party.")   # a newer review landed
    applied, _ = absorb.apply_edits(cid, [
        {"id": f"dossier:{ch}", "kind": "dossier",
         "target": {"kind": "characters", "id": ch}, "field": "dossier",
         "before": "Seraphine is wary.",                           # staged against the old text
         "after": "Seraphine is slightly less wary."}])
    assert applied == []
    assert dossiers.read(croot, ch) == "Seraphine rides with the party."


def test_a_character_vanishing_before_the_save_is_reported(monkeypatch, tmp_path):
    """The existence check sat outside the dossier failure handler, so a
    character deleted between staging and saving dropped an approved edit into
    the generic per-edit skip -- silently, with the save reading as a success."""
    from grimoire.store import dossiers
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    dossiers.write(croot, ch, "Seraphine is wary.")
    (croot / "characters" / ch / "character.md").unlink()      # deleted since staging
    applied, failures = absorb.apply_edits(cid, [
        {"id": f"dossier:{ch}", "kind": "dossier",
         "target": {"kind": "characters", "id": ch}, "field": "dossier",
         "before": "Seraphine is wary.", "after": "Seraphine is loyal."}])
    assert applied == []
    assert [(f["id"], f["kind"]) for f in failures] == [(f"dossier:{ch}", "error")]
    assert "no longer exists" in failures[0]["reason"]
    assert dossiers.read(croot, ch) == "Seraphine is wary."    # untouched


def test_a_dossier_read_failure_is_reported(monkeypatch, tmp_path):
    """The conflict check reads before it writes, and that read sits inside the
    generic per-edit `except`. A permissions or I/O error there would drop an
    approved edit with the save still reading as a success."""
    from grimoire.store import dossiers
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    real_read = dossiers.read

    def boom(*a, **k):
        raise OSError("permission denied")

    dossiers.read = boom
    try:
        applied, failures = absorb.apply_edits(cid, [
            {"id": f"dossier:{ch}", "kind": "dossier",
             "target": {"kind": "characters", "id": ch}, "field": "dossier",
             "before": "", "after": "Seraphine is loyal."}])
    finally:
        dossiers.read = real_read
    assert applied == []
    assert [(f["id"], f["kind"]) for f in failures] == [(f"dossier:{ch}", "error")]
    assert "permission denied" in failures[0]["reason"]


def test_a_stale_dossier_is_reported_as_a_conflict(monkeypatch, tmp_path):
    """Skipping the stale write is right; skipping it silently is not -- the
    reviewer approved that dossier and would otherwise be told the save
    succeeded while the text they wrote was dropped."""
    from grimoire.store import dossiers
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    dossiers.write(croot, ch, "Seraphine rides with the party.")
    applied, failures = absorb.apply_edits(cid, [
        {"id": f"dossier:{ch}", "kind": "dossier",
         "target": {"kind": "characters", "id": ch}, "field": "dossier",
         "before": "Seraphine is wary.", "after": "Seraphine is slightly less wary."}])
    assert applied == []
    assert failures == [{"id": f"dossier:{ch}", "kind": "conflict",
                         "reason": "this dossier changed since the scene was absorbed"}]


def test_apply_edits_writes_a_dossier_whose_before_still_matches(monkeypatch, tmp_path):
    from grimoire.store import dossiers
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    dossiers.write(croot, ch, "Seraphine is wary.")
    applied, _ = absorb.apply_edits(cid, [
        {"id": f"dossier:{ch}", "kind": "dossier",
         "target": {"kind": "characters", "id": ch}, "field": "dossier",
         "before": "Seraphine is wary.", "after": "Seraphine now rides with the party."}])
    assert applied == [f"dossier:{ch}"]
    assert dossiers.read(croot, ch) == "Seraphine now rides with the party."


def test_apply_edits_skips_empty_dossier(monkeypatch, tmp_path):
    """An empty `after` would blank a good dossier -- skip rather than erase."""
    from grimoire.store import dossiers
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    dossiers.write(croot, ch, "Seraphine is wary.")
    applied, _ = absorb.apply_edits(cid, [
        {"id": f"dossier:{ch}", "kind": "dossier",
         "target": {"kind": "characters", "id": ch}, "field": "dossier",
         "before": "Seraphine is wary.", "after": "   "}])
    assert applied == []
    assert dossiers.read(croot, ch) == "Seraphine is wary."


def test_apply_edits_rejects_a_forged_dossier_target(monkeypatch, tmp_path):
    """PUT /chronicle takes its edit list from the client, so a dossier row can
    name any target it likes -- it must not escape the campaign."""
    cid = _campaign(monkeypatch, tmp_path)
    applied, _ = absorb.apply_edits(cid, [
        {"id": "dossier:x", "kind": "dossier",
         "target": {"kind": "characters", "id": "../../../pwned"}, "field": "dossier",
         "before": "", "after": "owned"}])
    assert applied == []
    assert not (tmp_path / "pwned").exists()
    assert list(tmp_path.glob("**/pwned*")) == []


def test_apply_edits_dossier_needs_a_real_character(monkeypatch, tmp_path):
    """A forged row must not conjure a dossier-only phantom under characters/."""
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    applied, _ = absorb.apply_edits(cid, [
        {"id": "dossier:ghost", "kind": "dossier",
         "target": {"kind": "characters", "id": "ghost"}, "field": "dossier",
         "before": "", "after": "a ghost"}])
    assert applied == []
    assert not (croot / "characters" / "ghost").exists()


def test_apply_edits_dossier_rejects_a_non_character_target(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    _char(croot, "Seraphine")
    applied, _ = absorb.apply_edits(cid, [
        {"id": "dossier:seraphine", "kind": "dossier",
         "target": {"kind": "lore", "id": "seraphine"}, "field": "dossier",
         "before": "", "after": "wrong kind"}])
    assert applied == []


def test_apply_edits_records_dossier_in_changes(monkeypatch, tmp_path):
    from grimoire.store import dossiers, scenes
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    ch = _char(croot, "Seraphine")
    dossiers.write(croot, ch, "Wary.")     # the text the edit was staged against
    sid = scenes.create_scene(cid, "S")
    absorb.apply_edits(cid, [
        {"id": f"dossier:{ch}", "kind": "dossier",
         "target": {"kind": "characters", "id": ch}, "field": "dossier",
         "label": "Seraphine — campaign dossier",
         "before": "Wary.", "after": "Loyal."}], sid)
    entry = changes.read(cid)[f"characters/{ch}"]
    assert entry["fields"][0]["field"] == "dossier"
    assert entry["fields"][0]["before"] == "Wary." and entry["fields"][0]["after"] == "Loyal."


def test_dossier_edit_stages_before_and_after(monkeypatch, tmp_path):
    from grimoire.store import dossiers
    _campaign(monkeypatch, tmp_path)
    edit = dossiers.stage_edit("seraphine", "Seraphine", "Seraphine is wary.",
                               "Seraphine is loyal.")
    assert edit == {"id": "dossier:seraphine", "kind": "dossier",
                    "target": {"kind": "characters", "id": "seraphine"},
                    "label": "Seraphine — campaign dossier", "field": "dossier",
                    "before": "Seraphine is wary.", "after": "Seraphine is loyal.",
                    "authored": False}
    assert dossiers.stage_edit("seraphine", "Seraphine", "Seraphine is wary.",
                               "Seraphine is wary.") is None
    assert dossiers.stage_edit("seraphine", "Seraphine", "Seraphine is wary.", "  ") is None


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


def test_apply_new_character_survives_dossier_write_failure(monkeypatch, tmp_path):
    from grimoire.store import characters, dossiers, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    edit = {
        "id": "new_character:old-bram", "kind": "new_character",
        "target": {"kind": "characters", "id": ""}, "label": "New character — Old Bram",
        "field": "description", "before": "",
        "after": "Bram kept the inn.", "authored": False,
        "payload": {"name": "Old Bram", "personality": "", "mes_example": "",
                    "sd_prompt": "", "evidence": "", "confidence": "thin",
                    "open_questions": ""},
    }

    def boom(croot, cid_, text):
        raise OSError("disk full")

    monkeypatch.setattr(dossiers, "write", boom)
    applied, failures = absorb.apply_edits(cid, [edit], sid)

    # The dossier seed is best-effort: its failure must not strand a half-created
    # character (which a retry would duplicate via uniquify()).
    assert applied == ["new_character:old-bram"]
    assert failures == []
    croot = campaigns.campaign_root(cid)
    card = characters.read_card(croot, "old-bram", "default")
    assert "Bram kept the inn." in card["data"]["description"]
    assert dossiers.read(croot, "old-bram") == ""


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


# ---- commitments: promises, threats and foreshadowing (#115) ----------------

def test_parse_output_commitment_movements():
    text = ('{"commitment_movements": ['
            '{"id": "the-debt", "title": "Repay Winifred", "kind": "promise",'
            ' "status": "fulfilled", "due": "before the thaw", "beat": "She paid."},'
            '{"id": null, "title": "A knock at midnight", "kind": "WAGER",'
            ' "status": "advanced", "due": null, "beat": "Someone knocked."}]}')
    out = absorb.parse_output(text)
    assert out["commitment_movements"] == [
        {"id": "the-debt", "title": "Repay Winifred", "kind": "promise",
         "status": "fulfilled", "due": "before the thaw", "beat": "She paid."},
        # An unknown kind and a status from plot's vocabulary both normalize to
        # "" -- "the model said nothing" -- NOT to a default, so set_movement
        # keeps whatever is stored. A null id becomes "" the same way.
        #
        # `due` is the exception and drops OUT of the row: for it, "" is an
        # instruction to clear the deadline, and a null is the model saying
        # nothing rather than saying "none".
        {"id": "", "title": "A knock at midnight", "kind": "",
         "status": "", "beat": "Someone knocked."}]


def test_parse_output_carries_due_only_when_the_model_sent_the_key():
    """`due` takes the key-PRESENCE rule, not the blank-means-nothing rule the
    rest of the section takes. Absent = "this scene said nothing about the
    deadline, keep it"; an explicit "" = "the deadline is gone". Collapsing the
    two leaves a lifted deadline riding the ledger and every later prompt
    forever, with nothing the model can say to clear it."""
    out = absorb.parse_output(
        '{"commitment_movements": ['
        '{"id": "a", "beat": "b"},'
        '{"id": "b", "due": "", "beat": "b"},'
        '{"id": "c", "due": "by the third night", "beat": "b"},'
        '{"id": "d", "due": null, "beat": "b"},'
        '{"id": "e", "due": 7, "beat": "b"}]}')
    assert "due" not in out["commitment_movements"][0]
    assert out["commitment_movements"][1]["due"] == ""
    assert out["commitment_movements"][2]["due"] == "by the third night"
    # A non-string is omission, not "none". `_str` collapses null to "" for every
    # other field because the model uses the two interchangeably — but here that
    # would read as "lift the deadline" and quietly drop a stored one, which is
    # the very failure the three-valued contract was added to make expressible.
    assert "due" not in out["commitment_movements"][3]
    assert "due" not in out["commitment_movements"][4]


def test_a_scene_that_says_nothing_about_a_deadline_leaves_it_alone(monkeypatch, tmp_path):
    """The absorb path end to end: no `due` key survives as None through
    materialize's payload, and `set_movement` reads None as "keep it"."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-debt", "Repay Winifred", "promise", "open",
                             "before the thaw", "She swore it.", "s1")
    parsed = absorb.parse_output(
        '{"commitment_movements": [{"id": "the-debt", "beat": "She missed a payment."}]}')
    edits = absorb.materialize(cid, sid, parsed)
    assert edits[0]["payload"]["due"] is None
    assert edits[0]["label"] == "Repay Winifred — promise, open, due before the thaw"
    applied, failures = absorb.apply_edits(cid, edits, sid)
    assert applied == ["commitment:the-debt"] and failures == []
    assert commitments.get(cid, "the-debt")["due"] == "before the thaw"


def test_a_scene_that_lifts_a_deadline_clears_it(monkeypatch, tmp_path):
    """The other half: an explicit "" reaches the store as a cleared deadline,
    and the reviewer sees it go — the label drops the `due` clause the `before`
    head still carries, so the diff shows what the save will do."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-debt", "Repay Winifred", "promise", "open",
                             "before the thaw", "She swore it.", "s1")
    parsed = absorb.parse_output(
        '{"commitment_movements": [{"id": "the-debt", "due": "",'
        ' "beat": "Pay me whenever, she said."}]}')
    edits = absorb.materialize(cid, sid, parsed)
    assert edits[0]["payload"]["due"] == ""
    assert edits[0]["before"].startswith("promise, open, due before the thaw")
    assert edits[0]["label"] == "Repay Winifred — promise, open"
    applied, failures = absorb.apply_edits(cid, edits, sid)
    assert applied == ["commitment:the-debt"] and failures == []
    assert commitments.get(cid, "the-debt")["due"] == ""


def test_commitment_snapshot_renders_unresolved(monkeypatch, tmp_path):
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-debt", "Repay Winifred", "promise", "open",
                             "before the thaw", "She swore it.", "s1")
    commitments.set_movement(cid, "done", "Settled", "promise", "fulfilled", "", "Paid.", "s2")
    snap = absorb.commitment_snapshot(cid)
    assert "the-debt: Repay Winifred (promise, open), due before the thaw — She swore it." in snap
    assert "Settled" not in snap          # resolved commitments are not re-offered


def test_commitment_snapshot_tolerates_garbled(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "commitments.json").write_text("{ no", encoding="utf-8")
    assert absorb.commitment_snapshot(cid) == ""


def test_materialize_commitment_new_and_resolve(monkeypatch, tmp_path):
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-debt", "Repay Winifred", "promise", "open",
                             "before the thaw", "She swore it.", "s10")
    parsed = {"commitment_movements": [
        {"id": "the-debt", "title": "Repay Winifred", "kind": "promise",
         "status": "broken", "due": "", "beat": "The thaw came and went."},
        {"id": "", "title": "A knock at midnight", "kind": "foreshadowing",
         "status": "open", "due": "", "beat": "Three knocks, then nothing."},
        {"id": "", "title": "", "kind": "promise", "status": "open", "beat": "no id no title"},
        {"id": "x", "title": "X", "kind": "promise", "status": "open", "beat": ""}]}
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    resolved = edits["commitment:the-debt"]
    assert resolved["kind"] == "commitment" and resolved["field"] == "beat"
    assert resolved["target"] == {"kind": "commitments", "id": "the-debt"}
    # the stored KIND and deadline ride the head, so a reclassification or an
    # overwritten deadline is visible beside the label's resulting values, and the
    # trailing stamp makes the line a fingerprint rather than a description
    assert resolved["before"] == (
        "promise, open, due before the thaw — She swore it. [1 beat, last moved in s10]")
    assert resolved["after"] == "The thaw came and went."
    assert resolved["payload"] == {"id": "the-debt", "title": "Repay Winifred",
                                   "kind": "promise", "status": "broken", "due": "",
                                   "scene": sid}
    new = edits["commitment:a-knock-at-midnight"]      # slugified from the title
    assert new["before"] == "" and new["payload"]["kind"] == "foreshadowing"
    assert "commitment:x" not in edits                 # empty beat dropped
    assert "commitment:untitled" not in edits          # no id and no usable title dropped


def test_materialize_commitment_dedupes_same_id(monkeypatch, tmp_path):
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    parsed = {"commitment_movements": [
        {"id": "", "title": "The debt", "kind": "promise", "status": "open", "beat": "first"},
        {"id": "", "title": "The debt", "kind": "promise", "status": "broken", "beat": "second"}]}
    rows = [e for e in absorb.materialize(cid, sid, parsed) if e["kind"] == "commitment"]
    assert len(rows) == 1     # one edit per commitment per scene (no duplicate ids)


def test_apply_edits_writes_a_commitment(monkeypatch, tmp_path):
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    applied, failures = absorb.apply_edits(cid, [
        {"id": "commitment:the-debt", "kind": "commitment",
         "target": {"kind": "commitments", "id": "the-debt"}, "field": "beat",
         "after": "She swore it.",
         "payload": {"id": "the-debt", "title": "Repay Winifred", "kind": "promise",
                     "status": "open", "due": "before the thaw", "scene": sid}}], sid)
    assert applied == ["commitment:the-debt"] and failures == []
    got = commitments.get(cid, "the-debt")
    assert got == {"title": "Repay Winifred", "kind": "promise", "status": "open",
                   "due": "before the thaw", "last_scene": sid,
                   "beats": [{"scene": sid, "text": "She swore it."}]}


def test_apply_edits_commitment_payload_without_the_newer_keys_still_lands(monkeypatch, tmp_path):
    """A row that round-tripped through the reviewer's PUT body may carry only
    the keys plot's payload has. The beat must still land rather than the whole
    edit being swallowed."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    applied, _ = absorb.apply_edits(cid, [
        {"id": "commitment:the-debt", "kind": "commitment",
         "target": {"kind": "commitments", "id": "the-debt"}, "field": "beat",
         "after": "She swore it.",
         "payload": {"id": "the-debt", "title": "Repay Winifred", "scene": sid}}], sid)
    assert applied == ["commitment:the-debt"]
    got = commitments.get(cid, "the-debt")
    assert got["kind"] == "promise" and got["status"] == "open"   # the defaults
    assert got["beats"][0]["text"] == "She swore it."


def test_commitments_are_not_browsable_record_changes(monkeypatch, tmp_path):
    """Like plot movements, a commitment is not a record with a page to open, so
    it must not turn up in the Changes panel's per-record diffs."""
    from grimoire.store import changes, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    absorb.apply_edits(cid, [
        {"id": "commitment:the-debt", "kind": "commitment",
         "target": {"kind": "commitments", "id": "the-debt"}, "field": "beat",
         "label": "Repay Winifred — promise, open", "before": "", "after": "She swore it.",
         "payload": {"id": "the-debt", "title": "Repay Winifred", "kind": "promise",
                     "status": "open", "due": "", "scene": sid}}], sid)
    assert changes.read(cid) == {}


def test_omitting_kind_does_not_reclassify_an_existing_commitment(monkeypatch, tmp_path):
    """Appending a beat to a THREAT while saying nothing about its kind must not
    turn it into a promise. The blank has to survive parse and materialize, or
    set_movement's preserve-on-blank never gets the chance to fire."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-deadline", "Midnight deadline", "threat", "open",
                             "midnight", "She named the hour.", "s1")
    parsed = absorb.parse_output(
        '{"commitment_movements": [{"id": "the-deadline", "beat": "The hour came."}]}')
    edits = absorb.materialize(cid, sid, parsed)
    absorb.apply_edits(cid, edits, sid)
    got = commitments.get(cid, "the-deadline")
    assert got["kind"] == "threat"          # not silently reclassified
    assert got["status"] == "open"
    assert got["due"] == "midnight"
    assert [b["text"] for b in got["beats"]] == ["She named the hour.", "The hour came."]


def test_the_staged_label_shows_the_kind_and_status_the_save_will_produce(monkeypatch, tmp_path):
    """The payload carries blanks (meaning "unchanged"), but the reviewer's label
    has to name the values the record will actually read after the save."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-deadline", "Midnight deadline", "threat", "open",
                             "", "She named the hour.", "s1")
    parsed = {"commitment_movements": [
        {"id": "the-deadline", "title": "", "kind": "", "status": "", "due": "",
         "beat": "The hour came."},
        {"id": "", "title": "A new promise", "kind": "", "status": "", "due": "",
         "beat": "Sworn on the spot."}]}
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    existing = edits["commitment:the-deadline"]
    assert existing["label"] == "Midnight deadline — threat, open"   # the STORED values
    assert existing["payload"]["kind"] == "" and existing["payload"]["status"] == ""
    fresh = edits["commitment:a-new-promise"]
    assert fresh["label"] == "A new promise — promise, open"         # set_movement's defaults


@pytest.mark.parametrize("body", ["{ no", "[]"])
def test_an_unreadable_commitments_file_stages_nothing(monkeypatch, tmp_path, body):
    """Unparseable, and valid JSON of the wrong shape — `read` is a bare
    json.loads, so the second raises nothing and reaches `owed.get`. Neither may
    crash: that lands AFTER the extraction call and turns a paid-for absorb into
    a 500.

    Neither may stage a row either, which is the stronger half. Falling back to
    `{}` reads as "no such commitment" and stages every movement as NEW, and
    that row is a trap: its `before` claims nothing is stored when the truth is
    unknown, and approving it hits the same broken read inside `apply_edits`,
    whose per-edit `except` drops it without recording a failure — so the panel
    closes on a 200 and the approved commitment is simply gone."""
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    (campaigns.campaign_root(cid) / "commitments.json").write_text(body, encoding="utf-8")
    parsed = {"commitment_movements": [
        {"id": "the-debt", "title": "The debt", "kind": "promise", "status": "open",
         "beat": "moved"}],
        "plot_movements": [{"id": "t", "title": "T", "status": "open", "beat": "b"}]}
    edits = absorb.materialize(cid, sid, parsed)     # must not raise
    assert [e["id"] for e in edits] == ["plot:t"]    # the section, and only it


def test_a_commitment_deadline_is_visible_before_it_is_approved(monkeypatch, tmp_path):
    """`due` is applied on save and then steers the ledger and every later scene
    prompt. A model that invents or overwrites one must not be able to do it in a
    row whose only visible text is the beat."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-deadline", "Midnight deadline", "threat", "open",
                             "midnight", "She named the hour.", "s1")
    parsed = {"commitment_movements": [
        # an overwritten deadline on an existing commitment...
        {"id": "the-deadline", "title": "", "kind": "", "status": "",
         "due": "the following dawn", "beat": "She moved the hour."},
        # ...and an invented one on a brand-new commitment
        {"id": "", "title": "A new promise", "kind": "promise", "status": "open",
         "due": "before the thaw", "beat": "Sworn on the spot."}]}
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}
    moved = edits["commitment:the-deadline"]
    assert moved["label"] == "Midnight deadline — threat, open, due the following dawn"
    assert "due midnight" in moved["before"]        # what it was, beside what it becomes
    assert edits["commitment:a-new-promise"]["label"] == \
        "A new promise — promise, open, due before the thaw"


@pytest.mark.parametrize("record", [
    {"status": [], "due": "", "beats": []},                  # list-valued status
    {"status": "open", "due": ["x"], "beats": []},           # list-valued due
    {"status": "open", "due": "", "beats": [{"text": []}]},  # list-valued beat text
    {"status": "open", "due": "", "beats": "nope"},          # beats not a list
    {"title": 7, "kind": None, "status": "open"},            # title/kind wrong types
])
def test_materialize_tolerates_malformed_fields_inside_a_commitment(monkeypatch, tmp_path, record):
    """The top-level shape check does not reach the fields INSIDE a record, and
    a list-valued `status` concatenated into a label (or a list-valued `due`
    handed to `.strip()`) raises from inside materialize — after the extraction
    call, so a paid-for absorb becomes a 500."""
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    (campaigns.campaign_root(cid) / "commitments.json").write_text(
        json.dumps({"x": record}), encoding="utf-8")
    parsed = {"commitment_movements": [
        {"id": "x", "title": "X", "kind": "", "status": "", "due": "", "beat": "moved"}]}
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, parsed)}   # must not raise
    assert edits["commitment:x"]["after"] == "moved"


def test_a_commitment_resolved_by_a_newer_review_is_not_reopened(monkeypatch, tmp_path):
    """Two reviews open on one commitment. The newer is saved first and marks it
    fulfilled; this one still carries the `open` it saw at materialize time. The
    campaign lock serializes the two writes but cannot tell the second is stale
    — only the staged `before` dates the proposal."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-debt", "Repay Winifred", "promise", "open",
                             "", "She swore it.", "s1")
    stale = absorb.materialize(cid, sid, {"commitment_movements": [
        {"id": "the-debt", "title": "", "kind": "", "status": "open", "due": "",
         "beat": "Still owed."}]})

    # the other review lands first
    commitments.set_movement(cid, "the-debt", "", "", "fulfilled", "", "Repaid in full.", "s2")

    applied, failures = absorb.apply_edits(cid, stale, sid)
    assert applied == []
    assert failures == [{"id": "commitment:the-debt", "kind": "conflict",
                         "reason": "this commitment changed since the scene was absorbed"}]
    assert commitments.get(cid, "the-debt")["status"] == "fulfilled"   # not reopened


def test_an_intervening_movement_with_the_same_beat_text_is_still_a_conflict(
        monkeypatch, tmp_path):
    """The case the rendering alone cannot see. Kind, status, deadline and the
    latest beat TEXT are all identical after the newer movement — two absorbs
    really can produce the same short sentence — so a `before` built from those
    four reads as unmoved. Applying the stale row appends its older-scene beat
    AFTER the newer one and rewinds `last_scene`, which is what #103's aging
    reads. The beat count and last scene in the line are what close it."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-debt", "Repay Winifred", "promise", "open",
                             "", "She missed the payment.", "s1")
    stale = absorb.materialize(cid, sid, {"commitment_movements": [
        {"id": "the-debt", "title": "", "kind": "", "status": "",
         "beat": "She missed the payment."}]})

    # the other review lands first, with a beat that happens to read the same
    commitments.set_movement(cid, "the-debt", "", "", "", None,
                             "She missed the payment.", "s2")

    applied, failures = absorb.apply_edits(cid, stale, sid)
    assert applied == []
    assert failures == [{"id": "commitment:the-debt", "kind": "conflict",
                         "reason": "this commitment changed since the scene was absorbed"}]
    got = commitments.get(cid, "the-debt")
    assert got["last_scene"] == "s2"          # not rewound to s1
    assert len(got["beats"]) == 2             # the stale beat was not filed after the newer one


def test_a_new_commitment_does_not_reopen_a_resolved_one_by_title(monkeypatch, tmp_path):
    """A title-derived id colliding with a RESOLVED commitment gets a fresh id.
    `commitment_snapshot` offers only unresolved records, so the model was never
    shown the old one and cannot have meant it; treating the collision as a
    reference reopens a fulfilled promise and files the new beat into the closed
    record's history."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "fulfilled",
                             "", "Repaid in full.", "s1")
    edits = absorb.materialize(cid, sid, {"commitment_movements": [
        {"id": "", "title": "The debt", "kind": "promise", "status": "open",
         "beat": "She borrowed again."}]})
    assert [e["id"] for e in edits] == ["commitment:the-debt-2"]
    assert edits[0]["before"] == ""            # a new commitment, not a reopened one
    absorb.apply_edits(cid, edits, sid)
    old = commitments.get(cid, "the-debt")
    assert old["status"] == "fulfilled" and len(old["beats"]) == 1   # untouched
    assert commitments.get(cid, "the-debt-2")["status"] == "open"


def test_a_new_commitment_still_lands_on_an_open_one_of_the_same_title(monkeypatch, tmp_path):
    """The other side of the rule: an OPEN commitment under that title is in the
    snapshot, so the model naming it without an id is moving it, not opening a
    second one."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "open",
                             "", "Sworn.", "s1")
    edits = absorb.materialize(cid, sid, {"commitment_movements": [
        {"id": "", "title": "The debt", "kind": "", "status": "", "beat": "Still owed."}]})
    assert [e["id"] for e in edits] == ["commitment:the-debt"]
    assert edits[0]["before"].startswith("promise, open")


def test_two_titles_that_slug_alike_do_not_merge(monkeypatch, tmp_path):
    """`slugify` keeps only `[a-z0-9]`, so a title with no ASCII letters at all
    becomes the literal `untitled` — every one of them. Reusing an open record
    on the slug alone would file the second commitment's beat into the first and
    keep the first's title, leaving the new one with no record of its own."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "untitled", "Промолчать", "promise", "open",
                             "", "Sworn.", "s1")
    edits = absorb.materialize(cid, sid, {"commitment_movements": [
        {"id": "", "title": "Долг", "kind": "promise", "status": "open",
         "beat": "A different promise entirely."}]})
    assert [e["id"] for e in edits] == ["commitment:untitled-2"]
    assert edits[0]["before"] == ""                     # its own record, not a beat on hers
    assert edits[0]["payload"]["title"] == "Долг"       # and its own title


def test_two_titles_that_slug_alike_in_one_absorb_do_not_merge(monkeypatch, tmp_path):
    """The same collision one scope in: neither is in the store yet, so slug
    alone hands both the same id and the one-edit-per-commitment dedup drops the
    second outright — the model opened two commitments and one silently vanishes
    before the reviewer ever sees it."""
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    edits = absorb.materialize(cid, sid, {"commitment_movements": [
        {"id": "", "title": "Долг", "kind": "promise", "status": "open", "beat": "One."},
        {"id": "", "title": "Промолчать", "kind": "threat", "status": "open", "beat": "Two."}]})
    assert [e["id"] for e in edits] == ["commitment:untitled", "commitment:untitled-2"]
    assert [e["payload"]["title"] for e in edits] == ["Долг", "Промолчать"]


def test_one_commitment_named_twice_in_a_batch_is_still_one_edit(monkeypatch, tmp_path):
    """And the dedup this must not break: the SAME title twice is one commitment
    moving twice in one scene, which stays a single staged row."""
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    edits = absorb.materialize(cid, sid, {"commitment_movements": [
        {"id": "", "title": "The debt", "kind": "promise", "status": "open", "beat": "One."},
        {"id": "", "title": "the debt ", "kind": "", "status": "", "beat": "Two."}]})
    assert [e["id"] for e in edits] == ["commitment:the-debt"]


def test_the_same_title_still_lands_on_its_open_record(monkeypatch, tmp_path):
    """Case and surrounding space do not make it a different commitment."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "open",
                             "", "Sworn.", "s1")
    edits = absorb.materialize(cid, sid, {"commitment_movements": [
        {"id": "", "title": "  the DEBT ", "kind": "", "status": "", "beat": "Still owed."}]})
    assert [e["id"] for e in edits] == ["commitment:the-debt"]
    assert edits[0]["before"].startswith("promise, open")


def test_an_id_the_model_gave_is_never_redirected(monkeypatch, tmp_path):
    """A movement carrying an id is a reference and keeps pointing where it says,
    resolved or not — silently redirecting it would be the opposite mistake."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "fulfilled",
                             "", "Repaid.", "s1")
    edits = absorb.materialize(cid, sid, {"commitment_movements": [
        {"id": "the-debt", "title": "", "kind": "", "status": "broken",
         "beat": "The payment bounced."}]})
    assert [e["id"] for e in edits] == ["commitment:the-debt"]


def test_applying_over_an_unreadable_record_keeps_the_approved_title(monkeypatch, tmp_path):
    """The apply branch blanks the payload title when a commitment already
    exists, so an absorb cannot rename one. A truthy non-dict record is not a
    commitment that exists: `materialize` skips it and stages the model's title
    as new, `set_movement` replaces the record wholesale, and blanking on mere
    truthiness stored the row the reviewer approved as "The debt" under its id."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    (campaigns.campaign_root(cid) / "commitments.json").write_text(
        '{"the-debt": [1]}', encoding="utf-8")
    edits = absorb.materialize(cid, sid, {"commitment_movements": [
        {"id": "the-debt", "title": "The debt", "kind": "promise", "status": "open",
         "beat": "Sworn."}]})
    assert edits[0]["label"].startswith("The debt")     # what the reviewer approves
    applied, failures = absorb.apply_edits(cid, edits, sid)
    assert applied == ["commitment:the-debt"] and failures == []
    assert commitments.get(cid, "the-debt")["title"] == "The debt"   # ...is what is stored


def test_a_store_that_breaks_between_staging_and_saving_is_reported(monkeypatch, tmp_path):
    """`materialize` refuses to stage against an unreadable store, but the file
    can also break AFTER a good review is staged — a hand edit, a sync. Both
    conflict passes call that unjudgeable and the write raises, and the shared
    per-edit `except` would drop the row *after* the chronicle is written: a 200
    with no failure, and the panel closing on a movement that never landed.

    The commitment branch carried a bespoke handler for this until #271 gave
    every kind an error contract; the reason text is the generic one now, and
    what this pins is the contract rather than the wording."""
    from grimoire.store import scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    edits = absorb.materialize(cid, sid, {"commitment_movements": [
        {"id": "", "title": "The debt", "kind": "promise", "status": "open",
         "beat": "Sworn."}]})
    (campaigns.campaign_root(cid) / "commitments.json").write_text("{ no", encoding="utf-8")
    applied, failures = absorb.apply_edits(cid, edits, sid)
    assert applied == []
    assert [f["id"] for f in failures] == ["commitment:the-debt"]
    assert failures[0]["kind"] == "error"
    assert "could not apply this change" in failures[0]["reason"]


def test_an_absorb_still_cannot_rename_a_readable_commitment(monkeypatch, tmp_path):
    """The rule the fix above must not undo: a stored title stands."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-debt", "Repay Winifred", "promise", "open",
                             "", "She swore it.", "s1")
    edits = absorb.materialize(cid, sid, {"commitment_movements": [
        {"id": "the-debt", "title": "Something else", "kind": "", "status": "",
         "beat": "Still owed."}]})
    absorb.apply_edits(cid, edits, sid)
    assert commitments.get(cid, "the-debt")["title"] == "Repay Winifred"


def test_an_unchanged_commitment_still_applies(monkeypatch, tmp_path):
    """The staleness check must not turn every ordinary save into a conflict —
    materialize and apply have to spell the head identically."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-debt", "Repay Winifred", "promise", "open",
                             "before the thaw", "She swore it.", "s1")
    edits = absorb.materialize(cid, sid, {"commitment_movements": [
        {"id": "the-debt", "title": "", "kind": "", "status": "broken",
         "beat": "The thaw came and went."}]})
    applied, failures = absorb.apply_edits(cid, edits, sid)
    assert applied == ["commitment:the-debt"] and failures == []
    got = commitments.get(cid, "the-debt")
    assert got["status"] == "broken" and got["due"] == "before the thaw"


def test_a_reclassification_is_visible_before_it_is_approved(monkeypatch, tmp_path):
    """Approving the row writes the new kind. The label names the resulting kind;
    without the stored one in `before` there is nothing to read it against and a
    threat-to-promise reclassification looks like an ordinary beat."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-deadline", "Midnight deadline", "threat", "open",
                             "", "She named the hour.", "s1")
    edits = {e["id"]: e for e in absorb.materialize(cid, sid, {"commitment_movements": [
        {"id": "the-deadline", "title": "", "kind": "promise", "status": "",
         "beat": "She softened it."}]})}
    row = edits["commitment:the-deadline"]
    assert row["before"].startswith("threat, open")             # what it is
    assert row["label"] == "Midnight deadline — promise, open"  # what it becomes


# Derived from the contract rather than restated, the way evals/graders.py
# derives it: a section added later is covered the day it is added, which is
# exactly how the first round of this fix ended up covering one section and
# leaving seven behind.
_LIST_SECTIONS = [k for k, v in absorb.parse_output("{}").items() if isinstance(v, list)]


@pytest.mark.parametrize("value", ["null", "1", "true", '"a string"', "{}"])
@pytest.mark.parametrize("section", _LIST_SECTIONS)
def test_parse_output_treats_a_non_list_section_as_empty(section, value):
    """`"commitment_movements": null` is valid JSON a model really returns, and
    `.get(key, [])` hands back the null. Nothing catches parse errors between the
    extraction call and the reviewer, so iterating it is a 500 on a usable reply.

    Every non-list shape, not just null: `or []` was the first fix and a truthy
    scalar raised straight through it. A bare string is the nastiest of them —
    it iterates without raising, so `keywords` would come back as a list of
    single characters rather than as nothing."""
    out = absorb.parse_output('{"%s": %s}' % (section, value))
    assert out[section] == []

# ---- the failure contract for the non-sheet kinds (#271) ----
def test_apply_edits_reports_a_failed_non_sheet_write(scene_with_sheeted_cast, monkeypatch):
    """Before #271 only sheet and dossier edits had an error contract; every
    other kind was swallowed by a bare `except: continue`, so a save whose
    approved lore edit hit a full disk still returned 200 saying nothing."""
    from grimoire.store import entities, overlay
    cid, sid = scene_with_sheeted_cast
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Salt Cathedral", body="Ruined.")
    monkeypatch.setattr(overlay, "update_entity",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no space left on device")))
    applied, failures = absorb.apply_edits(cid, [
        {"id": "lore:salt-cathedral", "kind": "lore", "field": "body",
         "target": {"kind": "lore", "id": "salt-cathedral"}, "after": "Flooded."}], sid)
    assert applied == []
    assert [(f["id"], f["kind"]) for f in failures] == [("lore:salt-cathedral", "error")]
    assert "no space left" in failures[0]["reason"]
    assert entities.read_entity(croot, "lore", "salt-cathedral")["body"].strip() == "Ruined."


def test_apply_edits_reports_an_unknown_kind(scene_with_sheeted_cast):
    """An approved row this build cannot apply is a change the reviewer will
    never see land -- a mismatched frontend, or a kind dropped from the apply
    branch. Silence there is the same lost edit by a different route."""
    cid, sid = scene_with_sheeted_cast
    applied, failures = absorb.apply_edits(cid, [
        {"id": "invented:x", "kind": "invented",
         "target": {"kind": "invented", "id": "x"}, "after": "..."}], sid)
    assert applied == []
    assert [(f["id"], f["kind"]) for f in failures] == [("invented:x", "error")]


def test_apply_edits_reports_a_failed_changes_log(scene_with_sheeted_cast, monkeypatch):
    """The edits landed; only the Changes panel's delta did not. Raising would
    500 a commit that already succeeded, and silence would claim a write-back
    history that is not there -- so it is reported alongside the applied ids."""
    from grimoire.store import changes, entities
    cid, sid = scene_with_sheeted_cast
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Salt Cathedral", body="Ruined.")
    monkeypatch.setattr(changes, "record",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no space left on device")))
    applied, failures = absorb.apply_edits(cid, [
        {"id": "lore:salt-cathedral", "kind": "lore", "field": "body",
         "target": {"kind": "lore", "id": "salt-cathedral"}, "after": "Flooded."}], sid)
    assert applied == ["lore:salt-cathedral"]
    assert [(f["id"], f["kind"]) for f in failures] == [("changes", "error")]
    assert entities.read_entity(croot, "lore", "salt-cathedral")["body"].strip() == "Flooded."


def test_a_plot_beat_is_stamped_with_the_scene_being_saved(monkeypatch, tmp_path):
    """`materialize` stages `payload.scene` as the scene being absorbed, so the
    two normally agree. They part after a RENAME between a crashed commit and its
    retry: the ledger follows the rename so the retry is accepted, but the body
    cannot change -- the fingerprint refuses any retry whose body differs -- so
    the payload still names the id the scene had before. Writing that would stamp
    the beat and `last_scene` with a scene that no longer exists, and
    `plot.repoint_scenes` has already run, so nothing would come back for it."""
    from grimoire.store import plot
    cid = _campaign(monkeypatch, tmp_path)
    edit = {"id": "plot:the-map", "kind": "plot", "field": "beat",
            "target": {"kind": "plot", "id": "the-map"},
            "before": "", "after": "It is a forgery.",
            "payload": {"id": "the-map", "title": "The map", "status": "advanced",
                        "scene": "001--the-crypt"}}   # the id the scene had at absorb

    applied, failures = absorb.apply_edits(cid, [edit], sid="001--the-lower-crypt")

    assert applied == ["plot:the-map"] and failures == []
    thread = plot.read(cid)["the-map"]
    assert thread["last_scene"] == "001--the-lower-crypt"
    assert [b["scene"] for b in thread["beats"]] == ["001--the-lower-crypt"]


def test_a_plot_beat_falls_back_to_the_payload_when_there_is_no_scene(monkeypatch, tmp_path):
    """`sid` is optional on `apply_edits`, and a caller that passes none still
    gets the staged attribution rather than a blank one."""
    from grimoire.store import plot
    cid = _campaign(monkeypatch, tmp_path)
    absorb.apply_edits(cid, [{"id": "plot:the-map", "kind": "plot", "field": "beat",
                              "target": {"kind": "plot", "id": "the-map"},
                              "before": "", "after": "It is a forgery.",
                              "payload": {"id": "the-map", "title": "The map",
                                          "status": "advanced", "scene": "001--the-crypt"}}])
    assert plot.read(cid)["the-map"]["last_scene"] == "001--the-crypt"

def test_a_mixed_case_resolved_record_does_not_capture_a_new_commitment(monkeypatch, tmp_path):
    """The allocator is a SECOND reader of `status`, so folding it in
    `open_commitments` alone was half a fix: a hand-edited `"Fulfilled"` is
    hidden from the snapshot by that one, and this one would still have read it
    as unresolved — landing the model's new commitment on the record it was
    never shown, and reopening it."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    (campaigns.campaign_root(cid) / "commitments.json").write_text(
        '{"the-debt": {"title": "The debt", "kind": "promise", "status": "Fulfilled",'
        ' "due": "", "beats": [], "last_scene": "s1"}}', encoding="utf-8")

    edits = absorb.materialize(cid, sid, {"commitment_movements": [
        {"id": "", "title": "The debt", "kind": "promise", "status": "open",
         "beat": "Sworn again, by someone else."}]})
    assert [e["id"] for e in edits] == ["commitment:the-debt-2"]   # a fresh record

    absorb.apply_edits(cid, edits, sid)
    assert commitments.get(cid, "the-debt")["status"] == "Fulfilled"   # untouched
    assert commitments.get(cid, "the-debt-2")["status"] == "open"


def test_a_commitment_edit_writes_where_its_target_says(monkeypatch, tmp_path):
    """An edit reaches `apply_edits` from a client-supplied PUT body typed as an
    unrestricted dict, and everything that JUDGED the row read `target`:
    `conflicts` surveyed it, `target_key` keyed the journal by it, and the
    reviewer's basis describes it. A payload naming a different record would be
    written with none of that having looked at it."""
    from grimoire.store import commitments, scenes
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "open", "",
                             "Sworn.", "s1")
    commitments.set_movement(cid, "the-oath", "The oath", "promise", "open", "",
                             "Also sworn.", "s1")

    # The staged basis has to MATCH the target, or the conflict pass refuses the
    # row before this branch is reached — which is the other half of the point:
    # the survey reads `target`, so a payload pointing elsewhere is written with
    # nothing having judged it.
    from grimoire.store.absorb import conflicts as absorb_conflicts
    basis = absorb_conflicts.commitment_line(commitments.get(cid, "the-debt"))

    applied, failures = absorb.apply_edits(cid, [{
        "id": "commitment:the-debt", "kind": "commitment", "field": "beat",
        "target": {"kind": "commitments", "id": "the-debt"},
        "before": basis, "after": "A forged beat.",
        "payload": {"id": "the-oath", "title": "", "kind": "", "status": "broken",
                    "due": None, "scene": sid}}], sid)

    # The row was judged against `the-debt`, so `the-debt` is what it touches.
    assert (applied, failures) == (["commitment:the-debt"], [])
    assert [b["text"] for b in commitments.get(cid, "the-debt")["beats"]] == \
        ["Sworn.", "A forged beat."]
    assert [b["text"] for b in commitments.get(cid, "the-oath")["beats"]] == ["Also sworn."]
    assert commitments.get(cid, "the-oath")["status"] == "open"      # not broken
