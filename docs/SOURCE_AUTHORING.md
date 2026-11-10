# Source authoring (109)

`grimoire.sourceapi` is what the app's Library pages call. It lets a person browse, read, create and edit the
library's source definitions, derive portrayals and attach an exact revision to a chat. It is trusted application
code acting for the library's **owner**, like 044's [text API](TEXT_API.md). Before 109, V2 had no source
editor: only the importers (026, 037) wrote definitions. This module is the narrow, authorized adapter that the
[UI coverage contract](roadmap/UI_COVERAGE.md) asks Sprint 109 to add. It writes nothing that a domain service does
not already define:

- **Revisions are immutable** (010). Every edit writes a *new* revision `r<n+1>` and moves the entity's head in the
  source manifest, in one guarded transaction. The manifest is written at the etag the editor read, so an edit made
  from a stale read is refused and writes nothing.
- **A derived portrayal** is 022's delta: a payload `derivation` with a pinned base, plus a `derived_variant` link
  that the portrayal's own source declares for its exact revision. One-field overrides, typed clears and resets are
  046's pure helpers ([INHERITANCE](INHERITANCE.md)); what a revision composes to, field by field, is 046's
  inspection.
- **Attaching** is 107's `ChatLibrary.attach_source`, always with a pinned ref. A later revision reaches the chat
  only through 042's reviewed rebase.

Nothing is matched by name, no model is called and no campaign record is written.

```python
api = SourceApi(store, authority, owner="human:owner")
read = api.call("sources.read", {"ref": "catalog:gothic/character/mara"})
api.call("sources.save", {"ref": read["entity"], "head": read["head"], "expected": read["manifest_etag"],
                          "fields": {"description": "A lamplighter of the quay."}})
```

## Families

A record's family comes from its schema:

| Family | Schema | Kinds | Structured fields |
|---|---|---|---|
| entity | `grimoire.entity/0` | `character`, `location`, `item`... any kind but the two below | `name`, `nature` (`individual` or `template`), `description`, `sections` (name → text), `traits` (a list of facet objects) |
| scenario | `grimoire.scenario/1` | `scenario` | `title`, `premise`, `openings` (a list of `{id?, text}`), `instructions` |
| lorebook | `grimoire.lorebook/1` | `lorebook` | `name`, `description`, `entries` (a list of `{index?, keys, content, constant?, enabled?}`) |
| other | anything else | | raw editor only |

A world is a source (`world:...`), not a record. Its locations, items and characters are entities in it.

**Unknown fields are kept.** A structured edit starts from the head's payload and changes only the keys it names.
An opening keeps its other keys by `id`, and a lorebook entry that names the `index` of an entry in the head keeps
that entry's settings, extensions and extras. The **raw editor** (`sources.save_raw`) writes a payload exactly as
given, in the record's own schema. An entity payload must still decode (009), and a scenario's lorebook must still
be a pinned lorebook ref.

A blank description or an empty section in the structured editor is left out of the payload. On a **portrayal**
the structured editor goes through 046's helpers: a description or section it is given is an override (which also
lifts a clear of it), and a blank one, or a local section left out, inherits again. A clear is always the typed
`clear_*` edit, never a blank field.

**Every entity revision must compose.** Before anything is written, the new revision is composed (022) over the
library as it would be. A head that could not be played is refused with 022's reason: a derivation base that does
not exist or is not written yet (`missing_base`), a field both cleared and set (`clear_and_set`), another nature
than the base's (`nature_mismatch`), a chain that is too deep, a malformed derivation.

## Ops

Every ref is passed and returned in its own string form, so a client hands it back unchanged. Every write names
`expected`: the etag of the source manifest that the client read. Edits of an existing entity also name `head`,
the revision they replace.

| Op | Arguments | Result |
|---|---|---|
| `sources.catalog` | `query?`, `kind?`, `source?`, `limit?` (1 to 1000, default 200) | `sources` (each with title, owner, namespace, etag, entity count), `entries` (each entity's unpinned ref, kind, head, name, family, whether it is derived, any problem, and its `namesakes` among the listed entries), `total`, `truncated`, `excluded`, `kinds` |
| `sources.read` | `ref` (pinned: that revision; unpinned: the head) | the exact `payload` and `envelope`, `revision`, `head`, `is_head`, `revisions`, `etag`, `manifest_etag`; for an entity, `effective` (the composed chain and each field's state, source, upstream and value, plus notices) or `composition_error`; `links`, `portrayals` (entities derived from this one), `namesakes`, the `chats` that bind it and at which revisions, `actions` and `unavailable` |
| `sources.create_source` | `source`, `title?` | `source`, `manifest_etag` |
| `sources.rename_source` | `source`, `title?`, `expected` | `source`, `manifest_etag` |
| `sources.create` | `source`, `kind`, `fields`, `id?`, `expected` | `ref` (the new `r1`), `manifest_etag` |
| `sources.save` | `ref`, `fields`, `head`, `expected` | the new revision's `ref`, `manifest_etag` |
| `sources.save_raw` | `ref`, `payload`, `head`, `expected` | the new revision's `ref`, `manifest_etag` |
| `sources.derive` | `base` (unpinned is pinned to its head now), `source`, `id?`, `name?`, `expected` (of `source`) | the portrayal's `ref` (`r1`), `manifest_etag` |
| `sources.portrayal` | `ref`, `edit`, `name?` (a section or facet), `text?` (an override), `head`, `expected` | the new revision's `ref`, `manifest_etag` |
| `sources.chats` | `limit?` | recent `chats`, each with the `etag` an attach must name |
| `sources.attach` | `ref` (pinned), `chat`, `expected` (the chat's etag) | `chat`, `ref`, `role`, the chat's new `etag`, the new `actors` |

`edit` is one of `override_section`, `clear_section`, `reset_section`, `override_description`,
`clear_description`, `reset_description`, `clear_facet` and `reset_facet`.

A field's `state` is 046's: `inherited`, `pinned`, `overridden`, `restated`, `local`, `cleared` or `missing`.

An `id` left out is a slug of the name. When the source already uses that slug, the first free `-2`, `-3`... suffix
is used. An `id` that is given and already used, under any kind, is refused: ids are never reused.

Attaching a character makes a new actor that has witnessed nothing (107). A scenario or lorebook makes none. Any
other kind is `not_attachable`.

## Refusals

Every refusal is 044's `ApiRefused`, and nothing was written.

| Reason | Meaning |
|---|---|
| `changed` | The source manifest or the head changed since the client read it. The client keeps its draft, reads again, shows the difference and retries. |
| `stale_clear` | A clear of something the revision does not inherit (it is already local, cleared or missing). |
| `reserved_namespace` | A new source in the `card:` namespace, which the card importer owns. Records of card sources can be edited. |
| `source_exists`, `entity_exists` | A new source or id that is already used. |
| `import_record_read_only` | The `card-import` record is written only by its importer. |
| `no_structured_editor` | The record's family has no structured editor; use the raw editor. |
| `not_attachable`, `no_chat` | The kind cannot be attached to a chat, or the chat does not exist. |
| `invalid_field`, `invalid_sections`, `invalid_derivation`, `invalid_payload` and 009's codec reasons | A malformed argument or payload. |
| `missing_base`, `clear_and_set`, `nature_mismatch`, `chain_too_deep` and 022's other reasons | The new revision would not compose. |
| `invalid_envelope`, `invalid_source_state` and the store's other refusals | The store refused the transaction (a derivation cycle, a payload nested too deep...): the error's class name in snake case. |
| `excluded_source`, `entity_dropped`, `missing_entity`... | The ref does not resolve, with the store's reason. |

## What is not offered

`sources.read` lists these under `unavailable` so a page shows them as unavailable instead of hiding or faking
them:

- **Deleting** a source or a revision. The store keeps every revision, so a chat that pins one never changes.
- **Pictures**, placed from the gallery (114).
- **Importing** cards and V0.2.1 libraries, from the import page (110).
- **Typed relations** between actors, which are campaign state (017). A source declares only derivation links here.
- **Abilities and other facets with fields**, edited in the raw editor.
- **Moving a chat to a newer revision**, a reviewed rebase (042) done from the chat.

Only the head revision is edited. An older revision is read exactly and can still be attached.

## Not yet in the app

The Library page that calls these ops plugs into the app shell through 108's route and page contract, which
lands separately. Until then the ops are reachable from Python only.
