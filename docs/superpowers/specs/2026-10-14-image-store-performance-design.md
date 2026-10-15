# Image store performance follow-ups (#477)

Remove repeated filesystem work without changing image identity, legacy metadata
precedence, API output or lock boundaries. All measurements use synthetic stores.

## Requirements

1. The world gallery and its filter derive subject lists and reviewed names from
   one greeting catalog and answer snapshot. Malformed legacy values still count
   as reviewed. Appearances uses its original catalog for subjects and output.
2. Greeting URL rows resolve each local picture once, carrying path and optional
   placement identity together. Share collection-member resolution with the
   collection reader. Keep canonical-index checks, missing/remote URL handling,
   legacy fallback and cross-world behavior. Only the #480 cleanup needed for
   this path belongs here; its other cleanup is outside scope.
3. Character and PC detail reuse their union listing for captions, preserving
   inheritance, campaign shadowing and legacy description precedence.
4. Collection responses derive format and available URLs from one manifest read.
   Usage reads each world's manifests at most once, and skips manifest reading
   when the manifest directory is absent.
5. Library uploads compute format-1 state once under the collection lock and
   reuse it through both guards. Corrupt manifests/journals still fail closed;
   independent guard callers still check state themselves.
6. Saving an identical shared description leaves the object sidecar unchanged.
   Confirmed existing text still permits legacy-key cleanup; a missing object
   does not. Explicit Save clears pending conflicts even when text is equal.
   Preconditions and equality are checked under the existing object stripe.
7. Scope sweeps use compact, stat-validated scope membership to select candidates.
   Reuse statcache.memo_stamped in a separate bounded pool. Cold scans read the
   sidecars; warm scans parse changed and matching objects rather than unrelated
   sidecars. External edits, atomic replacement, store moves, recent stamps and
   unavailable change times follow statcache's existing rules. Both association
   and review-only scopes count. Recheck each candidate fresh under update; no
   cached dict is mutated. This is advisory memory, never a persistent authority.
8. Fork rollback strips the destination scope only once scope copy was entered,
   including partial failures inside it. Earlier failure still removes the owned
   partial directory.

## Validation

Count catalogs, resolutions, manifest reads and writes rather than elapsed time.
Watch the regressions fail before fixing them. Preserve existing behavioral
coverage for snapshot races, invalid data, format-1 guards and partial cleanup.
Add scope-cache external-edit, review-only, root-change and no-op-description
cleanup regressions. Run make check and independent implementation/code-versus-
spec reviews before declaring completion.

Theory: a request carries what it already read; a later request validates what it
remembers against disk. Existing catalogs, listings, locks and statcache provide
those boundaries. A future image surface can reuse these same primitives without
adding an independent source of truth. No dependency or persisted format changes.
