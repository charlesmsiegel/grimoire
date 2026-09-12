import asyncio
import json

import httpx
import pytest

from grimoire import routes, store
from grimoire.llm import LLMClient
from grimoire.openai_compatible import OpenAICompatibleClient
from tests.test_character_turns import seed

CONN = {"kind": "openai_compatible", "model": "glm-5.3", "base_url": "https://example.test/v1"}
THOUGHT = "Private planning ```roll\n{not a roll}\n``` <script>alert(1)</script>"


def gateway(thought=THOUGHT, requests=None):
    def handler(request):
        if requests is not None:
            requests.append(json.loads(request.content))
        events = [{"choices": [{"delta": {"reasoning_content": thought}}]},
                  {"choices": [{"delta": {"content": '"Hello."\n```handoff\n{"next":null}\n```'}}]}]
        return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events))
    return LLMClient(openai_compatible=OpenAICompatibleClient(
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler))), retries=0)


async def test_reasoning_events_precede_visible_text_without_entering_complete():
    from grimoire import llm_reasoning
    client = gateway()
    try:
        events = [e async for e in llm_reasoning.stream(client, [], CONN, {})]
        assert ''.join(e.get("thinking_delta", "") for e in events) == THOUGHT
        assert next(i for i,e in enumerate(events) if e.get("thinking_delta")) < next(i for i,e in enumerate(events) if e.get("delta"))
        assert THOUGHT not in await client.complete([], CONN)
    finally:
        await client.aclose()


@pytest.mark.parametrize("effort", ["", "low", "high", "max"])
async def test_glm_effort_is_opt_in_and_sent_to_provider(effort):
    requests = []
    client = gateway(requests=requests)
    try:
        await client.complete([], {**CONN, "reasoning_effort": effort})
    finally:
        await client.aclose()
    assert requests[0].get("reasoning_effort") == (effort or None)


def test_reasoning_is_saved_per_variant_and_never_in_transcript_content(client):
    cid, sid = seed(client)
    conn_id = store.llm_connections.create_connection("openai_compatible", "Local", **{
        k:v for k,v in CONN.items() if k != "kind"})
    store.config.write_config(active_connection_id=conn_id)
    requests = []
    model = gateway(requests=requests)
    client.app.dependency_overrides[routes.get_llm] = lambda: model
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"speaker_ref": "characters:mara"})
    assert response.status_code == 200, response.text
    assert 'thinking_delta' in response.text
    message = store.scenes.read_scene(cid, sid)["messages"][-1]
    assert THOUGHT not in message["content"]
    assert message["response_thinking"]
    record = store.responses.get(cid, sid, message["response_id"])
    variant = next(v for v in record["variants"] if v["id"] == record["active_variant"])
    assert variant["reasoning"] == THOUGHT
    assert record["status"] == "complete"
    assert not store.proposals.get(cid, sid)
    response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content":"Go on.", "speaker_ref":"characters:mara"})
    assert response.status_code == 200
    assert THOUGHT not in json.dumps(requests[-1]["messages"])
    original = variant["id"]
    replacement = store.responses.save_variant(cid, sid, message["response_id"], '"Again."', "complete", reasoning="Replacement thought")
    assert store.scenes.read_scene(cid, sid)["messages"][0]["response_thinking"] == replacement["id"]
    store.responses.activate(cid, sid, message["response_id"], original)
    assert store.scenes.read_scene(cid, sid)["messages"][0]["response_thinking"] == original


def test_reasoning_effort_connection_roundtrip(client):
    response = client.post("/api/llm-connections", json={"kind":"openai_compatible", "name":"Local", "model":"glm-5.3", "reasoning_effort":"low"})
    assert response.status_code == 200, response.text
    cid = response.json()["id"]
    assert client.get(f"/api/llm-connections/{cid}").json()["reasoning_effort"] == "low"
    assert client.put(f"/api/llm-connections/{cid}", json={"reasoning_effort":""}).status_code == 200
    assert store.llm_connections.read_connection_raw(cid)["reasoning_effort"] == ""


async def test_reasoning_is_delivered_while_provider_is_still_thinking():
    from grimoire import llm_reasoning
    release = asyncio.Event()
    class Frames(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"reasoning_content":"Planning"}}]}\n\n'
            await release.wait()
            yield b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
    client = LLMClient(openai_compatible=OpenAICompatibleClient(http=httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Frames())))))
    events = llm_reasoning.stream(client, [], CONN, {})
    try:
        async def first_thought():
            async for event in events:
                if event.get("thinking_delta"):
                    return event["thinking_delta"]
            raise AssertionError("stream ended without reasoning")
        assert await asyncio.wait_for(first_thought(), 1) == "Planning"
        assert not release.is_set()
        release.set()
        assert ''.join([e.get("delta", "") async for e in events]) == "Hello"
    finally:
        await events.aclose()
        await client.aclose()


async def test_retry_resets_failed_attempt_reasoning(monkeypatch):
    from grimoire import llm, llm_reasoning
    monkeypatch.setattr(llm, "_backoff_delay", lambda attempt: 0)
    calls = []
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"reasoning_content":"Old thought"}}]}\n\n'
            raise httpx.ReadError("disconnected")
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, stream=Broken())
        return httpx.Response(200, text='data: {"choices":[{"delta":{"reasoning_content":"New thought", "content":"Hello"}}]}\n')
    client = LLMClient(openai_compatible=OpenAICompatibleClient(http=httpx.AsyncClient(
        transport=httpx.MockTransport(handler))), retries=1)
    thought = ""
    try:
        events = [e async for e in llm_reasoning.stream(client, [], CONN, {})]
        for event in events:
            if event.get("thinking_reset"):
                thought = ""
            thought += event.get("thinking_delta", "")
        assert thought == "New thought"
        assert sum(bool(e.get("thinking_reset")) for e in events) == 2
        assert ''.join(e.get("delta", "") for e in events) == "Hello"
    finally:
        await client.aclose()


async def test_glm_effort_is_not_sent_after_switching_to_another_model():
    requests = []
    client = gateway(requests=requests)
    try:
        await client.complete([], {**CONN, "model":"another-model", "reasoning_effort":"max"})
        assert "reasoning_effort" not in requests[0]
    finally:
        await client.aclose()


def test_reasoning_matches_the_selected_content_choice():
    from grimoire import llm_reasoning
    buffer = llm_reasoning.Buffer()
    llm_reasoning.from_chunk({"choices": [
        {"delta": {"reasoning_content":"Selected"}},
        {"delta": {"reasoning_content":"Other candidate"}},
    ]}, {llm_reasoning.KEY: buffer})
    assert buffer.drain() == [{"thinking_delta":"Selected"}]
