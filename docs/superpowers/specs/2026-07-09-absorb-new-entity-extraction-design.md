# Absorb: extract new characters, locations, and lore — design

**Date:** 2026-07-09
**Status:** approved, ready for planning
**Scope:** backend (`store/absorb.py`, `store/entities.py`, `templates/absorb/`)
+ frontend (`CampaignView.tsx`, `CharacterEditor.tsx`, generic `EntityEditor.tsx`).
Single LLM call — unchanged shape (still one `absorb` completion per End Scene).

## Problem

The "End scene" (absorb) flow already lets one LLM call propose edits —
character state, lore appends, card rewrites, relationship/bond deltas, plot
movement — as a list of typed staged edits the user reviews and approves
before `Save summary` applies them (`store/absorb.py`, `routes.py:1513-1560`,
`CampaignView.tsx`'s `absorb-panel`).

But every edit kind targets a record that must **already exist**. Casting a
character into a scene requires picking one from an existing roster; likewise
a scene's location and any lore entry. When a game master introduces a brand
new NPC, place, or lore beat purely through narration — never selecting them
from a picker, because there was nothing to pick — absorb has no way to turn
that prose into a real record. The workaround today is manual: stop mid-scene
and go create a character/location/lore entry by hand before continuing.

## Goals

1. The same absorb LLM call also proposes **new characters**, **new
   locations**, and **new lore entries** it found in the transcript that
   aren't already known records.
2. Each proposal renders in the existing review panel as one more approvable,
   **editable** staged edit — same checkbox-and-textarea mechanics already
   used for every other edit kind.
3. Approving and saving actually **creates the record** (character card /
   location entity / lore entity) in the campaign's copy-on-write store —
   the same trust boundary `character_state_edits`/`authored_edits` already
   write into (`campaigns.campaign_root(cid)`, not the master world).
4. A new character gets a full **W++ writeup** (in `description`) and a
   suggested **Stable Diffusion prompt**, persisted on the card so it's usable
   later even though nothing in grimoire generates the image itself.
5. A new location also gets a suggested SD prompt; a new lore entry does not.
6. Approving a new character **casts them as a present NPC** in the
   already-ended scene (they were clearly there).
7. Approving a new location **auto-links it as the scene's location**, but
   only when the scene doesn't already have one on record and the model
   marked it as the scene's actual setting (not merely mentioned).
8. Proposals that collide with an existing record (by id-slug or
   case-insensitive display name) are dropped rather than creating a
   duplicate — same "tolerate and drop" philosophy `materialize()` already
   applies to edits whose target doesn't resolve.

## Non-goals (explicitly out of scope)

- **No image generation.** `sd_prompt` is plain text the user pastes into an
  external tool. No API call, no portrait file is produced here.
- **No new *V3-spec* card fields.** The W++ block is written entirely into
  the standard `description` field, matching the single-field convention the
  `sillytavern-cards` skill itself documents; `personality`/`scenario` stay
  blank for a freshly-extracted character. (`sd_prompt` is not a spec field —
  it rides in the existing free-form `extensions` bag, same mechanism cards
  already use for out-of-spec metadata.)
- **No fuzzy dedup.** Only exact id-slug or exact case-insensitive name
  matches are treated as "already exists." A near-miss (typo, alias) creates
  a duplicate the user must merge/delete manually — same tolerance level as
  the rest of `materialize()`.
- **No heuristic fallback for `current_setting`.** If the model doesn't mark
  any new location as the scene's setting, none gets auto-linked — no
  "guess the first one" behavior.
- **No world-level (non-campaign) version of this.** Absorb only ever runs
  against a campaign's scene transcript.
- **No changes to the `/absorb` or `/chronicle` route signatures.** `edits`
  is already `list[dict]` on the wire (`ChronicleSave.edits: list[dict]`), so
  new edit kinds/payload shapes need no Pydantic model change.

## Design

### Prompt (`templates/absorb/system.j2`)

Three more keys in the single JSON object the model returns:

- **`new_characters`**: list of `{"name", "description", "sd_prompt"}`. Only
  for a person who is named and speaks/acts in the scene but has no existing
  character record (not in the "Present:" context line). `description` must
  be a complete W++ block (`[character("Name") { Attribute("value") ... }]`)
  covering species/age/occupation/appearance/personality as available from
  the scene. `sd_prompt` is a comma-separated txt2img prompt for their
  appearance.
- **`new_locations`**: list of `{"name", "body", "keys", "sd_prompt",
  "current_setting"}`. Any named place mentioned that isn't an existing
  location. `body` is a short descriptive paragraph; `keys` are comma-
  separated activation keywords (the same field existing location/lore
  entries use to trigger inclusion in future scene context — without it a
  new entry is inert). `current_setting` is `true` for at most the one
  location that is where this scene's action actually took place, if new.
- **`new_lore`**: list of `{"name", "body", "keys"}`. Factions, items,
  history, concepts — anything not a person or a place. No `sd_prompt`.

The model is instructed to propose these only for entities with enough
material in the transcript to write a real entry — not every passing name.

### Parsing (`absorb.parse_output`)

Three more tolerant list extractions, same shape as the existing `_list()`
helper: coerce to `str`, drop non-dict entries, keep `current_setting` as a
bool (default `false` on anything falsy/missing).

### Materializing (`absorb.materialize`)

For each proposed character/location/lore item:

1. Compute `candidate_id = slugify(name)`.
2. **Dedup check**: skip (silently drop) if `candidate_id` collides with an
   existing record's id, or if any existing record of that kind has the same
   name case-insensitively. (Characters checked via
   `characters.list_characters(croot)`; locations/lore via
   `entities.list_entities(croot, kind)`.)
3. Emit a staged edit:
   ```
   {"id": f"new_character:{candidate_id}", "kind": "new_character",
    "target": {"kind": "characters", "id": ""}, "label": f"New character — {name}",
    "field": "description", "before": "", "after": description,
    "authored": True, "payload": {"name": name, "sd_prompt": sd_prompt}}
   ```
   (`target.id` is `""`, not `null` — `StagedEdit.target.id` is a plain
   `string` on the frontend type; no record exists yet, so there's nothing to
   put there.) Locations and lore follow the same shape with
   `kind: "new_location"` / `"new_lore"`, `target.kind` "locations"/"lore",
   `field: "body"`, and payload carrying `{name, keys, sd_prompt?,
   current_setting?}`.

### Applying (`absorb.apply_edits`)

Three more branches:

- **`new_character`**: build `characters.blank_card(name)`, set
  `data["description"] = after`, set
  `data["extensions"]["sd_prompt"] = payload["sd_prompt"]` (flat key, same
  convention as the existing `extensions.chub_source`/`extensions.grimoire_label`
  — not a nested namespace object), call
  `characters.create_character(croot, name, "default", card)` →
  `(new_cid, new_vid)`, then `appearances.appear(cid, sid, "characters",
  new_cid, new_vid, "npc")` to cast them into the scene that just ended.
- **`new_location`**: `entities.create_entity(croot, "locations", name,
  body=after, keys=payload["keys"], sd_prompt=payload.get("sd_prompt", ""))`
  → `new_eid`. If `payload.get("current_setting")` and
  `scenes.get_location_history(cid, sid)` is still empty, call
  `scenes.set_location(cid, sid, new_eid)` (silent first-set — its existing
  contract for a previously location-less scene).
- **`new_lore`**: `entities.create_entity(croot, "lore", name, body=after,
  keys=payload["keys"])`.

All three are added to `_BROWSABLE_KINDS` so the creation shows up in the new
record's own change history (`changes.record`), same as any other browsable
edit.

### `entities.py`: new `sd_prompt` field

`create_entity`/`update_entity` gain an optional `sd_prompt: str = ""` param,
stored/read as a plain frontmatter meta key exactly like `keys`/`owners`
today. Lore entries simply never set it; only the absorb `new_location` path
and (if the user edits it later) the entity form populate it.

### Surfacing `sd_prompt` afterward

Since the point of persisting it is reuse, both detail sidebars get one more
`side-section`, rendered only when the field is non-empty:

- `CharacterEditor.tsx` detail view: `<h4>Image prompt</h4>` reading
  `card.data.extensions?.sd_prompt`.
- `EntityEditor.tsx` detail view: `<h4>Image prompt</h4>` reading
  `meta.sd_prompt`, shown for both `locations` and `lore` scopes (harmless —
  lore entries just never have one to show).

Plain text, not editable inline here; changing it means re-running absorb or
editing the record's underlying file directly (no new form field is added to
the character/entity edit forms in this pass).

### Frontend review panel (`CampaignView.tsx`)

The edit-row renderer's existing `kind`-based special case (currently just
`relationship`/`bond` vs. everything else) gains three more branches:

- `new_character` / `new_location` / `new_lore` all render: an editable Name
  input (bound to `e.payload.name`), the existing after-textarea (bound to
  `e.after`, i.e. description/body), and for `new_location`/`new_character`
  an editable "Suggested image prompt" input (bound to `e.payload.sd_prompt`);
  `new_location` also shows a "This is where the scene happened" checkbox
  bound to `e.payload.current_setting` (only meaningful, and only rendered,
  when the scene has no location yet).
- Approve checkbox and Save/Cancel flow are unchanged — these are just more
  entries in the same `editRows` array, applied through the same
  `saveAbsorb` → `PUT chronicle` → `apply_edits` path.

`StagedEdit["kind"]` (frontend type in `api/client.ts`) grows the three new
literals.

## Testing

`backend/tests/test_absorb_store.py`:

- `parse_output` extracts `new_characters`/`new_locations`/`new_lore`,
  tolerating missing/non-dict entries.
- `materialize` drops a proposed character/location/lore whose slug or name
  already exists, and emits a well-formed staged edit for a genuinely new one.
- `apply_edits`:
  - `new_character` creates the character, sets `description` and
    `extensions.sd_prompt`, and casts it as an NPC into the scene.
  - `new_location` creates the entity with `keys`/`sd_prompt`; when
    `current_setting` is true and the scene had no location, the scene's
    location history gets the new entity; when the scene already had a
    location, it is left untouched even if `current_setting` was true.
  - `new_lore` creates the entity; no scene-level side effect.

`backend/tests/test_entities_store.py`: `create_entity`/`update_entity`
round-trip `sd_prompt` like `keys`/`owners`.

`frontend/src/routes/CampaignView.test.tsx`: a `new_character` proposal in
the absorb response renders editable name/description/sd_prompt fields,
approving and saving posts a matching edit in the `edits` payload.

`frontend/src/components/CharacterEditor.test.tsx` /
`EntityEditor.test.tsx`: a record with a non-empty `sd_prompt` shows the
"Image prompt" side-section; one without doesn't.

## Files touched

- `templates/absorb/system.j2` — three new schema keys + instructions.
- `backend/src/grimoire/store/absorb.py` — parse/materialize/apply for the
  three new kinds; `_BROWSABLE_KINDS` extended.
- `backend/src/grimoire/store/entities.py` — `sd_prompt` field on
  `create_entity`/`update_entity`.
- `frontend/src/api/client.ts` — `StagedEdit["kind"]` literals.
- `frontend/src/routes/CampaignView.tsx` — review-panel rendering for the
  three new kinds.
- `frontend/src/components/CharacterEditor.tsx` /
  `frontend/src/components/EntityEditor.tsx` — "Image prompt" side-section.
- Corresponding backend/frontend test files listed above.
