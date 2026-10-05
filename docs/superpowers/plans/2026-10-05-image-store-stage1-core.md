# Content-Addressed Image Store — Stage 1 (Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From now on, every new image write produces an immutable, content-addressed blob, an image-object sidecar, and a placement reference. Every existing reader keeps working against legacy files and references alike.

**Architecture:**
- Four new low-level modules, layered from the bottom up:
  - `image_sanitize`: lossless metadata strip.
  - `image_hash`: `px1` identity.
  - `image_store`: blobs, sidecars, ingest and the blob index.
  - `image_refs`: placement records and resolution.
- These sit **below** `assets`.
- `assets`' directory primitives (`put_in` / `path_in` / `list_in` / `names_in` / `delete_in`) are re-implemented on top of them, so every surface changes at once.
- The code that bypasses those primitives is converted one task at a time: tree copies, byte comparisons, collection checks, serving and bundles.

**Tech Stack:** Python 3.11+, FastAPI, Pillow ≥ 10 (12.x in dev; base dependency, also on Android/Chaquopy), pytest, TypeScript (types only).

**Spec:** `docs/superpowers/specs/2026-10-05-content-addressed-image-store-design.md`, stage 1 of §14. Read §1–§8, §10 (bundles and backups only) and §13.

## Global Constraints

- **Every write goes through `store.atomic`** (`write_bytes` / `write_text`). This is enforced by `test_atomic_guard.py`; do not add an `# atomic-ok:` marker.
- **No new raw `Path.home()` / `expanduser`.** The store root comes from `paths.home()`. Enforced by `test_paths_guard.py`.
- **Imports:**
  - Module scope only, and the graph stays acyclic (`test_import_guard.py`).
  - `image_sanitize`, `image_hash`, `image_store` and `image_refs` must NOT import `assets`, `image_descriptions`, `overlay` or any route.
  - Inside `store/`, a cross-package import binds a submodule (for example `from .worlds import paths as worlds_paths`).
- **Pydantic stays v1/v2-agnostic.** No pydantic is needed in the new modules.
- **Layout constants:**
  - The store root is `paths.home() / "assets" / "image-store"`.
  - Blobs live at `blobs/<sha[:2]>/<sha>.<ext>`.
  - Objects live at `objects/<hex[:2]>/px1-<hex>.json`, where `hex` is the 64 characters after `px1-`.
- **Placement layout:**
  - A placement file is `<image dir>/image-refs/<name>.json`.
  - The promote journal is `<image dir>/image-refs/.promote.json`.
- **Ids and hashes:**
  - Ids match `px1-[0-9a-f]{64}`.
  - Byte hashes match `[0-9a-f]{64}`.
  - Paths are built only from validated hex.
- **Placement JSON** is `json.dumps(obj, sort_keys=True) + "\n"`, which is byte-deterministic, so campaign slimming can `filecmp` refs. Its keys are `format` (always `1`), `image` (optional) and `focus` (optional, an int from 0 to 100).
- **Sidecar JSON:**
  - Required keys: `format: 1`, `id`, `identity` (`"pixels"` or `"bytes"`), and `blob: {sha256, ext, mime, size, width?, height?, animated}`.
  - `reason` is present only when `identity == "bytes"`.
  - `sources` is optional.
- **Domain separators and budgets:**
  - Separators: `b"grimoire-pixels-v1\0"`, `b"grimoire-anim-v1\0"`, `b"grimoire-pixels-v1-opaque\0"`.
  - `STATIC_BUDGET = 16_000_000`.
  - `ANIM_AREA_BUDGET = 64_000_000`.
  - `MAX_FRAMES = 1000`.
- **Missing `loop`** is encoded as `0xFFFFFFFF`.
- **`v` token:**
  - A blob-backed image's `v` is the full 64-hex blob SHA-256.
  - A legacy file's `v` stays `"<mtime_ns hex>-<size hex>"`.
- **Thumbnail key** for a blob-backed image is `sha256(f"cas|{blob_sha}|{width}|{encoder}").hexdigest()[:32]`.
- **Run backend tests from `backend/`** as `PYTHONPATH=src .venv/bin/python -m pytest <path> -q`. The full gate is `make check` from the repo root.
- **The ratcheted lint gates** (`check-lint`, `check-mypy`, `check-eslint`) compare against `lint-baselines/`. New code adds no findings. If a fix removes one, run `make baseline` and commit the smaller file with it.
- **Facade baseline:** `tests/test_store_api_baseline.py` snapshots `dir(grimoire.store)`, and a store submodule imported anywhere in a pytest session shows up in it. So each task that creates a store module also:
  - adds it to the alphabetical `from . import (...)` list in `backend/src/grimoire/store/__init__.py`;
  - adds its name to the sorted `"dir"` list (not `"all"`) in `backend/tests/store_api_baseline.json`;
  - does both in the same commit, as the deliberate facade change that test's docstring allows.
- **Pre-existing failures:** the container runs as root, so permission-based tests fail before any change. Their names are in `docs/superpowers/plans/2026-10-05-image-store-stage1-baseline.txt`. A failure in that list is not a regression; every other failure is.
- **`ext` normalization:** `ingest` normalizes a caller's `"jpeg"` to `"jpg"`, because `assets._EXTS` accepts both.
- **Privacy:** invented names only (Seraphine, Mara, Realm, Saltmarch). Image fixtures are synthetic, generated by test code with Pillow.
- **Existing tests:**
  - A test that hand-plants a legacy file (`avatar.png` written directly) stays as it is. It now exercises the transitional reads.
  - A test that asserts a store write produced a specific legacy file path is updated to assert through `assets.image_path` / `assets.resolve`. Never delete a test to get green.

## Review Focus

1. **A sync client delivers a ref before its object or blob.** The image must fall back to a legacy file when one exists, otherwise read as absent rather than crashing, and must not be cached as absent (Tasks 4, 5).
2. **The same picture is uploaded to two slots, then one slot is deleted.** The other must still serve, and nothing under `image-store/` may be deleted (Task 5).
3. **A hand-edited or garbled `image-refs/<name>.json`** (bad JSON, a non-`px1` id, a list instead of an object) is ignored like a missing ref and never 500s a listing. A focus that is out of range or not an int reads as `None`, and the ref is otherwise kept (Task 4).
4. **Unsniffable or undecodable bytes reach the store primitive.** These are fake test bytes, or an AVIF that `fetch.download_url` mislabelled. They are stored under the caller's extension with opaque identity, and read back byte-identical (Tasks 3, 5).
5. **A world is deleted while its images are still placed in a campaign fork.** The campaign's refs keep resolving, because blobs and objects are global and deleting a world removes only world-tree refs (Task 14 roster test).

---

### Task 1: Lossless metadata sanitizer

**Files:**
- Create: `backend/src/grimoire/store/image_sanitize.py`
- Test: `backend/tests/test_image_sanitize.py`

**Interfaces:**
- Produces: `image_sanitize.sanitize(data: bytes) -> bytes`. It returns the sanitized container. It returns `data` unchanged (the same object is fine) when the format is not PNG/JPEG/WebP/GIF or the container does not parse structurally. It never raises.

**Steps:**

- [ ] **Step 1: Write the failing tests.** Generate inputs with Pillow in the test module.
  - `test_png_text_chunks_dropped`: save a PNG with `PngInfo` tEXt `chara`, `ccv3`, `parameters` and an iTXt. Assert:
    - `b"tEXt" not in out and b"iTXt" not in out`;
    - `Image.open(io.BytesIO(out)).tobytes() == Image.open(io.BytesIO(src)).tobytes()`;
    - running it twice is idempotent: `sanitize(out) == out`.
  - `test_png_colour_and_animation_chunks_kept`: a PNG saved with `icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()`, and an APNG (`save_all=True`, 2 frames). Assert `b"iCCP"`, `b"acTL"` and `b"fcTL"` survive.
  - `test_png_exif_before_idat_keeps_only_orientation`: a PNG saved with EXIF holding Orientation=6 and GPS. The out-EXIF parses to `{0x0112: 6}` only, and its `IDAT` is unchanged.
  - `test_png_exif_after_idat_dropped`: hand-insert an `eXIf` chunk after IDAT. Assert it is gone.
  - `test_jpeg_strips_xmp_iptc_com_keeps_icc_and_orientation`: a JPEG with EXIF (Orientation=3, Make, GPS), an XMP APP1, a COM segment, and an ICC APP2. Assert:
    - the output EXIF holds only Orientation 3;
    - there is no XMP or COM;
    - ICC is intact;
    - pixels decode identically.
  - `test_jpeg_orientation_1_drops_exif_entirely`: an EXIF block with Orientation=1 gives no APP1 in the output.
  - `test_webp_drops_exif_xmp_and_clears_vp8x_flags`: a WebP saved with `exif=` and `xmp=`. Assert there is no `EXIF` or `XMP ` chunk, the VP8X flags byte has bits 0x08 and 0x04 cleared, and the pixels are equal.
  - `test_gif_drops_comments_keeps_loop`: a 2-frame GIF with `comment=b"x"` and `loop=0`. Assert there is no `\x21\xfe` comment extension and `NETSCAPE2.0` is present.
  - `test_garbage_and_truncated_returned_as_received`: `b"a"`, `b"\x89PNG"` and a PNG truncated mid-IDAT all return the input unchanged.

- [ ] **Step 2: Run them and see them fail.**
  `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_image_sanitize.py -q` fails with ModuleNotFoundError.

- [ ] **Step 3: Implement `sanitize`.** Write pure byte-level parsers per format, dispatching on `fetch.sniff_ext`. There is no Pillow re-encode anywhere. Pillow may be used only to parse and build the minimal EXIF (`Image.Exif()`, `exif[0x0112] = n`, `exif.tobytes()`).
  - **PNG:**
    - Keep: `IHDR PLTE tRNS IDAT IEND iCCP gAMA cHRM sRGB cICP acTL fcTL fdAT sBIT pHYs`.
    - Drop: `tEXt zTXt iTXt tIME eXIf-after-IDAT`, and every other ancillary chunk not in that list.
    - Rewrite `eXIf` before IDAT to a minimal Orientation-only EXIF, or drop it when the orientation is 1, absent or unreadable. The PNG `eXIf` payload carries no `Exif\0\0` prefix, so strip it from `Image.Exif().tobytes()`; a JPEG APP1 keeps it.
    - Validate each chunk's CRC. If any CRC mismatches, return the input as received.
  - **JPEG:**
    - Walk the segments up to SOS, then copy the rest verbatim.
    - Keep APP0 JFIF, APP2 `ICC_PROFILE`, APP14 Adobe, and all non-APP/COM segments.
    - Replace APP1 `Exif\0\0` with an Orientation-only EXIF (or drop it).
    - Drop the other APPn segments and COM.
  - **WebP:** rewrite the RIFF chunk list without `EXIF` and `XMP `. Clear VP8X flag bits 0x08 (EXIF) and 0x04 (XMP), and fix the RIFF size.
  - **GIF:** walk the blocks. Drop comment extensions (`0x21 0xFE`) and application extensions (`0x21 0xFF`) other than `NETSCAPE2.0` / `ANIMEXTS1.0`.

- [ ] **Step 4: Run them and see them pass.** Same command, PASS.

- [ ] **Step 5: Commit.** `git add backend/src/grimoire/store/image_sanitize.py backend/tests/test_image_sanitize.py && git commit -m "feat(images): lossless metadata sanitizer"`

---

### Task 2: Pixel identity (`px1`)

**Files:**
- Create: `backend/src/grimoire/store/image_hash.py`
- Create: `backend/tests/fixtures/images/make_fixtures.py`, a generator with `--write` that writes the fixture files once.
- Create: `backend/tests/fixtures/images/*.png|jpg|gif|webp`, the **committed** fixture bytes. Tests read these files and never re-encode, so a Pillow encoder bump cannot move the pinned ids.
- Create: `backend/tests/fixtures/images/pixel_ids.json`
- Modify: `backend/src/grimoire/store/thumbs.py`. Move `_UPRIGHT`, `_ORIENTS` and `_orientation` out of it, and have `thumbs` call `image_hash.orientation`.
- Test: `backend/tests/test_image_hash.py`

**Interfaces:**
- Produces:
  - `ID_RE: re.Pattern`, matching `px1-[0-9a-f]{64}\Z`.
  - `is_image_id(s: object) -> bool`.
  - `ORIENTS: frozenset[str]`.
  - `orientation(im: Image.Image) -> Image.Transpose | None`, with exactly `thumbs._orientation`'s behaviour.
  - `@dataclass(frozen=True) class PixelIdentity`, with fields:
    - `id: str`;
    - `identity: Literal["pixels", "bytes"]`;
    - `reason: str | None`;
    - `width: int | None`;
    - `height: int | None`;
    - `animated: bool`.
  - `pixel_identity(data: bytes, byte_sha256: str) -> PixelIdentity`, which never raises.
  - `opaque_id(byte_sha256: str) -> str`.
  - The constants `STATIC_BUDGET`, `ANIM_AREA_BUDGET` and `MAX_FRAMES`.

**Steps:**

- [ ] **Step 1: Write the failing tests** (`test_image_hash.py`).
  - `test_orientation_moved_not_changed`: `thumbs` still passes all of `tests/test_thumbs.py`. Run that file in Step 4.
  - `test_same_pixels_different_png_text_same_id`.
  - `test_jpeg_exif_rotated_equals_physically_rotated`: JPEG A is saved with Orientation=6. B is `A_pixels.transpose(ROTATE_270)` saved as PNG (lossless reference). Compare the ids from JPEG A decoded and from the B raster re-saved as PNG with A's decoded-and-transposed pixels. Build both from the same decoded raster so JPEG loss cannot interfere.
  - `test_webp_exif_orientation_ignored`: the same WebP pixels with and without EXIF Orientation=6 give the same id. A physically rotated WebP gives a different id.
  - `test_xmp_orientation_ignored`: a PNG with XMP `tiff:Orientation="6"` has the same id as one without it.
  - `test_invisible_rgb_normalized`: two RGBA PNGs differ only in RGB where alpha is 0. Same id.
  - `test_one_pixel_change_changes_id`.
  - `test_icc_profile_changes_id`: the same samples, one with an sRGB ICC profile and one without. Different ids.
  - `test_animated_frame_change_and_timing_change`: 2-frame GIFs. Changing one frame's pixels changes the id, and changing a duration changes the id. A single-frame GIF has a static-domain id, not an animated one.
  - `test_opaque_cases`, each giving `identity == "bytes"`, `id == opaque_id(sha)`, and a non-empty `reason`:
    - a 16-bit RGB PNG (Pillow `Image.fromarray`-free: write the IHDR with bit depth 16 by saving mode `"I;16"` grayscale and patching, or build the raw PNG bytes with `zlib` in the test);
    - CMYK JPEG;
    - an oversized static image: monkeypatch `image_hash.STATIC_BUDGET = 10` with a 4×4 image;
    - undecodable PNG-magic bytes;
    - `b"a"`.
  - `test_fixture_ids_pinned`: for every committed fixture file, `pixel_identity(data, sha256(data)).id == pixel_ids.json[name]`.
  - `test_sanitize_preserves_identity` (spec §1.1): for every committed fixture, `pixel_identity(sanitize(data), ...).id` equals the unsanitized id.
  - `test_png_and_jpeg_same_pixels_share_id`: a PNG and a lossless-equivalent source with no colour chunks, for example a PNG re-saved from the decoded raster of a JPEG, compared with that decoded JPEG. Same id.
  - `test_cicp_changes_id`: hand-insert a `cICP` chunk before IDAT. Different id.
  - `test_never_raises`: run random byte strings through it. The result has `identity == "bytes"`.

- [ ] **Step 2: Run and see FAIL.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_image_hash.py -q`

- [ ] **Step 3: Implement.** Follow spec §1.3 exactly.
  - **Static images:**
    - Decode with `Image.open`. Use `getattr(im, "is_animated", False) and getattr(im, "n_frames", 1) > 1` to choose the domain, but only for formats in `{"GIF", "PNG", "WEBP"}`.
    - Detect 16-bit PNG from IHDR byte 24 (`data[24] == 16`).
    - Treat modes `CMYK` (YCCK JPEGs also open as `CMYK`), `I;16*`, `I` and `F` as opaque.
    - Apply the budget to `w*h`.
    - Transpose by `orientation(im)` and `convert("RGBA")`.
    - Normalize alpha: build `mask = alpha.point(lambda a: 255 if a else 0)` and `out = Image.new("RGBA", size, (0, 0, 0, 0))`, then `out.paste(rgba, mask=mask)`.
    - Hash `b"grimoire-pixels-v1\0" + w.to_bytes(4, "big") + h.to_bytes(4, "big") + len(color).to_bytes(4, "big") + color + out.tobytes()`.
    - `color` for **PNG** is read from the raw bytes, not from `im.info` (Pillow 12 drops cICP). It is the concatenation, in file order, of `type + payload` for every `iCCP`, `gAMA`, `cHRM`, `sRGB` and `cICP` chunk before the first IDAT. With none present it is `b""`.
    - `color` for **JPEG/WebP/GIF** is `im.info.get("icc_profile") or b""`.
    - With no colour data, `color` is exactly `b""` in every format. That is what lets identical pixels in PNG and JPEG share an id.
  - **Animated images:**
    - `seek(i)` each frame, `convert("RGBA")`, normalize alpha, and update the hash per frame (never all frames in memory).
    - Each frame contributes `duration = int(im.info.get("duration", 0))`.
    - `loop = im.info.get("loop")`, using `0xFFFFFFFF` when it is missing.
  - **Animated-WebP support check:**
    - On Pillow 12, `features.check_feature("webp_anim")` raises `ValueError`.
    - On 11.x it warns and returns.
    - Use `try: f = check_feature("webp_anim")` (inside `warnings.catch_warnings()` with the warning suppressed) `except ValueError: f = None`, then `animates = features.check("webp") if f is None else bool(f)`. Without support, an animated WebP is opaque with `reason="webp-animation-unsupported"`. An animated WebP is detected from the RIFF `ANIM` chunk in the bytes, not from Pillow.
  - **Errors:** wrap everything in `try`. Any exception gives opaque identity with `reason="undecodable"`.
  - **Fixtures:** `make_fixtures.build()` generates the fixture set. Run it once to write `pixel_ids.json` (a `python -m tests.fixtures.images.make_fixtures --write` entry point), and commit the JSON.

- [ ] **Step 4: Run and see PASS.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_image_hash.py tests/test_thumbs.py -q`

- [ ] **Step 5: Commit**, including the facade addition for `image_hash`. `git commit -m "feat(images): px1 pixel identity with pinned fixtures"`

---

### Task 3: Blob and object store, ingest, blob index

**Files:**
- Create: `backend/src/grimoire/store/image_store.py`
- Modify: `backend/src/grimoire/store/locks.py`. Add `image_object_lock(image_id: str) -> _ProcessScopedLock`, with 64 stripes keyed by `int(image_id[4:6], 16) % 64` and domain `"image-objects"`, and `image_ingest_gc_lock() -> _ProcessScopedLock`, domain `"image-store"`, name `"ingest-gc"`. Document both as leaf locks: never acquire a campaign lock while holding one.
- Test: `backend/tests/test_image_store.py`

**Interfaces:**
- Consumes:
  - `image_sanitize.sanitize`;
  - `image_hash.pixel_identity`, `image_hash.is_image_id`;
  - `fetch.sniff_ext`;
  - `paths.home`;
  - `atomic.write_bytes` / `write_text`;
  - `statcache`;
  - the `locks` additions above.
- Produces:
  - `MIME: dict[str, str]`, mapping `png/jpg/gif/webp` to their media types.
  - `store_root() -> Path`.
  - `blob_path(sha: str, ext: str) -> Path`. Raises `ValueError` on a bad sha or ext.
  - `object_path(image_id: str) -> Path`. Raises `ValueError` on a bad id.
  - `@dataclass(frozen=True) class ImageObject`, with fields:
    - `id: str`;
    - `identity: str`;
    - `blob_sha256: str`;
    - `ext: str`;
    - `mime: str`;
    - `size: int`;
    - `width: int | None`;
    - `height: int | None`;
    - `animated: bool`;
    - `raw: dict`.
  - `read(image_id: str) -> ImageObject | None`. Tolerant (garbled → None) and memoized via `statcache`.
  - `ingest(data: bytes, ext: str, *, source_url: str | None = None, sanitize: bool = True) -> ImageObject`.
    - `ext` is used only when the bytes do not sniff.
    - `sanitize=False` is for bundle import, where the bytes are already stored bytes.
  - `blob_sha_of(path: Path) -> str | None`. It returns the sha when `path` is `store_root()/blobs/<xx>/<sha>.<ext>`, using string checks and no stat.
  - `update(image_id: str, change: Callable[[dict], dict | None]) -> None`. A read-modify-write of the sidecar under `image_object_lock`; returning `None` means no write. This is the stage-2 hook. Stage 1 uses it for `sources`.
  - `project(raw: dict, scope: str) -> dict`. The bundle projection: drop `sources` and `description_conflicts`, and keep only the associations and `reviews.subjects` entries equal to `scope`.
  - `rebuild_index() -> int`, returning the number of entries.

**Steps:**

- [ ] **Step 1: Write the failing tests** (`monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`). PNG bytes are made with Pillow.
  - `test_first_ingest_writes_blob_and_object`:
    - the blob file exists at `blob_path(obj.blob_sha256, "png")` and its sha256 equals the name;
    - `read(obj.id) == obj`;
    - the sidecar has the format, id, identity and blob keys exactly.
  - `test_exact_duplicate_creates_nothing`: a second `ingest` of the same bytes returns an equal object. The count of files under `store_root()` is unchanged.
  - `test_pixel_duplicate_keeps_first_blob`: the same pixels with a different PNG `pHYs` value (bytes differ after sanitize). Same `id`, same `blob_sha256` as the first, and one blob on disk.
  - `test_different_pixels_new_object`.
  - `test_sanitized_bytes_are_stored`: a PNG with a `chara` tEXt. The blob bytes contain no `b"chara"`.
  - `test_unsniffable_bytes_take_callers_ext_and_opaque_identity`: `ingest(b"a", "png")` gives `obj.ext == "png"`, `obj.identity == "bytes"`, and the blob bytes are `b"a"`.
  - `test_sources_merge_deduplicated`: ingest twice with `source_url="https://example.invalid/a#frag"`, then once with `.../b`. `raw["sources"] == [{"url": "https://example.invalid/a"}, {"url": "https://example.invalid/b"}]`.
  - `test_index_hit_validated`: delete the sidecar after the first ingest. Re-ingesting the same bytes recreates a sidecar; there is no stale hit.
  - `test_missing_retained_blob_adopted`: delete the blob file. Re-ingesting pixel-identical but byte-different bytes gives `read(id).blob_sha256 == the new bytes' sha`, and that blob exists.
  - `test_corrupt_blob_repaired`: overwrite the blob with junk. Re-ingesting the same bytes restores the correct content.
  - `test_index_rebuilt_after_deletion`: `shutil.rmtree(.cache/image-store/blob-index)`, then `rebuild_index() == 1`, and a re-ingest does not decode (monkeypatch `image_hash.pixel_identity` to raise). `image_store` must call `image_hash.pixel_identity` through the module attribute, never a name bound at import, or this test passes vacuously.
  - `test_jpeg_ext_normalized`: `ingest(b"a", "jpeg").ext == "jpg"`.
  - `test_ingest_touches_sidecar_mtime`: set the sidecar mtime to 0, re-ingest, and the mtime is now recent.
  - `test_blob_sha_of`: a blob path gives its sha. Any other path, including one in a lookalike directory outside the store root, gives None.
  - `test_project_filters_scope`.
  - `test_bad_ids_rejected`: `object_path("px1-zz")` and `blob_path("../x", "png")` raise ValueError. `read("nope")` is None.

- [ ] **Step 2: Run and see FAIL.**

- [ ] **Step 3: Implement.**
  - **Blob index:** one tiny file per blob, `paths.home()/.cache/image-store/blob-index/<sha[:2]>/<sha>`, whose content is the image id. Concurrent ingests never rewrite one shared file.
    - A hit is valid only if `read(id)` exists, its `blob_sha256 == sha`, and the blob file exists. A missing blob is rewritten from the incoming bytes, since the bytes are the same.
    - If the `blob-index` directory is absent entirely, call `rebuild_index()` once first, scanning `objects/**/*.json`.
  - **Ingest order:**
    1. Sniff to get `ext` (the caller's `ext` when the bytes do not sniff).
    2. Sanitize, but only for a sniffed format.
    3. Compute `sha`.
    4. Look up the index.
    5. On a miss, compute `pixel_identity`.
    6. Under `image_ingest_gc_lock()` then `image_object_lock(id)`, resolve the four cases of spec §5 step 5. Publish the blob before the sidecar.
    7. Touch the sidecar with `os.utime`.
    8. Write the index entry.
  - **Blob publish:** if the path exists and `sha256(read) == sha`, skip. Otherwise `atomic.write_bytes`, logging a repair through `logs.record` if the file existed.
  - **Sidecar:** `json.dumps(raw, indent=2, sort_keys=True) + "\n"`.
  - **`source_url` normalization:** http(s) only, fragment stripped, deduplicated, at most 20 sources (oldest kept).

- [ ] **Step 4: Run and see PASS.** Also run `tests/test_atomic_guard.py tests/test_paths_guard.py tests/test_import_guard.py tests/test_lock_domain_guard.py`.

- [ ] **Step 5: Commit**, including the facade addition for `image_store`. `git commit -m "feat(images): content-addressed blob/object store with validated blob index"`

---

### Task 4: Placement records and resolution

**Files:**
- Create: `backend/src/grimoire/store/image_refs.py`
- Test: `backend/tests/test_image_refs.py`

**Interfaces:**
- Consumes: `image_store.read`, `image_store.blob_path`, `image_hash.is_image_id`, `atomic`, `assets`-independent name validation. Copy `assets._addressable_name`'s rule into a private `_valid_name` here, because `image_refs` must not import `assets`.
- Produces:
  - Constants: `REFS_DIR = "image-refs"`, `JOURNAL = ".promote.json"`.
  - `@dataclass(frozen=True) class Ref`, with fields `name: str`, `image: str | None`, `focus: int | None`.
  - `@dataclass(frozen=True) class ResolvedImage`, with fields:
    - `name: str`;
    - `image_id: str`;
    - `blob_sha256: str`;
    - `blob_path: Path`;
    - `ext: str`;
    - `mime: str`;
    - `width: int | None`;
    - `height: int | None`;
    - `focus: int | None`.
  - Path and file helpers:
    - `ref_path(d: Path, name: str) -> Path`;
    - `read(d: Path, name: str) -> Ref | None`, which is tolerant; a malformed file, id or focus is treated as absent;
    - `write(d: Path, name: str, image: str | None, *, focus: int | None = None) -> None`, where both `None` deletes the file;
    - `delete(d: Path, name: str) -> bool`.
  - Directory scans:
    - `scan(d: Path) -> dict[str, Ref]`, every valid ref in `d/image-refs/`, read with one `os.scandir` (the journal is excluded);
    - `image_names(d: Path) -> set[str]`, refs with `image`.
  - Resolution:
    - `resolve_ref(ref: Ref) -> ResolvedImage | None`, which needs `image` set, the object present and the blob file present;
    - `resolve(d: Path, name: str) -> ResolvedImage | None`;
    - `is_transient(path: Path) -> bool`, true for the journal file name.
  - The journal:
    - `read_journal(d: Path) -> dict | None`;
    - `write_journal(d: Path, journal: dict) -> None`;
    - `clear_journal(d: Path) -> None`.

**Steps:**

- [ ] **Step 1: Write the failing tests.**
  - Round trip: write, read, scan.
  - The file content is exactly `json.dumps({"format": 1, "image": id}, sort_keys=True) + "\n"`.
  - Focus is clamped and validated: a bool or out-of-range focus reads as `None`.
  - Garbled refs read as None and are absent from `scan`: bad JSON, a list, `{"image": "px1-short"}`, `{"format": 2}`.
  - An image-less ref with focus is present in `scan` but not in `image_names`.
  - `resolve` returns None when the object or the blob is missing.
  - `write(d, n, None)` deletes.
  - The journal round-trips, and the journal is never in `scan`.
  - Unsafe names (`"../x"`, `"a.b"`, `"*"`) raise `ValueError` on write and return None on read.

- [ ] **Step 2: Run and see FAIL.** Then **Step 3: implement**, with both halves atomic through `atomic.write_text`.

- [ ] **Step 4: Run and see PASS** (plus the import and atomic guards).

- [ ] **Step 5: Commit**, including the facade addition for `image_refs`. `git commit -m "feat(images): placement records and resolution"`

---

### Task 5: `assets` primitives on placements

**Files:**
- Modify: `backend/src/grimoire/store/assets.py`. This covers the primitives listed under Interfaces, plus `image_path`, `list_images`, `_free_gallery`, `_heal_stranded_promotion` (slot choice), `version_art`, `_restamp_avatar`, `image_version`, `read_focus` / `write_focus` / `clear_focus`, `put_image` and `delete_image`.
- Test: extend `backend/tests/test_assets_store.py`, and fix any assertions on legacy paths elsewhere that the full run flags.

**Interfaces:**
- Consumes: `image_store.ingest`, `image_store.blob_sha_of`, and Task 4's `image_refs.*`.
- Produces (same names; behaviour changed as listed):
  - `put_in(d, name, data, ext, *, supported_only=False) -> str` returns the blob's ext.
  - `path_in(d, name, *, supported_only=False) -> Path | None`.
  - `list_in(d) -> list[dict]`. Each row has `{name, ext, v}`, plus `image_id` when the name is ref-backed.
  - `names_in(d, also="")`.
  - `delete_in(d, name, *, supported_only=False)`.
  - `image_version(p: Path) -> str`, which returns `image_store.blob_sha_of(p)` when that is not None.
- New:
  - `resolve(d: Path, name: str) -> image_refs.ResolvedImage | None`;
  - `link_in(d: Path, name: str, image_id: str, *, keep_focus: bool = False) -> None`, which writes the ref and removes the name's legacy siblings, under the name lock;
  - `adopt_legacy(d: Path, name: str) -> str | None`. If `name` is a legacy file with no ref, it ingests the bytes, writes the ref, removes the legacy file, and returns the id. If the name is already ref-backed, it returns the ref's id;
  - `image_id(root, cid, vid, name, base="characters") -> str | None`.

**Behaviour rules:**
- **`path_in`:** a resolvable image ref returns `resolved.blob_path`. Otherwise it applies today's legacy rule (an unresolvable ref falls through to legacy).
- **`list_in` / `_candidates` / `_listing`:**
  - Ref names that resolve produce `{name, ext: blob ext, v: blob sha, image_id}`.
  - Legacy stems not shadowed by a resolvable ref produce today's rows.
  - The result is sorted by name.
  - Restructure `_candidates` to return both the file map and the ref map. Keep `_listing`'s newest-wins rule for legacy files.
- **`names_in`:** the union of legacy stems and `image_refs.image_names(d)`. The `also` semantics are unchanged.
- **`put_in`:**
  1. `obj = image_store.ingest(data, ext)`.
  2. Under `_image_lock(d, name)`, snapshot the legacy siblings' identities (today's code).
  3. Read the old ref.
  4. `image_refs.write(d, name, obj.id, focus=old.focus if old and old.image == obj.id else None)`.
  5. Unlink the snapshotted siblings.
  6. Return `obj.ext`.

  The `ext` argument is still validated by `_norm_ext` (a `ValueError` otherwise), which preserves today's error contract.
- **`delete_in`:** under the lock, `image_refs.delete`, plus today's sibling unlinks.
- **`_free_gallery(d)` and the heal slot choice:** occupancy is `names_in(d)[0]`, case-folded. "Avatar present" means `path_in(d, AVATAR) is not None`.
- **`version_art`:**
  - Stamps include `d / "image-refs"` when it exists, and each `image-refs/avatar.json`.
  - `cacheable` is False when any image ref in `d` fails to resolve.
  - `avatar_v` for a ref-backed avatar is the blob sha.
- **Focus:**
  - **`read_focus`:** if `image-refs/avatar.json` exists (with or without `image`), return its `focus`. Otherwise read `focus.json` as today.
  - **`write_focus`:**
    - if an avatar ref exists, rewrite it with the new focus, keeping `image`;
    - else if a legacy avatar file exists, write `focus.json` (unchanged);
    - else write an image-less ref `{format, focus}`, which is the occurrence override used by `put_campaign_avatar_focus` on an inherited avatar.
  - **`clear_focus`:** drop `focus` from the avatar ref (deleting an image-less ref entirely) and unlink `focus.json`.
- **`put_image`:** unchanged API, plus a keyword `source_url: str | None = None`, passed through `put_in(..., source_url=)` to `ingest`. Its `clear_focus` on `AVATAR` remains, and is a no-op on the new ref because `put_in` already dropped focus for a new id.
- **Every reader of the focus override moves here, not in Task 7** (plan review B5):
  - `overlay.read_focus` (≈1639) treats the campaign as authoritative when the campaign has `image-refs/avatar.json`, in addition to today's conditions;
  - `characters.version_facts`' `focus_file` fact (≈719) also stamps and reports `image-refs/avatar.json`;
  - the character-list merge at `overlay.py` ≈1249 honours a campaign override ref exactly as it honours a campaign `focus.json` today.

  The detail and the list must agree.
- **`names_in` ruling (plan review M7):** it counts image-bearing refs without resolving them, so sweeps stay cheap. Mid-sync, a ref whose object or blob has not arrived is counted by `names_in` but omitted by `list_in`. Accept that transient disagreement and say so in `names_in`'s docstring.

**Steps:**

- [ ] **Step 1: Write the failing tests** in `test_assets_store.py`.
  - `test_put_writes_ref_not_file`: after `put_image(tmp, "sera", "default", "avatar", png, "png")`:
    - no `*.png` exists under `characters/sera/assets/default`;
    - `image-refs/avatar.json` exists;
    - `image_path(...)` is under `image_store.store_root()`.
  - `test_same_bytes_two_slots_one_blob`: avatar and gallery_1 hold the same bytes. Exactly one blob, and `list_images` rows share an `image_id`.
  - `test_delete_one_placement_keeps_other_and_store`.
  - `test_ref_wins_over_legacy_and_unresolved_ref_falls_back`: plant `avatar.png` by hand, then write a ref to a real object. The ref wins. Delete the blob file and the legacy file is served again. Delete the legacy file and `image_path` is None and `list_images` omits avatar.
  - `test_list_row_shapes`: a ref row has keys `{name, ext, v, image_id}`, where `v` is the 64-hex blob sha. A legacy row is `{name, ext, v}`, where `v` is the mtime-size form.
  - `test_names_in_and_free_gallery_see_refs`: with only refs `gallery_1` and `gallery_2`, `_free_gallery` gives `gallery_3`.
  - `test_version_art_uncacheable_while_ref_unresolved`: the stamps are None. Restore the blob and the stamps come back.
  - `test_focus_lives_on_ref`:
    - `write_focus` on a ref-backed avatar stores focus in the ref and creates no `focus.json`;
    - `put_image` of different bytes clears it;
    - re-putting the same bytes keeps it.
  - `test_focus_override_without_avatar`: `write_focus` on an empty version dir creates an image-less ref, `read_focus` returns it, and `list_images` is empty.
  - `test_unsniffable_bytes_round_trip`: `put_image(..., b"\x89PNG", "png")`. `image_path(...).read_bytes() == b"\x89PNG"`.
  - `test_heal_slot_choice_with_ref_avatar`: a ref-backed avatar and `gallery_1`, plus a hand-planted `promote-tmp.png`. The heal renames the stray to `gallery_2.png`, which is visible in `list_images`, and the avatar is untouched.
  - `test_campaign_focus_override_list_and_detail_agree`: a campaign override ref on an inherited avatar. `characters.version_facts` / the campaign character list report the same `avatar_focus` as the detail view.
  - Update the existing tests in this file that assert legacy file names after a write, so they assert through `image_path`.

- [ ] **Step 2: Run and see FAIL.**

- [ ] **Step 3: Implement** to the rules above.

- [ ] **Step 4: Run** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_assets_store.py -q` and see PASS.

- [ ] **Step 5: Run the whole backend suite**, `PYTHONPATH=src .venv/bin/python -m pytest -q`, and compare it with the pre-existing failures in `docs/superpowers/plans/2026-10-05-image-store-stage1-baseline.txt`. Those come from running as root and are not regressions. Every **new** failure is fixed in this task:
  - Where a test asserted a legacy path after a store write, change it to assert through `image_path` / `list_images`.
  - Where the failure comes from a production reader that globs or byte-compares asset files (overlay, slimming, collections, serving), make the minimal correct fix now. Tasks 7–10 then add that reader's dedicated behaviour and tests.
  - Never skip, xfail or delete a test.
  - Known to break at this task:
    - `test_overlay.py` ≈1980: `shadowed_images` builds `held` from file stems, which are now blob hashes. Make the minimal fix, taking names from the listing.
    - `test_world_list_fast.py` ≈409: it rewrites `focus.json`, but the focus now lives on the ref. Update the test to set the focus through `assets.write_focus`.
    - `test_routes.py` ≈1410: the campaign focus on an inherited avatar, which the `read_focus` change above fixes.

- [ ] **Step 6: Commit.** `git commit -m "feat(images): assets primitives write placements; transitional legacy reads"`

---

### Task 6: Promotion as a journalled ref swap; copy-from-greeting as a link

**Files:**
- Modify: `backend/src/grimoire/store/assets.py` (`promote_image`, plus journal recovery inside `image_path` / `list_images`, next to `_heal_stranded_promotion`).
- Modify: `backend/src/grimoire/store/image_subjects.py` (`copy_to_character`).
- Test: `backend/tests/test_assets_store.py`, `backend/tests/test_image_subjects_store.py`.

**Interfaces:**
- Consumes: Task 5's `adopt_legacy`, `link_in`, `resolve`; Task 4's journal helpers.
- Produces:
  - `promote_image` with an unchanged signature and unchanged user-visible behaviour.
  - `assets._recover_promotion(d: Path) -> None`.

**Promote algorithm** (spec §8):

1. Under `_image_locks_held(d, name, AVATAR)` and `sidecar_lock(d, DESCRIPTIONS_FILE)`, run `adopt_legacy(d, name)` (raise `FileNotFoundError` when None) and `adopt_legacy(d, AVATAR)`. This makes both slots refs.
2. Set `pre = {AVATAR: avatar_id_or_None, name: name_id}` and `post = {AVATAR: name_id, name: avatar_id_or_None}`.
3. Write the journal `{"name": name, "pre": pre, "post": post, "desc": {AVATAR: <pre-swap description of AVATAR or None>, name: <pre-swap description of name or None>}}`. The `desc` values are read from `descriptions.json` before anything moves.
4. `image_refs.write(d, AVATAR, name_id)` with no focus.
5. If `post[name]` is set, write `image_refs.write(d, name, post[name])`. Otherwise `image_refs.delete(d, name)`, and if `path_in(d, name)` is not None, raise `OSError` (today's confirmation).
6. Write the description post-state **from the journal**, never by re-reading and swapping: `edit_sidecar(d, DESCRIPTIONS_FILE, {AVATAR: desc[name], name: desc[AVATAR] if post[name] else None})`. This is idempotent, so recovery can repeat it safely (plan review S2).
7. `clear_journal`, then `clear_focus`.

**Recovery** (`_recover_promotion`) runs from `image_path` (any name) and `list_images` when the journal exists. Under the two name locks:
- if every slot's current image id is in `{pre[slot], post[slot]}`, roll forward by repeating steps 4–7, where step 6 is idempotent;
- otherwise delete the journal and do nothing else.

**`copy_to_character`:**
- Resolve the source. Use `assets.resolve(src_dir, name)` when the source dir holds a ref. Otherwise ingest the legacy file's bytes once, via `image_store.ingest(src.read_bytes(), src.suffix[1:])`.
- Then `assets.link_in(dst_dir, slot_name, image_id)`. For `slot == "avatar"`, also `clear_focus`.
- No `put_image`, and no bytes are written.
- The return values and the `taken_names` logic are unchanged.

**Steps:**

- [ ] **Step 1: Write the failing tests.**
  - `test_promote_swaps_refs_without_new_blobs`: the blob count is unchanged after the promote, the image ids are swapped, the descriptions are swapped, and focus is cleared.
  - `test_promote_without_avatar_deletes_source_slot`.
  - `test_promote_legacy_slots_adopted`: hand-planted legacy `avatar.png` and `gallery_1.png`. After the promote there are no legacy files and both slots are refs, swapped.
  - `test_promote_crash_after_journal_rolls_forward`: monkeypatch `image_refs.write` to raise on its first call after the journal is written. Then `list_images` completes the swap.
  - `test_promote_crash_after_avatar_write_rolls_forward`.
  - `test_crash_after_description_write_does_not_double_swap`: inject a failure in `clear_journal`, then recover. Each picture keeps its own description.
  - `test_stale_journal_discarded`: write a journal whose `pre`/`post` match neither current slot (simulating later edits). `list_images` leaves the slots as they are and removes the journal.
  - `test_copy_from_greeting_writes_no_blob`: in `test_image_subjects_store.py`, the blob count is the same before and after, and the destination's `image_id` equals the source's.

- [ ] **Step 2: Run and see FAIL.**

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run and see PASS.**

- [ ] **Step 5: Commit.** `git commit -m "feat(images): journalled ref-swap promotion; copy-from-greeting links"`

---

### Task 7: Per-slot tree copies, tombstones by name, campaign focus override

**Files:**
- Modify: `backend/src/grimoire/store/assets.py`. Add `copy_slots`.
- Modify: `backend/src/grimoire/store/overlay.py`. Change `copy_record_dir_down`, `_tombstoned_asset` and `promote_image` (≈1781; copy the world image up by **link**, not bytes). The `read_focus` change already landed in Task 5.
- Modify: `backend/src/grimoire/store/sync.py`. In `_copy_tree`, route `assets/<vid>/` directories through `copy_slots`.
- Test: `backend/tests/test_overlay.py`, `backend/tests/test_sync*.py` (whichever covers promote and demote; find with `grep -l "copy_record_dir_down\|_copy_tree" tests`), `backend/tests/test_routes.py` or the campaign focus route test.

**Interfaces:**
- Produces: `assets.copy_slots(src: Path, dst: Path, *, skip: Callable[[str], bool] = lambda n: False, overwrite: bool = False) -> None`, defined as follows:
  - **Slots:** every logical name in `src`, meaning `names_in(src)[0]` plus image-less override refs.
  - **Which slots are copied:**
    - With `overwrite=False`, a slot is copied only when `dst` has neither an **image-bearing** ref nor a legacy file for it. An image-less override ref in `dst` does not block the copy: write the source's image into it and **keep the destination's focus** (plan review S1).
    - With `overwrite=True` (sync promote into a fresh world record), it always replaces.
    - A slot for which `skip(name)` is true is never copied.
  - **How a slot is copied:**
    - a ref is copied as a ref, via `image_refs.write` with the same image and focus;
    - a legacy file is copied as a file via `atomic.write_bytes`, named as in the source;
    - the journal is never copied.
  - **Not copied:** non-image files in `src`, such as `descriptions.json`, `focus.json` and `subjects.json`. Callers keep copying those as they do today.

**Rules:**
- **`_tombstoned_asset`** matches both `assets/<vid>/<name>.<ext>` (3 parts) and `assets/<vid>/image-refs/<name>.json` (4 parts), keyed by `(vid, name)`.
- **`copy_record_dir_down`:**
  - For each `assets/<vid>` directory, call `copy_slots(src_v, dst_v, skip=lambda n: _asset_ref(kind, rid, vid, n) in gone)`.
  - The file loop skips image files, the `image-refs/` contents and the journal, and copies the remaining files with today's exists/tombstone rules.
- **`sync._copy_tree`** works the same way, with `overwrite=True` for the asset version directories.

**Steps:**

- [ ] **Step 1: Write the failing tests.**
  - `test_demote_copy_respects_name_tombstone_for_refs`: the world record has ref `gallery_2` and the campaign tombstoned `assets/characters/sera/default/gallery_2`. After `copy_record_dir_down` there is no campaign `gallery_2`.
  - `test_demote_copy_keeps_campaign_legacy_art`: the campaign has a legacy `gallery_1.png` and the world has a `gallery_1` ref. After the copy, the campaign still serves its own bytes.
  - `test_demote_copy_never_copies_journal`.
  - `test_demote_copy_fills_override_ref_keeping_focus`: the campaign holds a focus override on an inherited avatar, and the world record is demoted with copy-down. The campaign avatar ref now holds the world image with the campaign's focus.
  - `test_sync_promote_copies_refs_not_bytes`: the blob count is unchanged, and the world record's refs equal the campaign's.
  - `test_overlay_promote_links`: `overlay.promote_image` on an inherited world gallery image writes no blob.
  - `test_campaign_focus_on_inherited_avatar_is_override`:
    - `PUT /api/campaigns/{cid}/characters/{char}/versions/{vid}/images/avatar/focus` on an inherited avatar creates a campaign image-less ref and no campaign `focus.json`;
    - `GET` detail shows that focus;
    - after the world avatar changes (new bytes uploaded on the world route), the campaign serves the **new** world avatar with the campaign focus.

- [ ] **Step 2: Run and see FAIL.**

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run and see PASS**, together with the full `tests/test_overlay.py` and the sync test files.

- [ ] **Step 5: Commit.** `git commit -m "feat(images): per-slot tree copies honour name tombstones; campaign focus override"`

---

### Task 8: Comparison readers (shadowed images, campaign slimming)

**Files:**
- Modify: `backend/src/grimoire/store/overlay.py` (`shadowed_images`, `_novel_bytes`).
- Modify: `backend/src/grimoire/store/campaigns/lifecycle.py` (`_tombstone_deleted_copied_assets`, `_prune_duplicate_files`).
- Test: `backend/tests/test_overlay.py`, `backend/tests/test_campaigns_store.py`.

**Rules:**
- **`shadowed_images`:**
  - `held` is the set of names in the campaign's `assets.list_images`.
  - A world row is novel unless some campaign row shares its `image_id` (when both have one) or its bytes. Byte equality is checked by comparing resolved paths with `filecmp` when either side lacks `image_id`; keep the size bucketing.
  - The output shape is unchanged.
- **`_prune_duplicate_files`:**
  - Ref files are pruned when byte-identical, which is safe because ref JSON is deterministic, except for `image-refs/avatar.json` when its `focus` differs; byte-identity already covers that.
  - The focus guard becomes `assets.path_in(p.parent, AVATAR)` for a `focus.json`, and "a campaign-side avatar ref exists" for a ref.
  - The descriptions guard becomes `assets.names_in(p.parent)[0]` non-empty.
  - Directory cleanup also removes an emptied `image-refs/`.
- **`_tombstone_deleted_copied_assets`:**
  - Walk the world's version directories (`<kind>/<aid>/assets/<vid>`), not their image files.
  - Take the world's names from `assets.names_in(wdir)[0]` and the campaign's from `assets.names_in(cdir)[0]`.
  - Tombstone `world_names - campaign_names` under the existing conditions.
  - The kind list is unchanged.

**Steps:**

- [ ] **Step 1: Write the failing tests.**
  - `test_shadowed_images_with_refs`:
    - the same image id under the same name is not shadowed;
    - a different id is shadowed;
    - a world ref against a campaign legacy file with identical bytes is not shadowed.
  - `test_slimming_prunes_identical_refs_keeps_divergent`: the same ref JSON is pruned. A ref with a different id is kept. A ref with the same image but a different focus is kept.
  - `test_slimming_tombstone_by_names_with_refs`: a world ref name missing in the campaign is tombstoned, and a name held as a ref is not.

- [ ] **Step 2: Run and see FAIL.** Then **Step 3: implement**, and **Step 4: run and see PASS**, including the whole `test_campaigns_store.py`.

- [ ] **Step 5: Commit.** `git commit -m "feat(images): shadowing and slimming compare placements by identity"`

---

### Task 9: Collection checks by identity (manifest format 1 retained)

**Files:**
- Modify: `backend/src/grimoire/store/image_collections.py` (`put_member`, `publish`, `guard_write`, `available`).
- Test: `backend/tests/test_image_collections.py`, `backend/tests/test_image_collection_routes.py`, `backend/tests/test_world_images_store.py`.

**Rules:**
- **`put_member(wid, data)`:**
  - The name is still `MEMBER_PREFIX + sha256(data)`, over the bytes as received.
  - Under the collection lock, compute `obj = image_store.ingest(data, ext)`.
  - Read `existing = assets.resolve(d, name)`:
    - when there is a ref with `image_id != obj.id`, raise `CollectionInvalidError("stored collection member has changed")`;
    - for a legacy file, compare bytes as today;
    - when nothing exists, call `assets.link_in(d, name, obj.id)`.
- **`publish`:**
  - A ref-backed member must resolve. Its identity is its ref, so the byte-hash re-check is skipped, because stored bytes are sanitized.
  - A legacy member keeps today's sha256 check.
- **`guard_write(wid, name, data)`:**
  - If the name is referenced and ref-backed, `data` is harmless exactly when `image_store.ingest(data, sniffed_or_png).id` equals the ref's id.
  - Deletion (`data is None`) is still refused.
  - A legacy member is unchanged.
- **`available`:**
  - The URL `v` is `assets.image_version(path)`, which is the blob sha for a ref.

**Steps:**

- [ ] **Step 1: Write the failing tests.**
  - `test_put_member_twice_same_bytes_ok`.
  - `test_pixel_duplicate_reupload_does_not_trip_guard`: re-uploading a member's pixels with a different `pHYs` through the world image PUT route returns 200 rather than 409. Different pixels still return 409 `image_in_collection`.
  - `test_publish_with_ref_members`.
  - `test_legacy_member_rules_unchanged`: hand-plant a legacy member file.

- [ ] **Step 2: Run and see FAIL.** Then **Step 3: implement**, and **Step 4: run and see PASS** (all three test files).

- [ ] **Step 5: Commit.** `git commit -m "feat(images): collection member checks compare image identity"`

---

### Task 10: HTTP identity, thumbnails, `image_id` in responses and types

**Files:**
- Modify: `backend/src/grimoire/routes/common.py` (`_serve_image_file`).
- Modify: `backend/src/grimoire/store/thumbs.py` (`_key`, `thumbnail`).
- Modify: `backend/src/grimoire/store/characters.py` (≈425, the detail dict).
- Modify: `backend/src/grimoire/store/overlay.py` (≈1894, ≈1969). Add `"image_ids": {name: image_id}` beside `image_v`, only for rows that carry `image_id`.
- Modify: `backend/src/grimoire/routes/characters.py`, `routes/campaigns.py`, `routes/worlds.py` and `routes/entities.py`. The record image PUT handlers return `{"name", "ext", "image_id"}`, with `image_id` from `store.assets.image_id(...)` after the put. The gallery rows (`routes/characters.py` ≈800–885) carry `image_id` when the listing row has one.
- Modify: `backend/src/grimoire/routes/world_images.py` and `routes/campaigns.py`. The library and cover PUT responses add `image_id`, from `store.assets.resolve(<that directory>, name).image_id`. `assets.image_id(...)` is for per-version record images only.
- Modify: `frontend/src/api/types.ts`:
  - `image_id?: string` on `CampaignImage`, `WorldImage` and `GalleryImage`;
  - `image_ids?: Record<string, string>` on the character-detail version type and on `BaseVersion`.
- Test: `backend/tests/test_routes.py`, `backend/tests/test_thumbs.py`, `backend/tests/test_world_images_routes.py`.

**Rules:**
- **ETag and source token:**
  - In `_serve_image_file`, `source = store.image_store.blob_sha_of(p) or f"{mtime:x}-{size:x}"`.
  - The stat is skipped for a blob, but a read failure still gives a 404.
  - The thumbnail tag is `f'"{source}-{bucket}-{generation}"'`.
- **`thumbs._key`:** when `image_store.blob_sha_of(src)` is set, return `sha256(f"cas|{sha}|{width}|{_encoder()}")[:32]` with no stat. `thumbnail()` must not stat a blob source before the cache lookup.
- **`v` tokens:** before changing anything, grep that no frontend or backend code parses `v` / `image_v` / `avatar_v` / `cover` tokens (`split("-")`, `parseInt` and the like). Record the grep in the commit message.

**Steps:**

- [ ] **Step 1: Write the failing tests.**
  - `test_serve_blob_etag_is_byte_sha`: `GET .../images/avatar` gives an ETag of `"<sha>"`, and `If-None-Match` gives a 304.
  - `test_thumbnail_shared_across_placements`: the same bytes as avatar on two characters. Two `?w=256` requests produce exactly one file under `.cache/thumbs/<gen>/`.
  - `test_put_record_image_returns_image_id`: covers world character, campaign PC and a world location.
  - `test_listing_and_detail_carry_image_id`.
  - `test_legacy_file_keeps_mtime_etag`.

- [ ] **Step 2: Run and see FAIL.** Then **Step 3: implement**, and **Step 4: run and see PASS**.

- [ ] **Step 5: Run `make check-web`** from the repo root (npm ci, typecheck, vitest). Expect PASS.

- [ ] **Step 6: Commit.** `git commit -m "feat(images): byte-sha cache identity, shared thumbnails, image_id in API"`

---

### Task 11: Chub gallery import ordering; PNG card export of a non-PNG avatar

**Files:**
- Modify: `backend/src/grimoire/store/characters.py` (`download_chub_gallery_stream` ≈1249, `export_card` ≈1361).
- Test: `backend/tests/test_characters_store.py` and `backend/tests/test_cards.py` (or wherever `download_chub_gallery_stream` and `export_card` are tested; find with grep).

**Rules:**
- **`download_chub_gallery_stream`:**
  - Yield `{"total": N}` as today.
  - For each path, call `fetch.download_url` and then `image_store.ingest(bytes, ext)`. Collect `(i, id)` and yield `done` events as today.
  - **Only after** the loop, under `_image_locks_held` for the affected names:
    - delete the existing `gallery_*` slots that are not being replaced;
    - `assets.link_in` each `gallery_{i}`, keeping today's `i`-from-0 naming.
  - A download that raises does not wipe the gallery.
- **`export_card(root, cid, vid, "png")`** (plan review B4):
  - Add a keyword to `cards.dumps(card, fmt, avatar=None, *, plane_png: bytes | None = None)`. When `fmt == "png"` and the avatar is not a PNG, `plane_png` (when given) becomes the image plane via `_png_image_plane(plane_png)`. The original avatar **still travels** in the card's assets through `_with_avatar_asset`, exactly as today.
  - `export_card` builds `plane_png` by decoding the avatar with Pillow: `seek(0)`, `RGBA` if it has alpha or transparency else `RGB`, saved as PNG.
  - If decoding fails, pass `None`, which is today's placeholder behaviour. `test_png_export_roundtrips_a_non_png_avatar_through_the_card` must stay green unchanged.
  - The other formats are unchanged.
- **Replaced Chub slots drop their caption** (plan review S6): when a `gallery_i` is re-linked to a different image id, or removed, its `descriptions.json` entry is dropped, as `delete_image` does today.
- **`sources` wiring** (plan review S6): pass `source_url=` through `assets.put_image` (Task 5's keyword) from:
  - `localize._store` (the remote URL it fetched, never a `data:` URI);
  - `characters.import_card`'s URL avatar path;
  - the Chub gallery download, via `ingest(..., source_url=url)` directly.

**Steps:**

- [ ] **Step 1: Write the failing tests.**
  - `test_chub_gallery_failure_midway_keeps_old_gallery`: monkeypatch `fetch.download_url` to return an image and then raise. The old `gallery_*` slots still resolve.
  - `test_chub_gallery_replaces_after_download`.
  - `test_chub_replaced_slot_drops_description`.
  - `test_localize_records_source_url`: the object's `sources` holds the fetched URL.
  - `test_png_export_of_jpeg_avatar_embeds_real_pixels`: the exported PNG's image plane decodes to the avatar's pixels, not the placeholder. Compare sizes, since the placeholder is 1×1.

- [ ] **Step 2: Run and see FAIL.** Then **Step 3: implement**, and **Step 4: run and see PASS**.

- [ ] **Step 5: Commit.** `git commit -m "feat(images): chub gallery ingests before replacing; PNG export converts avatar"`

---

### Task 12: World bundles, format 2

**Files:**
- Modify: `backend/src/grimoire/store/world_bundle.py`.
- Modify: `backend/src/grimoire/store/image_refs.py`. Add `walk_ids(root: Path) -> set[str]`, every valid image id in any `**/image-refs/*.json` under `root`, excluding journals and with symlinks not followed.
- Modify: `backend/src/grimoire/store/image_store.py`. Add `merge_projection(image_id: str, projected: dict, scope: str) -> None`. This stage-1 implementation fills `description` only when it is absent locally (the stage-2 associations merge has the same shape; implement it now as a union of the projected associations and reviews with `scope`, which is a no-op until stage 2 writes any).
- Test: `backend/tests/test_world_bundle.py`.

**Rules:**
- `FORMAT = 2`. `_read_manifest` accepts 1 or 2, and anything greater than 2 is refused as "newer" (today's message).
- **Export** (`write_bundle`):
  - After the `world/` walk, for each id in `walk_ids(root)` that resolves, add `image-store/blobs/<xx>/<sha>.<ext>` (STORED) and `image-store/objects/<xx>/<id>.json`.
  - The object JSON is `image_store.project(raw, f"world:{wid}")`, deflated.
  - Unresolvable ids are skipped. The world still exports, and the ref simply has no dependency.
- **Import** (`import_bundle`):
  - `_world_members` allows a second root prefix, `image-store`. Its members must fullmatch `image-store/blobs/[0-9a-f]{2}/[0-9a-f]{64}\.(png|jpg|gif|webp)` or `image-store/objects/[0-9a-f]{2}/px1-[0-9a-f]{64}\.json`, and the 2-hex shard must equal the hash prefix. Anything else raises `BundleError`.
  - Extract only the `world/` members into staging, as today.
  - **Blobs:** for each blob member:
    - refuse it with `BundleError` when `ZipInfo.file_size > fetch.MAX_BYTES`;
    - read it, wrapping `zipfile.BadZipFile`, `RuntimeError`, `NotImplementedError` and `OSError` in `BundleError`;
    - check `sha256 == name`, with `BundleError` on a mismatch;
    - call `local = image_store.ingest(data, ext, sanitize=False)`, and map the blob sha to `local.id`.
  - **Objects:** for each object member, parse it (`BundleError` if it is not a dict with a valid `blob.sha256` that is present among the blobs), and map `bundle_id → mapping[blob_sha]`.
  - **Rewrite:** for every staged `image-refs/*.json` whose image is a bundle id with a different local id, rewrite it via `image_refs.write`, keeping focus.
  - Then repoint the URLs and publish (today's code), which yields `wid`.
  - **After publish:** `image_store.merge_projection(local_id, projected, f"world:{wid}")` for each object, with the projection's own scope (`world:<manifest world_id>`) renamed to `world:<wid>`. Failures here are logged through `logs.record` and do not fail the import.

**Steps:**

- [ ] **Step 1: Write the failing tests.**
  - `test_bundle_carries_image_dependencies_once`: two placements of one image give exactly one blob member and one object member.
  - `test_bundle_object_projection_has_no_sources`.
  - `test_import_into_fresh_store_resolves`:
    - export, then wipe `image-store/` and `.cache/`;
    - import;
    - every ref in the new world resolves, and the description is restored.
  - `test_import_reuses_existing_blob`: the blob count is unchanged when the store already has it.
  - `test_import_recomputes_id_and_rewrites_refs`:
    - hand-craft a bundle whose object and ref claim `px1-<other hex>` for a blob;
    - after import, the refs name the locally computed id;
    - no object with the claimed id exists.
  - `test_import_never_overwrites_local_description`: the local object has `description: "Mine"`, and the bundle carries `"Theirs"`. The local value is kept. Also cover a local `""`.
  - `test_import_rejects_mismatched_blob` and `test_import_rejects_unknown_image_store_member`.
  - `test_format_1_bundle_still_imports`: the existing tests stay green.

- [ ] **Step 2: Run and see FAIL.** Then **Step 3: implement**, and **Step 4: run and see PASS** (all of `test_world_bundle.py`).

- [ ] **Step 5: Commit.** `git commit -m "feat(images): world bundle format 2 carries image dependencies"`

---

### Task 13: Backups include the image store and placements

**Files:**
- Modify: `backend/src/grimoire/store/backups.py` (`create_image_backup`'s selection predicate).
- Test: `backend/tests/test_backups_store.py`.

**Rules:**
- **Image-only backup:** select a path when any of these holds:
  - `_is_image_file(path)`;
  - the path is under `assets/image-store/` (blobs and objects);
  - its parent directory is named `image-refs` (journals included).
- **Full backup:** no code change. A test pins that `assets/image-store/...` is in the archive.

**Steps:**

- [ ] **Step 1: Write the failing tests.**
  - `test_image_backup_contains_store_and_refs`: put a character avatar through `assets.put_image`. The image backup zip has the blob, the object JSON and `image-refs/avatar.json`.
  - `test_full_backup_contains_image_store`.

- [ ] **Step 2: Run and see FAIL.** Then **Step 3: implement**, and **Step 4: run and see PASS**.

- [ ] **Step 5: Commit.** `git commit -m "feat(images): backups carry the image store and placements"`

---

### Task 14: Surface roster test, docs, and full gate

**Files:**
- Create: `backend/tests/test_image_surfaces.py`.
- Modify: `docs/store-guarantees.md`. Add a section, "The image store", covering:
  - blob immutability, and the only rewrite (corruption repair);
  - the image-object stripe lock and the ingest/GC lock as leaf locks;
  - placement JSON determinism;
  - the ref-over-legacy rule;
  - what is not promised (mixed-version devices, and a cross-decoder JPEG id).

  Name the guard tests. Run `tests/test_docs_guard.py` to see what it requires of new claims.
- Modify: `CLAUDE.md`. One short paragraph under "Working notes": images are written through `assets` primitives into the content-addressed store, never as files in record directories, plus a pointer to the spec.

**Roster test design:**
- **The `SURFACES` list.** A module-level list, where each entry has:
  - `id`;
  - an upload callable taking `(client, ids)` and returning a response;
  - a GET URL;
  - the record directory to inspect for stray image files.

  It covers:
  - world and campaign character versions;
  - world and campaign PCs;
  - every `store.entities.ENTITY_KINDS` kind in world and campaign;
  - world and campaign libraries;
  - world and campaign covers;
  - collection members via `image_collections.put_member`;
  - greeting images via `localize.localize_greeting`, with `fetch` monkeypatched to return PNG bytes.
- **`test_every_image_upload_route_is_rostered`:** introspect `app.routes` for `PUT` paths matching `/images/{name}` or ending in `/cover`. Every one must map to a `SURFACES` entry. Add a mapping table in the test.
- **The parametrized test, for each surface:**
  - upload real PNG bytes;
  - assert the response is 2xx;
  - assert there is no `*.png|*.jpg|*.gif|*.webp` under the surface's record directory (`rglob`);
  - `GET` gives 200, and the body equals the stored blob;
  - `GET ?w=128` gives 200;
  - the listing route row carries `image_id`;
  - for record surfaces, the image appears in the describe queue (`GET` the undescribed route), is packed once in a campaign Markdown export when referenced from a scene, and is reachable as an art handle once described (spec §13).
- **Review Focus 5:** fork a campaign, delete its world (if the API permits deleting a world with campaigns; otherwise delete a world *character* that a forked campaign promoted earlier). The campaign's ref still resolves.

**Steps:**

- [ ] **Step 1: Write the test.** Run it and fix any surface that still writes a file. A surface that writes a file is a bug in an earlier task's conversion, so fix it there.

- [ ] **Step 2: Write the docs.** Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_docs_guard.py -q` and see PASS.

- [ ] **Step 3: Run the frozen campaign sweep test**, `PYTHONPATH=src .venv/bin/python -m pytest tests -q -k frozen`. Expect PASS with an unchanged `snapshot.json`. If the snapshot differs only because a reader changed, stop and report rather than regenerating it.

- [ ] **Step 4: Run the full gate** with `make check` from the repo root.
  - Fix ratchet findings in new code.
  - If a finding was resolved, run `make baseline` and commit the smaller baseline.
  - `check-apk` is out of scope.

- [ ] **Step 5: Commit.** `git commit -m "test(images): rostered surface coverage; document image store guarantees"`
