"""The native async embed door (roadmap 01h-C4b, slice 01h-S5).

`store.inference.embed.embed` is `embed_sync` for a caller already in a
coroutine, through the app's `embeddings.AsyncEmbeddingsClient`. What this
suite holds:

* parity: the same cases through both doors file the same ledger rows and
  the same capture lines (options, the `param` split, a failure, a width
  mismatch, `post` and `run_id` included);
* the whole call runs under one deadline -- a server that never finishes
  its headers is cut there, the hole the synchronous client documents;
* a cancel files the meter `aborted`, never an error row, and drops the
  sums of a batch in flight;
* a sync client is never awaited and an async one never handed to
  `embed_sync`;
* one client per app, reached through `routes.get_embeddings` and closed
  at lifespan exit.

Every provider, name and key below is invented.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import grimoire.store as store
from grimoire import embeddings, routes
from grimoire.main import create_app
from grimoire.store import config, embed_space, llm_connections, logs, tokens, usage
from grimoire.store import inference_keys as keys
from grimoire.store.inference import embed, facts

TEXT = "Mara crossed the Saltmarch at dusk"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(tokens, "_loaded", lambda: None)
    logs.forget_file_sizes()
    logs.apply_level("debug")
    yield tmp_path
    logs.forget_file_sizes()
    logs.apply_level("info")


def _space(block: dict | None = None) -> dict:
    conn = llm_connections.create_connection(
        "openai_compatible", "Saltmarch Vectors", base_url="https://vectors.example/v1",
        api_key="sk-fake-0001", model="", post_process="none")
    config.write_config(**{keys.FORMAT_KEY: "2",
                           keys.role_key("embedding", "provider"): conn,
                           keys.role_key("embedding", "model"): "embed-1"})
    if block is not None:
        facts.state(conn, "embed-1", embedding=block)
    got = embed_space.endpoint()
    assert got is not None
    return got


def _answer(width: int = 2, usage_block: dict | None = None, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"error": {"message": f"cannot: {TEXT}"}})
        inputs = json.loads(request.content)["input"]
        body: dict = {"data": [{"index": i, "embedding": [0.5] * width}
                               for i in range(len(inputs))]}
        if usage_block is not None:
            body["usage"] = usage_block
        return httpx.Response(200, json=body)
    return handler


def _both(handler):
    """A sync and an async client over one recording transport."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(record)
    return (embeddings.EmbeddingsClient(httpx.Client(transport=transport)),
            embeddings.AsyncEmbeddingsClient(httpx.AsyncClient(transport=transport)), seen)


#: What a row or a line carries that differs between two runs of one call.
_VOLATILE = {"ts", "duration_ms", "at", "time", "seq", "id"}


def _rows() -> list[dict]:
    return [{k: v for k, v in r.items() if k not in _VOLATILE} for r in usage.calls(days=1)]


def _lines() -> list[dict]:
    return [{k: v for k, v in r.items() if k not in _VOLATILE}
            for r in logs.read(level="debug")["rows"] if r.get("module") != "llm_response"]


def _clear(home) -> None:
    for p in (home / "logs").glob("*.jsonl"):
        p.unlink()
    for p in (home / "usage").glob("*"):
        if p.is_file():
            p.unlink()
    logs.forget_file_sizes()


PARAM = {"input": "param", "param_field": "input_type", "query_value": "query",
         "document_value": "document"}
NOMIC = {"input": "prefix", "query_prefix": "search_query: ",
         "document_prefix": "search_document: "}

CASES = {
    "plain": (None, _answer(usage_block={"prompt_tokens": 9}), {}),
    "unreported": (None, _answer(), {"campaign": "saltmarch", "scene": "dusk", "post": 4,
                                     "run_id": "run-mara-1"}),
    "prefix": (NOMIC, _answer(), {"queries": 1}),
    "param": (PARAM, _answer(usage_block={"prompt_tokens": 2}), {"queries": 1}),
    "refused": (None, _answer(status=400), {}),
    "down": (None, _answer(status=503), {"campaign": "saltmarch"}),
    "mismatch": ({"dimensions": 4}, _answer(width=2), {}),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_both_doors_file_the_same_rows_and_lines(case, _home):
    block, handler, extra = CASES[case]
    space = _space(block)
    sync, aclient, seen = _both(handler)
    texts = ["Where is Mara?", TEXT, "Winifred waits."]

    def run_sync():
        return embed.embed_sync("semantic-recall", texts, space=space, client=sync, **extra)

    def run_async():
        return asyncio.run(embed.embed("semantic-recall", texts, space=space, client=aclient,
                                       **extra))

    results = []
    for run in (run_sync, run_async):
        _clear(_home)
        try:
            results.append(("ok", run()))
        except embeddings.EmbeddingsError as exc:
            results.append((exc.kind, getattr(exc, "returned_dims", None)))
        results.append((_rows(), _lines()))
    assert results[0] == results[2], case
    assert results[1] == results[3], case
    rows, lines = results[3]
    assert len(rows) == 1 and len([ln for ln in lines if ln.get("module") == "embed"]) == 1
    # Both doors sent the same requests.
    half = len(seen) // 2
    assert [r.content for r in seen[:half]] == [r.content for r in seen[half:]]


def test_post_and_run_id_are_filed(_home):
    space = _space()
    _sync, aclient, _ = _both(_answer())
    asyncio.run(embed.embed("semantic-recall", ["x"], space=space, client=aclient,
                            post=7, run_id="run-seraphine-2"))
    [row] = usage.calls(days=1)
    assert (row["post"], row["run_id"]) == (7, "run-seraphine-2")
    [line] = [r for r in logs.read(level="debug")["rows"] if r.get("module") == "embed"]
    assert line["run_id"] == "run-seraphine-2"


def test_without_post_or_run_id_the_row_is_todays(_home):
    space = _space()
    sync, _a, _ = _both(_answer())
    embed.embed_sync("semantic-recall", ["x"], space=space, client=sync)
    [row] = usage.calls(days=1)
    assert "post" not in row and "run_id" not in row


def test_the_doors_refuse_the_wrong_kind_of_client(_home):
    space = _space()
    sync, aclient, seen = _both(_answer())
    with pytest.raises(TypeError):
        embed.embed_sync("semantic-recall", ["x"], space=space, client=aclient)
    with pytest.raises(TypeError):
        asyncio.run(embed.embed("semantic-recall", ["x"], space=space, client=sync))
    assert seen == [] and list(usage.calls(days=1)) == []


# ---- the deadline and cancellation -----------------------------------------------

def _hanging(started: asyncio.Event | None = None):
    """A server that takes the request and never finishes its headers."""
    async def handler(request: httpx.Request) -> httpx.Response:
        if started is not None:
            started.set()
        await asyncio.sleep(30)
        return httpx.Response(200, json={"data": []})
    return handler


def test_a_server_that_never_finishes_its_headers_is_cut_at_the_deadline():
    client = embeddings.AsyncEmbeddingsClient(
        httpx.AsyncClient(transport=httpx.MockTransport(_hanging())))
    holder: dict = {}

    async def go():
        t0 = time.monotonic()
        with pytest.raises(embeddings.EmbeddingsError) as got:
            await client.embed(["x"], "m", "", "https://vectors.example/v1",
                               deadline=time.monotonic() + 0.2, usage=holder)
        return got.value, time.monotonic() - t0

    exc, took = asyncio.run(go())
    assert (exc.kind, exc.code) == ("network", embeddings.DEADLINE)
    assert took < 2.0
    assert "prompt_tokens" not in holder


def test_a_budgeted_deadline_cut_is_aborted_through_the_door(_home):
    space = _space()
    client = embeddings.AsyncEmbeddingsClient(
        httpx.AsyncClient(transport=httpx.MockTransport(_hanging())))
    with pytest.raises(embeddings.EmbeddingsError):
        asyncio.run(embed.embed("continuity-similarity", ["x"], space=space, client=client,
                                deadline=time.monotonic() + 0.2, budgeted=True))
    [row] = usage.calls(days=1)
    assert row["status"] == "aborted"
    assert logs.read(level="error")["rows"] == []


def test_a_cancel_mid_request_files_aborted_and_no_error(_home):
    space = _space()

    async def go():
        started = asyncio.Event()
        client = embeddings.AsyncEmbeddingsClient(
            httpx.AsyncClient(transport=httpx.MockTransport(_hanging(started))))
        task = asyncio.create_task(embed.embed("semantic-recall", ["x"], space=space,
                                               client=client, campaign="saltmarch"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    [row] = usage.calls(days=1)
    assert row["status"] == "aborted" and row.get("campaign") == "saltmarch"
    assert "prompt_tokens" not in row
    assert logs.read(level="error")["rows"] == []
    [line] = [r for r in logs.read(level="debug")["rows"] if r.get("module") == "embed"]
    assert line["error"] == "aborted" and line["ok"] is False


# ---- one client per app ----------------------------------------------------------

@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return create_app()


def test_each_app_owns_its_embeddings_client(app):
    other = create_app()
    request = Request({"type": "http", "headers": [], "app": app})
    assert isinstance(app.state.embeddings, embeddings.AsyncEmbeddingsClient)
    assert routes.get_embeddings(request) is app.state.embeddings
    assert app.state.embeddings is not other.state.embeddings


def test_the_client_is_closed_at_lifespan_exit(app):
    with TestClient(app) as client:
        pool = client.portal.call(_open_pool, app)
        assert not pool.is_closed
    assert pool.is_closed


async def _open_pool(app):
    return app.state.embeddings._client()
