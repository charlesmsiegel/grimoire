"""Semantic search over the library — the reader's half of the vector stack (#34).

The provider (`embeddings.py`), the on-disk cache (`store/vectors.py`) and the
cosine were built for context retrieval: `context/semantic.py` recalls lore the
keyword rule missed, on the model's behalf, on every turn. This module points
the same machinery at the reader's own question — and at the whole corpus
`store/search.py` walks, not only the world-info entries the context builder
cares about.

## The relationship to keyword search

One corpus, two rankings. `search.walk` yields every searchable document
exactly once, under the scope that holds its bytes, and this module embeds
those same documents. So a record findable one way is findable the other, and
`/api/search` can answer a `mode=semantic` request in keyword mode without
having changed the question — which is exactly what it does when there is no
endpoint to embed against (see `Unavailable`).

The envelope, the facet counts and the sort are `search.summarize`'s, so a page
of semantic hits means the same thing as a page of keyword hits: same fields,
same scope rule, same "facets count what dropping the filter would find".

## Passages, not documents

A document is embedded in pieces. Averaging a 40-post transcript into one
vector produces a direction that is close to nothing in particular — the
failure that makes naive semantic search over long text useless — so `passages`
cuts a text at its post markers (the ``**Speaker:** content`` grammar the
transcripts already use) and merges neighbours up to `PASSAGE_BYTES`. A record
is then ranked by its *closest* passage, and its snippet is that passage. Text
with no posts in it is cut on word boundaries at the same bound.

There is deliberately **no cap on how many passages one record contributes**.
An earlier draft capped it, which was wrong twice over: `passages` cuts the
whole text either way, so the cap discarded work already done rather than
avoiding it, and `corpus` then counted what survived the cap — so the page
could report "indexed 40 of 40" while the tail of every long scene had never
entered the corpus at all. A silent truncation dressed as complete coverage.
What bounds the cost is `WARM_LIMIT`, per query, where the reader can see it.

## What it promises

**It refuses rather than fails.** No connection, no model, a dead endpoint, a
rate limit, a malformed body: every one of them raises `Unavailable`, whose
message is written to be shown to the reader, and the route answers in keyword
mode instead. #34 asks for exactly this — semantic mode degrades to keyword,
and the UI says which mode answered.

**It never blocks on indexing the whole store.** Scoring runs against what is
already cached; each query additionally warms `WARM_LIMIT` uncached passages,
on the rotating window `embed_space.warm_window` documents. The response says
how much of the corpus is indexed (`indexed`/`corpus`) so the UI can say so
too, rather than quietly answering from a tenth of the library.

**It shows the reader everything the reader wrote.** Owner gating and `secrecy`
decide what reaches the *model's* prompt; they are not a filter on the author's
own search of their own library, any more than they are in keyword mode. A GM
who cannot find the twist they wrote has been failed by the tool. Making
semantic mode hide records keyword mode returns would also mean the mode toggle
changes which records exist, which is not a ranking control any more.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Iterator

from .. import embeddings
from ..llm_errors import LLMError
from . import embed_space, search, vectors

#: The mode name this module answers to, on the route and in the response.
MODE = "semantic"

#: UTF-8 bytes of one embeddable passage. Sized so an ordinary scene post or a
#: short lore entry is one passage and a long one is a handful, and so a full
#: warm run stays well inside the provider's per-request byte ceiling.
PASSAGE_BYTES = 1500

#: UTF-8 bytes of the query that get embedded. A search box query is short;
#: this is a bound on a paste, not on typing.
QUERY_BYTES = 3000

#: Cosine floor for a hit. Lower than recall's default (0.4): recall is adding
#: entries to a prompt nobody asked for, where a weak match is a cost, while a
#: reader who searched and got nothing would rather see the near misses. Low
#: enough to be forgiving, high enough that an unrelated record does not make
#: the page.
SCORE_FLOOR = 0.25

#: How far outside [-1, 1] a score may land before it is treated as corruption
#: rather than arithmetic — `context/semantic.py`'s constant, for its reason.
SCORE_SLACK = 1e-6

#: Passages one query may embed. Four round trips at `embeddings.BATCH` each:
#: this is a reader waiting on a result they asked for, not a turn's latency
#: tax, so it can afford to be an order of magnitude more than recall's — but
#: not unbounded, because a first query over a large store would otherwise be
#: an unbounded number of sequential requests with nothing saved until the last
#: one returns.
WARM_LIMIT = embeddings.BATCH * 4 - 1

#: One process-wide client, for its connection pool.
_CLIENT = embeddings.EmbeddingsClient()

#: A post boundary in a flattened transcript: `**Speaker:**` at the head of a
#: turn. Bounded name length so a bold run inside prose ("**that** was:**")
#: cannot masquerade as one, and zero-width so the marker stays with the post
#: it introduces.
_POST = re.compile(r"(?=\*\*[^*\n]{1,60}:\*\*)")


class Unavailable(Exception):
    """Semantic search cannot answer, and the caller should degrade.

    The message is reader-facing: it is what the search page shows under the
    "answered in keyword mode" badge, so it says what to do rather than what
    went wrong internally.
    """


def passages(text: str) -> list[str]:
    """`text` cut into embeddable passages, in order.

    Post markers first, byte bound second: a transcript comes apart at its
    speakers and a run of short posts is merged back into one passage, so the
    unit is a scene beat rather than a line. Prose with no markers is cut on
    word boundaries, and nothing is cut mid-word.

    One thing *is* dropped, and only one: the tail of a single "word" longer
    than `PASSAGE_BYTES` — see `_split_long`. That is a pasted blob or a
    corrupt file rather than prose, and the alternative is a request the
    provider refuses.
    """
    text = " ".join((text or "").split())
    if not text:
        return []
    parts = [p.strip() for p in _POST.split(text) if p.strip()]
    out: list[str] = []
    buffer = ""
    for part in parts:
        for piece in _split_long(part):
            if buffer and _size(buffer) + _size(piece) + 1 > PASSAGE_BYTES:
                out.append(buffer)
                buffer = piece
            else:
                buffer = f"{buffer} {piece}" if buffer else piece
    if buffer:
        out.append(buffer)
    return out


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def _key(text: str) -> bytes:
    """A passage's identity, for the two sets that have to span the whole walk.

    A digest rather than the string itself, and for the same reason the walk is
    a generator: a `set[str]` over every passage in the store retains the whole
    corpus in memory for the length of the query, defeating the point of
    yielding one record at a time. 32 bytes per passage instead of its text.
    The cache already keys on sha256 (`vectors.py`), so this is the collision
    assumption that is already load-bearing here, not a new one.
    """
    return hashlib.sha256(text.encode("utf-8")).digest()


def _split_long(text: str) -> list[str]:
    """`text` cut on word boundaries into pieces within `PASSAGE_BYTES`.

    A single word longer than the bound is clipped rather than kept whole: it
    is a pasted blob or a corrupt file, and the provider would refuse the
    request rather than the word.
    """
    if _size(text) <= PASSAGE_BYTES:
        return [text]
    out: list[str] = []
    words: list[str] = []
    used = 0
    for word in text.split(" "):
        cost = _size(word) + 1
        if words and used + cost > PASSAGE_BYTES:
            out.append(" ".join(words))
            words, used = [], 0
        words.append(word)
        used += cost
    if words:
        out.append(" ".join(words))
    return [embed_space.clip(piece, PASSAGE_BYTES) for piece in out]


def _passage_text(name: str, passage: str) -> str:
    """What actually gets embedded for one passage.

    The record's name rides on every passage of it. A passage from the middle
    of a transcript otherwise carries no trace of whose scene it is, and the
    name is the cheapest context there is — it is also what makes a query that
    is simply somebody's name rank that record's passages at all.
    """
    return f"{name}\n{passage}" if name else passage


def _records(scope: str, root: str) -> Iterator[dict]:
    """The corpus, one record at a time, with its passages worked out.

    One walk of `search.walk`, so the two modes cover the same library. A
    generator rather than a list because a query walks this twice (see
    `search_semantic`) and a materialized corpus would be every transcript in
    the store held in memory at once.
    """
    for scope_name, rid, root_name, doc in search.walk(scope, root):
        # The searchable text, not the prose: it carries the frontmatter a
        # record was found by in keyword mode (`keys`, `owners`, `tags`), and a
        # query about what an entry is *for* should reach those too. A record
        # with no text at all is embedded as its own name.
        chunks = passages(doc["text"] or doc["name"])
        if not chunks:
            continue
        yield {"scope": scope_name, "root": rid, "root_name": root_name,
               "doc": doc, "chunks": chunks,
               "texts": [_passage_text(doc["name"], c) for c in chunks]}


def _embed(cfg: dict, query_text: str, missing: list[str]) -> list[list[float]] | None:
    """Vectors for the query and this query's warm run, or None if the endpoint
    could not answer at all.

    One request for both, under one deadline — `embeddings.embed` starts a
    fresh `TIMEOUT` when it is not given one, so a retry after a slow failure
    would otherwise cost a second full timeout.

    On a *response-shaped* failure the query is retried alone, because that is
    what the already-cached passages need in order to be scored: a warm run
    that fails should cost this query's warming, not this query's answer. A
    failure that says the endpoint itself is unavailable gets no retry — the
    same answer is coming, and asking again leans on a provider that may
    already be throttling.
    """
    deadline = time.monotonic() + embeddings.TIMEOUT
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
    if not missing or kind != "bad_response":
        return None
    try:
        got = _CLIENT.embed([query_text], cfg["model"], cfg["key"], cfg["base_url"],
                            deadline=deadline)
    except (LLMError, OSError):
        return None
    return got + [[] for _ in missing] if len(got) == 1 else None


def _empty(q: str) -> dict:
    return {"q": q, "terms": [], "total": 0, "facets": {}, "scopes": {},
            "truncated": False, "hits": [], "indexed": 0, "corpus": 0}


def search_semantic(q: str, *, scope: str = "", root: str = "",
                    kinds: tuple[str, ...] = (),
                    limit: int = search.DEFAULT_LIMIT) -> dict:
    """Rank the library by how close each record's closest passage is to `q`.

    Same filters and same envelope as `search.search`, plus `indexed`/`corpus`:
    how many of the corpus's passages had a vector to score against, out of how
    many there are. Raises `Unavailable` when there is no endpoint to embed
    against or it could not be reached — the caller answers in keyword mode.
    """
    search.validate(scope, kinds)
    cfg = embed_space.resolve()
    if cfg is None:
        raise Unavailable("Semantic search needs an embeddings connection and model, "
                          "set on the Configuration page.")
    query = " ".join((q or "").split())
    if not query:
        return _empty(q)
    space = cfg["space"]

    # Two passes over the corpus, and the reason is memory rather than
    # tidiness: a 1536-dimension vector costs ~50kB as a python list of floats,
    # forty times the passage it stands for, so holding the whole cache at once
    # to score against it is tens or hundreds of megabytes on a store this
    # design otherwise calls small — and this package runs on Android. Pass one
    # reads the cache to find out what is missing and drops every vector as it
    # goes; pass two reads it again and never holds more than one record's
    # worth.
    #
    # The cost, stated at the scale it actually bites rather than a flattering
    # one: `vectors.py` measured 58ms per 500 vectors, so a warm store of 5000
    # passages spends about 1.2s per query reading its cache twice. That is a
    # deliberate, debounce-free action by a reader who is also waiting on an
    # HTTP round trip, so it is affordable — but it is the reason this is
    # bounded by what a person searches for rather than by what a turn costs.
    # The cheaper shape (probe for existence in pass one, read only in pass
    # two) was rejected: a file that exists and fails its checksum has to count
    # as missing, or it is never re-embedded and its record is unscorable for
    # good — the exact permanent-stuck failure `warm_window` exists to avoid.
    uncached = _uncached(space, _records(scope, root))
    query_text = embed_space.clip(query, QUERY_BYTES)
    missing = embed_space.warm_window(uncached, query_text, WARM_LIMIT)
    del uncached

    got = _embed(cfg, query_text, missing)
    if got is None:
        raise Unavailable("The embeddings endpoint could not be reached. "
                          "Check the connection on the Configuration page.")
    query_vector = vectors.unit(got[0])
    if query_vector is None:
        raise Unavailable("The embeddings endpoint returned no usable vector "
                          "for that query.")
    # Saved, not kept: pass two reads them back off disk with everything else,
    # so a vector that failed to save (read-only store, full disk) is simply
    # not counted as indexed rather than scored from memory this once and
    # missing on the next query.
    for text, raw in zip(missing, got[1:]):
        vectors.save(space, text, raw)
    del missing, got

    matched: list[dict] = []
    counted: set[bytes] = set()
    indexed = 0
    for rec in _records(scope, root):
        cached = vectors.load(space, rec["texts"])
        for text in rec["texts"]:
            if _key(text) not in counted:
                counted.add(_key(text))
                indexed += text in cached
        best = _best_passage(space, cached, query_vector, rec)
        if best is None or best[0] < SCORE_FLOOR:
            continue
        score, index = best
        doc = rec["doc"]
        matched.append({"scope": rec["scope"], "root": rec["root"],
                        "root_name": rec["root_name"], "kind": doc["kind"],
                        "id": doc["id"], "sub": doc["sub"], "name": doc["name"],
                        "score": score,
                        # Framed with no terms, so the window is the head of the
                        # passage that matched -- which is the evidence a
                        # semantic hit has to offer, there being no term in it
                        # to point at.
                        "snippet": search.snippet(rec["chunks"][index], "", [])})

    return {"q": q, "terms": [], **search.summarize(matched, kinds, limit),
            "indexed": indexed, "corpus": len(counted)}


def _uncached(space: str, records: Iterator[dict]) -> list[str]:
    """The corpus's passages that have no vector yet, in walk order, deduped.

    Reads the cache and keeps none of it — see `search_semantic`. Existence
    would be cheaper than a read, but a file that exists and fails its checksum
    has to count as missing, or a corrupted vector would never be offered for
    re-embedding and its record would be unscorable for good.
    """
    seen: set[bytes] = set()
    out: list[str] = []
    for rec in records:
        cached = vectors.load(space, rec["texts"])
        for text in rec["texts"]:
            key = _key(text)
            if key in seen:
                continue
            seen.add(key)
            if text not in cached:
                out.append(text)
    return out


def _best_passage(space: str, known: dict, query_vector: list[float],
                  rec: dict) -> tuple[float, int] | None:
    """(score, passage index) for the record's closest passage, or None when
    none of them has a usable vector yet.

    A cached vector of the wrong width is evicted rather than skipped: the
    endpoint has begun answering this model id at a different dimensionality,
    and a stale vector is still a cache *hit*, so skipping alone would leave
    the record permanently unscorable. So is one scoring outside [-1, 1], which
    can only be a cache file corrupted in place — `vectors.py` explains why
    that is checked at all.
    """
    best: tuple[float, int] | None = None
    for index, text in enumerate(rec["texts"]):
        vector = known.get(text)
        if vector is None:
            continue
        if len(vector) != len(query_vector):
            vectors.forget(space, text)
            continue
        score = vectors.dot(query_vector, vector)
        if not -1.0 - SCORE_SLACK <= score <= 1.0 + SCORE_SLACK:
            vectors.forget(space, text)
            continue
        if best is None or score > best[0]:
            best = (score, index)
    return best
