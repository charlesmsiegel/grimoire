"""The confirm-first model test call (spec 6.4, slice B Task 9).

The standing rule is "always ask before spending money", so the claims this
suite holds are about what is NOT sent as much as what is:

* the preview sends nothing, meters nothing and starts no run;
* the test refuses without `confirm: true`, before anything is sent or metered;
* a capability the provider's preset rules out is refused before sending;
* a confirmed test sends each probe ONCE -- no retry, no fallback (Review
  Focus 3), proven on the wire with a real `LLMClient` over `MockTransport`;
* a probe that completes without text still counts as accepted;
* only the provider refusing the probe itself is recorded as a `no` -- not an
  outage, an empty wallet, a rate limit, or a refusal of the probe's own cap;
* a failure that answers for every probe (a refused key, an outage, an
  unreachable host) stops the rest from being sent;
* the verdicts are recorded for the connection's `rev`, so an edit hides them.

Every connection, key and model name below is invented.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import zlib

import httpx
import pytest

import grimoire.store as store
from grimoire import catalog, embeddings, routes
from grimoire.anthropic import AnthropicClient
from grimoire.llm import LLMClient
from grimoire.llm_errors import LLMError
from grimoire.openai_compatible import OpenAICompatibleClient
from grimoire.routes import config as config_routes
from grimoire.store.inference import facts, probes
from grimoire.store.inference import resolve as inference
from tests.llm_fakes import FailingOpenRouter, FakeLLM, FakeOpenRouter

REFUSAL = ("This test sends a request to the provider and may cost money — "
           "confirm to run it.")
MODEL = "vendor/model-a"


def _connection(client, **fields) -> str:
    body = {"kind": "openrouter", "name": "Realm Router", "api_key": "sk-or-fake-0001",
            "model": MODEL, **fields}
    r = client.post("/api/llm-connections", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _rev(conn_id: str) -> str:
    return store.llm_connections.read_connection_raw(conn_id)["rev"]


def _rows() -> list[dict]:
    return list(store.usage.calls(days=1))


def _wait(client, run_id: str, state: str = "landed", timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["state"] == state:
            return run
        if run["state"] not in ("running", "pending"):
            raise AssertionError(f"run ended {run['state']}: {run}")
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} never reached {state}")


def _run(client, conn_id: str, caps, **extra) -> dict:
    r = client.post(f"/api/llm-connections/{conn_id}/test",
                    json={"model": MODEL, "capabilities": list(caps), "confirm": True, **extra})
    assert r.status_code == 202, r.text
    run = r.json()["run"]
    assert (run["cls"], run["kind"]) == ("draft", "model-test")
    return _wait(client, run["id"])


def _use(client, fake) -> None:
    client.app.dependency_overrides[routes.get_llm] = lambda: fake


def _embedder(monkeypatch, handler) -> list[httpx.Request]:
    """The route's embeddings client, over a `MockTransport` that records."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    fake = embeddings.EmbeddingsClient(httpx.Client(transport=httpx.MockTransport(record)))
    monkeypatch.setattr(config_routes, "_EMBEDDINGS", fake)
    return seen


def _vector(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]})


# ---- the probes themselves ---------------------------------------------------

def test_the_probe_payloads_are_the_specified_ones():
    assert probes.MAX_TOKENS == 64
    assert probes.messages("generate") == [
        {"role": "user", "content": "Reply with the single word: ok"}]
    vision = probes.messages("vision")[0]["content"]
    assert vision[0] == {"type": "text", "text": "What colour is this image? One word."}
    assert vision[1]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(probes.PROBE_PNG).decode("ascii"))
    assert probes.sampling()["params"] == {"max_tokens": 64}


def test_the_vision_probe_is_a_real_small_solid_png():
    png = probes.PROBE_PNG
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    # IHDR is the first chunk: width and height, big-endian, both 64 -- big
    # enough not to be refused as too small, small enough to be one tile.
    assert png[16:24] == (64).to_bytes(4, "big") + (64).to_bytes(4, "big")
    assert png.endswith(b"IEND\xaeB`\x82")
    # One colour throughout: every row is filter 0 then the same red pixels.
    idat_len = int.from_bytes(png[33:37], "big")
    assert png[37:41] == b"IDAT"
    pixels = zlib.decompress(png[41:41 + idat_len])
    assert pixels == (b"\x00" + b"\xff\x00\x00" * 64) * 64


def test_an_estimate_needs_every_price_it_uses():
    row = catalog.entry({"id": MODEL, "pricing": {"prompt": "0.000001", "completion": "0.000002"}})
    expected = (probes.PROBES["generate"].prompt_tokens * 0.000001
                + probes.PROBES["generate"].completion_tokens * 0.000002)
    assert probes.estimate_usd(row, ["generate"]) == pytest.approx(expected)
    # A price nobody reported is never rendered as zero.
    assert probes.estimate_usd(catalog.entry({"id": MODEL}), ["generate"]) is None
    assert probes.estimate_usd(None, ["generate"]) is None
    assert probes.estimate_usd({"prompt": "-1", "completion": "0"}, ["generate"]) is None
    # A model the provider says is free is a reported zero, not an unknown one.
    assert probes.estimate_usd({"prompt": "0", "completion": "0"}, ["generate"]) == 0.0


# ---- preview -----------------------------------------------------------------

def test_the_preview_sends_nothing_meters_nothing_and_starts_no_run(client):
    fake = FakeOpenRouter(["ok"])
    _use(client, fake)
    conn = _connection(client)

    r = client.post(f"/api/llm-connections/{conn}/test/preview",
                    json={"model": MODEL, "capabilities": ["vision", "generate"]})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "Realm Router"
    assert body["model"] == MODEL
    assert [s["capability"] for s in body["sends"]] == ["generate", "vision"]
    assert all(s["description"] for s in body["sends"])
    assert "64" in body["sends"][0]["description"]
    assert body["estimated_cost_usd"] is None   # no catalog: unknown, never 0
    assert fake.calls == 0
    assert _rows() == []
    assert client.get("/api/runs").json()["runs"] == []


def test_the_preview_prices_from_the_cached_catalog_row(client):
    _use(client, FakeOpenRouter(["ok"]))
    conn = _connection(client)
    row = catalog.entry({"id": MODEL, "pricing": {"prompt": "0.000003", "completion": "0.000015"}})
    store.llm_connections.set_cached_models(conn, [row], _rev(conn))

    body = client.post(f"/api/llm-connections/{conn}/test/preview",
                       json={"model": MODEL, "capabilities": ["generate"]}).json()

    assert body["estimated_cost_usd"] == pytest.approx(probes.estimate_usd(row, ["generate"]))
    assert body["estimated_cost_usd"] > 0


# ---- refusals: nothing sent, nothing metered ---------------------------------

@pytest.mark.parametrize("confirm", [None, False, "true", 1])
def test_without_confirm_the_test_is_refused_and_nothing_is_sent(client, confirm):
    fake = FakeOpenRouter(["ok"])
    _use(client, fake)
    conn = _connection(client)
    body = {"model": MODEL, "capabilities": ["generate"]}
    if confirm is not None:
        body["confirm"] = confirm

    r = client.post(f"/api/llm-connections/{conn}/test", json=body)

    assert r.status_code == 400
    assert r.json()["detail"] == REFUSAL
    assert fake.calls == 0
    assert _rows() == []
    assert client.get("/api/runs").json()["runs"] == []


@pytest.mark.parametrize("path", ["test", "test/preview"])
def test_a_capability_the_preset_rules_out_is_refused_before_sending(client, path):
    fake = FakeOpenRouter(["ok"])
    _use(client, fake)
    conn = _connection(client, kind="anthropic", name="Saltmarch Direct",
                       api_key="sk-ant-fake-0001", model="claude-model-x")

    r = client.post(f"/api/llm-connections/{conn}/{path}",
                    json={"model": "claude-model-x", "capabilities": ["generate", "embed"],
                          "confirm": True})

    assert r.status_code == 400
    assert "embed" in r.json()["detail"]
    assert fake.calls == 0
    assert _rows() == []


@pytest.mark.parametrize("caps", [[], ["stream"], ["telepathy"]])
def test_a_capability_with_no_probe_is_refused(client, caps):
    fake = FakeOpenRouter(["ok"])
    _use(client, fake)
    conn = _connection(client)

    r = client.post(f"/api/llm-connections/{conn}/test",
                    json={"model": MODEL, "capabilities": caps, "confirm": True})

    assert r.status_code == 400
    assert fake.calls == 0


def test_an_unknown_connection_is_a_404(client):
    r = client.post("/api/llm-connections/nobody/test/preview",
                    json={"model": MODEL, "capabilities": ["generate"]})
    assert r.status_code == 404


def test_a_connection_that_cannot_send_is_refused_before_sending(client):
    fake = FakeOpenRouter(["ok"])
    _use(client, fake)
    conn = _connection(client, api_key="")

    r = client.post(f"/api/llm-connections/{conn}/test",
                    json={"model": MODEL, "capabilities": ["generate"], "confirm": True})

    assert r.status_code == 409
    assert r.json()["kind"] == "missing_key"
    assert fake.calls == 0


# ---- a confirmed test ----------------------------------------------------------

def test_a_confirmed_test_meters_one_row_per_probe_and_records_the_verdicts(
        client, monkeypatch):
    fake = FakeOpenRouter(["ok"], usage={"prompt_tokens": 12, "completion_tokens": 1})
    _use(client, fake)
    conn = _connection(client)
    seen = _embedder(monkeypatch, _vector)
    rev = _rev(conn)

    run = _run(client, conn, ["embed", "generate", "vision"])

    result = run["result"]
    assert result["model"] == MODEL
    assert result["recorded"] is True
    assert {c: r["ok"] for c, r in result["results"].items()} == {
        "generate": True, "vision": True, "embed": True}
    # Each chat probe was sent once, on the probe's own connection: the model
    # under test and the 64-token cap, nothing else from a preset.
    assert fake.calls == 2
    for request in fake.requests:
        assert request["conn"]["model"] == MODEL
        assert request["conn"]["sampling"]["params"] == {"max_tokens": 64}
    assert fake.requests[0]["messages"] == probes.messages("generate")
    # The embed probe went to the endpoint the Embedding path resolves for an
    # OpenRouter provider, with the model under test and the provider's key.
    assert len(seen) == 1
    assert str(seen[0].url) == "https://openrouter.ai/api/v1/embeddings"
    assert seen[0].headers["Authorization"] == "Bearer sk-or-fake-0001"
    assert json.loads(seen[0].content)["model"] == MODEL

    rows = _rows()
    assert sorted(r["task"] for r in rows) == ["model-test"] * 3
    assert all(not r.get("campaign") for r in rows)
    # Ruling 14: the embed probe's row carries no token counts (slice D meters
    # embeddings); the two chat probes' rows carry what the provider reported.
    uncounted = [r for r in rows
                 if r.get("prompt_tokens") is None and r.get("completion_tokens") is None]
    assert len(uncounted) == 1
    assert uncounted[0]["model"] == MODEL
    assert uncounted[0]["provider"] == "openrouter"

    verified = facts.of(conn, MODEL, rev)["verified"]
    assert {c: r["ok"] for c, r in verified.items()} == {
        "generate": True, "vision": True, "embed": True}
    assert all(r.get("at") for r in verified.values())
    assert verified["embed"]["dims"] == 3


def test_an_empty_completion_still_counts_as_accepted(client):
    """A thinking model can spend a 64-token cap thinking and say nothing."""
    _use(client, FakeLLM([[]]))
    conn = _connection(client)

    run = _run(client, conn, ["generate"])

    assert run["result"]["results"]["generate"] == {"ok": True}
    assert facts.of(conn, MODEL, _rev(conn))["verified"]["generate"]["ok"] is True


def test_a_provider_refusal_is_recorded_with_its_message_scrubbed(client):
    message = ("image refused: data:image/png;base64,iVBORw0KGgoAAAA "
               "(key sk-or-fake-0001)")
    _use(client, FakeLLM([[]], error=LLMError("bad_response", message, status=400)))
    conn = _connection(client)

    run = _run(client, conn, ["vision"])

    got = run["result"]["results"]["vision"]
    assert got["ok"] is False
    assert "[elided]" in got["error"]
    assert "iVBORw0KGgo" not in got["error"]
    assert "sk-or-fake-0001" not in got["error"]
    stored = facts.of(conn, MODEL, _rev(conn))["verified"]["vision"]
    assert stored["ok"] is False
    assert stored["error"] == got["error"]


def test_a_failure_that_says_nothing_about_the_model_is_reported_not_recorded(client):
    """A rate limit is the provider's state, not the model's ability: storing
    it as a `test` no would hide a working model from every picker."""
    _use(client, FailingOpenRouter(kind="rate_limit", message="slow down"))
    conn = _connection(client)

    run = _run(client, conn, ["generate"])

    assert run["result"]["results"]["generate"]["ok"] is False
    assert run["result"]["results"]["generate"]["kind"] == "rate_limit"
    assert run["result"]["recorded"] is False
    assert facts.of(conn, MODEL, _rev(conn))["verified"] == {}


def test_a_key_edit_hides_the_results(client):
    _use(client, FakeOpenRouter(["ok"]))
    conn = _connection(client)
    before = _rev(conn)
    _run(client, conn, ["generate"])
    assert facts.of(conn, MODEL, before)["verified"]["generate"]["ok"] is True

    r = client.put(f"/api/llm-connections/{conn}", json={"api_key": "sk-or-fake-0002"})
    assert r.status_code == 200

    after = _rev(conn)
    assert after != before
    assert facts.of(conn, MODEL, after)["verified"] == {}


# ---- on the wire: one attempt, no fallback (Review Focus 3) -------------------

def _sse(*chunks: dict) -> str:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _wire_client(client, handler, **llm) -> list[httpx.Request]:
    """A REAL facade, with retries and a fallback configured, whose
    OpenAI-compatible adapter talks to a `MockTransport`."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    adapter = OpenAICompatibleClient(http=httpx.AsyncClient(transport=httpx.MockTransport(record)))
    facade = LLMClient(openai_compatible=adapter, **llm)
    client.app.dependency_overrides[routes.get_llm] = lambda: facade
    return seen


def _endpoint(client, name: str, host: str) -> str:
    return _connection(client, kind="openai_compatible", name=name,
                       base_url=f"https://{host}/v1", api_key="sk-fake-endpoint",
                       model=MODEL)


def test_a_rate_limit_is_not_retried_and_the_fallback_is_never_called(client, monkeypatch):
    from grimoire import llm as llm_mod
    monkeypatch.setattr(llm_mod, "RETRY_BASE", 0.0)
    conn = _endpoint(client, "Mara Endpoint", "primary.example")
    backup = store.llm_connections.read_connection_raw(
        _endpoint(client, "Winifred Endpoint", "backup.example"))
    seen = _wire_client(
        client, lambda _r: httpx.Response(429, json={"error": {"message": "slow down"}}),
        retries=3, fallback=lambda: backup)

    run = _run(client, conn, ["generate"])

    assert [r.url.host for r in seen] == ["primary.example"]
    got = run["result"]["results"]["generate"]
    assert got["ok"] is False
    assert got["kind"] == "rate_limit"
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["task"] == "model-test"
    assert rows[0].get("attempts", 1) == 1   # the ledger omits the default

    # The control: the same facade, sent the same probe through `stream`,
    # DOES retry and fall back -- so the one request above is the test call's
    # doing, not this setup's.
    seen.clear()
    facade = client.app.dependency_overrides[routes.get_llm]()
    probe_conn = inference.lower(store.llm_connections.read_connection_raw(conn),
                                 probes.sampling(), MODEL)
    with pytest.raises(LLMError):
        asyncio.run(facade.complete(probes.messages("generate"), probe_conn))
    assert [r.url.host for r in seen] == ["primary.example"] * 4 + ["backup.example"]


# ---- only what the MODEL answered is recorded --------------------------------

def _refusing(status: int, message: str):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": message}})
    return handler


def _answer(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=_sse({"choices": [{"delta": {"content": "ok"}}]}),
                          headers={"content-type": "text/event-stream"})


@pytest.mark.parametrize("status,message", [
    (503, "upstream unavailable"),
    (402, "insufficient credits"),
])
def test_an_outage_or_empty_wallet_is_reported_and_not_recorded(client, status, message):
    """The adapters map both to `bad_response`, which is also what a refusal
    is -- so the kind alone would have filed a `test` no that outranks the
    catalog for a model nobody actually asked."""
    conn = _endpoint(client, "Mara Endpoint", "primary.example")
    seen = _wire_client(client, _refusing(status, message))

    run = _run(client, conn, ["generate"])

    assert len(seen) == 1
    got = run["result"]["results"]["generate"]
    assert got["ok"] is False
    assert got["kind"] == "bad_response"
    assert message in got["error"]
    assert run["result"]["recorded"] is False
    assert facts.of(conn, MODEL, _rev(conn))["verified"] == {}


def test_a_400_refusing_the_image_is_recorded_and_shown_unverified(client):
    """The provider refused THIS request -- the model's own answer, recorded
    with its error. Spec 12: the row stays unverified with that error shown,
    never a `no` the seam could refuse on."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "image_url" in request.content.decode():
            return _refusing(400, "this model does not support image input")(request)
        return _answer(request)

    conn = _endpoint(client, "Mara Endpoint", "primary.example")
    seen = _wire_client(client, handler, retries=3)

    run = _run(client, conn, ["generate", "vision"])

    assert len(seen) == 2   # no retry, and no text-only re-send of the picture
    results = run["result"]["results"]
    assert results["generate"] == {"ok": True}
    assert results["vision"]["ok"] is False
    assert run["result"]["recorded"] is True
    verified = facts.of(conn, MODEL, _rev(conn))["verified"]
    assert verified["generate"]["ok"] is True
    assert verified["vision"]["ok"] is False
    assert "image input" in verified["vision"]["error"]
    vision = client.get(f"/api/llm-connections/{conn}/capabilities",
                        params={"need": "vision", "model": MODEL}).json()
    assert vision["hidden"] == []
    row = vision["groups"]["unverified"][0]
    assert row["capabilities"]["vision"]["value"] == "unknown"
    assert row["capabilities"]["vision"]["source"] == "test"
    assert "image input" in row["capabilities"]["vision"]["error"]
    assert row["reason"].startswith("a test call failed: ")


def test_the_verdicts_are_filed_off_the_event_loop(client, monkeypatch):
    """Filing reads the connection and writes the facts file: blocking work,
    kept off the loop every other run streams through."""
    fake = FakeOpenRouter(["ok"])
    _use(client, fake)
    conn = _connection(client)
    where: list[bool] = []
    real = config_routes._record_verdicts

    def record(*args):
        try:
            asyncio.get_running_loop()
            where.append(True)
        except RuntimeError:
            where.append(False)
        return real(*args)

    monkeypatch.setattr(config_routes, "_record_verdicts", record)
    run = _run(client, conn, ["generate"])
    assert run["result"]["recorded"] is True
    assert where == [False]


def test_a_400_naming_the_probes_own_cap_is_not_recorded(client):
    """A refusal of `max_tokens` is a refusal of the setting the PROBE sent,
    not of the capability; it is reported as the test's setting refused."""
    conn = _endpoint(client, "Mara Endpoint", "primary.example")
    seen = _wire_client(client, _refusing(400, "Unsupported parameter: 'max_tokens'"))

    run = _run(client, conn, ["generate", "vision"])

    # Not a failure that answers for the next probe: both were sent.
    assert len(seen) == 2
    for cap in ("generate", "vision"):
        got = run["result"]["results"][cap]
        assert got["ok"] is False
        assert "max_tokens" in got["error"] and "model test" in got["error"]
    assert run["result"]["recorded"] is False
    assert facts.of(conn, MODEL, _rev(conn))["verified"] == {}


def _unreachable(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


@pytest.mark.parametrize("handler,kind", [
    (_refusing(401, "invalid key"), "auth"),
    (_refusing(402, "insufficient credits"), "bad_response"),
    (_refusing(502, "bad gateway"), "bad_response"),
    (_unreachable, "network"),
])
def test_a_failure_that_answers_for_every_probe_stops_the_rest(
        client, monkeypatch, handler, kind):
    conn = _endpoint(client, "Mara Endpoint", "primary.example")
    seen = _wire_client(client, handler)
    embedded = _embedder(monkeypatch, _vector)

    run = _run(client, conn, ["generate", "vision", "embed"])

    assert len(seen) == 1
    assert embedded == []
    results = run["result"]["results"]
    first = results["generate"]
    assert (first["ok"], first["kind"]) == (False, kind)
    for cap in ("vision", "embed"):
        assert results[cap]["ok"] is False
        assert results[cap]["kind"] == "not_sent"
        assert results[cap]["error"].startswith("not sent: ")
        assert first["error"] in results[cap]["error"]
    assert run["result"]["recorded"] is False
    assert facts.of(conn, MODEL, _rev(conn))["verified"] == {}
    # Only the probe that went out was metered.
    assert len(_rows()) == 1


def test_an_empty_stream_on_the_wire_counts_as_accepted(client):
    conn = _endpoint(client, "Mara Endpoint", "primary.example")
    seen = _wire_client(
        client,
        lambda _r: httpx.Response(200, text=_sse({"choices": [{"delta": {},
                                                                "finish_reason": "length"}]}),
                                  headers={"content-type": "text/event-stream"}),
        retries=3)

    run = _run(client, conn, ["generate"])

    assert len(seen) == 1
    sent = json.loads(seen[0].content)
    assert sent["max_tokens"] == 64
    assert sent["model"] == MODEL
    assert sent["messages"] == probes.messages("generate")
    assert run["result"]["results"]["generate"] == {"ok": True}


def test_a_truncated_anthropic_stream_is_a_failed_probe_not_an_accepted_one(client):
    """A 200 whose stream ends before `message_stop` did not complete, so the
    probe did not succeed: it is reported as a failure, and -- a malformed
    stream being no verdict on the model -- nothing is filed."""
    conn = _connection(client, kind="anthropic", name="Saltmarch Direct",
                       api_key="sk-ant-fake-0001", model="claude-model-x")
    seen: list[httpx.Request] = []
    start = {"type": "message_start", "message": {
        "id": "msg_1", "model": "claude-model-x", "role": "assistant", "content": [],
        "usage": {"input_tokens": 10, "output_tokens": 1}}}
    delta = {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "o"}}
    cut = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in (start, delta))

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=cut, headers={"content-type": "text/event-stream"})

    adapter = AnthropicClient(http=httpx.AsyncClient(transport=httpx.MockTransport(record)))
    facade = LLMClient(anthropic=adapter, retries=3)
    client.app.dependency_overrides[routes.get_llm] = lambda: facade

    r = client.post(f"/api/llm-connections/{conn}/test",
                    json={"model": "claude-model-x", "capabilities": ["generate"],
                          "confirm": True})
    assert r.status_code == 202, r.text
    run = _wait(client, r.json()["run"]["id"])

    assert len(seen) == 1
    got = run["result"]["results"]["generate"]
    assert got["ok"] is False
    assert got["kind"] == "network"
    assert run["result"]["recorded"] is False
    assert facts.read(conn) == {}


def test_the_rev_moving_during_the_run_records_nothing(client):
    """An edit that lands while the probe is out describes a different
    endpoint; writing the old rev's verdict would replace whatever the new
    rev already holds."""
    conn = _endpoint(client, "Mara Endpoint", "primary.example")
    before = _rev(conn)

    def edit_then_answer(_request: httpx.Request) -> httpx.Response:
        store.llm_connections.update_connection(conn, api_key="sk-fake-rotated")
        return httpx.Response(200, text=_sse({"choices": [{"delta": {"content": "ok"}}]}),
                              headers={"content-type": "text/event-stream"})

    _wire_client(client, edit_then_answer)

    run = _run(client, conn, ["generate"])

    assert run["result"]["results"]["generate"] == {"ok": True}
    assert run["result"]["recorded"] is False
    assert _rev(conn) != before
    assert facts.read(conn) == {}


def test_an_embed_failure_is_one_metered_row_with_no_token_counts(client, monkeypatch):
    _use(client, FakeOpenRouter(["ok"]))
    conn = _connection(client)
    seen = _embedder(monkeypatch, lambda _r: httpx.Response(
        400, json={"error": {"message": "this model makes no embeddings"}}))

    run = _run(client, conn, ["embed"])

    assert len(seen) == 1
    got = run["result"]["results"]["embed"]
    assert got["ok"] is False
    assert "no embeddings" in got["error"]
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["task"] == "model-test"
    assert rows[0]["status"] == "error"
    assert rows[0].get("prompt_tokens") is None
    # A 400 is the endpoint refusing this request: a verdict, recorded.
    assert facts.of(conn, MODEL, _rev(conn))["verified"]["embed"]["ok"] is False


def test_the_gateway_fake_answers_single_like_complete():
    """`llm_fakes` stands in for the whole facade surface routes use."""
    import asyncio
    fake = FakeLLM([["o", "k"]], error=None)
    usage: dict = {}
    assert asyncio.run(fake.single([], {"kind": "openrouter", "model": "m"}, usage)) == "ok"
    assert usage["attempts"] == 1
    failing = FakeLLM([["x"]], error=LLMError("auth", "no"))
    with pytest.raises(LLMError):
        asyncio.run(failing.single([], {"kind": "openrouter", "model": "m"}))
