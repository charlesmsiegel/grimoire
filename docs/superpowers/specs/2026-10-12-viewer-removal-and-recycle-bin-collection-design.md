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
  across devices, and the user accepts the risk this opens (§3.6).
- **Only the picture goes to the bin**, as an ordinary image file with a
  readable name. Its sidecar (description, subject tags), blob-index entry and
  thumbnails are deleted. Getting a picture back means uploading the binned
  file again.
- **The system recycle bin, desktops only** (`Send2Trash`). No app-owned trash
  folder, and **no permanent deletion of a picture anywhere** — including the
  cases where the operating system would silently delete instead of recycling
  (§3.5).

### Non-goals

- No Undo for a removal, and no restore-from-bin flow in the app.
- No bulk selection in the gallery.
- No change to what a removal does on the server: it removes a placement or
  writes the existing campaign tombstone, exactly as the Art panel's Remove
  does. No removal route is added or changed.
- No change to reachability: what counts as a root, and the fail-closed walk,
  are unchanged.
- No collection on Android, and no collection started automatically.
- Narrative links that name a slot (`avatar`, `gallery_3`) are not rewritten;
  removing a placement can still make one stop showing a picture, as today.

## 1. Removal in the viewer

### 1.1 Shape

The viewer stays free of record code. Removal is a capability the opening
surface supplies:

- `ImageDescriptor` gains an optional `removal: { question: string }` — the
  confirmation sentence for this picture (§1.2). A descriptor with no
  `removal` is not removable.
- `ImageViewer` gains an optional prop `onRemove(key: string) =>
  Promise<RemoveOutcome>`, where `RemoveOutcome` is `"removed"` or
  `"changed"`. The promise **rejects** with an `Error` whose message is shown
  when the removal failed. The Remove control is drawn only when the prop is
  supplied **and** the active descriptor carries `removal`.
- **Contract for every `onRemove`:** it calls the API directly — never a
  surface's existing mutation wrapper, which swallows errors into panel state
  and refreshes before resolving (`useRecordArt`'s `mutation`, `WorldArtPanel`'s
  `mutate`). On success it resolves `"removed"` as soon as the request settles;
  the surface's list refresh starts after that and is not awaited by the
  viewer. On a `stale_image` refusal (409) it **does** await its own fresh
  re-read, and resolves `"removed"` when the key is gone from it (removed
  elsewhere) or `"changed"` when it is still there — the viewer never has to
  guess whether a refresh has landed. Any other refusal rejects with a
  readable sentence; a bare error code is never shown (for example the world
  library's 409 `image_in_collection` reads "This picture is part of a
  collection, so it can't be deleted from the library."). The surface keeps
  its own exclusion: while it has another mutation pending, or (the Art panel)
  holds an unsaved description or crop draft, `onRemove` rejects with "Finish
  or cancel the edit in the Art panel first." without sending anything.
- **The viewer remembers the key it removed.** A refresh that drops that key
  never fires the viewer's unavailable-close (`ImageViewer.tsx`'s
  `onClose("unavailable")` effect), whatever order the selection move and the
  refresh land in. A refresh that drops any *other* selected key still closes
  the viewer as today (unified spec §5.1).

### 1.2 Which surfaces supply it

| Surface | Pictures that get `removal` | Call |
|---|---|---|
| Combined gallery (`ImagesView`) | record placements (`ART_RECORD_KINDS`) carrying `v` and `provenance` other than `world-original`; world-library rows; campaign-library rows | record: the adapter's `remove` for the row's `RecordArtTarget` (`createRecordArtAdapter(target).remove`, the Art panel's own route, guarded by `expected_v`); world library: `api.deleteWorldImage`; campaign library: `api.deleteCampaignImage` |
| Record Art panel viewer (`useRecordArt` / `RecordArtEntry`) | every image when the read's `operations` include `remove`, except world originals | `adapter.remove(image)`; the controller's `pending` exclusion is taken for the call and released when it settles, and its refresh follows |
| World art panel (`WorldArtPanel`) | every library image | `api.deleteWorldImage`; the panel's `busy` is set for the call, then `load(true)` |

The adapter's `remove` runs `checkImage`, which reads
`image.placement.target`; gallery record rows already carry `placement`
(`routes/characters.py` `_gallery_art_fields`), so the gallery passes
`{name, v, placement}` through a narrowed parameter type (`Pick` of
`RecordArtImage`) rather than fabricating the rest, and keeps that check.

Every other viewer — Markdown images, the existing-art picker, the post-image
picker, drawers, character related art — supplies no `onRemove`. Greeting
pictures never join a viewer sequence (unchanged).

**Row fields.** Record rows already carry `v` and `provenance` (`campaign` or
`inherited` in a campaign gallery, `world` in a world gallery). Library rows
carry neither today; `_campaign_library_rows` and the world-library rows gain
`provenance` (`campaign` / `inherited` for a campaign's library, `world` for
the world's). Neither library delete takes a version guard today and this
change does not add one; the confirmation names the picture's slot.

**The question**, by what the server will do:

- world-scoped record placement: "Remove Mara's gallery 3?"
- any campaign-scoped record placement — the campaign DELETE route hides an
  inherited slot and, for the campaign's own placement, tombstones any world
  picture under it, so one sentence serves both, matching the Art panel's
  wording (`RecordArtPanel.tsx`): "Hide Mara's gallery 3 only in this
  campaign? Any inherited world image in this slot is hidden too."
- campaign-library row, `provenance: inherited`: "Hide <slot> in this
  campaign? The world keeps it."
- campaign-library row, `provenance: campaign`: "Remove <slot> from this
  campaign's library?"
- world-library row: "Delete <slot> from the world library? Every campaign on
  this world loses it." (the sentence the World art panel's popup carries).

The slot label is the descriptor's existing `label` / `primaryLabel`.

### 1.3 Interaction

- **Remove** sits with the viewer's other controls. Pressing it replaces it
  with the inline confirmation: the question, then **Remove** and **Cancel**
  buttons. Focus moves to the confirming **Remove** button, so Enter (native
  button activation) confirms. No `enter` hotkey is registered: the registry
  calls `preventDefault()` before running a binding (`shortcuts/registry.ts`),
  which would turn Enter on the focused Cancel button into a removal.
- Hotkeys, registered with `useHotkeys`, labelled in group `IMAGE VIEWER` for
  the `?` sheet: `delete` and `backspace` (a Mac's Delete key sends Backspace)
  open the confirmation; `escape` cancels an open confirmation and otherwise
  closes the viewer as today (the close binding's `enabled` is false while a
  confirmation is open). Each binding's `enabled` mirrors its control.
- While the confirmation is open, Previous/Next (buttons and arrow keys) are
  disabled, so the question cannot end up naming another picture than the one
  that would be removed.
- **Confirm** calls `onRemove(key)`. While it is pending, every control except
  Close is disabled, keys included, and the confirmation reads "Removing…".
- **`"removed"`**: the viewer moves to the next image in its sequence, or the
  previous one if the removed image was last, and closes (a plain close, not
  `"unavailable"`) when it was the only one.
- **`"changed"`** (the version guard refused and the surface's fresh re-read
  still holds the key): the viewer stays and shows "This picture changed
  since it was loaded, so it was not removed." A picture already removed
  elsewhere arrives as `"removed"` (§1.1).
- **A rejection**: the viewer stays on the image, closes the confirmation and
  shows the message under the controls.

### 1.4 What this amends

Unified image surfaces §5.2's sentence becomes: "the viewer never exposes crop
or description saves; it exposes removal only when the opening surface
supplies it (2026-10-12 viewer removal design)." §5.1's close-on-removal rule
gains the exception in §1.1. A dated amendment line is added to that spec.

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
`SCAN_GAP_HOURS` later) is removed, and with it the per-device sightings file:
`_judge` decides on mtimes alone. A sightings file left under
`.cache/image-store/gc/` by an older build is no longer read or written (the
tokens directory beside it is still used). The staged-tree rule (a work
directory touched within the hour blocks the run) is unchanged.

The grace is no longer a parameter: `scan()` loses `grace_days`;
`GRACE_DAYS_DEFAULT`, `GRACE_DAYS_MIN` and `SCAN_GAP_HOURS` go; reports and
tokens carry `grace_hours` in place of `grace_days`. `FORMAT` is bumped, so a
token an older build issued is refused as a format mismatch ("scan again"),
and the Settings card reads an older report's `grace_days` without failing.
Rows that will become collectable still carry `collectable_at`, now always
within the hour, so the card renders it as a time, not a date.

### 2.2 Why an hour

The grace used to bound a sync risk (§3.6); with the bin making a wrong
collection recoverable, what remains to guard is a picture ingested moments
ago by something that has not placed it yet — a Chub gallery import, a
harvest, a staged bundle. Those place within one request or one run; the
staging rule already covers the long ones. One hour is a structural margin
over that, not a measured one.

## 3. Collection disposal: the recycle bin

### 3.1 Under the lock: commit to the outbox

Per collectable object, in the existing batch and lock order (the ingest/GC
lock, then the object's stripe, re-checking mtime, root, reachability and
refcount):

1. **If the blob would be kept for any reason but `shared`** — an unreadable
   sidecar somewhere (`keep_all_blobs`) or an object that has not arrived yet
   (`unarrived`) — the object is **skipped**, sidecar included, and reported
   with that reason. Deleting its sidecar would throw away its description and
   bin nothing, which is the mid-sync state of a synced library.
2. **If another sidecar names the blob** (`shared`), the sidecar is deleted and
   the blob stays, as today: the picture is still in the store under the other
   object. Nothing is recycled; the report counts it under `released`, not
   `recycled`.
3. **Otherwise** the object's description is read from the sidecar, the blob
   is moved with `os.replace` into the outbox under its bin name (§3.2), and
   then the sidecar is deleted. The move is the commit: a crash after it
   leaves a sidecar naming a missing blob, which no placement reaches and the
   next collection removes. **A blob already missing** at this step (that
   crash, or a sync that delivered its deletion) is not a failure: the sidecar
   is deleted, nothing is recycled, and the report counts it under `released`
   — the same treatment `_delete_blob` gives a gone blob today. Any other
   move failure stops the run with the blob in place.

This reverses today's order (sidecar first, then blob) for the recycled case
only. It is safe for the reason the old order was: the stripe and the
ingest/GC lock are held across both steps, so an ingest of the same id waits
and then finds no sidecar and recreates the object with a fresh blob. (A
placement of the id made through `assets.link_in`, which needs neither ingest
nor that lock, after the roots walk and before the batch is a race that
predates this change; its outcome is now a file in the bin rather than a
deletion.) `docs/store-guarantees.md` ("delete the sidecar
before the blob") and `image_store.py`'s "never a sidecar naming nothing"
comment are rewritten to say a *collected* object may briefly be a sidecar
naming nothing, and only while unreachable; the tests that pin the old order
(`test_a_concurrent_ingest_is_never_left_dangling`,
`test_a_root_flip_between_sidecar_and_blob_reports_the_sidecar`) change with
it.
4. The blob-index entry and thumbnails are deleted, best effort (unchanged).

An orphan blob (named by no sidecar) is moved to the outbox the same way, with
no description.

**The scan agrees with the collection.** `_classify_objects` today never looks
at the hold reasons; only the blob classification does. So that the scan does
not offer — and the token does not name — objects the collection will skip,
an otherwise collectable object whose blob is held for `unreadable-sidecars`
or `unarrived-objects` is classified `protected` with that reason and left out
of the token and the button's count. A `shared` blob does not protect its
object. Nothing else goes to the outbox: sidecars, index entries,
thumbnails and tokens are deleted as today.

### 3.2 The outbox and the bin name

- **Where:** `.cache/image-store/recycle/<device key>/`
  (`maintenance_reports.device_key`), under the pinned root, so the move is a
  rename on one volume and a synced `.cache` never hands one device's outbox
  to another device's collector.
- **Name:** `grimoire - <description> - <sha8>.<ext>`. `<description>` is the
  sidecar's own description cut to 60 characters at a word boundary where one
  exists, with characters invalid in a Windows file name (`<>:"/\|?*`, control
  characters) removed, whitespace collapsed and trailing dots and spaces
  trimmed. With no description — none was written, the object holds
  `description_conflicts`, or the text sanitises to nothing — the name is
  `grimoire - <sha8>.<ext>`. An unplaced object has no legacy
  `descriptions.json` key to prefer, so the sidecar's text is the picture's
  description. `<sha8>` is the first eight hex characters of the blob sha256.
- The outbox is deliberately **not** added to the trees the collector may
  delete in (`_DELETABLE`), so `_delete_file` can never unlink a waiting
  picture. The move and the bin call carry their own checks — live root equals
  the pinned root before every destructive step, no path through a link,
  regular files only.

### 3.3 After the lock: to the bin

At the end of each batch, **after the ingest/GC lock is released**, every file
in this device's outbox is sent to the bin (`send2trash`), one at a time,
checking the root before each. The bin call never runs under the lock, so a
slow shell or Finder call cannot hold up an upload.

- A file the bin accepts is reported in `recycled` (its bin name and size).
- **A file the bin refuses** stays in the outbox under its readable name. The
  run stops with state `trash-unavailable`, the error's class (never its
  message, which can carry a path) and the outbox-relative names of the files
  still waiting. Nothing more is collected in that run.

### 3.4 Draining, and stuck files

- A collection begins — after its token is claimed and before its first
  batch — by sending this device's outbox to the bin (a crash or a refused
  file leaves files there). A refusal stops it as in §3.3, before anything
  else is collected: if the bin is refusing, no more pictures leave the store.
- A scan reports `outbox` — the names of this device's waiting files — and
  issues a token when the outbox is non-empty even if nothing else is
  collectable, so waiting files can always be drained.
- A scan also reports `foreign_outboxes`: the number of files in other outbox
  directories. They belong to another device, or to this one under an earlier
  store path (`device_key` hashes the root, so a data-dir move strands an
  outbox). The collector never touches them; the card says where they are so
  the reader can empty them by hand.
- The outbox syncs with `.cache`: a recycled picture is uploaded to the sync
  service and removed again when its bin call succeeds, and a stuck file is
  present on every device until it drains. That traffic is accepted.

### 3.5 Where there is no bin, and where the bin is not one

`Send2Trash` (pure Python) is added to the `desktop` extra; the Android
dependency set stays as it is. Availability is one function,
`store/recycle_bin.py`'s `check(root) -> Availability`, whose answer is
`ok`, or a refusal with a code and a sentence:

- **`android`** — `proclock._on_android()` (which also checks `ANDROID_ROOT`
  / `ANDROID_DATA`, for a Chaquopy without `sys.getandroidapilevel`), made a
  public helper both modules call: "This device has no recycle bin. Run the
  collection from a computer."
- **`not_installed`** — a desktop where `send2trash` does not import (an
  install upgraded by `git pull` without re-running the installer): "The
  recycle-bin helper is not installed. Re-run the installer, then try again."
- **Windows, a volume with no bin.** Send2Trash calls the shell without
  `FOF_WANTNUKEWARNING`, and on a volume with no Recycle Bin Windows then
  *deletes the file permanently and reports success* (verified against
  Send2Trash 2.1.0's `win/legacy.py` and `win/modern.py` flags). So on Windows
  the outbox's volume must pass, before any bin call: `GetDriveTypeW` is
  `DRIVE_FIXED` (a network share, a mapped drive or a removable drive is
  refused, code `volume_has_no_bin`: "This library's drive has no Recycle Bin
  (a network, mapped or removable drive), so pictures can't be sent to it.");
  `SHQueryRecycleBinW` on that volume
  succeeds (same code); the policy `NoRecycleFiles` (under
  `Software\Microsoft\Windows\CurrentVersion\Policies\Explorer`, read from
  both HKCU and HKLM) is not set, and the volume's
  `HKCU\...\Explorer\BitBucket\Volume\{GUID}\NukeOnDelete` is not 1 (code
  `bin_deletes_immediately`: "Windows is set to delete files immediately
  instead of using the Recycle Bin on this drive."). An absent per-volume key
  means Windows' defaults — the bin is on, with its default capacity — and is
  not a refusal; `MaxCapacity` is then unknown and the size check below is
  skipped.
- **Windows, a full bin or an oversized file.** A bin at capacity purges its
  oldest items to fit a new one, and a file larger than the whole bin is
  deleted outright. Before each batch's bin calls the volume's
  `SHQueryRecycleBinW` size is read once; a file whose size alone exceeds a
  known `MaxCapacity` stays in the outbox and stops the run
  (`too_large_for_bin`: "This picture is larger than the Recycle Bin on its
  drive can hold."). The bin making room by purging its own older items is
  ordinary bin behaviour and is not refused.
- **Windows backstop.** After each batch's bin calls, the volume's item count
  is read again; if it rose by less than the number of files sent and no
  purge could have happened — the size before the batch plus the bytes sent
  fits the capacity (the known `MaxCapacity`, or, when the per-volume key is
  absent, 10% of the volume, which is at or above Windows' default, so a doubt
  errs toward stopping) —
  the run stops with `recycle-bin-unverified`
  and names the files it could not confirm: "Windows did not confirm these
  went to the Recycle Bin. Check the bin before collecting again." It says
  *unverified*, not *deleted*, because a purge or a concurrent delete from
  Explorer moves the count too. This is the backstop for a configuration none
  of the checks above recognise and it can only notice after the fact; it is
  stated as the residual in `docs/store-guarantees.md`.
- `Send2Trash` is pinned `>=2.1,<3`, the range whose Windows flags were
  checked.
- **macOS and Linux** refuse rather than delete when a volume has no trash
  (`NSFileManager` / the freedesktop topdir trash raise); a refusal is §3.3.

Where the answer is not `ok`:

- `POST /maintenance/images/gc` with `dry_run: false` is refused with **409
  `no_recycle_bin`**, carrying the code and sentence, before a run is reserved
  — the same place the missing-token 400 is refused. A scan is always allowed
  and its report carries `recycle_bin: {ok, code, message}`.
- `collect()` checks again (a direct call, a test) and returns a `refused`
  report before claiming the token.

### 3.6 Accepted risk: a placement still syncing

Suppose device B places a picture and that change has not reached device A
when A collects. A sees nothing reaching the picture and bins it; the deletion
syncs to B, whose placement then resolves to a missing object. The 30-day grace
bounded this; an hour does not. Recovery: upload the binned file on any device.
The store names a picture by its pixels (`image_hash`), so on the same decoder
the re-upload lands under the same object id and B's placement resolves again.
(JPEG decoders differ across platforms, so a JPEG re-uploaded on another
platform may get a new id — the existing "one id per JPEG across decoders"
caveat.) The binned file has lost its description and tags.

## 4. Settings

`ImageStoreCard`:

- The collect button reads **Send N unused images to the recycle bin** (N
  counts pictures that will be recycled plus outbox files waiting); its
  confirmation says the pictures go to this computer's recycle bin, that their
  descriptions and tags are not kept, and that a picture another device linked
  but has not synced yet would be binned too.
- When the scan's `recycle_bin` is not `ok`, the button is not offered and the
  card shows its sentence. A 409 `no_recycle_bin` is shown the same way.
- The scan explanation drops "two scans a grace period apart" for "unused and
  at least an hour old"; a row too new says the time it becomes collectable.
- Waiting outbox files and foreign outboxes are listed with where they are.
- The collection report counts from `recycled`: "Sent N images to the recycle
  bin (X). The space is freed when you empty the bin." with "Their names start
  with `grimoire - `." A stop names its cause: `trash-unavailable` ("The
  recycle bin refused a file; N are waiting to be sent. Nothing more was
  collected."), `recycle-bin-unverified` (names the files Windows did not confirm
  reached the bin), and the existing ones. `ImageGcReport.state` gains the two
  new states, and its rows gain `protected` objects (§3.1) with their reason.

## 5. Documentation and tooling

- `store/image_gc.py`'s module docstring: candidates (§2), disposal (§3), the
  outbox, the accepted risk. `store/assets.py`'s docstring that says "grace
  period and two scans apart".
- `docs/store-guarantees.md`, The image store → Garbage collection: the
  candidate bullet, a disposal bullet, the "does not promise" paragraph (§3.6
  replaces the offline-device bound, and the Windows backstop of §3.5 is
  stated); "Nothing is collected unless someone asks" and the "No collection of
  an image a device outside the grace period still holds" line updated. Test
  names the section cites are updated to the renamed tests; it names
  `recycle_bin` beside the other maintenance modules.
- `2026-10-05-content-addressed-image-store-design.md`: a dated amendment
  under §12 pointing here.
- `2026-10-11-unified-image-surfaces-design.md`: the §1.4 amendment.
- `mypy.ini`: `[mypy-send2trash.*]` with `ignore_missing_imports` and
  `follow_imports = skip`, since CI does not install the `desktop` extra.
- `CLAUDE.md`'s image-store paragraph ("anything younger than its grace
  period") stays true and is left as is.

## 6. Tests

Backend. An autouse fixture in `conftest.py` replaces the bin with a fake that
records each path it was handed (and moves the file aside, as a bin does) and
makes `recycle_bin.check` answer `ok`, because CI has no `desktop` extra;
tests that are about availability override it.

- an unreachable object older than an hour is collectable on the first scan;
  one younger is not and carries `collectable_at`; a future mtime is skew; no
  sightings file is written; an older-format token is refused;
- a collected object's blob reaches the fake bin under the §3.2 name (with a
  description, without one, with characters that must be stripped, with a long
  description, with `description_conflicts`), and its sidecar, index entry and
  thumbnails are gone;
- an object whose blob is held for `unreadable-sidecars` or `unarrived-objects`
  keeps its sidecar and is reported skipped; a `shared` blob keeps the blob and
  is reported `released`;
- the bin call happens after the ingest/GC lock is released (the fake asserts
  the lock is free);
- a refused bin call leaves the file in the outbox under its readable name,
  stops the run `trash-unavailable` and lists it; the next collection drains it
  first, and a drain refusal stops that collection before its first batch;
- a scan with only outbox files issues a token; another outbox directory is
  never touched and is counted in `foreign_outboxes`;
- `recycle_bin.check`: `android`, `not_installed`, and on Windows (through
  seams over `ctypes` and `winreg`) a non-fixed drive, a failing
  `SHQueryRecycleBinW`, `NoRecycleFiles` in HKCU and in HKLM, `NukeOnDelete`,
  an absent per-volume key (not a refusal), a file over `MaxCapacity`
  (`too_large_for_bin`), and a batch whose item count rose by less than the
  files sent with the bin below capacity (`recycle-bin-unverified`) versus one
  at capacity (not a stop);
- an object held by `unreadable-sidecars` / `unarrived-objects` is
  `protected` in the scan and absent from the token; a blob already missing at
  collect deletes the sidecar, recycles nothing and is `released`; the
  rewritten ordering tests (blob to the outbox, then the sidecar) hold against
  a concurrent ingest and a root flip between the two;
- the route refuses `dry_run: false` with 409 `no_recycle_bin` and reserves no
  run when `check` is not `ok`; a scan reports it; `collect()` refuses without
  spending the token;
- the existing root-changed, link and unsafe-path refusals cover the outbox
  move and the bin call.

Frontend (`ImageViewer.test.tsx`, `ImagesView` tests, `RecordArtPanel` /
`WorldArtPanel` tests, `ImageStoreCard.test.tsx`):

- Remove is drawn only with both `onRemove` and `removal`; world originals and
  greeting entries never get it; the five questions of §1.2;
- Remove → confirmation with focus on its Remove button; Enter confirms; Cancel
  and Escape cancel without closing the viewer; Enter on a focused Cancel does
  not remove; Delete/Backspace open it; arrows do nothing while it is open;
- `"removed"` moves to the next image, the previous when last, and closes when
  none remain — both when the refresh lands before the move and after it, with
  no "no longer available" notice;
- `"changed"` with the key gone after refresh steps on; with it still there
  stays and shows the sentence; a rejection keeps the image and shows the
  message;
- each surface's `onRemove` rejects on an API error (not swallowed) with a
  sentence, never a bare code (`image_in_collection`); resolves `"removed"`
  before its refresh completes on success; on `stale_image` awaits its
  re-read and resolves `"removed"` or `"changed"` from it; rejects unsent
  while the surface has a mutation pending or a draft held; the gallery sends the record route
  with the row's `v`, and the library deletes for library rows;
- the Settings card: button text and count, each refusal sentence, the time of
  a too-new row, waiting and foreign outbox files, the recycled report and both
  new stop states, and an older report with `grace_days`.
