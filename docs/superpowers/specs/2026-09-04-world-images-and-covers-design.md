# A world's own art: a cover, and an image library campaigns inherit

**Date:** 2026-09-04
**Status:** Draft for review (revision 4, after three adversarial gates)

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
  keeps its placeholder rather than borrowing its world's.
- A **world image library**, managed from the world's Images tab, **visible to
  every campaign on that world** — in the post-image picker and the narrator's
  art pool — with a per-campaign way to hide one it does not want.

## Inheritance is read-through plus hiding, and NOT shadowing

This is the design's centre and the place two review rounds found defects, so it
is stated as a rule before any mechanism:

- A campaign **sees** every world library image (read-through).
- A campaign may **hide** one, with a tombstone. Without that, a described world
  image is force-fed to the narrator art pool of every campaign in the world,
  and there is no way out of it.
- A campaign may **not replace** one under the same name. "I want a different
  picture in this campaign" is answered completely by uploading it under a
  different name.

Shadowing was in revision 3 and is deliberately gone. A record diverges because
a record is a document you edit; a library image is bytes, and copy-on-write for
bytes buys nothing here while costing the most defect-prone surface in the
design — the collision rule, `put`'s divergence semantics, a two-step delete, a
description merge whose correctness rested on an invisible `names=` argument, and
a second branch in the export resolver. All of that is deleted by this one
decision, and none of it is missed. It is additive later if it is ever wanted.

**The one collision that remains is accidental and must still have a rule.** A
campaign may hold `map` and the world may *later* add its own `map`. Nothing can
prevent that, so: the campaign's own file wins, because it is the campaign's and
its posts already point at it. `put_image` refuses a name the world holds *at
the time of upload* (409, "that name belongs to the world"), which is what keeps
the case rare rather than routine.

## The rule that shapes the URLs

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
`routes/campaigns.py:845`), `validate_size`, `UNADDRESSABLE`, `RESERVED` and
`addressable`, plus `listing(d)` — the `assets.list_in` read filtered by
`addressable`. It knows nothing about campaigns or worlds.

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

**`store/world_images.py` (new).** `<world>/assets/images`, after proving the
world exists (`worlds.paths.world_exists`, the mirror of
`campaign_images.images_dir`'s existence guard and #360/#373's lesson: a put for
an unknown id must not create a directory of bytes no listing can show).

**`store/campaign_images.py` (rewritten as the campaign's read-through view).**

**`store/covers.py` (widened, one module).** `validate()`, `_FORMAT_EXT`,
`MAX_BYTES`, `MAX_PIXELS` and `TOO_LARGE` are already scope-free. The module
grows `world_*` faces beside its campaign ones, each with its own
`assets.put_in` / `assets.delete_in` call for the reason above. The PIL decode
and the 50 MP raster bound stay exactly where they are and apply to both: a
cover is the one image thumbnailed for a list, which is the reason that check
exists, and both covers are. A `wid`-taking function is not a campaign mutator
by `_takes_cid`'s convention, so the world faces do not disturb this module's
existing `DOMAIN_MODULES` standing.

### The API surface, spelled out

Left to inference, an implementer invents it. `->` gives the return shape.

```python
# store/image_library.py            (directory in, no scope, no lock)
MAX_BYTES: int; TOO_LARGE: str; UNADDRESSABLE: frozenset; RESERVED: frozenset
class ImageTooLarge(Exception)
validate_size(data: bytes) -> None
addressable(name: str) -> bool
listing(d: Path) -> list[dict]              # [{"name","ext","v"}], addressable only

# store/world_images.py             (wid in; no lock -- worlds have no domain)
images_dir(wid) -> Path                     # raises WorldNotFound
list_images(wid) -> list[dict]
image_path(wid, name) -> Path | None
image_version(wid, name) -> str             # "" when absent; swallows OSError
put_image(wid, name, data, ext) -> str
delete_image(wid, name) -> None             # confirms the unlink; sweeps (below)
read_descriptions(wid) -> dict[str, str]
set_description(wid, name, text) -> None
undescribed(wid) -> list[dict]              # for the world backlog route
undescribed_count(wid) -> int               # for todo.py:354 / shell.py
has_undescribed(wid) -> bool                # for the CHEAP probe, todo.py:492

# store/campaign_images.py          (cid in; campaign_lock on every mutator)
images_dir(cid) -> Path                     # campaign-OWNED dir; see note
list_images(cid) -> list[dict]              # rows carry "inherited": bool
list_hidden(cid) -> list[str]               # tombstoned inherited names
image_path(cid, name) -> Path | None
image_version(cid, name) -> str
put_image(cid, name, data, ext) -> str      # ValueError if the world holds it
delete_image(cid, name) -> None             # unlink or tombstone
restore_image(cid, name) -> None            # drops the tombstone
read_descriptions(cid) -> dict[str, str]    # world's, then the campaign's own
set_description(cid, name, text) -> None    # campaign-OWNED names only
own_undescribed(cid) -> list[dict]          # campaign-owned only, never inherited
```

`images_dir(cid)` stays public but now means *the campaign's own directory*,
which is a wrong answer for two of its three current readers
(`context/art.py:342,650` and `routes/campaigns.py:738`). Those move to
`list_images` / `read_descriptions`; the name keeps its narrow meaning and its
docstring says so.

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

This is a correctness fix the design depends on, not a nicety.
`overlay.add_deleted` (`store/overlay.py:138`) is an **unlocked**
read-modify-write of the whole `deleted.json`, and `overlay.delete_image`
(`:1459`) calls it unlocked too. A library tombstone written under
`campaign_lock` concurrently with a record-image tombstone written without it
loses one of the two, silently and permanently — and the lost one resurrects an
image the user hid, which is the one direction of failure `overlay.deleted`'s own
fail-soft docstring says a user cannot spot by looking.

So `add_deleted` and `_drop_deleted` take `campaign_lock(cid)`. The lock is
reentrant, so callers already inside a hold pay nothing — `reclassify.py:156` is
one of those, inside the hold opened at `reclassify.py:145`. The genuinely
unserialized callers are `overlay.py:645,913,1276,1459` and
`campaigns/lifecycle.py:239,380`.

**`ensure_campaign_slim` needs ONE hold spanning both of its call sites, not two.**
`campaigns/lifecycle.py:238` is `for ref in deleted_by_user: add_deleted(...)`,
and `:240` immediately calls `_tombstone_deleted_copied_assets`, which loops to
`:380` — and opens at `:348` with `gone = overlay.deleted(cid)`, the *read* half
of a read-modify-write. Two separate holds would leave that read outside, and
would let the second raise `CampaignBusy` after the first had already committed;
"the tombstone set lands whole" is only true of a single
`with locks.campaign_lock(cid):` spanning `:238`–`:240`. Per-ref locking is
worse still: each non-reentrant acquire is an advisory file-lock round trip
(`locks.py:47`), N of them, on top of N whole-file rewrites.

**The cost, stated:** `ensure_campaign_slim` gains the ability to raise
`CampaignBusy`, and it runs lazily on ordinary read paths
(`routes/common.py:1015`; `routes/campaigns.py:558,1013,1022,1032,1045,1058`;
`sync.py:211,704,982`), so a contended campaign can answer 409 where today it
cannot. That is the correct trade — a retryable 409 against a silently
half-migrated campaign — but it is a behaviour change and belongs in the
release note, not only here.

## How the campaign's view resolves

| operation | behaviour |
|---|---|
| `list_images(cid)` | the campaign's own names, **plus** world names the campaign neither holds nor has tombstoned. Rows carry `inherited`. |
| `image_path(cid, name)` | the campaign's own file; else, if tombstoned, **stop** (the serve route 404s rather than falling through); else the world's. The shape of `overlay.image_root:1313`. |
| `put_image(cid, name, ...)` | writes campaign-side; refused if the world holds that name now. |
| `delete_image(cid, name)` | unlink campaign-side if a copy is there, **then** tombstone if the world still holds the name — `overlay.delete_image`'s order (`:1456`), which without it turns the accidental-collision delete into a revert. |
| `restore_image(cid, name)` | drops the tombstone. The exit from a hidden image, and the reason nothing here has to be all-or-nothing. |
| `read_descriptions(cid)` | the world's map for inherited names, the campaign's for its own. Disjoint by construction, so this is a union and not a merge — the subtlety that made revision 3's version load-bearing is gone with shadowing. |

**The tombstone filter applies to the inherited half ONLY**, which is exactly
what `overlay.list_images:1306` does and what revision 3 got wrong. Subtracting
tombstones from the whole union instead would hide a campaign's *own* image
uploaded under a previously-tombstoned name: bytes that serve but never list, in
no picker, no gallery and no describe row, with no UI able to clear the
tombstone that hid them. That is `campaign_images.addressable`'s own bug class
(#373) inverted, and it is the single easiest thing to get wrong here.

`assets/library/<name>` is a new tombstone ref shape beside `_asset_ref`'s
`assets/<base>/<aid>/<vid>/<name>`. It cannot collide: every existing asset ref
has five segments and a base drawn from the kind roster, and `library` is not a
kind. Checked against the other readers of `deleted.json` rather than assumed —
`campaigns/lifecycle.py`'s slim migration only walks `wroot/<kind>` and requires
a 5-part path whose `parts[2]` is `assets`; `sync.dependents` and
`record_refs.repoint` handle record refs only, and the latter excludes
`deleted.json` by name.

### A hidden image is visible, which is what makes the sweep cheap

`overlay.forget_world_record` (`:1849`) calls a stale per-asset tombstone a
defect — "they hide by slot" — and sweeps `assets/<kind>/<rid>/` when the world
record goes. A library image has no record, so a world-side delete must sweep
too, or deleting `map` and re-uploading a new `map` leaves every campaign that
hid the old one blind to the new one.

**That sweep is best-effort, per campaign**, exactly as `forget_world_record` is:
`dependent_campaigns(wroot)` (`overlay.py:1699`), then per campaign a
`campaign_lock` and a tombstone drop, catching `OSError`/`ValueError`/
`locks.StoreBusy` **per campaign** and logging. Its comment is the argument:
aborting the whole sweep on one busy campaign would "500 a delete that has
already happened".

The all-or-nothing alternative — `hold_all` before the delete — was specified in
revision 3 and is rejected here. `hold_all` raises `CampaignBusy` on the first
lock it misses (`locks.py:777`), and an ordinary hold in this store is "a
minutes-long absorb", so deleting a picture from a gallery would fail routinely
in any world with several campaigns, for a reason the user can neither see nor
act on.

Best-effort is only acceptable **because a skipped campaign is recoverable**:
`list_hidden(cid)` and a Hidden section in the campaign's library UI make every
tombstone visible with a Restore beside it. A stale tombstone is then a row the
user can clear, not permanent invisible blindness — which is what turned this
from an all-or-nothing operation into a cheap one. The two halves are one
decision and neither works without the other.

Dropping a tombstone needs a public door: `_drop_deleted` (`:142`) is private and
`forget_world_record` is record-shaped, so `overlay` grows
`drop_library_tombstone(cid, name)` (under the lock) for both `restore_image`
and the sweep.

### The guard gap, closed rather than deferred

`test_overlay_guard.py` flags "a campaign root meeting an inheritable *kind*",
and the library is not a kind — so nothing would catch a future read of
`campaign_root(cid) / "assets" / "images"` that forgets to read through, which is
this codebase's most repeated bug class, in a design whose central invariant is
"all library resolution lives in `campaign_images`".

Revision 3 deferred this and mis-costed it. The roster is
`INHERITED_SEGMENTS = frozenset(overlay.INHERITED_KINDS + overlay.INHERITED_FILES)`
(`test_overlay_guard.py:159`), derived from overlay's own constants — so adding
`assets` there would change overlay *semantics*, not just the guard. The real
cost is a **test-local** extension list beside `INHERITED_SEGMENTS`, plus
`# overlay-ok:` markers on the two call sites that legitimately build that path
(`covers.py:117`, `campaign_images.images_dir`), plus raising the marker cap
from 4 (`:476`).

It is in scope. A prose claim is not an invariant in a codebase whose culture is
AST-enforced invariants, and the cap raise is the argument this section exists to
make.

## Routes

World side, mirroring `routes/campaigns.py`'s cover and library blocks:

```
GET    /api/worlds/{wid}/cover
PUT    /api/worlds/{wid}/cover
DELETE /api/worlds/{wid}/cover
GET    /api/worlds/{wid}/images
GET    /api/worlds/{wid}/images/{name}
PUT    /api/worlds/{wid}/images/{name}
DELETE /api/worlds/{wid}/images/{name}          # sweeps dependent campaigns
PUT    /api/worlds/{wid}/images/{name}/description
POST   /api/worlds/{wid}/images/{name}/description/draft   -> 202
```

The draft route carries **no `@computes_only`**, matching the three world
description-draft routes that already exist (`routes/worlds.py:583`,
`routes/entities.py:463`, `routes/characters.py:1055`). The marker is resolved
from a `cid` path parameter (`main.py:286`) and its own docstring says it marks
"a campaign-scoped POST" (`routes/common.py:1296`) — on a world route it would be
inert, and an inert decorator in a spec is a false claim. The rest of the draft
contract holds: `runs.world_subject` and all four `("world", wid)` run routes
exist (`routes/runs.py:1161,1252`).

Upload keeps both size checks and their order: `UploadFile.size` **before**
`read()` (the allocation `MAX_BYTES` bounds, and the one that OOMs Chaquopy),
then `validate_size` on the bytes actually received, because `size` is Optional
in the ASGI contract. The stored extension comes from the bytes
(`routes/common._upload_image_ext`, #321), never the filename. `DELETE` is
deliberately *not* gated by `addressable`, matching
`delete_campaign_library_image`: a stray a sync client dropped must have a way
out of the app. Note that it therefore builds a `deleted.json` ref from a raw
path parameter — `assets`' own name rules still apply to the unlink, and the ref
is only ever compared, never resolved.

**These live in a new `routes/world_images.py`, included after `characters`.**
Not in `routes/worlds.py`: `routes/__init__.py` includes `worlds` *before*
`characters`, and `characters` owns `/worlds/{wid}/images/undescribed`, which
`/worlds/{wid}/images/{name}` generalizes — so any other order fails
`test_no_route_is_shadowed_by_an_earlier_one`. The new module's inclusion is
itself covered by `test_every_domain_router_is_composed`
(`test_route_order.py:217`).

The description-draft route needs a **`CROSSING_PAIRS` entry**: it crosses
`POST /api/worlds/{wid}/{kind}/instantiate/{mid}/{content_id}`
(`routes/worlds.py:339`) at eight segments with neither pattern generalizing the
other, which `test_route_order.py:173` fails on unless the pair is pinned. The
campaign mirror is already pinned (`:102-103`) with the reasoning to copy.

**`GET /worlds/{wid}/images/undescribed` changes too**, and not only in name: its
body (`routes/characters.py:639`) gains the world library's rows. Adding the
library to the backlog's *count* without adding it to the *queue* would produce a
badge that can never be cleared, which is what `_DESCRIBE_BASES`' "it must stay
that list" comment exists to prevent. That route stays where it is — it must be
registered before `{name}` — so `routes/characters.py` gains a `world_images`
import.

Campaign side, unchanged in shape and changed in resolution:

- `GET /campaigns/{cid}/images` lists inherited images too, each row carrying
  `inherited`; a `hidden` list rides along for the Hidden section.
- `GET|PUT|DELETE /campaigns/{cid}/images/{name}` resolve, refuse and tombstone
  per the table above.
- `POST /campaigns/{cid}/images/{name}/restore` drops a tombstone.
- `PUT .../description` accepts **campaign-owned names only**. An inherited
  image is described in the world's queue, where describing it once serves every
  campaign — the split `GET /campaigns/{cid}/images/undescribed` already
  documents. Dropping shadowing is what makes this simply true.
- `GET /campaigns/{cid}/images/undescribed` filters to campaign-owned library
  images (`own_undescribed`).
- `GET /campaigns/{cid}/gallery` and `GET /worlds/{wid}/gallery` each gain the
  library as a base, with `id` and `vid` empty. The world gallery emits
  `kind: "world"`. **The campaign gallery emits `kind: "campaign"` for every
  library row, inherited ones included** — that route requires a campaign-scoped
  URL on every row it returns (`routes/characters.py:801`), and the kind names
  the URL scope. Origin rides on `record_name` instead ("World library" when
  inherited, "Campaign library" when the campaign's own).

  `kind: "campaign"` is already on the wire in the *describe backlog*
  (`DescribeQueue.tsx:71`) but **not** in either gallery, and
  `ImagesView.tsx:10-18`'s `KIND_LABELS`/`KINDS` is a fixed eight-kind map the
  rail iterates at `:322`. Both new kinds must be added there or they get no
  filter row (the `??` at `:61` prevents a crash, not an omission); the rail's
  `if (n === 0) return null` at `:324` means neither costs a dead row.

**The world cover token is derived in `routes/worlds.py`, not in the store.**
`GET /worlds` and `GET /worlds/{wid}` (`routes/worlds.py:65,81`) are pure
pass-throughs today; the campaigns precedent adds `cover` in the *route*
(`routes/campaigns.py:178,567`) and `store/campaigns/read.py` has no cover at
all, for the stated reason that nothing about a cover is written into the meta
file. Following it keeps `store.worlds.list_worlds` free of a `covers` import and
a per-world `stat` that `todo.ctx.worlds()` and `shell.py` would otherwise pay —
and it means **`frozen_campaign/snapshot.json` does not move**, since `sweep.py`
calls the store functions, not the routes.

## Everything downstream

**Narrator art (`store/context/art.py`).** `_library_candidates` reads
`campaign_images.list_images` and `read_descriptions`; `_resolved`'s library
branch calls the read-through `image_path`; `handle_for`, `parse_handle`,
`LIBRARY` and `url_for` are untouched. Its docstring's cost note must be
corrected rather than left: it already says the library half "has no record to be
in scope through, so it is included whole", and names that half as where a limit
would go if one is ever needed. The pool now includes the world's library too,
which makes that half bigger by exactly the number of *described* world images.
The note gets the truth; no limit is added, because a limit nobody has hit is a
threshold invented without evidence.

**The describe badge — three call sites, needing two different faces.**
`_DESCRIBE_BASES` (`routes/todo.py:366`) is consumed at `routes/todo.py:354`
(the count), `routes/shell.py:130` (the rail badge) and **`routes/todo.py:492`**
— `_has_world_describe`, which uses `image_descriptions.has_undescribed`, a
short-circuiting *presence probe*, because the `_CHEAP` roster (`todo.py:500`)
exists for "chores whose COUNT costs far more than their presence". So the
library needs `undescribed_count` for the first two and `has_undescribed` for
the third; giving the probe a count would defeat the roster it belongs to.

Miss the third and the bug survives one level up: a world whose only undescribed
art is library art gets a fixed count and a fixed badge, while the probe returns
False, the chore never renders, and nobody is told the queue has rows. Two
consequences to handle rather than discover: `_DESCRIBE_BASES`' comment stops
being true once the world backlog holds a non-base, so it gets restated; and
`_chore_world_describe`'s `fix_label` is "The cast" (`todo.py:381`), which is
wrong for a library-only backlog — it becomes "Images", the tab both kinds of art
are now reached through.

**Export (`store/export.py`).** Three edits, and they are the risky ones:

- `_IMG_URL` gains `/api/worlds/<wid>/images/<name>`, replacing the comment that
  reserves it as never-written. The regex header states the stakes: "A URL shape
  missing from here is not a rendering bug — it is a book shipped with that image
  silently degraded to its alt text."
- It must be a **second named group**, not a widened prefix on the existing one.
  Today `(?P<lib>images)` captures the literal segment rather than the scope
  (`export.py:46`) and `_resolve_image` branches on `if m["lib"]` alone (`:157`),
  so a shared group would leave the resolver unable to tell the two apart.
- Both library branches resolve through the campaign's view, so a **tombstone is
  honoured** and the image degrades to alt text. A world-shaped URL in an
  inherited lore body is still being exported *for that campaign*; resolving it
  world-side would pack the one picture the reader hid. This is the rule
  `_resolve_image` already applies to world-shaped *record* URLs (`:156-181`), not
  a new one.

**World bundles.** `store/world_bundle.py` zips the world directory whole and
enumerates nothing (`rglob`), so `assets/` rides along on export and import with
no code change, as it does through `fork_world`'s `copytree`
(`worlds/lifecycle.py:144`). Import already rewrites `/api/worlds/{old}/` to
`/api/worlds/{new}/` across `.md` and `.json`, so a world library URL embedded in
a lore body survives a re-id.

**Campaign fork.** `store/fork.py` copies a campaign's own tree; the world is
shared, so a fork inherits the same library and the same tombstones as its
source. Nothing to change, one test to add.

**No migration.** Existing stores have no `worlds/<wid>/assets/` and existing
campaigns have no library tombstones. A missing directory reads as empty through
`assets.list_in`, and an absent tombstone set is already `deleted`'s default, so
nothing happens on first read. Said explicitly because "does anything need to
happen on upgrade" is the first question an implementer asks.

**Stale declarations to update in the same change**, since
`test_modules_declared_outside_are_really_outside`'s docstring says a stale
declaration is worse than none: `locks.OUTSIDE_DOMAIN`'s entry for
`store.campaigns.lifecycle` asserts `ensure_campaign_slim` "is unlocked" and that
fixing it "needs its own review" — this change is that review, for that function.
And `campaign_images`' own module docstring asserts "**Not under the overlay** …
`store/overlay.py` does not know about them", which stops being true.

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
  strip is `"gallery" | "queue"` (`:20`). So the Images tab gains a **third tab,
  "World art"**, holding the `CoverPanel` and the library editor; the gallery
  keeps its contract and merely gains the library as a base it *reports* on.
- **That tab renders whether or not `forCampaign` is set.** `shell/rail.ts:280`
  appends `&for=<cid>` to the Images row's target whenever a campaign is open,
  and `WorldView.tsx:515` is the only mount, so hiding the tab under `for=`
  would make the feature's only editing surface unreachable by the app's own
  navigation for as long as a campaign is open. The controls are labelled as the
  world's, which is what they are; the reader is on `/worlds/<wid>`, not inside
  the campaign.
- `components/DescribeQueue.tsx` handles `kind === "world"` alongside its
  existing `kind === "campaign"` branch (draft and save go to the world routes).
- `components/PostImagePicker.tsx` marks inherited images; "remove from this
  campaign" writes a tombstone and is a different sentence from deleting the
  campaign's own. Hidden images are listed with a Restore.
- `routes/WorldsView.tsx` cards render the cover with the `broken`-by-version
  fallback `CampaignsView.tsx:83` documents, and `routes/WorldView.tsx` shows it
  in the header. CSS mirrors `.shelf-cover`.
- `api/client.ts` gains the world cover and world library calls, and
  `KIND_LABELS`/`KINDS` in `ImagesView.tsx` gain both new kinds. `CampaignImage`
  gains `inherited` — it is declared in `api/types.ts:847` and re-exported
  through `client.ts:15`, so the type edit goes in `types.ts`.

## Edge cases, stated so they are not invented

- **Ordering.** `list_images(cid)` sorts by name across both halves, as
  `overlay.list_images` does; today's `campaign_images.list_images` does not sort
  and gains it.
- **`image_version(cid, name)` for an inherited image** is the world file's
  `mtime-size` (`assets.image_version`), which is what makes the `?v=` immutable
  URL correct for it.
- **A logical name is one image across extensions.** The collision, the
  tombstone and the newest-wins resolution are all by logical name, never by
  filename — `map.png` campaign-side and `map.webp` world-side are the same
  name. `lifecycle.py:333` records shipping the other rule as a real bug.
- **A campaign whose world root is missing** reads as an empty world half rather
  than raising; `ensure_campaign_slim:166` already guards for that shape.
- **Imports** bind the submodule, per `test_import_guard.py` and CLAUDE.md:
  `from .worlds import paths as worlds_paths`, never
  `from .worlds.paths import world_exists`.

## Testing

Backend (pytest, `GRIMOIRE_HOME` per test as always):

- `image_library`: addressable/reserved/unaddressable names, the two size
  checks, newest-wins resolution, a `notes.txt` neither served nor deleted.
- `world_images` and the world cover: put/list/serve/replace/delete, the
  extension named from bytes and not the filename, unknown-world put creates
  nothing.
- Read-through: inherited list and serve; `inherited` on the rows; a campaign
  upload under a world-held name is refused; the accidental collision (world
  adds a name the campaign already holds) serves the campaign's.
- Hiding: tombstone hides and 404s; **a campaign image uploaded under a
  previously-tombstoned name is listed AND served** (the revision-3 blocker);
  `restore_image` brings an inherited image back; `list_hidden` reports it.
- The sweep: a world-side delete clears dependent campaigns' refs; a busy
  campaign is skipped and logged rather than failing the delete, and its stale
  tombstone is still visible through `list_hidden` and clearable by Restore.
- `add_deleted` under the lock: two concurrent tombstone writes both survive;
  `ensure_campaign_slim`'s tombstone set lands whole under one hold.
- Descriptions: world descriptions reach a campaign; campaign-side description
  of an inherited name is refused; campaign backlog excludes inherited; the
  world backlog, the count and the `has_undescribed` probe all include the
  library.
- Route order: `/worlds/{wid}/images/undescribed` still answers the backlog, and
  the new `CROSSING_PAIRS` entry is present.
- Export: a post carrying an inherited library URL packs the world's bytes; a
  world-shaped library URL in an inherited lore body packs too; **both shapes**
  degrade to alt text when the campaign has hidden the image.
- Art pool: a described world image is offered to a campaign and resolves; a
  hidden one is not offered.
- Bundle and fork: cover and library survive export then import under a new id,
  with the URL rewrite. `world_fixtures.tree()` diffs the whole tree, so
  `test_world_bundle` and `test_world_fork` cover `assets/` automatically —
  **but only once `SEEDED_FILES` (`world_fixtures.py:37`) actually writes one**,
  which today it does not. Seeding a world cover and a described library image is
  what turns those two existing tests from vacuously passing to load-bearing.
- Unknown-id sweeps: `_actor_image_write_routes` (`test_routes.py:858`) requires
  a record segment before `/images`, and `_campaign_library_write_routes`
  (`:1088`) is `^/api/campaigns/` only, so neither reaches the new world routes;
  `test_path_guard_store.py`'s generic `_id_routes()` sweep does. A
  `_world_library_write_routes` sibling is part of the work, for the reason both
  docstrings give: catching "route number five, added later by someone who did
  not read this file".
- Guards and frozen rosters, none of them optional:
  - `backend/tests/store_api_baseline.json` compares **both** `store.__all__` and
    the public names in `dir(store)`, and that second list already carries
    modules nothing re-exports (`paths`, `statcache`, `vectors`) — so it fails
    the moment anything imports `grimoire.store.world_images` or
    `image_library`, re-export or not. Regenerating it is a reviewed act in the
    same commit, and declining to re-export does not avoid it.
  - `test_lock_domain_guard.py`: `covers` and `campaign_images` must still be
    surveyed after the seam moves (the reason the write calls stay put).
  - `test_overlay_guard.py`: the new test-local segment list, two markers, and
    the cap raise from 4.
  - Other marker budgets, counted in `backend/src`: `lock-domain-ok` 2/2
    (`test_lock_domain_guard.py:2378`), `routing-ok` 3/3
    (`test_routing_guard.py:216`), `atomic-ok` 2/3 (`test_atomic_guard.py:172`).
    Any further exemption means arguing for a cap raise, not bumping one.
  - `make baseline` if a lint count moves, committed with the fix.

Frontend (vitest, run from `frontend/`): `CoverPanel` in both scopes; the World
art tab uploads and deletes, and renders under `?for=`; `DescribeQueue` on a
world library image; `PostImagePicker` marks inherited, offers the
campaign-scoped URL, and restores a hidden image; a world card renders its cover
and falls back when it fails to load.

## Rejected

- **Shadowing** — see *Inheritance is read-through plus hiding*. Cut after
  review round three; it bought parity with records and cost the most
  defect-prone surface in the design.
- **A campaign falling back to its world's cover.** It would push a world read
  into the campaigns list, the hub and the EPUB cover path, and turn "remove
  cover" into "revert to the world's" — a different verb wearing the same button.
- **Posts carrying `/api/worlds/{wid}/images/{name}` directly.** This is exactly
  what makes greeting art unofferable in the picker today.
- **An all-or-nothing sweep under `hold_all`.** Rejected in revision 4: it fails
  a routine delete whenever any campaign in the world is mid-turn.
- **Version-keyed tombstones** (hide `name@version`, so a re-upload is simply a
  different image and no sweep is needed). Genuinely elegant, and rejected
  because the token is `mtime-size`: a sync client rewriting the file would
  silently un-hide a picture the user hid, on exactly the multi-device stores
  this feature is most useful on.
- **A world lock.** Out of scope, and inventing one for a single directory while
  `focus.json`, `subjects.json` and the world-side description write all still
  race would imply a guarantee the world root does not make.
