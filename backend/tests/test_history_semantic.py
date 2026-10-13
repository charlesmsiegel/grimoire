"""Bounded semantic recall uses one space and never publishes cancelled work."""

import asyncio
import json
import threading
import time

import pytest

from grimoire import embeddings
from grimoire.llm_errors import LLMError
from grimoire.store import compiled, embed_space, llm_connections, usage, vectors
from grimoire.store.context import semantic as lore
from grimoire.store.history import model, retrieval, semantic
from grimoire.store.searchdocs import embedded, live, scene
from tests.cache_sync_fixtures import cache as cache
from tests.history_fixtures import absorbed, actor, played
from tests.history_fixtures import cid as cid
from tests.inference_fixtures import embedding
from tests.llm_fakes import FakeAsyncEmbeddings, FakeEmbeddings


@pytest.fixture
def space(cid, monkeypatch):
    monkeypatch.setattr(embed_space, "_OUTAGES", {}, raising=False)
    conn = llm_connections.create_connection(
        "openai_compatible", "Vectors", base_url="https://vectors.example/v1",
        api_key="sk-fake", model="", post_process="none")
    embedding(conn, "embed-1")
    return embed_space.endpoint()


def documents(cid):
    old = played(cid)
    absorbed(cid, old)
    return live.scene_documents(cid).documents


def score(cid, space, client, docs, texts=("silver ledger",), **kwargs):
    return asyncio.run(semantic.score(
        texts, docs, space=space, embed_client=client,
        deadline=time.monotonic() + 1, campaign=cid, scene="999--saltmarch",
        post=3, run_id="history-run", **kwargs))


def test_query_split_attribution_cache_and_materialized(cid, space, cache, tmp_path):
    docs = documents(cid)
    client = FakeAsyncEmbeddings(usage_for=lambda texts: {"prompt_tokens": 7})
    result = score(cid, space, client, docs, ("silver ledger", "amber seal"))
    assert result.status == "ok" and result.warmed == 1
    assert client.queries == [2] and client.calls == [["silver ledger", "amber seal", docs[0].doc.text]]
    assert vectors.load(space["space"], ["silver ledger", "amber seal"]) == {}
    assert docs[0].doc.text in vectors.load(space["space"], [docs[0].doc.text])
    rows = list(usage.calls(days=1))
    assert len(rows) == 1
    assert rows[0]["task"] == "history-recall" and rows[0]["scene"] == "999--saltmarch"
    assert rows[0]["post"] == 3 and rows[0]["run_id"] == "history-run"
    assert result.signals[docs[0].doc.key] == model.SemanticSignal(1.0, 0)
    assert embedded.vector_kind(space).startswith("vector:searchdocs.scene:")
    path = live.scene_file(cid, docs[0])
    with compiled.batch(tmp_path) as request:
        materialized = request.materialized([path])[path]
        request.disabled = True
    assert [json.loads(r.instance) for r in materialized if r.kind == embedded.vector_kind(space)] == [
        {"campaign": cid, "identity": docs[0].doc.meta["identity"]}]


def test_bad_warm_retries_only_queries_under_same_deadline(cid, space):
    docs = documents(cid)
    client = FakeAsyncEmbeddings(errors=[LLMError("bad_response", "refused document"), None])
    result = score(cid, space, client, docs)
    assert result.status == "ok" and result.warmed == 0
    assert client.calls == [["silver ledger", docs[0].doc.text], ["silver ledger"]]
    assert client.deadlines[0] == client.deadlines[1]
    assert len(list(usage.calls(days=1))) == 2


@pytest.mark.parametrize("kind", ["auth", "network", "rate_limit", "timeout"])
def test_outage_shared_with_lore_and_success_clears(cid, space, monkeypatch, kind):
    docs = documents(cid)
    client = FakeAsyncEmbeddings(error=LLMError(kind, "unavailable"))
    assert score(cid, space, client, docs).status == "failed:" + kind
    assert score(cid, space, client, docs).status == "backoff"
    assert len(client.calls) == 1 and embed_space.outage(space["space"])
    sync = FakeEmbeddings()
    monkeypatch.setattr(lore, "_CLIENT", sync)
    assert lore._embed(space, "silver ledger", [], campaign=cid, scene="") is None
    assert sync.calls == []
    embed_space.clear_outage(space["space"])
    assert score(cid, space, FakeAsyncEmbeddings(), docs).status == "ok"


def test_cached_wrong_width_and_range_are_evicted(cid, space, monkeypatch):
    docs = documents(cid)
    text = docs[0].doc.text
    vectors.save(space["space"], text, [1, 0, 0])
    assert not score(cid, space, FakeAsyncEmbeddings(), docs).signals
    assert vectors.load(space["space"], [text]) == {}
    vectors.save(space["space"], text, [1, 0])
    monkeypatch.setattr(vectors, "dot", lambda *args: 2.0)
    assert not score(cid, space, FakeAsyncEmbeddings(), docs).signals
    assert vectors.load(space["space"], [text]) == {}


def test_stalled_call_bounds_deadline_and_sets_backoff(cid, space):
    docs = documents(cid)
    client = FakeAsyncEmbeddings(delay=10)
    async def run():
        return await semantic.score(("silver ledger",), docs, space=space,
                                    embed_client=client, deadline=time.monotonic()+0.05,
                                    campaign=cid, scene="999--saltmarch")
    start = time.monotonic()
    assert asyncio.run(run()).status == "failed:timeout"
    assert time.monotonic()-start < 0.8
    assert embed_space.outage(space["space"])


def test_cancelled_request_does_not_save(cid, space):
    docs = documents(cid)
    client = FakeAsyncEmbeddings(delay=10)
    async def run():
        task = asyncio.create_task(semantic.score(
            ("silver ledger",), docs, space=space, embed_client=client,
            deadline=time.monotonic()+30, campaign=cid, scene="999--saltmarch"))
        while not client.calls:
            if task.done():
                await task
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert vectors.load(space["space"], [docs[0].doc.text]) == {}


def test_retrieve_adds_semantic_only_scene(cid, space):
    docs = documents(cid)
    current = played(cid, text="amber seal")
    evidence = asyncio.run(retrieval.retrieve(
        cid, current, model.Query(("amber seal",)), ceiling=1200,
        embed_client=FakeAsyncEmbeddings(), deadline=time.monotonic()+1))
    assert evidence.coverage.semantic == "ok" and evidence.coverage.tiers == (1, 2)
    assert evidence.candidates[0].scene.sid == docs[0].sid
    assert evidence.candidates[0].admitted_by == frozenset({"semantic"})


@pytest.mark.parametrize("tier, expected", [(1, 64), (2, 1000)])
def test_1100_weighted_matches_retain_lexical_candidates_and_bound_reads(cid, space, monkeypatch,
                                                                       tier, expected):
    current = played(cid)
    actor(cid, current, "Mara")
    docs = tuple(live.LiveDocument(f"{i:04d}--saltmarch", None,
                                  scene.SceneDocument(str(i), f"Mara ledger {i}", {"identity": str(i)}))
                 for i in range(1100))
    monkeypatch.setattr(retrieval, "_eligible", lambda *args: docs)
    monkeypatch.setattr(retrieval.structural, "candidates", lambda *args, **kwargs: [])
    calls = []
    def load(space_id, texts):
        calls.append(list(texts))
        return {text: [0.0, 1.0] for text in texts}
    monkeypatch.setattr(vectors, "load", load)
    client = FakeAsyncEmbeddings()
    evidence = asyncio.run(retrieval.retrieve(
        cid, current, model.Query(("Mara",), terms=("mara",), tier_limit=tier), ceiling=1200,
        embed_client=client, deadline=time.monotonic()+4))
    assert len(evidence.candidates) == 1100
    assert all("lexical" in c.admitted_by for c in evidence.candidates)
    assert len(calls[0]) == 64
    assert sum(map(len, calls)) == expected
    assert len(client.calls) == 1
    assert evidence.coverage.verdict == "thin"


def test_group_only_admission_widens_to_old_cached_semantic_evidence(cid, space, monkeypatch):
    current = played(cid)
    docs = tuple(live.LiveDocument(f"{i:04d}--saltmarch", None,
                                  scene.SceneDocument(str(i), f"Summary: ledger {i}", {"identity": str(i)}))
                 for i in range(100))
    monkeypatch.setattr(retrieval, "_eligible", lambda *args: docs)
    relation = model.StructuralSignal((model.Relation("group", ("groups:realm",)),))
    def group(*args, **kwargs):
        return [model.Candidate(model.SceneRef(d.sid, d.doc.meta["identity"]),
                                (model.Signals(structural=relation),), {}, 1,
                                frozenset({"structural"})) for d in reversed(docs)]
    monkeypatch.setattr(retrieval.structural, "candidates", group)
    calls = []
    def load(space_id, texts):
        calls.append(list(texts))
        return {text: [1.0, 0.0] if text == docs[0].doc.text else [0.0, 1.0] for text in texts}
    monkeypatch.setattr(vectors, "load", load)
    client = FakeAsyncEmbeddings()
    evidence = asyncio.run(retrieval.retrieve(
        cid, current, model.Query(("amber seal",)), ceiling=1200,
        embed_client=client, deadline=time.monotonic()+4))
    assert evidence.coverage.tiers == (1, 2)
    assert evidence.coverage.verdict == "exhausted"
    assert evidence.candidates[0].scene.sid == docs[0].sid
    assert evidence.candidates[0].admitted_by == frozenset({"structural", "semantic"})
    assert len(calls[0]) == 64 and sum(map(len, calls)) == 100
    assert len(client.calls) == 1


def test_cancel_between_reply_and_worker_publication(cid, space, monkeypatch):
    docs = documents(cid)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = semantic._publish
    def paused(*args):
        entered.set()
        assert release.wait(2)
        try:
            return original(*args)
        finally:
            finished.set()
    monkeypatch.setattr(semantic, "_publish", paused)
    async def run():
        task = asyncio.create_task(semantic.score(
            ("silver ledger",), docs, space=space, embed_client=FakeAsyncEmbeddings(),
            deadline=time.monotonic()+4, campaign=cid, scene="999--saltmarch"))
        await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
    asyncio.run(run())
    assert finished.wait(2)
    assert vectors.load(space["space"], [docs[0].doc.text]) == {}


def test_lore_outage_suppresses_history_and_memo_expires(cid, space, monkeypatch):
    docs = documents(cid)
    sync = FakeEmbeddings(error=LLMError("network", "unavailable"))
    monkeypatch.setattr(lore, "_CLIENT", sync)
    assert lore._embed(space, "silver ledger", [], campaign=cid, scene="") is None
    client = FakeAsyncEmbeddings()
    assert score(cid, space, client, docs).status == "backoff" and not client.calls
    embed_space._OUTAGES[space["space"]] = time.monotonic()-1
    assert score(cid, space, client, docs).status == "ok"


def test_file_load_spending_deadline_stays_unsent(cid, space, monkeypatch):
    docs = documents(cid)
    original = vectors.load
    def slow(*args):
        time.sleep(0.03)
        return original(*args)
    monkeypatch.setattr(vectors, "load", slow)
    client = FakeAsyncEmbeddings()
    async def run():
        return await semantic.score(("silver ledger",), docs, space=space,
                                    embed_client=client, deadline=time.monotonic()+0.01,
                                    campaign=cid, scene="999--saltmarch")
    assert asyncio.run(run()).status == "skipped:deadline"
    assert not client.calls and not embed_space.outage(space["space"])


def test_four_queries_keep_warm_inside_batch_and_pool_first(cid, space):
    docs = tuple(live.LiveDocument(f"{i:04d}--saltmarch", None,
                                  scene.SceneDocument(str(i), f"Mara ledger {i}", {}))
                 for i in range(100))
    client = FakeAsyncEmbeddings()
    result = score(cid, space, client, docs, ("Mara", "Seraphine", "Winifred", "silver ledger"),
                   pool=docs[:64])
    assert result.status == "ok"
    assert len(client.calls[0]) == embeddings.BATCH and client.queries == [4]
    assert set(client.calls[0][4:]) <= {d.doc.text for d in docs[:64]}
    assert result.evaluated == frozenset(d.doc.key for d in docs[:64])


def test_signal_records_best_query_direction(cid, space):
    docs = documents(cid)
    client = FakeAsyncEmbeddings(vector_for=lambda t: [1.0, 0.0] if t == "silver ledger" else [0.0, 1.0])
    result = score(cid, space, client, docs, ("silver ledger", "amber seal"))
    assert result.signals[docs[0].doc.key] == model.SemanticSignal(1.0, 1)


def test_sufficient_union_does_not_load_speculative_widening(cid, space, monkeypatch):
    current = played(cid)
    actor(cid, current, "Mara")
    docs = tuple(live.LiveDocument(f"{i:04d}--saltmarch", None,
                                  scene.SceneDocument(str(i), f"Mara ledger {i}", {"identity": str(i)}))
                 for i in range(1100))
    monkeypatch.setattr(retrieval, "_eligible", lambda *args: docs)
    relation = model.StructuralSignal((model.Relation("group", ("groups:realm",)),))
    monkeypatch.setattr(retrieval.structural, "candidates", lambda *args, **kwargs: [
        model.Candidate(model.SceneRef(d.sid, d.doc.meta["identity"]),
                        (model.Signals(structural=relation),), {}, 1, frozenset({"structural"}))
        for d in reversed(docs)])
    loads = []
    def load(space_id, texts):
        loads.append(texts)
        return {text: [0.0, 1.0] for text in texts}
    monkeypatch.setattr(vectors, "load", load)
    evidence = asyncio.run(retrieval.retrieve(
        cid, current, model.Query(("Mara",), terms=("mara",)), ceiling=1200,
        embed_client=FakeAsyncEmbeddings(), deadline=time.monotonic()+4))
    assert len(evidence.candidates) == 1100
    assert evidence.coverage.verdict == "sufficient" and evidence.coverage.tiers == (1,)
    assert sum(map(len, loads)) == 64


@pytest.mark.parametrize("count", [64, 100])
def test_missing_vectors_do_not_make_group_coverage_exhausted(cid, space, monkeypatch, count):
    current = played(cid)
    docs = tuple(live.LiveDocument(f"{i:04d}--saltmarch", None,
                                  scene.SceneDocument(str(i), f"Summary: ledger {i}", {"identity": str(i)}))
                 for i in range(count))
    monkeypatch.setattr(retrieval, "_eligible", lambda *args: docs)
    relation = model.StructuralSignal((model.Relation("group", ("groups:realm",)),))
    monkeypatch.setattr(retrieval.structural, "candidates", lambda *args, **kwargs: [
        model.Candidate(model.SceneRef(d.sid, d.doc.meta["identity"]),
                        (model.Signals(structural=relation),), {}, 1, frozenset({"structural"}))
        for d in reversed(docs)])
    client = FakeAsyncEmbeddings(vector_for=lambda text: [1.0, 0.0] if text == "amber seal" else [0.0, 1.0])
    evidence = asyncio.run(retrieval.retrieve(
        cid, current, model.Query(("amber seal",)), ceiling=1200,
        embed_client=client, deadline=time.monotonic()+4))
    assert evidence.coverage.semantic == "ok"
    assert evidence.coverage.verdict == "thin"
    assert len(evidence.candidates) == count
    assert sum(c.rounds[0].semantic is not None for c in evidence.candidates) < count


@pytest.mark.parametrize("batch_size", [1, 2000])
def test_cancel_during_materialized_staging_discards_pending_rows(cid, space, cache, tmp_path, monkeypatch,
                                                                 batch_size):
    docs = documents(cid)
    monkeypatch.setattr(compiled, "MAX_STAGED_ROWS", batch_size)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    kind = embedded.vector_kind(space)
    mark = compiled.Batch.mark_materialized
    def paused_mark(self, path, row_kind, *args, **kwargs):
        result = mark(self, path, row_kind, *args, **kwargs)
        if row_kind == kind:
            entered.set()
            assert release.wait(2)
        return result
    monkeypatch.setattr(compiled.Batch, "mark_materialized", paused_mark)
    publish = semantic._publish
    def finished_publish(*args):
        try:
            return publish(*args)
        finally:
            finished.set()
    monkeypatch.setattr(semantic, "_publish", finished_publish)
    async def run():
        task = asyncio.create_task(semantic.score(
            ("silver ledger",), docs, space=space, embed_client=FakeAsyncEmbeddings(),
            deadline=time.monotonic()+4, campaign=cid, scene="999--saltmarch"))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
    asyncio.run(run())
    assert finished.wait(2)
    paths = live.input_paths(cid, docs[0])
    with compiled.batch(tmp_path) as request:
        rows = request.materialized(paths)
        request.disabled = True
    assert not any(row.kind == kind for found in rows.values() for row in found)
