"""Ranking the corpus: the lexical baseline, the embedded rankings (through
the production door, `store.inference.embed.embed_sync`), the offline
hashed bag-of-words endpoint, recordings, and the live run.

**Replay is offline and in `make check`** (`backend/tests/test_embed_evals.py`):
the recordings under `evals/recordings/embed/` are graded, ids only -- no
vector is ever checked in. A second offline case runs the whole embedded
path over an `httpx.MockTransport` whose endpoint is a deterministic hashed
bag of words (`bow_handler`): it proves the harness, the prefixes, the
`param` split and the dimensions check end to end, and claims nothing about
quality.

**Live** (`evals/run.py --live --embed`) costs money and is opt-in. The space
is resolved from the REAL store before any isolate (`resolve_live`); inside
the throwaway home nothing re-resolves, so the corpus's vectors never reach
the user's cache, and every row is filed in the isolate and harvested into
the report (`evals.runner.harvest`) -- the tripwire refuses a run whose
active home is the real one. `--embed-options` runs the same corpus under
each option set in memory (`with_options`), each validated as a facts write
would be and with its space id recomputed, so no two sets share a
`space_id`. The calls are made outside any coroutine; the loop guard refuses
only the app's loop anyway.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from grimoire import embeddings, wire
from grimoire.store import embed_space, search, tokens
from grimoire.store.inference import embed, facts
from grimoire.store.inference import resolve as inference_resolve

from .corpus import SHAPES, Corpus, Query
from .metrics import KS

#: Where recorded rankings live: `<model-slug>-<option digest or "none">.json`.
RECORDINGS = Path(__file__).resolve().parents[1] / "recordings" / "embed"

#: How deep a ranking is kept: the largest k.
DEPTH = max(KS)

#: The offline endpoint's model and width.
BOW_MODEL = "bow-1"
BOW_DIMS = 64


# ---- ranking -----------------------------------------------------------------

#: Decimal places a score is compared at. Scores that are equal in exact
#: arithmetic (two documents sharing the same words) can differ in their last
#: bit, and that bit must never order them: `sum` over floats rounds
#: differently on 3.11 than on 3.12+, so an unrounded tie would rank one way in
#: CI's 3.11 job and the other in 3.14's.
SCORE_PLACES = 9


def _rank(scores: dict[str, float]) -> list[str]:
    """Document ids, best first; scores compared at `SCORE_PLACES` and ties
    broken by id, so a ranking is the same on every Python version."""
    return [doc for doc, _s in sorted(scores.items(),
                                      key=lambda kv: (-round(kv[1], SCORE_PLACES), kv[0]))][:DEPTH]


def lexical_rankings(corpus: Corpus) -> dict[str, list[str]]:
    """The baseline: `store.search`'s term scorer over the same corpus -- what
    a reader's search would rank, with no embedding at all."""
    out = {}
    for q in corpus.queries:
        terms = search.query_terms(q.text)
        out[q.id] = _rank({d.id: search._score("", d.text, terms) for d in corpus.documents})
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    # `math.fsum`, never `sum`: it is correctly rounded on every version.
    dot = math.fsum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(math.fsum(x * x for x in a))
    nb = math.sqrt(math.fsum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def embedded_rankings(corpus: Corpus, *, space: dict, client: embeddings.EmbeddingsClient,
                      run_id: str = "") -> dict[str, list[str]]:
    """Every query ranked against the whole pool by cosine, through
    `embed_sync`. One call per shape, under that shape's real embed task, as
    production shapes them: the shape's queries first (`queries=` their count,
    or 0 for the symmetric identity shape, whose query is a reworded record),
    then the shape's documents -- the fillers ride the search shape's call --
    so every document is embedded once, as a document."""
    shaped: dict[str, list] = {shape: [] for shape in SHAPES}
    for d in corpus.documents:
        shaped["search" if d.shape not in SHAPES else d.shape].append(d)
    doc_vectors: dict[str, list[float]] = {}
    query_vectors: dict[str, list[float]] = {}
    for shape, (task, as_query) in SHAPES.items():
        queries: list[Query] = [q for q in corpus.queries if q.shape == shape]
        docs = shaped[shape]
        texts = [q.text for q in queries] + [d.text for d in docs]
        vectors = embed.embed_sync(task, texts, space=space, client=client,
                                   queries=len(queries) if as_query else 0, run_id=run_id)
        for q, v in zip(queries, vectors, strict=False):
            query_vectors[q.id] = v
        for d, v in zip(docs, vectors[len(queries):], strict=True):
            doc_vectors[d.id] = v
    return {q.id: _rank({doc: _cosine(query_vectors[q.id], v)
                         for doc, v in doc_vectors.items()})
            for q in corpus.queries}


# ---- the offline endpoint ----------------------------------------------------

_TOKEN = re.compile(r"[a-z]+")


def bow_vector(text: str, width: int = BOW_DIMS) -> list[float]:
    """A deterministic hashed bag of words, unit length: each lower-cased
    word adds one to the component its SHA-256 names."""
    out = [0.0] * width
    for word in _TOKEN.findall(text.lower()):
        out[int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big") % width] += 1.0
    norm = math.sqrt(math.fsum(x * x for x in out)) or 1.0
    return [x / norm for x in out]


def bow_handler(*, seen: list[dict] | None = None, dimensions_field: str = "dimensions",
                honour_dimensions: bool = True) -> Callable[[httpx.Request], httpx.Response]:
    """An OpenAI-shaped `/embeddings` endpoint answering `bow_vector`s: as wide
    as a requested `dimensions_field` when it honours one, else `BOW_DIMS`
    (a width-ignoring endpoint). Records each request body in `seen`."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if seen is not None:
            seen.append(body)
        width = body.get(dimensions_field) if honour_dimensions else None
        width = width if isinstance(width, int) and width > 0 else BOW_DIMS
        inputs = body["input"]
        return httpx.Response(200, json={
            "model": body["model"],
            "data": [{"index": i, "embedding": bow_vector(t, width)}
                     for i, t in enumerate(inputs)],
            "usage": {"prompt_tokens": sum(len(_TOKEN.findall(t.lower())) for t in inputs)}})
    return handler


def bow_client(**kwargs) -> embeddings.EmbeddingsClient:
    """A client whose endpoint is `bow_handler(**kwargs)`."""
    return embeddings.EmbeddingsClient(
        httpx.Client(transport=httpx.MockTransport(bow_handler(**kwargs))))


def bow_space(options: wire.EmbedOptions | None = None) -> dict:
    """The offline endpoint as an endpoint dict, its space computed by the
    production `space_of` from the options it carries."""
    options = options or wire.EmbedOptions()
    return {"model": BOW_MODEL, "base_url": "https://bow.invalid/v1", "key": "",
            "space": inference_resolve.space_of({"id": "bow", "rev": "r0"}, BOW_MODEL, options),
            "provider": "bow", "provider_name": "bag of words",
            "provider_kind": "openai_compatible", "options": options}


# ---- option sets -------------------------------------------------------------

def options_of(block: object) -> wire.EmbedOptions:
    """An `--embed-options` set, validated as a facts write is
    (`facts._check_embedding`): a `ValueError` naming the problem."""
    return facts.embed_options({"embedding": block}) or wire.EmbedOptions()


def with_options(space: dict, options: wire.EmbedOptions) -> dict:
    """`space` under other options, in memory: its id recomputed by
    `space_of` from the same connection id, rev and model, so capture lines
    and rows of two option sets never share a `space_id`. Nothing is
    written."""
    base = space["space"].split(f"\0{wire.EMBED_OPTIONS_TAG}:", 1)[0]
    conn_id, rev, model = base.split("\0", 2)
    return {**space, "options": options,
            "space": inference_resolve.space_of({"id": conn_id, "rev": rev}, model, options)}


def resolve_live() -> dict:
    """The Embedding role's endpoint, read from the REAL store (call it before
    any isolate). A `RuntimeError` when the role names no space."""
    space = embed_space.endpoint()
    if space is None:
        why = embed_space.problem(None) or "the Embedding role is off"
        raise RuntimeError(f"no embedding space to evaluate: {why}")
    return space


def estimate(corpus: Corpus) -> tuple[int, int]:
    """`(bytes, tokens)` the corpus sends, before any prefix: what a live run
    prints before it sends anything. Tokens by a loaded encoder, else the
    characters/4 heuristic (`tokens.count_if_loaded`)."""
    texts = [q.text for q in corpus.queries] + [d.text for d in corpus.documents]
    return (sum(len(t.encode("utf-8")) for t in texts),
            sum(tokens.count_if_loaded(t) for t in texts))


# ---- recordings --------------------------------------------------------------

def recording_name(model: str, options: wire.EmbedOptions) -> str:
    """`<model-slug>-<option digest or "none">`: named by model and options,
    never by connection id."""
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-") or "model"
    return f"{slug}-{'none' if options.is_default() else options.digest()}"


def write_recording(name: str, model: str, options: wire.EmbedOptions,
                    rankings: dict[str, list[str]], root: Path = RECORDINGS) -> Path:
    """Save `rankings` (ids only) as `<root>/<name>.json`."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.json"
    doc = {"model": model, "options": json.loads(options.canonical()),
           "rankings": {q: list(r) for q, r in sorted(rankings.items())}}
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_recordings(root: Path = RECORDINGS) -> list[tuple[str, dict[str, list[str]]]]:
    """Every recording under `root`: `(name, rankings)`, by name."""
    out = []
    for path in sorted(root.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        out.append((path.stem, {q: list(r) for q, r in doc["rankings"].items()}))
    return out


def timed(fn: Callable[[], dict[str, list[str]]]) -> tuple[dict[str, list[str]], int]:
    """`fn()` and how long it took, in ms."""
    started = time.monotonic()
    got = fn()
    return got, int((time.monotonic() - started) * 1000)


def live_client() -> embeddings.EmbeddingsClient:
    """The client a live run embeds through (a test's seam)."""
    return embeddings.EmbeddingsClient()
