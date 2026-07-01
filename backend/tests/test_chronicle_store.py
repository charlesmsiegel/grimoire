import pytest

from grimoire.store import campaigns, chronicle, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    return campaigns.create_campaign("Run", wid)


def test_read_missing_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert chronicle.read_chronicle(cid) == {}


def test_absorb_stores_and_stamps(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    rec = chronicle.absorb(cid, {"id": "2026-01-01-a", "one_line": "They met.",
                                 "summary": "A met B.", "keywords": ["salt"]})
    assert rec["one_line"] == "They met."
    assert rec["absorbed"]  # timestamp added
    assert chronicle.read_chronicle(cid)["2026-01-01-a"]["summary"] == "A met B."


def test_absorb_replaces_by_id(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    chronicle.absorb(cid, {"id": "s1", "one_line": "v1", "summary": "", "keywords": []})
    chronicle.absorb(cid, {"id": "s1", "one_line": "v2", "summary": "", "keywords": []})
    data = chronicle.read_chronicle(cid)
    assert len(data) == 1 and data["s1"]["one_line"] == "v2"


def test_recent_orders_by_id_and_bounds(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    chronicle.absorb(cid, {"id": "2026-01-02-b", "one_line": "second", "summary": "", "keywords": []})
    chronicle.absorb(cid, {"id": "2026-01-01-a", "one_line": "first", "summary": "", "keywords": []})
    assert [r["one_line"] for r in chronicle.recent(cid, 5)] == ["first", "second"]
    assert [r["one_line"] for r in chronicle.recent(cid, 1)] == ["second"]
    assert chronicle.recent(cid, 0) == []


def test_append_timeline_writes_lines(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    chronicle.append_timeline(cid, [{"date": "2026-01-01", "text": "The gate opened."}])
    chronicle.append_timeline(cid, [{"date": "2026-01-02", "text": "It closed."}])
    body = (campaigns.campaign_root(cid) / "timeline.md").read_text(encoding="utf-8")
    assert "The gate opened." in body and "It closed." in body
    chronicle.append_timeline(cid, [])  # no-op, no crash


def test_transcript_text_labels_roles():
    text = chronicle.transcript_text([{"role": "user", "content": "hi"},
                                      {"role": "assistant", "content": "hello"}])
    assert "**You:** hi" in text and "**Grimoire:** hello" in text


def test_build_prompt_includes_facts_and_transcript():
    msgs = chronicle.build_prompt("**You:** hi", {"location": "The Crypt",
                                                  "date": "2026-01-01", "cast": ["characters/seraphine"]})
    assert msgs[0]["role"] == "system"
    user = msgs[1]["content"]
    assert "The Crypt" in user and "2026-01-01" in user and "seraphine" in user and "**You:** hi" in user


def test_parse_output_extracts_json():
    text = ('Sure!\n```json\n{"one_line": "They met.", "summary": "A met B by the sea.",'
            ' "keywords": ["sea", ""], "timeline_events": [{"date": "2026-01-01", "text": "Met."}]}\n```')
    out = chronicle.parse_output(text)
    assert out == {"one_line": "They met.", "summary": "A met B by the sea.",
                   "keywords": ["sea"],
                   "timeline_events": [{"date": "2026-01-01", "text": "Met."}]}


def test_parse_output_tolerates_garbage():
    assert chronicle.parse_output("no json here") == {
        "one_line": "", "summary": "", "keywords": [], "timeline_events": []}
