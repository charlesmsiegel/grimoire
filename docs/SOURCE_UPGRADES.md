# Source upgrades and optional dependencies

Sprint 079's headless `grimoire.sourceupgrades` previews what removing or updating one source
would do, and lets a source declare optional dependencies pinned to the version it was checked
against. Nothing here rewrites a reference, moves a head, deletes a source or calls a model. UI
controls belong to the Wave 9 UI exit; package distribution to 103.

## Removal and update previews

```python
preview_source_removal(store, source)               # the source would be gone (or disabled)
preview_source_update(store, source, candidate)     # the same source id in another library
```

`candidate` is another `LibraryStore`, usually an unpacked update opened with `writable=False`.
Only the named source is read from it; its other sources are ignored. A candidate with no files for that
source refuses (`candidate_missing_source`): previewing a removal is the other call. Disabling a
source has the same impact as removing it. A candidate that holds the source's files but cannot
load it (a malformed manifest) is still previewed: nothing of the source resolves after, and its
load problems are reported on the `after` side. A source that is already absent may be previewed, so
leftover references from a hand deletion are listed.

**What counts as a reference.** Both calls read every canonical file of the library in one locked
pass (`store.scan()` and `store.load()`), except the changed source's own files, which go with it.
Each JSON file is walked like 042's history scan, keys included, as deep as the store's JSON
nesting limit allows (a deeper container would be reported as `too_deep`). A
reference is a string that is exactly:

- a definition ref of the source (`<source>/<kind>/<id>[@<revision>]`): an actor's base, a chat
  binding, a portrayal's derivation, another source's link, a catalog member or dependency;
- an image ref of the source (`<source>#image/<id>`);
- the bare source id under a `source` key: a world attached to a play scope, or (in a top-level
  `optional_dependencies` list) a dependency declaration below;
- the pinned ref at a `resolved_revision` stored beside an unpinned ref of the source: a
  head-following chat binding (107) records the revision it meant, and history replays it.

The scan is raw, so a record shape this version does not know still counts. Prose that merely
contains a reference does not, and neither does a source whose id only starts with, ends with or
is nested under the changed one's (`catalog:cast:extra` is not `catalog:cast`). Two unpinned
positions name an entity without reaching its content: an art pin's layer `entity` (beside the
record's pinned base) and a catalog's `exclusions`. They are listed by existence only (`unchanged`
while the entity has a head on both sides, otherwise `missing`, `still_missing` or `restored`) and
never as `follows_head`, and never in place of a content use of the same text in that file (an
actor that follows its base's head keeps its `follows_head` row beside its art pin). A detached
binding is provenance: its unpinned ref is not listed as following, only the revision it meant.
Every other unpinned ref is listed, so an actor whose `source` follows its head, or a binding detached and reattached with
`follow=True`, shows as `follows_head` even when the same file also holds an older pin.

**How each reference fares.** Every reference is resolved by its exact qualified id on both
sides: a pinned ref at that revision, an unpinned ref at the entity's head, an image or an
attached world by its record's etag, a dependency declaration by the dependency's `source_version`
(below). Nothing resolves on a side where the source does not load, images included. Nothing is matched by display name, slug, list or key order, or a
similarly named entity in another source, so reordering or renaming two catalogs never swaps what
their references reach, and a removed source shows its references as missing, never resolved by
a namesake. `Reference` holds the file `path`, the `record` kind, the `text` as written, `before`
and `after` (revision or etag), and a `status`:

| status | meaning |
|---|---|
| `unchanged` | the same revision with the same bytes, or the same whole-source/image etag |
| `follows_head` | an unpinned ref whose head moves: this record would reach other content without review |
| `edited` | the same revision or image id with other bytes (pins protect against moving heads, not edits) |
| `changed` | an attached world whose manifest changes, or a dependency whose `source_version` changes |
| `missing` | resolves now and would not |
| `still_missing` | resolves on neither side |
| `restored` | broken now and would resolve |

A pinned ref that stays on its revision carries `newer_head` when the candidate's head differs:
an upgrade a human may take deliberately (for an actor, 042's reviewed `SourceRebase`, which
re-pins its chats). The preview never moves a pin. `attention` lists the rows to read first
(`follows_head`, `edited`, `changed`, `missing`, `restored`).

The preview also lists `entities` of the source whose head revision or display name differ
(`added`, `removed`, `moved`, with `renamed`), keyed by qualified id; `edited` revisions whose bytes
differ between the two sides; and `problems`: blocked, unparsable or too deep files of the library
outside the changed source (so `complete` is false and their references are not listed), and
blocked paths and load problems of the changed source itself on either side (`before`/`after`),
including a blocked folder above the candidate's `_source`.
Entities are every entity the loaded index holds for the source, at the head it resolves, whether
or not the manifest declares that head. An image ref whose id no file can hold resolves on
neither side.

A preview is one read, not a lock or an authorization: rerun it after edits. Applying an update
(copying revisions and moving heads) or a removal is not part of this slice.

## Optional dependencies

A source manifest may declare other sources it uses under the extension key
`optional_dependencies`:

```json
"optional_dependencies": [{"source": "catalog:shared", "version": "sha256:..."}]
```

Every declared dependency is optional: its absence never stops the declaring source from loading,
and the references it leaves show as missing in the removal preview. `version` pins the
dependency's `source_version(view, source)` when it was pinned; `null` follows it. That version is
a SHA-256 digest of the dependency's declared heads, its links, and the etag of every entity's head
revision as the index resolves it. A moved head, a changed link or bytes edited at a head revision
change it; the dependency's title, owner, extensions (including its own declarations) and manifest
history do not, so two sources may pin each other. Older, non-head revisions are not part of it:
the update preview covers pins into them.
Declarations are direct only; a dependency's own dependencies are never followed (recursive
dependency graphs are a later decision). At most 32 are allowed; unknown keys, a bad id or etag, a
duplicate and a self-dependency refuse (`invalid_dependency`, `self_dependency`,
`limit_exceeded`).

- `dependency_report(view, source)` reports each as `pinned` (present at its pin), `drifted`
  (present, its version has changed since: preview the update), `following` (no pin) or `missing`.
- `pin_dependency(store, handle, source, dependency, expected=manifest_etag)` declares or re-pins
  one dependency at its current version, in one guarded compare-and-swap of the declaring
  manifest that keeps every other field and extension. The dependency must load. A declaration
  list that is already malformed refuses rather than being silently replaced. Re-pinning after
  reading a preview is the deliberate upgrade of the declaration; it rewrites no reference. An old
  `expected` raises `StaleWrite`, and the store handle authorizes the write as for any manifest.

Older clients keep the extension as an opaque manifest field; removing this reader needs no
rollback writes.

## Evidence and limits

`test_source_upgrades.py` seeds a shared cast with two catalogs, four world portrayals of its Mara,
two world catalogs that list both cast catalogs in opposite orders (one also excluding the cast's
Mara), namesake Maras in two other sources (one stored under the cast's own folder), a pinned chat
whose actor overrides its name, and a following chat in a scope with an attached world. It proves that removing the cast lists every chat, actor, portrayal,
link and catalog reference as missing and names neither namesake; that an update which swaps the
two catalogs' names and members keeps every reference, in either listing order, on its own id
(pinned ones `unchanged` with `newer_head`; the following chat and its actor `follows_head`; the
exclusion not listed); that a lorebook detached and reattached to follow is reported as following;
that bytes edited at a pinned revision and a dropped revision are flagged; that removing a world
reports its scope attachment; that a detached following binding reports the
revision it meant, not a follow; that a dependency row drifts with the version digest (head bytes)
and not with a title; that an art pin never hides its actor's follow; that a stray unstorable image
id, deep nesting, a damaged candidate manifest (whose images then resolve nothing) and a candidate
folder blocked above `_source` do not break a preview or pass silently; and the dependency pin, mutual pins, drift from a moved
head or edited head bytes, re-pin, following and optional-absence behaviors and refusals. Previews leave every library file byte-identical.

Not built: applying an update or removal, rollback of an applied update, a follow-live versus
pin-per-campaign policy beyond 042's pin by default, re-pinning non-actor bindings, recursive
dependencies, campaign ledgers outside `sources/` and `campaigns/` (the scan covers what
`store.scan()` covers), and any UI.
