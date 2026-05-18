# Character Card Imports (SillyTavern v2/v3)

The current ingestor (`grimoire.characters.ingest`) parses cards to a
`CharacterData` and preserves raw `character_book` + `alternate_greetings`
on `IngestedCharacterCard` but never persists them. Macros remain literal
in imported text. This spec closes those gaps and defines the canonical
import shape.

## 1. Macro substitution

Closed set of macros, expanded once at ingest:

| Macro | Behaviour |
|---|---|
| `{{char}}` | Replaced with the card owner's name (case-insensitive match) |
| `{{user}}` | Preserved literal at ingest; substituted by Context Builder at runtime against the active PC (`the player` if none) |
| `{{random:a,b,c}}` / `{{random:a::b::c}}` | Seeded random pick |
| `{{pick:...}}` | Alias of `random` (same determinism) |
| `{{roll:NdM}}` | Sum of N M-sided rolls, seeded |
| `{{newline}}` | `\n` |
| `{{trim}}` | Empty; one whitespace char on each side consumed |
| `{{// comment}}` | Stripped |

- Seed: `SHA-256(card_asset_id :: field_name :: macro_index)`. Same input
  → same output across runs.
- Unknown macros pass through unchanged; one warning per field on
  `IngestedCharacterCard.warnings`.
- Public API: `expand_macros(text, *, char_name, card_asset_id, field_name,
  keep_user) -> (str, warnings)` in `grimoire.characters.macros`.
- Late `{{user}}` substitution pass on Context Builder
  (`_resolve_runtime_macros(assembled, active_pc)`) invoked at the end of
  `build()`. Pure string replace; idempotent; touches `Message.content`
  only.

Variant macros for greetings / style guides / image presets (the
`{{pick weighted}}`, `{{calendar}}`, `{{pc:name/pronoun}}` set from
external-influences) should reuse this engine — pick the smallest
directive set that satisfies both flows and document the final list here.

## 2. `character_book` → setting lore

Walk `character_book.entries[]` → `IngestedLoreEntry` → write to
`library/settings/<sid>/lore/<char_slug>--<entry_slug>.md` via
`LibraryService.create_entity`.

- `char_slug` from the card; `entry_slug` from entry name → first
  slugifiable key → `entry-<source_index>`.
- Conflict resolution: suffix `-2`, `-3`, … with a warning. Never
  overwrite an existing lore file.
- Frontmatter records `import_source: {kind: sillytavern_character_book,
  card_asset_id, source_index}` and tags `[imported, from-card,
  <char_slug>]`.
- Macro pass applied to the entry body, `keys`, `secondary_keys`,
  `comment`.

Optional per-import lorebook scope: setting (default) or character —
`lore/<char_slug>/...`, triggers only when the character is in scene.
Heuristic recommendation based on whether the lorebook keys reference the
character's name.

## 3. Richer `LoreEntry` + `lore_for_post`

Extend `LoreEntry` (backwards-compatible defaults so existing files parse
unchanged):

- `secondary_keys: list[str]`
- `selective_logic: and_any | and_all | not_all | not_any`
- `constant: bool`
- `enabled: bool`
- `case_sensitive: bool`
- `match_whole_words: bool`
- `priority: int = 100`
- `probability: int = 100`
- `position: before_cast | after_cast | at_depth | archive`
- `at_depth: int`
- `scan_depth: int | None`
- `comment: str`
- `import_source: ImportSource | None`

`lore_for_post` honours every field. Algorithm:

1. Skip if `enabled` is false.
2. `constant` entries always fire.
3. Build haystack from the last `scan_depth` posts.
4. Primary keyword match: `case_sensitive` + `match_whole_words` controls.
5. Secondary keys evaluated per `selective_logic`.
6. Deterministic probability check: roll = `int(SHA-256(entry.id ::
   turn_id)[:8]) % 100`; include if `roll < probability`.
7. Sort `(-priority, id)`, truncate to `max_results`.

Context Builder routes hits by position: `before_cast` → Spotlight,
`after_cast` → Background, `at_depth` → recent-posts system message,
`archive` → Archive tier. `turn_id` plumbed in as a keyword arg with a
default that uses the current scene's turn.

## 4. `first_mes` + `alternate_greetings` → Greeting library entries

- `first_mes` → `library/settings/<sid>/greetings/<char_slug>--default.md`.
- `alternate_greetings[i]` →
  `library/settings/<sid>/greetings/<char_slug>--alt-<i+1:02>.md`.
- Frontmatter: `id`, auto-derived `name`, `present_characters:
  [<char_slug>]`, `tags: [imported, from-card, <char_slug>(,
  alternate-greeting)]`, `import_source`.
- Body goes through the macro pass; `{{char}}` resolves, `{{user}}`
  preserved.
- Remove the existing `## Alternate greetings` section from the character
  body — greetings are now first-class entities. Release-note the change.

## 5. Service wiring

`CharactersService.import_character_card(payload, target_setting_id, *,
options) -> (ImportResult, IngestedCharacterCard)` writes character +
greetings + lore in one call. `ImportResult.created` carries all three id
kinds.

New `IngestOptions` toggles (all default `True`): `expand_macros`,
`import_character_book`, `import_alternate_greetings`,
`import_primary_greeting`.

Atomicity: validate everything in memory → write character →
write greetings → write lore. Character write failure aborts the import;
greeting / lore failures append to `ImportResult.errors` without aborting.

## 6. Discarded inputs + import report

Mapped fields, recorded in the import report:

- `system_prompt` → campaign-scoped `system_addendum` field (never
  written into the character card).
- `post_history_instructions`, `extensions.depth_prompt` → discarded with
  warning (anti-pattern: card author rewriting assembled prompt).
- V3 `extensions` (`risuai`, `chub`, `regex_scripts`) → discarded; regex
  scripts rejected by policy.
- Avatar PNG: strip non-essential metadata; keep only the `chara` chunk.

Each import run writes a markdown report to
`data/library/imports/<timestamp>-<source>.md` listing every file
created, field discarded, collision resolved.

## Out of scope (v1)

Vectorized lore (`vectorized: true`), recursion / sticky / cooldown /
delay-until-recursion, group scoring / weights, scan-scope flags
(`matchScenario`, `matchCreatorNotes`, `matchPersonaDescription`),
slash commands / STScript, connection profiles / sampler presets /
instruct templates, multi-character card formats, late-rebinding
`{{char}}`, auto-pruning previous imports, live bidirectional sync,
SillyTavern-format export.
