"""Document vectors for scene SearchDocuments: the `materialized` kind and
`record_embedded` (roadmap 08-C2b, the half with no network).

Document vectors live in `store/vectors.py` under `space["space"]` -- the plain
space id of the Embedding role's endpoint -- and the exact document text: the
key every document producer uses, so 08 and 09 address a scene document's
vector the same way and neither derives a key of its own. A re-render that
yields the same text costs no embedding, whatever `BUILD` or
`SEARCH_DOCUMENT_VERSION` did to the document's key.

`record_embedded` writes, for each document whose text has a vector under the
space, a `materialized` row of kind `vector_kind(space)` on EVERY store file
the document read, `instance {"campaign", "identity"}`. 05's hot rebuild
re-embeds only documents with such a row, so whoever embeds a document --
08's hook or 09's turn warm -- records it here. A row left by a deleted scene
names that scene's identity, so a scene that inherits the sid is never
re-embedded because of it.

`vectors_for` embeds documents out of a turn under the `history-index` task
(08-C3a). Its one caller is 05's sync, through the `searchdocs` hook
(`hook.reembed`); a turn's warm run is 09's, under its own task.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ... import embeddings
from ...llm_errors import LLMError
from .. import compiled, embed_space, vectors
from ..inference import embed
from . import live
from . import scene as scene_mod

PROJECTION = scene_mod.KIND
TASK = "history-index"


@dataclass(frozen=True)
class DocVectors:
    vectors: dict[str, list[float]]   # document key -> unit vector, for every one held
    embedded: int                     # vectors saved by this call
    sent: int                         # texts the request carried (0: nothing sent)
    error: str | None                 # the embed failure's kind, if one
    tried: frozenset[str] = frozenset()   # the texts the request carried
    dropped: bool = False             # a reply discarded because `keep` said no


def vectors_for(cid: str, docs: Sequence, *, space: dict,
                client: embeddings.EmbeddingsClient, limit: int,
                keep: Callable[[], bool] = lambda: True) -> DocVectors:
    """Vectors for `docs` (`live.LiveDocument`s) under `space["space"]` and
    each document's exact text -- the key 09's turn warm reads and writes --
    embedding at most `limit` of the texts the cache lacks, in one call.

    The misses are cut on `embed_space.warm_window`, a rotating proper subset,
    so a document the provider refuses never holds the head of every run.
    One `history-index` ledger row, campaign-attributed, no scene: a warm-up
    is not part of playing one. `space` is the caller's, never re-resolved
    here. Never raises: a failure returns what is held, with its kind.

    `keep` is asked once the reply is in, before anything is saved: a sync
    whose store root moved while the request was out passes one that says
    no, since `vectors.save` resolves the root again and would put this
    campaign's vectors in the other library. The reply is then dropped."""
    texts = list(dict.fromkeys(d.doc.text for d in docs))
    held = vectors.load(space["space"], texts)
    misses = [t for t in texts if t not in held]
    window = embed_space.warm_window(misses, cid, limit) if limit > 0 else []
    embedded, error = 0, None
    if window:
        try:
            got = embed.embed_sync("history-index", window, space=space, client=client,
                                   campaign=cid, cached=len(held), uncached=len(misses),
                                   queries=0)
        except LLMError as exc:
            error = exc.kind
        except OSError:
            error = "network"
        else:
            if not keep():
                return DocVectors(vectors={}, embedded=0, sent=len(window), error=None,
                                  tried=frozenset(window), dropped=True)
            if len(got) == len(window):
                for text, vector in zip(window, got, strict=True):
                    vectors.save(space["space"], text, vector)
            else:
                error = "bad_response"
    held = vectors.load(space["space"], texts)
    # Saved means it reads back: `vectors.save` drops a vector with no
    # direction, or a write the cache cannot make, rather than raise.
    embedded = sum(1 for text in window if text in held) if error is None else 0
    record_embedded(cid, docs, space=space)
    return DocVectors(vectors={d.doc.key: held[d.doc.text] for d in docs if d.doc.text in held},
                      embedded=embedded, sent=len(window), error=error,
                      tried=frozenset(window))


def vector_kind(space: dict) -> str:
    """`vector:searchdocs.scene:<space-digest>`, 03-C3's form, the digest
    `compiled.space_digest` gives 03, 05 and 09 alike."""
    return f"vector:{PROJECTION}:{compiled.space_digest(space['space'])}"


def record_embedded(cid: str, docs: Sequence, *, space: dict) -> None:
    """For each of `docs` (`live.LiveDocument`s) whose text now has a vector
    under `space["space"]`, a row on every input path the document read. No
    network; never raises: the rows are advisory, and a failure costs 05 a
    re-embed it would otherwise have skipped."""
    try:
        sid_space = space["space"]
        if not isinstance(sid_space, str) or not sid_space or not docs:
            return
        held = vectors.load(sid_space, list(dict.fromkeys(d.doc.text for d in docs)))
        kind = vector_kind(space)
        rows = list(dict.fromkeys(
            (path, json.dumps(live.row_instance(cid, d.doc.meta.get("identity", ""), path,
                                                live.scene_file(cid, d)), sort_keys=True))
            for d in docs if d.doc.text in held for path in live.input_paths(cid, d)))
        # 03 stages a bounded number of rows per request; past it a row is
        # dropped silently, so the rows go in batches that fit.
        for start in range(0, len(rows), compiled.MAX_STAGED_ROWS):
            with compiled.batch() as request:
                for path, instance in rows[start:start + compiled.MAX_STAGED_ROWS]:
                    request.mark_materialized(path, kind, instance=json.loads(instance))
    except (KeyError, TypeError, ValueError, OSError):
        return
