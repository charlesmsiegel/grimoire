# V0.2.1 image, gallery and expression migration mapping

V1's media model is **not** just `images/<filename>`. The migration must preserve two separate identities: **picture objects** and **placements of those pictures**.

## V1 layout

The 0.2.1 `image_store.py` contract stores immutable encoded image bytes at `<V1-home>/assets/image-store/blobs/<first-two-hash-chars>/<sha256>.<ext>` and picture-object sidecars at `.../objects/<id[4:6]>/px1-<64 hex>.json` (the shard is the two hex digits *after* `px1-`). `image_refs.py` stores one placement at `<record-assets-dir>/image-refs/<logical-name>.json` (`{"format": 1, "image": "px1-…", "focus": 0-100}`; a focus-only file is a crop override, not a picture). Expression sprites are placements under `<actor>/assets/<version>/expressions/revisions/<revision>/<outfit>/image-refs/<expression>.json`, made live by `expressions/manifest.json`, whose content digest must match or the whole layer is unavailable. Legacy loose files may coexist and act as fallback when a placement is unavailable.

An object (same picture/pixels) is not the same as its encoded blob (byte-for-byte content); and an object's identity is not the same as the use of that object as an avatar, gallery illustration or expression sprite.

### Required mapping records

| V1 datum | V2 import result |
|---|---|
| V1 image object ID, blob SHA-256, validated byte length and format | One **V2 image-object reference** and preserved/verified image bytes, with old→new identity mapping |
| V1 object sidecar, descriptions, source references and subjects | Attributed object metadata; unknown parts preserved separately for loss-aware audit |
| V1 per-record placement `image-refs/<name>.json` | A typed relation from a specific **source-qualified entity/version/campaign scope** to the V2 image object |
| Avatar, gallery, focus and logical image name | Distinct placement semantics; do not flatten to image object global attributes |
| Shared world image library, image collection member, greeting/scene image reference | Explicit scoped placement or collection membership, never a raw unchecked URL/path |
| Outfit/expressions per character or PC version | Typed appearance assignments, default outfit, neutral fallback, scale/alignment and expression labels |
| Campaign inherited image, local override or hidden inherited placement | Explicit inherited/overridden/hidden state; no same-named fallback after detach |
| Legacy loose image file without a valid object link | Verify exact bytes and create a new V2 object with a source-path and hash receipt; never discard it |

### Scan and apply algorithm

1. Walk the V1 source **without following symlinks outside the frozen source**, inspect manifests and associations, and list all references before copying any bytes.
2. Resolve an image object by its *declared object ID plus verified underlying blob content*, not only by a guessed filename or image cache URL. Prefer V1's safe resolution logic where available.
3. Build `old_picture_id -> new_picture_id` and `source_placement -> new_placement` maps separately. Many placements can resolve to one picture object. Multiple encodings with equal pixels should not produce accidental duplicate subjects.
4. Copy bytes into a V2 staging area; verify byte hashes and decode bounds. Import metadata and scoped placements only after associated objects exist. Preserve all source strings for an audit report.
5. Preserve outfit hierarchy and per-version overrides/hides rather than flattening visible sprites alone. If exact semantics are not yet supported, stage them as unresolved attachments with a future mapping, not discard them.
6. For each imported gallery, avatar and expression, verify the resulting **picture, subject, crop, version, scope and fallback** on a synthetic rendering test.
7. Do not retrieve images from remote URLs automatically. Keep external URLs as references with authorization and network-fetch status.

### Example, synthetic

Source picture `px1-A` has an avatar placement on character version `young`, and a neutral-expression placement on version `adult`. Both reference one V1 object and potentially one blob. The importer produces one V2 picture object with **two different placement links**. The adult version's outfit remains distinct, with its own crop, fallback and source provenance.

**Do not put binary image data in the graph itself.** Store blobs/content-addressed assets and connect them through typed placement relations.
