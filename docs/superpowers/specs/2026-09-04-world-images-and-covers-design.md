# A world's own art: a cover, and an image library campaigns inherit

**Date:** 2026-09-04
**Status:** Draft for review (revision 3, after two adversarial gates)

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

Three moves. **Policy is extracted; the write calls are not** — see the guard
note below, which is the reason this seam sits where it does.

**`store/image_library.py` (new).** The scope-free *policy* half of today's
`campaign_images`: `MAX_BYTES` (25 MB, the Chaquopy bound — unchanged and for
its stated reason), `TOO_LARGE`, `ImageTooLarge` (caught by name at
`routes/campaigns.py:845`), `validate_size`, `UNADDRESSABLE`, `RESERVED`,
`addressable`, and the `list_in`-filtering read. It knows nothing about
campaigns or worlds.

**It deliberately does not own `put` and `delete`.** Each scope module keeps its
own `assets.put_in` / `assets.delete_in` call, because
`test_lock_domain_guard.py` recognizes a mutating module by exactly those call
sites — `_ASSETS_WRITERS` at `tests/test_lock_domain_guard.py:306`, consumed at
`:912` — and mutation does **not** propagate across an import. Moving the write
behind `image_library.put(...)` would drop `store.covers` and
`store.campaign_images` out of `_survey()` entirely and fail
`test_the_declaration_has_no_phantom_modules` (`:2315`), whose failure message
invites deleting the two `DOMAIN_MODULES` entries — deleting the guard's grip on
the two modules that most need it. Two or three lines repeated per scope, in
exchange for the write call staying visibly next to the lock it is taken under,
is the right trade and not an accident.

`RESERVED = {"undescribed"}` carries over looking incidental and is load-bearing:
`GET /worlds/{wid}/images/undescribed` is a live route
(`routes/characters.py:639`), so the world side needs that reservation for
precisely the reason the campaign side already has it.

**`store/world_images.py` (new).** `<world>/assets/images`, after proving the
world exists (`worlds.paths.world_exists`, the mirror of
`campaign_images.images_dir`'s existence guard and #360/#373's lesson: a put for
an unknown id must not create a directory of bytes no listing can show).

**`store/campaign_images.py` (rewritten around the overlay).** Below.

**`store/covers.py` (widened, one module).** `validate()`, `_FORMAT_EXT`,
`MAX_BYTES`, `MAX_PIXELS` and `TOO_LARGE` are already scope-free. The module
grows `world_*` faces beside its campaign ones, each with its own
`assets.put_in` / `assets.delete_in` call for the reason above. The PIL decode
and the 50 MP raster bound stay exactly where they are and apply to both: a
cover is the one image thumbnailed for a list, which is the reason that check
exists, and both covers are. A `wid`-taking function is not a campaign mutator
by `_takes_cid`'s convention, so the world faces do not disturb this module's
existing `DOMAIN_MODULES` standing.

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
directory.

**No new `store/locks.py` entries.** `_survey()` only yields a module that has a
function taking a parameter literally named `cid` (`_takes_cid`,
`tests/test_lock_domain_guard.py:443`), and `world_images` takes `wid` while
`image_library` takes a `Path` — so neither is ever surveyed, and declaring
either would fail `test_the_declaration_has_no_phantom_modules` (`:2315`) as a
phantom. The reason each takes no lock goes in its own module docstring, which
is where a reader meets it. `campaign_images` stays in `DOMAIN_MODULES`, and
stays surveyed because it keeps its own `assets` write calls.

### `overlay.add_deleted` has to take the lock

This is a correctness fix the inheritance design depends on, not a nicety.
`overlay.add_deleted` (`store/overlay.py:138`) is an **unlocked**
read-modify-write of the whole `deleted.json`, and `overlay.delete_image` calls
it unlocked too. A library tombstone written under `campaign_lock` concurrently
with a record-image tombstone written without it loses one of the two, silently
and permanently — and the lost one resurrects an image the user deleted, which
is the one direction of failure `overlay.deleted`'s own fail-soft docstring says
a user cannot spot by looking.

So `add_deleted` takes `campaign_lock(cid)`. The lock is reentrant, so callers
already inside a hold pay nothing — `reclassify.py:156` is one of those, inside
the hold opened at `reclassify.py:145`. The genuinely unserialized callers are
`overlay.py:645,913,1276,1459` (the last being `overlay.delete_image`, the very
race this closes) and `campaigns/lifecycle.py:239,380`. `_drop_deleted` takes it
for the same reason — same file, rewritten whole.

**Two of those callers must take the lock around their loop, not per ref.**
`campaigns/lifecycle.py:238` is `for ref in deleted_by_user: add_deleted(...)`
and `:380` calls it per name inside a per-version loop. Locking inside
`add_deleted` alone would turn each into N advisory file-lock round trips
(`locks.py:47`, `proclock.acquire` when `_depth == 0`) on top of N whole-file
rewrites — and, worse, make each iteration individually able to raise
`CampaignBusy`, leaving `ensure_campaign_slim` **half-migrated**. That migration
runs lazily on ordinary request paths (`routes/common.py:1015`), so a contended
campaign would turn it into a 409. Holding the lock around each loop fixes the
cost and the atomicity together: the migration's tombstone set lands whole,
which it does not today.

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
| `delete_image(cid, name)` | unlink campaign-side if a copy is there, **then** tombstone if the world still holds the name — `overlay.delete_image`'s order (`store/overlay.py:1456`), which is the only one that gets a *shadowed* name right. Deleting a shadow with only the first half turns Delete into Revert; with only the second, into a delete that leaves the bytes. Either way the campaign-side description entry is dropped. |
| `read_descriptions(cid)` | the merged campaign-over-world map, the rule `overlay.read_descriptions` applies to record art. It is a **named function on this module**, because `context/art.py:342,650` and the listing routes each need it and a second hand-rolled merge is the thing that drifts. |

`assets/library/<name>` is a new ref shape beside `_asset_ref`'s
`assets/<base>/<aid>/<vid>/<name>`. It cannot collide: every existing asset ref
has five segments and a base drawn from the kind roster, and `library` is not a
kind. Checked against the other readers of `deleted.json` rather than assumed —
`campaigns/lifecycle.py`'s slim migration only walks `wroot/<kind>` and requires
a 5-part path whose `parts[2]` is `assets`; `sync.dependents` and
`record_refs.repoint` handle record refs only, and the latter excludes
`deleted.json` by name.

### A tombstone may not outlive the image it hid

`overlay.forget_world_record` (`store/overlay.py:1849`) already calls this exact
shape a defect for record art — "per-asset tombstones outlive the record they
hid, and they hide by slot" — and sweeps `assets/<kind>/<rid>/` when the world
record goes. A library image has no record, so nothing would sweep it: deleting
`map` world-side and re-uploading a new `map` would leave every campaign that
had tombstoned the old one permanently blind to the new one, with no UI to
undo it, because `list_images(cid)` hides the name it is hiding.

So **a world-side library delete sweeps the dependent campaigns' refs**:
`dependent_campaigns` for the world root, then drop `assets/library/<name>` from
each.

**It takes `locks.hold_all` BEFORE deleting the bytes, and that is deliberately
the opposite of `forget_world_record`'s design.** That sweep loops per campaign
and catches `locks.StoreBusy` per campaign, for a reason its comment states
plainly: a campaign busy for `LOCK_TIMEOUT` "would otherwise abort the sweep for
every campaign after it and 500 a delete that has already happened". `hold_all`
raises `CampaignBusy` on the first lock it cannot take
(`store/locks.py:777-783`), so specifying it *after* the delete would produce
exactly that: bytes gone, zero campaigns swept, a 500, and every stale tombstone
left standing.

The two cases differ in what a skipped campaign costs. For a record, skipping
leaves that campaign at the pre-#225 behaviour — visible state that a later
sweep or a human can still reach. For a library image, a skipped campaign is
**permanently and invisibly blind** to any future image of that name, with no UI
that can show it, which is the whole failure this section exists to prevent. A
partial sweep is therefore not an acceptable outcome here, and the operation
must be all-or-nothing. So this mirrors `reclassify.world_entity`
(`store/reclassify.py:198`) instead: acquire every dependent campaign's lock
first, and let a busy campaign refuse the *whole* delete with `CampaignBusy`.
A refusal is retryable; a half-done sweep is not.

`DELETE /worlds/{wid}/images/{name}` is therefore a multi-campaign write, which
also makes it one that stamps every campaign it wrote (`store/revision.py`). It
carries no `@leaves_campaign_unchanged`: that marker is for campaign-path routes
(`routes/common.py:1314`), and this route's path names a world.

**The describe backlog split does not change, and that is the point.**
`GET /campaigns/{cid}/images/undescribed` says in its docstring that inherited
art belongs to the world's queue, "where describing it once serves every
campaign on that world". With a world library that sentence becomes true of
library images too — so the campaign backlog must filter its library rows to
**campaign-owned** ones, and the world backlog gains the world library. Reusing
the now-merged `list_images` there unfiltered would re-offer every world image
in every campaign's queue: the exact failure that docstring exists to prevent.

**One honest gap, and what closing it would cost.** `test_overlay_guard.py`
flags "a campaign root meeting an inheritable *kind*", and the library is not a
kind — so as it stands nothing catches a future read of
`campaign_root(cid) / "assets" / "images"` that forgets to read through, which is
this codebase's most repeated bug class.

It is not true that the guard *cannot* catch it. Its raw-path form already
matches `croot / "<literal>"`, so adding `assets` to the flagged roster would
work. The cost is the two call sites that legitimately build that path
(`covers.py:117` and `campaign_images.images_dir`) needing `# overlay-ok:`
markers against a budget that is **already full at 4/4**
(`test_overlay_guard.py:476`) — so it means raising a cap, which is a decision
about the exemption budget rather than about this feature. Deferred on those
terms, not waved away: the mitigation here is containment plus a stated claim
(all library resolution lives in `campaign_images`, and its docstring says so),
and the roster extension is recorded as the follow-up that would make it
enforced.

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
POST   /api/worlds/{wid}/images/{name}/description/draft   -> 202
```

The draft route carries **no `@computes_only`**, matching the three world
description-draft routes that already exist (`routes/worlds.py:583`,
`routes/entities.py:463`, `routes/characters.py:249`). The marker is resolved
from a `cid` path parameter (`main.py:286`) and its own docstring says it marks
"a campaign-scoped POST" (`routes/common.py:1296`) — on a world route it would
be inert, and an inert decorator in a spec is a false claim. The rest of the
draft contract does hold: `runs.world_subject` and all four `("world", wid)` run
routes exist (`routes/runs.py:1162,1252`).

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
answers with the backlog, and the new module's inclusion is itself covered by
`test_every_domain_router_is_composed` (`test_route_order.py:217`).

The description-draft route needs a **`CROSSING_PAIRS` entry**: it crosses
`POST /api/worlds/{wid}/{kind}/instantiate/{mid}/{content_id}` at eight segments
with neither pattern generalizing the other, which `test_route_order.py:172`
fails on unless the pair is pinned. The campaign mirror is already pinned
(`:99-101`) with the reasoning to copy — "images" is not an entity kind, so the
instantiate pattern can never legitimately claim a URL under it.

Campaign side, unchanged in shape and changed in resolution:

- `GET /campaigns/{cid}/images` lists inherited images too, each row carrying
  `inherited`.
- `GET|PUT|DELETE /campaigns/{cid}/images/{name}` resolve, shadow and tombstone
  per the table above.
- `PUT .../description` on an inherited image writes campaign-side — a
  description divergence, which is what the record surfaces already do. This
  works **because** `set_description` passes `names={...list_images(cid)}` into
  `image_descriptions.set_in`, where `names` is the existence check that raises
  "unknown image" (`image_descriptions.py:160`): once `list_images` is merged,
  that set admits inherited names. Said out loud because it is load-bearing and
  otherwise invisible — keeping `names` campaign-only would 404 exactly the
  divergence this bullet promises.
- `GET /campaigns/{cid}/images/undescribed` filters to campaign-owned library
  images.
- `GET /campaigns/{cid}/gallery` and `GET /worlds/{wid}/gallery` each gain the
  library as a base, with `id` and `vid` empty. The world gallery emits
  `kind: "world"`. **The campaign gallery emits `kind: "campaign"` for every
  library row, inherited ones included** — that route requires a campaign-scoped
  URL on every row it returns (`routes/characters.py:800`), and the kind is what
  names the URL scope, so an inherited row cannot be `kind: "world"` there.
  Origin is carried by `record_name` instead ("World library" when inherited,
  "Campaign library" when the campaign's own), which is also the honest label
  for a reader.

  `kind: "campaign"` is already on the wire in the *describe backlog*
  (`list_campaign_undescribed_images`, branched on at `DescribeQueue.tsx:71`) —
  but it is **not** on the wire in either gallery today, and
  `ImagesView.tsx:10-18`'s `KIND_LABELS`/`KINDS` is a fixed eight-kind map the
  rail iterates at `:322`. Both new kinds must be added there or they get no
  filter row at all (the `??` fallback at `:61` prevents a crash, not an
  omission).

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

**The describe badge — three call sites, not two.** `_DESCRIBE_BASES`
(`routes/todo.py:366`) is a base roster consumed at `routes/todo.py:354` (the
count), `routes/shell.py:129` (the rail badge) and **`routes/todo.py:492`**,
`_has_world_describe` — the cheap yes/no that decides whether the chore is
computed at all. Its counter, `image_descriptions.undescribed_count`, is a
*base walker* (`image_descriptions.py:222` requires
`<root>/<base>/<record>/assets/<vid>/`) and structurally cannot reach a flat
library directory.

Miss the third and the bug survives one level up: a world whose only undescribed
art is library art gets a fixed count and a fixed badge, while the probe returns
False, the chore row never renders, and nobody is told the queue has rows. All
three get the library's own count. Two consequences to handle rather than
discover: `_DESCRIBE_BASES`' comment ("it must stay that list", mirroring
`characters.list_undescribed_images`) stops being true once the world backlog
holds a non-base, so it gets restated; and `_chore_world_describe`'s `fix_label`
is "The cast" (`todo.py:381`), which points a library-only backlog at the wrong
section.

**Export (`store/export.py`).** Three edits, and they are the risky ones:

- `_IMG_URL` gains `/api/worlds/<wid>/images/<name>`, replacing the comment that
  reserves it as never-written. The regex header states the stakes correctly
  already: "A URL shape missing from here is not a rendering bug — it is a book
  shipped with that image silently degraded to its alt text."
- It must be a **second named group** (or a second alternative), not a widened
  prefix on the existing one. Today `(?P<lib>images)` captures the literal
  segment rather than the scope (`store/export.py:46`) and `_resolve_image`
  branches on `if m["lib"]` alone (`:157`) — so a shared group would leave the
  resolver unable to tell a world-shaped URL from a campaign-shaped one.
- `_resolve_image`'s library branch resolves through the campaign's library view
  (campaign copy, tombstone, then world) rather than campaign-only. **The
  world-shaped branch resolves through that same view**, not straight at
  `wroot_of(cid)`: the campaign's tombstone means "this campaign does not have
  this image", and a world-shaped URL sitting in an inherited lore body is still
  being exported *for that campaign*. Resolving it world-side would pack, into
  the book, the one image the reader deleted. The world root is still what the
  view falls through to, so a non-tombstoned image resolves exactly as before.

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
  existing call sites (`CampaignHub.tsx:350`, `CampaignView.tsx:4328`). It must
  **not** claim the `.campaign-cover` class name: `index.css:429` records that
  taking that name once redefined a 260px preview into a 104px thumbnail
  everywhere the component renders.
- **The gallery stays a browser.** `ImagesView.tsx:68` says so in as many words
  — "There is nothing to edit here — the gallery is a browser, and the two
  sidecars it reports are written in the editors that own them" — and its tab
  strip is `"gallery" | "queue"` (`:20`). Hanging upload and delete off the grid
  would contradict the one sentence that component is built around. So the
  Images tab gains a **third tab, "World art"**, holding the `CoverPanel` and
  the library editor; the gallery keeps its contract and merely gains the
  library as a base it *reports* on, like every other base. Brainstorming put
  both halves in this tab, and this is how they fit in it without breaking it.
- **That third tab is world-scoped only.** `ImagesView` is also reached
  campaign-scoped (`WorldView.tsx:516` passes `forCampaign`, and the gallery
  then reads `listCampaignGallery`, `ImagesView.tsx:184`). The tab strip omits
  "World art" when `forCampaign` is set: a world cover and a world library are
  the world's, and offering their upload and delete buttons from inside a
  campaign view would edit one thing while the reader is looking at another.
  The Tagging queue is unconditionally world-scoped (`:296`) and is precedent
  for reaching world state from here — but tagging edits a sidecar, while this
  replaces a cover and deletes bytes for every campaign in the world, which is
  not the same weight.
- `components/DescribeQueue.tsx` handles `kind === "world"` alongside its
  existing `kind === "campaign"` branch (draft and save go to the world routes).
- `components/PostImagePicker.tsx` shows inherited images with their origin
  marked; removing one is offered as "remove from this campaign" (it writes a
  tombstone), which is a different sentence from deleting the campaign's own.
- `routes/WorldsView.tsx` cards render the cover with the `broken`-by-version
  fallback `CampaignsView.tsx:83` documents, and `routes/WorldView.tsx` shows it
  in the header. CSS mirrors `.shelf-cover`.
- `api/client.ts` gains the world cover and world library calls, and
  `KIND_LABELS`/`KINDS` in `ImagesView.tsx` gain both new kinds. `CampaignImage`
  gains `inherited` — it is declared in `api/types.ts:847` and re-exported
  through `client.ts:15`, so the type edit goes in `types.ts`.

## Testing

Backend (pytest, `GRIMOIRE_HOME` per test as always):

- `image_library`: addressable/reserved/unaddressable names, the two size
  checks, newest-wins resolution, a `notes.txt` neither served nor deleted.
- `world_images` and the world cover: put/list/serve/replace/delete, the
  extension named from bytes and not the filename, unknown-world put creates
  nothing.
- Inheritance: inherited list and serve; campaign shadow wins; deleting a
  *shadowed* name removes the campaign copy **and** tombstones (it does not
  revert to the world's); tombstone hides and 404s; description merge; campaign
  backlog excludes inherited; world backlog and the `todo`/`shell` badge counts
  both include the world library.
- Tombstone lifetime: a world-side delete sweeps dependent campaigns' refs, so a
  re-uploaded world image of the same name is visible again in a campaign that
  had tombstoned the old one.
- `add_deleted` under the lock: two concurrent tombstone writes to one campaign
  both survive.
- Route order: `/worlds/{wid}/images/undescribed` still answers the backlog, and
  the new `CROSSING_PAIRS` entry is present.
- Export: a post carrying an inherited library URL packs the world's bytes; a
  world-shaped library URL in an inherited lore body packs too; **both shapes**
  degrade to alt text when the campaign has tombstoned the image.
- Art pool: a described world image is offered to a campaign and resolves; a
  tombstoned one is not offered.
- Bundle and fork: cover and library survive export then import under a new id,
  with the URL rewrite. `world_fixtures.tree()` diffs the whole tree, so
  `test_world_bundle` and `test_world_fork` cover `assets/` automatically —
  **but only once `SEEDED_FILES` (`world_fixtures.py:39`) actually writes one**,
  which today it does not. Seeding a world cover and a library image with a
  description is what turns those two existing tests from vacuously passing to
  load-bearing.
- Unknown-id sweeps: the two targeted image-write enumerations reach neither new
  route — `test_routes.py:1092` requires a record segment before `/images`, and
  `test_path_guard_store.py:353` is `^/api/campaigns/` only. Both docstrings say
  their point is catching "route number five, added later by someone who did not
  read this file", which is this change. A `_world_library_write_routes` sibling
  is part of the work.
- Guards and frozen rosters, none of which are optional:
  - `backend/tests/store_api_baseline.json` is a frozen facade roster already
    listing `campaign_images` and `covers`. It compares **both** `store.__all__`
    and the public names in `dir(store)`, and that second list already carries
    modules nothing re-exports (`paths`, `statcache`, `vectors`) — so it fails
    the moment anything imports `grimoire.store.world_images`, re-export or not.
    Regenerating it is a reviewed act in the same commit, not a silencing, and
    it is not optional by declining to re-export.
  - `backend/tests/fixtures/frozen_campaign/snapshot.json` moves: `sweep.py:110`
    and `:115` call `list_worlds` and `read_world`, both of which gain a `cover`
    field. Regenerated deliberately with the change, per that directory's README
    — `home/` itself is never touched.
  - `test_lock_domain_guard.py`: `covers` and `campaign_images` must still be
    surveyed after the seam moves (the reason the write calls stay put).
  - **Every relevant marker budget is already full**, counted in `backend/src`:
    `overlay-ok` 4/4 (`test_overlay_guard.py:476`), `lock-domain-ok` 2/2
    (`test_lock_domain_guard.py:2378`), `routing-ok` 3/3
    (`test_routing_guard.py:216`), `atomic-ok` 2/3
    (`test_atomic_guard.py:172`). Any new exemption means raising a cap, which
    is part of this change and a thing to argue for rather than bump.
  - `make baseline` if a lint count moves, committed with the fix.

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
