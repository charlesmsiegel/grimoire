# Frontend — Deferred UI (playtest slice + greetings + lorebook)

> Builds the world-authoring and play-setup UI that every backend spec since worlds/campaigns
> deferred. Delivers a single-world **WorldView** hub (characters, PCs, tags, locations/lore with
> `keys`, greetings, lorebook import) and extends the play space with a **cast panel**,
> **start-from-greeting**, and **generate-opener**. Honors the existing "occult grimoire" visual
> identity and CSS-token system; **defers** the sync/IncomingReview UI, suggested-cast strip, world
> push panel, and the plot-map graph editor.

**Status:** Design — not yet implemented
**Date:** 2026-06-22
**Branch:** `frontend-deferred-ui` (off `lorebook-import`)
**Builds on:** the worlds/campaigns phase-2 frontend (App shell, theme tokens, `api/client.ts`,
`EditableRow`, `CampaignView`) and the backends from
[`2026-06-21-character-cards-design.md`](2026-06-21-character-cards-design.md),
[`2026-06-21-pcs-tags-actors-design.md`](2026-06-21-pcs-tags-actors-design.md),
[`2026-06-22-context-builder-design.md`](2026-06-22-context-builder-design.md),
[`2026-06-22-greetings-plotmaps-design.md`](2026-06-22-greetings-plotmaps-design.md),
[`2026-06-22-lorebook-import-design.md`](2026-06-22-lorebook-import-design.md).
**Scope confirmed with user:** playtest slice + 2b/2c surfaces; extend the existing aesthetic.

## Scope

**In:** WorldView route + section tabs; generic entity editor (locations/lore incl. `keys`); world
tag vocabulary editor; PC editor (persona + versions + tags); character editor (V3 card-field form +
versions + set-default + JSON import); greeting editor (form + import-from-character + leads_to/
excludes as id multiselects); lorebook import (upload → parse → review/route → commit); CampaignView
cast panel (add character/PC with kind+role+version) + start-from-greeting + generate-opener.

**Out (deferred, unchanged):** IncomingReview / sync-accept-reject UI + conflict diff; suggested-cast
strip + dismiss; world push panel (pending counts); the plot-map **graph** editor (edges are edited
as plain id lists here); character PNG/CHARX export UI; tags on characters.

## Visual direction — extend "occult grimoire"

Reuse the established tokens (`--bg/--surface/--fg/--muted/--accent/--radius`, the serif display/body
faces) and the existing component vocabulary (`.view`, `.picker`, `.list`, `.row`, `.banner`,
`.primary`). New shared classes added to `index.css` (token-only, no hardcoded colors):

- `.tabs` / `.tab` (`.tab.active`) — the WorldView section strip (a hairline-underlined row, accent
  on the active tab — consistent with the existing `.row.active` treatment).
- `.field` (label + control stack), `.form` (vertical group), `.form-actions` (right-aligned button
  row), `.field textarea` (monospace-free serif, `--surface` bg) — mirrors `.config` styling.
- `.chip` / `.chip.on` — toggle for tag assignment and category routing (accent border when on).
- `.editor` (two-pane: a `.editor-list` rail + a `.editor-body` form) for character/PC/greeting
  editors — same rail metrics as `.sidebar`.
- `.table` (hairline rows) — the lorebook review list.

One signature touch consistent with the grimoire framing: section tabs and editor rails use the
display serif and the accent sparingly; the bulk stays quiet parchment-on-ink. No new fonts, no new
palette — the distinctiveness is the existing identity applied consistently to authoring surfaces.

## Architecture

```
frontend/src/
  api/client.ts          # EXTENDED — entities(+keys), characters(+versions/import/export),
                         #   pcs, tags, greetings, cast/appearances/available/start/opener, lorebook
  App.tsx                # EXTENDED — add /worlds/:wid route
  routes/
    WorldsView.tsx       # EXTENDED — rows link to /worlds/:wid
    WorldView.tsx        # NEW — single-world hub: title + section tabs + active panel
    CampaignView.tsx     # EXTENDED — render <CastPanel/> + play controls
  components/
    EntityEditor.tsx     # NEW — locations|lore list + name/body/keys form (generic by kind)
    TagEditor.tsx        # NEW — world tag vocabulary CRUD
    PCEditor.tsx         # NEW — PC list + persona form + versions + tag chips
    CharacterEditor.tsx  # NEW — character list + V3 card-field form + versions + import
    GreetingEditor.tsx   # NEW — greeting list + form + import-from-character + edges multiselect
    LorebookImport.tsx   # NEW — upload → parse → review table (route each) → commit
    CastPanel.tsx        # NEW — scene roster + add-actor + start-from-greeting + opener
    Field.tsx            # NEW — tiny labeled-input helper used across forms
  index.css              # EXTENDED — .tabs/.field/.form/.chip/.editor/.table (tokens only)
```

Each editor is a **self-contained component** that takes the `wid` (and loads its own data via
`api`), so WorldView is a thin tab host. Components communicate only through the typed `api` client.

## API client additions (typed)

New types: `EntitySummary {id,name,keys?}`, `EntityDetail {meta:{id,name,keys?}, body}`,
`CardData` (the V3 `data` subset: name/description/personality/scenario/first_mes/mes_example/
system_prompt/post_history_instructions/alternate_greetings[]), `Card {spec,spec_version,data}`,
`CharacterSummary {id,name,default_version,versions:{id,name}[]}`, `CharacterDetail {meta,versions:{id,name,card}[]}`,
`Persona {name,pronouns,summary,description}`, `PCSummary`/`PCDetail`, `Greeting {id,name,character,version,requires_tags[],predecessor_join}`,
`Edges {leads_to[],excludes[]}`, `Availability {id,name,available,reasons[]}`, `Actor {kind,id,role}`,
`RosterEntry {kind,id,version,role,scenes[]}`, `LoreEntryDraft {name,keys[],body,category}`.

New methods (paths already exist on the backend):

```
// entities (generic kind: 'locations'|'lore'; world or campaign scope via base path)
listEntities(scope, kind), createEntity(scope, kind, {name,body,keys}),
readEntity(scope, kind, id), updateEntity(scope, kind, id, {name?,body?,keys?}), deleteEntity(...)
// tags
listTags(wid), addTag(wid,name), renameTag(wid,tid,name), deleteTag(wid,tid)
// characters
listCharacters(wid), createCharacter(wid,{name,version_name?,card?}), readCharacter(wid,cid),
setDefaultVersion(wid,cid,vid), deleteCharacter(wid,cid),
createVersion(wid,cid,{name,card}), updateVersion(wid,cid,vid,card), deleteVersion(wid,cid,vid),
importCharacter(wid, file, format, into?)            // multipart
// pcs
listPCs(wid), createPC(wid,{name,tags?,persona?}), readPC(wid,pid),
updatePC(wid,pid,{default_version?,tags?}), deletePC(wid,pid),
createPCVersion(wid,pid,{name,persona}), updatePCVersion(wid,pid,vid,persona), deletePCVersion(...)
// greetings
listGreetings(wid), createGreeting(wid,body), readGreeting(wid,gid),
updateGreeting(wid,gid,patch), deleteGreeting(wid,gid),
setEdges(wid,gid,{leads_to?,excludes?}), importGreetings(wid,{character,version})
// campaign cast & play
listAppearances(cid), getCast(cid,sid), addToCast(cid,sid,{kind,id,version?,role?}),
availableGreetings(cid), startFromGreeting(cid,sid,greeting),
opener(cid,sid,prompt,onEvent)                       // SSE, reuses streamPost
// lorebook
lorebookParse(wid, file, format), lorebookImport(wid, entries)
```

`importCharacter` / `lorebookParse` use `FormData` (a new `requestForm` helper alongside `request`).

## Screen behaviors

- **WorldsView** rows: clicking the label navigates to `/worlds/:wid` (rename/delete stay on the row).
- **WorldView**: loads the world; renders the title and a `.tabs` strip (Characters · PCs · Tags ·
  Locations · Lore · Greetings · Import); the active tab renders its editor. Tab state is local
  (default Characters). A "‹ Worlds" back-link (mirrors CampaignView's back-link).
- **EntityEditor** (`kind`): list rows (EditableRow) + a form to create/edit `name`, `body`
  (textarea), and `keys` (comma string with a hint "comma-separated activation triggers; blank =
  always-on"). Edits save on a Save button; delete via row.
- **TagEditor**: a picker to add a tag + EditableRow list (rename/delete). Shows the id next to the
  display name.
- **PCEditor**: two-pane. Left rail lists PCs (+ New). Right: persona form (name, pronouns, summary,
  description textarea), a **version** selector (switch/create/delete, set-default), and **tag
  chips** from the world vocabulary (toggle on/off → `updatePC`).
- **CharacterEditor**: two-pane. Left rail lists characters (+ New, + Import JSON). Right: a version
  selector + a V3 **card-field form** (name, description, personality, scenario, first_mes,
  mes_example, system_prompt, post_history_instructions, alternate_greetings as one-per-line); Save
  writes the version; set-default control. Import posts a `.json` file → reloads.
- **GreetingEditor**: two-pane. Left rail lists greetings (+ New, + Import from character). Right:
  form (name, character `<select>`, version `<select>` from that character, body, requires_tags
  chips from vocab, predecessor_join select all|any) + an **edges** block: `leads_to` and `excludes`
  as multi-select chip lists over the *other* greetings. Save writes greeting + edges.
- **LorebookImport**: a file input + format select (`lorebook`/`json`/`png`/`charx`) → Parse →
  a `.table` of parsed entries, each row editable: name, keys, a category `<select>` (lore|locations),
  and a body preview → Import commits all → success line with created count. Parse writes nothing.
- **CampaignView / CastPanel**: a collapsible panel beside the transcript showing the scene cast
  (kind · name · role) from `getCast`, an **Add to scene** control (pick world character or PC →
  role player/npc for characters → version if multi → `addToCast`), and two play controls:
  - **Start from greeting**: lists `availableGreetings` (unavailable ones disabled, with reason
    tooltip); picking one calls `startFromGreeting`, then reloads the scene (the seeded first post
    appears). Disabled when the scene already has messages.
  - **Generate opener**: a prompt box → streams `opener` into a preview area (ephemeral); buttons to
    **Insert as first post** (only if scene empty — appends via the normal flow is out of scope, so
    instead: "Save as greeting" posts a new world greeting with the text) and **Dismiss**. v1 ships
    **Save as greeting** + copy-to-clipboard; inserting into the scene is deferred (no backend seed
    for arbitrary text beyond greetings).

## Error handling

- All `api` calls surface `ApiError.detail`; each editor shows a small inline `.banner` on failure
  and never leaves a half-applied optimistic state (reload list after mutations, mirroring
  WorldsView). Destructive actions (`delete*`) confirm via `window.confirm` (existing pattern).
- The opener/start controls respect the `keySet`/empty-scene constraints (disable + explain), and
  the opener stream reuses CampaignView's `runStream` error contract.

## Testing (vitest + Testing Library; mock `../api/client`)

Light component tests in the existing style (one `.test.tsx` per new component), asserting the
**wiring**, not styling:
- `client.test.ts`: new methods hit the right method+path+body (extend the existing client test).
- `WorldView`: renders tabs; switching tab renders the right editor (mock each editor's list call).
- `EntityEditor`: lists, creates with keys, edits, deletes (confirm).
- `TagEditor`: add/rename/delete.
- `PCEditor`: create PC, edit persona, toggle a tag chip → `updatePC`, add a version.
- `CharacterEditor`: create, edit card fields → `updateVersion`, set default, import file → `importCharacter`.
- `GreetingEditor`: create, set character/version, edit requires_tags + predecessor_join, set edges → `setEdges`, import-from-character.
- `LorebookImport`: parse renders rows; re-route a row's category; import posts the edited entries.
- `CastPanel`: renders cast; add-actor → `addToCast`; start-from-greeting (available vs disabled);
  opener streams into the preview and "Save as greeting" posts a greeting.

Keep the suite green (49 today) and add coverage with each component.

## Phasing (for the plan)

1. **API client + types** (`requestForm` helper) + client tests.
2. **WorldView shell** + route + WorldsView links + `index.css` additions + `Field` helper.
3. **EntityEditor** (locations/lore + keys).
4. **TagEditor**.
5. **PCEditor**.
6. **CharacterEditor** (+ import).
7. **GreetingEditor** (+ import-from-character + edges).
8. **LorebookImport**.
9. **CastPanel** + start-from-greeting + opener (CampaignView wiring).

## What's next (still deferred)

IncomingReview/sync UI + conflict diff, suggested-cast strip, world push panel, the plot-map graph
editor, character PNG/CHARX export, and richer opener→scene seeding.
