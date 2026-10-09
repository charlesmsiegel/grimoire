import asyncio
import dataclasses
import json

import httpx
import pytest

from grimoire import routes, store, wire
from grimoire.llm import LLMClient
from grimoire.openai_compatible import OpenAICompatibleClient
from grimoire.store.inference import resolve
from tests.inference_fixtures import primary
from tests.test_character_turns import seed

CONN = wire.Target(provider_id="", kind="openai_compatible", model="glm-5.3",
                   requested_model="glm-5.3", base_url="https://example.test/v1")
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
    holder: dict = {}
    try:
        events = [e async for e in llm_reasoning.stream(
            holder, lambda: client.stream([], CONN, holder))]
        assert ''.join(e.get("thinking_delta", "") for e in events) == THOUGHT
        assert next(i for i,e in enumerate(events) if e.get("thinking_delta")) < next(i for i,e in enumerate(events) if e.get("delta"))
        assert THOUGHT not in await client.complete([], CONN)
    finally:
        await client.aclose()


@pytest.mark.parametrize("effort", ["", "low", "high", "max"])
async def test_glm_effort_is_opt_in_and_sent_to_provider(effort):
    """The preset's effort, and only the preset's (slice I): a connection's
    legacy `reasoning_effort` sends nothing -- a legacy store's rides on a
    derived reasoning preset instead (`legacy_plan`)."""
    requests = []
    client = gateway(requests=requests)
    params = {"reasoning_effort": effort} if effort else {}
    try:
        await client.complete([], dataclasses.replace(CONN, sampling=wire.Sampling(params=params)))
        # A legacy `reasoning_effort` on a connection record is built into
        # nothing: a target has no field for it.
        legacy = {"kind": "openai_compatible", "model": "glm-5.3",
                  "base_url": "https://example.test/v1", "reasoning_effort": effort or "high"}
        await client.complete([], resolve.provider_target(legacy))
    finally:
        await client.aclose()
    assert requests[0].get("reasoning_effort") == (effort or None)
    assert "reasoning_effort" not in requests[1]


def test_reasoning_is_saved_per_variant_and_never_in_transcript_content(client):
    cid, sid = seed(client)
    conn_id = store.llm_connections.create_connection("openai_compatible", "Local",
                                                      base_url=CONN.base_url)
    primary(client, CONN.model, provider=conn_id, api_key="")
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


def test_reasoning_effort_connection_roundtrip(legacy_client):
    # A provider's own `reasoning_effort` is the legacy layout's: at format 2
    # it is a model field the provider routes refuse (`MODEL_FIELDS`).
    client = legacy_client
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
    holder: dict = {}
    events = llm_reasoning.stream(holder, lambda: client.stream([], CONN, holder))
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
    holder: dict = {}
    try:
        events = [e async for e in llm_reasoning.stream(
            holder, lambda: client.stream([], CONN, holder))]
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
        await client.complete([], dataclasses.replace(
            CONN, model="another-model", requested_model="another-model",
            sampling=wire.Sampling(params={"reasoning_effort": "max"})))
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
