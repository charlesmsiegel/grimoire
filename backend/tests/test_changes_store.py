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
