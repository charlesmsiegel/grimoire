# Campaign cover images

A campaign can carry one cover image. It shows as a thumbnail on the campaigns
list, is set and cleared from a panel inside the campaign, and becomes the
cover of the EPUB the campaign exports to.

## Problem

Every image in the store hangs off a *record*: `store/assets.py` resolves
`<root>/<base>/<id>/assets/<vid>/<name>.<ext>`, and the bases in use are
`characters`, `greetings`, `locations`, `lore` and the other entity kinds. A
campaign is not one of those records, so there is nowhere to put an image that
belongs to the campaign itself.

Two consequences:

- **The campaigns list is text-only.** `CampaignsView.tsx` renders a name, a
  world, a scene count and a last-scene title. A library of a dozen campaigns
  is a wall of identical rows.
- **The exported book has no cover.** `store/epub.py` opens the spine with
  `titlepage.xhtml` — title, world name, date range, set in Cinzel. That is a
  title page, not a cover: `package.opf` carries no `properties="cover-image"`
  item and no legacy `<meta name="cover">`, so a reader's shelf shows the book
  as a grey rectangle with a filename under it.

## Scope

In: campaign-local cover storage, its HTTP surface, the extraction in
`assets.py` that lets it reuse the existing image machinery, the EPUB cover
page and its manifest metadata, the settings panel, and the list thumbnail.

Out: covers on *worlds* (same mechanism, no request for it); the cover in the
HTML / markdown / plain-text / JSON exports; picking a cover from images
already in the store rather than uploading one; a crop-focus picker.

## Storage

`campaigns/<cid>/assets/cover.<ext>`, alongside `campaign.md`, `activity.txt`
and the campaign's record directories.

Not a key in `campaign.md`. That file is read-modify-written unlocked by
`campaigns.read.touch`, `rename_campaign` and `set_campaign_response` — a race
`store/locks.py` records under `OUTSIDE_DOMAIN` as a known gap — so a cover
recorded there could be dropped by a concurrent rename. The file's presence on
disk is the record, resolved by globbing `cover.*`, exactly as `assets.py`
resolves every other image.

Not under the overlay either. A cover is campaign-local: it is never inherited
from the campaign's world, so there is no world-side copy to shadow, nothing
to tombstone in `deleted`, and no `croot`-read hazard of the kind
`test_overlay_guard.py` exists to catch. `store/overlay.py` is untouched.
`delete_campaign` rmtrees the campaign directory, so the cover is removed with
it and needs no cleanup of its own.

### `store/covers.py`

```python
def cover_path(cid: str) -> Path | None      # None when there is no cover
def cover_version(cid: str) -> str           # "" when there is no cover
def put_cover(cid: str, data: bytes, ext: str) -> str   # returns stored ext
def delete_cover(cid: str) -> None
```

Every one of them first requires the campaign to exist —
`campaigns.paths.campaign_meta_path(cid).exists()`, raising `CampaignNotFound`
otherwise. `campaign_root` alone is **not** an existence check: it only
rejects ids `safe_id` refuses, so without this a `put_cover("no-such-id")`
would happily create `campaigns/no-such-id/assets/` and invent a campaign
directory with no `campaign.md` in it.

`cover_version` returns `assets.image_version(p)` — the `mtime_ns-size` token
the rest of the app already uses for cache-busting — so callers can build a
`?v=` URL without a second round trip. It swallows `OSError` and returns `""`:
`image_version` calls `p.stat()` unguarded, and this runs once per row in
`GET /api/campaigns`, so a cover deleted between resolution and `stat` must
read as "no cover" rather than 500 the whole campaigns list.

`put_cover` and `delete_cover` each take `locks.campaign_lock(cid)`, so the
module is declared in `DOMAIN_MODULES` in `store/locks.py`. It is a new module
mutating campaign-scoped state, so `test_lock_domain_guard.py` requires a
classification, and `UNREVIEWED` is a frozen backlog that may not grow.

`delete_cover` **verifies**. `assets.delete_image` swallows a failed `unlink`
by design (a lost cleanup self-heals there, because `image_path` prefers the
newest file), but here the unlink *is* the operation: on Windows a sync client
or a scanner can hold the file, and a swallowed failure would answer 200 to a
Remove that did nothing. So it re-resolves afterwards and raises `OSError` if a
cover is still there — the same shape as `promote_image`'s "promoted image
could not be cleared".

### The extraction in `store/assets.py`

Every function there computes `_dir(root, id, vid, base)` and then does the
real work on that directory. The second half becomes callable with an explicit
directory, and `covers.py` calls it rather than reimplementing it:

```python
def path_in(d: Path, name: str, *, supported_only: bool = False) -> Path | None
def put_in(d: Path, name: str, data: bytes, ext: str, *, supported_only: bool = False) -> str
def delete_in(d: Path, name: str, *, supported_only: bool = False) -> None
```

`image_path`, `put_image` and `delete_image` become `_dir(...)` plus a call to
these three, preserving their current behaviour exactly: the extension
allowlist (`_norm_ext`), the name guard (`_safe_name`), the per-image lock
(`_image_lock`, keyed on `str(d / name)`, so a cover's lock is distinct from
every record image's), write-before-cleanup with identity-checked stale
removal, and newest-wins resolution when two extensions momentarily coexist.

Three pieces stay record-only, in the wrappers: `promote_image`, the focus
functions, and the `_heal_stranded_promotion` call inside `image_path`'s
avatar branch. A cover has no gallery to promote from, no crop focus, and
never went through the pre-#253 three-rename swap.

**`supported_only` exists because the cover directory is one a human browses
and a sync client writes into.** Today `image_path` globs `name.*` and filters
nothing, so a `cover.txt` — an editor sidecar, a sync conflict note — that is
newer than `cover.png` would *become* the cover, served as
`application/octet-stream` and packed into the book. With `supported_only`,
resolution ignores any suffix outside `_EXTS`, and both `put_in`'s
stale-sibling cleanup and `delete_in` only touch supported-extension siblings
rather than whatever happens to share the stem. All three take the flag, or
Remove would delete the sidecar that Replace was careful to leave alone.

Record images keep today's unfiltered behaviour (`supported_only=False`, the
default) deliberately: `promote_image` raises `ValueError` for "an
externally-placed file whose extension we never accepted", which requires
`image_path` to still return such a file. Changing that is a separate decision
from this feature.

Writes go through `store.atomic` as they do today, satisfying
`test_atomic_guard.py`; path resolution goes through `campaigns.paths`,
satisfying `test_paths_guard.py`. The new helpers take a `Path` and no `cid`,
so the lock-domain guard — which approximates "campaign-scoped" by a `cid`
parameter — does not classify them; `store.covers`, which does take `cid`, is
what carries the lock and the classification.

That is not a hole in the guard, and the docstring on the helpers should say
so: they are lock-agnostic primitives over a directory, with no campaign
identity to lock on. Any future module that builds a `cid`-taking mutator on
top of them is itself classified by the guard — which is exactly what happens
to `store.covers` here.

## HTTP

All three routes live in `routes/campaigns.py` and open with
`_campaign_root_or_404(cid)`, like every other campaign route: that is what
turns an unknown campaign into a 404 (and runs the lazy slim migration).

| Route | Success | Failure |
| --- | --- | --- |
| `GET /api/campaigns/{cid}/cover` | The image bytes. | 404 for an unknown campaign or no cover. |
| `PUT /api/campaigns/{cid}/cover` | `{"ext": "png", "v": "<token>"}` | 400 unsupported/undecodable, 413 too large, 404 unknown campaign. |
| `DELETE /api/campaigns/{cid}/cover` | `{"ok": true}`, whether or not a cover was there. | 500 `{"detail": "cover could not be removed"}` if the file survived, 404 unknown campaign. |

The 500 is raised as an `HTTPException`, not left to the default handler, so
it carries a `detail` string — the panel renders backend `detail` text, and an
unhandled `OSError` would reach it as an opaque server error instead.

`GET` reuses the serving behaviour `_serve_image` in `routes/common.py`
already implements — an `ETag` from `mtime_ns`/`size`, a 304 on a matching
`If-None-Match`, `Cache-Control: no-cache` for a bare URL and
`public, max-age=31536000, immutable` for a `?v=` one, and a `?w=` downscale
served as WebP through `store.thumbs`. That body is extracted as
`_serve_image_file(p: Path, request)`; `_serve_image` keeps its signature and
calls it after resolving the path, so no existing route changes. ~~The
extracted function additionally treats an `OSError` from `stat`/`read_bytes`
as a 404: the cover can be deleted between resolution and read, and that is a
missing image, not a server fault.~~

**Amended during implementation** (final review, 2026-08-08): narrowed to
`FileNotFoundError`, not `OSError` whole. The file-went-away race is real and
still answers 404, but catching `OSError` whole also swallows a
`PermissionError`, a Windows sharing violation, an exhausted file-descriptor
table or a disk read error — cases where the image is still there. Reporting
those as "not found" tells the user their data is missing when it isn't, makes
the frontend mark a valid cover broken, and hides an operational fault behind
the wrong status code. `FileNotFoundError` is exactly the race this section
was written for; the wider catch was never needed to cover it. This applies to
every route through `_serve_image_file`, not only covers — every image route
shares the one helper.

### Upload validation

Two checks the other image routes do not have, because a cover is embedded
whole into an exported book and this backend also runs on Android
(Chaquopy, per `CLAUDE.md`), where several full-size copies of one upload —
request body, `bytes`, the EPUB's in-memory `BytesIO`, its `getvalue()` — sit
in one process:

- **Size cap of 25 MB**, answered with 413. This reverses an earlier
  "no limit, no downscale" decision, which was made for consistency with the
  entity-image routes; the mobile memory profile is the reason to break that
  consistency. Nothing is downscaled — under the cap, the full-resolution
  image is stored and embedded.
- **Decodability**, via `PIL.Image.open` (Pillow is already a base dependency
  — `backend/pyproject.toml:17`, mirrored in `android/app/build.gradle.kts`),
  answered with 400. Extension-only validation accepts arbitrary bytes named
  `.jpg`, which then serve as `image/jpeg`, fail `store.thumbs`, and produce
  an invalid book.
- **Decoded size**, from the same `Image.open`: `w * h` above 50 megapixels is
  a 400. A byte cap does not bound the raster — a few hundred KB of PNG can
  describe a billion pixels, and the thing that eventually decodes it is
  `store.thumbs` serving the 96px list thumbnail, inside the Android process.
  Pillow's own `DecompressionBombError` is a backstop above its ~89 MP
  default, not a policy: 50 MP is the policy, checked before the bytes are
  stored.

~~The stored extension stays the one from the filename (normalized by
`_norm_ext`); the decode check is a gate, not a converter.~~

**Amended during implementation** (final review, 2026-08-08): the **detected**
format decides the stored extension, and a decodable image whose format is not
one the store allows is rejected. Taking the extension from the filename let a
JPEG uploaded as `cover.png` be stored as `cover.png`, served as `image/png`,
and packed into `package.opf` with `media-type="image/png"` — an epubcheck
error, and precisely the "produce an invalid book" outcome the decode check was
added to prevent. `im.format` was already in hand. The filename now decides
nothing.

Deliberately fixed for covers only: every packed *record* image still takes its
manifest media type from its filename suffix, which is a pre-existing systemic
hazard and a separate change.

### Cover presence in campaign reads

`GET /api/campaigns` rows and `GET /api/campaigns/{cid}`'s `meta` each gain a
computed `cover: string` — `covers.cover_version(cid)`, empty when there is
none. A derived field injected by the route, the way `get_campaign` already
injects `world_name`; nothing new is written to `campaign.md`.

A version token rather than a boolean, because the frontend needs both facts
and a bare boolean only gives one. The token both says "there is a cover" and
makes the URL change when the bytes change, so a replaced cover cannot keep
showing as the browser's already-decoded old bitmap under an identical `src`
— and the `?v=` URL then caches immutably, so the thumbnail costs zero
requests on later renders.

## EPUB

`export.collect` gains a `"cover"` key: `covers.cover_path(cid)` — a `Path`,
or `None`.

**Deliberately not registered in the shared `Images` registry.** Everything in
that registry is packed by every renderer: `build_markdown_bundle` writes all
of `data["images"].by_path` into its ZIP, and `build_html` base64-inlines all
of it. Registering the cover would therefore ship it into the markdown bundle
and the HTML page — both declared out of scope — as an unreferenced payload,
and would shift the `img-NNN` numbering of every other packed image.
(`build_json` never calls `collect` at all; it builds from `campaigns_read`,
scenes, chronicle and roster.)

`build_epub` packs the cover itself, under the fixed name
`images/cover.<ext>`, with the manifest id `cover-img`. **It reads the bytes
first, before it composes `docs`, `items`, `spine` and the OPF** — the current
code builds the manifest up front and calls `p.read_bytes()` much later, at
zip-writing time, so a cover read that fails there would leave a cover page
and a manifest entry pointing at a file that never got written. Staging the
bytes first makes "there is a cover" one decision that everything downstream
follows.

- **`templates/epub/cover.xhtml`** (new): one `<img src="../images/cover.<ext>">`,
  `alt` set to the campaign title, `<body class="cover">`.
- **`stylesheet.css`**: `html, body.cover { height: 100%; margin: 0 }` and
  `body.cover { display: flex; align-items: center; justify-content: center }`,
  with `.cover img { max-width: 100%; max-height: 100% }`. Percentages against
  an explicit height context rather than `vh`, whose support in older reading
  systems is uneven; flex rather than `text-align`, which centres horizontally
  only and would leave a short cover pinned to the top of the page. A cover
  smaller than the page is centred, **not** upscaled — a stretched low-res
  cover looks worse than a small sharp one.
- **`templates/epub/package.opf`**: manifest items gain a `properties`
  attribute, emitted only when non-empty, so the cover item carries
  `properties="cover-image"` (EPUB 3). Every item dict must carry the key —
  `_env()` uses `StrictUndefined`, so `it.properties` on the CSS, font and
  chapter items would raise rather than render empty — which means the
  builders that construct those dicts all set `"properties": ""`. The metadata
  block additionally emits `<meta name="cover" content="cover-img"/>`, the
  EPUB 2 form Kindle and older readers key on. Both, because readers disagree
  about which they honour.
- **Spine**: `cover.xhtml` first, then `titlepage.xhtml`, then the chapters.
  The title page stays — it carries the world name and date range the cover
  does not.
- **`nav.xhtml`**: unchanged. The cover is not a ToC entry, by convention.

**A cover that vanishes mid-export does not fail the export.** `collect`
resolves a path; a concurrent PUT of a different extension, or a DELETE, can
remove that file before `build_epub` stages it. The read is wrapped, and an
`OSError` drops the cover entirely — no cover page, no manifest item, no
`properties`, no legacy `meta` — leaving exactly the no-cover book.

This is stricter than what `export.py` does for record images, and knowingly
so. `rewrite_images` degrades a *missing* image to its alt text at collect
time, but every renderer then calls `p.read_bytes()` unguarded at packing
time, so an image deleted in between currently raises. The cover is the one
asset a user can replace from a panel that sits next to the Export menu, so it
is the one where that window is actually reachable; widening the fix to every
packed image is a separate change.

Without a cover, the book is structurally unchanged: no cover page, no
`properties` attribute, no `<meta name="cover">`, spine still opening on the
title page, and the same packed image names. It is **not** byte-identical —
`build_epub` always writes `css/stylesheet.css`, and that stylesheet gains the
`body.cover` rules unconditionally, so a no-cover EPUB differs from today's in
exactly that one file. Rendering the cover rules conditionally would make the
stylesheet vary by campaign for no benefit. The frozen-campaign sweep does not
build EPUBs, so its snapshot does not move either way.

**Format caveat, accepted:** the store accepts `webp` and `gif`, and both are
core media types in EPUB 3, but WebP support in older readers is thinner than
JPEG/PNG. A user who covers a book in WebP may see it on some readers and not
others. Not worth transcoding for; worth knowing.

After editing the templates, `make check` runs both prompt/template harnesses
(`scripts/verify_templates.py` and the eval suite) as it does for any template
change.

## What needs no change

`main.py`'s activity middleware already stamps any successful, non-streaming,
mutating request whose matched route has a `cid` path parameter, so
PUT/DELETE of a cover advances the campaign's activity — and its position in
Recent — with nothing added here.

## Frontend

**`components/CampaignCover.tsx`** (new) — a settings panel, following the
`CalendarConfig` / `panel-slot` pattern rather than the list/detail editor
pattern (there is no list of covers; there is one image or none). States:

- no cover — a placeholder and a "Choose image…" file input;
- a cover — the image at `?v=<token>`, plus Replace and Remove;
- in flight — the control disabled while the PUT or DELETE runs;
- rejected — the backend's `detail` string shown inline (unsupported type,
  undecodable, too large), the previous cover left on screen.

A successful PUT returns the new token, which the panel uses directly rather
than refetching. Remove returns the panel to the no-cover state; a 500 from a
held file shows the error and leaves the cover displayed, which is the truth.

It is opened by a **Cover** button in `CampaignView`'s `rail-foot`, beside the
existing calendar and Response buttons, rendering in the same `panel-slot` as
`CalendarConfig`.

**`CampaignsView.tsx`** — a row whose `cover` token is non-empty renders
`<img className="list-row-cover" src={api.campaignCoverUrl(c.id, {w: 96, v: c.cover})}/>`,
boxed at a fixed size with `object-fit: cover`, with an `onError` fallback to
the placeholder for the case where the cover was removed in another tab
between the list response and the image request. A row without one renders an
empty box of the same size, so rows stay aligned instead of jumping between
two layouts.

**`api/client.ts`** — `campaignCoverUrl(cid, opts?)`, `putCampaignCover(cid,
file)`, `deleteCampaignCover(cid)`, and `cover?: string` on `CampaignMeta`.

## Tests

Backend, store:

- `test_covers_store.py` — put then read; replacing a `.png` with a `.jpg`
  leaves exactly one file and reads back as the new one; delete removes it; an
  unsupported extension raises `ValueError`; no cover reads `None` and an
  empty version; `put_cover`/`delete_cover`/`cover_path` on an unknown
  campaign raise `CampaignNotFound` **and create no directory**; a stray
  `cover.txt` newer than `cover.png` is ignored by resolution and survives
  both a replace and a delete (it is not ours to delete); a monkeypatched
  `unlink` that raises makes `delete_cover` raise rather than return;
  `cover_version` returns `""` rather than raising when the file disappears
  between resolution and `stat` (monkeypatched `stat`).
- `test_assets_store.py` — existing cases unchanged, plus direct cases for
  `path_in` / `put_in` / `delete_in` (both `supported_only` values), since
  "the old tests still pass" only covers what they already exercised.

Backend, routes:

- PUT then GET returns the bytes with the right media type; GET with no cover
  is 404; all three verbs 404 on an unknown campaign; a `.txt` upload is 400;
  a `.png`-named file of non-image bytes is 400; a 26 MB upload is 413; a
  small PNG declaring a >50 MP raster is 400; a second GET with
  `If-None-Match` is 304; `?w=64` serves WebP; `?v=` sets an immutable
  `Cache-Control`; DELETE twice is 200 both times; a DELETE whose `unlink` is
  monkeypatched to fail is 500 with the `detail` string; both
  `GET /api/campaigns` and `GET /api/campaigns/{cid}` report the token, and
  `""` for a campaign without one; a PUT advances the campaign's activity
  stamp (the middleware path, asserted once so a future route rename that
  drops the `cid` path parameter is caught).

Backend, EPUB:

- With a cover: `text/cover.xhtml` exists and is the first spine item, the
  `cover-img` manifest item carries `properties="cover-image"`,
  `<meta name="cover" content="cover-img"/>` is present, and
  `images/cover.<ext>` holds the uploaded bytes.
- Without a cover: no `cover.xhtml`, no `cover-image` property, no legacy
  `meta`, spine opens on the title page — asserted against the full zip
  namelist and the rendered `package.opf`, so "unchanged" is checked rather
  than assumed. The stylesheet is the one file that does change, and the
  assertion is written to say so rather than to claim byte identity.
- Every manifest item renders under `StrictUndefined` — a book with fonts,
  CSS, images and chapters is already the default fixture, so the existing
  build tests catch a missing `properties` key the moment the template reads
  it.
- A cover path that no longer exists at pack time (delete the file after
  `collect`, or monkeypatch the read to raise) produces the no-cover book
  rather than an exception.
- The markdown bundle and the HTML export of a campaign *with* a cover contain
  no cover image, and their packed image names are unchanged — the regression
  the "not in the shared registry" decision exists to prevent.

Frontend:

- `CampaignCover.test.tsx` — the panel shows the current cover; choosing a
  file calls `putCampaignCover` and re-renders at the new token; Remove calls
  `deleteCampaignCover`; a 400 shows the error text and keeps the old cover.
- `CampaignsView.test.tsx` — a campaign with a token renders the thumbnail at
  that `?v=`; one without renders the placeholder; an `onError` on the
  thumbnail falls back to the placeholder (the cover removed in another tab).

Out of reach of this suite, and not claimed by it: whether a 413 is returned
before the body is buffered (Starlette reads the upload before the handler
runs, so the cap is enforced after receipt — it bounds what is *stored* and
*embedded*, not what transits), whether real reading systems render the cover,
and whether a real browser re-decodes an image. The `?v=` token exists so the
last of those cannot arise; it is not asserted by a jsdom test.

Gate: `make check`, then the Codex checkpoints in `CLAUDE.md`.

## Decisions taken

- **No crop-focus picker.** Character avatars have one (`focus.json`), because
  they are cropped to circles in dense UI. A cover is shown whole in the book,
  and the only crop is the 96px list thumbnail, which `object-fit: cover`
  handles.
- **A 25 MB cap and a decode check, but no downscaling.** See "Upload
  validation" — the cap is about the Android process, not about disk.
- **The title page survives.** A cover replacing it would lose the world name
  and the in-world date range.
- **A PUT racing `delete_campaign` can recreate a directory.** `delete_campaign`
  rmtrees the campaign while holding no lock at all — `store/locks.py` records
  that under `OUTSIDE_DOMAIN`, and `module_edit` already notes it as a known
  limit — so an upload that passed its existence check before the rmtree can
  land in a tree that is being removed, leaving a stray `assets/` directory
  behind. The campaign is gone from every listing regardless (`campaign.md` is
  what enumeration keys on). Closing this means locking campaign deletion,
  which is the pre-existing concurrency change that entry exists to defer.
- **Cross-device conflicts are out of reach, as everywhere else.** The
  per-image lock is in-process, and the store may be a synced folder: two
  devices setting different covers with different extensions resolve by mtime,
  and the loser's file lingers until someone replaces the cover. That is the
  same residual `assets.py` documents for every other image, not something
  this feature can close on its own.
