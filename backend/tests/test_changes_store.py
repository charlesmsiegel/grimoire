from grimoire.store import changes


def test_line_diff_insert_only():
    d = changes.line_diff("a", "a\nb")
    assert d == [{"op": "equal", "text": "a"}, {"op": "insert", "text": "b"}]


def test_line_diff_delete_only():
    d = changes.line_diff("a\nb", "a")
    assert d == [{"op": "equal", "text": "a"}, {"op": "delete", "text": "b"}]


def test_line_diff_replace_emits_delete_then_insert():
    d = changes.line_diff("a\nold", "a\nnew")
    assert d == [{"op": "equal", "text": "a"},
                 {"op": "delete", "text": "old"},
                 {"op": "insert", "text": "new"}]


def test_line_diff_identical_all_equal():
    assert changes.line_diff("a\nb", "a\nb") == [
        {"op": "equal", "text": "a"}, {"op": "equal", "text": "b"}]


def test_line_diff_empty_sides():
    assert changes.line_diff("", "") == []
    assert changes.line_diff("", "x") == [{"op": "insert", "text": "x"}]
    assert changes.line_diff("x", "") == [{"op": "delete", "text": "x"}]


from grimoire.store import worlds, campaigns


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def test_record_and_read_roundtrip(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    fields = [{"field": "body", "label": "Harbor — locations", "before": "old", "after": "new"}]
    changes.record(cid, "s1", {"locations/harbor": fields})
    assert changes.read(cid) == {"locations/harbor": {"scene": "s1", "fields": fields}}


def test_record_replaces_prior_entry(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    changes.record(cid, "s1", {"lore/pact": [{"field": "body", "label": "L", "before": "a", "after": "b"}]})
    changes.record(cid, "s2", {"lore/pact": [{"field": "body", "label": "L", "before": "b", "after": "c"}]})
    entry = changes.read(cid)["lore/pact"]
    assert entry["scene"] == "s2" and entry["fields"][0]["before"] == "b"


def test_record_empty_is_noop(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    changes.record(cid, "s1", {})
    assert changes.read(cid) == {}


def test_read_tolerates_garbage(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "changes.json").write_text("{not json", encoding="utf-8")
    assert changes.read(cid) == {}


from grimoire.store import absorb, entities, scenes


def _lore_edit(before, after):
    return {"id": "lore:pact", "kind": "lore", "target": {"kind": "lore", "id": "pact"},
            "label": "The Pact — lore", "field": "body", "before": before, "after": after,
            "authored": False}


def test_apply_records_lore_edit(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Pact", body="old body")
    absorb.apply_edits(cid, [_lore_edit("old body", "old body\n\nnew line")], "s1")
    entry = changes.read(cid)["lore/pact"]
    assert entry["scene"] == "s1"
    assert entry["fields"] == [{"field": "body", "label": "The Pact — lore",
                                "before": "old body", "after": "old body\n\nnew line"}]


def test_apply_accumulates_multiple_fields_per_record(monkeypatch, tmp_path):
    from grimoire.store import characters, playstate, appearances
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    card = characters.blank_card("Mara")
    card["data"]["personality"] = "aloof"
    ch = characters.create_character(croot, "Mara", "main", card)[0]
    playstate.write_state(croot, ch, "calm")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    cs = {"id": f"character_state:{ch}", "kind": "character_state",
          "target": {"kind": "characters", "id": ch}, "label": "Mara — current state",
          "field": "current_state", "before": "calm", "after": "shaken", "authored": False}
    au = {"id": f"authored:{ch}:personality", "kind": "authored",
          "target": {"kind": "characters", "id": ch}, "label": "Mara — personality (card edit)",
          "field": "personality", "before": "aloof", "after": "warmer", "authored": True}
    absorb.apply_edits(cid, [cs, au], sid)
    fields = changes.read(cid)[f"characters/{ch}"]["fields"]
    assert {f["field"] for f in fields} == {"current_state", "personality"}


def test_apply_skips_non_browsable_kinds(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    plot_edit = {"id": "plot:the-map", "kind": "plot", "target": {"kind": "plot", "id": "the-map"},
                 "label": "The map — advanced", "field": "beat", "before": "", "after": "It moved.",
                 "authored": False,
                 "payload": {"id": "the-map", "title": "The map", "status": "advanced", "scene": "s1"}}
    absorb.apply_edits(cid, [plot_edit], "s1")
    assert changes.read(cid) == {}


def test_apply_without_sid_records_nothing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Pact", body="old body")
    absorb.apply_edits(cid, [_lore_edit("old body", "old body\n\nx")])
    assert changes.read(cid) == {}
