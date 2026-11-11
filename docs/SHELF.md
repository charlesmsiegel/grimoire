# The shelf: tags, pins and saved filters

**Status:** implemented as the pure package `grimoire.shelf` (values, tag rules, codec), the file adapter
`grimoire.shelfstore` and the app's `shelf` adapter ops (`backend/src/grimoire/app/adapters/shelf.py`). It is the
person's own organisation of a library: tags and a pin on library entries, a pin on chats, and saved filters of
Characters and lore.

## What it is, and is not

A tag, a pin and a saved filter are notes **about** an entry or a chat. They are not part of it:

- They are keyed by the entry's exact **unpinned** `SourceRef` or the chat's exact `ChatRef`, never by a name
  and never by one revision. A pinned ref (`...@r2`) is refused `invalid_ref`.
- Writing one writes no source record, no manifest, no revision and no chat record. An entry's revisions, etag
  and manifest etag, and what a chat binds at which revisions, are the same before and after.
- They outlive any new revision of the entry: the same tags show on every version.
- A note about an entry or chat the library no longer holds is kept (it may come back with a sync) and listed
  by `shelf.read` as it is. Pages find marks by the refs they list, so such a note is simply not shown. It can be
  taken away at any time; a **new** tag or pin is given only to an entry or chat the library holds now
  (`no_entry`, `no_chat`).

"Pinned" here always means *kept at the top by the person*. It has nothing to do with a pinned revision.

## Tag names

A tag is trimmed, NFC-normalized, single-spaced text of 1 to 40 characters. Refused `invalid_tag`: an empty
name, a longer one, a control, format or other invisible character (a tab, a line break, a zero-width or
direction character), a line or paragraph separator, and a comma (tags are typed and linked with commas between
them). An entry or a saved filter carries at most 32 tags (`too_many_tags`).

A tag's identity is its name casefolded, so `Noir` and `noir` are one tag. One entry never holds both. Across
entries the tag is shown in **one** spelling everywhere: the spelling most entries use, and among equals the
first in code-point order (`grimoire.shelf.vocabulary`). `shelf.read` answers every tag in that spelling. The rule
depends only on the records, so two devices holding the same records show the same spelling. Which spelling is
shown can change when another entry is tagged, so a tag that is kept (in an address, in a saved filter) is matched
whatever its capitals: the library page finds `?tags=crew` after the tag has come to read `Crew`.

## Files

```text
<library>/config/shelf/entries/<name>.json    one annotated entry
<library>/config/shelf/chats/<name>.json      one pinned chat
<library>/config/shelf/filters/<id>.json      one saved filter
```

`<name>` is the first 32 hex digits of the SHA-256 of the ref's exact text (a ref holds `:` and `/`, so it is
never a file name). The record names its ref inside, and a record that sits under another name is refused
`address_mismatch`. A saved filter's `<id>` is its own id (lower-case letters, digits and hyphens, up to 40).

```json
{"format": "grimoire.shelf.entry/1", "ref": "world:harbor/character/wren", "tags": ["crew", "noir"], "pinned": true}
{"format": "grimoire.shelf.chat/1", "chat": "campaign:quay#chat/main", "pinned": true}
{"format": "grimoire.shelf.filter/1", "id": "f-3a91c2d07b5e", "name": "Harbor crew", "role": "character",
 "shelf": "", "source": "world:harbor", "tags": ["crew"], "query": "", "sort": "name"}
```

- Each file is strict JSON as the store writes it (sorted keys, two-space indent, a trailing newline), with
  exactly these keys. There is no time, device or counter in a record and tags are written in key order, so the
  same marks are the same bytes whoever writes them.
- A record that says nothing any more (no tag and not pinned) is **removed**, not kept empty. A deleted saved
  filter's file is removed.
- A saved filter keeps one view of Characters and lore: `role`, `shelf` and `sort` are the page's own short
  words (kept as lower-case tokens and not interpreted here), `source` is one source's id or `null` (never
  together with `shelf`), `query` is the search text (up to 200 characters). Names are 1 to 60 characters and
  unlike every other saved filter's, whatever the case (`filter_name_taken`); at most 200 are kept
  (`too_many_filters`).

### Files that are not records

Reading never fails on one file. A file that is not exactly a record of its version is listed in
`ShelfView.problems` (and in `shelf.read`'s `problems`) with its path, a reason and a detail, and is left exactly
as it is; everything else still reads. Reasons: `malformed`, `unsupported_format` (a `format` of a later
version), `address_mismatch`, `too_large` (over 64 KiB), `unexpected_file` (anything not named `*.json`),
`unreadable`, `link`. Editor and operating-system droppings are ignored, as the store ignores them.

A write to a record whose file cannot be read is refused `unreadable_record`, naming the file: it is never
written over. So whatever another tool, a hand edit or a conflict left in the folder, the app opens, says which
file it could not read, and loses nothing. Links are never followed.

## Writing

Every write names `expected`: the etag (`sha256:` of the file's exact bytes) of the record the caller read, or
`None`/`null` when it read none. A record that moved on, appeared or went away meanwhile refuses the write
(`StaleWrite`, answered `changed`); nothing is written. The etag is re-checked just before the rename, so an
edit made while a write was under way is refused, not lost.

A write is one temp file (`.shelf-*.tmp`) in the record's folder, synced and renamed over its target; a removal
is one `remove`. A crash leaves the old record or the new one, and at worst a temp file, which the next write of
that folder (or `ShelfStore.recover()`) sweeps. `ShelfStore` keeps no state between calls and does all its file
access through the `FileOps` seam; the caller serializes writers (the app's one writer lock).

## Where it lives, and sync

The shelf is **per-library** data: it lives inside the library folder, under the root name `config` that the
[native store](STORAGE.md#library-layout) reserves and never reads, so a copy or a backup of the library carries
it and `load()` is unaffected.

**Device sync does not carry it yet.** [`grimoire.sync`](SYNC.md) transfers only the record families it owns
under `sources/` and `campaigns/`; it lists `config` among the entries it skipped and neither sends nor
disturbs the shelf. Each device therefore has its own tags, pins and saved filters today. Carrying the shelf is a
new synced family for the sync core, not a change of this format, which is laid out for it:

- one file per entry, chat and filter, so edits to different things on two devices never meet in one file;
- deterministic bytes with no timestamps, so the same edit on both devices is the same etag and already agrees;
- only two edits of the *same* record can conflict, and a byte-level keep-both conflict leaves one whole valid
  record in place. A foreign or damaged file beside it is reported and never overwritten (above).

## Ops

Called as `shelf.<op>` through the [app shell](APP_SHELL.md#an-adapter-op). Refs travel as their own strings.

| Op | Arguments | Result |
|---|---|---|
| `shelf.read` | | `entries` (`ref`, `tags`, `pinned`, `etag`), `chats` (`chat`, `pinned`, `etag`), `filters` (`id`, `name`, `role`, `shelf`, `source`, `tags`, `query`, `sort`, `etag`), `tags` (each tag in use with how many records carry it), `problems` (`path`, `reason`, `detail`) and `limits` |
| `shelf.set_tags` | `ref`, `tags`, `expected` | the entry's marks (`etag` is `null` when nothing is left) |
| `shelf.edit_tags` | `ref`, `add?`, `remove?`, `expected` | the entry's marks; its other tags and its pin are kept |
| `shelf.pin_entry` | `ref`, `pinned`, `expected` | the entry's marks |
| `shelf.pin_chat` | `chat`, `pinned`, `expected` | the chat's marks |
| `shelf.save_filter` | `name`, `role?`, `shelf?`, `source?`, `tags?`, `query?`, `sort?` | the new filter, with its `id` |
| `shelf.rename_filter` | `id`, `name`, `expected` | the filter |
| `shelf.delete_filter` | `id`, `expected` | `id`, `deleted` |

`expected` is required on every write that replaces a record (a request without the key is `invalid_field`), so a
client that never read the shelf cannot write over it.

| Reason | Status | Meaning |
|---|---|---|
| `changed` | 409 | The record is not at `expected` any more. Read the shelf again. |
| `invalid_tag`, `too_many_tags`, `invalid_name`, `invalid_filter`, `invalid_ref`, `invalid_field` | 400 | A malformed argument. |
| `no_entry`, `no_chat`, `no_filter` | 404 | The entry, chat or saved filter is not there. |
| `filter_name_taken`, `too_many_filters` | 409 | The saved filters refuse it. |
| `unreadable_record`, `link` | 409 | The record's file is not one this app reads; it was not written over. |
| `shelf_unwritable`, `shelf_unreadable`, `read_only` | 409 | The files could not be written or read. |

## In the app

All of this shows only where the app serves the shelf ops.

- **Characters and lore** (`#/sources`): a row of tag chips, the tags the entries in view carry with their counts.
  Choosing several narrows to the entries that carry all of them (`tags=<tag>,<tag>` in the address). The
  **Pinned** shelf (`shelf=pinned`) lists the pinned entries. Each tile and row shows the entry's tags, opens its
  tag editor and has a Pin toggle; the list has a Tags column. **Save this filter** names the current role, shelf
  or source, tags, search and order; saved filters are listed under the shelves with how many entries each
  lists, open their view, and are renamed or deleted from **Edit** (a delete asks first).
- **Select** turns tiles into toggles (a ring, `aria-pressed`) and shows a bar: how many are selected, Select
  all shown, **Pin** (Unpin when all are pinned) and **Tag…** (add and remove tags, each entry keeping its
  others). A bulk action goes entry by entry at the etag the page read, says how many it changed and names each
  it could not, which stays selected. The bar has no Move, Lorebook or Delete: the library has no op that moves
  an entry between sources, binds a lorebook to several entries or deletes one.
- **An entry's sheet** (`#/source/<SourceRef>`): its tags as pills in the header, Edit tags and a Pin toggle, the
  same on every version of the entry.
- **Chats** (`#/chats`): a Pin toggle on each row; pinned chats are lifted into a **Pinned** group at the top;
  the **Pinned** filter (`?filter=pinned`) lists them alone.

When the marks changed since the page read them, nothing is written, the page says so and shows them as they are
now. Files the shelf could not read are named in a notice on Characters and lore and on Chats.

## Tests

`backend/tests/test_shelf_model.py` (tag rules, values, codec round trips and malformed documents),
`backend/tests/test_shelfstore.py` (records, stale etags, files that are not records, links, and every write
explored under the [fault harness](TESTING.md#fault-injection-harness-grimoiretesting) with crashes, torn writes,
I/O errors, recovery crashes and an edit made during a write), `backend/tests/test_app_shelf.py` (the ops, each
refusal, that an entry and a chat are untouched, a damaged shelf, and a device sync beside it), and the pages in
`backend/tests/js/library.unit.mjs`, `backend/tests/js/chats.test.mjs` and `backend/tests/js/library.e2e.mjs`.
