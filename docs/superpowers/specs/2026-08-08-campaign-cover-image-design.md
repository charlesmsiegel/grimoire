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
HTML / markdown / plain-text / JSON exports (the collector will carry it, so
adding one later is a template change); picking a cover from images already in
the store rather than uploading one; a crop-focus picker; automatic
downscaling or a size limit on upload.

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

### `store/covers.py`

```python
def cover_path(cid: str) -> Path | None      # newest-wins across extensions
def has_cover(cid: str) -> bool
def put_cover(cid: str, data: bytes, ext: str) -> str   # returns stored ext
def delete_cover(cid: str) -> None
```

`put_cover` and `delete_cover` each take `locks.campaign_lock(cid)`, so the
module is declared in `DOMAIN_MODULES` in `store/locks.py`. It is a new module
mutating campaign-scoped state, so `test_lock_domain_guard.py` requires a
classification, and `UNREVIEWED` is a frozen backlog that may not grow.

`cover_path` resolves through `campaigns.paths.campaign_root(cid)`, which
raises `CampaignNotFound` for an id the guard refuses — the readers below
translate that to a 404.

### The extraction in `store/assets.py`

Every function there computes `_dir(root, id, vid, base)` and then does the
real work on that directory. The second half becomes callable with an explicit
directory, and `covers.py` calls it rather than reimplementing it:

```python
def path_in(d: Path, name: str) -> Path | None
def put_in(d: Path, name: str, data: bytes, ext: str) -> str
def delete_in(d: Path, name: str) -> None
```

`image_path`, `put_image` and `delete_image` become `_dir(...)` plus a call to
these three, preserving their current behaviour exactly: the extension
allowlist (`_norm_ext`), the name guard (`_safe_name`), the per-image lock
(`_image_lock`), write-before-cleanup with identity-checked stale removal, and
newest-wins resolution when two extensions momentarily coexist.

Three pieces stay record-only, in the wrappers: `promote_image`,
`read_focus` / `write_focus` / `clear_focus`, and the
`_heal_stranded_promotion` call inside `image_path`'s avatar branch. A cover
has no gallery to promote from, no crop focus, and never went through the
pre-#253 three-rename swap, so none of that applies to it.

The per-image lock keys on `str(d / name)`, so a cover's lock is naturally
distinct from every record image's — the directory differs.

Writes go through `store.atomic` as they do today, satisfying
`test_atomic_guard.py`; path resolution goes through `campaigns.paths`,
satisfying `test_paths_guard.py`.

## HTTP

All three routes live in `routes/campaigns.py`.

| Route | Behaviour |
| --- | --- |
| `GET /api/campaigns/{cid}/cover` | Serves the bytes. 404 when the campaign has no cover, or does not exist. |
| `PUT /api/campaigns/{cid}/cover` | `multipart/form-data` `UploadFile`; extension taken from the filename. 400 on an unsupported type, 404 for an unknown campaign. |
| `DELETE /api/campaigns/{cid}/cover` | Idempotent — 200 whether or not a cover was there. 404 only for an unknown campaign. |

`PUT` mirrors `_entity_image_put` in `routes/entities.py`: read the upload,
split the extension off the filename, let `ValueError` from the store become a
400 with its message.

`GET` reuses the serving behaviour that `_serve_image` in `routes/common.py`
already implements — an `ETag` from `mtime_ns`/`size`, a 304 on a matching
`If-None-Match`, `Cache-Control: no-cache` for a bare URL and
`public, max-age=31536000, immutable` for a `?v=` one, and a `?w=` downscale
served as WebP through `store.thumbs`. That body is extracted as
`_serve_image_file(p: Path, request)`; `_serve_image` keeps its signature and
calls it after resolving the path, so no existing route changes.

The bare cover URL is `no-cache` + `ETag`, so replacing a cover is visible on
the next render (a 304 when unchanged, fresh bytes when not) without any
cache-busting token in the URL.

### Cover presence in campaign reads

`GET /api/campaigns` rows and `GET /api/campaigns/{cid}`'s `meta` each gain a
computed `cover: bool`, from `covers.has_cover(cid)`. This is a derived field
injected by the route, the same way `get_campaign` already injects
`world_name` — nothing new is written to `campaign.md`.

Without it the list would have to fire a speculative image request per row and
render a broken image for every campaign that has no cover.

## EPUB

`export.collect` gains a `"cover"` key: the packed image name for the
campaign's cover (registered through the shared `Images` registry, so it is
packed and manifested like any other image), or `None`. The registry is shared
by every renderer, so the cover is available to the HTML and markdown builders
whenever someone wants it; only `build_epub` consumes it now.

`build_epub` with a cover:

- **`templates/epub/cover.xhtml`** (new): one full-page `<img>`, `alt` set to
  the campaign title. Styled in `stylesheet.css` — `body.cover { margin: 0;
  text-align: center }` and `.cover img { max-width: 100%; max-height: 100vh }`.
- **`templates/epub/package.opf`**: manifest items gain an optional
  `properties` attribute, so the cover image's item carries
  `properties="cover-image"` (EPUB 3). The metadata block additionally emits
  `<meta name="cover" content="<item id>"/>`, the EPUB 2 form Kindle and older
  readers key on. Both, because readers disagree about which one they honour,
  and emitting both is the standard practice.
- **Spine**: `cover.xhtml` first, then `titlepage.xhtml`, then the chapters.
  The title page stays — a cover and a title page are different pages, and the
  title page carries the world name and date range the cover does not.
- **`nav.xhtml`**: unchanged. The cover is not a ToC entry, by convention.

Without a cover, the output is byte-identical to today's: no cover page, no
`properties` attribute, no `<meta name="cover">`, spine still opening on the
title page. The frozen-campaign fixture has no cover, so its snapshot does not
move.

After editing the templates, `make check` runs both prompt/template harnesses
(`scripts/verify_templates.py` and the eval suite) as it does for any template
change.

## Frontend

**`components/CampaignCover.tsx`** (new) — a settings panel, following the
`CalendarConfig` / `panel-slot` pattern rather than the list/detail editor
pattern (there is no list of covers; there is one image or none). It shows the
current cover, a file input to set or replace it, a Remove button, and inline
error text when the backend rejects the type.

It is opened by a **Cover** button in `CampaignView`'s `rail-foot`, beside the
existing calendar and Response buttons, and renders in the same `panel-slot`
as `CalendarConfig`.

**`CampaignsView.tsx`** — a row with `cover: true` renders
`<img className="list-row-cover" src={api.campaignCoverUrl(c.id, 96)}/>`,
boxed at a fixed size with `object-fit: cover`. A row without one renders an
empty box of the same size, so rows stay aligned instead of jumping between
two layouts.

**`api/client.ts`** — `campaignCoverUrl(cid, w?)`, `putCampaignCover(cid,
file)`, `deleteCampaignCover(cid)`, and `cover?: boolean` on `CampaignMeta`.

## Tests

Backend:

- `test_covers_store.py` — put then read; replacing a `.png` with a `.jpg`
  leaves exactly one file and reads back as the new one; delete removes it;
  an unsupported extension raises `ValueError`; a campaign with no cover reads
  `None`.
- Route tests in the campaigns route suite — `PUT` then `GET` returns the
  bytes with the right media type; `GET` on a campaign with no cover is 404;
  an unknown campaign is 404 on all three verbs; a `.txt` upload is 400; a
  second `GET` with `If-None-Match` is 304; `?w=64` serves WebP; `DELETE`
  twice is 200 both times; `GET /api/campaigns` reports `cover` correctly
  either way.
- `test_epub_store.py` — with a cover: `text/cover.xhtml` exists and is the
  first spine item, the cover image's manifest item carries
  `properties="cover-image"`, `<meta name="cover">` names that item's id, and
  the image bytes are packed. Without a cover: no `cover.xhtml`, no
  `cover-image` property, spine still opens on the title page.
- `test_assets_store.py` — unchanged and still passing, which is what proves
  the extraction preserved behaviour.
- `test_lock_domain_guard.py` — passes with `store.covers` in
  `DOMAIN_MODULES`.

Frontend:

- `CampaignCover.test.tsx` — the panel shows the current cover; choosing a
  file calls `putCampaignCover` and re-renders; Remove calls
  `deleteCampaignCover`; a 400 shows the error text.
- `CampaignsView.test.tsx` — a campaign with `cover: true` renders the
  thumbnail; one without does not.

Gate: `make check`, then the Codex checkpoints in `CLAUDE.md`.

## Decisions taken

- **No crop-focus picker.** Character avatars have one (`focus.json`), because
  they are cropped to circles in dense UI. A cover is shown whole in the book,
  and the only crop is the 96px list thumbnail, which `object-fit: cover`
  handles.
- **No size limit or downscaling on upload.** No other image upload in the app
  has one, and the EPUB should embed the full-resolution image. The list
  thumbnail already avoids the cost through `?w=`.
- **The title page survives.** A cover replacing it would lose the world name
  and the in-world date range.
