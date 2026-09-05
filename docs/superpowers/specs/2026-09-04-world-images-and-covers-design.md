# A world's own art: a cover, and an image library campaigns inherit

**Date:** 2026-09-04
**Status:** Draft for review

## Problem

Every other record in the store can hold pictures. Characters and PCs have
per-version galleries with an avatar, a focus point and descriptions; the five
entity kinds and greetings have galleries of their own; a campaign has a cover
(`store/covers.py`) and an image library that belongs to no record at all
(`store/campaign_images.py`, #376).

A **world** has none of it. `worlds/<wid>/` holds `world.md`, the kind folders,
`characters/`, `pcs/`, `greetings/`, `sheets/`, `plotmap.json`, `tags.md` and
`calendar.json` — and no `assets/` directory. Two consequences:

- **The worlds list draws no picture.** `routes/WorldsView.tsx` renders a
  `.world-card` of a name and a count footer, beside a campaigns list
  (`routes/CampaignsView.tsx:289`) that renders a 208px cover per shelf.
- **Art that belongs to a world has nowhere to live.** A regional map, a banner,
  a piece of establishing art for the setting is not a character's, not a
  location's, and not any one campaign's. Today the only home for it is one
  campaign's library — where every *other* campaign in that world cannot see it,
  and where it is copied per campaign or not had at all.

The second is the substantive half. `store/campaign_images.py`'s docstring names
the reason it exists — "a map of the coastline, a photograph of the party's
handout, a picture of the room the narrator is describing belongs to none of
them" — and every one of those examples is more often a property of the *world*
than of one campaign in it.

## What this adds

```
worlds/<wid>/assets/cover.<ext>                     # one, or none
worlds/<wid>/assets/images/<name>.<ext>             # any number
worlds/<wid>/assets/images/descriptions.json        # what each one depicts
```

- A **world cover**, on the worlds-list card and the world header, and in the
  world bundle. No fallback in either direction: a campaign without a cover
  keeps its placeholder rather than borrowing its world's (decided in
  brainstorming; see *Rejected* below).
- A **world image library**, managed from the world's Images tab, **inherited by
  every campaign on that world** the way records are: a campaign sees world
  images in its post-image picker and its narrator art pool, may shadow one with
  a campaign copy of the same name, and may tombstone one it does not want.

## The rule that shapes everything else

`store/context/art.py:url_for` already decides this, and it is obeyed here
rather than restated:

> Campaign-scoped for every kind, **including art the campaign inherits from its
> world**: a post carrying a world URL is the one image shape that does not
> follow a campaign which later diverges, which is the reason `PostImagePicker`
> refuses to offer greeting art at all.

So an inherited library image is served to a campaign at
`/api/campaigns/{cid}/images/{name}` — the URL a post already carries today.
Nothing about the transcript format, the `[[art:campaign:name]]` handle grammar
(`context/art.py:HANDLE`), the picker's insert path or the EPUB's URL scanner
changes shape. What changes is where that campaign-scoped URL *resolves*: one
module learns to read through to the world.

The world's own URL, `/api/worlds/{wid}/images/{name}`, exists too — it is what
the Images tab renders and what a lore body may embed — but nothing writes it
into a scene transcript. `store/export.py:_IMG_URL` currently carries a comment
reserving exactly that shape as one "the app never writes"; this spec makes the
app write it, so that comment and that regex both move (below).

## Modules

Three moves, all extraction rather than duplication.

**`store/image_library.py` (new).** The scope-free half of today's
`campaign_images`: `MAX_BYTES` (25 MB, the Chaquopy bound — unchanged and for
its stated reason), `TOO_LARGE`, `validate_size`, `UNADDRESSABLE`, `RESERVED`,
`addressable`, and list/put/delete/describe against a *given* directory. It
knows nothing about campaigns or worlds.

`RESERVED = {"undescribed"}` carries over looking incidental and is load-bearing:
`GET /worlds/{wid}/images/undescribed` is a live route
(`routes/characters.py:639`), so the world side needs that reservation for
precisely the reason the campaign side already has it.

**`store/world_images.py` (new).** `<world>/assets/images`, after proving the
world exists (`worlds.paths.world_exists`, the mirror of
`campaign_images.images_dir`'s existence guard and #360/#373's lesson: a put for
an unknown id must not create a directory of bytes no listing can show).

**`store/campaign_images.py` (rewritten around the overlay).** Below.

**`store/covers.py` (widened).** `validate()`, `_FORMAT_EXT`, `MAX_BYTES`,
`MAX_PIXELS` and `TOO_LARGE` are already scope-free. `cover_path`,
`cover_version`, `put_cover` and `delete_cover` grow root-taking cores with
campaign and world faces. The PIL decode and the 50 MP raster bound stay exactly
where they are and apply to both: a cover is the one image thumbnailed for a
list, which is the reason that check exists, and both covers are.

### Locking, and an asymmetry stated rather than discovered

Campaign-side writes take `locks.campaign_lock(cid)`, as `covers` and
`campaign_images` do today. **World-side writes take no lock, because worlds
have no lock domain at all.** That is not a gap this spec opens; it is the one
`store/overlay.py` already names in `set_description`'s docstring:

> The WORLD-side write (`image_descriptions.set_description` straight onto a
> world root) is still unlocked, and that is not an oversight this closes:
> worlds have no lock domain at all, and `focus.json` and `subjects.json` race
> there in exactly the same way.

The new world writes join that set rather than inventing a half-lock for one
directory. `store/locks.py` classifies `world_images` in `OUTSIDE_DOMAIN` with
that reason, and `image_library` likewise (it takes no `cid` and so has no
campaign to lock). `campaign_images` stays in `DOMAIN_MODULES`.
`test_lock_domain_guard.py` is what fails if a future mutator forgets.

### No focus point on either cover

A record's avatar has `avatar/focus` because it is cropped to a circle in a
dozen places. A cover is one 208px card image and one header; the campaign cover
has never had a focus point, and the world cover does not get one.

## Inheritance

`campaign_images` becomes the campaign's overlay view of the library. The merge
lives there, not in `store/overlay.py`: overlay's machinery is keyed on records
(`INHERITED_KINDS`, `_record_dir`, per-version asset roots) and a library image
hangs off no record. What it borrows from overlay is the tombstone store, which
is already ref-string-keyed and needs no new concept:

| operation | behaviour |
|---|---|
| `list_images(cid)` | campaign names union world names, campaign wins a collision, minus tombstones. Each row carries `inherited: bool`. |
| `image_path(cid, name)` | campaign file; else **if tombstoned, stop** (the serve route 404s rather than falling through); else the world file. The shape of `overlay.image_root`. |
| `put_image(cid, name, ...)` | writes campaign-side. Over an inherited name this is a divergence — no copy-up step, because for a library image the bytes *are* the whole record. |
| `delete_image(cid, name)` | campaign-local: unlink and confirm, as today. Inherited: `overlay.add_deleted(cid, "assets/library/<name>")`. Both drop the campaign-side description entry. |
| descriptions | campaign-over-world merge, the rule `overlay.read_descriptions` applies to record art. |

`assets/library/<name>` is a new ref shape beside `_asset_ref`'s
`assets/<base>/<aid>/<vid>/<name>`. It cannot collide: every existing asset ref
has five segments and a base drawn from the kind roster, and `library` is not a
kind.

**The describe backlog split does not change, and that is the point.**
`GET /campaigns/{cid}/images/undescribed` says in its docstring that inherited
art belongs to the world's queue, "where describing it once serves every
campaign on that world". With a world library that sentence becomes true of
library images too — so the campaign backlog must filter its library rows to
**campaign-owned** ones, and the world backlog gains the world library. Reusing
the now-merged `list_images` there unfiltered would re-offer every world image
in every campaign's queue: the exact failure that docstring exists to prevent.

**One honest gap.** `test_overlay_guard.py` flags "a campaign root meeting an
inheritable *kind*". The library is not a kind, so the guard structurally cannot
catch a future read of `campaign_root(cid) / "assets" / "images"` that forgets to
read through — this codebase's most repeated bug class, in the one shape the
guard misses. The mitigation is containment and a stated claim, not a pretence
of coverage: all library resolution lives in `campaign_images`, its docstring
says so, and the guard's own docstring already declares this class of blind spot
("Passing a campaign root into a *separate* helper ... escapes it").

## Routes

World side, mirroring `routes/campaigns.py`'s cover and library blocks
one-for-one:

```
GET    /api/worlds/{wid}/cover
PUT    /api/worlds/{wid}/cover
DELETE /api/worlds/{wid}/cover
GET    /api/worlds/{wid}/images
GET    /api/worlds/{wid}/images/{name}
PUT    /api/worlds/{wid}/images/{name}
DELETE /api/worlds/{wid}/images/{name}
PUT    /api/worlds/{wid}/images/{name}/description
POST   /api/worlds/{wid}/images/{name}/description/draft   -> 202, @computes_only
```

Upload keeps both size checks and their order: `UploadFile.size` **before**
`read()` (the allocation `MAX_BYTES` bounds, and the one that OOMs Chaquopy),
then `validate_size` on the bytes actually received, because `size` is Optional
in the ASGI contract. The stored extension comes from the bytes
(`routes/common._upload_image_ext`, #321), never the filename. `DELETE` is
deliberately *not* gated by `addressable`, matching
`delete_campaign_library_image`: a stray a sync client dropped must have a way
out of the app.

**These live in a new `routes/world_images.py`, included after `characters`.**
Not in `routes/worlds.py`, and the reason is a live shadowing bug rather than
taste: `routes/__init__.py` includes `worlds` *before* `characters`, and
`characters` owns `/worlds/{wid}/images/undescribed`. A `{name}` route
registered in `worlds.py` matches first and turns the describe backlog into a
404 for an image named `undescribed` — the same class of break the campaign
side records in `campaign_images.RESERVED`'s comment, where it cost a broken
picker tile and a broken post. A route-order test asserts the backlog still
answers with the backlog.

Campaign side, unchanged in shape and changed in resolution:

- `GET /campaigns/{cid}/images` lists inherited images too, each row carrying
  `inherited`.
- `GET|PUT|DELETE /campaigns/{cid}/images/{name}` resolve, shadow and tombstone
  per the table above.
- `PUT .../description` on an inherited image writes campaign-side — a
  description divergence, which is what the record surfaces already do.
- `GET /campaigns/{cid}/images/undescribed` filters to campaign-owned library
  images.
- `GET /campaigns/{cid}/gallery` and `GET /worlds/{wid}/gallery` each gain the
  library as a base: `kind: "world"` world-side, `kind: "campaign"`
  campaign-side, with `id` and `vid` empty and `record_name` "World library" /
  "Campaign library". Those two kind strings are the ones already on the wire —
  `list_campaign_undescribed_images` emits `kind: "campaign"` and
  `DescribeQueue.tsx:71` already branches on it — so this adds one and renames
  none.

`GET /worlds` rows and `GET /worlds/{wid}` gain a `cover` version token, exactly
as `routes/campaigns.py:178,567` do for campaigns.

## Everything downstream

**Narrator art (`store/context/art.py`).** `_library_candidates` reads the
merged list and merged descriptions; `_resolved`'s library branch calls the
now-read-through `image_path`; `handle_for`, `parse_handle`, `LIBRARY` and
`url_for` are untouched. Its docstring's cost note must be corrected rather than
left: it already says the library half "has no record to be in scope through, so
it is included whole", and names that half as where a limit would go if one is
ever needed. The pool now includes the world's library as well, which makes that
half bigger by exactly the number of *described* world images. The note gets the
truth; no limit is added, because a limit nobody has hit is a threshold invented
without evidence.

**Export (`store/export.py`).** Two edits, and they are the risky ones:

- `_IMG_URL` gains `/api/worlds/<wid>/images/<name>`, replacing the comment that
  reserves it as never-written. The regex header states the stakes correctly
  already: "A URL shape missing from here is not a rendering bug — it is a book
  shipped with that image silently degraded to its alt text."
- `_resolve_image`'s library branch resolves through the campaign overlay
  (campaign copy, tombstone, then world) rather than campaign-only; a
  world-shaped library URL resolves against `overlay.wroot_of(cid)` — the
  campaign being exported, not the wid written in the URL, which is the same
  rule the campaign branch already applies and the reason a forked campaign's
  book carries its own copies.

**World bundles.** `store/world_bundle.py` zips the world directory whole and
enumerates nothing, so `assets/` rides along on export and import with no code
change. Import already rewrites `/api/worlds/{old}/` to `/api/worlds/{new}/`
across `.md` and `.json` (`worlds/staging.py`), so a world library URL embedded
in a lore body survives a re-id. The bundle tests gain a case that proves both
halves rather than assuming them.

**Campaign fork.** `store/fork.py` copies a campaign's own tree; the world is
shared, so a fork inherits the same library and the same tombstones as its
source. Nothing to change, one test to add.

**World fork/delete.** `fork_world` copies the directory, so a forked world
carries its cover and library. `delete_world` removes the tree. Neither knows
about assets, and neither needs to.

## Frontend

- `components/CoverPanel.tsx` (new) is today's `CampaignCover` parameterized by
  scope, keeping its `live` ref discipline verbatim — that guard exists because
  the panel is reused across navigation and every await can resolve after the
  reader has moved on. `CampaignCover` becomes its campaign face at the two
  existing call sites (`CampaignHub.tsx:350`, `CampaignView.tsx:4328`).
- `components/ImagesView.tsx` gains a library base in the rail, labelled
  "World library" (or "Campaign library" under `?for=`), with upload, replace
  and delete, plus the world's `CoverPanel` at the top of the tab. This is where
  brainstorming put both halves.
- `components/DescribeQueue.tsx` handles `kind === "world"` alongside its
  existing `kind === "campaign"` branch (draft and save go to the world routes).
- `components/PostImagePicker.tsx` shows inherited images with their origin
  marked; removing one is offered as "remove from this campaign" (it writes a
  tombstone), which is a different sentence from deleting the campaign's own.
- `routes/WorldsView.tsx` cards render the cover with the `broken`-by-version
  fallback `CampaignsView.tsx:83` documents, and `routes/WorldView.tsx` shows it
  in the header. CSS mirrors `.shelf-cover`.
- `api/client.ts` gains the world cover and world library calls; `CampaignImage`
  gains `inherited`.

## Testing

Backend (pytest, `GRIMOIRE_HOME` per test as always):

- `image_library`: addressable/reserved/unaddressable names, the two size
  checks, newest-wins resolution, a `notes.txt` neither served nor deleted.
- `world_images` and the world cover: put/list/serve/replace/delete, the
  extension named from bytes and not the filename, unknown-world put creates
  nothing.
- Inheritance: inherited list and serve; campaign shadow wins; tombstone hides
  and 404s; tombstone survives a re-added world image of the same name;
  description merge; campaign backlog excludes inherited; world backlog includes
  the world library.
- Route order: `/worlds/{wid}/images/undescribed` still answers the backlog.
- Export: a post carrying an inherited library URL packs the world's bytes; a
  world-shaped library URL in an inherited lore body packs too; a tombstoned
  image degrades to alt text.
- Art pool: a described world image is offered to a campaign and resolves; a
  tombstoned one is not offered.
- Bundle: cover and library survive export then import under a new id, with the
  URL rewrite.
- Guards: lock-domain classification, and `make baseline` if a lint count moves.

Frontend (vitest, run from `frontend/`): `CoverPanel` in both scopes; the Images
tab library base uploads and deletes; `DescribeQueue` on a world library image;
`PostImagePicker` marks inherited and offers the campaign-scoped URL; a world
card renders its cover and falls back when it fails to load.

## Rejected

- **A campaign falling back to its world's cover.** Decided against in
  brainstorming: it would push a world read into the campaigns list, the hub and
  the EPUB cover path, and it would turn "remove cover" into "revert to the
  world's" — a different verb wearing the same button.
- **Posts carrying `/api/worlds/{wid}/images/{name}` directly.** This is exactly
  what makes greeting art unofferable in the picker today; see *The rule that
  shapes everything else*.
- **Read-through with no shadowing or tombstones.** Half the machinery, and it
  leaves a state with no exit: a world image you want gone from one campaign.
- **A world lock.** Out of scope, and inventing one for a single directory while
  `focus.json`, `subjects.json` and the world-side description write all still
  race would be a lock that implies a guarantee the world root does not make.
