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

`vectors_for` and the `history-index` task land with 08-S5, behind 05's sync.
"""

from __future__ import annotations

from collections.abc import Sequence

from .. import compiled, vectors
from . import scene as scene_mod

PROJECTION = scene_mod.KIND


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
        rows = [(path, {"campaign": cid, "identity": d.doc.meta.get("identity", "")})
                for d in docs if d.doc.text in held for path in d.paths]
        # 03 stages a bounded number of rows per request; past it a row is
        # dropped silently, so the rows go in batches that fit.
        for start in range(0, len(rows), compiled.MAX_STAGED_ROWS):
            with compiled.batch() as request:
                for path, instance in rows[start:start + compiled.MAX_STAGED_ROWS]:
                    request.mark_materialized(path, kind, instance=instance)
    except (KeyError, TypeError, ValueError, OSError):
        return
