"""Semantic recall: the second stage behind `activate`, for the entries the
keywords missed.

`world_state.activate` decides which world-info entries the model gets to see,
and its rule is lexical — an entry is on if it is keyless, or if one of its
keys appears whole-word in the scan window. The failure mode that rule has is
a *miss*: the scene is about the blade a character's mother left her, the lore
entry keyed ``Sablewrought`` says exactly that, and it stays silent because
nobody typed the word. This module scores those misses against the same scan
window by embedding similarity and hands back the closest few.

## What this layer promises

**It can only add.** `activate` runs the keyword rule first and passes this
module only what that rule rejected, so the recalled entries are appended to
the keyword ones and nothing that used to activate stops activating. With
recall off — the default — the prompt is byte-identical to the one that
shipped before any of this existed.

**It cannot leak owned lore.** Owner gating is a privacy rule: an entry with
owners is silent unless one of them is in the scene. `activate` applies that
gate *before* it builds the candidate list, so an entry whose owner is absent
is never embedded, never scored, and never returned. The rule is structural
rather than repeated here — there is no `owners` check in this file because
there is no code path into it that could need one.

**It fails to keyword-only, never to an error.** No connection, no model, a
dead endpoint, a malformed body, a rate limit, a vector of the wrong
dimensionality: every one of them returns ``[]`` and the turn proceeds on the
keyword activation. A scene is never worth losing to a search index.

**It is bounded, and it gives way first.** `semantic_recall_depth` caps how
many entries it may add and `semantic_recall_threshold` is the cosine floor
each must clear. What it adds renders as its own "Recalled lore" section in
the packer's ``archive`` tier — the first thing dropped when the prompt does
not fit, and separate from the keyword hits on purpose. Sharing their section
would have made "can only add" false under a context budget: the packer drops
sections whole and largest-first within a tier, so a recall could have grown
World info until the whole section went, keyword hits and all.

## Configuration

Four keys in config.md, all off by default:

``embeddings_connection_id``
    An `llm_connections` entry of kind ``openai_compatible``; its ``base_url``
    and ``api_key`` are the endpoint. Reusing a connection rather than adding
    ``embeddings_url``/``embeddings_key`` to config.md is deliberate: config.md
    gave up holding credentials when llm_connections/ took over, and the
    connection store already handles masking, editing, and dropping the key
    when the endpoint is repointed. The other two kinds are rejected —
    OpenRouter and the Claude SDK serve no ``/embeddings`` route, so accepting
    one would only produce a confusing 404 per turn.
``embeddings_model``
    The embedding model id, e.g. ``text-embedding-3-small``.
``semantic_recall_depth``
    Entries a recall may add. 0 (default) disables the layer.
``semantic_recall_threshold``
    Cosine floor, default 0.4. Model-dependent — tune it against the scene
    inspector, which shows what actually activated.

## The cost, stated plainly

`activate` is synchronous, reached from a synchronous `build_messages` that
async route handlers call directly, so a recall blocks the event loop for the
length of its HTTP call. That is why `embeddings.TIMEOUT` is tight, why entry
vectors are cached by content hash (`store/vectors.py`) so the steady state is
one small request per context build, and why the whole layer is off unless
somebody turns it on. Making it non-blocking means making the context builder
async to its roots — a bigger change than the retrieval strategy it would be
serving, and one worth doing only if this proves its worth first.
"""

from __future__ import annotations

import time

from ... import embeddings
from ...llm_errors import LLMError
from .. import config, embed_space, vectors

#: UTF-8 *bytes* of an entry's text that get embedded — not characters.
#: Characters are the wrong unit for a token window: an entry written in CJK
#: is about one token per character, so an 8000-character bound is an
#: 8000-token input against the ~8191-token window the common embedding models
#: have, and the provider rejects it. Bytes bound tokens from above for every
#: script (no tokenizer emits more than one token per byte), so this is ~6000
#: tokens worst case and ~1500 for ordinary English prose — comfortably inside
#: any 8k window, and still far more than an entry body needs.
DOC_BYTES = 6000

#: UTF-8 bytes of the scan window that get embedded — the *last* this many. A
#: long window's recent end is what the next reply answers, so a truncation
#: that dropped it would be scoring against the wrong half of the scene.
QUERY_BYTES = 3000

#: How far outside [-1, 1] a score may land before it is treated as corruption
#: rather than arithmetic. Both operands are unit vectors, so an honest cosine
#: cannot exceed 1; float32 storage moves a self-similarity by ~1e-7.
SCORE_SLACK = 1e-6

#: Entries whose vector may be computed in one turn. The cache warms
#: incrementally: whatever does not fit sits out this turn and is picked up by
#: a later one, so switching the layer on over a campaign with hundreds of
#: keyed entries costs a little more latency per turn for a while instead of
#: one enormous stall.
#:
#: It has to be a bound and not just a batch size. Without it, turning recall
#: on over a large store sends every uncached entry in one `embed` call: many
#: sequential requests with the event loop blocked behind them, and a rate
#: limit anywhere in that run raises before a single vector is saved — so the
#: next turn repeats the whole thing and fails in the same place.
#:
#: Sized so the query and a full warm run stay inside one `embeddings.BATCH`,
#: i.e. one round trip per turn. Raising it past BATCH-1 costs another.
WARM_LIMIT = embeddings.BATCH - 1

#: One process-wide client, for its connection pool: a recall runs on nearly
#: every turn, and a fresh TLS handshake each time is a needless share of a
#: budget that is blocking the event loop. Lazy — constructing this opens
#: nothing.
_CLIENT = embeddings.EmbeddingsClient()


def _clip(text: str, max_bytes: int, tail: bool = False) -> str:
    """`embed_space.clip`, under this module's own name — see there."""
    return embed_space.clip(text, max_bytes, tail)


def _warm_window(uncached: list[str], query_text: str) -> list[str]:
    """The `WARM_LIMIT` texts to embed this turn, rotated by the scan window.

    `embed_space.warm_window` with this layer's bound — the rotation and why it
    has to be a proper subset are documented there. The offset comes from the
    scan window, which differs every turn, so warming moves on by itself while
    staying deterministic for any one turn: the same store and the same scene
    always warm the same entries.
    """
    return embed_space.warm_window(uncached, query_text, WARM_LIMIT)


def _int(value: object, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _float(value: object, default: float) -> float:
    try:
        out = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    # nan fails every comparison, so a hand-edited "nan" threshold would look
    # like "recall nothing" rather than like the mistake it is; inf is a
    # legitimate way to say "never recall", but so is any value above 1.
    return out if -1.0 <= out <= 1.0 else default


def settings() -> dict | None:
    """The resolved recall configuration, or None when the layer is off.

    The endpoint half is `embed_space.resolve`, shared with the search surface
    so both read and write one cache under one namespace. What this adds is the
    part that is recall's alone: `depth`, which is also recall's on/off switch,
    and `threshold`.

    None is the answer for every kind of "not set up" — depth 0, or anything
    that makes `resolve` return None. The caller treats all of them the same
    way, so distinguishing them here would buy nothing; and a store this reads
    may be hand-edited or half-synced, so it must not raise for any of them
    either.
    """
    try:
        cfg = config.read_config()
        depth = max(_int(cfg.get("semantic_recall_depth"), 0), 0)
        if depth <= 0:
            return None
        space = embed_space.resolve(cfg)
        if space is None:
            return None
        threshold = _float(cfg.get("semantic_recall_threshold"),
                           float(config.DEFAULT_SEMANTIC_RECALL_THRESHOLD))
        return {"depth": depth, "threshold": threshold, **space}
    except (OSError, UnicodeDecodeError, KeyError, TypeError, ValueError):
        return None


def entry_text(entry: dict) -> str:
    """What gets embedded for one world-info entry.

    Name, keys, then body. The keys earn their place: they are the author's own
    statement of what the entry is about, and often carry a proper noun the
    body itself uses only once. Including them cannot make an entry match on a
    key literally present in the window — such an entry was a keyword hit and
    never reaches this module.
    """
    parts = [str(entry.get("name") or "").strip(),
             ", ".join(str(k).strip() for k in (entry.get("keys") or []) if str(k).strip()),
             str(entry.get("body") or "").strip()]
    return _clip("\n".join(p for p in parts if p), DOC_BYTES).strip()


def recall(candidates: list[dict], recent_text: str) -> list[dict]:
    """The `candidates` closest to `recent_text`, most similar first.

    `candidates` are the entries the keyword rule rejected *and* the owner gate
    already admitted — see the module docstring. Returns at most `depth` of
    them, each scoring at least `threshold`.
    """
    if not candidates or not recent_text.strip():
        return []
    cfg = settings()
    if cfg is None:
        return []
    texts = [entry_text(e) for e in candidates]
    # Two reasons an entry is not a candidate, and they are different:
    #
    # - nothing to embed. An empty string is what some endpoints reject
    #   outright, and it would fail the whole batch.
    # - nothing to *render*. `recalled_lore.j2` emits the body alone, so an
    #   entry with a name and keys but an empty body is perfectly scorable --
    #   `entry_text` includes the name -- and contributes nothing if it wins.
    #   Left in, it can take a slot from a lower-scoring entry that would have
    #   said something, which at depth 1 means the whole recall.
    scored_idx = [i for i, t in enumerate(texts)
                  if t and str(candidates[i].get("body") or "").strip()]
    if not scored_idx:
        return []
    query_text = _clip(recent_text.strip(), QUERY_BYTES, tail=True)

    wanted = [texts[i] for i in scored_idx]
    known = vectors.load(cfg["space"], wanted)
    # Unique, order-preserving: two entries can share text, and the same text
    # must not be billed twice in one request.
    uncached = list(dict.fromkeys(t for t in wanted if t not in known))
    missing = _warm_window(uncached, query_text)
    got = _embed(cfg, query_text, missing)
    if got is None:
        # The turn proceeds on keyword activation. Deliberately silent: this
        # runs on every turn, and a provider that is down would otherwise fill
        # the log with one identical line per message.
        return []
    query = vectors.unit(got[0])
    if query is None:
        return []
    for text, raw in zip(missing, got[1:]):
        vectors.save(cfg["space"], text, raw)
        fresh = vectors.unit(raw)
        if fresh is not None:
            known[text] = fresh

    hits = []
    for i in scored_idx:
        vector = known.get(texts[i])
        if vector is None:
            continue
        if len(vector) != len(query):
            # The endpoint now answers this model id at a different width than
            # the cached vector was made at. Scoring the overlap would be
            # arithmetic across two unrelated spaces — but skipping alone is
            # not enough: a stale vector is still a cache *hit*, so it would
            # never be re-embedded and its entry would drop out of recall
            # permanently. Evicting it makes next turn a miss, which heals it.
            vectors.forget(cfg["space"], texts[i])
            continue
        score = vectors.dot(query, vector)
        # Both operands are unit vectors, so an honest cosine is in [-1, 1].
        # Outside it means a cache file corrupted in place — which unpacks
        # cleanly, so nothing earlier can catch it — and such a vector would
        # otherwise outrank every genuine hit (a stray 1e20 component scores
        # 1e20). One comparison per candidate, against a re-derived norm per
        # candidate on the read path. See vectors.py.
        if not -1.0 - SCORE_SLACK <= score <= 1.0 + SCORE_SLACK:
            vectors.forget(cfg["space"], texts[i])
            continue
        if score >= cfg["threshold"]:
            # `i` breaks ties by the entry order `activate` was given, so the
            # same store always recalls the same entries in the same order.
            hits.append((-score, i))
    hits.sort()
    return [candidates[i] for _, i in hits[:cfg["depth"]]]


def _embed(cfg: dict, query_text: str, missing: list[str]) -> list[list[float]] | None:
    """Vectors for the query and this turn's warm run, or None if the provider
    could not be reached at all. A `[]` in place of a document's vector means
    "not this turn" — `recall` stores nothing for it and does not score it.

    One request for both. The query is never cached — the scan window differs
    every turn, so storing it would grow the cache without ever being read —
    which leaves this a single small request once the entries have settled.

    On a *response-shaped* failure the query is retried alone, because that is
    what the *already cached* entries need in order to be scored: a warm run
    that fails should cost this turn's warming, not this turn's recall. A
    failure that says the endpoint itself is unavailable gets no retry at all
    (see below). Nothing tries to work out *which* document caused a failed
    batch. That was the job of an isolation pass, and `_warm_window` makes it
    unnecessary — a document that cannot be embedded simply fails again
    whenever a window includes it, and blocks nothing in between.

    Both calls share one deadline. `embeddings.embed` starts a fresh `TIMEOUT`
    when it is not given one, so a retry after a slow failure would otherwise
    cost a second full timeout and this function would be worth twice what the
    constant says.
    """
    deadline = time.monotonic() + embeddings.TIMEOUT
    # `bad_response` unless the provider says otherwise, so a wrong-length reply
    # -- which raises nothing -- is treated as the response-shaped failure it is.
    kind = "bad_response"
    try:
        got = _CLIENT.embed([query_text, *missing], cfg["model"], cfg["key"],
                            cfg["base_url"], deadline=deadline)
        if len(got) == 1 + len(missing):  # defensive: the client promises this
            return got
    except LLMError as exc:
        kind = exc.kind
    except OSError:
        kind = "network"
    if not missing:
        return None  # the query itself is what failed; there is nothing else
    if kind != "bad_response":
        # `auth`, `rate_limit`, `network`: the endpoint is unavailable, not the
        # input. The same answer is coming for the query, so asking costs a
        # redundant request on every context build, doubles the time this turn
        # spends failing, and leans on a provider that may already be
        # throttling. Only a response-shaped failure is worth retrying, because
        # only that one might have been caused by a document in the batch.
        return None
    try:
        got = _CLIENT.embed([query_text], cfg["model"], cfg["key"], cfg["base_url"],
                            deadline=deadline)
    except (LLMError, OSError):
        # The turn proceeds on keyword activation. Deliberately silent: this
        # runs on every turn, and a provider that is down would otherwise fill
        # the log with one identical line per message.
        return None
    return got + [[] for _ in missing] if len(got) == 1 else None
