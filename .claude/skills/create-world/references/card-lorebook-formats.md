# Card & lorebook ingestion reference

The faithful mapping lives in `backend/src/grimoire/characters/ingest.py`
(`ingest_character_card_v2`) and `imports.py`. This doc summarizes it so you can
author files without a running backend. When in doubt, read those modules.

## Supported inputs

- **SillyTavern Character Card V2/V3** — JSON envelope `{ "spec":
  "chara_card_v2"|"chara_card_v3", "data": { ... } }`.
- **PNG with embedded card** — a `tEXt` chunk keyed `chara` (base64-encoded JSON,
  V2) or `ccv3` (V3). Decode the chunk's text, base64-decode, then parse as the
  JSON envelope above. The image itself is the avatar.
- **`charx`** — a ZIP bundle; the card JSON is an entry inside it (look for
  `card.json` / a `.json` matching the envelope) plus asset files.
- **Plaintext** — first non-empty line = name; quoted lines = voice samples;
  remaining prose = description/body. Role defaults to `minor_npc`.

To read a PNG `tEXt` chunk or a `charx` zip without the backend, use a short
Python one-off (`zipfile`, or walk PNG chunks) — or, if the backend is handy,
prefer the existing parser. Expand SillyTavern macros (`{{char}}`, `{{user}}`,
`{{original}}`, etc.) in every text field before writing.

## Field mapping: card `data` → Grimoire

| card field | Grimoire target |
|------------|-----------------|
| `name` | character `name` + slugified `id` |
| `description`, `personality` | `voice.summary` + character body prose |
| `scenario` | body context; may seed a greeting `mood` |
| `mes_example` | parsed dialogue → `voice.samples[]` |
| `first_mes` | primary greeting (`greetings/<slug>.md`, present_characters=[char]) |
| `alternate_greetings[i]` | one greeting file each |
| `system_prompt`, `post_history_instructions` | keep as `extras` or body notes; not first-class |
| `tags` | character `tags` |
| `creator`, `character_version` | `extras` |
| `character_book.entries[]` | lore files (below) |
| embedded avatar (PNG) | `images[]` `{ source: embedded_avatar, kind: portrait }` if kept; else derive `image.base_prompt` |

Default imported role: `major_npc` (override if the user says otherwise).

## character_book.entries[] → lore/<id>.md (`LoreEntry`)

| book entry field | LoreEntry field |
|------------------|-----------------|
| `keys` | `keywords` |
| `content` | body |
| `secondary_keys` | `secondary_keys` |
| `selectiveLogic` / `selective_logic` | `selective_logic` (and_any/and_all/not_any/not_all) |
| `constant` | `constant` |
| `enabled` | `enabled` |
| `case_sensitive` | `case_sensitive` |
| `match_whole_words` / `extensions.match_whole_words` | `match_whole_words` |
| `insertion_order` / `priority` | `priority` |
| `probability` | `probability` |
| `position` | `position` (before_cast/after_cast/at_depth/archive) |
| `depth` | `at_depth` (with `position: at_depth`) |
| `scan_depth` | `scan_depth` |
| `comment` | `comment` and/or `title` |

## Reclassification

A `character_book` entry that clearly describes a **place**, **organization**,
**item**, or **person** should become that entity kind instead of lore:

- place → `locations/<id>.md` (pick a `kind`)
- organization → `factions/<id>.md`
- item → `items/<id>.md`
- person → `characters/<id>.md` (`role: minor_npc` unless richer)

This mirrors the app's import reclassify step. When unsure, keep it as lore.

## Merge rules

- Dedupe against existing world ids; if an id collides, suffix or merge content
  (ask the user on a real conflict).
- On update, wire imported characters into existing factions/relationships when
  the source text implies membership or ties.
- After ingesting, run the validator (see SKILL.md step 6).
