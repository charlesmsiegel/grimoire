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

A **scope** is a source id or `campaign:<id>`. A **subject** is an unpinned definition
(`card:mara-vell/character/mara-vell`) or a campaign instance (`campaign:q-c-1/character/mara-vell-1`); a pinned
revision is refused, because placements attach to the entity.

**Gallery.** Each image shows its blob (`media_type`, `size`, `sha256`), its 018 provenance and `used_by`, the
placements of the scope's own subjects that name it. Provenance shows `origin`, `note` and `legacy_id` as text and
every other key an importer kept under `other`, as compact JSON cut at 2000 characters. An image whose blob is
missing or the wrong size is `unavailable` with that `reason`; the picture is never shown from anywhere else.

**Art.** `slots` is the resolver's answer per slot: `shown` (with `image`, `layer`, `crop`, `focus`,
provenance), `hidden`, or `unavailable` (with `reason`). Each slot says who decided it (`origin`, `placed_by`).
`own` is what the subject itself placed, slot by slot as written, each with its `etag`. `inherits` says, for an
instance, whether its inherited art is the snapshot pinned with its revision (`pinned`, 046: a picture added to
the definition later shows only after a reviewed rebase) or follows its definition (`live`). `modes` is what an
edit may write here: an instance or a derived portrayal may `set`, `adjust`, `hide` and `inherit`; a standalone
definition only `set`s. `reusable` is the scopes whose pictures may be placed here (038 W7): a campaign's own and
its instances' derivation source, or a definition's own source.

**Writes.** `expected` is required: the slot's `etag` from `own`, or `null` when the subject placed nothing
there. A slot that changed since it was read is refused `changed` (409). An upload that names a subject places
the new picture in the same 038 batch, so a refused placement leaves no picture behind. A new picture's id is
drawn fresh and qualified by this device's tag, so two devices adding pictures offline never write one image
with two contents. Rewriting a placement keeps its unknown fields and provenance; changing its mode drops its
old `note` and records `origin: placed in the app`. `crop` is `{x, y, w, h}` and `focus` is `{x, y}`, in
integer basis points (0 to 10000).

**Conflicted slots.** A slot of an instance that a sync left in conflict is listed in `conflicts`, and every
write to it is refused `art_conflict` until it is resolved: a third version would leave neither device a
version to resolve against. When this device's sync records cannot be read, `editable` is false and every write
is refused.

## Devices and sync

| Op | Arguments | Returns |
|---|---|---|
| `sync.devices` | none | `device`, `device_problem`, `owner`, `peers`, `transport`, `unavailable` |
| `sync.identify` | `device` | names an unnamed library, once |
| `sync.pair` | `peer`, `key` (hex), `address` (loopback `host:port`), `replace` | the pairing; `key` only when it was generated |
| `sync.run` | `peer` | the text API's `sync` report |
| `sync.conflicts` | none | `conflicts` with `mine` and every `theirs`, and campaigns whose records are `unknown` |
| `sync.resolve` | `peer`, `path`, `keep`, `mine`, `theirs` | the text API's report with `kept` and `not_kept` |
| `sync.forks` | `limit` (1 to 1000, default 50) | recent chats waiting on a fork choice, with what was set aside |
| `sync.choose` | `chat`, `turn` | the fork, `set_aside` and whether the chat waits |

**Transport.** `transport` says plainly what sync can and cannot do: loopback only, authenticated by the pairing
key but not encrypted, and never in the background. A peer is dialed through a forwarded loopback address
(`adb reverse` or an SSH tunnel; [MOBILE](MOBILE.md)).

**Pairing.** Pairing needs a named device. Without `key`, a new key is drawn and returned once, to type on the
other device. Pairing a device that is already paired replaces its key and ends the old one's access, so it
needs `replace: true`; it does not end the conflicts kept with that device. `address` is kept while the app runs;
the app's `--peer` keeps it across restarts.

**Conflicts.** Each conflict shows this device's version and every other version kept here side by side: its
`etag`, whether it was deleted, its size and a preview. A turn's preview is 035's table view, so intentions,
briefs and unshown lines are never shown; a placement shows its mode and picture; any other record shows its kind.
A version this build cannot read is still listed (`unreadable`). `sync.resolve` takes the etags as read, and its
`not_kept` lists every version that was not kept, with which device held it. Keeping this device's version over
the other device's deletion reports that deletion as not kept.

**Unavailable here.** `sync.devices` lists, with reasons, what the app does not offer yet: unpairing (ending a
pairing drops the conflicts kept with that device), background sync (waits on an encrypted transport), and
resolving a source's conflict (only a campaign's files can be resolved in the app; Sync still reports a source's
conflict). A definition's placements are a source's files, so a definition slot in conflict is not marked in
`conflicts`.

## Limits of this slice

- This slice is the adapter. Serving it over HTTP and the page that uses it register through 108's shell contract.
- Expression slots (`expr.<outfit>.<name>`) are placed by name; there is no expression editor yet.
- `media.gallery` lists at most 1000 pictures of one scope per call, from the start.
