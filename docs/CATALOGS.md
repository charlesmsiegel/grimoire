# Optional catalog composition

Sprint 056's headless `grimoire.catalogs` adapter provides reference-only catalog
composition, bounded discovery and explicit selection. Worlds and world-less
chats can reuse the same authored character/lore revisions without copying them.
UI controls belong to 115; package distribution to 103 and reviewed upgrades to
079. A catalog is optional: existing direct-card chats and self-contained worlds
continue unchanged.

## Authoring and persistence

A catalog is an ordinary source-owned definition, with kind `catalog`, a pinned
`SourceRef`, and envelope schema `grimoire.catalog/1`. Its payload has exactly:

```json
{
  "members": ["catalog:shared/character/mara@r1"],
  "dependencies": ["catalog:shared/catalog/lore@r1"],
  "exclusions": ["catalog:shared/item/gear"]
}
```

All three fields are required lists. Members and dependencies must be pinned;
dependencies name catalogs and members name other definition kinds. Exclusions
are unpinned qualified member identities, applying to every revision of that
entity. Duplicate references, multiple revisions of one entity in a list,
unknown keys and unsupported schema versions refuse. Names never establish
identity or inheritance. The catalog contains no copies of member payloads.

Construct `Catalog(ref, members=(), dependencies=(), exclusions=())`, then use
`publish_catalog(store, handle, catalog, expected=source_manifest_etag, name=None)`.
The source must already exist. Publication validates composition and uses the
existing canonical transaction to create the immutable revision and update its
source head together, preserving manifest links, owner, title and extensions.
All writes require the existing store authority handle. No implicit migration,
source creation, world promotion, actor creation or model call occurs.

Retries are strict: an old manifest etag raises `StaleWrite`; reusing an existing
revision with a refreshed etag still refuses its create-only write. After a lost
response, reread to reconcile; retrying never invents a new revision or moves a
head backward. Crash recovery belongs to the existing store journal. Older
clients retain these opaque generic records; removing this reader requires no
rollback writes to definitions or chats.

## Dependency and exclusion rules

`compose_catalogs(view, roots)` takes one loaded `LibraryView` and explicit pinned
catalog roots. Dependencies compose first. At each node, union dependency members
with local members, remove that node's exclusions, then reject conflicting
surviving revisions and missing surviving members. There is no last-writer-wins.
Identical references deduplicate and retain their declaring catalog provenance.

Exclusions are node-local. If B excludes a member from A, but sibling C includes
A without that exclusion, a parent of B and C sees the member from C. Exclude it
at their common parent to suppress it there. A nonexistent exclusion is a valid
persistent opt-out. Exclusions apply to local additions too. A parent cannot
repair an already-invalid dependency: a missing member or conflicting pins inside
a child fail even if the parent would exclude that entity. Missing dependencies
always fail; locally excluded missing members need not resolve.

Availability is not access control or character knowledge. Excluding a base
character from discovery does not prevent an explicitly selected derived portrayal
from composing its pinned base. [Effective views](EFFECTIVE_VIEWS.md) continue to
own inherit/replace/clear and field provenance. A source catalog has no campaign
memory, controller assignment or knowledge grant.

## Discovery, selection and integration

The immutable result contains:

- `entries`: qualified pinned ref, display name (at most 256 characters), member
  etag and unique declaring `CatalogOrigin(ref, etag)` values;
- `basis`: the visited pinned catalog refs and their etags;
- `search(query='', limit=20)`: a tuple sorted by qualified ref text, matching
  casefolded name or ref text. Query length is at most 256 and limit is 1–100;
- `select(sources, world=None)`: explicit `SelectedSource` values converted to an
  existing `SourceSelection`. Every chosen ref must be pinned and available.
  Existing selection validation owns roles and actor conflicts.

Selection never attaches the whole catalog. Pass only deliberately selected refs
to the existing `ChatLibrary.start_chat` / attachment APIs, which own actor
validation and durable bindings. For example, select a character and lorebook,
launch from the character and attach that lorebook. A new chat still creates an
independent play scope with `world=None`; worlds may instead own catalog records
that depend on optional shared packs. This adapter does not infer a world's root
catalog or silently add its contents to existing chats.

Pinned references protect against moving heads, not human edits to bytes at the
same reference. Etags record what discovery actually read. Results are operation-
local views, not authorization tokens. Rediscover after edits; durable chat
attachment uses its existing fresh-load validation, without an atomic hold on
catalog discovery etags. Similarly, publication validates dependencies in a
loaded view but does not lock their bytes against external manual editing.

Composition refuses rather than returning partial results when it exceeds 32
roots, 16 levels, 128 node expansions (repeat diamond visits count), 20,000
member/dependency/exclusion edges or 10,000 surviving members per node/result.
Origins are unique declaring nodes, not exponentially many traversal paths.
Selection is capped at 32 entries. These are explicit safety policies, not
measured capacity claims. Loading the underlying library still reads its existing
records; these limits do not turn the store loader into a lazy filesystem index.

`CatalogError.reason` identifies validation failures (`invalid_catalog`,
`invalid_reference`, `unpinned_reference`, `invalid_catalog_reference`,
`invalid_member`, `invalid_dependency`, `invalid_exclusion`, `pinned_exclusion`,
`duplicate_reference`, `unsupported_schema`, `invalid_record`, `invalid_name`),
composition failures (`missing_catalog`, `missing_member`, `cycle`,
`conflicting_revision`, `limit_exceeded`), or discovery/selection failures
(`invalid_search`, `invalid_selection`, `not_available`). Existing store and
`SourceSelection` errors remain their own contracts.

## Evidence and limits

`test_catalog_scenarios.py` exercises three synthetic world catalogs sharing
character/lore references, a world-specific gear exclusion and creature addition,
pinned portrayal overrides, separate world-less chat actors, and unchanged actual
context packs after adding 2,000 unrelated catalog members. Context costs are the
existing deterministic estimates, not provider-reported measurements. Catalog
membership alone never enters the prompt. `test_catalogs.py` covers identity,
exclusions, dependency failures and traversal limits. `test_catalog_store.py`
covers stale/replayed/unauthorized writes; `test_catalog_recovery.py` crashes before
and after each canonical target replacement and checks atomic, repeatable recovery.

This is a headless first slice, not the Wave 8 UI exit, content distribution,
automatic catalog attachment, follow-head source upgrading, or a new chat actor-
kind policy. The existing 033 owner remains responsible for actor-kind validation.
