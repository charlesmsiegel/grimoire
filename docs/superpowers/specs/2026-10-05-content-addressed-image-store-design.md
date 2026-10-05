# Content-addressed image store and shared visual identity

## Intent

Grimoire stores the same picture many times. An avatar copied from a greeting
becomes a second file. A card's art can exist independently in a world, a
campaign overlay, a greeting and several character versions, and forks multiply
those copies. Storage is wasted, but identity is the bigger problem. Nothing can
say that these five occurrences are the same picture. So descriptions, subjects,
sources and thumbnails are repeated per copy and drift apart.

This design separates three things:

1. **Blob**: one exact encoded file, identified by the SHA-256 of its bytes.
2. **Image object**: one visual image, identified by the SHA-256 of its
   canonical decoded pixels (`px1-<hex>`).
3. **Placement**: one use of an image object under a logical name (`avatar`,
   `gallery_3`, `embed-…`, a library name, `cover`).

The governing rule:

> Store each independent fact once. Derive inverse relationships and indexes
> rather than keeping a second authoritative copy.

A placement never owns bytes. Copying an image inside Grimoire writes a new
placement that points at the same image object. Shared facts about the picture
live once, on the image object. Facts about one occurrence (such as avatar
focus) stay on the placement.

The outcome is simple to state: Grimoire stores one image object per exact
picture and one encoded blob for it. It stores as many lightweight placements as
needed to use that picture across the library.

### Non-goals

These are out of scope for now, though the design should make several of them
easier later:

- perceptual or near-duplicate detection;
- facial or character recognition;
- image embeddings, or semantic image search beyond existing descriptions;
- immediate automatic deletion of orphans;
- transcoding for compression;
- distributed multi-device transactions;
- SVG or any other format not accepted today (PNG, JPEG, GIF, WebP).

Hard links and reflinks are rejected as the data model. They save disk but give
no durable identity, and they behave badly under cloud sync, in archives, across
filesystems and on Android storage providers.

## What exists today (the ground this lands on)

All of this was checked against the tree at the commit this spec lands on.

- **One byte writer.** Apart from the raw tree copies listed below, every stored
  image ends in `assets.put_in(d, name, data, ext)`. `assets.path_in` resolves
  `<d>/<name>.*` (newest mtime wins). `assets.list_in` / `_candidates` produce
  `{name, ext, v}`, where `v = "<mtime_ns hex>-<size hex>"`. All of the
  following sit on these primitives:
  - per-version record images (characters, PCs, every `ENTITY_KINDS` kind,
    greetings);
  - both flat libraries (`world_images`, `campaign_images`);
  - covers (`covers`);
  - collection members (`image_collections.put_member`);
  - localized embeds (`localize._store`);
  - copy-from-greeting (`image_subjects.copy_to_character`);
  - card and Chub imports (`characters.import_card`,
    `download_chub_gallery_stream`).
- **Raw tree copies that bypass the primitive:**
  - `overlay.copy_record_dir_down`;
  - `sync._copy_tree` (promote) and demote;
  - `fork._copy` (`shutil.copytree` of a campaign);
  - `worlds.lifecycle.fork_world`;
  - `world_bundle` export and import (staging, then `repoint_urls`);
  - campaign slimming (`campaigns.lifecycle._prune_duplicate_files`, which
    compares bytes with `filecmp`);
  - `overlay.shadowed_images` (also `filecmp`).
- **Image-level sidecars:**
  - `descriptions.json` (`image_descriptions`) in record version folders and in
    both library folders. An absent key means undescribed; `""` means reviewed
    with nothing to say.
  - `focus.json` (`assets`), which holds `{"avatar": 0–100}`.
  - `subjects.json` (`image_subjects`), only in greeting folders. Its keys are
    image names, local `/api/worlds/…` reference URLs, or remote URLs.
  - Collection manifests `{format: 1, members: ["collection-image-<sha256>"]}`,
    whose members live in the world library.
- **Thumbnails** are keyed by library-relative path, stat, width and encoder,
  under `.cache/thumbs/<generation>/`. The same bytes at fifty paths get fifty
  entries.
- **Export** dedupes by path (`export.Images.by_path`), so the same bytes at two
  paths are packed twice.
- **Validation is uneven:**
  - Record uploads are magic-byte sniffed only (`fetch.sniff_ext` via
    `routes.common._upload_image_ext`) and have no size cap.
  - Libraries cap uploads at 25 MB (`image_library.validate_size`).
  - Covers and collection members are Pillow-verified with a 50 MP bound
    (`covers.validate`).
- **Card-imported PNG avatars** are stored whole, including the
  `chara` / `ccv3` tEXt chunk that carries the card JSON.

## Design

### 1. Identity

#### 1.1 Byte identity

`byte_sha256 = SHA256(exact stored bytes)`, computed before any decode or
transformation. It names the blob file. It is the HTTP cache version of the
original-file response (§7). Equal byte hashes are unconditionally the same
blob.

#### 1.2 Pixel identity, version 1

Image ids are `px1-<64 lowercase hex>`. The `1` is the canonicalization version.
A future change to the rules becomes `px2`; it never silently reinterprets `px1`.

**Static images** (single frame):

1. Decode with Pillow.
2. Apply EXIF orientation (`ImageOps.exif_transpose`).
3. Convert to RGBA8.
4. Set RGB to `(0,0,0)` wherever alpha is exactly 0. Use a mask paste, not a
   blend, so the result is exact.
5. Hash this, streaming in horizontal bands so memory stays bounded to one band:

   ```
   SHA256("grimoire-pixels-v1\0" ‖ u32be(width) ‖ u32be(height)
          ‖ u32be(len(color)) ‖ color ‖ rgba_bytes)
   ```

`color` is the colour-interpretation descriptor. It is the embedded ICC profile
bytes if present. Otherwise, for PNG, it is a canonical serialization of
gAMA/cHRM/sRGB as Pillow reports them. Otherwise it is empty.

This is one deliberate addition to the brief. An ICC profile or a gAMA chunk
changes what a browser displays from identical samples. Collapsing two such
files and keeping one blob would silently re-colour the other. EXIF, XMP, text
chunks and comments are excluded; they do not change rendering.

**Animated images** (GIF, APNG or WebP with more than one frame) are hashed in
their own domain:

```
SHA256("grimoire-anim-v1\0" ‖ u32be(w) ‖ u32be(h) ‖ u32be(loop) ‖ u32be(n)
       ‖ color-descriptor ‖ for each frame: u32be(duration_ms) ‖ rgba_bytes)
```

- Frames are the fully composited canvas after Pillow's `seek(i)` and
  `convert("RGBA")`. Disposal is resolved by compositing, so the hash covers
  what is displayed.
- A missing `loop` is encoded as `0xFFFFFFFF`, which is distinct from `0`
  (infinite).
- Each frame gets the same alpha normalization as a static image.
- Identical frames with different timing produce different objects.

**Opaque identity.** Some decodes cannot be canonicalized without risking two
different pictures collapsing:

- a mode Pillow converts many-to-one (CMYK, or 16-bit samples truncated to 8);
- a raster above the pixel budget: more than 50 MP static (the existing
  `covers.MAX_PIXELS`), or more than 200 MP of total frame area / more than 1000
  frames when animated;
- bytes that sniff as a supported format but fail to decode.

These get an id in a third domain:
`px1-` + `SHA256("grimoire-pixels-v1-opaque\0" ‖ byte_sha256)`. Such an image
dedupes only on exact bytes and never collapses across representations. Its
sidecar records `"identity": "bytes"` and the reason. Every other object records
`"identity": "pixels"`. Ingest therefore accepts exactly what is accepted today,
with no behaviour narrowing, and exact identity can never merge different
pictures.

**Fixtures.** Representative PNG, JPEG, GIF, WebP, APNG and animated WebP files
are checked in with their expected `px1` ids
(`backend/tests/fixtures/images/pixel_ids.json`). A dependency upgrade that
changes a decode then fails the build instead of redefining identity.

**Decoder portability, stated rather than solved.** JPEG decoding is not
guaranteed bit-identical across libjpeg implementations. Desktop Pillow wheels
ship libjpeg-turbo, and the Android Chaquopy build may not. Two consequences:

- A JPEG ingested on two devices may get two ids. That is a duplicate object,
  never a wrong merge, because no decoder disagreement makes different pictures
  agree.
- Re-ingesting byte-identical files is protected regardless of decoder, by the
  blob lookup in §5 step 2.

A later `px2` or a maintenance merge can collapse such duplicates. If a JPEG
fixture fails after a Pillow upgrade, that is the signal to decide. It is not a
fixture to re-pin silently.

#### 1.3 Placement identity

A placement is addressed by `(directory, logical name)` exactly as today. Logical
names (`avatar`, `gallery_N`, `embed-<12 hex>`, `map`, `cover`, library names,
`collection-image-<sha>` during the transition) keep their current validation
(`assets.storable`). They stop being identity: two names can point at one
object.

### 2. Physical layout

```
<GRIMOIRE_HOME>/assets/image-store/
  blobs/<b0b1>/<byte_sha256>.<ext>        immutable encoded bytes
  objects/<p0p1>/px1-<hex>.json           image-object sidecar (mutable)
```

- **Sharding.** The shard is the first two hex digits of the respective hash.
- **Extension.** It comes from `fetch.sniff_ext` on the bytes (always `jpg`,
  never `jpeg`), never from a filename.
- **Blob immutability.** A blob path is published once with `atomic.write_bytes`
  and is never rewritten. If a publish finds the path already present, it
  verifies size and hash. A matching blob is left alone. A non-matching blob is
  corruption (for example a truncated sync), so it is repaired with the correct
  bytes and the repair is logged. That is the only rewrite, and it restores the
  invariant that a blob's name and contents agree.
- **Path construction.** Paths are built only from regex-validated hex:
  `px1-[0-9a-f]{64}` and `[0-9a-f]{64}`. A sidecar or bundle can never choose a
  destination.
- **Placement records** live beside the slots they replace, in an `image-refs/`
  folder of the directory that holds the images:

  ```
  <root>/characters/<id>/assets/<vid>/image-refs/avatar.json
  <root>/<kind>/<eid>/assets/default/image-refs/map.json
  <root>/greetings/<gid>/assets/default/image-refs/embed-3f2a….json
  <world|campaign>/assets/images/image-refs/<name>.json
  <world|campaign>/assets/image-refs/cover.json
  ```

  `image-refs` is the folder name, never an image name. Logical names cannot
  contain a dot, so `<name>.json` is unambiguous.

### 3. The image-object sidecar

```json
{
  "format": 1,
  "id": "px1-…",
  "identity": "pixels",
  "blob": {"sha256": "…", "ext": "jpg", "mime": "image/jpeg", "size": 483921,
           "width": 1024, "height": 1536, "animated": false},
  "description": "A dark-haired woman standing before a ruined arch.",
  "associations": [
    {"kind": "character", "relation": "subject", "scope": "world:realm", "id": "seraphine"}
  ],
  "reviews": {"subjects": ["world:realm"]},
  "sources": [{"url": "https://example.invalid/original"}],
  "description_conflicts": []
}
```

**Field rules:**

- Only fields Grimoire understands are stored. No EXIF dump.
- `width` / `height` are post-orientation. They are absent for an opaque
  identity that did not decode.
- **`description`**: an absent key means not reviewed, `""` means reviewed with
  nothing to say, and a non-empty string means described. These are today's
  `descriptions.json` semantics, moved onto the object.
- **`scope`** is how an association names its namespace: `world:<wid>` or
  `campaign:<cid>` (a campaign-local character). A bare character id is never
  written.
- **`reviews.subjects`** lists the scopes in which subject review happened.
  Within a scope, the three states are:
  - scope absent: needs review;
  - scope present with no associations in it: deliberately no known subjects;
  - scope present with associations: tagged.
- **`sources`** holds normalized http(s) URLs only (fragment stripped,
  deduplicated, capped at 20). They are written only when a caller passes one:
  localize, card or Chub import. Collection harvesting passes none, keeping the
  collections spec's rule that accepted collections carry no source address.
- **`description_conflicts`** exists only while a migration-detected
  disagreement is unresolved (§11).

**Writing the sidecar:**

- It is read-modify-written whole, under a per-id lock following the
  `locks._ProcessScopedLock` precedent (`image_collection_lock`).
- That lock is a **leaf**. Code may take it while holding a campaign lock, but
  nothing acquires a campaign lock while holding it.
- No public function takes a `cid`, so the module sits outside the lock-domain
  survey by construction. That is correct: an object is library-global.
- Reads are tolerant. A missing or garbled sidecar makes the placement
  unresolvable, which is not an error (§6).
- Reads are memoized through `store.statcache` because serving and listing hit
  them on every request.

### 4. Placements

```json
{"format": 1, "image": "px1-…"}
{"format": 1, "image": "px1-…", "focus": 43}
```

- A placement holds the id plus occurrence-only data. Today that is only avatar
  `focus`, which absorbs `focus.json`. Future crop, alt-text override or
  presentation mode would also go here.
- A placement is written with `atomic.write_text` under the existing per-name
  `assets._image_lock`.
- **Shared facts never go on a placement.** No placement metadata is created
  just because a picture was placed. A contextual semantic override is a future,
  explicit act, and this design adds none.

### 5. Ingest: the one way in

```
image_store.ingest(data, *, source_url=None) -> ImageObject
```

1. **Validate.** Sniff the format; refuse what cannot be sniffed, which closes
   the "AVIF saved as .png" hazard in `fetch.download_url`. The caller's
   existing byte cap still applies (libraries 25 MB, remote fetch 100 MB).
   Record uploads stay uncapped, as today. Adding a cap is a separate decision.
2. **Byte hash, and the exact-byte shortcut.** Compute `byte_sha256` and look it
   up in a **rebuildable** blob→object index at
   `.cache/image-store/blob-index.json`. The index is derived from object
   sidecars (each names its retained blob), so deleting it costs one rescan,
   never data. A hit returns that object without decoding. This shortcut is also
   what keeps decoder drift harmless for identical bytes.
3. **Decode and pixel hash** (§1.2), on a miss only.
4. **Find or create the object.**
   - If `objects/…/px1-<hex>.json` exists, keep its retained blob and discard
     the incoming representation. Steady state never swaps blobs.
   - Otherwise publish the blob, then the sidecar. The order is chosen so a
     crash leaves an orphan blob rather than a sidecar pointing at nothing.
5. **Merge `sources`** without duplicates.
6. **Return the object.** The caller then writes a placement.

**Card-import avatars** have their `chara` / `ccv3` tEXt chunks stripped before
ingest. This is a chunk-level copy with no re-encode, so the pixels are
untouched. Without it, the retained blob for art shared by two cards would serve
and export one card's JSON under the other's avatar URL, and could carry it into
a bundle of an unrelated world. The card text already lives in the card record,
and `cards.dumps` re-embeds on export.

**Other embedded metadata.** EXIF and XMP in a retained blob are otherwise kept
as uploaded. When pixel duplicates collapse, the first-ingested representation's
metadata is what later exports carry. This is stated in the privacy notes of the
bundle docstring.

### 6. Resolution and transitional reads

```
image_store.resolve_placement(d, name) -> ResolvedImage | None
# image_id, pixel_sha256, blob_sha256, blob_path, ext, mime, width, height, focus
```

- The `assets` primitives are re-implemented on top of `resolve_placement`, so
  every surface changes at once.
- **`path_in(d, name)`** returns the ref's blob path when the ref resolves.
  Otherwise it returns the newest legacy `<name>.*` file, which is today's rule.
  - When both exist, **the ref wins** (the brief's rule).
  - A ref that does not resolve (missing object or blob, for example
    mid-sync) falls back to a legacy file if one exists. Redundant data beats
    lost data.
  - Otherwise it returns `None`.
- **`list_in(d)` / `_candidates(d)`** union ref names with legacy names. A ref
  row is `{name, ext, v, image_id}`. A legacy row keeps today's
  `{name, ext, v}`. Listing a directory reads its refs and (memoized) sidecars.
  It never scans the global store.
- **`put_in(d, name, data, ext)`** works in three steps:
  1. ingest the bytes;
  2. write the ref under the per-name lock;
  3. remove that name's legacy siblings with today's identity-checked unlink.

  After this lands, no caller writes image bytes into a record or library
  directory.
- **`delete_in`** removes the ref and any legacy files. It never touches the
  object or blob (§10).
- **`assets.image_path`** keeps returning a `Path`, now the canonical blob path.
  Serving, export, thumbnails, drafts (`image_drafts.data_uri`) and
  `cards._stored_avatar` keep working unchanged, because they operate on paths.
- **Readers that reach files without the primitives must be converted:**
  - `greeting_images.local_path` and `_record_image_path`;
  - `overlay.shadowed_images` and campaign slimming: compare `image_id` when
    both sides are refs, and resolved-path bytes otherwise;
  - `image_descriptions._names`;
  - `image_collections.referenced` and `guard_write`;
  - the Chub gallery wipe;
  - `backups._is_image_file`.

  The implementation plan must carry a grep-backed list of these. The roster
  test (§13) is the guarantee.
- **Stat-keyed memos.** `assets.version_art` and its `avatar_v` / `version_facts`
  callers must include the `image-refs/` directory in their signature, because a
  ref write changes that directory, not the version directory's files.

**Mixed-version devices.** An older client writing a legacy file beside an
existing ref is shadowed by the ref. Upgrade every device sharing a store. The
release note says so.

### 7. HTTP identity

- **Ref-resolved originals:**
  - `v = blob_sha256`, the full 64 hex digits;
  - ETag `"<blob_sha256>"`.

  If the bytes behind a URL change, `v` changes. The pixel id would not satisfy
  that, because two encodings of one picture differ in MIME and bytes.
- **Legacy files** keep the mtime-size token until migrated.
- **Thumbnails of ref-resolved images:**
  - key `sha256("cas|<image_id>|<blob_sha256>|<width>|<encoder>")`;
  - ETag `"<image_id>-<bucket>-<generation>"`;
  - no stat call.

  One image used in fifty places gets one thumbnail per bucket. `REVISION` and
  `generation()` keep working unchanged.
- **Listings** add `image_id` to every row that has one: record image listings,
  both library listings, gallery rows, character-detail `image_v` companions,
  and PUT responses. The frontend types gain an optional `image_id`. Consumers
  that use only `name`, `ext` and `v` are untouched.
- **URLs in prose** stay bare and logical (`…/images/avatar`), as today. No
  markdown is rewritten.

### 8. Reference operations replace copies

Each of these reads an id and writes refs. None of them reads and rewrites
bytes. A legacy source is ingested once first, then linked.

- **`image_subjects.copy_to_character`** (the route keeps the
  `copy-from-greeting` name): resolve the source placement to an id, then write
  the destination ref.
- **Promotion `gallery_N` ↔ `avatar`** becomes a ref swap under the existing
  two-name lock:
  1. Write an intent journal `image-refs/.promote.json` recording both ids.
  2. Write the avatar ref, without focus.
  3. Write the gallery ref.
  4. Delete the journal.

  A read that finds the journal completes steps 2–4. The steps are idempotent,
  as `_heal_stranded_promotion` is today. User-visible behaviour is unchanged:
  the image becomes the avatar, the old avatar takes the gallery slot, nothing
  is lost, and focus is cleared.
- **Overlay `promote_image`, `copy_record_dir_down`, `sync._copy_tree`,
  `fork._copy`, `fork_world`:** these copy trees, and a tree now holds refs, so
  they copy refs. Within one library, no blob is duplicated. They need no change
  beyond the reader conversions in §6. Campaign and world forks additionally copy
  the scoped metadata of §9 into the new scope.
- **Campaign materialization** never copied assets and still does not.

### 9. Shared metadata

**Descriptions** move to the object. Editing a description edits it everywhere
that picture is placed.

- `image_descriptions.read_in` / `set_in` keep their directory-level signatures,
  so the seven route surfaces and `overlay.read_descriptions` keep their shape.
  Internally:
  - a **ref-resolved** name reads and writes the object's `description`;
  - a **legacy** name reads and writes `descriptions.json` as today.
- Writing to the object deletes that name's legacy key in the same operation.
  There is never a dual write.
- `""` versus absent is preserved end to end.

**Subjects** move to `associations` and `reviews`. Greeting `subjects.json`
keeps only what has no object:

- remote http(s) reference URLs;
- unmigrated legacy names.

A local reference URL resolves through its placement to an object. Tagging an
image in one greeting therefore tags it in every placement of the same world.
The same object can be tagged independently under another world's scope.

**Derived usage.**
`image_usage.find(image_id) -> list[Usage]` scans refs across worlds and
campaigns, plus collection manifests. It is exposed as
`GET /api/images/{image_id}/usage`, which returns
`{characters, pcs, entities, greetings, world_images, campaign_images, covers,
collections}`. Placing an image never writes the inverse into the object. A
rebuildable `.cache/image-store/usage/` index may be added if profiling demands
one; deleting it never loses data.

**Scope lifecycle.** World and campaign ids are reusable slugs: deleting
`realm` and creating a new `realm` must not inherit the dead world's tags. The
same reason makes `runs.forget_subject` exist.

- Deleting a world or campaign strips its scope from every object that names
  it. This is a full sidecar sweep, which is rare and off the play path.
- Forking a world or campaign copies the source scope's associations and
  reviews to the new scope on each reachable object.
- Bundle import maps the bundle's source scope to the new world id (§10).

**Art recall.** `context.art` dedupes candidates by `image_id`. Five placements
of one picture are one ranked entry. The handle still names one reachable
placement, chosen deterministically in the order the cast tier, the location
tier, entities, then the library. Ranking may still use why an image is
reachable. Legacy rows, which have no id, are not deduplicated.

**Campaign-side descriptions.** Today a campaign may describe an inherited world
image without diverging it (`overlay.set_description`), and that text overrides
the world's for that campaign only. Under this design a description is a visual
fact on the object, so a campaign-side description write edits the shared
object. Where the world's text already differs, migration reports a conflict
(§11).

This is a real semantic change. **Open decision (D1)** asks the reviewer to
confirm it, or to keep the campaign-side override as the first explicit
contextual override.

### 10. Collections, covers, libraries, export, bundles, backups

**Collections:**

- Manifest `format: 2` holds `{"format": 2, "members": ["px1-…", …]}`.
- `put_member` becomes `ingest`; no world-library file is written.
- Format 1 manifests are read by resolving `collection-image-<sha>` names
  through the world library. Migration rewrites them to format 2.
- Member immutability is now structural, because a `px1` id cannot come to mean
  other pixels. `guard_write` is retired once no format 1 manifest remains in a
  world.
- Rendering and per-occurrence selection are unchanged.

**Covers** become `assets/image-refs/cover.json` placements, using the same
ingest and resolution. `covers.validate` remains the cover upload gate.

**World and campaign libraries** become named placements. Tombstones, `hidden`,
the no-shadowing rule and `inherited` are unchanged. They address logical names,
not objects.

**Export** needs no new dedupe. Placements of one object resolve to one blob
path, so the existing path registry packs it once and references it N times.
The packed extension comes from the blob.

**World bundles, format 2:**

- **Export:**
  - Collect every `px1` id reachable from the world tree: refs under every
    record, greetings, the library, the cover, and collection manifests.
  - Add `image-store/blobs/…` and `image-store/objects/…` beside `world/`.
  - Each object is a **projection**. It keeps global fields and associations or
    reviews in that world's scope only, and has no `sources`. Provenance stays
    in the library it was gathered in, matching what a bundle disclosed before
    this change.
  - The projection is computed at export time and is never stored.
- **Import:**
  1. Re-hash every blob and reject any whose name and content disagree.
  2. **Recompute** each object's pixel id from its blob.
  3. Use the locally computed id. If it differs from the bundle's id (decoder
     drift, or a hostile bundle), rewrite the staged tree's refs from the
     bundle's id to the local one. A bundle can never claim an id for pixels it
     does not contain, so it cannot poison a future dedupe.
  4. Merge blobs and objects into the global store:
     - an existing object keeps its blob;
     - descriptions merge by the §11 rules;
     - the bundle's scope is rewritten to the new world id.
  5. Publish the world.

  Format 1 bundles import as today, as legacy files.
- Bundle validation continues to reject path traversal and malformed names via
  `ziputil.scan` plus the hex regexes.

**Backups:**

- The full backup already archives all of `home()` except `.cache` and staging,
  so `assets/image-store/` is included. Rebuildable caches stay excluded.
- The image-only backup (`create_image_backup`) must include
  `assets/image-store/` plus every `image-refs/*.json`. Without the refs it
  would be a pile of hashes with no names.
- A test pins both.

### 11. Migration

Migration is an explicit maintenance operation, never a startup step: hashing a
whole library would stall `_lifespan`. It runs from:

- `python -m grimoire.images migrate [--dry-run]`;
- `POST /api/maintenance/images/migrate` (a `global`-subject detached run per
  the detached-runs contract, so it is pollable and outlives the request);
- a Settings → Storage card showing the report.

**A. Inventory** follows an explicit roster, `image_store.SURFACES`. Each entry
says how to enumerate its directories:

- per-version characters, PCs and every `ENTITY_KINDS` kind, plus greetings,
  each under both world and campaign roots;
- world and campaign libraries;
- world and campaign covers;
- collection manifests.

The roster test (§13) fails when a new image-bearing surface is added without an
entry. The test discovers surfaces from the code's own path builders and
`ENTITY_KINDS`, not from a parallel list.

**B. Byte grouping.** Hash every legacy file's bytes. The work map lives in
`.cache/image-store/migration/`. It is restartable and losing it costs only
time.

**C. Pixel grouping.** Decode each distinct byte hash once and group by `px1`.
Files that do not sniff, or that sniff but fail to decode, are **left untouched
and reported**. This follows the brief's failure rule. Ingest would give the
latter an opaque identity, but migration does not move what it cannot verify.
Unrelated images continue.

**D. Metadata merge** per visual group:

- **Descriptions:**
  - all absent → absent;
  - one distinct non-empty text (possibly with some `""`) → that text;
  - only `""` → `""`;
  - several distinct non-empty texts → `description` stays absent and the
    candidates go to `description_conflicts: [{text, from}]`.

  The describe queue lists a conflict with its candidates. Resolving it writes
  `description` and clears the list.
- **Subjects:** union per scope. A scope reviewed empty in one greeting and
  tagged in another unions to tagged, and the disagreement is reported. Nothing
  is silently discarded.
- **Sources:** normalized and deduplicated.
- **Focus:** never merged. It moves to the avatar ref it came from.

**Retained blob** for a group:

1. most placements;
2. then the smaller file;
3. then the lowest byte hash.

**E. Write**, per occurrence, with the occurrence's campaign lock held where it
has one and its per-name image lock always held:

1. Re-stat the legacy file and confirm it still has the hashed identity.
   Otherwise skip it; the next run picks it up.
2. Publish or verify the blob, then the sidecar.
3. Atomically write the ref, including any focus.

Legacy files are still present at this point.

**F. Verify, then clean up.** For each occurrence, confirm all of the following:

- the ref resolves to the intended object, and the object to the intended blob;
- the blob re-hashes correctly;
- the route-level `path_in` returns the blob;
- the moved focus and description read back.

Only then is the legacy file deleted. The migrated legacy sidecar key
(`descriptions.json`, `subjects.json`, `focus.json`) is removed in the same
step, and an emptied sidecar file is deleted. Format 1 collection manifests are
rewritten to format 2 after all their members verify.

A crash at any point leaves one of:

- the legacy representation only;
- both, in which case the ref wins and is verified, and a rerun cleans up;
- the new representation only.

It never leaves neither. Rerunning is idempotent.

**Report.** Dry-run and real runs produce the same report:

- legacy files;
- unique byte streams;
- unique visual images;
- exact duplicates;
- pixel-identical variants;
- descriptions merged;
- description conflicts;
- subject disagreements;
- untouched or undecodable files, with paths;
- bytes before, after and reclaimed.

The report lives under `.cache/` and in the run result. It is never committed
anywhere, and docs and tests use invented fixtures only.

### 12. Garbage collection

Deleting a placement deletes only the ref. There is no reference count.

`python -m grimoire.images gc [--dry-run]` (and a maintenance route) computes
reachability:

- all refs under every world and campaign root;
- all collection manifests;
- objects named in an in-progress migration work map.

From those it derives reachable objects, then reachable blobs.

**Candidates** must meet all of these:

- unreachable;
- older than the grace period: 30 days by sidecar and blob mtime, configurable
  but never below 7;
- unreachable on two scans at least 24 hours apart. The first scan records
  candidates in `.cache/image-store/gc.json`.

**GC refuses to delete anything** when reachability cannot be established:

- any ref or manifest fails to parse;
- a world or module staging directory exists;
- the store root changed mid-scan.

**Dry run is the default.** Deletion requires the token of a dry-run report
from the same day. The report gives:

- placement, object and blob counts;
- unreferenced objects;
- objects old enough to collect, and objects protected by grace;
- exact reclaimable bytes.

Migration's own cleanup of legacy duplicates (§11 F) is separate from GC and
needs no grace, because it removes only files it has just replaced and
verified.

### 13. Tests (in addition to the brief's §44 list, which is adopted whole)

**Hashing:**

- identical bytes;
- PNG text-chunk variant;
- JPEG EXIF variant;
- EXIF-rotated versus physically rotated;
- transparent pixels with differing invisible RGB;
- one-pixel change;
- ICC-profile variant (different id);
- animated frame change and animated timing change;
- CMYK and 16-bit inputs, which take opaque identity;
- the checked-in fixture ids.

**Ingest:**

- first ingest creates a blob and an object;
- exact-byte duplicate creates neither;
- pixel duplicate creates neither, and the retained blob is unchanged;
- different pixels create a new object;
- sources merge without duplicates;
- card avatar tEXt is stripped;
- a deleted blob index rebuilds.

**Placements:** these run per surface in the roster, parameterized over it.

- write through the route;
- assert no image bytes appear under the record directory;
- read back through serve, list, thumbnail, export and art handle;
- copy-from-greeting writes zero blobs;
- promote swaps refs, survives a crash at each step via the journal, and clears
  focus;
- delete one of several placements, and the others resolve.

**Transition:**

- ref and legacy coexisting, where the ref wins;
- an unresolvable ref falls back to legacy;
- stat memos invalidate on a ref write;
- the frozen campaign fixture (`backend/tests/fixtures/frozen_campaign/`) still
  sweeps to its unchanged snapshot. Its `home/` is never migrated.

**Metadata:**

- a shared description is visible through every placement;
- writing it deletes the legacy key;
- a world-scoped tag is visible across that world's greetings and absent in
  another world;
- world delete strips the scope;
- fork copies the scope;
- usage is derived and never stored.

**Migration:**

- exact and metadata-only variants collapse;
- conflicts are preserved;
- a crash injected before the ref write, after the ref write, and after cleanup;
- rerun is idempotent;
- one corrupt file does not stop the rest;
- a concurrent upload during migration is not lost.

**Collections, export, bundles, backups:**

- format 2 round-trip;
- a format 1 manifest is read and then rewritten;
- a repeated image is packed once;
- a fork writes zero blobs;
- the bundle carries its dependencies and only that world's scope;
- import reuses an existing blob;
- import recomputes the id and rewrites refs on mismatch;
- full and image-only backup contain the store and refs.

**GC:**

- referenced and multiply-referenced images are kept;
- recent orphans are protected;
- an old orphan appears in a dry run only after two scans;
- a parse failure aborts;
- nothing outside `image-store/` is ever deleted.

**Existing guards:**

- new modules satisfy `test_atomic_guard`, `test_paths_guard` and
  `test_import_guard` (`image_store` sits **below** `assets`, which already
  cannot import `image_descriptions`);
- a decision on `store/__init__` exposure, with the `store_api_baseline.json`
  regeneration if exposed;
- `docs/store-guarantees.md` gains a section on blob immutability and the
  object-sidecar lock, held by `test_docs_guard`.

### 14. Delivery stages

One spec, four implementation plans. Each stage is shippable alone and keeps
every invariant above for what it has touched.

1. **Core: identity, store, placements.**
   - Modules `image_hash`, `image_store` (ingest, resolve, the blob index) and
     the `assets` primitives on refs.
   - The §6 reader conversions.
   - Serving `v`, ETag and `image_id`.
   - Thumbnail keys.
   - Reference copy and promote; focus on the placement.
   - Bundle format 2 and backups.

   Bundles and backups are in this stage, not later: the moment a world can
   hold a ref, a bundle without the store is broken.
2. **Shared metadata.**
   - Descriptions and subjects on objects, with no dual write.
   - Scope lifecycle on delete and fork.
   - `image_usage` and its route.
   - Art-pool dedupe.
3. **Collections format 2** and retirement of the collection write guard.
4. **Migration and GC.** Roster, CLI and route, the Settings report card,
   cleanup of legacy sidecars, and GC.

### 15. Decisions taken on the brief's behalf, and open ones

**Taken** (with reasons above):

- ICC and PNG colour chunks are part of pixel identity (§1.2).
- An opaque identity domain exists for many-to-one decodes, oversized rasters
  and undecodable-but-sniffable bytes (§1.2).
- Blob lookup goes through a rebuildable index derived from sidecars (§5).
- Card tEXt is stripped at import (§5).
- Bundle projections omit `sources` (§10).
- Bundle import recomputes ids (§10).
- Promotion uses an intent journal (§8).
- No feature flag. Stage 1 ships complete, with bundles and backups.
- Migration is a maintenance operation, not a startup migration (§11).
- GC requires two scans (§12).
- The ref wins over a legacy file, and mixed-version devices are unsupported
  for writes (§6).

**Open, for the reviewer:**

- **D1.** Should a campaign-side description edit change the shared object
  (recommended, as the brief's model), or should today's campaign override
  survive as the first explicit contextual override?
- **D2.** Should record-image uploads gain the 25 MB library cap now that every
  upload is decoded at ingest? This design keeps today's no-cap behaviour.
- **D3.** Is the blob-index cache acceptable as the one place byte→object is
  looked up, or should ingest always decode (simpler, slower)?
