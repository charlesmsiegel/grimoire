# Campaign-scoped character avatar/gallery images — design

**Date:** 2026-07-10
**Status:** approved, ready for planning
**Scope:** backend (`routes.py`) + frontend (`api/client.ts`, `CharacterEditor.tsx`, `CharacterEditor.test.tsx`)

## Problem

A character with no avatar has no way to get one when viewed in **campaign**
scope — the "no avatar" placeholder renders with no upload control at all,
even though every other character field (name, description, ...) is editable
there. The same gap applies to the gallery shelf ("+ add", "Set as avatar"
promote) and the avatar crop/focus control.

This isn't a UI oversight: it mirrors a real, currently-missing backend
capability. Every character image mutation (`putImage`, `deleteImage`,
`promoteImage`, `setAvatarFocus`, `copyGreetingImage`) is hardcoded to a bare
world id and only has a world-scoped backend route
(`/worlds/{wid}/characters/{cid}/versions/{vid}/images/...`). There is no
campaign-scoped counterpart — only a single campaign **GET** route exists
today (`routes.py:1635`) for display.

By contrast, every other character mutation (`updateVersion`, etc.) already
has a campaign-scoped route, because campaigns own a full physical copy of
their characters: `create_campaign` (`store/campaigns.py`) `shutil.copytree`s
each character's whole directory — cards *and* asset images — into
`~/.grimoire/campaigns/{cid}/...` at creation time. A campaign-scoped
`PUT .../versions/{vid}` already writes only to that private copy, leaving
the world's shared file untouched. Locations and lore ("entity") images
already have this exact world+campaign route pairing
(`_entity_image_put`/`_entity_image_promote`, `routes.py:1128-1179`,
`2124-2149`). Characters are the one record type where the campaign-scoped
image routes were never added — that's the actual gap.

## Goals

1. Add campaign-scoped backend routes for every character image mutation,
   mirroring the existing world-scoped ones and reusing the same
   `store.assets` / `store.image_subjects` functions (already root-generic —
   no store-layer changes needed).
2. Make the frontend API client and `CharacterEditor.tsx` scope-aware for
   these calls, the same way `putEntityImage`/`deleteEntityImage`/
   `promoteEntityImage` already are for locations/lore.
3. Remove the `worldScope`-only gating on the avatar-block (Upload / Replace /
   Remove), the gallery shelf ("+ add", "Set as avatar" promote), and the
   avatar crop/focus-adjust control, so a campaign-scoped character —
   especially one with no avatar yet — has the same image-assignment
   controls a world-scoped one does.

## Non-goals

- PCs have no avatar/image support at all today (no routes in either scope).
  Tracked separately as
  [issue #948](https://github.com/charlesmsiegel/grimoire/issues/948), not
  part of this change.
- `localizeControls` (markdown image localization) and version-management
  actions (Import version, Delete, Download version from URL) stay
  world-only by design — unrelated to avatar/gallery assignment.

## Backend changes (`backend/src/grimoire/routes.py`)

Add four campaign-scoped routes, each a thin mirror of its world-scoped
counterpart with `_campaign_root_or_404(cid)` in place of `_world_root_or_404(wid)`:

- `PUT /campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}`
  → `store.assets.put_image`
- `DELETE /campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}`
  → `store.assets.delete_image`
- `POST /campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}/promote`
  → `store.assets.promote_image`
- `PUT /campaigns/{cid}/characters/{char}/versions/{vid}/images/avatar/focus`
  → `store.assets.write_focus`
- `POST /campaigns/{cid}/characters/{char}/versions/{vid}/images/copy-from-greeting`
  → `store.image_subjects.copy_to_character`

No changes to `store/assets.py` or `store/image_subjects.py`: both already
take a generic `root`, proven by the identical reuse for entity images.

## Frontend changes

1. `frontend/src/api/client.ts`: change `putImage`, `deleteImage`,
   `promoteImage`, `setAvatarFocus`, `copyGreetingImage` to accept an
   `EntityScope` (routing through `entityBase(scope)`) instead of a bare
   `wid` — same shape as `putEntityImage` / `deleteEntityImage` /
   `promoteEntityImage`.
2. `frontend/src/components/CharacterEditor.tsx`: update the call sites
   (`onAvatar`, `removeAvatar`, `promote`, `saveFocus`, `copyFromGreeting`,
   `onShelfAdd`) to pass `scope` instead of `wid`.
3. Remove the `worldScope &&` gating around:
   - the avatar-block Upload/Replace/Remove actions (edit mode)
   - the gallery "+ add" button and per-image "Set as avatar" promote button
     (view mode)
   - the `AvatarFocusPicker` crop control (open button + picker itself)

All other `worldScope`-gated controls (version import/delete/download-from-URL,
"+ New character", localize) are untouched.

## Testing

- Backend: new tests for the four campaign routes, covering the isolation
  guarantee — a campaign-scoped image write lands under
  `campaigns/{cid}/characters/...` and leaves the world's copy under
  `worlds/{wid}/characters/...` unchanged.
- Frontend: `CharacterEditor.test.tsx`'s
  `"campaign scope: the avatar crop control is absent (world-side mutation)"`
  test asserts an invariant this change intentionally reverses — rewrite it
  to assert the crop control, avatar-block actions, and gallery shelf
  controls are present in campaign scope and call the scope-aware endpoints.
  Add coverage for the new "+ add"/promote/upload paths in campaign scope.
