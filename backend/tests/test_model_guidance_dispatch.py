"""The real LLM facade selects frozen variants before provider transforms."""

import json

import httpx
import pytest

from grimoire import llm, model_guidance, openai_compatible
from grimoire.llm_errors import LLMError
from tests.llm_fakes import ScriptedProvider


def _prepared(model):
    def compose(selected):
        text = "Shared scene instructions."
        if selected == "glm-5.3":
            text += " GLM profile."
        return ([{"role": "system", "content": text},
                 {"role": "user", "content": "Meet at noon."},
                 {"role": "assistant", "content": "Agreed."},
                 {"role": "system", "content": "Keep it brief."}],
                {"model": selected})
    return model_guidance.PreparedMessages(model, compose)


@pytest.mark.parametrize("primary_model,fallback_model", [
    ("glm-5.3", "vendor/unknown"), ("vendor/unknown", "glm-5.3"),
])
async def test_each_dispatch_uses_its_own_model(primary_model, fallback_model):
    primary = ScriptedProvider(chunks=(), error=LLMError("auth", "refused"))
    fallback = ScriptedProvider(chunks=("Mara nods.",))
    facade = llm.LLMClient(openrouter=primary, openai_compatible=fallback, retries=0,
                          fallback={"kind": "openai_compatible", "model": fallback_model})
    messages = _prepared(primary_model)
    captured = []
    messages.on_variant = lambda model, breakdown: captured.append((model, breakdown))

    assert await facade.complete(messages, {"model": primary_model}) == "Mara nods."

    assert ("GLM profile." in primary.requests[0]["messages"][0]["content"]) == (
        primary_model == "glm-5.3")
    assert ("GLM profile." in fallback.requests[0]["messages"][0]["content"]) == (
        fallback_model == "glm-5.3")
    assert captured == [(fallback_model, {"model": fallback_model})]


async def test_same_model_retry_uses_identical_prompt_without_fallback_capture(monkeypatch):
    # Zero backoff keeps the real retry loop fast; no replacement retry logic.
    monkeypatch.setattr(llm, "_backoff_delay", lambda *args: 0)
    primary = ScriptedProvider(chunks=(), error=LLMError("network", "reset"))
    facade = llm.LLMClient(openrouter=primary, retries=1)
    messages = _prepared("glm-5.3")
    captured = []
    messages.on_variant = lambda model, breakdown: captured.append(model)
    with pytest.raises(LLMError):
        await facade.complete(messages, {"model": "glm-5.3"})
    assert len(primary.requests) == 2
    assert primary.requests[0]["messages"] == primary.requests[1]["messages"]
    assert captured == []


async def test_no_fallback_or_capture_after_visible_output():
    primary = ScriptedProvider(chunks=("Mara",), error=LLMError("network", "reset"))
    fallback = ScriptedProvider()
    facade = llm.LLMClient(openrouter=primary, openai_compatible=fallback, retries=0,
                          fallback={"kind": "openai_compatible", "model": "vendor/unknown"})
    messages = _prepared("glm-5.3")
    captured = []
    messages.on_variant = lambda model, breakdown: captured.append(model)
    with pytest.raises(LLMError):
        await facade.complete(messages, {"model": "glm-5.3"})
    assert fallback.calls == 0
    assert captured == []


async def test_plain_utility_messages_never_acquire_scene_guidance():
    primary = ScriptedProvider(chunks=(), error=LLMError("auth", "refused"))
    fallback = ScriptedProvider(chunks=("{}",))
    facade = llm.LLMClient(openrouter=primary, openai_compatible=fallback, retries=0,
                          fallback={"kind": "openai_compatible", "model": "glm-5.3"})
    messages = [{"role": "system", "content": "Return a JSON object."}]
    assert await facade.complete(messages, {"model": "vendor/unknown"}) == "{}"
    assert primary.requests[0]["messages"] == fallback.requests[0]["messages"] == messages


async def test_strict_endpoint_receives_selected_profile_and_final_content_only():
    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        frames = [
            {"choices": [{"delta": {"reasoning_content": "private planning"}}]},
            {"choices": [{"delta": {"content": "Mara nods."}}]},
        ]
        body = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
        return httpx.Response(200, text=body + "data: [DONE]\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = openai_compatible.OpenAICompatibleClient(http=http)
        facade = llm.LLMClient(openai_compatible=provider, retries=0)
        messages = _prepared("vendor/unknown")
        # Dispatch is authoritative even if the prepared primary names another model.
        result = await facade.complete(messages, {
            "kind": "openai_compatible", "model": "glm-5.3",
            "base_url": "https://example.test/v1", "post_process": "strict"})

    assert result == "Mara nods."
    payload, = payloads
    assert payload["model"] == "glm-5.3"
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant", "user"]
    assert "GLM profile." in payload["messages"][0]["content"]
    assert payload["messages"][-1]["content"] == "Keep it brief."
    assert not {"temperature", "top_p", "thinking", "reasoning_effort"} & payload.keys()
