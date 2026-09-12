"""Reviewed creation is source-checked and separate from a detached draft."""
import time

from grimoire import routes
from grimoire.routes import passage_characters
from grimoire.store import (
    appearances,
    campaigns,
    characters,
    overlay,
    passage_evidence,
    responses,
    scenes,
    worlds,
)
from grimoire.store.scenes import serialize
from tests.llm_fakes import FakeOpenRouterComplete


def source(client):
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    sid = scenes.create_scene(cid, "Arrival")
    text = 'Mara said, "Wait." Winifred said, "Go."'
    scenes.append_message(cid, sid, "assistant", text, speaker="Grimoire")
    rid = responses.migrate(cid, sid)[0]["id"]
    body = {"name": "Mara", "passage": text, "source_text": text,
            "description": "Watches the door.", "mes_example": passage_evidence.examples(text, "Mara")}
    return cid, sid, rid, body


def test_review_creates_campaign_card_and_seats_it(client):
    cid, sid, rid, body = source(client)
    saved = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/character", json=body)
    assert saved.status_code == 200, saved.text
    aid = saved.json()["character"]
    assert appearances.scene_cast(cid, sid)[0]["id"] == aid
    card = characters.read_card(overlay.char_root(cid, aid), aid, saved.json()["version"])
    assert "Go." not in card["data"]["mes_example"]
    assert card["data"]["extensions"]["grimoire"]["passage_evidence"][0]["response_id"] == rid


def test_changed_source_rejects_save_without_creating_character(client):
    cid, sid, rid, body = source(client)
    body["source_text"] = "An earlier version."
    reply = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/character", json=body)
    assert reply.status_code == 409
    assert reply.json()["kind"] == "source_changed"
    assert overlay.list_characters(cid) == []


def test_draft_is_detached_optional_and_does_not_save(client):
    cid, sid, rid, body = source(client)
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouterComplete("Watches the door.")
    started = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/character-draft", json=body)
    assert started.status_code == 202, started.text
    run = started.json()["run"]
    assert run["cls"] == "draft"
    deadline = time.monotonic() + 5
    while run["state"] == "running" and time.monotonic() < deadline:
        run = client.get(f"/api/campaigns/{cid}/runs/{run['id']}").json()["run"]
        time.sleep(0.01)
    assert run["state"] == "landed", run
    assert run["result"]["description"] == "Watches the door."
    assert run["result"]["quotes"] == ["Wait."]
    assert overlay.list_characters(cid) == []


def test_save_is_refused_while_the_scene_is_generating(client):
    cid, sid, rid, body = source(client)
    identity = scenes.scene_identity(cid, sid)
    run, _ = client.app.state.runs.start_or_existing(
        ("scene", cid, identity), "turn", "chat", "held", identity,
        {"campaign": "Saltmarch", "scene": "Arrival"})
    try:
        reply = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/character", json=body)
        assert reply.status_code == 409
        assert reply.json()["kind"] == "scene_busy"
        assert overlay.list_characters(cid) == []
    finally:
        run.finish("cancelled")


def test_attach_to_player_fails_before_mutating_the_card(client):
    cid, sid, rid, body = source(client)
    aid, vid = overlay.create_character(cid, "Mara")
    appearances.appear(cid, sid, "characters", aid, vid, "player", narrate=False)
    before = characters.read_card(overlay.char_root(cid, aid), aid, vid)
    body["existing_ref"] = "characters:" + aid
    reply = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/character", json=body)
    assert reply.status_code == 422
    assert characters.read_card(overlay.char_root(cid, aid), aid, vid) == before


def test_retry_after_seating_failure_reuses_created_card(client, monkeypatch):
    cid, sid, rid, body = source(client)
    original = appearances.appear
    def fail_seating(*args, **kwargs):
        raise appearances.AppearError("Please retry seating.")
    monkeypatch.setattr(appearances, "appear", fail_seating)
    url = f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/character"
    failed = client.post(url, json=body)
    assert failed.status_code == 422
    assert len(overlay.list_characters(cid)) == 1
    monkeypatch.setattr(appearances, "appear", original)
    saved = client.post(url, json=body)
    assert saved.status_code == 200, saved.text
    assert len(overlay.list_characters(cid)) == 1
    result = saved.json()
    card = characters.read_card(overlay.char_root(cid, result["character"]), result["character"], result["version"])
    assert len(card["data"]["extensions"]["grimoire"]["passage_evidence"]) == 1
    assert len(appearances.scene_cast(cid, sid)) == 1


def test_retry_attachment_does_not_duplicate_reviewed_description(client):
    cid, sid, rid, body = source(client)
    aid, vid = overlay.create_character(cid, "Mara")
    body["existing_ref"] = "characters:" + aid
    url = f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/character"
    assert client.post(url, json=body).status_code == 200
    assert client.post(url, json=body).status_code == 200
    card = characters.read_card(overlay.char_root(cid, aid), aid, vid)
    assert card["data"]["description"] == body["description"]
    assert len(card["data"]["extensions"]["grimoire"]["passage_evidence"]) == 1


def test_quote_review_needs_no_model_connection(client):
    cid, sid, rid, body = source(client)
    preview = client.post(f"/api/campaigns/{cid}/scenes/{sid}/responses/{rid}/character-evidence", json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["quotes"] == ["Wait."]
    assert preview.json()["mes_example"] == body["mes_example"]
    assert overlay.list_characters(cid) == []


def test_neighbor_selection_excludes_director_and_nonconversation_entries():
    posts = [{"role": "user", "content": "Observable earlier speech"},
             {"role": "assistant", "speaker": serialize.DIRECTOR_SPEAKER, "content": "Private direction"},
             {"role": "system", "content": "Private instruction"},
             {"role": "assistant", "speaker": serialize.ROLL_SPEAKER, "content": "Internal roll details"},
             {"role": "assistant", "speaker": "Winifred", "content": "Observable later speech"},
             {"role": "assistant", "speaker": "Grimoire", "content": "Source"}]
    assert passage_characters._observable_neighbors(posts, 5) == [posts[0], posts[4]]
