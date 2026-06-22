# Character Cards — Design

> Refines the part the worlds/campaigns spec deferred — the **internal schema of a
> character**. A character stops being generic markdown and becomes a **container of
> SillyTavern V3 cards** (one per *version*). Locations and lore are untouched. A campaign
> starts with **no** characters; each is introduced — **locked to one chosen version** — only
> when it explicitly appears in a scene, either by manual pick or by accepting a
> name-mention **suggestion**.

**Status:** Design — not yet implemented
**Date:** 2026-06-21
**Branch:** `character-cards` (off `worlds-campaigns-frontend`)
**Builds on:** [`2026-06-20-worlds-campaigns-design.md`](2026-06-20-worlds-campaigns-design.md)

## Purpose

The worlds/campaigns spec made entities generic markdown and explicitly deferred two things:
the internal schema of a character, and prompt injection. This spec delivers the **character
schema** and the **appearance model** that sits on top of it. It does *not* address prompt
injection (still deferred).

Three ideas drive it:

- **A character is a container of versions.** Most characters are a single card; some have
  multiple versions (e.g. `default`, `corrupted`, `young`). Each version is a full SillyTavern
  **V3** card.
- **Characters appear on use, locked to one version.** A campaign no longer copies characters
  on create. A character is introduced into the campaign the first time it explicitly appears
  in a scene, at which point the user picks a version; from then on the campaign tracks **only
  that one card**. The choice is permanent for that campaign.
- **Appearance has two paths into one operation.** Manual pick from the full world roster, and
  accepting a live **name-mention suggestion**. Both run the same `appear()` and both require
  version selection.

### Amendments to the worlds/campaigns spec

This spec changes two rules from its predecessor:

- Characters **no longer copy-on-create** into a campaign (locations and lore still do).
- The sync **"new"** status and deletion handling no longer apply to characters (they appear
  on use, not on create). Characters sync as **update / conflict / nothing** only.

Locations and lore are unchanged in every respect — plain markdown, copy-on-create, the
existing `sync.md` engine.

## Non-goals (this iteration)

- **No prompt injection.** Locked cards do not yet feed the LLM (still deferred to the context
  builder).
- **No version switching after lock.** Once a campaign locks a character to a version it stays
  there; a deliberate re-version action is a possible later feature, not built now.
- **No alias/nickname matching** in the suggestion scan — whole-word match on the character's
  name only. False positives are acceptable because every suggestion requires explicit confirm.
- **No transcript scanning** for suggestions — only the text of cards already in the scene. (A
  later spec could scan the running transcript.)
- **No sub-field card merge.** A character sync conflict is resolved whole-card (take-world /
  keep-mine), consistent with the predecessor spec.

## Storage — `~/.grimoire/`

### World: a character is a folder of versions

```
worlds/<wid>/
  characters/
    <char-id>/
      character.md          # frontmatter: name, default_version ; body: optional notes
      <version-id>.json     # SillyTavern V3 card — e.g. default.json, corrupted.json
      assets/               # avatar/images extracted from imported PNG/CHARX (optional)
  locations/<id>.md         # unchanged generic markdown
  lore/<id>.md              # unchanged
```

- `<char-id>` and `<version-id>` are slugified from their names and uniquified on collision
  (the same helper used elsewhere), no date prefix.
- `character.md` holds the container's display **name** and its **default_version** (the
  version preselected when the character appears). Its body is optional free notes.
- Each `<version-id>.json` is a full V3 card: `{spec: "chara_card_v3", spec_version: "3.0",
  data: {name, description, personality, scenario, first_mes, mes_example, creator_notes,
  system_prompt, post_history_instructions, alternate_greetings, character_book, tags,
  creator, character_version, extensions, …}}`. The app authors a practical subset (see
  Frontend) but round-trips the whole object.
- Entities still **carry no auto timestamps**, so a card's hash changes only when its content
  changes — the property the sync engine relies on. (`character.md` carries no timestamps
  either.)

### Campaign: only the locked card lands

```
campaigns/<cid>/
  campaign.md
  sync.md                                   # base hashes for LOCATIONS/LORE only (unchanged)
  appearances.md                            # the cast index — see below
  characters/<char-id>/<version-id>.json    # the ONE locked card (single file in the folder)
  locations/<id>.md  lore/<id>.md           # copy-on-create, as today
  scenes/<sid>.md
```

The campaign keeps the `<char-id>/<version-id>.json` folder shape (rather than flattening)
so the locked version id is visible on disk and the per-character sync ref is stable.

### `appearances.md` — cast index, version lock, and character sync state

The single source of truth for everything character-specific in a campaign: which characters
have appeared, the locked version, the sync **base** hash, and which scenes they're in. It is
the fast "who's in which scene" index **and** the per-character sync state — so character sync
state lives here, **not** in `sync.md`. Reuses the existing frontmatter writer (one mapping per
character).

```markdown
---
seraphine:
  version: corrupted
  base: 3f9a2c…              # world hash of characters/seraphine/corrupted.json at last accept/reject
  scenes: [the-docks, the-reckoning]
drowned-king:
  version: default
  base: a12b8f…
  scenes: [the-docks]
---
```

Per-scene **dismissed suggestions** are stored on the scene file's frontmatter (a `dismissed:
[char-id, …]` list), so a declined suggestion stops nagging without polluting the cast index.

## Appearance model

`appear(cid, scene_id, char_id, version_id)` is the one operation both entry paths call:

1. If `char_id` is **not yet in `appearances.md`**: copy the world card
   `worlds/<wid>/characters/<char_id>/<version_id>.json` (and its `assets/`) into the campaign,
   create the `appearances.md` record with `version = version_id`, `base = world card hash`,
   `scenes = [scene_id]`. This is the **lock** — `version_id` is fixed for the campaign.
2. If `char_id` **is already appeared**: `version_id` must equal the locked version (the UI
   never offers another); just append `scene_id` to its `scenes` list if absent. Idempotent.
3. Bump `campaign.updated`.

Two entry paths, same operation:

- **Manual** — the **Add Character** picker lists the **full world roster**, marking
  already-appeared characters (greyed/disabled). Selecting one opens the version picker
  (default preselected; skipped when the character has a single version), then calls `appear`.
- **Suggestion** — accepting a suggested character (below) opens the same version picker and
  calls the same `appear`.

A character therefore becomes a real, locked appearance **only on explicit confirm** through
the version picker. There is no silent locking.

## Suggestion engine (name-mention scan)

Pure, live computation — never mutates state:

`GET /campaigns/{cid}/scenes/{sid}/suggestions` →
`[{character, name, mentioned_by: [char-id, …]}]`

- Scans the text fields — `description`, `personality`, `scenario`, `first_mes`,
  `mes_example` — of the **cards already in the scene** (the locked cards of characters whose
  `scenes` includes `sid`).
- Matches **whole-word, case-insensitive** against the **name** of every *other* world
  character.
- **Excludes** characters already appeared in the campaign, the mentioning character itself,
  and any char-id in the scene's `dismissed` list.

Frontend renders the result as a dismissible **"Suggested cast"** strip in the scene:

- **Accept** → version picker → `appear` (becomes a normal locked appearance).
- **Dismiss** → `POST …/suggestions/dismiss {character}` adds it to the scene's `dismissed`
  list; suggestions are otherwise always recomputed, so nothing else persists.

## Sync for characters

Per appeared character the ref is `characters/<char-id>`; its locked **version** is read from
`appearances.md`. Compare three hashes of **that locked version's card only**:

- **world** — hash of `worlds/<wid>/characters/<char-id>/<version>.json` (or absent)
- **base** — `appearances.md[char].base`
- **mine** — hash of the campaign's `characters/<char-id>/<version>.json`

| world's locked-version card | mine vs base       | status                              |
|-----------------------------|--------------------|-------------------------------------|
| changed (`world≠base`)      | unchanged (`==base`) | **update** (world moved, you didn't) |
| changed                     | changed (`≠base`)    | **conflict** (both moved)           |
| unchanged (`world==base`)   | —                  | nothing to offer                    |

- A world **new version** (a different version id appears) → **ignored**; the campaign locked
  another version.
- A **not-yet-appeared** character → nothing to sync (no copy exists). Characters never produce
  a **"new"** push.
- World-side deletion of the locked version is **skipped** this iteration (matches the
  predecessor's deletion stance).

**Accept(char)** copies the world locked-version card into the campaign and sets `base =
world hash`. **Reject/keep-mine(char)** leaves the card and sets `base = world hash` (so the
next world edit surfaces as a conflict, mirroring the predecessor's reject semantics). Both
advance `base`, so a handled change never nags again.

`incoming(cid)` merges two computations: **locations/lore** from the existing `sync.md` engine
(unchanged) plus **characters** from `appearances.md` + the world containers. `accept`/`reject`
route a character ref to `appearances.md` and a location/lore ref to `sync.md`.

## Import / export (full SillyTavern fidelity)

In scope this iteration — `characters.py` parses and emits all three:

- **`.json`** — a bare V3 card object (or a V2 card, upconverted to V3 on read).
- **PNG** — read the `tEXt` chunk keyed `ccv3` (base64 V3 JSON), falling back to `chara`
  (base64 V2, upconverted). Export writes the card into a PNG's `ccv3` `tEXt` chunk over the
  card's avatar (from `assets/`, or a placeholder). Pure-Python PNG chunk read/write (no Pillow
  needed for chunk surgery; the avatar bytes pass through).
- **`.charx`** — a zip; read `card.json` and unpack embedded assets into `assets/`. Export zips
  `card.json` + `assets/`.

Import lands as a new character (or a new version of an existing character, user's choice):
`POST /worlds/{wid}/characters/import` (multipart) → `{character, version}`. Export:
`GET /worlds/{wid}/characters/{cid}/versions/{vid}/export?format=json|png|charx`.

V2→V3 upconversion maps the known V2 fields into `data` and synthesizes `spec`/`spec_version`;
unknown fields are preserved under `data.extensions`.

## Backend — extending the planned `store/` package

```
backend/src/grimoire/store/
  characters.py    # NEW — container/version CRUD, hashing, import/export, V2→V3
  appearances.py   # NEW — cast index IO; appear(); roster/scene queries; dismissed-set
  sync.py          # EXTENDED — character incoming from appearances + world; loc/lore unchanged
  entities.py      # locations|lore only now (characters split off)
  worlds.py campaigns.py scenes.py config.py paths.py frontmatter.py  # as planned
```

Core functions (sketch):

```python
# characters.py  (operates on a world OR campaign root)
list_characters(root) -> [{id, name, default_version, versions:[{id,name}]}]
read_character(root, cid) -> {meta, versions:[{id, name, card}]}
read_card(root, cid, vid) -> dict           # the V3 card object
create_character(root, name, default_version_name, card) -> (cid, vid)
create_version(root, cid, name, card) -> vid
update_version(root, cid, vid, card)
set_default_version(root, cid, vid)
delete_character(root, cid); delete_version(root, cid, vid)
card_hash(root, cid, vid) -> str | None     # sha256 of the version file text
import_card(root, data: bytes, fmt, into_cid=None) -> (cid, vid)   # json|png|charx
export_card(root, cid, vid, fmt) -> bytes

# appearances.py
appear(cid, scene_id, char_id, version_id)  # lock+copy on first, append scene after
roster(cid) -> [{character, name, version, scenes}]
scene_cast(cid, scene_id) -> [char-id]
dismiss(cid, scene_id, char_id); dismissed(cid, scene_id) -> [char-id]

# sync.py
incoming(cid) -> [...]                       # loc/lore (sync.md) + characters (appearances.md)
accept(cid, refs); reject(cid, refs)         # routes per ref kind
```

`characters.py` is container-aware (folders + multiple cards), so it is a **dedicated module**,
not the generic `entities.py` (which now serves locations/lore only). It takes a root, so world
and campaign character storage share the same code.

## API (deltas to the worlds/campaigns surface, all under `/api`)

```
# World characters — dedicated routes ({kind} generic routes now serve locations|lore only)
GET    /worlds/{wid}/characters                              → [{id, name, default_version, versions}]
POST   /worlds/{wid}/characters         {name, version_name?, card?}   → {character, version}
GET    /worlds/{wid}/characters/{cid}                        → {meta, versions:[{id,name,card}]}
PUT    /worlds/{wid}/characters/{cid}   {name?, default_version?}
DELETE /worlds/{wid}/characters/{cid}
GET    /worlds/{wid}/characters/{cid}/versions/{vid}         → {card}
POST   /worlds/{wid}/characters/{cid}/versions   {name, card}   → {version}
PUT    /worlds/{wid}/characters/{cid}/versions/{vid}  {card}
DELETE /worlds/{wid}/characters/{cid}/versions/{vid}
POST   /worlds/{wid}/characters/import   (multipart: file=.json|.png|.charx, into?=cid)  → {character, version}
GET    /worlds/{wid}/characters/{cid}/versions/{vid}/export?format=json|png|charx

# Campaign cast
GET    /campaigns/{cid}/appearances                          → roster
GET    /campaigns/{cid}/scenes/{sid}/cast                    → [char-id]
POST   /campaigns/{cid}/scenes/{sid}/cast   {character, version?}   → appear/introduce
GET    /campaigns/{cid}/scenes/{sid}/suggestions             → [{character, name, mentioned_by}]
POST   /campaigns/{cid}/scenes/{sid}/suggestions/dismiss  {character}

# incoming/accept/reject (existing routes) now include character refs alongside loc/lore
```

`POST /cast` with a `version` is required on first appearance of a multi-version character;
omitted/ignored once locked or for single-version characters (server uses the default).

## Error handling

- `CharacterNotFound`, `VersionNotFound` → `404`. Importing an unparseable/garbage card → `400`
  with a readable reason. Exporting an absent version → `404`.
- Appearing a character whose world version no longer exists → `400`.
- `appear` on an already-appeared character with a **different** version → `409` (the UI never
  sends this; defensive).
- Accept/reject of a non-pending character ref is an idempotent no-op (advances base harmlessly).
- A missing `appearances.md` reads as an empty cast; a scene with no `dismissed` key reads as
  an empty set.
- Name collisions on character/version ids auto-uniquify (never an error).

## Frontend (deltas)

- **WorldView — character editor.** Character list → versions list → a V3 **card form**
  authoring a practical subset (name, description, personality, scenario, first_mes,
  mes_example, alternate_greetings, optional `character_book`; advanced/unknown fields
  preserved on round-trip). Set-default-version control. **Import** (drop a `.png`/`.json`/
  `.charx`, choose new-character vs new-version) and **export** (pick format).
- **CampaignView — cast.** A **"+ Add character"** control opens the full-world-roster picker
  (appeared = greyed) → version picker → appear. A **cast panel** shows the roster from
  `appearances.md` with scene membership. A scene shows a dismissible **"Suggested cast"** strip
  from `…/suggestions`; accept → version picker → appear, dismiss → `…/suggestions/dismiss`.
- **IncomingReview.** Character `update`/`conflict` rows render the card as **fields**
  (`conflict` = world-vs-mine side-by-side with the louder warning); accept/keep-mine is
  whole-card. Location/lore rows unchanged.
- **`api/client.ts`** gains typed functions for character containers/versions, import/export,
  appearances, cast, and suggestions.

All components reference **theme tokens only** — no hardcoded colors or fonts.

## Testing

**Backend (pytest, temp `GRIMOIRE_HOME`):**

- card round-trip + `card_hash` stability (no timestamps → stable hash);
- **import parsers** — bare V3 `.json`; **V2 `.json` upconverted** to V3; PNG `ccv3` `tEXt`
  read + `chara` fallback; `.charx` zip with assets; garbage → `400`. Export of each format,
  and PNG/CHARX **round-trip** (import then export then re-import equals original card data);
- **appearance** — first appear locks the version, copies **only** that card + its assets,
  writes the `appearances.md` record; second appear in another scene only appends to `scenes`;
  `appear` with a mismatched version → `409`;
- **suggestion scan** — a card whose text mentions another world character's name surfaces it;
  already-appeared / the mentioning character / dismissed ones are excluded; whole-word match
  (no substring false-hit);
- **character sync table** — update / conflict / nothing; a world **new version is ignored**;
  accept copies the locked card + advances base; reject advances base only and does not
  re-surface;
- locations/lore sync still passes unchanged.

**Frontend (light):**

- card form renders/saves the subset and preserves unknown fields;
- version picker is **skipped** for single-version characters, shown for multi;
- Add-Character roster greys appeared characters;
- Suggested-cast strip renders, accept routes through the version picker, dismiss hides it;
- character conflict row shows both sides.

## Phasing

The implementation plan will sequence:

1. **Backend characters** — `characters.py` (container/version CRUD + hashing), world character
   routes, the dedicated character editor data path. App stays green.
2. **Import/export** — V3/V2 JSON, PNG `tEXt`, CHARX; routes; round-trip tests.
3. **Appearance + suggestions** — `appearances.md`, `appear()`, cast + suggestion routes,
   per-scene dismissed set.
4. **Character sync** — extend `incoming/accept/reject` for character refs.
5. **Frontend** — character/version editor + import-export UI; Add-Character roster + version
   picker + cast panel; Suggested-cast strip; character rows in IncomingReview.

## What grows later (not built now)

- Injecting locked cards into the LLM prompt (the deferred context builder).
- Version switching after lock; alias/nickname matching and transcript scanning for suggestions.
- Sub-field (per-field) card conflict resolution beyond whole-card keep-mine / take-world.
- Deletion propagation for characters.
