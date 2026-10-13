"""One query request, then bounded evaluation of caller-owned live documents.

Theory: a space and its query directions belong to one retrieval, while document
vectors are shared by exact text. Widening reuses those directions and evaluates
only new documents; admission remains the merger's independent decision.
Workers own cache I/O, and a cancelled publication checks its flag before every
write. No query is persisted and no client or endpoint is constructed here.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from functools import partial

import anyio

from ... import embeddings
from ...llm_errors import LLMError
from .. import embed_space, vectors
from ..inference import embed
from ..searchdocs import embedded
from . import model

POOL_MAX = 64
WIDEN_LIMIT = 1000
SCORE_SLACK = 1e-6


@dataclass(frozen=True)
class SemanticResult:
    signals: dict[str, model.SemanticSignal] = field(default_factory=dict)
    status: str = "off"
    cached: int = 0
    warmed: int = 0
    queries: tuple[list[float], ...] = ()
    # Attempted documents spend the cap even when no usable vector exists.
    # Only `signals` proves a valid cosine, including below-threshold scores;
    # the merger must not call missing/evicted vectors complete coverage.
    evaluated: frozenset[str] = frozenset()
    # Includes misses: widening never opens a text from tier 1 a second time.
    held: dict[str, list[float]] = field(default_factory=dict)
    loaded: frozenset[str] = frozenset()


def _evaluate(docs, queries, held, space_id, stopped):
    out = {}
    for doc in docs:
        if stopped.is_set():
            break
        text = doc.doc.text
        vector = held.get(text)
        if vector is None:
            continue
        if any(len(vector) != len(q) for q in queries):
            vectors.forget(space_id, text)
            held.pop(text, None)
            continue
        cosines = [vectors.dot(q, vector) for q in queries]
        if any(not -1-SCORE_SLACK <= s <= 1+SCORE_SLACK for s in cosines):
            vectors.forget(space_id, text)
            held.pop(text, None)
            continue
        best = max(range(len(cosines)), key=cosines.__getitem__)
        out[doc.doc.key] = model.SemanticSignal(min(1.0, max(-1.0, cosines[best])), best)
    return out


def _publish(docs, score_docs, missing, returned, queries, held, space, campaign, stopped):
    warmed = 0
    for text, raw in zip(missing, returned, strict=True):
        if stopped.is_set():
            return {}, warmed
        unit = vectors.unit(raw)
        if unit is None or any(len(unit) != len(q) for q in queries):
            continue
        if stopped.is_set():
            return {}, warmed
        vectors.save(space["space"], text, raw)
        held[text] = unit
        warmed += 1
    warmed_docs = [d for d in docs if d.doc.text in missing and d.doc.text in held]
    if not stopped.is_set():
        embedded.record_embedded(campaign, warmed_docs, space=space,
                                 keep=lambda: not stopped.is_set())
    return _evaluate(score_docs, queries, held, space["space"], stopped), warmed


def _load(docs, pool, space, texts):
    pool_texts = list(dict.fromkeys(d.doc.text for d in pool))
    wanted = list(dict.fromkeys(d.doc.text for d in docs))
    held = vectors.load(space["space"], pool_texts)
    outside = [t for t in wanted if t not in pool_texts]
    if outside:
        held.update(vectors.load(space["space"], outside))
    misses = [t for t in wanted if t not in held]
    limit = max(0, embeddings.BATCH-len(texts))
    pool_misses = [t for t in pool_texts if t not in held]
    missing = embed_space.warm_window(pool_misses, texts[0], limit) if limit else []
    outside_misses = [t for t in misses if t not in pool_texts]
    if len(missing) < limit:
        missing += embed_space.warm_window(outside_misses, texts[0], limit-len(missing))
    return held, wanted, missing, len(misses)


async def _request(texts, missing, *, space, embed_client, deadline, campaign, scene,
                   post, run_id, cached, uncached):
    attempt = [*texts, *missing]
    returned = []
    try:
        got = await embed.embed(
            "history-recall", attempt, queries=len(texts), space=space,
            client=embed_client, deadline=deadline, campaign=campaign, scene=scene,
            post=post, run_id=run_id, cached=cached, uncached=uncached)
        if len(got) != len(attempt):
            raise LLMError("bad_response", "wrong vector count")
        returned = got[len(texts):]
    except LLMError as exc:
        if exc.kind != "bad_response" or not missing:
            raise
        got = await embed.embed(
            "history-recall", list(texts), queries=len(texts), space=space,
            client=embed_client, deadline=deadline, campaign=campaign, scene=scene,
            post=post, run_id=run_id)
        if len(got) != len(texts):
            raise LLMError("bad_response", "wrong query count") from exc
        missing = []
    return got, missing, returned


async def score(texts, docs, *, space, embed_client, deadline, campaign, scene,
                post=None, run_id="", pool=None) -> SemanticResult:
    """Embed queries and a rotating pool warm once, then score pool documents.

    Only a response-shaped warm failure retries query-only, under the same
    deadline. A phase timeout cancels its call; external cancellation propagates.
    Counts and attribution are filed solely by the production embed meter.
    """
    if time.monotonic() >= deadline:
        return SemanticResult(status="skipped:deadline")
    if space is None or embed_client is None or not texts or not any(texts):
        return SemanticResult()
    if embed_space.outage(space["space"]):
        return SemanticResult(status="backoff")
    score_docs = docs if pool is None else pool
    held, wanted, missing, uncached = await anyio.to_thread.run_sync(partial(
        _load, docs, score_docs, space, texts))
    cached = len(held)
    if time.monotonic() >= deadline:
        return SemanticResult(status="skipped:deadline", cached=cached)
    try:
        with anyio.fail_after(max(0, deadline-time.monotonic())):
            got, missing, returned = await _request(
                texts, missing, space=space, embed_client=embed_client, deadline=deadline,
                campaign=campaign, scene=scene, post=post, run_id=run_id,
                cached=cached, uncached=uncached)
    except (LLMError, OSError, TimeoutError) as exc:
        if getattr(exc, "code", None) == embeddings.NOT_SENT:
            return SemanticResult(status="skipped:deadline", cached=cached)
        kind = exc.kind if isinstance(exc, LLMError) else "timeout" if isinstance(exc, TimeoutError) else "network"
        if kind in embed_space.OUTAGE_KINDS:
            embed_space.note_outage(space["space"])
        return SemanticResult(status="failed:"+kind, cached=cached)
    embed_space.clear_outage(space["space"])
    queries = tuple(vectors.unit(v) for v in got[:len(texts)])
    if any(q is None for q in queries):
        return SemanticResult(status="failed:bad_response", cached=cached)
    directions = tuple(q for q in queries if q is not None)
    stopped = threading.Event()
    try:
        await anyio.lowlevel.checkpoint()
        signals, warmed = await anyio.to_thread.run_sync(partial(
            _publish, docs, score_docs, missing, returned, directions, held, space, campaign, stopped),
            abandon_on_cancel=True)
    except BaseException:
        stopped.set()
        raise
    return SemanticResult(signals, "ok", cached, warmed, directions,
                          frozenset(d.doc.key for d in score_docs), held, frozenset(wanted))


async def widen(result: SemanticResult, docs, *, space, deadline) -> SemanticResult:
    """Evaluate new live documents with the original queries, without a request."""
    if result.status != "ok" or time.monotonic() >= deadline:
        return result
    wanted = list(dict.fromkeys(d.doc.text for d in docs if d.doc.text not in result.loaded))
    held = dict(result.held)
    held.update(await anyio.to_thread.run_sync(partial(vectors.load, space["space"], wanted)))
    stopped = threading.Event()
    try:
        signals = await anyio.to_thread.run_sync(partial(
            _evaluate, docs, result.queries, held, space["space"], stopped),
            abandon_on_cancel=True)
    except BaseException:
        stopped.set()
        raise
    return replace(result, signals={**result.signals, **signals}, held=held,
                   evaluated=result.evaluated | frozenset(d.doc.key for d in docs),
                   loaded=result.loaded | frozenset(wanted))
