# Remove from the viewer; collect unused images to the recycle bin

## Intent

A reader wants to step through a gallery, remove the pictures that are poorly
done one at a time, and then reclaim the space from Settings. Two things stand
in the way today:

1. The shared image viewer cannot remove anything. The unified image surfaces
   design (`2026-10-11-unified-image-surfaces-design.md` §5.2) said "the viewer
   itself never exposes crop, deletion, or description saves", so a removal
   means viewer → Open record → Manage art → Remove → browser confirm, per
   picture.
2. The image collector (`store/image_gc.py`) deletes permanently, and only
   once an object has been unreachable for 30 days *and* seen unreachable by
   two scans on this device at least a grace period apart. A picture removed
   today cannot be reclaimed for about a month.

This change lets the viewer remove the picture it is showing, with an inline
confirmation, and moves on to the next one; and it changes the collector to
send each unused picture to the operating system's recycle bin, collectable as
soon as nothing links to it and its files are an hour old.

### Decisions taken with the user

- **Inline confirmation**, not a browser popup and not one-click-with-Undo.
- **Collectable right away**: the 30-day grace and the two-scan sighting rule
  are removed; a one-hour floor on file age remains. The library is synced
  across devices, and the user accepts the risk this opens (§3.4).
- **Only the picture goes to the bin**, as an ordinary image file with a
  readable name. Its sidecar (description, subject tags), blob-index entry and
  thumbnails are deleted. Getting a picture back means uploading the binned
  file again.
- **The system recycle bin, desktops only** (`Send2Trash`). No app-owned trash
  folder, and no fallback to permanent deletion anywhere.

### Non-goals

- No Undo for a removal, and no restore-from-bin flow in the app.
- No bulk selection in the gallery.
- No change to what a removal does on the server: it removes a placement or
  writes the existing campaign tombstone, exactly as the Art panel's Remove
  does. No route is added for removal.
- No change to reachability: what counts as a root, and the fail-closed walk,
  are unchanged.
- No collection on Android, and no collection started automatically.
- Narrative links that name a slot (`avatar`, `gallery_3`) are not rewritten;
  removing a placement can still make one stop showing a picture, as today.

## 1. Removal in the viewer

### 1.1 Shape

The viewer stays free of record code. Removal is a capability the opening
surface supplies:

- `ImageDescriptor` gains an optional `removal: { verb: "remove" | "hide" |
  "delete"; from: string }`. `from` is the human name of what the picture is
  removed from ("Mara", "the world library", "this campaign's library"). A
  descriptor with no `removal` is not removable.
- `ImageViewer` gains an optional prop `onRemove(key: string) => Promise<void>`.
  The Remove control is drawn only when the prop is supplied **and** the active
  descriptor carries `removal`. `onRemove` resolves when the removal has
  landed and rejects with an `Error` whose message is shown to the reader.

### 1.2 Which surfaces supply it

| Surface | Pictures that get `removal` | How it removes |
|---|---|---|
| Combined gallery (`ImagesView`) | record placements (`ART_RECORD_KINDS`) whose row has a `v`; world-library rows; campaign-library rows whose provenance is not `world-original` | record: `api.removeRecordArt(target, {name, v})`, the route the Art panel uses, guarded by `expected_v`; world library: `api.deleteWorldImage`; campaign library: `api.deleteCampaignImage` |
| Record Art panel viewer (`useRecordArt` / `RecordArtEntry`) | every image for which the read's `operations` include `remove` and that is not a world original | the controller's existing `remove(image)` |
| World art panel (`WorldArtPanel`) | every library image | the panel's existing delete, without its `window.confirm` |

The two library deletes take no version guard today (a library picture replaced
under the same name since the gallery loaded would be the one removed); this
change does not add one, and the confirmation names the picture's slot so the
reader sees what they are confirming.

Every other viewer — Markdown images, the existing-art picker, post-image
picker, drawers, character related art — supplies no `onRemove`. Greeting
pictures never join a viewer sequence (unchanged).

Verb, by what the server will do:

- a world-scoped record placement, or a campaign's own placement or library
  picture: **remove** ("Remove from Mara?");
- a campaign record placement whose provenance is `inherited`, or a
  campaign-library row whose provenance is `inherited`: **hide** ("Hide in
  this campaign? The world keeps it.") — the campaign DELETE route writes the
  tombstone;
- a world-library picture: **delete** ("Delete from the world library? Every
  campaign on this world loses it.") — the sentence the World art panel's
  popup already carries.

A campaign's own placement over an inherited slot also tombstones that slot;
its confirm says "Remove from Mara in this campaign? Any world picture in this
slot is hidden too." (the Art panel's existing wording).

### 1.3 Interaction

- **Remove** sits with the viewer's other controls. Pressing it replaces it
  with an inline confirmation: the sentence from §1.2 and **Yes** / **No**
  buttons; focus moves to **No**.
- Keys, registered through `useHotkeys` with labels in group `IMAGE VIEWER` so
  the `?` sheet lists them: `delete` and `backspace` open the confirmation
  (Backspace because a Mac's Delete key sends it); while it is open, `enter`
  confirms and `escape` cancels it — Escape closes the viewer only when no
  confirmation is open. Each binding's `enabled` mirrors its button.
- **Yes** calls `onRemove(key)`. While it is pending, Remove, Yes, No and
  Previous/Next are disabled (keys included) and the confirmation says
  "Removing…".
- **On success** the viewer moves to the next image in its sequence, or the
  previous one if the removed image was last, and closes (reason
  `"unavailable"` is *not* used — a plain close) when it was the only one.
  The selection moves before the parent's refresh lands, so the existing rule
  "a refresh that drops the selected placement closes the viewer" (unified
  spec §5.1) does not fire for a removal the viewer made; it still fires for
  one made elsewhere.
- **On failure** the viewer stays on the image and shows the error under the
  controls; the confirmation closes. A record removal refused because the
  placement changed (409 on `expected_v`) reads "This picture changed since
  the gallery loaded, so it was not removed." and the surface refreshes.
- The surface refreshes its own list after a successful removal through the
  path it already has (`artChanged` for record art; a re-read for the
  galleries).

### 1.4 What this amends

Unified image surfaces §5.2's sentence becomes: "the viewer never exposes crop
or description saves; it exposes removal only when the opening surface supplies
it (2026-10-12 viewer removal design)." §5.1's close-on-removal rule gains the
exception above. A dated amendment line is added to that spec.

## 2. Collection timing

### 2.1 The rule

An object (or an orphan blob) is collectable when:

- no root reaches it (unchanged; the walk is strict and all-or-nothing);
- its sidecar's and blob's mtimes are both at least **one hour** old
  (`GRACE_HOURS = 1`), so an object ingested by an import that has not placed
  it yet is not taken; a future mtime is clock skew and never collectable
  (unchanged);
- and the collection carries a token from a scan on this device for this root,
  at most 24 hours old (unchanged).

The sighting requirement (first seen unreachable a grace period ago, seen again
`SCAN_GAP_HOURS` later) is removed, and with it the per-device sightings file.
`_judge` decides on mtimes alone. The sightings file under
`.cache/image-store/gc/` is no longer read or written; one left by an older
build stays where it is (the tokens directory beside it is still used). The
staged-tree rule (a work directory touched within the hour blocks the run) is
unchanged.

The grace is no longer a parameter: `scan()` loses `grace_days`,
`GRACE_DAYS_DEFAULT`, `GRACE_DAYS_MIN` and `SCAN_GAP_HOURS` go, and a report
carries `grace_hours: 1` in place of `grace_days`. Rows that will become
collectable still carry `collectable_at` (now always within the hour).

### 2.2 Why an hour

The grace used to bound a sync risk (§3.4); with the bin making a wrong
collection recoverable, what remains to guard is a picture ingested moments
ago by something that has not placed it yet — a Chub gallery import, a
harvest, a staged bundle. Those place within one request or one run; the
staging rule already covers the long ones. One hour is a structural margin
over that, not a measured one.

## 3. Collection disposal: the recycle bin

### 3.1 What goes where

For each collectable object, in the existing batch and lock order (ingest/GC
lock, then the object's stripe, re-checking mtime, root, reachability and
refcount):

1. The sidecar is deleted (unchanged; it goes first so the blob refcount stays
   right).
2. If no surviving sidecar names the blob, the blob is **recycled** (§3.2)
   instead of unlinked.
3. Its blob-index entry and thumbnails are deleted, best effort (unchanged).

An orphan blob is recycled the same way. Nothing else is recycled: sidecars,
index entries, thumbnails and tokens are deleted as today.

### 3.2 Recycling one blob

1. **Name.** `grimoire - <description> - <sha8>.<ext>`, where `<description>`
   is the object's description (read from the sidecar before step 1 of §3.1)
   cut to 60 characters at a word boundary where one exists, with characters
   invalid in a Windows file name (`<>:"/\|?*`, control characters) removed,
   whitespace collapsed and trailing dots and spaces trimmed. With no
   description, or one that sanitises to nothing: `grimoire - <sha8>.<ext>`.
   `<sha8>` is the first eight hex characters of the blob's sha256. An orphan
   blob has no description.
2. **Outbox.** The blob is moved with `os.replace` to
   `.cache/image-store/recycle/<device key>/<name>` (`maintenance_reports.
   device_key`, so a synced `.cache` never hands one device's outbox to
   another). The outbox is under the pinned root, so the move is a rename on
   the same volume; a move that fails stops the run with the blob in place.
3. **Bin.** `send2trash.send2trash(path)` on the outbox file.
4. **On a bin failure** the file is moved back to its blob path, where it is an
   orphan blob (its sidecar is gone) that a later collection recycles. The run
   stops with state `trash-unavailable`, the error class (never its message,
   which can carry a path) in the report. If the move back fails too, the file
   stays in the outbox and the report names it.

Every path is still built from the pinned root and checked against the live
root before each destructive step (`_check_root`). The outbox is added to the
trees the collector may touch; `_delete_file`'s refusal of anything else, of
links and of non-regular files applies to the move as well.

### 3.3 The outbox is drained first

A collection begins — after its token is claimed and before its first batch —
by sending to the bin every regular file in its own device's outbox (a crash
between steps 2 and 3 leaves one there). A drain failure stops the run as in
§3.2.4, before anything else is collected. A scan reports how many files are
waiting in this device's outbox.

### 3.4 Accepted risk: a placement still syncing

Suppose device B places a picture and that change has not reached device A
when A collects. A sees nothing reaching the picture and bins it; the deletion
syncs to B, whose placement then resolves to a missing object. The 30-day grace
bounded this; an hour does not. Recovery: upload the binned file on any device.
The store names a picture by its pixels (`image_hash`), so on the same decoder
the re-upload lands under the same object id and B's placement resolves again.
(JPEG decoders differ across platforms, so a JPEG re-uploaded on another
platform may get a new id — the existing "one id per JPEG across decoders"
caveat.) The binned file has lost its description and tags.

### 3.5 Where there is no bin

`Send2Trash` (pure Python) is added to the `desktop` extra only; the Android
dependency set stays as it is.

- `image_gc.recycle_bin_available()` is true when `send2trash` imports. A scan
  report carries `recycle_bin: bool`.
- `POST /maintenance/images/gc` with `dry_run: false` is refused with **409
  `no_recycle_bin`** before a run is reserved when it is false — the same
  place the missing-token 400 is refused. A scan is always allowed.
- `collect()` checks again (a direct call, a test) and returns a `refused`
  report with reason `no_recycle_bin` before claiming the token.
- A bin that exists but refuses a file (a network drive, a Linux volume with
  no trash directory) is §3.2.4: the run stops; nothing is unlinked instead.

## 4. Settings

`ImageStoreCard`:

- The collect button reads **Send N unused images to the recycle bin**; its
  confirmation says the pictures go to this computer's recycle bin, that their
  descriptions and tags are not kept, and that a picture another device linked
  but has not synced yet would be binned too.
- When the scan says `recycle_bin: false`, the button is not offered and the
  card says "This device has no recycle bin. Run the collection from a
  computer." A 409 `no_recycle_bin` is shown the same way.
- The scan explanation drops "two scans a grace period apart" for "unused and
  at least an hour old"; rows that are too new say when they become
  collectable.
- The collection report reads "Sent N to the recycle bin, freeing X", and says
  their file names start with `grimoire - `. A `trash-unavailable` stop says
  the recycle bin refused a file and nothing more was collected.

## 5. Documentation

- `store/image_gc.py`'s module docstring: candidates (§2), disposal (§3), the
  outbox, the accepted risk.
- `docs/store-guarantees.md`, The image store → Garbage collection: the
  candidate bullet, a disposal bullet, the "does not promise" paragraph (§3.4
  replaces the offline-device bound); "Nothing is collected unless someone
  asks" and the "No collection of an image a device outside the grace period
  still holds" line updated to match. `test_docs_guard.py` keeps naming the
  modules; test names cited in the doc are updated to the renamed tests.
- `2026-10-05-content-addressed-image-store-design.md`: a dated amendment
  under §12 pointing here.
- `2026-10-11-unified-image-surfaces-design.md`: the §1.4 amendment.
- `CLAUDE.md`'s image-store paragraph ("anything younger than its grace
  period") stays true and is left as is.

## 6. Tests

Backend (`test_image_gc.py`, `test_maintenance_routes.py`), with `send2trash`
replaced by a fake that records paths or raises:

- an unreachable object older than an hour is collectable on the first scan;
  one younger is not and carries `collectable_at`; a future mtime is skew;
- no sightings file is written;
- a collected object's blob reaches the fake bin under the §3.2 name (with a
  description, without one, with characters that must be stripped, with a long
  description), and its sidecar, index entry and thumbnails are gone;
- a shared blob is not recycled while another sidecar names it (unchanged
  refcount);
- a bin failure moves the blob back, stops the run `trash-unavailable`, and a
  later collection recycles the orphan blob;
- a file left in the outbox is binned at the start of the next collection; a
  drain failure stops that collection before its first batch;
- another device's outbox directory is never touched;
- with `send2trash` absent, the route refuses `dry_run: false` with 409
  `no_recycle_bin` and reserves no run, a scan reports `recycle_bin: false`,
  and `collect()` refuses without spending the token;
- the existing root-changed, link and unsafe-path refusals cover the outbox
  move.

Frontend (`ImageViewer.test.tsx`, `ImagesView` tests, `RecordArtPanel` /
`WorldArtPanel` tests, `ImageStoreCard.test.tsx`):

- Remove is drawn only with both `onRemove` and `removal`; world originals and
  greeting entries never get it;
- Remove → inline confirm with the §1.2 sentence for each verb; No and Escape
  cancel without closing the viewer; Delete/Backspace then Enter removes;
- success moves to the next image, the previous when last, and closes when
  none remain; the parent refresh does not close the viewer after the move;
- failure keeps the image and shows the message; a 409 shows the "changed"
  sentence;
- the combined gallery sends `removeRecordArt` with the row's `v`, and the
  library deletes for library rows;
- the Settings card's button text, the no-bin sentence, and the recycled
  report.
