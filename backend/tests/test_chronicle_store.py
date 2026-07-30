
from grimoire.store import campaigns, chronicle, scenes, worlds


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


def test_transcript_text_prefers_speakers():
    text = chronicle.transcript_text([
        {"role": "user", "content": "hi", "speaker": "Elara Vane"},
        {"role": "assistant", "content": "yo"}])
    assert "**Elara Vane:** hi" in text and "**Grimoire:** yo" in text


def test_transcript_text_hides_transition_tag():
    """The transition tag is internal drift metadata, never a speaker an LLM
    prompt should see — a tagged transition must render identically to the
    same content untagged (spec: never expose ⁣Scene to a prompt)."""
    tagged = chronicle.transcript_text([
        {"role": "assistant", "content": "*Time passes. It is now dusk.*",
         "speaker": scenes.TRANSITION_SPEAKER}])
    untagged = chronicle.transcript_text([
        {"role": "assistant", "content": "*Time passes. It is now dusk.*"}])
    assert tagged == untagged
    assert "⁣" not in tagged
    assert "Scene:" not in tagged
    assert "**Grimoire:** *Time passes. It is now dusk.*" in tagged


def test_transcript_text_keeps_roll_label():
    """Manual dice-roll lines are genuine transcript content; their existing
    labelling is intentional and long-standing and must not be stripped."""
    text = chronicle.transcript_text([
        {"role": "assistant", "content": "Rolled 1d20: 14",
         "speaker": scenes.ROLL_SPEAKER}])
    assert f"**{scenes.ROLL_SPEAKER}:** Rolled 1d20: 14" in text


def test_transcript_text_no_transition_marker_leaks():
    text = chronicle.transcript_text([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "*The scene moves to the docks.*",
         "speaker": scenes.TRANSITION_SPEAKER},
        {"role": "assistant", "content": "hello"}])
    assert "⁣" not in text
