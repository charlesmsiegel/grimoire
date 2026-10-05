"""The provider adapters' share of #377: an HTTP status on every status-mapped
error (so the facade can tell "the provider refused this request" from a server
error), no image bytes in an error detail or a capture, and strict folding that
can merge content parts."""

import httpx
import pytest

from grimoire import llm_capture
from grimoire.llm_errors import LLMError
from grimoire.openai_compatible import OpenAICompatibleClient, _strict_messages
from grimoire.openrouter import OpenRouterClient

ECHO = '{"detail":[{"msg":"bad image","input":"data:image/png;base64,QUJDREVGR0g="}]}'


def _capture(events):
    return {llm_capture.KEY: llm_capture.Capture(events.append, "c", 1, "m", "p")}


def test_llm_error_carries_an_optional_status():
    assert LLMError("bad_response", "x", status=422).status == 422
    assert LLMError("bad_response", "x").status is None


async def _drain(agen):
    return [c async for c in agen]


@pytest.mark.parametrize("which", ["openrouter", "openai_compatible"])
async def test_a_rejection_carries_its_status_and_no_image_bytes(which):
    http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(422, text=ECHO)))
    events: list = []
    usage = _capture(events)
    msgs = [{"role": "user", "content": "hi"}]
    if which == "openrouter":
        agen = OpenRouterClient(http=http).stream(msgs, "m", "k", usage=usage)
    else:
        agen = OpenAICompatibleClient(http=http).stream(
            msgs, "m", "", "https://x.example/v1", usage=usage)
    with pytest.raises(LLMError) as err:
        await _drain(agen)
    assert err.value.status == 422
    assert "QUJD" not in err.value.detail and "[elided]" in err.value.detail
    body = [e["payload"] for e in events if e["event"] == "http_error_body"]
    assert body and "QUJD" not in body[0] and "[elided]" in body[0]


@pytest.mark.parametrize("status", [500, 429, 401])
async def test_status_rides_on_every_mapped_kind(status):
    http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(status, text="{}")))
    with pytest.raises(LLMError) as err:
        await _drain(OpenRouterClient(http=http).stream(
            [{"role": "user", "content": "hi"}], "m", "k"))
    assert err.value.status == status


IMG = {"type": "image_url", "image_url": {"url": "d"}}


def test_strict_folds_a_system_block_into_a_parts_list():
    out = _strict_messages([
        {"role": "system", "content": "S"},
        {"role": "user", "content": [{"type": "text", "text": "u"}, IMG]}])
    assert out == [{"role": "user", "content": [{"type": "text", "text": "S\n\nu"}, IMG]}]


def test_strict_merges_two_user_lists_and_a_trailing_system_block():
    out = _strict_messages([
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": [IMG]},
        {"role": "system", "content": "P"}])
    assert out == [{"role": "user", "content": "a"},
                   {"role": "assistant", "content": "b"},
                   {"role": "user", "content": [IMG, {"type": "text", "text": "\n\nP"}]}]


def test_strict_string_folding_is_unchanged():
    out = _strict_messages([
        {"role": "system", "content": "S"},
        {"role": "user", "content": "u"},
        {"role": "user", "content": "v"},
        {"role": "assistant", "content": "a"},
        {"role": "system", "content": "P"}])
    assert out == [{"role": "user", "content": "S\n\nu\n\nv"},
                   {"role": "assistant", "content": "a"},
                   {"role": "user", "content": "P"}]
