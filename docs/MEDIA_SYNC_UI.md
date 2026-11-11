# Media and sync adapter (114)

`grimoire.mediaapi.MediaSyncApi` is the graphical app's adapter for pictures, devices, sync, forks and keep-both
conflicts. It has JSON ops over the one open library that 044's [text API](TEXT_API.md) serves, for the same
owner: a JSON object goes in and a JSON object comes out. It keeps no record of its own and writes only through
the services that own each record:

- 038's `ImageLibrary` for pictures and placements ([IMAGES](IMAGES.md)), every placement write compare-and-swap
  on the slot
- 028's `pair` and 019's `identify` for a pairing and this device's name ([DEVICE_SYNC](DEVICE_SYNC.md), [SYNC](SYNC.md))
- the text API's `sync`, `resolve` and `choose` for a transfer, a keep-both resolution and a fork choice

It shares the text API's writer lock, so a picture written here never interleaves with a turn played there.

```python
media = MediaSyncApi(text_api)
art = media.call("media.art", {"subject": "campaign:q-c-1/character/mara-vell-1"})
media.call("media.place", {"subject": art["subject"], "slot": "avatar", "mode": "hide",
                           "expected": None})
```

Refusals are the text API's `ApiRefused` (`reason`, `status`). A refused op writes nothing.

## Never substituted, never settled by time

- A slot is shown, hidden or unavailable exactly as 038's resolver says. A missing picture stays unresolved;
  nothing falls back to another one. `unresolved` is an importer's record of a missing picture, so the app never
  writes it: a person adds the picture instead.
- A keep-both conflict waits for the owner, and a fork waits for the owner's choice. Every version not kept is
  reported back with what it held: `not_kept` from `sync.resolve`, `set_aside` from `sync.choose`.
- An unreachable peer is `incomplete`. Nothing here is deleted, and the device plays on offline.

## Pictures

| Op | Arguments | Returns |
|---|---|---|
| `media.scopes` | none | `sources` (admitted, with title; excluded ones show no art) and `campaigns` |
| `media.gallery` | `scope`, `limit` (1 to 1000, default 200) | `images`, `more`, `subjects`, `problems` |
| `media.art` | `subject` | the subject's slots, own placements, modes, reusable scopes, conflicts |
| `media.upload` | `scope`, `media` (base64), `note`, `origin`; optionally `subject`, `slot`, `expected`, `crop`, `focus` | `image`, and `art` when placed |
| `media.place` | `subject`, `slot`, `mode`, `image` (set, adjust), `crop`, `focus`, `expected` | the subject's `art` as it now reads |
| `media.shown` | `subjects` (at most 200), `slot` (default `cover`) | `shown`: that one slot of each subject, with its `etag` |
| `media.entries` | `subject` (a lorebook) | `entries` (each with its picture's `slot` and what shows there), `orphans`, `art` |
| `media.chat` | `chat` | `backdrop`, `posts`, `orphans`, `offered`, and the campaign's `art` |
| `media.backdrop_from` | `chat`, `image` (one of `offered`), `expected`, `crop`, `focus` | the campaign's `art` as it now reads |

A **scope** is a source id or `campaign:<id>`. A **subject** is an unpinned definition
(`card:mara-vell/character/mara-vell`), a campaign instance (`campaign:q-c-1/character/mara-vell-1`), or a source
or a campaign as a whole, written as its scope is (`world:saltmarch`, `campaign:q-c-1`: 018's
[owner subject](IMAGES.md#subjects)); a pinned revision is refused, because placements attach to the entity.

**Gallery.** `subjects` lists the scope itself first, then what it holds. Each image shows its blob (`media_type`, `size`, `sha256`), its 018 provenance and `used_by`, the
placements of the scope's own subjects that name it. Provenance shows `origin`, `note` and `legacy_id` as text and
every other key an importer kept under `other`, as compact JSON cut at 2000 characters. An image whose blob is
missing or the wrong size is `unavailable` with that `reason`; the picture is never shown from anywhere else.

**Art.** `slots` is the resolver's answer per slot: `shown` (with `image`, `layer`, `crop`, `focus`,
provenance), `hidden`, or `unavailable` (with `reason`). Each slot says who decided it (`origin`, `placed_by`).
`own` is what the subject itself placed, slot by slot as written, each with its `etag`. `inherits` says, for an
instance, whether its inherited art is the snapshot pinned with its revision (`pinned`, 046: a picture added to
the definition later shows only after a reviewed rebase) or follows its definition (`live`). `modes` is what an
edit may write here: an instance or a derived portrayal may `set`, `adjust`, `hide` and `inherit`; a standalone
definition, a source and a campaign inherit nothing, so they `set` and `hide`. `reusable` is the scopes whose
pictures may be placed here (038 W7): a campaign's own and its instances' derivation source, a definition's own
source, or, for a source or a campaign itself, only its own.

**Writes.** `expected` is required: the slot's `etag` from `own`, or `null` when the subject placed nothing
there. A slot that changed since it was read is refused `changed` (409). An upload that names a subject places
the new picture in the same 038 batch, so a refused placement leaves no picture behind. A new picture's id is
drawn fresh and qualified by this device's tag, so two devices adding pictures offline never write one image
with two contents. Rewriting a placement keeps its unknown fields and provenance; changing its mode drops its
old `note` and records `origin: placed in the app`, and so does placing another picture; re-cropping the same
picture keeps its record. `crop` is `{x, y, w, h}` and `focus` is `{x, y}`, in
integer basis points (0 to 10000).

**Conflicted slots.** A slot of an instance, or of a campaign itself (its cover, a chat's backdrop, a post's
picture), that a sync left in conflict is listed in `conflicts`, and every
write to it is refused `art_conflict` until it is resolved: a third version would leave neither device a
version to resolve against. When this device's sync records cannot be read, `editable` is false and every write
is refused.

## Beyond character slots

Covers, lore entry pictures, scene backdrops and pictures on posts are ordinary 018 placements
(`grimoire.mediaapi.beyond`). Nothing else stores them: provenance, pictures stored once and referenced,
compare-and-swap on the slot, sync and keep-both conflicts are the image model's own. Only the **slot** of the
**subject** differs, and each is exact, never matched by name:

| What | Subject | Slot | Shape |
|---|---|---|---|
| a cover | any entry (an unpinned definition), a source, a campaign | `cover` | 16/5 |
| a lore entry's picture | the lorebook | `entry.<key>` | 1/1 |
| a scenario's backdrops | the scenario | `backdrop.<name>` | 16/5 |
| a chat's backdrop | the chat's campaign | `backdrop.<chat id>` | 16/5 |
| a picture on a post | the chat's campaign | `post.<turn id>` | 16/10 |

All of them are per library, so they sync to the other device like any picture. Each is a `gallery`-role slot,
written with `media.upload` and `media.place` like any other, with the same refusals, etags and 32 MiB limit, and
taken off with a `hide` (the picture stays in its gallery). A picture is put (`set`) only where the slot's name
points at something; a `hide` is never checked, since it is how an orphan is taken off:

- **`entry.<key>`**: a lorebook's entries carry no id of their own (a card may repeat the one it gives, and an
  entry's place changes when the list is edited), so an entry is keyed by what it says: `<key>` is 24 hex digits of
  the sha256 of its keys and text (`entry_slot`). Reordering a lorebook, or changing an entry's settings, keeps
  every picture on its entry; two entries that say exactly the same share one. Editing an entry's keys or text
  leaves its picture an **orphan**: `media.entries` lists it under `orphans`, on no entry, until it is taken off
  or placed again. A slot no entry of the head reads as is refused `missing_entry`.
- **`backdrop.<chat id>`**: one per chat, not per scene, since a chat's scenes have no identity a picture could be
  keyed by. The chat must exist (`missing_chat`). A campaign may place only its own pictures (038 W7), so a chat
  never names a scenario's picture: `media.chat` lists, under `offered`, the backdrops of the scenarios the chat is
  played from (an active `scenario` binding), and `media.backdrop_from` makes one the campaign's own image over the
  same stored bytes, its provenance naming the image it was copied from (`x-copied-from`), and places it in the
  same batch. A copy made before is used again. Any other image is refused `wrong_scope`.
- **`post.<turn id>`**: the person's own illustration of a post, kept beside the transcript like a bookmark. The
  turn must be on the chat's line of play (`missing_turn`) and hold a post (`not_a_post`). The turn record is never
  read into the picture or written for it, no context builder reads placements, and no model is called, so no
  character knows of it and no prompt names it. Two devices' turns have different ids, so their pictures never
  share a slot. `media.chat` lists a post's picture under `posts` only while its turn is a post on the line; one
  whose turn a fork choice set aside, or whose turn is gone, is under `orphans` (`why`: `set_aside`, `turn_gone`,
  `not_a_post`, or `unknown` when the chat cannot be opened): listed, on the Art page too, and shown on no post.

`media.shown` reads one slot of many subjects in one pass (a grid's covers): each answer is the slot as resolved
(`state` `empty` when nothing was ever placed) with the etag of the subject's own placement, and a subject that
cannot be parsed or read is `unavailable` without failing the others.

**Conflicts.** Two devices that each set a different cover keep both. A campaign's (its cover, a backdrop, a post's
picture) is listed by `sync.conflicts` and in the inbox, the slot is in `conflicts`, and every write to it is
refused `art_conflict` until the owner keeps one. An entry's or a source's is a source's file: Sync reports it on
every sync and each device keeps showing its own (below, "Unavailable here").

## Devices and sync

| Op | Arguments | Returns |
|---|---|---|
| `sync.devices` | none | `device`, `device_problem`, `owner`, `peers`, `transport`, `unavailable` |
| `sync.identify` | `device` | names an unnamed library, once |
| `sync.pair` | `peer`, `key` (hex), `address` (loopback `host:port`), `replace` | the pairing; `key` only when it was generated |
| `sync.run` | `peer` | the text API's `sync` report |
| `sync.conflicts` | none | `conflicts` with `mine` and every `theirs`, and campaigns whose records are `unknown` |
| `sync.resolve` | `peer`, `path`, `keep`, `mine`, `theirs` | the text API's report with `kept` and `not_kept` |
| `sync.forks` | `limit` (1 to 999, default 50) | recent chats waiting on a fork choice, with what was set aside, and `more` |
| `sync.choose` | `chat`, `turn` | the fork, `set_aside` and whether the chat waits |

**Transport.** `transport` says plainly what sync can and cannot do: loopback only, authenticated by the pairing
key but not encrypted, and never in the background. A peer is dialed through a forwarded loopback address
(`adb reverse` or an SSH tunnel; [MOBILE](MOBILE.md)).

**Peers.** `peers` lists every device this one is configured to dial, paired with while the app runs, or ever
synced with, each with its `address` (or `null`: this device does not dial it, it dials here), whether it is
`paired`, and its `last` sync report since the app started.

**Pairing.** Pairing needs a named device. Without `key`, a new key is drawn and returned once, to type on the
other device. Pairing a device that is already paired replaces its key and ends the old one's access, so it
needs `replace: true`; it does not end the conflicts kept with that device. `address` is kept while the app runs;
the app's `--peer` keeps it across restarts.

**Conflicts.** Each conflict shows this device's version and every other version kept here side by side: its
`etag`, whether it was deleted, its size and a preview. A turn's preview is 035's table view, so intentions,
briefs and unshown lines are never shown; a placement shows its mode and picture; any other record shows its kind.
A turn a fork choice set aside was heard by no one, so it shows its id, number and device only
(`set_aside`), and so does any turn whose chat cannot be opened to tell. A version this build cannot parse is still
listed (`unreadable`). `peers` gives each paired device's side of the conflict: the versions of it that stand here
and whether that device's version is its deletion, and each of `theirs` names the devices that hold it. So one
device's deletion shows beside another device's edit (`their_deletion`). A kept version whose bytes are lost or
damaged here puts its campaign under `unknown`, so an empty `theirs` in a campaign that is not unknown is a deletion
too. `sync.resolve` ends the conflict with one device and takes the etags as read. Its `not_kept` lists every
version that conflict set aside, with the device that held it (`holder`), and `still_open` is what other devices
still hold in conflict on that file, or `null`. Keeping this device's version over that device's deletion reports
the deletion as not kept. A device's deletion is resolved after every other device's version: before that, the text
API refuses it (`stale`) and nothing changes.

**Unavailable here.** `sync.devices` lists, with reasons, what the app does not offer yet: unpairing (ending a
pairing drops the conflicts kept with that device), background sync (waits on an encrypted transport), and
resolving a source's conflict (only a campaign's files can be resolved in the app; Sync still reports a source's
conflict). A definition's and a source's own placements are a source's files, so such a slot in conflict is not
marked in `conflicts`.

## Limits of this slice

- The app shell serves every op as `media.<op>` (`media.devices` for `sync.devices`, `media.sync` for `sync.run`,
  and so on), through one `MediaSyncApi` per open library, so a pairing made on one request is listed on the next.
  The Art page (`#/art`, a scope's gallery or one subject's slots: a character's or a chat actor's avatar and
  portrait, any other entry's cover and named slots, and the source's or campaign's own, with every orphaned entry
  or post picture) and the Devices and sync page (`#/sync`) use
  only those ops (and Devices and sync `inbox.list`, for what waits), and hide an action whose op the app does
  not serve. Art is not in the rail: it is
  opened by its address (`?scope=`, `&subject=`) or from the command palette.
- A slot's picture is chosen and framed in the **picture picker**, a modal any page may open
  (`static/pages/art/picker.js`, `openPicker(ctx, {subject, slot, target, aspect, use, art, file, offered})`,
  resolving to the subject's art after a write or to `null` on a cancel). It lists the readable pictures of the
  scopes the subject may reuse, takes a new file (`media.upload`, placed in the same batch; `file` starts with one
  dropped on the page), and writes with `media.place`: `adjust` when the picked picture is the one the slot
  inherits and the subject may adjust, `set` otherwise; Hide and "Use the inherited picture" show only where
  `modes` allows them and something is there. The preview has the slot's shape (`aspect`: 1/1, 3/4, 16/5 or
  16/10). For a cover, a backdrop, an entry's or a post's picture, Hide reads **Remove**, offered once the subject
  placed something. `offered` lists pictures the subject may take from elsewhere (a scenario's backdrops, for a
  chat) and places them with the caller's own op.
- Covers and the other single pictures are drawn by `static/pages/art/covers.js`, for any page: a cover band
  (Add cover, Change cover, Remove, or a dashed block that takes a dropped file), a library tile's cover, a
  lorebook's entry slots and a scenario's backdrops. A grid reads every tile's cover in one `media.shown` request
  and keeps the answers for 20 seconds; a page that shows one subject reads afresh, and a write refused `changed`
  reads the slot again. A band or a tile with a picture keeps its text white over a dark scrim. Its zoom (100 to 200 percent) is stored as the
  placement's `crop`, a centred box; exact crop and focus numbers can be typed instead. The preview and the Art
  page's slot cards show that zoom; other screens do not apply a crop yet.
- The Devices and sync page shows a card for this device, one for each other device (how it is reached, its
  `last` sync, **Sync now** when this device dials it) and **Pair a device**, which opens the pairing form. A sync
  reads "Syncing…" until its request answers, then a notice says what was exchanged, or what went wrong with
  **Try again**. Under the cards, one card says how many conflicts and forks wait and links to **Needs you**: no
  conflict or fork is decided on this page. The app keeps no log of past syncs, so the page lists none.
- Conflicts and forks are decided in the inbox ([INBOX](INBOX.md)), with these ops, the same choices and the
  same confirmations: `inbox.list` names, for each conflict, the versions that may be kept now, and the page
  shows every version side by side and, after a decision, everything it set aside (`static/pages/sync/versions.js`).
  This device's version of a conflict can be kept only while one other version stands, as the text API has it;
  with several, one of theirs is kept and this device's is listed as not kept, or it is kept on the other device.
- Expression slots (`expr.<outfit>.<name>`) are placed by name; there is no expression editor yet.
- `media.gallery` lists at most 1000 pictures of one scope per call, from the start.
- A slot is at most 123 characters (018), so a chat or a turn whose id leaves no room after `backdrop.` or `post.`
  takes no picture: the write is refused `invalid_field`.
- An entry's picture shows on its lorebook's sheet and on the Art page only; nothing shows it during play. A
  chat's backdrop is one picture for the whole chat. The Campaigns list shows no covers.
