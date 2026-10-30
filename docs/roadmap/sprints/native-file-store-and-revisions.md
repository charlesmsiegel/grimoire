# Human-editable V2 file storage and revisions

**Outcome:** Native canonical Markdown/JSON records support definitions and campaign instances independently, with stable references and safe revisioned writes.

## Scope and first proof
Persist one definition and two divergent campaign instances; reopen them; preserve unknown authored fields; reject a stale write.

## Subsequent milestones and moonshot
Versioned snapshots and change journals, atomic replacement, external edit handling and guarded cross-file mutations.

## Independence and interfaces
Canonical storage package, not graph indexes or the image blob store; requires V2 path and identity contracts.

## Proving scenario
A world change doesn't silently rewrite a pinned campaign instance; a failed write doesn't masquerade as success.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- Create and write libraries only under a root returned by `check_write_destination`, following the writer obligations in [Architecture](../../ARCHITECTURE.md#library-location-and-v1-isolation-implemented-grimoirelibrary). Map a `grimoire.security.ResourcePath` to disk only by joining its segments under that root and never through a link: a V2 root holding `worlds -> <V1 library>/worlds` must not be written through (001, 007).
- Write references only in the [identity](../../IDENTITY.md) text forms. A loaded package may contribute definitions, heads and links only under its own source id: the in-memory index trusts the `source` and `declared_by` it is given (002).
- `AuthorityError` and `LibraryPathError` are refusals, not I/O errors to retry.
- Refuse to create a name that collides case- or normalization-insensitively with a differently spelled existing entry (007).
- Persist the `grimoire.security` decision audit and the grant/approval lifecycle, and the delegations and corrections of [player sovereignty](../../PLAYER_SOVEREIGNTY.md) (003, 007).

**First-proof additions**
- Persist source owners, declared heads and links (with `declared_by`); reopening after an add-on is removed keeps its references byte-identical and reports `missing_source` (002's "retain origin, version, owner and broken-reference information in human-editable files").
- Prove the writer with the [fault harness](../../TESTING.md) (`explore_crashes(..., errors=True, recovery_crashes=True)`), replacing the harness's synthetic reference writers (006).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
