# Entity frontmatter reference

Source of truth: the Pydantic models in `backend/src/grimoire/types/`. `*` =
required. Unlisted keys are ignored on load. Prose goes in the markdown body
below the frontmatter (for every kind that has a body).

## world.yaml (`WorldMeta`)

| field | type | notes |
|-------|------|-------|
| `id`* | str | kebab-case; matches the directory name |
| `name`* | str | display name |
| `description` | str | one-paragraph pitch |
| `tags` | list[str] | genre/setting tags |
| `pc_role_tags` | list[str] | role tags a PC can take (e.g. `transfer-student`) |
| `genre` | str | free text |
| `calendar` | mapping | inline calendar block (below); default style |
| `calendar_ids` | list[str] | first-class calendar entities (alternative to inline) |
| `holiday_set_ids` | list[str] | first-class holiday sets |
| `display_calendar_id` | str | which attached calendar renders by default |
| `atmosphere` | mapping | `default_register`, `default_palette` |
| `defaults` | mapping | `starting_location`, `default_style_guide_id`, `default_image_preset_id` |
| `version` | int | bump on schema-relevant edits |

Inline `calendar:` block:
- `epoch`: ISO date (campaign start)
- `months`: list of `{ name, days }`
- `days_per_week`: int; `week_day_names`: list[str]
- `seasons`: list of `{ name, start_month, start_day, palette, weather_bias: {kind: weight} }`
- `holidays`: list of `{ name, month, day, description, tags }`

## characters/<id>.md (`Character`)

| field | type | notes |
|-------|------|-------|
| `id`* | str | |
| `name`* | str | |
| `role`* | enum | `pc` \| `major_npc` \| `minor_npc` \| `ensemble` \| `named_flavor` |
| `aliases` | list[str] | |
| `age` | str | string, not int (e.g. `"16"`) |
| `tags` | list[str] | |
| `role_tags` | list[str] | |
| `voice` | mapping | `summary`, `voice_register`, `samples[]`, `speech_patterns[]`, `address_terms{}`, `dos[]`, `donts[]` |
| `image` | mapping | `base_prompt`, `negative_prompt`, `canonical_seed`, `extra{}` |
| `images` | list | `{ path, description, kind, tags[], seed, prompt_used, source, created_at, extra }`; `kind` ∈ portrait/avatar/expression/pose/scene/reference |
| `structural_relationships` | list | `{ to_ref, kind, note }`; `to_ref` is a character id or `worlds/<w>/factions/<id>`; `kind` e.g. `mentor`, `rival`, `faction:member` |
| `household_id` | str | shared key for characters who tick together |
| `privacy` | mapping | `internal_thoughts: { surface_in_hud, surface_inline, surface_in_context }` |
| `extras` | mapping | snake_case custom keys |

Use `voice_register`, **not** `register`. Description/personality prose goes in
the body.

## locations/<id>.md (`Location`)

| field | type | notes |
|-------|------|-------|
| `id`* | str | |
| `name`* | str | |
| `parent_id` | str | containing location id, or `null` for a top-level place |
| `kind` | enum | `city` \| `building` \| `room` \| `region` \| `outdoor` \| `other` |
| `aliases` | list[str] | |
| `tags` | list[str] | |
| `climate_zone` | str | |
| `indoor` | bool | |
| `coordinates` | mapping | `{ x, y }` (floats) |
| `permanent_features` | list[str] | |
| `connections` | list | `{ to, via, duration_min, notes }`; `to` is a location id |
| `typical_occupants` | list[str] | character ids |

## items/<id>.md (`Item`)

| field | type | notes |
|-------|------|-------|
| `id`* | str | |
| `name`* | str | |
| `aliases` | list[str] | |
| `tags` | list[str] | |
| `provenance` | str | |
| `current_holder` | str | character id or `null` |

## factions/<id>.md (`Faction`)

| field | type | notes |
|-------|------|-------|
| `id`* | str | |
| `name`* | str | |
| `kind` | str | free text |
| `base_location` | str | location id |
| `leaders` | list[str] | character ids |
| `members` | list[str] | character ids |
| `allies` | list[str] | faction ids |
| `rivals` | list[str] | faction ids |
| `tags` | list[str] | |

## monsters/<id>.md (`Monster`)

| field | type | notes |
|-------|------|-------|
| `id`* | str | |
| `name`* | str | |
| `category` | enum | beast/undead/dragon/fey/demon/aberration/humanoid/construct/elemental/other |
| `aliases` | list[str] | |
| `tags` | list[str] | |
| `threat_level` | str | free text (`"deadly"`, `"CR 12"`) |
| `habitat` | list[str] | location ids or biome strings |
| `abilities` | list[str] | |
| `weaknesses` | list[str] | |

## lore/<id>.md (`LoreEntry`)

| field | type | notes |
|-------|------|-------|
| `id`* | str | |
| `title`* | str | **lore uses `title`, not `name`** |
| `tags` | list[str] | |
| `keywords` | list[str] | trigger keys |
| `related_locations` | list[str] | location ids |
| `related_factions` | list[str] | faction ids |
| `related_characters` | list[str] | character ids |
| `secrecy` | enum | public/common-knowledge/common-knowledge-among-kindred/restricted/secret |
| `secondary_keys` | list[str] | |
| `selective_logic` | enum | and_any/and_all/not_any/not_all |
| `constant` | bool | always injected if true |
| `enabled` | bool | |
| `case_sensitive` | bool | |
| `match_whole_words` | bool | |
| `priority` | int | default 100 |
| `probability` | int | 0–100, default 100 |
| `position` | enum | before_cast/after_cast/at_depth/archive |
| `at_depth` | int | when `position: at_depth` |
| `scan_depth` | int | |
| `comment` | str | author note |

Lore prose goes in the markdown body.

## greetings/<id>.md (`Greeting`)

| field | type | notes |
|-------|------|-------|
| `id`* | str | |
| `name`* | str | |
| `starting_location` | str | location id (or `null`) |
| `starting_time` | str | ISO-8601 in the world calendar |
| `present_characters` | list[str] | character ids on stage |
| `pov_character` | str | character id or `null` |
| `mood` | str | one-line scene mood |
| `tags` | list[str] | |
| `role_tags` | list[str] | gates which PC roles see this greeting |

Greeting prose (the opening scene shown to the player) goes in the body.

## Minimal valid examples

A location:
```markdown
---
id: town-square
name: Town Square
parent_id: rivermouth
kind: outdoor
tags: [public, hub]
indoor: false
---
The cobbled heart of Rivermouth, ringed by awnings and the smell of fried fish.
```

A character:
```markdown
---
id: mara-vance
name: Mara Vance
role: major_npc
age: "34"
tags: [smuggler]
voice:
  summary: Clipped, wry, allergic to sentiment.
  voice_register: low, casual
  samples:
    - "You want it done, or you want it done quiet? Pick one."
  dos: ["Names a price fast.", "Watches the exits."]
  donts: ["Never apologizes first."]
image:
  base_prompt: "weathered woman, short dark hair, oilskin coat, harbor at dusk"
---

## Appearance
Lean, sun-creased, a knife she never mentions.

## What she wants
Out from under a debt she didn't sign for.
```
