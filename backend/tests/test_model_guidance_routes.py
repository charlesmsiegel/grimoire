"""Scene guidance follows the requested route, including a fallback attempt."""

import pytest

from grimoire import llm, routes, store
from grimoire.llm_errors import LLMError
from tests.llm_fakes import FakeLLM, ScriptedProvider


def _scene(client):
    client.put("/api/llm-connections/openrouter",
               json={"api_key": "sk-test", "model": "vendor/unknown"})
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Saltmarch"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "We agreed to meet at noon.")
    store.scenes.append_message(cid, sid, "assistant", "Mara nods.")
    return cid, sid


def _profile_rows(breakdown):
    return [r for r in breakdown["sections"] if r["id"] == "model_guidance"]


@pytest.mark.parametrize("action,body", [
    ("chat", {"content": "Shall we go?"}),
    ("chat", {"content": ""}),
    ("retry", {}),
    ("regenerate", {}),
    ("opener", {"prompt": "Open at the harbor."}),
])
def test_scene_routes_follow_current_model_not_historical_stamp(client, action, body):
    cid, sid = _scene(client)
    client.put("/api/llm-connections/openrouter", json={"model": "glm-5.3"})
    fake = FakeLLM([["Mara nods."]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake

    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/{action}", json=body)

    assert response.status_code == 200
    assert fake.requests
    entries = store.prompt_log.list_entries(cid, sid)
    captured = store.prompt_log.read_entry(cid, entries[0]["id"], scene=sid)
    assert captured["model"] == "glm-5.3"
    row, = _profile_rows(captured)
    assert row["text"] in fake.requests[0]["messages"][0]["content"]


def test_live_inspector_uses_campaign_route_and_needs_no_credentials(client):
    cid, sid = _scene(client)
    connection = client.post("/api/llm-connections", json={
        "kind": "openrouter", "name": "Mara", "model": "z-ai/glm-5.3"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/routing", json={"routes": {"scene": connection}})
    live = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()
    assert live["model"] == "z-ai/glm-5.3"
    assert len(_profile_rows(live)) == 1


def test_reroll_override_gets_guidance_without_changing_next_turn(client):
    cid, sid = _scene(client)
    fake = FakeLLM([["Mara nods."]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate",
                           json={"model": "glm-5.3", "guidance": "Keep it brief."})
    assert response.status_code == 200
    entries = store.prompt_log.list_entries(cid, sid)
    captured = store.prompt_log.read_entry(cid, entries[0]["id"], scene=sid)
    assert captured["model"] == "glm-5.3"
    assert len(_profile_rows(captured)) == 1
    assert fake.requests[0]["messages"][-1]["content"].find("Keep it brief.") >= 0
    live = client.get(f"/api/campaigns/{cid}/scenes/{sid}/context").json()
    assert live["model"] == "vendor/unknown"
    assert _profile_rows(live) == []


def test_fallback_captures_matching_profile_only_when_attempted(client):
    cid, sid = _scene(client)
    client.put("/api/llm-connections/openrouter", json={"model": "glm-5.3"})
    primary = ScriptedProvider(chunks=(), error=LLMError("auth", "refused"))
    fallback = ScriptedProvider(chunks=("Mara nods.",))
    facade = llm.LLMClient(openrouter=primary, openai_compatible=fallback, retries=0,
                          fallback={"kind": "openai_compatible", "model": "vendor/unknown",
                                    "base_url": "https://example.test/v1"})
    client.app.dependency_overrides[routes.get_llm] = lambda: facade
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                       json={"content": "Shall we go?"}).status_code == 200
    entries = store.prompt_log.list_entries(cid, sid)
    assert [e["model"] for e in entries] == ["vendor/unknown", "glm-5.3"]
    captures = [store.prompt_log.read_entry(cid, e["id"], scene=sid) for e in entries]
    assert _profile_rows(captures[0]) == []
    row, = _profile_rows(captures[1])
    assert row["text"] in primary.requests[0]["messages"][0]["content"]
    assert row["text"] not in fallback.requests[0]["messages"][0]["content"]
