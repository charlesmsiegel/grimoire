# Image identity and placements

**Status:** first slice of [Image Identity And Placements](roadmap/sprints/image-identity-and-placements.md) (Sprint 018, Wave 3). It is implemented as:
- `grimoire.images`: pure values, a payload codec and the placement resolver;
- `grimoire.imagestore`: a store-backed service;
- an additive blob and asset-record family in [the native store](STORAGE.md#images-and-blobs-018).

There is **no UI, no importer and no image generation**. Nothing is fetched from the network, and no model is called. The Wave 4 importers ([026](roadmap/sprints/v1-to-v2-import-apply.md) for V1, [037](roadmap/sprints/character-card-ingestion.md) for cards) call this API. A green test suite proves this contract, not a playable app.

## Three things, never conflated

| Concept | What it is | Identity | Mutability |
|---|---|---|---|
| **Blob** | The exact bytes of a picture | `sha256` of the bytes, plus a media type sniffed from magic bytes | Create-only. The only exception is the explicit repair of a file whose bytes no longer hash to its name. Shared by the whole library. |
| **Image object** | "This picture": owned by one source or one campaign, points at exactly one blob, carries provenance | `ImageRef`: `<source>#image/<id>` or `campaign:<c>#image/<id>` | Create-only. A different picture, or new metadata, is a new image object. |
| **Placement** | One use of an image object by one subject in one slot: its role, crop, focus and provenance | `PlacementRef`: `<subject>#placement/<slot>`. The subject is an **unpinned** definition or a campaign instance. | Compare-and-swap, one file per (subject, slot) |

- A V1 `px1-…` id is not a file path, a blob or an actor identity. The importer chooses an image id and records the old id in `provenance.legacy_id`.
- Two image objects in different sources may share a blob. The bytes are shared; ownership is not.
- A placement attaches to the **entity**, across all of its revisions. A V1 "version" is a separate portrayal entity in V2 ([IDENTITY](IDENTITY.md#revisions-pins-and-portrayals)), so per-version art means per-entity placements.
- Ownership is **structural**. A source's images and placements live under its own `_source/`, and a campaign's under its own folder. No field can claim another owner. This mirrors manifest links' `declared_by`.
- `hide` is the V2 spelling of V1's "hidden" placement and of the sprint's `clear`.

## Payloads (provisional, schema ids `/0`)

### Image object: `grimoire.image/0`

```json
{"blob": {"sha256": "<64 hex>", "media_type": "image/png", "size": 1234},
 "provenance": {"legacy_id": "v1:<archive>:<path>", "origin": "…", "note": "…", "x-anything": "kept"}}
```

- `media_type` is one of `image/png`, `image/jpeg`, `image/gif` or `image/webp`, and it agrees with the blob's extension.
- `provenance` is **open**:
  - `legacy_id` must be a `LegacyId`;
  - `origin` and `note` are text of at most 2000 characters;
  - any other key is preserved. 037 can therefore record card, CHARX or embedded origins without a schema change.
- Unknown top-level fields are preserved.

### Placement: `grimoire.placement/0`

```json
{"mode": "set", "role": "avatar", "image": "world:harbor#image/wren",
 "crop": {"x": 0, "y": 500, "w": 10000, "h": 6000}, "focus": {"x": 5000, "y": 3000}}
```

- **`mode`** is one of:
  - `set`: show `image`;
  - `adjust`: keep the inherited picture and override `crop` and/or `focus`. `image` must name that inherited picture;
  - `hide`: show nothing in this slot. A source placement may only `set`.
- **The slot decides the role:**

  | role | slot |
  |---|---|
  | `avatar` | `avatar` |
  | `portrait` | `portrait` |
  | `expression` | `expr.<outfit>.<name>`. `<outfit>` and `<name>` match `[a-z0-9][a-z0-9_-]*`, and the payload repeats them in `expression`. |
  | `gallery` | any other segment |

  Every slot is at most 123 characters.
- **`crop` and `focus`** are integer **basis points** (0–10000) of the whole image; floats are refused.
  - `crop` needs `w, h ≥ 1` and must lie inside the image.
  - `focus` is independent of `crop`.
  - When absent, they mean the whole image and its centre.
  - `adjust` overrides each of `crop` and `focus` whole; it does not merge their keys.
- **Strictness.** Inside `crop`, `focus` and `expression`, an unknown key is refused. Unknown top-level fields are preserved.
- **Rewriting.** To rewrite a placement, modify the `Placement` returned by `ImageLibrary.read_placement`, so its payload extras survive. The service also carries the envelope's own unknown fields forward.

## Writing: `ImageLibrary` and `ImageBatch`

```python
images = ImageLibrary(store, handle, owner="human:owner")
batch = images.change()
batch.put(SourceManifest("card:mira", owner), None)        # an importer's own records, same transaction
batch.put(RecordEnvelope(mira_r1, payload), None)
ref = batch.add_image("card:mira", "mira", png_bytes, {"origin": "card"})
batch.place(SourceRef("card:mira", "character", "mira"), "avatar", Placement("set", "avatar", ref), None)
batch.preview()                                            # {logical path: exact bytes} for approval
batch.commit(approvals)                                    # one guarded store transaction
```

- **Validation.** Each op is validated in call order against disk plus the batch's earlier ops, so an image must be added before it is placed. A refused op raises and leaves the batch unchanged; that includes an id or slot no portable path can hold (a Windows device name such as `con`), which raises `UnstorableIdentifier` when the op is added. Ops share one verified view of the library until any commit of the store, so an edit made by another program meanwhile is seen at `commit`, which re-validates the whole batch under the store lock against fresh facts. Validating many placements costs one scan per changed source, not one per subject.
- **Blobs** are deduplicated by `(sha256, ext)`:
  - bytes already stored, or added twice in one batch, are put once;
  - a blob that appears on disk between `preview` and `commit` with identical bytes is simply dropped.
- **Preview binding.** If what `commit` would write differs from the last `preview()` in any other way, it raises `StaleWrite`, and a new preview is needed.
- **Pairs formed since.** If a placed instance's id was taken by another kind after its op was added (W11), `preview` and `commit` raise `StaleWrite` naming that instance's file (`instance_id_conflict`) and write nothing. The batch is unchanged, so it can be committed once the pair is undone.
- **Convenience.** `images.add_image(...)`, `images.place(...)` and `images.repair_blob(...)` return one-op batches.
- `put` accepts only `RecordEnvelope` and `SourceManifest`. Scope and chat records stay with [`ChatLibrary`](CHATS.md).

### Rules

A refused rule writes nothing. Reasons are machine-readable: `InvalidImage.reason` and `InvalidPlacement.reason`.

| # | Rule | Reason |
|---|---|---|
| W1–W2 | The bytes must sniff as PNG, JPEG, GIF or WebP, be nonempty, and be at most 32 MiB | `unsupported_media`, `empty`, `too_large` |
| W3 | Image ids are never reused | `image_exists` |
| W4 | Identical intact bytes are stored once. If an existing blob with that name is corrupt, `add_image` is refused and you must `repair_blob` first. A look-alike spelling raises `NameCollision`. | `blob_corrupt` |
| W5 | The subject must exist. For a definition that means an admitted source entity, or a revision in the batch whose source is admitted; a batch manifest counts only for a `missing_source`, never for an `excluded_source`. For an instance it means a readable instance, or one in the batch. A campaign image needs an existing campaign: a canonical `scope.json`, an instance, or an instance put in the batch. | `missing_subject`, `missing_scope` |
| W6 | A placed image must exist, be readable, and its blob be present with the declared size | `missing_image`, `invalid_image`, `missing_blob` |
| W7 | A source placement may use only its **own** source's images: not a parent's, a sub-source's or a campaign's. A campaign placement may use that campaign's images and its instance's derivation source's. | `wrong_scope` |
| W8 | The mode, role, slot and field rules above | `invalid_field`, `mode_not_allowed`, `role_slot_mismatch` |
| W9 | An `adjust` needs a shown inherited picture in that slot, and it must name that picture | `nothing_to_adjust`, `stale_adjust` |
| W10 | Compare-and-swap on a placement's expected etag (`None` for a new one) | store `StaleWrite` |
| W11 | A `set` or `adjust` may not name an instance whose id another kind of its campaign also uses: `load()` leaves both members of such a [pair](IDENTITY.md) out, so the id is no one individual. Only instances on disk count, since the store refuses a `put` that would form a pair. A `hide` stays allowed as the repair that takes art placed before the pair formed off the slot. Adding an image never names a subject, so a campaign image is unaffected. | `instance_id_conflict`; store `StaleWrite` at `preview`/`commit` for a pair formed since |

### Blob repair

`repair_blob(bytes)` stages only a repair. Its target must be a regular file, at the exact name, whose bytes do not hash to that name.

| On disk | Result |
|---|---|
| absent, or intact | refused `nothing_to_repair` |
| look-alike name | `NameCollision` |
| over 32 MiB | refused `oversize_blob`. It cannot be repaired through the API; delete it by hand. |

**The corrupt bytes are overwritten, not preserved.** The overwrite is a separately approved `APPLY` on the blob's path.

## Reading: `effective_placements(subject)`

The result is a `Resolution`. Each slot is exactly one of:

- **`Shown(slot, role, image, blob, crop, focus, layer, provenance)`**, where `layer` is `source`, `campaign` or `campaign_adjusted`.
- **`Hidden(slot)`.** A campaign `hide` never falls back to the source's picture, or to any picture with the same name.
- **`Unavailable(slot, reason, text)`.** The reasons are:
  - `missing_image`, `invalid_image`, `wrong_scope`;
  - `missing_blob`, `blob_size_mismatch`;
  - `invalid_placement` (unreadable, malformed, or an unknown mode or role);
  - `unsupported_format`;
  - `nothing_to_adjust`, `stale_adjust` (the inherited picture changed after the adjust was written).

  Nothing is substituted by name, and nothing falls back.

`problems` lists:
- stray names and unreadable listings;
- `ambiguous_derivation` and `invalid_derivation`;
- a subject or derivation source that is `excluded_source`, `missing_source`, `entity_dropped`, `missing_entity` or `missing_revision`.

**It fails closed.**
- A definition whose source is excluded, missing or dropped shows **no slots**, and its orphan placement files are never shown.
- An instance inherits from its derivation entity: `derived_from` if present, else `base`, taken unpinned.
  - If both are present and name different entities, or a value cannot be parsed, nothing is inherited. Only campaign images are then in scope.
  - An unreadable or newer-format instance is handled the same way.
- A derived variant or alternate portrayal in another source does **not** inherit art in this slice. That policy belongs to [038](roadmap/sprints/expression-and-art-lineage.md), over 022's derivation links.
- **A damaged campaign layer never reveals the source picture.**
  - A campaign placement file that is a link or a directory gives its slot `Unavailable(invalid_placement)`.
  - If the instance's placement folder is a link, is unreadable, exists only under a look-alike spelling, holds any non-canonically named `.json` file (it could be a placement: `Avatar.json`, `avatar copy.json`, a sync tool's `Avatar.sync-conflict-….json`), or has a differently spelled sibling folder at any level below `placements/` (`Wren-b` beside `wren-b`), every inherited slot, and any `adjust` of one, is `Unavailable(invalid_placement, "campaign layer damaged")` instead of `Shown`, because the layer might be hiding something.
  - A clearly unrelated stray file (`notes.txt`) is only reported.
- **A bad blob entry never crashes a resolution.** A linked or non-regular blob path counts as `missing_blob`. Resolution never reads or hashes blob bytes.
- `read_image` and `read_image_bytes` refuse with `InvalidImage`: `excluded_source` or `missing_source` for an image whose source `load()` would exclude, `missing_image`, `unsupported_format` for a record written by a newer version, `invalid_image` for one that cannot be read, decoded or reached (a link), and, for the bytes, `missing_blob`. Only a blob whose bytes no longer match its name raises `BlobCorrupt`.
- `read_placement` refuses a malformed or undecodable placement with `InvalidPlacement("invalid_placement")` carrying its `etag`, so it can be replaced by CAS; a newer-format placement is `unsupported_format`, with no etag, because the store never overwrites it.
- A batch refuses a second `put` of the same manifest or record with `InvalidBatch("duplicate_put")`, leaving the batch usable. A source reference that parses but that no portable path can hold (`world:con`) names nothing: it never crashes a resolution or a placement.
- An empty folder where a campaign placement file belongs is reported, and its slot is `Unavailable(invalid_placement)`.
- At write time, an instance whose derivation entity does not exist has only campaign images in scope, exactly as the resolver decides.

The resolver is pure (`grimoire.images.resolve`). It checks a blob's presence and size, not its digest. `read_image_bytes` verifies the digest and raises `BlobCorrupt`.

## Authorization, self-containment and privacy

- **Authorization.**
  - Blobs live at the library-global `assets/blobs/…`. A grant on `sources/<s>` or `campaigns/<c>` never covers them: each new blob needs an approval or an `assets` grant.
  - A deduplicated blob is not re-staged, so no new byte approval happens for it.
  - `preview()` therefore reveals whether identical bytes already exist. That is acceptable only because the service is trusted code.
- **Self-containment.** A source folder no longer carries its own bytes. Moving or removing it leaves its blobs in `assets/`. Transplanting it to another library by hand resolves `missing_blob` until its blobs are copied. [Device sync](SYNC.md#art) carries blobs with the images and placements that need them, and never delivers a picture without its blob; a blob deleted by hand on both devices and imported again later syncs like a new one. Garbage collection is deferred.
- **Privacy.** Tests use synthetic byte strings and the existing synthetic V1 fixture only.

## V0.2.1 mapping (for 026)

The [inventory](MIGRATION_INVENTORY.md) rows map as follows. Test-only adapter: `backend/tests/test_imagestore_inventory.py`.

| V0.2.1 | V2 |
|---|---|
| Image object with an intact blob | `add_image` in the owning source, with `provenance.legacy_id` |
| A V1 object placed from several worlds (V1 objects are library-global; V2 image objects belong to one source, and W7 refuses another source's image) | one `add_image` **per owning source**, each with the same `provenance.legacy_id`; the bytes are one shared blob. Art used only by a campaign may instead be one campaign image |
| `resolved` avatar / gallery placement | `set`, role `avatar` / `gallery`, slot = the V1 name |
| `focus` (0–100, vertical) | `focus = {x: 5000, y: focus × 100}` |
| Live expression sprite `<outfit>/<name>` | `set` in `expr.<outfit>.<name>` |
| Campaign layering `hidden` | `hide` |
| Campaign layering `focus_override` (`focus_only`) | `adjust` naming the inherited image, with the new focus |
| Campaign layering `overridden` | campaign `set` |

**Unmapped or unrepresentable.** The importer must report each of these as unresolved; 018 defines no fallback:
- `unarrived_blob` / `hash_mismatch` objects: no intact bytes, so no image object;
- `dangling_object`: refused with `missing_image`;
- a world-level `focus_only` placement: sources may only `set`;
- hidden outfits and expressions, the default outfit, and stale expression revisions;
- `animated`, width/height, descriptions, associations, reviews, `sources` lists and unknown sidecar fields. These may be carried as preserved `provenance` keys at the importer's choice;
- the `cover`, `library` and `embed` slots, collections, legacy loose images and Markdown image references;
- gallery names that are not V2 segments (uppercase, spaces, non-ASCII), names that fold to a collision, and expression slots longer than 123 characters;
- gallery names that are V2 role slots: `avatar`, `portrait` or anything starting `expr.` (the slot decides the role, so such a gallery placement is refused with `role_slot_mismatch`);
- slot or image ids that are Windows device names (`con`, `aux`, …), which the store refuses as `UnstorableIdentifier`.

## Limits and later milestones

- **Limits:**
  - Gating uses `check_source`/`check_definition`, which consult the store's shared, cached [`excluded_sources`](STORAGE.md#read-only-helpers-107). A source excluded **only** by a cross-source `identity_conflict` therefore shows no art, exactly as `load()` would exclude it. [Device sync](SYNC.md#art) reads the same verdict on both devices: art of a source either one excludes is never copied, so a device never shows a picture the other excludes, until the source is repaired. Writing art never invalidates that cache; a batch verifies it once until a commit, and each preview, commit or resolution once ([STORAGE](STORAGE.md#read-only-helpers-107) has the costs and the one staleness limit). Reading many images one by one should be wrapped in one `store.read_pass()`.
  - A damaged campaign layer (a linked, unreadable or look-alike folder or file) shows no source picture at all: every inherited slot, and any `adjust` of one, is `Unavailable(invalid_placement)`.
  - Art follows the entity, not an instance's pinned `base` revision. Pinning placements to a revision or release belongs to 042/056.
  - There is no unplace or delete, because the store has no deletes. A source avatar can be removed only by hand.
  - Direct store use (not the service) of a blob create or repair over a hand-placed file larger than 32 MiB reads that file whole during the commit.
  - **Placement subject ids stay taken.** `placement_subject_ids(store, campaign_id)` returns every id with a campaign placement folder (`placements/<kind>/<id>/`) from listings only, and a look-alike folder keeps the id it folds to. It is part of the one reservation set, [`campaign_taken_ids`](CHATS.md#taken-instance-ids), so a hand-deleted actor's id never goes to a new individual who would show the old one's `hide` or `set` art. A folder that could hold a subject but is a link or cannot be listed raises `TakenIdsUnlisted`.
- **Later milestones:** gallery ordering and collections; expression manifests and outfit sets; captions and subjects; scene art; legacy loose images; cross-source portrayal inheritance ([038](roadmap/sprints/expression-and-art-lineage.md)); blob garbage collection; dimension probing; and image generation. Image objects, placements and blobs are synced between devices ([SYNC](SYNC.md#art)).
