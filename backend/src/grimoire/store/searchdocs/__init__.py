"""History SearchDocuments (roadmap 08): a compact, derived document per scene
that historical retrieval (09) can filter, match and embed, and the helper that
turns "this scene was selected" into bounded, keyed transcript evidence.

A document is an index, never a record: it may be deleted at any time, it is
built only from the scene head and the ledgers (never from transcript text), and
nothing reads one to decide a write. The evidence retrieval then shows is read
from the transcript itself, through `expand`, in the regex phase its caller
names.

Deliberately empty of imports, the `continuity` precedent: importers name the
submodule they want (``from .searchdocs import expand``), so importing one never
pulls in the others.

- ``expand`` -- bounded, keyed, phase-correct transcript excerpts (08-C3b).
- ``rolling`` -- a running summary only while the app's intactness test passes.
- ``slices`` -- the campaign's memoized inputs and one scene's slices.
- ``scene`` -- rendering, bounds, metadata and the key (08-C1).
- ``live`` -- the live document set, batch lookup and lazy build (08-C2a).
- ``embedded`` -- the vector kind, `record_embedded` and `vectors_for`, the
  `history-index` call site (08-C2b, 08-C3a).
- ``hook`` -- the `searchdocs` hot rebuild 05's sync runs (08-C2c), wrapped by
  `cache_sync._SearchDocsHook`.
"""
