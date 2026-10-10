"""No embedding on the event loop (roadmap 01h-C4a, slice 01h-S1).

`embed_sync` is synchronous, and a blocking embeddings request made on the
app's lifespan loop thread freezes every other request for as long as the
endpoint takes. Two halves, landed together:

* the one caller that did it -- the greeting opener, whose `async def` frame
  generator composed (recall and art both embed) on the loop -- composes in a
  worker now;
* a runtime guard: `runner.install` marks the lifespan's loop, its exit
  unmarks it, and `embed_sync` called while it is the running loop is refused
  as `network`/`on_loop` before any meter, so the caller degrades to keyword,
  with ONE error row -- the named exception to "a call that sends nothing
  files nothing".

A worker, a CLI, a thread calling through a portal and a private
`asyncio.run` loop are never refused. Every provider, name and key below is
invented.
"""

from __future__ import annotations

import asyncio
import functools
import importlib
import json
import threading
import types

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.concurrency import run_in_threadpool

import grimoire.store as store
from grimoire import embeddings, routes, runner
from grimoire.main import create_app
from grimoire.routes import greetings
from grimoire.routes.common import require_inference
from grimoire.store import (
    campaign_images,
    campaigns,
    config,
    embed_space,
    entities,
    errors,
    image_descriptions,
    llm_connections,
    logs,
    usage,
)
from grimoire.store import inference_keys as keys
from grimoire.store.context import art, semantic
from grimoire.store.inference import embed
from tests.inference_fixtures import embedding
from tests.llm_fakes import FakeEmbeddings, FakeLLM

TEXT = "Mara crossed the Saltmarch at dusk"

#: A hand-built space, as the art tests use: enough for `_stamp`.
SPACE = {"space": "s", "model": "m", "key": "k", "base_url": "u"}


# ---- helpers -----------------------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    logs.forget_file_sizes()
    logs.apply_level("info")
    yield tmp_path
    logs.forget_file_sizes()
    logs.apply_level("info")


@pytest.fixture
def space(home) -> dict:
    conn = llm_connections.create_connection(
        "openai_compatible", "Saltmarch Vectors", base_url="https://vectors.example/v1",
        api_key="sk-fake-0001", model="", post_process="none")
    config.write_config(**{keys.FORMAT_KEY: "2",
                           keys.role_key("embedding", "provider"): conn,
                           keys.role_key("embedding", "model"): "embed-1"})
    got = embed_space.endpoint()
    assert got is not None
    return got


def _client() -> tuple[embeddings.EmbeddingsClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        inputs = json.loads(request.content)["input"]
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [0.5, 0.25]}
                                                  for i in range(len(inputs))]})

    return embeddings.EmbeddingsClient(httpx.Client(transport=httpx.MockTransport(handler))), seen


def _on_marked_loop(fn):
    """Run `fn()` inside a private loop marked as an app loop."""
    async def body():
        loop = asyncio.get_running_loop()
        embed.mark_app_loop(loop)
        try:
            return fn()
        finally:
            embed.unmark_app_loop(loop)
    return asyncio.run(body())


def _other_loop():
    """A loop object that is never run: something to mark that is not ours."""
    return asyncio.new_event_loop()


def _error_rows() -> list[dict]:
    return logs.read(level="error")["rows"]


def _on_loop_rows() -> list[dict]:
    return [r for r in _error_rows() if r.get("message") == embed.ON_LOOP]


def _rows() -> list[dict]:
    return list(usage.calls(days=1))


def _log_text(home) -> str:
    return "".join(p.read_text(encoding="utf-8")
                   for p in sorted((home / "logs").glob("*.jsonl")))


# ---- the guard ---------------------------------------------------------------

def test_a_call_on_a_marked_loop_is_refused_before_anything_is_sent(space, home):
    logs.apply_level("debug")
    client, seen = _client()

    with pytest.raises(embeddings.EmbeddingsError) as caught:
        _on_marked_loop(lambda: embed.embed_sync(
            "semantic-recall", [TEXT], space=space, client=client,
            campaign="saltmarch", scene="001-quay"))

    assert caught.value.kind == "network"
    assert caught.value.code == embed.ON_LOOP
    assert seen == []
    assert _rows() == []
    assert [r for r in logs.read(level="debug")["rows"] if r.get("module") == "embed"] == []
    [row] = _error_rows()
    assert row["module"] == "semantic-recall"
    assert row["kind"] == "network"
    assert row["message"] == embed.ON_LOOP
    assert row["task"] == "semantic-recall"
    assert (row["campaign"], row["scene"]) == ("saltmarch", "001-quay")
    assert "tests/test_embed_off_loop.py" in row["trace"]
    assert "_refuse_on_loop" not in row["trace"]   # the guard's own frames left out
    assert "_caller_frames" not in row["trace"]
    # Counts, kinds and ids only: never the text it was handed.
    assert "Saltmarch at dusk" not in _log_text(home)


def test_the_refusal_is_recorded_once(space):
    client, _ = _client()

    with pytest.raises(embeddings.EmbeddingsError) as caught:
        _on_marked_loop(lambda: embed.embed_sync("semantic-recall", ["x"], space=space,
                                                 client=client))
    errors.record_exception(caught.value, "semantic-recall")

    assert len(_error_rows()) == 1


def test_an_empty_input_is_free_even_on_the_loop(space):
    client, seen = _client()

    assert _on_marked_loop(lambda: embed.embed_sync("art-catalog", [], space=space,
                                                    client=client)) == []

    assert seen == []
    assert _rows() == []
    assert _error_rows() == []


def test_the_refusal_comes_before_a_spent_deadline(space):
    client, _ = _client()

    with pytest.raises(embeddings.EmbeddingsError) as caught:
        _on_marked_loop(lambda: embed.embed_sync("semantic-recall", ["x"], space=space,
                                                 client=client, deadline=0.0))

    assert caught.value.code == embed.ON_LOOP


def test_an_unmarked_private_loop_is_not_refused(space):
    client, seen = _client()
    other = _other_loop()
    embed.mark_app_loop(other)
    try:
        async def body():
            return embed.embed_sync("semantic-search", ["x"], space=space, client=client)
        out = asyncio.run(body())
    finally:
        embed.unmark_app_loop(other)
        other.close()

    assert out == [[0.5, 0.25]]
    assert len(seen) == 1
    assert len(_rows()) == 1
    assert _error_rows() == []


def test_a_worker_under_a_marked_loop_is_not_refused(space):
    client, seen = _client()

    async def body():
        loop = asyncio.get_running_loop()
        embed.mark_app_loop(loop)
        try:
            first = await asyncio.to_thread(
                functools.partial(embed.embed_sync, "semantic-search", ["x"],
                                  space=space, client=client))
            second = await embed.embed("semantic-search", ["y"], space=space, client=client)
        finally:
            embed.unmark_app_loop(loop)
        return first, second

    assert asyncio.run(body()) == ([[0.5, 0.25]], [[0.5, 0.25]])
    assert len(seen) == 2
    assert len(_rows()) == 2
    assert _error_rows() == []


def test_a_cli_call_is_not_refused_while_an_app_loop_is_marked(space):
    """No loop runs in a CLI, so nothing is blocked and nothing is refused,
    whatever else is marked."""
    client, seen = _client()
    other = _other_loop()
    embed.mark_app_loop(other)
    try:
        out = embed.embed_sync("semantic-search", ["x"], space=space, client=client)
    finally:
        embed.unmark_app_loop(other)
        other.close()

    assert out == [[0.5, 0.25]]
    assert len(seen) == 1
    assert _error_rows() == []


def test_the_registry_counts_marks():
    loop = _other_loop()
    try:
        assert not embed.app_loop_marked(loop)
        embed.unmark_app_loop(loop)              # unmarking the unmarked: a no-op
        embed.mark_app_loop(loop)
        embed.mark_app_loop(loop)
        try:
            embed.unmark_app_loop(loop)
            assert embed.app_loop_marked(loop)
        finally:
            embed.unmark_app_loop(loop)
        assert not embed.app_loop_marked(loop)
    finally:
        loop.close()


# ---- the callers degrade -----------------------------------------------------

TURN = "She drew the blade her mother left her."
BODY = "The sword her mother left her."


def _toward_the_blade(text: str) -> list[float]:
    return [1.0, 0.0] if ("blade" in text or "sword" in text) else [0.0, 1.0]


def _recall_on():
    """The Embedding role on an invented provider, and recall switched on."""
    conn = llm_connections.create_connection(
        "openai_compatible", "Saltmarch Vectors", base_url="https://vectors.example/v1",
        api_key="sk-fake-0001", model="", post_process="none")
    embedding(conn, "embed-1")
    config.write_config(semantic_recall_depth="1", semantic_recall_threshold="0.4")


def test_recall_on_the_loop_falls_back_to_keyword_and_does_not_retry(home, monkeypatch):
    _recall_on()
    fake = FakeEmbeddings(vector_for=_toward_the_blade)
    monkeypatch.setattr(semantic, "_CLIENT", fake)
    cands = [{"name": "Sablewrought", "keys": ["sablewrought"], "owners": [], "body": BODY}]

    got = _on_marked_loop(lambda: semantic.recall_scored(cands, TURN, campaign="saltmarch",
                                                         scene="001-quay"))

    assert got == []                             # keyword-only: nothing recalled
    assert fake.calls == []
    # One refusal: a `network` kind is never retried with the query alone,
    # which would have filed a second.
    [row] = _on_loop_rows()
    assert row["module"] == "semantic-recall"
    assert _rows() == []


def test_art_on_the_loop_keeps_the_keyword_ranking(home, monkeypatch):
    fake = FakeEmbeddings()
    monkeypatch.setattr(art, "_CLIENT", fake)
    cfg = {**SPACE, "threshold": 0.0}

    got = _on_marked_loop(lambda: art._semantic_scores(
        [{"description": "Fishing boats at the quay."}], "The boats came in.", cfg,
        campaign="saltmarch", scene="001-quay"))

    assert got is None                           # the caller keeps its keyword scores
    assert fake.calls == []
    [row] = _on_loop_rows()
    assert row["module"] == "art-catalog"
    assert _rows() == []


# ---- the lifespan marks its loop ---------------------------------------------

def test_the_lifespan_marks_its_loop(client):
    loop = client.portal.call(asyncio.get_running_loop)

    assert embed.app_loop_marked(loop)
    assert client.app.state.run_loop is loop


def test_a_sync_call_portal_called_onto_the_app_loop_is_refused(client):
    fake = FakeEmbeddings()

    with pytest.raises(embeddings.EmbeddingsError) as caught:
        client.portal.call(functools.partial(embed.embed_sync, "semantic-recall", ["x"],
                                             space=SPACE, client=fake))

    assert caught.value.code == embed.ON_LOOP
    assert fake.calls == []
    assert _rows() == []
    assert len(_on_loop_rows()) == 1


def test_a_worker_reached_from_the_app_loop_is_not_refused(client):
    fake = FakeEmbeddings()

    out = client.portal.call(run_in_threadpool, functools.partial(
        embed.embed_sync, "semantic-recall", ["x"], space=SPACE, client=fake))

    assert out == [[1.0, 0.0]]
    assert len(_rows()) == 1
    assert _on_loop_rows() == []


def test_a_thread_calling_through_the_portal_is_not_refused(client):
    """The caller of `portal.call` is a thread of its own, not the loop: what
    it embeds before and after reaching through the portal is not refused."""
    fake = FakeEmbeddings()
    loop = client.portal.call(asyncio.get_running_loop)
    assert embed.app_loop_marked(loop)

    def through_the_portal():
        assert client.portal.call(asyncio.get_running_loop) is loop
        return embed.embed_sync("semantic-search", ["x"], space=SPACE, client=fake)

    assert embed.embed_sync("semantic-search", ["y"], space=SPACE, client=fake) == [[1.0, 0.0]]
    assert through_the_portal() == [[1.0, 0.0]]
    assert len(fake.calls) == 2
    assert _on_loop_rows() == []


def test_the_lifespan_exit_unmarks_its_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    with TestClient(app) as c:
        loop = c.portal.call(asyncio.get_running_loop)
        assert embed.app_loop_marked(loop)

    assert not embed.app_loop_marked(loop)
    assert app.state.run_loop is None


def test_uninstall_unmarks_once():
    loop = _other_loop()
    stub = types.SimpleNamespace(state=types.SimpleNamespace(run_loop=loop))
    embed.mark_app_loop(loop)                # this app's mark
    embed.mark_app_loop(loop)                # another owner's
    try:
        runner.uninstall(stub)
        runner.uninstall(stub)               # nothing left to undo: a no-op
        assert embed.app_loop_marked(loop)
        assert stub.state.run_loop is None
    finally:
        embed.unmark_app_loop(loop)
        loop.close()
    assert not embed.app_loop_marked(loop)


# ---- the opener composes off the loop ----------------------------------------

def _opener_scene(client) -> tuple[str, str]:
    """A keyed Primary, a campaign whose one lore entry only recall can reach
    (keyed on a word nobody says), recall switched on, and an empty scene: no
    NPC in the cast, because art ranks only for the Grimoire speaker."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Arrival"}).json()["id"]
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Sablewrought", BODY,
                           keys="sablewrought")
    _recall_on()
    return cid, sid


def test_a_compose_on_the_loop_names_its_route_in_the_row(client, monkeypatch):
    """What the row is for: finding the caller. Recall is the deepest embed
    path, and its row still reaches up to the route module that composed."""
    cid, sid = _opener_scene(client)
    monkeypatch.setattr(semantic, "_CLIENT", FakeEmbeddings(vector_for=_toward_the_blade))
    resolved = require_inference("opener", cid)
    actor = {"actor_ref": "grimoire", "speaker": "Grimoire", "version": ""}

    client.portal.call(functools.partial(greetings._compose_opener_call, cid, sid, TURN,
                                         actor, [], resolved, False))

    [row] = _on_loop_rows()
    assert row["module"] == "semantic-recall"
    assert "routes/greetings.py" in row["trace"]
    assert "_compose_opener_call" in row["trace"]


def test_an_opener_with_recall_and_art_embeds_off_the_loop(client, monkeypatch):
    """The fix is live: an opener's recall and art both embed, in a worker.
    Without it the guard refuses the opener's query embed on the loop -- the
    body never reaches the prompt and an `on_loop` row is filed."""
    cid, sid = _opener_scene(client)
    campaign_images.put_image(cid, "coastline", b"png-41", "png")
    image_descriptions.set_in(campaign_images.images_dir(cid), "coastline",
                             "A hand-drawn map of the northern coastline.")
    recall = FakeEmbeddings(vector_for=_toward_the_blade)
    ranking = FakeEmbeddings(vector_for=_toward_the_blade)
    monkeypatch.setattr(semantic, "_CLIENT", recall)
    monkeypatch.setattr(art, "_CLIENT", ranking)
    fake = FakeLLM(turns=[["A quiet road."]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    loop = client.portal.call(threading.get_ident)        # the loop's thread

    url = f"/api/campaigns/{cid}/scenes/{sid}"
    with client.stream("POST", f"{url}/opener", json={"prompt": TURN}) as response:
        frames = [json.loads(row[6:]) for row in response.iter_lines()
                  if row.startswith("data: ")]

    assert any("speaker_done" in f for f in frames)
    assert any(f.get("done") for f in frames)
    assert BODY in json.dumps(fake.requests[0]["messages"])
    assert recall.threads and ranking.threads
    assert loop not in recall.threads
    assert loop not in ranking.threads
    assert _on_loop_rows() == []
    entries = client.get(f"{url}/prompts").json()["entries"]
    assert any(e.get("task") == "opener" for e in entries)


def test_a_stop_during_the_compose_lets_its_embed_finish_and_file_an_ordinary_row(
        client, monkeypatch):
    """`run_in_threadpool` is not cancellable: a Stop while the worker composes
    lands at the checkpoint after it returns. The recall that was in flight
    finishes and files an ordinary row (never `aborted`), no speaker starts,
    and no generation is sent -- the fake here never suspends, so without the
    checkpoint nothing would deliver the Stop at all and the run would land."""
    cid, sid = _opener_scene(client)
    entered, release = threading.Event(), threading.Event()

    def slow(text: str) -> list[float]:
        if not entered.is_set():
            entered.set()
            assert release.wait(30)
        return _toward_the_blade(text)

    monkeypatch.setattr(semantic, "_CLIENT", FakeEmbeddings(vector_for=slow))
    fake = FakeLLM(turns=[["A quiet road."]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    url = f"/api/campaigns/{cid}/scenes/{sid}/opener"
    answer: dict = {}
    poster = threading.Thread(
        target=lambda: answer.setdefault("body", client.post(url, json={"prompt": TURN}).text))
    poster.start()
    try:
        assert entered.wait(30)
        [run] = client.app.state.runs.live_of_class("draft")
        runner.cancel(client.app, run)
        assert run.cancel_scope.cancel_called
    finally:
        release.set()
        poster.join(30)

    assert run.terminal.wait(10)
    assert run.state == "cancelled"
    assert "speaker_start" not in answer["body"]
    assert fake.requests == []
    [row] = [r for r in _rows() if r["task"] == "semantic-recall"]
    assert row["status"] == "ok"
    assert _on_loop_rows() == []
