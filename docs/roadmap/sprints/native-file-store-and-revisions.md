# Human-editable V2 file storage and revisions

**Status:** first slice implemented as `grimoire.store`; contract in [STORAGE.md](../../STORAGE.md). Persistence of the security audit/grants/approvals and of PC delegations/corrections is the next 010 pull request ([remaining obligations](../../STORAGE.md#remaining-010-obligations)).

**Outcome:** Native canonical Markdown/JSON records support definitions and campaign instances independently, with stable references and safe revisioned writes.

## Scope and first proof
Implement a **facet-agnostic** Markdown/JSON record-envelope reader/writer for one authored definition and two independent campaign instances. Round-trip stable source-qualified addresses, owner/source-head/link metadata and unknown authored fields; detect stale expected revisions, broken references and conflicting writes; prove recovery with the Wave 1 fault harness.

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

## Wave 2 generic envelope and ownership rules

The file writer is generic: a validated Wave 1 `SourceRef` or `InstanceRef` address, namespace/scope and owner, format/schema version, content revision, expected write revision, opaque authored payload, and lossless unknown-field preservation. Source heads, source-declared links and broken references must retain attribution and exact spelling after reopening. `010` owns the concrete file schema and its revision/write API, but **must not import 009's entity facet classes** or decide whether a record is a Character, PC, Creature or Item. No character-card field becomes a compulsory low-level store column.

**The first-slice implementation must also:**

- Reject V1 path and symlink/junction traversal **at the write boundary** using `grimoire.library`, including a V2 `worlds` directory that is a link to V1. Revalidate the selected root and every resolved descendant at session start and before publishing. Preserve direct-edit conflict evidence rather than auto-overwriting an authored file.
- Enforce source ownership on import: a source may declare heads/links for its own source only; invalid `declared_by` cannot smuggle another owner's revisions.
- Provide narrow persistence of security grants/approvals/audit and PC delegations/corrections **without** implementing the later permission UI, scene executor or model writer. Use explicit authorization and audit preconditions for mutating operations.
- Run the **actual writer** under `grimoire.testing.explore_crashes` with I/O errors, recovery crashes and adversarial external edits. Test old/new record visibility, missing head, stale compare-and-swap, crash after publication, no V1 write, unknown-field round trip and two divergent campaign instances.
- Define the source-base/revision retention needed later by 019 device sync, 022 snapshots and 042 selective rebase. Do not make sync's vector/concurrent-edit protocol a hidden 010 dependency.

**Parallelism:** 010 accepts opaque payloads and has no dependency on the unfinished 009 data model. 009 independently validates domain facets; Wave 3 consumers join the two through codecs. See [Wave 2 handoffs](../WAVE2_HANDOFFS.md#generic-record-envelope-boundary-009--010).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
