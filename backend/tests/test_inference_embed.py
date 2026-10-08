"""The embed operation: `embed_sync` / `embed`, the one metered door to the
embeddings endpoint (spec 7.3, 9.3, 9.4; slice D Task 4).

What this suite holds:

* one ledger row per call, covering every batch, under its embed task with
  `operation: "embed"`, and carrying the campaign and scene it is handed;
* nothing sent, nothing filed: an empty input, and a deadline already spent
  when the call starts; a deadline that lapses inside the meter is `aborted`;
* a failure is recorded at `Meter.done` with its kind and HTTP status only --
  never a provider's body or a redirect's `Location` (I1);
* the space is the caller's: the role is never re-resolved (Review Focus 4);
* one Debug-level capture line, which never carries the text;
* what served the call (slice E): `provider_id`, `role`, `billing`, and the
  `requested_model` when the endpoint answered under another name; a prompt
  count the endpoint did not report is estimated locally, never by loading an
  encoder, and flagged `tokens_estimated` -- never a completion count.

Every provider, name and key below is invented.
"""

from __future__ import annotations

import asyncio
import json
import time
import types

import httpx
import pytest

from grimoire import embeddings
from grimoire.store import (
    config,
    embed_space,
    errors,
    llm_connections,
    logs,
    pricing,
    tokens,
    usage,
)
from grimoire.store import inference_keys as keys
from grimoire.store.inference import embed
from grimoire.store.inference import resolve as inference_resolve
from tests.llm_fakes import FakeEmbeddings

TEXT = "Mara crossed the Saltmarch at dusk"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    logs.forget_file_sizes()
    logs.apply_level("info")
    yield tmp_path
    logs.forget_file_sizes()
    logs.apply_level("info")


def _provider(name: str = "Saltmarch Vectors",
              base_url: str = "https://vectors.example/v1") -> str:
    return llm_connections.create_connection(
        "openai_compatible", name, base_url=base_url, api_key="sk-fake-0001",
        model="", post_process="none")


def _role(conn: str, model: str = "embed-1") -> None:
    config.write_config(**{keys.FORMAT_KEY: "2",
                           keys.role_key("embedding", "provider"): conn,
                           keys.role_key("embedding", "model"): model})


@pytest.fixture
def space() -> dict:
    _role(_provider())
    got = embed_space.endpoint()
    assert got is not None
    return got


def _answer(usage_block: dict | None = None):
    """A handler answering each input with `[0.5, 0.25]`."""
    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["input"]
        body: dict = {"data": [{"index": i, "embedding": [0.5, 0.25]}
                               for i in range(len(inputs))]}
        if usage_block is not None:
            body["usage"] = usage_block
        return httpx.Response(200, json=body)
    return handler


def _client(handler=None) -> tuple[embeddings.EmbeddingsClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    handler = handler or _answer()

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return embeddings.EmbeddingsClient(httpx.Client(transport=httpx.MockTransport(record))), seen


def _rows() -> list[dict]:
    return list(usage.calls(days=1))


def _log_text(home) -> str:
    return "".join(p.read_text(encoding="utf-8")
                   for p in sorted((home / "logs").glob("*.jsonl")))


def _embed_rows() -> list[dict]:
    return [r for r in logs.read(level="debug")["rows"] if r.get("module") == "embed"]


# ---- the row -------------------------------------------------------------------

def test_an_embed_files_one_row(space):
    client, seen = _client(_answer({"prompt_tokens": 7}))

    out = embed.embed_sync("semantic-recall", ["x"], space=space, client=client)

    assert out == [[0.5, 0.25]]
    assert len(seen) == 1
    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["task"] == "semantic-recall"
    assert row["operation"] == "embed"
    assert row["model"] == "embed-1"
    assert row["connection"] == "Saltmarch Vectors"
    assert row["provider"] == "openai_compatible"
    assert row["prompt_tokens"] == 7
    assert "completion_tokens" not in row
    assert "campaign" not in row
    assert row["status"] == "ok"


def test_a_row_names_what_served_it(space):
    """Slice E on D's door: the row carries the provider's id, the Embedding
    role whose slot supplied the selection, and -- when the endpoint answered
    under another name -- the model that was asked for (spec 9.3)."""
    def renamed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "embed-1-2026",
                                         "data": [{"index": 0, "embedding": [0.5, 0.25]}],
                                         "usage": {"prompt_tokens": 3}})
    client, _ = _client(renamed)

    embed.embed_sync("semantic-recall", ["x"], space=space, client=client)

    row = _rows()[0]
    assert row["provider_id"] == space["provider"]
    assert row["role"] == "embedding"
    assert row["operation"] == "embed"
    assert row["billing"] == "metered"
    assert row["model"] == "embed-1-2026"
    assert row["requested_model"] == "embed-1"


def test_the_asked_model_is_not_restated_when_it_answered(space):
    client, _ = _client(_answer({"prompt_tokens": 3}))

    embed.embed_sync("semantic-recall", ["x"], space=space, client=client)

    row = _rows()[0]
    assert row["model"] == "embed-1"
    assert "requested_model" not in row
    assert row["provider_id"] == space["provider"]
    assert row["role"] == "embedding"


def test_the_resolution_never_writes_the_stored_record():
    """The account block is the lowered copy's: resolving the role stamps
    nothing onto what a later read of the provider returns."""
    conn = _provider()
    _role(conn)
    got = inference_resolve.embedding()
    assert got.attempts[0].conn[inference_resolve.ACCOUNT_KEY] == {
        "billing": "metered", "operation": "embed", "role": "embedding"}
    assert inference_resolve.ACCOUNT_KEY not in llm_connections.read_connection_raw(conn)


# ---- a prompt count nobody reported (slice E, ruling I1) ----------------------

def test_an_unreported_prompt_is_counted_locally_and_says_so(space, monkeypatch):
    # No encoder loaded: the characters/4 heuristic, rounded up per text.
    monkeypatch.setattr(tokens, "_loaded", lambda: None)
    client, _ = _client(_answer())

    embed.embed_sync("semantic-recall", [TEXT, "abc"], space=space, client=client)

    row = _rows()[0]
    assert row["prompt_tokens"] == -(-len(TEXT) // 4) + 1
    assert row["tokens_estimated"] is True
    # An embedding generates nothing: never a completion count, estimated or not.
    assert "completion_tokens" not in row


def test_the_estimate_never_loads_an_encoder(space, monkeypatch):
    """Only an encoder that has already loaded counts on the request path: a
    load can be a download."""
    def load():
        raise AssertionError("the estimate started an encoder load")
    monkeypatch.setattr(tokens, "_encoder", load)
    monkeypatch.setattr(tokens, "_loaded", lambda: types.SimpleNamespace(
        encode=lambda text: text.split()))
    client, _ = _client(_answer())

    embed.embed_sync("semantic-recall", [TEXT], space=space, client=client)

    assert _rows()[0]["prompt_tokens"] == len(TEXT.split())


def test_a_reported_prompt_is_never_estimated(space):
    client, _ = _client(_answer({"prompt_tokens": 7}))

    embed.embed_sync("semantic-recall", [TEXT], space=space, client=client)

    row = _rows()[0]
    assert row["prompt_tokens"] == 7
    assert "tokens_estimated" not in row


def test_a_failed_embed_is_never_estimated(space):
    client, _ = _client(lambda _r: httpx.Response(500, json={"error": "down"}))

    with pytest.raises(embeddings.EmbeddingsError):
        embed.embed_sync("semantic-recall", [TEXT], space=space, client=client)

    row = _rows()[0]
    assert row["status"] == "error"
    assert "prompt_tokens" not in row
    assert "tokens_estimated" not in row


def test_an_estimated_embed_row_is_modelled_at_a_prompt_rate(space, monkeypatch):
    """The estimate makes the row priceable: its completion is the structural
    zero `usage._completion_count` reads, so a rate prices the whole call into
    `modelled_usd` -- never spend."""
    monkeypatch.setattr(tokens, "_loaded", lambda: None)
    pricing.write_pricing({"embed-1": {"prompt_usd_per_1k": 1.0,
                                       "completion_usd_per_1k": 2.0}})
    client, _ = _client(_answer())

    embed.embed_sync("semantic-recall", ["x" * 4000], space=space, client=client)

    out = usage.summary(days=1)["totals"]
    assert out["modelled_usd"] == pytest.approx(1.0)
    assert out["cost_usd"] == 0.0
    assert out["estimated_token_calls"] == 1


def test_a_row_carries_the_campaign_and_scene_it_is_given(space):
    logs.apply_level("debug")
    client, _ = _client()

    embed.embed_sync("semantic-recall", ["x"], space=space, client=client,
                     campaign="saltmarch", scene="s1")

    row = _rows()[0]
    assert (row["campaign"], row["scene"]) == ("saltmarch", "s1")
    line = _embed_rows()
    assert len(line) == 1
    assert (line[0]["campaign"], line[0]["scene"]) == ("saltmarch", "s1")


def test_a_split_call_is_still_one_row(space, monkeypatch):
    monkeypatch.setattr(embeddings, "BATCH", 1)
    client, seen = _client(_answer({"prompt_tokens": 3}))

    out = embed.embed_sync("art-catalog", ["a", "b"], space=space, client=client)

    assert out == [[0.5, 0.25], [0.5, 0.25]]
    assert len(seen) == 2
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["prompt_tokens"] == 6


def test_the_fake_client_reports_through_the_same_holder(space):
    fake = FakeEmbeddings(usage_for=lambda texts: {"prompt_tokens": 2 * len(texts)})

    embed.embed_sync("continuity-similarity", ["a", "b", "c"], space=space, client=fake)

    assert fake.calls == [["a", "b", "c"]]
    row = _rows()[0]
    assert (row["task"], row["prompt_tokens"], row["model"]) == (
        "continuity-similarity", 6, "embed-1")


# ---- nothing sent, nothing filed -----------------------------------------------

def test_nothing_to_embed_is_free(space):
    logs.apply_level("debug")
    client, seen = _client()

    assert embed.embed_sync("art-catalog", [], space=space, client=client) == []

    assert seen == []
    assert _rows() == []
    assert _embed_rows() == []


def test_a_spent_deadline_sends_nothing_and_files_nothing(space):
    client, seen = _client()

    with pytest.raises(embeddings.EmbeddingsError) as caught:
        embed.embed_sync("semantic-recall", ["x"], space=space, client=client,
                         deadline=time.monotonic() - 1)

    assert caught.value.kind == "network"
    assert caught.value.code == embeddings.NOT_SENT
    assert seen == []
    assert _rows() == []
    assert logs.read(level="error")["rows"] == []


def test_a_deadline_that_lapses_inside_the_meter_files_nothing(space, monkeypatch):
    """No request went out, so there is no row, no error and no capture line
    (spec 9.3): the meter opened, but nothing was sent."""
    logs.apply_level("debug")
    client, seen = _client()
    # `embed_sync`'s own check sees a clock before the deadline; the client's
    # first-batch check sees the real one, after it.
    monkeypatch.setattr(embed, "time", types.SimpleNamespace(monotonic=lambda: 0.0))

    with pytest.raises(embeddings.EmbeddingsError) as caught:
        embed.embed_sync("semantic-recall", ["x"], space=space, client=client,
                         deadline=time.monotonic() - 1)

    assert caught.value.code == embeddings.NOT_SENT
    assert seen == []
    assert _rows() == []
    assert logs.read(level="error")["rows"] == []
    assert _embed_rows() == []


class _Drip(httpx.SyncByteStream):
    """A body that arrives a byte at a time, forever."""

    def __iter__(self):
        while True:
            time.sleep(0.05)
            yield b" "


def test_the_callers_own_deadline_cutting_a_request_is_aborted(space):
    """The request went out and the CALLER's clock cut it (absorb's budget):
    the row is `aborted`, and nothing reaches the error store -- the provider
    did not fail (`routes.scenes._embedding_reason`)."""
    logs.apply_level("debug")
    client, seen = _client(lambda _r: httpx.Response(200, stream=_Drip()))

    with pytest.raises(embeddings.EmbeddingsError):
        embed.embed_sync("continuity-similarity", ["x"], space=space, client=client,
                         deadline=time.monotonic() + 0.3, budgeted=True, campaign="realm")

    assert len(seen) == 1
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "aborted"
    assert not rows[0].get("error")
    assert logs.read(level="error")["rows"] == []
    line = _embed_rows()
    assert len(line) == 1
    assert (line[0]["ok"], line[0]["error"]) == (False, "aborted")


@pytest.mark.parametrize("handed", ["none", "a bound"])
def test_a_deadline_that_only_bounds_the_provider_is_a_provider_failure(
        space, monkeypatch, handed):
    """The client's own `TIMEOUT`, or a deadline that only shares it across a
    retry (lore recall's, search's): a slow provider cut by it is that
    provider failing, filed as an error. Only a `budgeted` deadline is the
    caller's clock."""
    monkeypatch.setattr("grimoire.embeddings.TIMEOUT", 0.3)
    client, _ = _client(lambda _r: httpx.Response(200, stream=_Drip()))
    deadline = None if handed == "none" else time.monotonic() + 0.3

    with pytest.raises(embeddings.EmbeddingsError):
        embed.embed_sync("semantic-recall", ["x"], space=space, client=client,
                         deadline=deadline)

    rows = _rows()
    assert (rows[0]["status"], rows[0]["error"]) == ("error", "network")
    assert len(logs.read(level="error")["rows"]) == 1


# ---- failures ------------------------------------------------------------------

def test_a_provider_failure_is_one_error_row(space):
    client, seen = _client(lambda _r: httpx.Response(503, json={"error": "down"}))

    with pytest.raises(embeddings.EmbeddingsError):
        embed.embed_sync("semantic-recall", ["x"], space=space, client=client)

    assert len(seen) == 1
    rows = _rows()
    assert len(rows) == 1
    assert (rows[0]["status"], rows[0]["error"]) == ("error", "network")
    logged = logs.read(level="error")["rows"]
    assert len(logged) == 1
    assert logged[0]["module"] == "semantic-recall"
    assert logged[0]["message"] == "network (HTTP 503)"


def test_a_provider_error_never_logs_its_body_or_location(space, _home):
    logs.apply_level("debug")
    echo, _ = _client(lambda _r: httpx.Response(
        400, json={"error": {"message": f"cannot embed: {TEXT}"}}))
    moved, _ = _client(lambda _r: httpx.Response(
        307, headers={"Location": "https://gw.example/v1/embeddings/?key=sk-fake-307"}))

    with pytest.raises(embeddings.EmbeddingsError) as first:
        embed.embed_sync("semantic-recall", [TEXT], space=space, client=echo)
    with pytest.raises(embeddings.EmbeddingsError) as second:
        embed.embed_sync("semantic-search", [TEXT], space=space, client=moved)

    # The caller still sees the provider's own words.
    assert "Saltmarch at dusk" in first.value.detail
    assert "sk-fake-307" in second.value.detail
    # Nothing that was written down does.
    text = _log_text(_home)
    assert "Saltmarch at dusk" not in text
    assert "sk-fake-307" not in text
    summary = json.dumps(errors.summary())
    assert "Saltmarch at dusk" not in summary
    assert "sk-fake-307" not in summary
    messages = sorted(r["message"] for r in logs.read(level="error")["rows"])
    assert messages == ["bad_response (HTTP 400)", "missing_key"]


def test_an_unregistered_task_is_refused_before_anything_is_sent(space):
    client, seen = _client()

    with pytest.raises(ValueError):
        embed.embed_sync("chat", ["x"], space=space, client=client)

    assert seen == []
    assert _rows() == []


# ---- the space is the caller's -------------------------------------------------

def test_embed_uses_the_space_it_was_handed(space, monkeypatch):
    other = _provider("Winifred Vectors", "https://elsewhere.example/v1")
    _role(other, "embed-2")
    assert embed_space.endpoint()["model"] == "embed-2"
    calls: list[object] = []
    real = inference_resolve.embedding
    monkeypatch.setattr(inference_resolve, "embedding",
                        lambda *a, **k: calls.append(a) or real(*a, **k))
    client, seen = _client()

    embed.embed_sync("semantic-recall", ["x"], space=space, client=client)

    assert len(seen) == 1
    assert str(seen[0].url) == "https://vectors.example/v1/embeddings"
    assert json.loads(seen[0].content)["model"] == "embed-1"
    assert calls == []


# ---- the capture line ----------------------------------------------------------

def test_the_capture_line_never_carries_the_text(space, _home):
    logs.apply_level("debug")
    client, _ = _client()

    embed.embed_sync("semantic-recall", [TEXT], space=space, client=client,
                     cached=3, uncached=5)

    rows = _embed_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["inputs"] == 1
    assert row["bytes"] == 34
    # Named as spec 9.4 names them.
    assert row["dimension"] == 2
    assert (row["cached"], row["uncached"]) == (3, 5)
    assert row["space_id"] == space["space"]
    assert "dims" not in row and "space" not in row
    assert row["ok"] is True
    assert row["task"] == "semantic-recall"
    assert "Saltmarch at dusk" not in _log_text(_home)


def test_a_failed_capture_line_names_only_the_kind(space, _home):
    logs.apply_level("debug")
    client, _ = _client(lambda _r: httpx.Response(503, json={"error": TEXT}))

    with pytest.raises(embeddings.EmbeddingsError):
        embed.embed_sync("semantic-recall", [TEXT], space=space, client=client)

    rows = _embed_rows()
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert rows[0]["error"] == "network"
    assert "dimension" not in rows[0]
    assert "cached" not in rows[0] and "uncached" not in rows[0]
    assert "Saltmarch at dusk" not in _log_text(_home)


def test_nothing_is_captured_above_debug(space):
    logs.apply_level("info")
    client, _ = _client()

    embed.embed_sync("semantic-recall", ["x"], space=space, client=client)

    assert _embed_rows() == []


# ---- the async form ------------------------------------------------------------

def test_the_async_form_is_the_same_call(space):
    client, seen = _client()

    out = asyncio.run(embed.embed("semantic-search", ["x"], space=space, client=client))

    assert out == [[0.5, 0.25]]
    assert len(seen) == 1
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["task"] == "semantic-search"
