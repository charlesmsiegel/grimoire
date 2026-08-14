"""The configured embedding endpoint, and the text preparation that feeds it.

Two things in this codebase embed text: `context/semantic.py`, which recalls
lore behind the `activate` seam, and `store/semsearch.py`, which answers the
reader's own query (#34). They ask the same four questions — which endpoint,
which model, which *space* the resulting vectors live in, and how to cut a
text down to something the provider will accept — and they have to answer them
identically, because they share one on-disk cache.

That sharing is the whole reason this module exists rather than each caller
resolving its own connection. A "space" is the namespace a cached vector is
keyed under, and two callers that disagree about it either miss every vector
the other warmed (harmless, expensive) or read a vector made against different
weights (silent, wrong). One definition, in one place.

What is *not* here is policy: how many entries a recall may add, what a search
scores as relevant, how much of a corpus one request may warm. Those differ
between the two callers and belong with them.
"""

from __future__ import annotations

import zlib

from . import config, llm_connections


def resolve() -> dict | None:
    """`{model, base_url, key, space}` for the configured endpoint, or None.

    None is the answer for every kind of "not set up": no model, no connection
    id, an id naming a connection that was deleted, a connection of a kind that
    serves no ``/embeddings`` route, or one with no base URL. Callers treat all
    of them the same way — the layer is off — so distinguishing them here would
    buy nothing. The store this reads may be hand-edited or half-synced, so it
    must not raise for any of them either.
    """
    try:
        cfg = config.read_config()
        model = str(cfg.get("embeddings_model") or "").strip()
        conn_id = str(cfg.get("embeddings_connection_id") or "").strip()
        if not model or not conn_id:
            return None
        conn = llm_connections.read_connection_raw(conn_id)
        if conn["kind"] != "openai_compatible" or not conn["base_url"]:
            return None
        return {"model": model, "base_url": conn["base_url"], "key": conn["api_key"],
                # The cache namespace. Two endpoints can both serve a model
                # called "embedding" and mean different weights, and vectors
                # from different spaces are incomparable even at matching
                # dimensionality -- so keying on the model name alone would
                # reuse one provider's vectors against another's queries and
                # rank silently wrongly.
                #
                # The connection's `rev` is in here for the case the URL does
                # not cover: a gateway where the *credential* selects the
                # tenant or deployment. Two connections to one URL with
                # different keys are different spaces, and replacing a key can
                # move an existing one. `rev` is restamped on every write, so
                # it captures both. It over-invalidates -- renaming a
                # connection costs a full re-embed -- and that is the right
                # direction: re-embedding costs money and latency, while a
                # stale namespace costs silently wrong rankings with nothing
                # to notice them by. `llm_connections.cached_models` gates its
                # own sidecar on `rev` for exactly this reason.
                #
                # `model` stays explicit because it lives in config.md, not on
                # the connection, so changing it does not move `rev`.
                "space": f"{conn['id']}\0{conn['rev']}\0{model}"}
    except (llm_connections.ConnectionNotFound, OSError, UnicodeDecodeError,
            KeyError, TypeError, ValueError):
        return None


def clip(text: str, max_bytes: int, tail: bool = False) -> str:
    """`text` cut to `max_bytes` UTF-8 bytes, from the end when `tail`.

    Bytes, not characters, because characters are the wrong unit for a token
    window: text written in CJK is about one token per character, so an
    8000-character bound is an 8000-token input against the ~8191-token window
    the common embedding models have, and the provider rejects it. Bytes bound
    tokens from above for every script (no tokenizer emits more than one token
    per byte).

    Cutting bytes can split a multi-byte character; the partial one is dropped
    rather than replaced, so the result is always text the provider will accept
    and always shorter than the bound rather than one byte over it.
    """
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    cut = raw[-max_bytes:] if tail else raw[:max_bytes]
    return cut.decode("utf-8", "ignore")


def warm_window(uncached: list[str], query_text: str, limit: int) -> list[str]:
    """The `limit` texts to embed this time, starting at a rotating offset.

    This is the whole answer to a class of failure that took several attempts
    to see as one thing. A *fixed* prefix means whatever sits at the head and
    fails to cache sits there again next time, and forever: a document the
    provider refuses, a document it answers with a zero vector, a document
    caught by a transient outage. Each of those was found and patched
    separately — with an isolation pass, then a bound on that pass, then a
    tombstone for refused inputs — and the tombstone then had its own failure
    mode, permanently rejecting a valid document over a transient 502. The
    mechanism grew more dangerous than what it was guarding against.

    Rotating the window makes all of it transient instead. Nothing can occupy
    the head, because there is no head: a stuck document costs its own slot in
    the windows that happen to include it, and every other document is reached
    within a few rounds regardless. A permanently unembeddable entry settles
    into costing one failed request on the rounds it appears in, and nothing
    else. No failure classification, no persistent state, no second code path.

    The offset comes from the query, so it varies between queries while staying
    deterministic for any one of them — which keeps the caller a pure function
    of its inputs and the cache.

    Convergence is probabilistic rather than ordered, so it is worth having
    measured. With one permanently unembeddable document among N, everything
    else warms within: 32 rounds at N=64, 5 at N=67, 13 at N=83, 16 at N=263,
    142 at N=1063 — the tail is slowest because the last few entries need a
    window that happens to exclude the stuck one. Without a stuck document
    nothing fails and warming is a full window per round as before.
    """
    if len(uncached) < 2:
        return list(uncached)
    # A PROPER subset, always. Taking the whole list once it fits inside
    # `limit` looks harmless and undoes the rotation entirely: the window
    # stops varying, so a stuck document is in every window again and the last
    # `limit` entries never warm. Measured -- the tail never converged.
    size = min(limit, len(uncached) - 1)
    start = zlib.crc32(query_text.encode("utf-8")) % len(uncached)
    return [uncached[(start + n) % len(uncached)] for n in range(size)]
