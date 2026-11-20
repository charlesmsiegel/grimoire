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
Only the named source is read from it; its other sources are ignored. A candidate without that
source refuses (`candidate_missing_source`): previewing a removal is the other call. Disabling a
source has the same impact as removing it. A source that is already absent may be previewed, so
leftover references from a hand deletion are listed.

**What counts as a reference.** Both calls read every canonical file of the library in one locked
pass (`store.scan()` and `store.load()`), except the changed source's own files, which go with it.
Each JSON file is walked like 042's history scan, keys included and up to 64 levels deep. A
reference is a string that is exactly:

- a definition ref of the source (`<source>/<kind>/<id>[@<revision>]`): an actor's base, a chat
  binding, a portrayal's derivation, another source's link, a catalog member or dependency;
- an image ref of the source (`<source>#image/<id>`);
- the bare source id under a `source` key: a world attached to a play scope, or a dependency
  declaration below.

The scan is raw, so a record shape this version does not know still counts. Prose that merely
contains a reference does not, and neither does a source whose id only starts with, ends with or
is nested under the changed one's (`catalog:cast:extra` is not `catalog:cast`). An unpinned mention
of an entity in a file that also pins that entity (an art layer beside an actor's pinned base) is
that pin's identity, not a follow, and is reported only by the pinned row.

**How each reference fares.** Every reference is resolved by its exact qualified id on both
sides: a pinned ref at that revision, an unpinned ref at the entity's head, an image or a whole
source by its record's etag. Nothing is matched by display name, slug, list or key order, or a
similarly named entity in another source, so reordering or renaming two catalogs never swaps what
their references reach, and a removed source shows its references as missing, never resolved by
a namesake. `Reference` holds the file `path`, the `record` kind, the `text` as written, `before`
and `after` (revision or etag), and a `status`:

| status | meaning |
|---|---|
| `unchanged` | the same revision with the same bytes, or the same whole-source/image etag |
| `follows_head` | an unpinned ref whose head moves: this record would reach other content without review |
| `edited` | the same revision or image id with other bytes (pins protect against moving heads, not edits) |
| `changed` | a whole-source ref (an attached world, a dependency) whose manifest changes |
| `missing` | resolves now and would not |
| `still_missing` | resolves on neither side |
| `restored` | broken now and would resolve |

A pinned ref that stays on its revision carries `newer_head` when the candidate's head differs:
an upgrade a human may take deliberately (for an actor, 042's reviewed `SourceRebase`, which
re-pins its chats). The preview never moves a pin. `attention` lists the rows to read first
(`follows_head`, `edited`, `changed`, `missing`, `restored`).

The preview also lists `entities` of the source whose head revision or display name differ
(`added`, `removed`, `moved`, with `renamed`), keyed by qualified id; `edited` revisions whose bytes
differ between the two sides; and `problems`: blocked or unparsable files of the library (so
`complete` is false and their references are not listed) and load problems of the changed source
on either side.

A preview is one read, not a lock or an authorization: rerun it after edits. Applying an update
(copying revisions and moving heads) or a removal is not part of this slice.

## Optional dependencies

A source manifest may declare other sources it uses under the extension key
`optional_dependencies`:

```json
"optional_dependencies": [{"source": "catalog:shared", "manifest_etag": "sha256:..."}]
```

Every declared dependency is optional: its absence never stops the declaring source from loading,
and the references it leaves show as missing in the removal preview. `manifest_etag` pins the
dependency's manifest bytes (its heads and links) when it was pinned; `null` follows it.
Declarations are direct only; a dependency's own dependencies are never followed (recursive
dependency graphs are a later decision). At most 32 are allowed; unknown keys, a bad id or etag, a
duplicate and a self-dependency refuse (`invalid_dependency`, `self_dependency`,
`limit_exceeded`).

- `dependency_report(view, source)` reports each as `pinned` (present at its pin), `drifted`
  (present, its manifest has changed since: preview the update), `following` (no pin) or `missing`.
- `pin_dependency(store, handle, source, dependency, expected=manifest_etag)` declares or re-pins
  one dependency at its current manifest etag, in one guarded compare-and-swap of the declaring
  manifest that keeps every other field and extension. The dependency must exist. Re-pinning after
  reading a preview is the deliberate upgrade of the declaration; it rewrites no reference. An old
  `expected` raises `StaleWrite`, and the store handle authorizes the write as for any manifest.

Older clients keep the extension as an opaque manifest field; removing this reader needs no
rollback writes.

## Evidence and limits

`test_source_upgrades.py` seeds a shared cast with two catalogs, four world portrayals of its Mara,
a world catalog pinning both cast catalogs, namesake Maras in two other sources (one stored under
the cast's own folder), a pinned chat whose actor overrides its name, and a following chat in a
scope with an attached world. It proves that removing the cast lists every chat, actor, portrayal,
link and catalog reference as missing and names neither namesake; that an update which swaps the
two catalogs' names and members and reverses the manifest's head order keeps every reference on
its own id (pinned ones `unchanged` with `newer_head`, the following chat `follows_head`); that bytes
edited at a pinned revision and a dropped revision are flagged; that removing a world reports its
scope attachment; and the dependency pin, drift, re-pin and optional-absence behaviors and
refusals. Previews leave every library file byte-identical.

Not built: applying an update or removal, rollback of an applied update, a follow-live versus
pin-per-campaign policy beyond 042's pin by default, re-pinning non-actor bindings, recursive
dependencies, campaign ledgers outside `sources/` and `campaigns/` (the scan covers what
`store.scan()` covers), and any UI.
