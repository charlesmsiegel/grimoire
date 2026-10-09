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
import threading
import time
import zlib

import httpx
import pytest

import grimoire.store as store
from grimoire import catalog, decisions, embeddings, routes, wire
from grimoire.anthropic import AnthropicClient
from grimoire.llm import LLMClient
from grimoire.llm_errors import LLMError
from grimoire.openai_compatible import OpenAICompatibleClient
from grimoire.routes import config as config_routes
from grimoire.store.inference import facts, probes, resolve
from tests import wire_kit
from tests.llm_fakes import FailingOpenRouter, FakeLLM, FakeOpenRouter, carrying

REFUSAL = ("This test sends a request to the provider and may cost money — "
           "confirm to run it.")
MODEL = "vendor/model-a"


def _connection(client, **fields) -> str:
    body = {"kind": "openrouter", "name": "Realm Router", "api_key": "sk-or-fake-0001",
            **fields}
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


def _tokens(cap, prompt, completion):
    probe = probes.PROBES[cap]
    return probe.prompt_tokens * prompt + probe.completion_tokens * completion


def test_the_vision_probe_adds_one_image_at_the_rows_image_price():
    row = catalog.entry({"id": MODEL, "pricing": {"prompt": "0.000001", "completion": "0.000002",
                                                  "image": "0.0025"}})
    assert row["image"] == "0.0025"
    vision = _tokens("vision", 0.000001, 0.000002) + 0.0025
    assert probes.estimate_usd(row, ["vision"]) == pytest.approx(vision)
    both = vision + _tokens("generate", 0.000001, 0.000002)
    assert probes.estimate_usd(row, ["generate", "vision"]) == pytest.approx(both)


def test_a_free_image_is_a_reported_zero():
    row = catalog.entry({"id": MODEL, "pricing": {"prompt": "0", "completion": "0",
                                                  "image": "0"}})
    assert probes.estimate_usd(row, ["vision"]) == 0.0


@pytest.mark.parametrize("image", [None, "", "free", "-1", "nan", "inf", True, [], {}])
def test_an_unreported_image_price_makes_the_vision_probe_unknown(image):
    pricing = {"prompt": "0.000001", "completion": "0.000002"}
    if image is not None:
        pricing["image"] = image
    row = catalog.entry({"id": MODEL, "pricing": pricing})
    assert probes.estimate_usd(row, ["vision"]) is None
    # ... and so the whole test's total, never the text probes' share alone.
    assert probes.estimate_usd(row, ["generate", "vision"]) is None


def test_a_text_only_test_ignores_the_image_price():
    priced = catalog.entry({"id": MODEL, "pricing": {"prompt": "0.000001",
                                                     "completion": "0.000002",
                                                     "image": "0.0025"}})
    unpriced = catalog.entry({"id": MODEL, "pricing": {"prompt": "0.000001",
                                                       "completion": "0.000002"}})
    expected = _tokens("generate", 0.000001, 0.000002)
    for row in (priced, unpriced):
        assert probes.estimate_usd(row, ["generate"]) == pytest.approx(expected)
        assert probes.estimate_usd(row, ["embed"]) == pytest.approx(
            probes.PROBES["embed"].prompt_tokens * 0.000001)


def test_the_preview_prices_the_vision_probes_image(client):
    _use(client, FakeOpenRouter(["ok"]))
    conn = _connection(client)
    priced = catalog.entry({"id": MODEL, "pricing": {"prompt": "0.000003",
                                                     "completion": "0.000015",
                                                     "image": "0.004"}})
    store.llm_connections.set_cached_models(conn, [priced], _rev(conn))
    body = client.post(f"/api/llm-connections/{conn}/test/preview",
                       json={"model": MODEL, "capabilities": ["generate", "vision"]}).json()
    expected = (_tokens("generate", 0.000003, 0.000015)
                + _tokens("vision", 0.000003, 0.000015) + 0.004)
    assert body["estimated_cost_usd"] == pytest.approx(expected)

    unpriced = catalog.entry({"id": MODEL, "pricing": {"prompt": "0.000003",
                                                       "completion": "0.000015"}})
    store.llm_connections.set_cached_models(conn, [unpriced], _rev(conn))
    body = client.post(f"/api/llm-connections/{conn}/test/preview",
                       json={"model": MODEL, "capabilities": ["generate", "vision"]}).json()
    assert body["estimated_cost_usd"] is None


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


# ---- the user's rates ----------------------------------------------------------

RATES = {"prompt_usd_per_1k": 0.001, "completion_usd_per_1k": 0.002}


def _preview(client, conn, caps=("generate",)) -> dict:
    r = client.post(f"/api/llm-connections/{conn}/test/preview",
                    json={"model": MODEL, "capabilities": list(caps)})
    assert r.status_code == 200, r.text
    assert _rows() == []
    assert client.get("/api/runs").json()["runs"] == []
    return r.json()


def _at_rates(caps, rates=RATES) -> float:
    return sum(probes.PROBES[c].prompt_tokens * rates["prompt_usd_per_1k"] / 1000
               + probes.PROBES[c].completion_tokens * rates["completion_usd_per_1k"] / 1000
               for c in caps)


def _unreported(client) -> str:
    """A provider that does not report its own price, the only kind the
    user's rates may price (an OpenRouter row is the catalog's or unknown)."""
    return _connection(client, kind="openai_compatible", name="Saltmarch Local",
                       base_url="https://saltmarch.example/v1", api_key="sk-fake-local")


def test_estimate_from_rates_sums_the_probes_and_prices_images_as_tokens():
    entry = dict(RATES)
    assert probes.estimate_from_rates(entry, ["generate", "vision"]) == pytest.approx(
        _at_rates(["generate", "vision"]))
    # An embed probe has no completion; the counts are still both stated.
    assert probes.estimate_from_rates(entry, ["embed"]) == pytest.approx(_at_rates(["embed"]))
    # A free model is a reported zero.
    free = {"prompt_usd_per_1k": 0.0, "completion_usd_per_1k": 0.0}
    assert probes.estimate_from_rates(free, ["generate"]) == 0.0


def test_estimate_from_rates_is_none_without_an_entry():
    assert probes.estimate_from_rates(None, ["generate"]) is None
    assert probes.estimate_from_rates({}, ["generate"]) is None
    assert probes.estimate_from_rates({"prompt_usd_per_1k": 1.0}, ["generate"]) is None


def test_preview_prices_from_model_rates_when_the_catalog_states_none(client):
    fake = FakeOpenRouter(["ok"])
    _use(client, fake)
    conn = _unreported(client)
    facts.state(conn, MODEL, rates=RATES)
    body = _preview(client, conn, ["generate", "vision"])
    assert body["estimated_cost_usd"] == pytest.approx(_at_rates(["generate", "vision"]))
    assert body["estimate_basis"] == "rates"
    assert fake.calls == 0


def test_a_catalog_row_without_an_image_price_falls_through_to_rates(client):
    fake = FakeOpenRouter(["ok"])
    _use(client, fake)
    conn = _unreported(client)
    row = catalog.entry({"id": MODEL, "pricing": {"prompt": "0.000003", "completion": "0.000015"}})
    store.llm_connections.set_cached_models(conn, [row], _rev(conn))
    facts.state(conn, MODEL, rates=RATES)
    body = _preview(client, conn, ["generate", "vision"])
    assert body["estimated_cost_usd"] == pytest.approx(_at_rates(["generate", "vision"]))
    assert body["estimate_basis"] == "rates"
    assert fake.calls == 0


def test_preview_prices_from_pricing_json_when_the_model_has_no_rates(client):
    fake = FakeOpenRouter(["ok"])
    _use(client, fake)
    conn = _unreported(client)
    store.pricing.write_pricing({MODEL: RATES})
    body = _preview(client, conn)
    assert body["estimated_cost_usd"] == pytest.approx(_at_rates(["generate"]))
    assert body["estimate_basis"] == "rates"
    assert fake.calls == 0


def test_the_models_rates_outrank_pricing_json(client):
    fake = FakeOpenRouter(["ok"])
    _use(client, fake)
    conn = _unreported(client)
    store.pricing.write_pricing({MODEL: {"prompt_usd_per_1k": 9.0, "completion_usd_per_1k": 9.0}})
    facts.state(conn, MODEL, rates=RATES)
    assert _preview(client, conn)["estimated_cost_usd"] == pytest.approx(_at_rates(["generate"]))
    assert fake.calls == 0


def test_a_catalog_price_outranks_rates(client):
    fake = FakeOpenRouter(["ok"])
    _use(client, fake)
    conn = _connection(client)
    row = catalog.entry({"id": MODEL, "pricing": {"prompt": "0.000003", "completion": "0.000015"}})
    store.llm_connections.set_cached_models(conn, [row], _rev(conn))
    facts.state(conn, MODEL, rates=RATES)
    body = _preview(client, conn)
    assert body["estimated_cost_usd"] == pytest.approx(probes.estimate_usd(row, ["generate"]))
    assert body["estimate_basis"] == "catalog"
    assert fake.calls == 0


def test_no_price_anywhere_is_null(client):
    fake = FakeOpenRouter(["ok"])
    _use(client, fake)
    conn = _connection(client)
    body = _preview(client, conn)
    assert body["estimated_cost_usd"] is None
    assert body["estimate_basis"] is None
    assert fake.calls == 0


def test_a_stated_free_catalog_row_is_a_catalog_zero_not_a_fall_through(client):
    fake = FakeOpenRouter(["ok"])
    _use(client, fake)
    conn = _connection(client)
    row = catalog.entry({"id": MODEL, "pricing": {"prompt": "0", "completion": "0"}})
    store.llm_connections.set_cached_models(conn, [row], _rev(conn))
    facts.state(conn, MODEL, rates=RATES)
    body = _preview(client, conn)
    assert body["estimated_cost_usd"] == 0.0
    assert body["estimate_basis"] == "catalog"
    assert fake.calls == 0


def test_a_provider_that_reports_its_price_is_never_priced_from_the_users_rates(client):
    """OpenRouter bills what it says it bills, and the ledger never applies a
    user rate to it -- so with no catalog row the preview is "cost unknown",
    never "≈ $0.00 at your rates" from a zero default meant for local models."""
    fake = FakeOpenRouter(["ok"])
    _use(client, fake)
    conn = _connection(client)
    store.pricing.write_pricing({"": {"prompt_usd_per_1k": 0.0, "completion_usd_per_1k": 0.0}})
    body = _preview(client, conn, ["generate", "vision"])
    assert body["estimated_cost_usd"] is None
    assert body["estimate_basis"] is None
    # Nor from rates stated on the model itself.
    facts.state(conn, MODEL, rates=RATES)
    body = _preview(client, conn, ["generate", "vision"])
    assert body["estimated_cost_usd"] is None
    assert body["estimate_basis"] is None
    # A catalog row that states token prices but no image price stays unknown too.
    row = catalog.entry({"id": MODEL, "pricing": {"prompt": "0.000003", "completion": "0.000015"}})
    store.llm_connections.set_cached_models(conn, [row], _rev(conn))
    body = _preview(client, conn, ["generate", "vision"])
    assert body["estimated_cost_usd"] is None
    assert body["estimate_basis"] is None
    assert fake.calls == 0


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


@pytest.mark.parametrize(("kind", "model"), [("openrouter", MODEL), ("claude", "")])
def test_an_unconfirmed_test_builds_no_target(client, monkeypatch, kind, model):
    """CLAUDE.md's spend rule, held literally: the route refuses the request
    without its yes "before anything is built or sent". The plan's default
    model is the record's (`facts.model_of`, a pure rule), so not even the
    probe's target is built before the refusal."""
    built: list[str] = []
    for name in ("provider_target", "target_for"):
        real = getattr(resolve, name)
        monkeypatch.setattr(resolve, name,
                            lambda *a, _real=real, _name=name, **kw: built.append(_name)
                            or _real(*a, **kw))
    _use(client, FakeOpenRouter(["ok"]))
    # A Claude record names no model: the plan's default is the one it runs.
    conn = _connection(client, kind=kind, name=f"Realm {kind}")
    r = client.post(f"/api/llm-connections/{conn}/test",
                    json={"model": model, "capabilities": ["generate"]})
    assert r.status_code == 400 and r.json()["detail"] == REFUSAL
    assert built == []
    # The control: a confirmed test does build its probe's target there.
    _run(client, conn, ["generate"], model=model)
    assert "target_for" in built


@pytest.mark.parametrize("path", ["test", "test/preview"])
def test_a_capability_the_preset_rules_out_is_refused_before_sending(client, path):
    fake = FakeOpenRouter(["ok"])
    _use(client, fake)
    conn = _connection(client, kind="anthropic", name="Saltmarch Direct",
                       api_key="sk-ant-fake-0001")

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


class _StrictRecorder:
    """An OpenAI-compatible client that answers every probe and records the
    `strict` flag each was sent with -- the one probe field a model's facts
    put on the wire (`post_process`)."""

    def __init__(self) -> None:
        self.strict: list[bool] = []

    async def stream(self, messages, model, key, base_url, strict=False, usage=None, **kw):
        self.strict.append(strict)
        yield "ok"


@pytest.mark.parametrize("stated", ["strict", "none"])
def test_a_probe_is_sent_its_models_post_processing(client, stated):
    """The probe target carries the model's facts (`resolve.facts_for`): a
    model whose facts say strict post-processing is probed strict, as a turn
    on it is sent."""
    assert store.inference_keys.is_current(store.read_config())
    conn = _connection(client, kind="openai_compatible", name="Saltmarch Local",
                       base_url="http://localhost:1234/v1", api_key="sk-fake-local")
    facts.set_stated(conn, MODEL, post_process=stated)
    recorder = _StrictRecorder()
    _use(client, LLMClient(openai_compatible=recorder, retries=0))  # type: ignore[arg-type]
    got = _run(client, conn, ["generate"])
    assert got["result"]["results"]["generate"]["ok"] is True
    assert recorder.strict == [stated == "strict"]


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
        assert request["target"].model == MODEL
        assert request["target"].sampling.params == {"max_tokens": 64}
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
    # The embed probe's endpoint reported no counts (`_vector` reports none),
    # so its prompt is counted locally and says so, and it never gets a
    # completion count (slice E, ruling I1); it says its operation. The two
    # chat probes' rows carry what the provider reported.
    embedded = [r for r in rows if r.get("operation") == "embed"]
    assert len(embedded) == 1
    assert embedded[0]["model"] == MODEL
    assert embedded[0]["provider"] == "openrouter"
    assert embedded[0]["prompt_tokens"] > 0
    assert embedded[0]["tokens_estimated"] is True
    assert "completion_tokens" not in embedded[0]
    assert not any(r.get("tokens_estimated") for r in rows if r is not embedded[0])

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


class _Offering(LLMClient):
    """A REAL facade that hands every chain it is sent a fallback (`offer`,
    as the call's own: `wire.Chain.fallback`), so a route that generates
    through `stream` or `complete` has one within reach -- and only the
    route's choice of `single`, whose one target can carry no fallback (a
    chain is refused), keeps it unused."""

    def __init__(self, offer: wire.Target | None, **llm) -> None:
        super().__init__(**llm)
        self._offer = offer

    def stream(self, messages, chain, usage=None, **kwargs):
        return super().stream(messages, carrying(chain, self._offer), usage, **kwargs)

    async def complete(self, messages, chain, usage=None, **kwargs):
        return await super().complete(messages, carrying(chain, self._offer), usage, **kwargs)


def _wire_client(client, handler, *, offer: wire.Target | None = None,
                 **llm) -> list[httpx.Request]:
    """A REAL facade, with retries configured and -- given `offer` -- a
    fallback handed every call, whose OpenAI-compatible adapter talks to a
    `MockTransport`."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    adapter = OpenAICompatibleClient(http=httpx.AsyncClient(transport=httpx.MockTransport(record)))
    facade = _Offering(offer, openai_compatible=adapter, **llm)
    client.app.dependency_overrides[routes.get_llm] = lambda: facade
    return seen


def _endpoint(client, name: str, host: str) -> str:
    return _connection(client, kind="openai_compatible", name=name,
                       base_url=f"https://{host}/v1", api_key="sk-fake-endpoint")


def test_a_rate_limit_is_not_retried_and_the_fallback_is_never_called(client, monkeypatch):
    from grimoire import llm as llm_mod
    monkeypatch.setattr(llm_mod, "RETRY_BASE", 0.0)
    conn = _endpoint(client, "Mara Endpoint", "primary.example")
    backup = config_routes._probe_target(store.llm_connections.read_connection_raw(
        _endpoint(client, "Winifred Endpoint", "backup.example")), MODEL)
    seen = _wire_client(
        client, lambda _r: httpx.Response(429, json={"error": {"message": "slow down"}}),
        retries=3, offer=backup)

    run = _run(client, conn, ["generate"])

    assert [r.url.host for r in seen] == ["primary.example"]
    got = run["result"]["results"]["generate"]
    assert got["ok"] is False
    assert got["kind"] == "rate_limit"
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["task"] == "model-test"
    assert rows[0].get("attempts", 1) == 1   # the ledger omits the default

    # The control: the same facade, sent the same probe through `stream` --
    # with the same fallback offered -- DOES retry and fall back, so the one
    # request above is the test call's doing, not this setup's.
    seen.clear()
    facade = client.app.dependency_overrides[routes.get_llm]()
    probe = config_routes._probe_target(store.llm_connections.read_connection_raw(conn), MODEL)
    with pytest.raises(LLMError):
        asyncio.run(facade.complete(probes.messages("generate"), wire.Chain(probe)))
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
                       api_key="sk-ant-fake-0001")
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


def _anthropic_wire(client, handler) -> tuple[str, list[httpx.Request]]:
    """An Anthropic connection and a REAL facade whose Anthropic adapter talks
    to a `MockTransport` that records."""
    conn = _connection(client, kind="anthropic", name="Saltmarch Direct",
                       api_key="sk-ant-fake-0001")
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    adapter = AnthropicClient(http=httpx.AsyncClient(transport=httpx.MockTransport(record)))
    client.app.dependency_overrides[routes.get_llm] = lambda: LLMClient(anthropic=adapter)
    return conn, seen


def _anthropic_run(client, conn: str, caps) -> dict:
    r = client.post(f"/api/llm-connections/{conn}/test",
                    json={"model": "claude-model-x", "capabilities": list(caps),
                          "confirm": True})
    assert r.status_code == 202, r.text
    return _wait(client, r.json()["run"]["id"])


def _anthropic_error(status: int, etype: str, message: str, **error):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"type": "error", "error": {
            "type": etype, "message": message, **error}})
    return handler


@pytest.mark.parametrize("handler", [
    _anthropic_error(400, "invalid_request_error",
                     "You have reached your specified API usage limits. You will regain "
                     "access on 2026-11-01 at 00:00 UTC."),
    _anthropic_error(400, "invalid_request_error",
                     "You have reached your specified workspace API usage limits. You will "
                     "regain access on 2026-11-01 at 00:00 UTC."),
    _anthropic_error(429, "rate_limit_error", "Spend limit reached for this organization.",
                     details={"error_code": "enforced_spend_limit_reached"}),
    _anthropic_error(402, "billing_error", "Your credit balance is too low."),
], ids=["api-limit-400", "workspace-limit-400", "spend-cap-429", "billing-402"])
def test_an_account_limit_is_not_a_verdict_and_stops_the_rest(client, handler):
    """A spend limit says the ACCOUNT may not spend, not what the model can
    do: nothing is filed, and every probe after it would meet the same limit,
    so none is sent."""
    conn, seen = _anthropic_wire(client, handler)

    run = _anthropic_run(client, conn, ["generate", "vision"])

    assert len(seen) == 1
    results = run["result"]["results"]
    assert results["generate"]["ok"] is False
    assert results["vision"]["kind"] == "not_sent"
    assert run["result"]["recorded"] is False
    assert facts.read(conn) == {}


def test_an_ordinary_anthropic_capability_refusal_is_still_recorded(client):
    def handler(request: httpx.Request) -> httpx.Response:
        if '"image"' in request.content.decode():
            return _anthropic_error(400, "invalid_request_error",
                                    "this model does not support image input")(request)
        return _anthropic_error(400, "invalid_request_error",
                                "the requested model cannot generate text")(request)

    conn, seen = _anthropic_wire(client, handler)

    run = _anthropic_run(client, conn, ["generate", "vision"])

    assert len(seen) == 2
    assert run["result"]["recorded"] is True
    verified = facts.of(conn, "claude-model-x", _rev(conn))["verified"]
    assert verified["generate"]["ok"] is False
    assert verified["vision"]["ok"] is False
    assert "image input" in verified["vision"]["error"]


def test_a_429_without_the_spend_code_is_neither_filed_nor_halting(client):
    conn, seen = _anthropic_wire(
        client, _anthropic_error(429, "rate_limit_error", "Number of requests exceeded."))

    run = _anthropic_run(client, conn, ["generate", "vision"])

    assert len(seen) == 2
    assert run["result"]["recorded"] is False


def _land_inside_the_window(monkeypatch, other) -> list[threading.Thread]:
    """Run `other` on a second thread the first time the CURRENT thread reads
    a connection, and give it a second to finish before that read returns.

    That read is the rev check `_record_verdicts` makes before it files a
    verdict. Without one serialization boundary over the check and the write,
    `other` (an edit, a delete) finishes inside the window, between them;
    with it, `other` waits for the lock the record holds, the wait times out,
    and it lands after the record -- deterministic either way."""
    real = store.llm_connections.read_connection_raw
    caller = threading.get_ident()
    started: list[threading.Thread] = []

    def hooked(conn_id):
        got = real(conn_id)
        if not started and threading.get_ident() == caller:
            t = threading.Thread(target=other)
            started.append(t)
            t.start()
            t.join(timeout=1.0)
        return got

    monkeypatch.setattr(store.llm_connections, "read_connection_raw", hooked)
    return started


def test_an_edit_inside_the_record_window_keeps_the_newer_revs_verdicts(client, monkeypatch):
    """A probe run started on rev A files its verdict while an edit (rev B)
    and that rev's own test land: B's verdicts must survive, and nothing of
    A's may replace them."""
    conn = _endpoint(client, "Mara Endpoint", "primary.example")
    stale = _rev(conn)
    newer: dict = {}

    def edit_and_test() -> None:
        store.llm_connections.update_connection(conn, api_key="sk-fake-rotated")
        newer["rev"] = _rev(conn)
        facts.record_verified(conn, MODEL, newer["rev"], {"vision": {"ok": True, "at": "b"}})

    threads = _land_inside_the_window(monkeypatch, edit_and_test)
    config_routes._record_verdicts(conn, MODEL, stale, {"vision": {"ok": False, "error": "a"}})
    for t in threads:
        t.join(timeout=10)

    assert newer["rev"] != stale
    assert facts.of(conn, MODEL, newer["rev"])["verified"] == {"vision": {"ok": True, "at": "b"}}
    assert facts.read(conn)[MODEL]["verified"]["rev"] == newer["rev"]


def test_a_delete_inside_the_record_window_leaves_no_facts_file(client, monkeypatch):
    conn = _endpoint(client, "Mara Endpoint", "primary.example")
    rev = _rev(conn)

    threads = _land_inside_the_window(
        monkeypatch, lambda: store.llm_connections.delete_connection(conn))
    config_routes._record_verdicts(conn, MODEL, rev, {"vision": {"ok": True}})
    for t in threads:
        t.join(timeout=10)

    with pytest.raises(store.llm_connections.ConnectionNotFound):
        store.llm_connections.read_connection_raw(conn)
    assert not store.llm_connections.facts_path(conn).exists()


def test_a_record_after_the_rev_moved_writes_nothing(client):
    """The ordinary case: the edit landed before the record was asked for."""
    conn = _endpoint(client, "Mara Endpoint", "primary.example")
    stale = _rev(conn)
    store.llm_connections.update_connection(conn, api_key="sk-fake-rotated")
    new = _rev(conn)
    assert facts.record_verified(conn, MODEL, new, {"embed": {"ok": True, "at": "b"}})

    assert config_routes._record_verdicts(conn, MODEL, stale, {"vision": {"ok": True}}) is False
    assert facts.of(conn, MODEL, new)["verified"] == {"embed": {"ok": True, "at": "b"}}
    assert config_routes._record_verdicts(conn, MODEL, new, {"vision": {"ok": True}}) is True


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


def test_an_embed_probe_row_carries_what_the_endpoint_reported(client, monkeypatch):
    _use(client, FakeOpenRouter(["ok"]))
    conn = _connection(client)
    _embedder(monkeypatch, lambda _r: httpx.Response(200, json={
        "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}],
        "usage": {"prompt_tokens": 4}}))

    run = _run(client, conn, ["embed"])

    assert run["result"]["results"]["embed"]["ok"] is True
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["task"] == "model-test"
    assert rows[0]["operation"] == "embed"
    assert rows[0]["prompt_tokens"] == 4
    assert "completion_tokens" not in rows[0]


def test_an_embed_probes_local_count_runs_off_the_event_loop(client, monkeypatch):
    """`_embed_probe` is a coroutine on the lifespan loop, and the estimate
    can count with an encoder: it runs in a worker, as the embed does."""
    import asyncio

    _use(client, FakeOpenRouter(["ok"]))
    conn = _connection(client)
    _embedder(monkeypatch, lambda _r: httpx.Response(200, json={
        "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]}))
    real = store.inference.embed.estimate_prompt
    on_loop: list[bool] = []

    def spy(holder, texts):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(holder, texts)

    monkeypatch.setattr(store.inference.embed, "estimate_prompt", spy)

    run = _run(client, conn, ["embed"])

    assert run["result"]["results"]["embed"]["ok"] is True
    assert on_loop == [False]
    assert _rows()[0]["tokens_estimated"] is True


def test_an_embed_probe_failure_logs_only_its_kind_and_status(client, monkeypatch, tmp_path):
    """I1: a redirect's `Location` can carry a key, and the probe's error row
    is its kind and status only -- while the verdict the user reads still
    shows the provider's own text."""
    _use(client, FakeOpenRouter(["ok"]))
    conn = _connection(client)
    _embedder(monkeypatch, lambda _r: httpx.Response(
        307, headers={"Location": "https://gw.example/v1/embeddings/?key=sk-fake-307"}))

    run = _run(client, conn, ["embed"])

    got = run["result"]["results"]["embed"]
    assert got["ok"] is False
    assert "sk-fake-307" in got["error"]
    logged = [r for r in store.logs.read(level="error")["rows"] if r["module"] == "model-test"]
    assert [r["message"] for r in logged] == ["missing_key"]
    text = "".join(p.read_text(encoding="utf-8")
                   for p in sorted((tmp_path / "logs").glob("*.jsonl")))
    assert "sk-fake-307" not in text
    rows = _rows()
    assert len(rows) == 1
    assert (rows[0]["status"], rows[0]["operation"]) == ("error", "embed")


def test_the_gateway_fake_answers_single_like_complete():
    """`llm_fakes` stands in for the whole facade surface routes use."""
    import asyncio
    fake = FakeLLM([["o", "k"]], error=None)
    usage: dict = {}
    assert asyncio.run(fake.single([], wire_kit.target(model="m"), usage)) == "ok"
    assert usage["attempts"] == 1
    failing = FakeLLM([["x"]], error=LLMError("auth", "no"))
    with pytest.raises(LLMError):
        asyncio.run(failing.single([], wire_kit.target(model="m")))


def test_a_probe_that_never_answers_ends_the_run_whatever_the_budget_says(
        client, monkeypatch):
    """`llm_call_budget` `0` means "no ceiling at all", which the model test
    must not inherit: a wedged probe (the Claude CLI is a subprocess with no
    transport bound) would hold the run -- and `PUT /config/data-dir` with it
    -- for good. The probes carry a ceiling of their own, as the health check
    does, and an overrun is a `timeout` that stops the probes after it."""
    from tests.llm_fakes import StallingGateway

    store.write_config(llm_call_budget="0")
    monkeypatch.setattr(config_routes, "MODEL_TEST_CEILING", 0.05)
    _use(client, StallingGateway(where="single"))
    conn = _connection(client)

    run = _run(client, conn, ["generate", "vision"])

    got = run["result"]["results"]
    assert got["generate"]["kind"] == "timeout", got
    assert got["vision"]["kind"] == "not_sent", got


def test_a_model_test_row_carries_its_probe_operation(client, monkeypatch):
    """M9: each probe's row names the operation it probed, and the billing the
    lowered connection carries -- on a target stamped with it, so the
    connection the run holds is never written to."""
    _use(client, FakeOpenRouter(["ok"]))
    conn = _connection(client)
    _embedder(monkeypatch, _vector)

    _run(client, conn, ["embed", "generate"])

    rows = _rows()
    assert sorted(r.get("operation") for r in rows) == ["embed", "generate"]
    assert all(r["provider_id"] == conn for r in rows)
    assert all(r["billing"] == "metered" for r in rows)
    assert all("role" not in r for r in rows)


# ---- the decide_native probe (slice H, spec 6.4) -------------------------------

def _native_answer() -> decisions.ItemResult:
    return decisions.ItemResult({"probe": decisions.Answer(True)})


def _openai(client) -> str:
    """The OpenAI preset: the one `openai_compatible` provider with a native
    endpoint, and one that does not report its own price (rates may price it)."""
    return _connection(client, kind="openai_compatible", name="Realm OpenAI",
                       base_url="https://api.openai.com/v1", api_key="sk-fake-openai")


def test_the_decide_native_probe_has_one_predicate_and_no_price():
    item = probes.PROBE_ITEM
    assert item == decisions.Item("The lamp in the window is lit.",
                                  (decisions.Predicate("probe", "Is the lamp lit?"),))
    assert probes.PROBES["decide_native"].priceable is False
    assert all(p.priceable for c, p in probes.PROBES.items() if c != "decide_native")
    assert probes.describe("decide_native", True) == (
        "One native decision request: the statement “The lamp in the window is lit.” "
        "and the yes/no question “Is the lamp lit?”.")


def test_the_decide_native_probe_sends_one_native_request(client):
    fake = FakeLLM([["unused"]], decisions=[_native_answer()])
    _use(client, fake)
    conn = _connection(client)
    rev = _rev(conn)

    run = _run(client, conn, ["decide_native"])

    assert run["result"]["results"]["decide_native"] == {"ok": True}
    assert len(fake.native_requests) == 1
    item, sent, retries = fake.native_requests[0]
    assert item == probes.PROBE_ITEM
    assert retries == 0
    assert sent.model == MODEL
    assert fake.calls == 0   # nothing generated
    rows = _rows()
    assert [(r["task"], r["operation"], r["decision_mode"]) for r in rows] == [
        ("model-test", "decide", "native")]
    assert not rows[0].get("campaign")
    assert facts.of(conn, MODEL, rev)["verified"]["decide_native"]["ok"] is True


def test_the_decide_native_probe_row_stays_unpriced_in_the_ledger(client):
    """A rate on file prices a chat call; it must not price a native decision
    (ruling 10), so the probe's row is unpriced, not modelled."""
    fake = FakeLLM([["unused"]], decisions=[_native_answer()],
                   usage={"prompt_tokens": 30, "completion_tokens": 0})
    _use(client, fake)
    conn = _openai(client)
    store.pricing.write_pricing({MODEL: RATES})
    facts.state(conn, MODEL, rates=RATES)

    _run(client, conn, ["decide_native"])

    totals = store.usage.summary(days=1)["totals"]
    assert totals["modelled_usd"] == 0.0 and totals["modelled_calls"] == 0
    assert totals["unpriced_calls"] == 1


def test_a_refused_decide_native_probe_is_recorded_unverified(client):
    refusal = LLMError("bad_response", "decisions endpoint refused the request", status=400)
    _use(client, FakeLLM([["unused"]], decisions=[refusal]))
    conn = _connection(client)

    run = _run(client, conn, ["decide_native"])

    got = run["result"]["results"]["decide_native"]
    assert got["ok"] is False and "refused" in got["error"]
    stored = facts.of(conn, MODEL, _rev(conn))["verified"]["decide_native"]
    assert stored["ok"] is False and stored["error"] == got["error"]
    cap = store.inference.capabilities.caps_for(
        store.llm_connections.read_connection_raw(conn), MODEL)["decide_native"]
    assert (cap.value, cap.source, cap.error) == ("unknown", "test", got["error"])


def test_a_403_refuses_the_decide_native_probe_and_is_recorded(client):
    forbidden = LLMError("auth", "this key may not use decisions", status=403)
    _use(client, FakeLLM([["unused"]], decisions=[forbidden]))
    conn = _connection(client)

    run = _run(client, conn, ["decide_native"])

    assert run["result"]["recorded"] is True
    assert facts.of(conn, MODEL, _rev(conn))["verified"]["decide_native"]["ok"] is False


def test_a_decide_native_failure_with_no_status_is_not_recorded(client):
    _use(client, FakeLLM([["unused"]], decisions=[
        LLMError("bad_response", "the decisions body did not parse")]))
    conn = _connection(client)

    run = _run(client, conn, ["decide_native"])

    assert run["result"]["results"]["decide_native"]["ok"] is False
    assert run["result"]["recorded"] is False
    assert "decide_native" not in facts.of(conn, MODEL, _rev(conn)).get("verified", {})


@pytest.mark.parametrize("path", ["test", "test/preview"])
def test_the_decide_native_probe_is_refused_on_presets_that_cannot(client, path):
    fake = FakeLLM([["unused"]], decisions=[_native_answer()])
    _use(client, fake)
    conn = _connection(client, kind="anthropic", name="Saltmarch Direct",
                       api_key="sk-ant-fake-0001")

    r = client.post(f"/api/llm-connections/{conn}/{path}",
                    json={"model": "claude-model-x", "capabilities": ["decide_native"],
                          "confirm": True})

    assert r.status_code == 400
    assert "decide_native" in r.json()["detail"]
    assert fake.native_requests == [] and fake.calls == 0
    assert _rows() == []


def test_the_preview_says_cost_unknown_for_a_decision_probe(client):
    _use(client, FakeLLM([["unused"]], decisions=[_native_answer()]))
    conn = _connection(client)
    row = catalog.entry({"id": MODEL, "pricing": {"prompt": "0.000003", "completion": "0.000015"}})
    store.llm_connections.set_cached_models(conn, [row], _rev(conn))

    body = _preview(client, conn, ["decide_native"])

    assert body["estimated_cost_usd"] is None
    assert body["estimate_basis"] is None
    assert [s["capability"] for s in body["sends"]] == ["decide_native"]
    # Alongside a priceable probe the whole estimate is unknown, not the
    # priceable half: a sum that left a probe out would be a floor shown as a price.
    both = _preview(client, conn, ["generate", "decide_native"])
    assert both["estimated_cost_usd"] is None and both["estimate_basis"] is None
    assert probes.estimate_usd(row, ["generate"]) is not None


def test_a_pricing_wildcard_does_not_price_the_decision_probe(client):
    """I6: a `pricing.json` entry would price a 0-token probe at 0.0 and the
    confirmation would read $0.00 at the moment it asks for consent."""
    _use(client, FakeLLM([["unused"]], decisions=[_native_answer()]))
    conn = _openai(client)
    store.pricing.write_pricing({"vendor/*": RATES})
    assert _preview(client, conn, ["generate"])["estimate_basis"] == "rates"

    body = _preview(client, conn, ["decide_native"])

    assert body["estimated_cost_usd"] is None
    assert body["estimate_basis"] is None
    assert probes.estimate_from_rates(dict(RATES), ["decide_native"]) is None
    assert probes.estimate_from_rates(dict(RATES), ["generate", "decide_native"]) is None
