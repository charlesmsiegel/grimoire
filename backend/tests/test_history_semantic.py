"""Bounded semantic recall uses one space and never publishes cancelled work."""

import asyncio
import contextlib
import json
import threading
import time
from dataclasses import replace

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


def test_stalled_call_bounds_deadline_without_tripping_the_memo(cid, space):
    """The phase deadline cutting a slow endpoint is the turn's own clock,
    not an outage: it costs this turn's semantic signal and nothing more, so
    lore recall (with its own, longer budget) is not switched off."""
    docs = documents(cid)
    client = FakeAsyncEmbeddings(delay=10)
    async def run():
        return await semantic.score(("silver ledger",), docs, space=space,
                                    embed_client=client, deadline=time.monotonic()+0.05,
                                    campaign=cid, scene="999--saltmarch")
    start = time.monotonic()
    assert asyncio.run(run()).status == "failed:timeout"
    assert time.monotonic()-start < 0.8
    assert not embed_space.outage(space["space"])


def test_the_door_spending_our_deadline_does_not_trip_the_memo(cid, space):
    """The same, when the client itself stops at the deadline it was handed
    (`embeddings.DEADLINE`); a provider-reported timeout still trips it."""
    docs = documents(cid)
    ours = LLMError("network", "deadline spent", code=embeddings.DEADLINE)
    assert score(cid, space, FakeAsyncEmbeddings(error=ours), docs).status == "failed:network"
    assert not embed_space.outage(space["space"])
    theirs = LLMError("timeout", "provider timed out")
    assert score(cid, space, FakeAsyncEmbeddings(error=theirs), docs).status == "failed:timeout"
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
    # A live set within POOL_MAX is tier 1's pool whole, so the semantic-only
    # scene is found without widening (and a young campaign never widens).
    assert evidence.coverage.semantic == "ok" and evidence.coverage.tiers == (1,)
    assert evidence.candidates[0].scene.sid == docs[0].sid
    assert evidence.candidates[0].admitted_by == frozenset({"semantic"})
    assert evidence.candidates[0].tier == 1


def test_a_first_scene_sends_no_embedding(cid, space):
    """`exhausted` spends nothing further (09-C2): a campaign's first scene has
    no history, so not even the query is embedded."""
    current = played(cid, text="amber seal")
    client = FakeAsyncEmbeddings()
    evidence = asyncio.run(retrieval.retrieve(
        cid, current, model.Query(("amber seal",)), ceiling=1200,
        embed_client=client, deadline=time.monotonic()+1))
    assert evidence.coverage.verdict == "exhausted" and evidence.coverage.tiers == (1,)
    assert evidence.coverage.semantic == "skipped:exhausted"
    assert client.calls == [] and not list(usage.calls(days=1))


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


class _ObservedWriterLock:
    """Expose the real publication wait without holding it across staging."""

    def __init__(self, lock, waiting, timeout):
        self.lock, self.waiting, self.timeout = lock, waiting, timeout

    def acquire(self, *args, **kwargs):
        if kwargs.get("timeout") == self.timeout:
            self.waiting.set()
        return self.lock.acquire(*args, **kwargs)

    def release(self):
        self.lock.release()

    def locked(self):
        return self.lock.locked()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


def test_cancel_while_history_flush_waits_for_writer_discards_rows(cid, space, cache, tmp_path, monkeypatch):
    docs = documents(cid)
    ready, begin, waiting, finished = (threading.Event() for _ in range(4))
    original_lock = compiled._state.lock
    monkeypatch.setattr(compiled, "WRITE_LOCK_SECONDS", 2)
    monkeypatch.setattr(compiled._state, "lock", _ObservedWriterLock(
        original_lock, waiting, compiled.WRITE_LOCK_SECONDS))
    kind = embedded.vector_kind(space)
    flush = compiled.Batch.flush
    def paused_flush(self, *args, **kwargs):
        if not any(row[1] == kind for row in self._materialized.values()):
            return flush(self, *args, **kwargs)
        ready.set()
        assert begin.wait(2)
        try:
            return flush(self, *args, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(compiled.Batch, "flush", paused_flush)
    async def run():
        task = asyncio.create_task(semantic.score(
            ("silver ledger",), docs, space=space, embed_client=FakeAsyncEmbeddings(),
            deadline=time.monotonic()+4, campaign=cid, scene="999--saltmarch"))
        held = False
        try:
            assert await asyncio.to_thread(ready.wait, 2)
            held = original_lock.acquire(blocking=False)
            assert held
            begin.set()
            assert await asyncio.to_thread(waiting.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if held:
                original_lock.release()
            begin.set()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    try:
        asyncio.run(run())
    finally:
        begin.set()
        assert finished.wait(2)
    with compiled.batch(tmp_path) as request:
        rows = request.materialized(live.input_paths(cid, docs[0]))
        request.disabled = True
    assert not any(row.kind == kind for found in rows.values() for row in found)


def test_a_success_clears_an_outage_another_caller_noted(cid, space, monkeypatch):
    """The memo is cleared by a request that answered, not only by time: a
    concurrent caller may note an outage while this one's request is in
    flight, and the endpoint that then answered is available again (6.3).
    Both history and lore recall clear it."""
    docs = documents(cid)
    real_async, real_sync = semantic.embed.embed, lore.embed.embed_sync

    async def racing(*args, **kwargs):
        embed_space.note_outage(space["space"])
        return await real_async(*args, **kwargs)

    monkeypatch.setattr(semantic.embed, "embed", racing)
    assert score(cid, space, FakeAsyncEmbeddings(), docs).status == "ok"
    assert not embed_space.outage(space["space"])
    monkeypatch.setattr(semantic.embed, "embed", real_async)

    def racing_sync(*args, **kwargs):
        embed_space.note_outage(space["space"])
        return real_sync(*args, **kwargs)

    monkeypatch.setattr(lore.embed, "embed_sync", racing_sync)
    monkeypatch.setattr(lore, "_CLIENT", FakeEmbeddings())
    assert lore._embed(space, "silver ledger", [], campaign=cid, scene="") is not None
    assert not embed_space.outage(space["space"])


def test_no_embedding_role_is_structural_and_lexical_tier_one(cid, monkeypatch):
    """Acceptance 2 and 11.3: with no Embedding role retrieval still answers
    from structure and words, says `off`, and never widens."""
    monkeypatch.setattr(embed_space, "_OUTAGES", {}, raising=False)
    old = played(cid, text="Mara studies the silver ledger.")
    absorbed(cid, old)
    sid = played(cid, text="Where is the silver ledger?")
    assert embed_space.endpoint() is None
    client = FakeAsyncEmbeddings()
    ev = asyncio.run(retrieval.retrieve(
        cid, sid, model.Query(("Where is the silver ledger?",), terms=("silver", "ledger")),
        ceiling=1200, embed_client=client, deadline=time.monotonic() + 4))
    assert [i.scene.sid for i in ev.items] == [old]
    assert ev.coverage.semantic == "off" and ev.coverage.tiers == (1,)
    assert client.calls == []


def test_a_stalled_endpoint_costs_one_phase_per_turn_and_keeps_file_evidence(cid, space):
    """10.2 through the turn-path wrapper: a stalled embed costs at most the
    phase deadline, the file evidence survives it, and -- the cut being the
    turn's own clock -- the next turn asks again rather than backing off."""
    from grimoire.routes import history_recall
    old = played(cid, text="Mara studies the silver ledger.")
    absorbed(cid, old)
    sid = played(cid, text="Where is the silver ledger?")
    client = FakeAsyncEmbeddings(delay=10)

    def turn(seconds):
        return asyncio.run(history_recall.gather(
            None, cid, sid, seed="", resolved=None, llm_client=None, embed_client=client,
            deadline=time.monotonic() + seconds))

    start = time.monotonic()
    first = turn(0.3)
    assert time.monotonic() - start < 2
    assert first.coverage.semantic == "failed:timeout"
    assert [i.scene.sid for i in first.items] == [old]
    second = turn(0.3)
    assert second.coverage.semantic == "failed:timeout" and len(client.calls) == 2
    assert [i.scene.sid for i in second.items] == [old]


def test_two_legacy_scenes_sharing_a_document_key_keep_their_own_ranks(cid, space, monkeypatch):
    """Document keys are content digests, so two legacy scenes (no identity)
    can project to one key. Their vector is shared; their semantic ranks,
    admission and completeness are each scene's own."""
    shared = scene.SceneDocument("same-key", "Mara keeps the amber seal.", {})
    docs = tuple(live.LiveDocument(sid, None, shared) for sid in ("001--saltmarch", "002--saltmarch"))
    monkeypatch.setattr(retrieval, "_eligible", lambda *args: docs)
    monkeypatch.setattr(retrieval.structural, "candidates", lambda *args, **kwargs: [])
    current = played(cid, text="amber seal")
    evidence = asyncio.run(retrieval.retrieve(
        cid, current, model.Query(("amber seal",)), ceiling=1200,
        embed_client=FakeAsyncEmbeddings(), deadline=time.monotonic()+1))
    semantic_ranks = {c.scene.key: c.ranks.get((0, "semantic")) for c in evidence.candidates}
    assert semantic_ranks == {"sid:002--saltmarch": 2, "sid:001--saltmarch": 1} or \
        semantic_ranks == {"sid:002--saltmarch": 1, "sid:001--saltmarch": 2}
    assert evidence.coverage.verdict == "exhausted" and evidence.coverage.tiers == (1,)


def _two_docs():
    return tuple(live.LiveDocument(f"00{i}--saltmarch", None,
                                   scene.SceneDocument(f"k{i}", f"Mara ledger {i}", {"identity": f"i{i}"}))
                 for i in (1, 2))


def test_a_stop_before_publication_saves_nothing(cid, space):
    """10.4: a retrieval cancelled before its vectors are published writes
    none -- and so never has anything to withdraw."""
    docs = _two_docs()
    texts = [d.doc.text for d in docs]
    stopped = threading.Event()
    stopped.set()
    got = semantic._publish(docs, docs, texts, [[1.0, 0.0], [0.0, 1.0]], ([1.0, 0.0],), {},
                            space, cid, stopped)
    assert got == ({}, 0)
    assert vectors.load(space["space"], texts) == {}


def test_a_stop_inside_one_campaign_never_saves_the_next(cid, space, monkeypatch):
    """Each campaign's vectors are saved just before its rows are recorded: a
    stop landing while the first campaign records leaves the second unsaved,
    and no row anywhere names a vector that is missing."""
    first, second = _two_docs()
    other = live.LiveDocument(second.sid, None, replace(
        second.doc, meta={**second.doc.meta, "_history_campaign": "elsewhere",
                          "_history_document_key": second.doc.key}))
    docs = (first, other)
    texts = [d.doc.text for d in docs]
    stopped = threading.Event()
    recorded = []

    def record(source, source_docs, *, space, keep):
        recorded.append(source)
        stopped.set()  # the cancel lands while the first campaign records

    monkeypatch.setattr(embedded, "record_embedded", record)
    got = semantic._publish(docs, docs, texts, [[1.0, 0.0], [0.0, 1.0]], ([1.0, 0.0],), {},
                            space, cid, stopped)
    assert got == ({}, 0) and recorded == [cid]
    assert set(vectors.load(space["space"], texts)) == {first.doc.text}


def test_a_cancelled_retrieval_never_deletes_a_concurrent_save(cid, space):
    """Vector keys are shared by text: another retrieval that saved the same
    text keeps it when this one is cancelled, because a cancelled publication
    deletes nothing."""
    docs = _two_docs()
    texts = [d.doc.text for d in docs]
    vectors.save(space["space"], texts[0], [1.0, 0.0])  # the other retrieval's save
    stopped = threading.Event()
    stopped.set()
    semantic._publish(docs, docs, texts, [[1.0, 0.0], [0.0, 1.0]], ([1.0, 0.0],), {},
                      space, cid, stopped)
    assert set(vectors.load(space["space"], texts)) == {texts[0]}


def test_a_save_lands_in_the_library_the_retrieval_read_after_a_root_move(
        cid, space, monkeypatch, tmp_path):
    """A retrieval takes its root before it reads, and a save its thread is
    already making when the root moves still writes there: the vector of a
    text from one library never lands in another's cache."""
    docs = _two_docs()
    texts = [d.doc.text for d in docs]
    read_from = semantic.paths.home()
    moved_to = tmp_path / "another-library"
    monkeypatch.setattr(embedded, "record_embedded", lambda *a, **k: None)
    monkeypatch.setenv("GRIMOIRE_HOME", str(moved_to))  # the root moved mid-retrieval
    semantic._publish(docs, docs, texts, [[1.0, 0.0], [0.0, 1.0]], ([1.0, 0.0],), {},
                      space, cid, threading.Event(), read_from)
    assert not (moved_to / ".cache" / "embeddings").exists()
    monkeypatch.setenv("GRIMOIRE_HOME", str(read_from))
    assert set(vectors.load(space["space"], texts)) == set(texts)


def test_a_held_back_structural_match_is_in_the_semantic_pool(cid, space, monkeypatch):
    """5.4: the pool is cut from the structural list and the admitted lexical
    list. Plain co-presence past STRUCTURAL_PLAIN_CAP ranks without admitting,
    but it is still on the structural list, so semantic may admit it -- here
    at tier 1, with nothing else to widen into."""
    docs = tuple(live.LiveDocument(f"{i:04d}--saltmarch", None,
                                   scene.SceneDocument(f"k{i}", f"Saltmarch scene {i}", {"identity": f"i{i}"}))
                 for i in range(semantic.POOL_MAX + 40))
    old = docs[5]
    held = model.Candidate(
        model.SceneRef(old.sid, "i5"),
        (model.Signals(structural=model.StructuralSignal(
            (model.Relation("shared_cast", ("characters:mara", "pcs:seraphine")),))),),
        {}, 1, frozenset())
    monkeypatch.setattr(retrieval, "_eligible", lambda *args: docs)
    monkeypatch.setattr(retrieval.structural, "candidates", lambda *args, **kwargs: [held])
    current = played(cid, text="amber seal")
    evidence = asyncio.run(retrieval.retrieve(
        cid, current, model.Query(("amber seal",), tier_limit=1), ceiling=1200,
        embed_client=FakeAsyncEmbeddings(), deadline=time.monotonic()+2))
    found = {c.scene.sid: c for c in evidence.candidates}
    assert old.sid in found
    assert found[old.sid].admitted_by == frozenset({"semantic"})
    assert found[old.sid].rounds[0].structural == held.rounds[0].structural
    assert evidence.coverage.tiers == (1,)


def test_the_phase_deadline_cut_files_aborted_not_an_error(cid, space):
    """The phase deadline is the turn's own budget: a request it cuts (the
    door reporting `embeddings.DEADLINE`) files its row `aborted` and puts
    nothing in the error store, as absorb's budget does."""
    from grimoire.store import logs
    docs = documents(cid)
    ours = LLMError("network", "deadline spent", code=embeddings.DEADLINE)
    assert score(cid, space, FakeAsyncEmbeddings(error=ours), docs).status == "failed:network"
    rows = list(usage.calls(days=1))
    assert len(rows) == 1 and rows[0]["status"] == "aborted" and not rows[0].get("error")
    assert logs.read(level="error")["rows"] == []
