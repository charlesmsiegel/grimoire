# Campaign Creation Wizard — Design

**Date:** 2026-06-29
**Status:** Approved, ready for implementation plan

## Problem

Campaign creation today is a single inline form on the Campaigns list: a name
input plus a world dropdown → `POST /api/campaigns` → navigate. There is no
guidance toward the things a new campaign actually needs to be playable — a
player character, and the places that character starts among. Creating a
campaign and then hunting through the Cast panel to seat a PC is a poor first
run.

Campaign creation should be a proper multi-step wizard that **at least** creates
a player character and asks for locations relevant to that character.

## Model context (why the decisions below)

- A campaign is **created from a world** by copy-on-create: world `locations` and
  `lore` are deep-copied into the campaign and recorded in `sync.md`; characters
  and PCs are copied lazily, only when they "appear" in a scene.
- The intended grain is **world = general/shared assets, campaign = overlays and
  additions on top.** The wizard's PC and locations are campaign overlays.
- PC store functions (`pcs.create_pc(root, …)`, `pcs.list_pcs(root)`, etc.) are
  already **root-generic**, so they work against a campaign root unchanged.
- Campaign-scoped location (entity) creation already exists via the
  `EntityScope = "campaign"` API path — no backend change needed for locations.
- `player_tags()` already reads PCs from the **campaign** root (`croot`), and
  greeting availability flows from player tags. So a campaign-local PC with tags
  unlocks greetings correctly with no extra wiring.

## Decisions

- **PC and locations are campaign-local** (overlays), not written to the world.
- **End state:** the wizard creates the campaign, the campaign-local PC, a first
  scene with the PC seated as the player, the locations, and then offers an
  **opener** step (start from a greeting or generate an opener) so play can begin.
- **PC depth:** full persona (name, pronouns, summary, description) **plus tags**.
- **Wizard shape:** a dedicated multi-step route, `/campaigns/new`.

## Flow

Replace the inline name+world picker on the Campaigns list with a
**`+ New campaign`** button routing to **`/campaigns/new`**. The campaigns list,
rename, and delete stay as they are. The existing "create a world first" guard
stays (button disabled with a hint when no worlds exist).

Four steps:

1. **Backdrop** — campaign name (required) + world dropdown (required). The world
   is the source of NPC characters and lore.
2. **Your character** — full PC persona: name (required), pronouns, summary,
   description, and tags. Tag input offers the world's existing tags as
   suggestions and allows free additions.
3. **Locations** — a repeatable "relevant to your character" list; each row is
   name + description (markdown body) + optional keys. Add/remove rows.
   Skippable (zero locations is valid).
4. **Opening** — start the first scene from an available greeting, or generate an
   opener, or skip. Operates on the now-live campaign and scene.

```
┌─ /campaigns/new ───────────────────────────────┐
│  ● Backdrop  ─ ○ Character ─ ○ Locations ─ ○ Open │
│                                                  │
│  [ step body ]                                   │
│                                                  │
│  [ Back ]                      [ Skip ] [ Next ] │
└──────────────────────────────────────────────────┘
```

## When the campaign is committed

Steps 1–3 are pure local form state — nothing is written until the user clicks
**Create campaign** at the bottom of step 3. That action commits everything in
order, then advances to step 4:

1. `POST /api/campaigns {name, world}` → `cid` (existing; copies world
   locations/lore into the campaign).
2. `POST /api/campaigns/{cid}/pcs {name, tags, persona}` → `{pc, version}`
   (**new endpoint** — campaign-local PC overlay).
3. `POST /api/campaigns/{cid}/scenes` → `sid` (existing; the first scene).
4. `POST /api/campaigns/{cid}/scenes/{sid}/cast {kind:"pcs", id:pc, version}` →
   seats the PC as the player (existing route; needs the local-PC fix below).
5. For each location: `POST /api/campaigns/{cid}/locations {name, body, keys}`
   (existing campaign-scoped entity create — no backend change).

Because the campaign is live once step 4 begins, **step 4 has only Finish/Skip
(no Back)** to avoid double-commit logic. Finish/Skip navigates to
`/campaigns/{cid}`.

## Backend changes

Campaign-local PCs are the only real new plumbing.

- **New routes** (declared *before* the generic `/campaigns/{cid}/{kind}`
  catch-all so the literal path wins):
  - `POST /api/campaigns/{cid}/pcs` → `pcs.create_pc(campaign_root(cid), …)`,
    returns `{pc, version}`. Same request body shape as the world PC create.
  - `GET /api/campaigns/{cid}/pcs` → `pcs.list_pcs(croot)`, so a campaign-local
    PC can later be re-cast into other scenes via the Cast panel.
- **`appearances.appear()` local fallback:** today it reads the actor's base hash
  from the *world* and copies world→campaign, raising `AppearError` if the world
  lacks the actor. Add: if the world has no such actor but the campaign already
  has it (a local overlay), record the appearance with an empty world-base and
  skip the copy. `sync.py`'s `_actor_incoming` already short-circuits when the
  world hash is `None`, so local PCs correctly never surface as incoming sync
  changes — **no sync.py change needed.**
- **`post_scene_cast` version resolution:** when no explicit version is supplied,
  fall back to reading the PC from the campaign root if it is not found in the
  world. (The wizard passes the version explicitly, so this is robustness for
  later re-casting.)

`CastPanel` merges world PCs + campaign-local PCs in its "Add to scene" PC
dropdown so a campaign-local PC is not a dead-end after the first scene.

## Frontend changes

- New route component `CampaignWizard.tsx` at `/campaigns/new`, holding a
  `step` state (1–4) and the accumulated draft: `{ name, world, persona, tags,
  locations[] }`. Each step is a small presentational sub-section within the
  file; a shared footer holds Back / Skip / Next (and Create campaign on step 3,
  Finish on step 4).
- `CampaignsView.tsx`: replace the inline picker with a `+ New campaign` button
  (disabled + hint when no worlds exist); keep the list/rename/delete.
- New API client methods: `createCampaignPC(cid, body)` and
  `listCampaignPCs(cid)`.
- Step 4 reuses existing `availableGreetings`, `startFromGreeting`, and `opener`.

## Error handling

- Per-step validation gates Next: name required on steps 1 and 2; world required
  on step 1.
- The commit (end of step 3) is sequential; if a call fails, surface the error in
  a banner and stay on step 3. The campaign may be partially created — acceptable
  since it is recoverable from the campaign page; **no rollback is attempted.**
- Step 4 reuses the existing opener/greeting error banners.

## Testing

- **Backend (pytest):**
  - campaign-local PC create + list endpoints;
  - seating a campaign-local PC via the cast route (exercises the `appear()`
    local fallback and `post_scene_cast` campaign version resolution);
  - `sync.incoming` ignores a campaign-local PC (no spurious incoming change).
- **Frontend (vitest):**
  - step navigation + per-step validation;
  - **Create campaign** fires the commit sequence in order against a mocked api;
  - skipping the locations step and skipping the opener step;
  - `tsc -b` clean.

## Out of scope

- Editing a campaign-local PC through the existing world PC editor (the world
  editor remains world-scoped). The wizard creates and seats; later edits to a
  campaign-local PC are a follow-up.
- Rollback of a partially-created campaign on commit failure.
- Selecting an existing world PC inside the wizard (create-only by design; world
  PCs remain available via the Cast panel).
