"""The facade's share of #377: lowering image references per route, the
degrade-to-text sibling, and the guarantees around both."""

import json

import httpx
import pytest

from grimoire import content_parts as cp
from grimoire import llm
from grimoire.llm import LLMClient
from grimoire.llm_errors import LLMError
from grimoire.model_guidance import PreparedMessages
from grimoire.openai_compatible import OpenAICompatibleClient
from tests.llm_fakes import ScriptedProvider, SequencedProvider

MAP = cp.ref("/api/campaigns/c/images/coastline", "a map", False)
HALL = cp.ref("/api/campaigns/c/images/hall", "the hall", True)


def _t(text):
    return {"type": "text", "text": text}


def _msgs():
    return [{"role": "system", "content": "SYS"},
            {"role": "user", "content": [_t("Look a map"), MAP]},
            {"role": "assistant", "content": "The doors open. the hall"},
            {"role": "user", "content": [HALL], cp.CARRIER: True},
            {"role": "system", "content": "POST"}]


def _prepared(msgs=None, campaign="c"):
    msgs = _msgs() if msgs is None else msgs
    return PreparedMessages("m", lambda _model: (msgs, None), campaign=campaign)


def _conn(id_="a", kind="openrouter", vision="on", model="m"):
    return {"id": id_, "kind": kind, "model": model, "api_key": "k", "vision": vision}


def _images(conn):
    return 3 if conn.get("vision") == "on" and conn.get("kind") != "claude" else 0


loaded: list = []


def _load(cid, part):
    loaded.append(cid)
    return f"data:image/png;base64,{part['alt'].replace(' ', '')}"


def _client(openrouter, *, claude=None, fallback=None, images=_images, load=_load, retries=0):
    return LLMClient(openrouter=openrouter, claude=claude or ScriptedProvider(),
                     retries=retries, fallback=fallback, images=images, load_image=load)


async def _run(client, messages, conn, usage=None):
    return "".join([c async for c in client.stream(messages, conn, usage)])


def _image_roles(messages):
    return [m["role"] for m in messages if isinstance(m["content"], list)
            and any(p.get("type") == "image_url" for p in m["content"])]


async def test_a_vision_route_gets_image_parts_in_user_messages_only():
    loaded.clear()
    provider = SequencedProvider([("ok",)])
    usage: dict = {}
    assert await _run(_client(provider), _prepared(), _conn(), usage) == "ok"
    sent = provider.requests[0]["messages"]
    assert _image_roles(sent) == ["user", "user"]
    assert all(cp.CARRIER not in m for m in sent)
    assert sent[3]["content"][0] == _t("[Image from the previous reply: the hall]")
    assert usage["images"] == 2
    assert loaded == ["c", "c"]


async def test_a_text_route_gets_exactly_the_text_lowering():
    provider = SequencedProvider([("ok",)])
    usage: dict = {}
    await _run(_client(provider), _prepared(), _conn(vision="off"), usage)
    assert provider.requests[0]["messages"] == cp.as_text(_msgs())
    assert usage["images"] == 0


async def test_it_works_without_a_usage_holder():
    provider = SequencedProvider([("ok",)])
    assert await _run(_client(provider), _prepared(), _conn()) == "ok"


async def test_a_broken_resolver_reads_as_no_images():
    def boom(_conn):
        raise RuntimeError("sidecar exploded")
    provider = SequencedProvider([("ok",)])
    await _run(_client(provider, images=boom), _prepared(), _conn())
    assert provider.requests[0]["messages"] == cp.as_text(_msgs())


async def test_an_image_that_will_not_load_is_left_out():
    """Review Focus 2: the file went away between compose and dispatch."""
    def load(_cid, part):
        if part["alt"] == "the hall":
            raise OSError("gone")
        return "data:image/png;base64,AA"
    provider = SequencedProvider([("ok",)])
    usage: dict = {}
    await _run(_client(provider, load=load), _prepared(), _conn(), usage)
    sent = provider.requests[0]["messages"]
    assert _image_roles(sent) == ["user"]
    assert usage["images"] == 1
    assert len(sent) == 4  # the carrier had nothing left to carry


async def test_a_snapshot_replayed_with_the_setting_off_sends_text():
    """Review Focus 3: the prompt was frozen with images; the setting is off now."""
    prepared = _prepared()
    restored = PreparedMessages.from_snapshot(
        {"version": 1, "primary_model": "m", "unprofiled": [_msgs(), None], "profiles": {}},
        "m", campaign="c")
    assert cp.has_refs(restored) and prepared.campaign == restored.campaign
    provider = SequencedProvider([("ok",)])
    await _run(_client(provider, images=lambda _c: 0), restored, _conn())
    assert provider.requests[0]["messages"] == cp.as_text(_msgs())


async def test_a_claude_fallback_is_kept_and_sent_text():
    primary = SequencedProvider([LLMError("network", "down")])
    claude = SequencedProvider([("from claude",)])
    client = _client(primary, claude=claude,
                     fallback=lambda: {"id": "b", "kind": "claude", "model": "opus"})
    assert await _run(client, _prepared(), _conn()) == "from claude"
    assert claude.requests[0]["messages"] == cp.as_text(_msgs())


async def test_a_drafts_own_image_parts_still_exclude_claude():
    draft = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "d"}}]}]
    client = _client(SequencedProvider([("x",)]),
                     fallback=lambda: {"id": "b", "kind": "claude"})
    routes = client._usable_routes(draft, _conn())
    assert [c["id"] for c, _n in routes] == ["a"]


@pytest.mark.parametrize("status", [400, 404, 413, 415, 422])
async def test_a_refused_image_is_retried_as_text_on_the_same_connection(status):
    """404 included: OpenRouter answers "No endpoints found that support image
    input" when no endpoint it may route to takes images."""
    provider = SequencedProvider([LLMError("bad_response", "no images", status=status), ("ok",)])
    usage: dict = {}
    assert await _run(_client(provider), _prepared(), _conn(), usage) == "ok"
    first, second = provider.requests
    assert _image_roles(first["messages"])
    assert second["messages"] == cp.as_text(_msgs())
    assert usage["images"] == 0


async def test_a_lone_primary_whose_degrade_also_fails_reports_the_degrade():
    provider = SequencedProvider([LLMError("bad_response", "no images", status=422),
                                  LLMError("rate_limit", "slow down", 7.0, status=429)])
    with pytest.raises(LLMError) as err:
        await _run(_client(provider), _prepared(), _conn())
    assert err.value.kind == "rate_limit" and err.value.retry_after == 7.0
    assert "fallback failed too" not in err.value.detail


async def test_the_combined_error_takes_the_primary_connections_last_word():
    provider = SequencedProvider([LLMError("bad_response", "no images", status=422),
                                  LLMError("rate_limit", "slow down", 7.0, status=429),
                                  LLMError("network", "backup down")])
    client = _client(provider, fallback=lambda: _conn("b", vision="off", model="backup"))
    with pytest.raises(LLMError) as err:
        await _run(client, _prepared(), _conn())
    assert err.value.kind == "rate_limit" and err.value.retry_after == 7.0
    assert "fallback failed too" in err.value.detail
    assert [r["model"] for r in provider.requests] == ["m", "m", "backup"]


async def test_no_degrade_after_a_rate_limit_and_the_primary_kind_survives():
    provider = SequencedProvider([LLMError("rate_limit", "slow down", 3.0, status=429),
                                  LLMError("network", "backup down")])
    client = _client(provider, fallback=lambda: _conn("b", model="backup"))
    with pytest.raises(LLMError) as err:
        await _run(client, _prepared(), _conn())
    assert err.value.kind == "rate_limit" and err.value.retry_after == 3.0
    assert [r["model"] for r in provider.requests] == ["m", "backup"]


@pytest.mark.parametrize("error", [
    LLMError("bad_response", "boom", status=500),
    LLMError("rate_limit", "slow", status=429),
    LLMError("bad_response", "bad stream"),
])
async def test_only_a_rejection_degrades(error):
    provider = SequencedProvider([error, ("unreachable",)])
    with pytest.raises(LLMError):
        await _run(_client(provider), _prepared(), _conn())
    assert len(provider.requests) == 1


async def test_a_rejection_of_a_request_that_sent_no_image_does_not_degrade():
    provider = SequencedProvider([LLMError("bad_response", "nope", status=422), ("x",)])
    with pytest.raises(LLMError):
        await _run(_client(provider), _prepared(), _conn(vision="off"))
    assert len(provider.requests) == 1


async def test_a_fallback_that_refuses_an_image_degrades_too():
    provider = SequencedProvider([LLMError("network", "down"),
                                  LLMError("bad_response", "no images", status=400),
                                  ("ok",)])
    client = _client(provider, fallback=lambda: _conn("b", model="backup"))
    assert await _run(client, _prepared(), _conn()) == "ok"
    assert [r["model"] for r in provider.requests] == ["m", "backup", "backup"]
    assert provider.requests[2]["messages"] == cp.as_text(_msgs())


async def test_a_fallback_that_kept_an_image_the_primary_packed_away_degrades():
    """A fallback's variant is packed for its own window, so it can carry a
    picture the primary's could not; its refusal still gets a text retry."""
    text = cp.as_text(_msgs())
    profiles = {"": (text, None), "wide": (_msgs(), None)}
    prepared = PreparedMessages(
        "m", lambda model: profiles["wide" if model == "backup" else ""],
        profiles=profiles, campaign="c")
    provider = SequencedProvider([LLMError("network", "down"),
                                  LLMError("bad_response", "no images", status=400),
                                  ("ok",)])
    client = _client(provider, fallback=lambda: _conn("b", model="backup"))
    assert await _run(client, prepared, _conn()) == "ok"
    assert [r["model"] for r in provider.requests] == ["m", "backup", "backup"]
    assert _image_roles(provider.requests[1]["messages"]) == ["user", "user"]
    assert provider.requests[2]["messages"] == text


async def test_plain_prompts_skip_the_thread_hop(monkeypatch):
    async def no_thread(*_a, **_k):
        raise AssertionError("a plain prompt must not pay for lowering")
    monkeypatch.setattr(llm.asyncio, "to_thread", no_thread)
    provider = SequencedProvider([("ok",)])
    assert await _run(_client(provider), [{"role": "user", "content": "hi"}], _conn()) == "ok"


async def test_closing_the_facade_stream_closes_the_provider():
    provider = SequencedProvider([("a", "b", "c")])
    agen = _client(provider).stream(_prepared(), _conn())
    assert await agen.__anext__() == "a"
    await agen.aclose()
    assert provider.closed == 1


async def test_strict_folding_of_a_carrier_keeps_alternation():
    """Review Focus 1: narrator art last, a strict OpenAI-compatible route."""
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                                        "data: [DONE]\n\n")

    oc = OpenAICompatibleClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    client = LLMClient(openrouter=ScriptedProvider(), claude=ScriptedProvider(),
                       openai_compatible=oc, retries=0, images=_images, load_image=_load)
    conn = {"id": "s", "kind": "openai_compatible", "model": "m", "base_url": "https://x/v1",
            "post_process": "strict", "vision": "on"}
    assert await _run(client, _prepared(), conn) == "ok"
    roles = [m["role"] for m in bodies[0]["messages"]]
    assert roles == ["user", "assistant", "user"]
    last = bodies[0]["messages"][-1]["content"]
    assert any(p.get("type") == "image_url" for p in last)
    assert cp.text_of(last).endswith("POST")
