"""The response settings a prompt rendered are recorded on the response."""

from grimoire import routes, store
from tests.llm_fakes import FakeLLM
from tests.test_character_turns import seed

HANDOFF = 'Hi.\n```handoff\n{"next":null}\n```'


def _record(client, cid, sid):
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    return base, rid, client.get(base + f"/responses/{rid}").json()


def test_first_take_records_the_settings_the_prompt_rendered(client):
    cid, sid = seed(client)
    store.scenes.set_response(cid, sid, {"response_continuation_words": "300"})
    fake = FakeLLM([[HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    _base, _rid, record = _record(client, cid, sid)
    assert record["settings"]["phase"] == "continuation"
    assert record["settings"]["words"] == 300
    assert "style_id" in record["settings"]
    assert "provenance" not in record["settings"]


def test_one_shot_override_is_in_the_recorded_settings(client):
    cid, sid = seed(client)
    fake = FakeLLM([[HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={
        "content": "Hello", "speaker_ref": "characters:mara",
        "response": {"response_continuation_words": "90"}})
    _base, _rid, record = _record(client, cid, sid)
    assert record["settings"]["words"] == 90


def test_reroll_does_not_re_resolve_settings(client):
    cid, sid = seed(client)
    store.scenes.set_response(cid, sid, {"response_continuation_words": "300"})
    fake = FakeLLM([[HANDOFF], [HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    rid = _record(client, cid, sid)[1]
    store.scenes.set_response(cid, sid, {"response_continuation_words": "120"})
    response = client.post(base + f"/responses/{rid}/regenerate", json={})
    assert response.status_code == 200, response.text
    assert fake.calls == 2
    record = client.get(base + f"/responses/{rid}").json()
    assert record["settings"]["words"] == 300


def test_roll_continuation_records_resume_settings(client):
    cid, sid = seed(client)
    store.scenes.set_response(cid, sid, {"response_continuation_words": "300"})
    fake = FakeLLM([
        ['Wait.\n```roll\n{"check":"notice"}\n```'],
        ['No roll.\n```handoff\n{"next":null}\n```'],
    ])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    proposal = store.proposals.get(cid, sid)
    store.scenes.set_response(cid, sid, {"response_continuation_words": "120"})
    response = client.post(
        base + "/roll-proposal", json={"proposal": proposal["id"], "action": "decline"})
    assert response.status_code == 200 and "error" not in response.text, response.text
    _base, _rid, record = _record(client, cid, sid)
    assert record["settings"]["words"] == 300
    assert record["resume_settings"]["words"] == 120
