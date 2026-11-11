# Source authoring (109)

`grimoire.sourceapi` is what the app's Library pages call. It lets a person browse, read, create and edit the
library's source definitions, derive portrayals and attach an exact revision to a chat. It is trusted application
code acting for the library's **owner**, like 044's [text API](TEXT_API.md). Before 109, V2 had no source
editor: only the importers (026, 037) wrote definitions. This module is the narrow, authorized adapter that the
[UI coverage contract](roadmap/UI_COVERAGE.md) asks Sprint 109 to add. It writes nothing that a domain service does
not already define:

- **Revisions are immutable** (010). Every edit writes a *new* revision `r<n+1>` and moves the entity's head in the
  source manifest, in one guarded transaction. The manifest is written at the etag the editor read, and the head
  record must still have the etag the editor read, so an edit made from a stale read, or over a head whose file was
  edited by hand since, is refused and writes nothing.
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
api.call("sources.save", {"ref": read["entity"], "head": read["head"], "etag": read["etag"],
                          "expected": read["manifest_etag"], "fields": {"description": "A lamplighter of the quay."}})
```

## Families

A record's family comes from its schema:

| Family | Schema | Kinds | Structured fields |
|---|---|---|---|
| entity | `grimoire.entity/0` | `character`, `location`, `item`... any kind but the two below | `name`, `nature` (`individual` or `template`), `description`, `sections` (name → text), `traits` (a list of facet objects) |
| scenario | `grimoire.scenario/1` | `scenario` | `title`, `premise`, `openings` (a list of `{id?, text}`), `instructions` |
| lorebook | `grimoire.lorebook/1` | `lorebook` | `name`, `description`, `entries` (a list of `{index?, keys, content, constant?, enabled?}`, where an entry with `index` may leave out its keys and content to keep the head's; `constant` is written to the entry's `settings`, where 037 reads it) |
| other | anything else | | raw editor only |

A world is a source (`world:...`), not a record. Its locations, items and characters are entities in it.

**Unknown fields are kept.** A structured edit starts from the head's payload and changes only the keys it names.
An opening keeps its other keys by `id`, and a lorebook entry that names the `index` of an entry in the head keeps
that entry's settings, extensions and extras. The **raw editor** (`sources.save_raw`) writes a payload exactly as
given, in the record's own schema (a record that has none keeps having none). An entity payload must still decode
(009), and a scenario's lorebook must still be a pinned lorebook ref. Every edit, structured, raw or portrayal, keeps
the head's other envelope fields (STORAGE's preserved unknown keys) on the new revision. An imported character (026, 037) keeps its verbatim card for provenance, and a `personality` section edited here is
the personality it speaks from in play. Renaming an imported character keeps the lines its example messages give it: a card record keeps the name they
label its turns with in `card.example_speaker`, and a V1 portrayal reads it from its verbatim card.

A blank description or an empty section in the structured editor is left out of the payload. On a **portrayal**
the structured editor goes through 046's helpers: a description or section it is given is an override (which also
lifts a clear of it), and a blank one, or a local section left out, drops the override and inherits again. A blank
field never lifts a clear, since a form posts a cleared field back blank: that is the typed `reset_*` edit. A clear
is always the typed `clear_*` edit, never a blank field.

**Every entity revision must compose.** Before anything is written, the new revision is composed (022) over the
library as it would be. A head that could not be played is refused with 022's reason: a derivation base that does
not exist or is not written yet (`missing_base`), a field both cleared and set (`clear_and_set`), another nature
than the base's (`nature_mismatch`), a chain that is too deep, a malformed derivation.

## Ops

Every ref is passed and returned in its own string form, so a client hands it back unchanged. Every write names
`expected`: the etag of the source manifest that the client read. Edits of an existing entity also name `head`,
the revision they replace, and `etag`, that head record's own etag from `sources.read` of the entity.

| Op | Arguments | Result |
|---|---|---|
| `sources.catalog` | `query?`, `kind?`, `source?`, `limit?` (1 to 1000, default 200) | `sources` (each with title, owner, namespace, etag, entity count), `entries` (each entity's unpinned ref, kind, head, name, family, whether it is derived, any problem, and its `namesakes` among the listed entries), `total`, `truncated`, `excluded`, `kinds` |
| `sources.read` | `ref` (pinned: that revision; unpinned: the head) | the exact `payload` and `envelope`, `revision`, `head`, `is_head`, `revisions`, `etag`, `manifest_etag`; for an entity, `effective` (the composed chain and each field's state, source, upstream and value, plus notices) or `composition_error`; `links`, `portrayals` (entities derived from this one), `namesakes`, the `chats` that bind it and at which revisions, `actions` and `unavailable` |
| `sources.create_source` | `source`, `title?` | `source`, `manifest_etag` |
| `sources.rename_source` | `source`, `title?`, `expected` | `source`, `manifest_etag` |
| `sources.create` | `source`, `kind`, `fields`, `id?`, `expected` | `ref` (the new `r1`), `manifest_etag` |
| `sources.save` | `ref`, `fields`, `head`, `etag`, `expected` | the new revision's `ref`, `manifest_etag` |
| `sources.save_raw` | `ref`, `payload`, `head`, `etag`, `expected` | the new revision's `ref`, `manifest_etag` |
| `sources.derive` | `base` (unpinned is pinned to its head now), `source`, `id?`, `name?`, `expected` (of `source`) | the portrayal's `ref` (`r1`), `manifest_etag` |
| `sources.portrayal` | `ref`, `edit`, `name?` (a section or facet), `text?` (an override), `head`, `etag`, `expected` | the new revision's `ref`, `manifest_etag` |
| `sources.chats` | `query?` (in a chat's label or ref, any case), `limit?` (1 to 1000, default 50) | `chats`, newest activity first, each with the `etag` an attach must name; `truncated` when more match |
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
| `changed` | The source manifest, the head or the head record changed since the client read it. The client keeps its draft, reads again, shows the difference and retries. |
| `not_head` | An edit named an older revision; only the head is edited. |
| `stale_clear` | A clear of something the revision does not inherit (it is already local, cleared or missing). |
| `reserved_namespace` | A new source in the `card:` namespace, which the card importer owns. Records of card sources can be edited. |
| `source_exists`, `entity_exists` | A new source or id that is already used. |
| `import_record_read_only` | The `card-import` record is written only by its importer. |
| `no_structured_editor` | The record's family has no structured editor; use the raw editor. |
| `unusable_lore` | A lorebook entry the lore reader (037) would skip, so it would never reach play: content over 8000 characters or not plain text, or no key and not constant. An entry the head already holds that the reader skips for the same reason (an imported regex entry...) stays editable. A key the reader cannot match, or more than 64 keys, is `invalid_field`. |
| `not_attachable`, `no_chat` | The kind cannot be attached to a chat, or the chat does not exist. |
| `duplicate_binding`, `binding_limit` and the chat model's other reasons | The chat refuses the binding (107): that revision is already bound, or the chat is full. |
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
- **Voices for characters made here.** A character speaks in play from an authored voice profile (014, 027), or,
  if it was imported, from its own imported text. Voice profiles are not authored in the library yet, so a
  character created here can be attached but is not yet a speaker.

Only the head revision is edited. An older revision is read exactly and can still be attached.

## In the app

The Library is two pages of the [app shell](APP_SHELL.md), reached from the Library menu. They call these
ops as the app's `sources` adapter (`backend/src/grimoire/app/adapters/sources.py`): `POST /api/sources.read` and so
on, under the text API's writer lock, as the owner's trusted handle, with the refusals above.

- **`#/sources`**, Characters and lore, lists every entry by its exact ref, as portrait tiles (characters) and text
  tiles (everything else) or as a table (`view=list`), 48 at a time. Its address holds what is in view:
  `role=character|scenario|lore|world` (`persona` is the characters; `world` is locations, items and every other
  kind), `q=` a search over names and refs, `source=` one source's shelf or `shelf=cards` every imported card's,
  `sort=recent|name|played` and `limit=`. Where the app keeps the person's tags, pins and saved filters, the page
  also shows tag chips (`tags=`), a Pinned shelf (`shelf=pinned`), saved filters and Select mode with its bulk
  Pin and Tag, and an entry's sheet shows its tags and pin: see [SHELF](SHELF.md#in-the-app). Each role and shelf shows how many entries it holds. Same-named entries are
  separate tiles, each saying how many others share its name. A character or scenario has a Chat link to Start Chat.
  Where the app reads covers ([MEDIA_SYNC_UI](MEDIA_SYNC_UI.md#beyond-character-slots)), a text tile shows its
  entry's cover behind its kind and title, or **Add cover**; the list shows it as the row's picture; and one
  source's shelf opens under that source's cover band (**Add cover**, **Change cover**, **Remove**). A grid reads
  all of its covers in one request and keeps them a few moments.
  How often and how lately an entry was played is counted from the `chats` op, and is shown and sorted by only when
  that list is whole. New entry, New source and a shelf's Rename each open a dialog; a refusal is shown in it.
- **`#/source/<SourceRef>`** shows one revision exactly: a character as a character sheet, anything else as an entry
  sheet under a title band: the entry's cover with **Change cover** and **Remove**, or, with no cover, a dashed
  block that takes a dropped image or opens the picture picker. Its tabs are in the address
  (`?tab=art|edit|revisions|raw`; no tab is the sheet):
  - **Sheet**: each field's value, and for a portrayal its state and source (046); what it opens with, the lorebook
    an imported card binds, a lorebook's entries with their keys; the chats that use it and the revisions they are
    on, its portrayals, namesakes and links. A portrayal overrides, clears (after a confirmation) and resets
    inherited fields here, and any entity can be derived from the revision shown. On the current version, each
    lorebook entry has a picture slot beside it, and the sheet says how many entry pictures no entry reads as any
    more. A scenario lists its **Scene backdrops** (add, change, remove), and every entry links to all of its
    pictures on the Art page.
  - **Art** (a character, where the app serves `media.art`): its picture slots as the library resolves them, and a
    link to the Art page for it, where pictures are placed.
  - **Edit**: on the head, the structured editor for its family.
  - **Revisions**: every revision, newest first, and one (`rev=`) compared with the head field by field
    (`static/pages/source/sheet.js`). An older one is restored by saving its payload as a new revision
    (`sources.save_raw`, after a confirmation); nothing old is changed.
  - **Raw**: the stored payload; on the head, the raw editor.

  Any revision can be attached to a chat exactly as shown (Attach to a chat), after a confirmation that names it.
- **Drafts and stale writes.** What is typed is kept per tab, with the head it started from and the editor's model
  of that head, until it is saved or discarded. A structured save sends only the fields that differ from the head;
  a lorebook entry the edit leaves alone is sent as `{"index": n}` alone, and an indexed entry keeps whatever keys,
  content and settings the edit leaves out, so the head keeps it exactly. A save refused `changed` keeps the draft
  and reads the head again. A draft whose head has moved on says what changed since and what the draft changes.
  Kept, only the draft's own changes are laid over the new head (`static/pages/source/drafts.js`): sections are
  matched by name, openings by id and lorebook entries by exact content. A part changed differently on both sides
  is a conflict: the draft's own text for it is shown to copy and the draft can only be discarded. A change the head
  already holds (saved, then the page left) is recognized, additions and lore entries included, and a draft with
  nothing else is dropped. A head changed in place (same revision, new etag) counts as moved on. What is typed
  while a save is on its way is kept as a draft. The editors wait until the draft is kept or discarded.
- An older revision shows what it holds, read-only, on the same tabs.
- The raw editor is unavailable for a payload holding a whole number beyond 2^53, which the browser would round, and
  refuses to save one, or a number beyond its range.
  Nothing is offered to derive from a revision that does not compose.
- What the ops do not offer is shown as such, with the reason `sources.read` gives (above).

Tested through the adapter in `backend/tests/test_library_pages.py`, and end to end in Node's stand-in DOM by
`backend/tests/js/library.e2e.mjs`; the drafts on their own by `backend/tests/js/library.unit.mjs`.
