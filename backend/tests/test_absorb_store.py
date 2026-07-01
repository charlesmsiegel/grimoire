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
