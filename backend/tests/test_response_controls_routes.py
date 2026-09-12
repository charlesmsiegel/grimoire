"""User-facing response controls reject unsafe edits without another model call."""
import pytest

from grimoire import routes, store
from grimoire.llm_errors import LLMError
from tests.llm_fakes import FakeLLM
from tests.test_character_turns import seed


def _answer(client, base, text="Original."):
    fake = FakeLLM([[text + '\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(base + "/chat", json={"speaker_ref": "characters:mara"})
    assert "error" not in result.text
    return client.get(base).json()["messages"][-1]["response_id"]


@pytest.mark.parametrize("method,suffix", [("get", ""), ("delete", ""),
    ("post", "/regenerate"), ("post", "/variants/missing/activate")])
def test_missing_response_is_a_404_and_does_not_generate(client, method, suffix):
    cid, sid = seed(client)
    fake = FakeLLM([["Must not run"]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    result = getattr(client, method)(base + "/responses/missing" + suffix)
    assert result.status_code == 404
    assert fake.calls == 0


@pytest.mark.parametrize("actor", ["characters:absent", "pcs:seraphine", "../mara"])
def test_invalid_speaker_does_not_append_the_player_post(client, actor):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    before = store.scenes.read_scene(cid, sid)["messages"]
    assert client.post(base + "/chat", json={"content": "Wait.", "speaker_ref": actor}).status_code == 400
    assert store.scenes.read_scene(cid, sid)["messages"] == before


def test_legacy_reply_is_deletable_but_cannot_be_rerolled_from_current_state(client):
    cid, sid = seed(client)
    store.scenes.append_message(cid, sid, "assistant", "Old narration.")
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = client.get(base).json()["messages"][-1]["response_id"]
    result = client.post(base + f"/responses/{rid}/regenerate")
    assert result.status_code == 409 and "historical_context_unavailable" in result.text
    assert client.delete(base + f"/responses/{rid}").status_code == 200
    assert not any(m.get("response_id") == rid for m in client.get(base).json()["messages"])


def test_variant_controls_keep_later_replies_and_reject_incomplete_versions(client):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base)
    later = _answer(client, base, "Later.")
    original = store.responses.get(cid, sid, rid)["active_variant"]
    replacement = store.responses.save_variant(cid, sid, rid, "Replacement.", "complete", activate=False)
    incomplete = store.responses.save_variant(cid, sid, rid, "Partial", "incomplete", activate=False)
    result = client.post(base + f"/responses/{rid}/variants/{replacement['id']}/activate")
    assert result.status_code == 200
    messages = result.json()["messages"]
    assert messages[-2]["content"] == "Replacement."
    assert messages[-1]["response_id"] == later and messages[-1]["context_changed"]
    for vid in (incomplete["id"], "unknown"):
        assert client.post(base + f"/responses/{rid}/variants/{vid}/activate").status_code == 409
    assert client.post(base + f"/responses/{rid}/variants/{original}/activate").status_code == 200
    assert store.responses.get(cid, sid, rid)["content"] == "Original."


def test_retry_after_same_length_player_edit_is_refused(client):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    fake = FakeLLM([["Partial"]], error=LLMError("rate_limit", "Wait"))
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    client.post(base + "/chat", json={"content": "Stay", "speaker_ref": "characters:mara"})
    posts = store.scenes.read_scene(cid, sid)["messages"]
    index = next(i for i, m in enumerate(posts) if m["role"] == "user")
    store.scenes.edit_message(cid, sid, index, "Move")
    result = client.post(base + "/retry")
    assert result.status_code == 409 and "context_changed" in result.text
    assert fake.calls == 1


@pytest.mark.parametrize("answer", ["not json", "{}", '{"next":"pcs:seraphine"}', '{"next":"absent"}'])
def test_invalid_selector_stops_without_a_repair_or_actor_call(client, answer):
    cid, sid = seed(client)
    fake = FakeLLM([[answer]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    result = client.post(base + "/chat", json={"content": "Hello"})
    assert result.status_code == 200 and fake.calls == 1
    assert not any(m.get("response_id") for m in client.get(base).json()["messages"])


def test_reroll_attempt_replay_does_not_spend_again(client):
    cid, sid = seed(client)
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base)
    fake = FakeLLM([['Replacement.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    headers = {"X-Grimoire-Attempt": "response-reroll-attempt"}
    for _ in range(2):
        result = client.post(base + f"/responses/{rid}/regenerate", headers=headers)
        assert result.status_code == 200 and "Replacement." in result.text
    assert fake.calls == 1
    assert len(store.responses.get(cid, sid, rid)["variants"]) == 2


def test_explicit_actor_remains_available_in_combined_mode(client):
    cid, sid = seed(client)
    client.put("/api/config", json={"character_response_mode": "combined"})
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    fake = FakeLLM([['Mara answers.\n```handoff\n{"next":"characters:winifred"}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    result = client.post(base + "/chat", json={"speaker_ref": "characters:mara"})
    assert result.status_code == 200 and fake.calls == 1
    assert client.get(base).json()["messages"][-1]["speaker"] == "Mara"


def test_legacy_cast_retains_observations_across_later_player_rounds(client):
    from grimoire.store.appearances import paths

    cid, sid = seed(client)
    data = paths.record(cid)
    for rec in data.values():
        rec.pop("presence", None)
    paths._write(cid, data)
    store.scenes.append_message(cid, sid, "assistant", "PREHISTORY_PRIVATE_SENTINEL")
    fake = FakeLLM([
        ['WITNESSED_SENTINEL.\n```handoff\n{"next":null}\n```'],
        ['Winifred remembers.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "We meet here.", "speaker_ref": "characters:mara"})
    client.post(base + "/chat", json={"content": "Continue that thought.", "speaker_ref": "characters:winifred"})
    second_prompt = str(fake.requests[1]["messages"])
    assert "WITNESSED_SENTINEL" in second_prompt
    assert "PREHISTORY_PRIVATE_SENTINEL" not in second_prompt
    assert paths.record(cid)["characters/winifred"]["presence"][sid] == [{"start": 1, "end": None}]
