# 19 — SillyTavern card imports: macros, lorebooks, greetings, triggers

## Purpose

Grimoire's Character Card V2/V3 ingestor (spec 08 §Import, implemented in `grimoire.characters.ingest`) currently lands the character's name, voice anchor, prose, embedded portrait, and structurally-extracted relationships. Three large parts of every real SillyTavern card are dropped on the floor:

1. **Macros** — every card uses `{{char}}`, `{{user}}`, `{{random:a,b,c}}`, `{{pick:a,b,c}}`, `{{roll:1d6}}`, `{{newline}}`, `{{// comment}}` in greetings, scenarios, examples, and lorebook content. The ingestor preserves the literal tokens, so imported text reads "Hello {{user}}, sit down" after import.
2. **Embedded `character_book`** — SillyTavern V2/V3 cards carry a private lorebook (keyword-triggered prose snippets); the ingestor captures `character_book` on `IngestedCharacterCard` and never persists it.
3. **`alternate_greetings`** — the ingestor concatenates all of them into the character's markdown body as a bulleted list. The campaign-creation flow can only point `greeting_id` at one Greeting entity, so the alternates are unreachable from the UI.

A fourth gap is structural: even if we did import `character_book` today, Grimoire's `LoreEntry` only has a flat `keywords[]` substring trigger. SillyTavern lorebook entries depend on a richer trigger contract (secondary keys with selective logic, always-on entries, probability, case sensitivity, whole-word matching, priority, position). Importing without those fields silently changes the meaning of every lorebook authored against SillyTavern's rules.

This spec covers the four-item batch needed to close those gaps:

- A macro-substitution utility, applied at ingest time and at Context Builder assembly time
- `character_book` → setting `lore/*` persistence
- Richer trigger fields on `LoreEntry` honored by `lore_for_post`
- `alternate_greetings` → setting `greetings/*` persistence

The result is that a card downloaded from a SillyTavern hub round-trips into Grimoire as a Character, a set of Lore entries, and a set of Greetings, with all macros expanded and all lorebook trigger semantics preserved.

## Scope

In scope:

- Macro substitution pass on every human-authored text field in an ingested card (incl. lorebook entry content)
- Macro substitution pass in Context Builder assembly for the `{{user}}` placeholder that resolves to the active PC at runtime
- Promotion of `character_book.entries[]` into `LoreEntry` rows scoped to the target setting
- Promotion of `first_mes` + `alternate_greetings[]` into `Greeting` rows scoped to the target setting
- Extension of `LoreEntry` with SillyTavern-compatible trigger fields (`secondary_keys`, `selective_logic`, `constant`, `enabled`, `case_sensitive`, `match_whole_words`, `priority`, `probability`, `position`, `at_depth`, `scan_depth`, `comment`)
- `setting.lore_for_post` honoring all the above, with a deterministic seeded probability check
- Wire all three import flows into a single `CharactersService.import_character_card` call that lands the character + lore + greetings atomically (best-effort; see §Atomicity)

Out of scope (explicitly):

- Vectorized lore retrieval (`vectorized: true`) — separate spec; reuses the Continuity hybrid-search infra
- Recursion / sticky / cooldown / delay-until-recursion — SillyTavern's chat-loop features, not directly applicable to Grimoire's tier-driven assembly; tracked in §Open questions
- Group scoring, group overrides, group weights
- `matchScenario` / `matchCreatorNotes` / `matchPersonaDescription` scan-scope flags — v1 scans the current post body only
- Author's Note as a separate user-editable injection slot — a related but distinct feature (see §Open questions and item 5 in the recommendations memo)
- Slash commands / STScript — out of scope for the card-import work
- Connection profiles, sampler presets, instruct templates — orthogonal to imports
- Multi-character card formats (`Character Card V3` group/duo) — v2/v3 single-character only
- Late-rebinding `{{char}}` to scene speaker — only `{{user}}` is rebindable at runtime in v1; `{{char}}` is frozen at import to the card's owner

## Non-responsibilities

- This spec does not change how `Character` cards are stored on disk (one markdown file per character)
- It does not change the Library's file-mediated CRUD path
- It does not introduce a new entity kind — lorebook entries become `LoreEntry` rows, greetings become `Greeting` rows
- It does not change the Context Builder's tier system; it only adds two narrow behaviors (`{{user}}` late substitution and reading the new LoreEntry fields)

## Background: what SillyTavern exposes

Mapping reference — every field name on the right is the corresponding Grimoire identifier.

### Macros (subset implemented in v1)

| SillyTavern | Where it appears | Grimoire behavior |
|---|---|---|
| `{{char}}` | Card text, lorebook content | Expanded at ingest to the card owner's name |
| `{{user}}` | Card text, lorebook content | Left literal at ingest; substituted at Context Builder assembly with the active PC name |
| `{{random:a,b,c}}` and `{{random:a::b::c}}` | Anywhere | Expanded at ingest; seeded for determinism (see §Determinism) |
| `{{pick:a,b,c}}` | Anywhere | Expanded at ingest; seeded; stable across re-imports of the same card |
| `{{roll:1d6}}` / `{{roll:d20}}` | Anywhere | Expanded at ingest; seeded |
| `{{newline}}` | Anywhere | Replaced with `\n` |
| `{{// any text}}` | Anywhere | Stripped (treated as authoring comment) |
| `{{trim}}` | Anywhere | Replaced with empty; whitespace around it consumed |

All other SillyTavern macros (`{{time}}`, `{{date}}`, `{{idle_duration}}`, `{{lastMessage}}`, `{{getvar}}`, …) are explicitly out of scope for v1. They produce dynamic runtime values that don't make sense at import time and don't have a clean home in Grimoire's deterministic tier assembly.

### Character book entry shape (V2/V3)

```jsonc
{
  "character_book": {
    "name": "vivienne's Lore",
    "description": "",
    "scan_depth": 50,
    "token_budget": 500,
    "recursive_scanning": false,
    "extensions": {},
    "entries": [
      {
        "keys": ["Camarilla"],
        "secondary_keys": ["sect", "pyramid"],
        "selective": true,
        "selectiveLogic": 0,        // AND_ANY=0, NOT_ALL=1, NOT_ANY=2, AND_ALL=3
        "constant": false,
        "enabled": true,
        "case_sensitive": false,
        "extensions": {
          "match_whole_words": false,
          "position": 0,             // 0=before_char, 1=after_char, 2=ANTop, 3=ANBottom, 4=at_depth
          "depth": 4,
          "probability": 100,
          "useProbability": true
        },
        "insertion_order": 100,
        "comment": "Sect overview",
        "name": "Camarilla",
        "priority": 100,
        "id": 0,
        "content": "The Camarilla is the dominant sect of vampires…"
      }
    ]
  }
}
```

Field-by-field mapping is given in §Trigger field reference below.

### Alternate greetings shape

```jsonc
{
  "first_mes": "\"Darling, you look terrible.\"",
  "alternate_greetings": [
    "\"Oh — back so soon?\"",
    "\"You found me. I almost hoped you wouldn't.\""
  ]
}
```

V3 adds optional metadata per greeting (`name`, `tags`, `mood`) under `extensions.depth_prompt` shapes; v1 reads only the string form. Per-greeting metadata extraction is an open question.

## Macro substitution

### Macros implemented

Pure, deterministic, and small. The list is closed — anything not on it passes through unchanged so that future macros don't silently change meaning.

| Macro | Pattern | Result |
|---|---|---|
| `{{char}}` | exact literal (case-insensitive) | The card's `name` field after normalization |
| `{{user}}` | exact literal (case-insensitive) | **Left as `{{user}}`** at ingest; resolved at Context Builder assembly |
| `{{random:items}}` | `items` is delimited by `,` or `::` | One item chosen by seeded RNG (see §Determinism) |
| `{{pick:items}}` | same delimiters as `random` | One item chosen by seeded RNG, **same instance always picks the same item** for a given card id |
| `{{roll:NdM}}` | `N`, `M` positive ints; `N` optional (defaults to 1) | The sum of N rolls of an M-sided die, seeded |
| `{{newline}}` | exact literal | `\n` |
| `{{trim}}` | exact literal | Empty string; one leading and one trailing whitespace character is consumed |
| `{{// comment}}` | any text up to `}}` after `// ` | Stripped (the entire macro plus any surrounding spaces collapses) |

Unknown macros (`{{anything_else}}`) are left untouched and a warning is added to `IngestedCharacterCard.warnings`. Two reasons: it surfaces SillyTavern features we haven't picked up yet, and it leaves an obvious breadcrumb for the user.

### Determinism

All randomized macros (`{{random}}`, `{{pick}}`, `{{roll}}`) are deterministic: the same input produces the same output across re-runs.

The seed is computed as:

```
seed = SHA-256(
    card_asset_id
    || "::"
    || field_name           # e.g. "first_mes", "alternate_greetings[2]", "lore.camarilla.content"
    || "::"
    || macro_index_in_field # 0, 1, 2, …, counted in source order
)
```

The seed is converted to an `int` and used as an `random.Random(seed)` instance, scoped to a single field. Two macros in the same field draw from the same RNG sequence (so re-ordering them changes both outputs — that's fine, it's still deterministic on the input).

`{{pick}}` differs from `{{random}}` only in name (per SillyTavern convention) — both are equally deterministic in Grimoire. We expose both so authored cards round-trip cleanly.

### Late substitution for `{{user}}`

`{{user}}` is the only macro left unresolved at ingest. The reason: `{{user}}` is meant to resolve to the live player persona, which in Grimoire is the active PC. If we baked the PC's name into the card at import, switching PCs mid-campaign would silently break every imported scenario.

The contract:

- The macro substitution pass at ingest time **preserves** `{{user}}` verbatim.
- A new lightweight pass on Context Builder, called **right before** the assembled prompt is handed to the LLM Gateway, walks each `Message.content` and substitutes `{{user}}` with the active PC's display name. The pass is a literal string replace, not Jinja.
- If no active PC is set (very early campaign state), `{{user}}` resolves to `the player`.
- The pass is idempotent — running it twice produces the same result — and is exempt from the macro-warning behavior described above (it's expected to find `{{user}}`).

This adds a `_resolve_runtime_macros(assembled: AssembledPrompt, active_pc: PCEntry | None) -> AssembledPrompt` private method on `ContextBuilderService`, called once at the end of `build()`. The pass touches every `Message` content string. It does not touch `params`, `sources`, or `summary`. It does not recurse into other macros (none exist at this stage).

### Where macros are applied at ingest

The full list of fields that go through the macro pass, in source order, when `IngestOptions.expand_macros=True` (default):

- `first_mes`
- each entry of `alternate_greetings`
- `scenario`
- `description`
- `personality`
- `mes_example`
- `system_prompt`
- `post_history_instructions`
- `creator_notes`
- For each `character_book.entries[i]`:
  - `content`
  - each entry of `keys`
  - each entry of `secondary_keys`
  - `comment`

The pass does not touch `tags`, `creator`, `character_version`, raw `extensions` blobs, or any user-defined keys we don't recognize. Macros inside structured frontmatter (like `extensions.foo.bar`) are not expanded.

### Public API

```python
# grimoire.characters.macros
def expand_macros(
    text: str,
    *,
    char_name: str,
    card_asset_id: str,
    field_name: str,
    keep_user: bool = True,
) -> tuple[str, list[str]]:
    """Return (expanded_text, warnings).

    Macros are expanded in source order. ``warnings`` lists unknown
    macros encountered (each at most once per field). When
    ``keep_user`` is True, ``{{user}}`` is left literal for later
    runtime substitution; when False, it is substituted with
    ``the player``.
    """
```

The `keep_user=False` form is used when the caller wants a fully-baked snapshot (e.g. seeding a campaign's opening narration into a Post). The ingest pass always uses `keep_user=True`.

## Character book → setting lore

### What lands where

When the user calls `import_character_card(payload, target_setting_id)`, the lorebook flow:

1. Walks `IngestedCharacterCard.character_book["entries"]` in source order.
2. For each entry, produces an `IngestedLoreEntry` (a new ingest-time type, deliberately not the same as the persisted `LoreEntry` — it carries the raw SillyTavern fields one-to-one, before slugging/conflict resolution).
3. Writes each entry as a lore markdown file under `library/settings/<target_setting_id>/lore/<id>.md` via `LibraryService.create_entity(setting_id, "lore", id, frontmatter, body)`.

### ID and namespacing

SillyTavern entries don't have stable IDs across exports — they have a numeric `id` that's local to the card. To avoid collisions and to make the source obvious in the file tree:

```
<char_slug>--<entry_slug>
```

For example: `vivienne--camarilla`, `vivienne--pyramid-of-elders`, etc. Slug derivation:

- `char_slug` is the slug already used for the character file (`asset_id` in the ingestor)
- `entry_slug` is `entry["name"]` if present, else the first `entry["keys"][i]` that slugifies non-empty, else `entry-<source_index>` (e.g. `entry-3`)
- Conflict resolution: if a lore entry with the same id already exists in the target setting, append `-2`, `-3`, … and warn

This namespacing scheme is deliberately visible: a user browsing `library/settings/wod-london/lore/` immediately sees that everything prefixed `vivienne--` came from vivienne's card. It also makes deletion clean — re-importing vivienne after a card update can prune the previous `vivienne--*` set with one warning.

### Frontmatter shape

Persisted `LoreEntry` markdown file:

```yaml
---
id: vivienne--camarilla
name: Camarilla
keywords: [Camarilla]
secondary_keys: [sect, pyramid]
selective_logic: and_any
constant: false
enabled: true
case_sensitive: false
match_whole_words: false
priority: 100
probability: 100
position: before_cast
at_depth: 0
scan_depth: null
comment: "Sect overview"
tags: [imported, from-card, vivienne]
related_characters: [vivienne]
secrecy: public
import_source:
  kind: sillytavern_character_book
  card_asset_id: vivienne
  source_index: 0
---

The Camarilla is the dominant sect of vampires…
```

The body is the entry's `content` after macro expansion.

`tags` gets `imported` and `from-card` automatically, plus the source character's slug, so it's easy to filter the library view. `import_source` is recorded so a re-import can find and replace prior entries.

### Selective logic

A direct port of SillyTavern's four-way enum, surfaced as a string for readability in YAML:

| Grimoire value | SillyTavern enum | Meaning |
|---|---|---|
| `and_any` | `AND_ANY` (0) | Fires when at least one primary key matches AND at least one secondary key matches |
| `and_all` | `AND_ALL` (3) | Fires when at least one primary key matches AND **every** secondary key matches |
| `not_all` | `NOT_ALL` (1) | Fires when at least one primary key matches AND **not all** secondary keys are present |
| `not_any` | `NOT_ANY` (2) | Fires when at least one primary key matches AND **no** secondary keys are present |

When `secondary_keys` is empty (or after slug filtering becomes empty), the entry falls back to the single-key contract: fires iff any primary key matches. The `selective_logic` value is ignored in that fallback path.

### Position mapping

SillyTavern's 8 position values collapse to four in Grimoire because Grimoire's tier system handles most of the same distinctions structurally.

| SillyTavern | Grimoire `position` | Where it lands |
|---|---|---|
| 0 = before_char | `before_cast` | Top of the Lock-in or Spotlight tier, ahead of the cast block |
| 1 = after_char | `after_cast` | Background tier, between cast and location |
| 2 = ANTop | `at_depth` (depth=AN_depth_default) | Depth-injection slot N posts from end (default depth 0) |
| 3 = ANBottom | `at_depth` (depth=AN_depth_default) | Same; SillyTavern's top/bottom distinction collapses since we don't have a separate Author's Note region in v1 |
| 4 = atDepth | `at_depth` (depth=entry.depth) | At entry-specified depth |
| 5 = EMTop / 6 = EMBottom | `before_cast` | Examples region doesn't exist in Grimoire; fall back to before_cast and warn |
| 7 = outlet | `before_cast` | Custom outlets not supported; fall back and warn |

`at_depth` injections are inserted into the recent-posts conversational region as a system-role message N posts from the end. If `at_depth` is larger than the available recent-posts count, the entry is dropped and a debug log line is written (this matches SillyTavern's behavior).

### Atomicity

Writing characters + lore + greetings in one call is best-effort but not transactional, because each is a separate file write through `LibraryService.create_entity`. The flow:

1. Validate everything in-memory first (resolve all slug conflicts, render all bodies).
2. Write the character file.
3. Write each greeting file. Failures here are logged + appended to `ImportResult.errors` but don't abort.
4. Write each lore file. Same.

If the character write fails, the whole import fails and no greetings/lore are written. If a greeting/lore write fails mid-batch, already-written files stay (the user can clean them up by slug prefix). This matches the existing partial-write tolerance in `LibraryService`.

## Richer LoreEntry trigger semantics (delta to spec 09)

### New fields on `LoreEntry`

Backwards-compatible — every field has a default that makes the row behave exactly like a pre-spec-19 entry.

```python
class LoreEntry(BaseModel):
    # existing fields
    setting_id: str
    id: str
    title: str
    body: str = ""
    tags: list[str] = []
    keywords: list[str] = []
    related_locations: list[str] = []
    related_factions: list[str] = []
    related_characters: list[str] = []
    secrecy: str = "public"

    # new in spec 19
    secondary_keys: list[str] = []
    selective_logic: SelectiveLogic = SelectiveLogic.AND_ANY
    constant: bool = False
    enabled: bool = True
    case_sensitive: bool = False
    match_whole_words: bool = False
    priority: int = 100
    probability: int = 100             # 0–100; 100 = always fires when keywords match
    position: LorePosition = LorePosition.BEFORE_CAST
    at_depth: int = 0
    scan_depth: int | None = None      # None = use setting-level default (current behavior = 1)
    comment: str = ""
    import_source: ImportSource | None = None
```

```python
class SelectiveLogic(StrEnum):
    AND_ANY = "and_any"
    AND_ALL = "and_all"
    NOT_ALL = "not_all"
    NOT_ANY = "not_any"

class LorePosition(StrEnum):
    BEFORE_CAST = "before_cast"
    AFTER_CAST = "after_cast"
    AT_DEPTH = "at_depth"
    ARCHIVE = "archive"

class ImportSource(BaseModel):
    kind: str                          # "sillytavern_character_book", "manual", ...
    card_asset_id: str = ""
    source_index: int = 0
```

### Trigger algorithm (`lore_for_post`)

Pseudocode (deterministic, no I/O after the initial library read):

```python
def lore_for_post(
    text: str,
    campaign_id: str,
    *,
    turn_id: str,
    min_length: int = 4,
    max_results: int = 5,
    scan_history: list[str] | None = None,   # for scan_depth > 1; default = [text]
) -> list[LoreEntry]:
    entries = list_for_composition(campaign_id, EntityKind.LORE)
    hits: list[LoreEntry] = []
    for entry in entries:
        if not entry.enabled:
            continue
        if entry.constant:
            hits.append(entry)
            continue
        scan_window = (scan_history or [text])[-(entry.scan_depth or 1):]
        haystack = "\n".join(scan_window)
        if not _primary_match(entry, haystack, min_length):
            continue
        if entry.secondary_keys and not _secondary_match(entry, haystack):
            continue
        if entry.probability < 100:
            seed = sha256(f"{entry.id}::{turn_id}").digest()[:8]
            roll = int.from_bytes(seed, "big") % 100   # 0..99
            if roll >= entry.probability:
                continue
        hits.append(entry)

    hits.sort(key=lambda e: (-e.priority, e.id))   # priority desc, stable
    return hits[:max_results]
```

Where:

- `_primary_match(entry, haystack, min_length)`:
  - For each key in `entry.keywords`, skip if `len(key) < min_length`.
  - If `entry.case_sensitive` is false, lowercase both sides for compare.
  - If `entry.match_whole_words` is true, match `\b<re.escape(key)>\b`; else substring `in`.
  - Returns True on first hit.
- `_secondary_match(entry, haystack)` evaluates the four-way enum exactly as defined in §Selective logic.

The result is sorted by priority desc, then by id for stable ordering across runs. `max_results` then truncates.

### Determinism contract

The probability check is the only step that involves a random draw. Seeding by `(entry.id, turn_id)` means: the same lore entry, on the same turn, always fires (or doesn't). Re-running the prompt builder for the same turn — for instance after a retry — picks up the same set of entries. Branch forks produce different `turn_id` strings (per spec 03 §Branches), so a forked branch may see different probability outcomes; that's intended.

`turn_id` is plumbed into `lore_for_post` by Context Builder; tests that call it directly may pass any stable string.

### Context Builder integration

The four `LorePosition` values land in three places in the canonical message order (spec 02):

- `before_cast` → added to the Spotlight tier, before the present-cast cards
- `after_cast` → added to the Background tier, after offscreen-character cards
- `at_depth` → inserted into the recent-posts conversational region as a `system`-role message N posts from the end
- `archive` → added to the Archive tier (alongside vector retrieval results)

Lore entries don't carry their own token budgets; they participate in their tier's allocation. Each entry's body is treated as a single chunk for budget accounting.

The current `lore_for_post` is called from Context Builder's archive-resolution step (per spec 02 §The build pipeline step 4); the new behavior splits the hits by `position` after retrieval and routes each subset to its tier.

### Frontmatter on-disk shape

The existing setting/lore frontmatter is preserved verbatim and the new keys are added as optional. Backwards compatibility: a lore file that doesn't mention any of the new keys parses to defaulted values. `_lore_from_entity` reads each new key with `fm.get(key, default)`.

```yaml
---
id: the-masquerade
title: The Masquerade
keywords: [masquerade, breach, mortal, exposure]
secondary_keys: []
selective_logic: and_any
constant: false
enabled: true
case_sensitive: false
match_whole_words: true
priority: 100
probability: 100
position: before_cast
at_depth: 0
scan_depth: 1
comment: ""
related_factions: [the-camarilla]
secrecy: common-knowledge-among-kindred
---
```

## Alternate greetings → Greeting library entries

### What lands where

For each ingested card:

- `first_mes` → `library/settings/<sid>/greetings/<char_slug>--default.md`
- `alternate_greetings[i]` → `library/settings/<sid>/greetings/<char_slug>--alt-<i+1:02>.md` (1-indexed for human readability)

Empty strings are skipped (with a warning).

### Frontmatter shape

```yaml
---
id: vivienne--default
name: "Meeting vivienne"
starting_location: null
starting_time: null
present_characters: [vivienne]
pov_character: null
mood: ""
tags: [imported, from-card, vivienne]
import_source:
  kind: sillytavern_first_mes
  card_asset_id: vivienne
  source_index: 0
---

"Darling, you look terrible."
```

For alternates:

```yaml
---
id: vivienne--alt-01
name: "Meeting vivienne (alt 1)"
present_characters: [vivienne]
tags: [imported, from-card, vivienne, alternate-greeting]
import_source:
  kind: sillytavern_alternate_greeting
  card_asset_id: vivienne
  source_index: 1
---

"Oh — back so soon?"
```

`starting_location`, `starting_time`, `pov_character`, `mood`, `season_constraint` are all null/empty by default. The user fills them in later via the Library UI; the Greeting is still usable without them, since the campaign creation flow only requires `present_characters` + `body`.

### Macro expansion

Greeting bodies go through the ingest-time macro pass like every other field. `{{char}}` is replaced with the character name; `{{user}}` is preserved for runtime resolution by Context Builder.

### Conflict resolution

Same rules as lore: existing greeting with the same id gets a `-2`, `-3`, … suffix appended, and a warning is added. Re-importing a card whose `alternate_greetings` list shrunk does **not** auto-delete the orphaned `vivienne--alt-N.md` files in v1 — the user is told to delete them manually. Auto-prune is in §Open questions.

### Body field

Currently the ingestor folds `alternate_greetings` into the character markdown body under an `## Alternate greetings` section (`grimoire.characters.ingest._compose_body`, line 534). With this spec, that section is **removed** from the body — the greetings are first-class entities, and duplicating them in the body would be confusing. A migration note explains this for any callers that read the body looking for the bullet list.

## Revised import flow

The user-facing API is unchanged in name but expanded in effect:

```python
async def import_character_card(
    self,
    payload: bytes,
    target_setting_id: str,
    *,
    options: IngestOptions | None = None,
) -> tuple[ImportResult, IngestedCharacterCard]:
```

The implementation walks the new pipeline:

```
1. ingest_character_card_v2(payload, options)
   ├─ parse PNG/charx/JSON
   ├─ run macro pass on every recognized text field
   ├─ build CharacterData (existing logic)
   ├─ parse character_book.entries[] → list[IngestedLoreEntry]
   └─ parse first_mes + alternate_greetings → list[IngestedGreeting]
2. (optional) enrich_with_llm — unchanged
3. _finalize_import(target_setting_id, ingested):
   ├─ write character markdown (existing)
   ├─ for each IngestedGreeting: LibraryService.create_entity(..., "greeting", ...)
   ├─ for each IngestedLoreEntry: LibraryService.create_entity(..., "lore", ...)
   └─ return ImportResult with created[]/skipped[]/warnings[]/errors[]
```

`ImportResult` already lists `created`, `updated`, `skipped`, `warnings`, `errors`. With this change, `created` carries character ids, greeting ids, and lore ids in source order. Existing callers that look at `result.created` to confirm a character landed need no change; new callers can filter by id prefix.

### New `IngestOptions` toggles

```python
class IngestOptions(BaseModel):
    # existing
    extract_relationships: bool = True
    keep_embedded_avatar: bool = True
    derive_image_prompt: bool = True
    role_default: CharacterRole = CharacterRole.MAJOR_NPC
    enrich_with_llm: bool = False
    setting_factions: list[str] = []
    setting_characters: list[str] = []
    avatar_dir: str | None = None

    # new in spec 19
    expand_macros: bool = True
    import_character_book: bool = True
    import_alternate_greetings: bool = True
    import_primary_greeting: bool = True   # the first_mes itself
```

All four new toggles default to True. Callers who want the pre-spec-19 behavior pass `expand_macros=False, import_character_book=False, import_alternate_greetings=False, import_primary_greeting=False`.

## Data model summary

New types introduced in this spec:

```python
# grimoire.types.characters
class IngestedLoreEntry(BaseModel):
    id: str                            # final lore id after slug+conflict resolution
    name: str
    body: str                          # post-macro-expansion content
    keywords: list[str]
    secondary_keys: list[str] = []
    selective_logic: SelectiveLogic = SelectiveLogic.AND_ANY
    constant: bool = False
    enabled: bool = True
    case_sensitive: bool = False
    match_whole_words: bool = False
    priority: int = 100
    probability: int = 100
    position: LorePosition = LorePosition.BEFORE_CAST
    at_depth: int = 0
    scan_depth: int | None = None
    comment: str = ""
    tags: list[str] = []
    related_characters: list[str] = []
    import_source: ImportSource

class IngestedGreeting(BaseModel):
    id: str
    name: str
    body: str
    present_characters: list[str]
    tags: list[str] = []
    import_source: ImportSource

class IngestedCharacterCard(BaseModel):
    # ... existing fields ...
    lore_entries: list[IngestedLoreEntry] = []
    greetings: list[IngestedGreeting] = []
```

Modified types:

```python
# grimoire.types.setting — LoreEntry extended (see §Richer LoreEntry above)
# grimoire.types.composition — Greeting unchanged
# grimoire.types.characters — IngestOptions gets four new toggles
```

## Storage and migration plan

### Library files

New files written under `library/settings/<sid>/lore/` and `library/settings/<sid>/greetings/` go through the existing `LibraryService.create_entity` path, which already handles slug normalization, watcher notification, and `library_index` upsert. No new file conventions.

### SQLite

`library_index` already stores `keywords` as JSON and arbitrary `frontmatter` as JSON. The new LoreEntry fields land inside the frontmatter blob — **no schema migration needed**. The existing FTS index over `(name, body, keywords)` keeps working.

### Caveat: re-running the import

If a user re-imports the same card after editing it:

- Character: the existing code raises `result.skipped` if the character file already exists. With this spec, that behavior is unchanged. A future "update existing character" toggle is left for §Open questions.
- Greetings/lore: id collisions append `-2`, `-3`, …. This is conservative and surfaces obvious duplicates to the user.

A `prune_previous_import(card_asset_id, target_setting_id)` helper is **not** part of v1 — see §Open questions for the rationale.

### Watcher

The watcher (`grimoire.watcher`) is unchanged; it picks up the new files via the existing `library_file_changed` event. The classifier already routes `lore/*.md` and `greetings/*.md` correctly.

## Test plan

### Unit tests

`backend/tests/characters/test_macros.py` (new file):

- Expand `{{char}}` to the character name; case-insensitive match
- Preserve `{{user}}` when `keep_user=True`; substitute with `the player` when False
- `{{random:a,b,c}}` returns one of the three options deterministically given a fixed seed
- `{{random:a,b,c}}` and `{{random:x,y,z}}` in the same field draw from the same RNG and produce different but reproducible picks
- `{{random:a::b::c}}` (double-colon delimiter) parses identically to comma form
- `{{pick:a,b,c}}` and `{{random:a,b,c}}` with the same seed pick the same item (alias semantics)
- `{{roll:1d6}}` returns a stable integer in `1..6`; `{{roll:2d6}}` returns `2..12`
- `{{roll:d20}}` (omitted N) defaults to one die
- `{{newline}}` becomes `\n`
- `{{trim}}` collapses one whitespace on each side
- `{{// comment text}}` strips the entire token
- Unknown macros (`{{foo}}`) pass through and produce a warning
- Empty input returns empty output, no warnings

`backend/tests/characters/test_ingest.py` (extended):

- Card with `{{char}}` in `first_mes` produces a Greeting whose body contains the character name
- Card with `{{user}}` in `description` keeps the literal `{{user}}` in the persisted character body
- Card with `character_book.entries=[...]` lands lore rows under the target setting, ids prefixed with the character slug
- Card with three alternate_greetings produces three greeting files plus the default
- Card with empty `first_mes` skips the default greeting and warns
- Card with `character_book.entries[i].enabled=false` is still imported with `enabled=false` (not dropped)
- Card with `position=2` (ANTop) maps to `at_depth` and a warning is recorded about the collapsed semantics
- Card with `position=5/6/7` (unsupported) falls back to `before_cast` and warns
- Re-importing the same card a second time results in `-2`-suffixed greeting/lore ids
- Importing with `import_character_book=False` writes the character but no lore entries; `IngestedCharacterCard.lore_entries` still populated for inspection
- Importing with `expand_macros=False` preserves literal `{{char}}` in the persisted body (escape hatch test)

`backend/tests/setting/test_lore_triggers.py` (new file):

- Disabled entries (`enabled=false`) never fire even on direct keyword match
- Constant entries (`constant=true`) fire on every call regardless of haystack
- `case_sensitive=true` distinguishes "Camarilla" from "camarilla"
- `match_whole_words=true` rejects "camarillas" as a hit for "Camarilla"
- `match_whole_words=false` accepts "camarillas" (current behavior)
- `secondary_keys=[sect]` with `selective_logic=and_any` requires both a primary key and at least one secondary key
- `selective_logic=and_all` requires every secondary key
- `selective_logic=not_any` rejects when any secondary key is present
- `selective_logic=not_all` rejects only when every secondary key is present
- `probability=50` with a fixed `turn_id` fires deterministically (assertion compares to the known SHA bucket)
- Two entries with the same priority order by id deterministically
- Higher-priority entries appear earlier in the result
- `scan_depth=3` searches the last three posts in `scan_history`
- `max_results` truncates after sort (priority is honored)

`backend/tests/setting/test_service.py` (extended):

- Existing `test_lore_for_post_extracts_triggers` still passes (back-compat sanity check)
- New parametrized test fixture validates all four `selective_logic` enum values

### Integration tests

`backend/tests/characters/test_import_integration.py` (new file):

- End-to-end: import a card, then `library.list_lore(setting_id)` returns the new entries, `library.list_greetings(setting_id)` returns the new greetings
- Campaign creation can pick one of the imported greetings by id and the orchestrator seeds the opening scene correctly (existing campaign create flow + new greetings)
- Context Builder pulls in a `before_cast` lore entry when the player input mentions one of its keywords (uses the existing Context Builder test fixtures + a seeded lore row)
- `{{user}}` in a Spotlight-tier message body is substituted with the active PC's display name at the end of `build()`

### Determinism / golden tests

`backend/tests/characters/test_ingest_golden.py` (extended):

- Importing the same canonical SillyTavern card twice yields byte-identical lore + greeting files
- A card with one `{{random}}` and one `{{pick}}` produces the same expanded text across re-runs

## Backwards compatibility

| Concern | Behavior |
|---|---|
| Existing lore files without new keys | Parse with defaulted values; `lore_for_post` returns identical results to pre-spec-19 |
| Existing character cards already imported | Unchanged on disk; only newly-imported cards get the new files |
| `library_index` / `campaign_content_index` schemas | Unchanged — new lore fields live in the frontmatter JSON column |
| `LibraryService.create_entity` callers | Unchanged signature |
| `setting.lore_for_post` callers | Existing positional call style preserved; `turn_id` is keyword-only with a default that uses the current scene's turn id, so callers don't have to thread it through |
| `import_character_card` callers | Return type unchanged (`tuple[ImportResult, IngestedCharacterCard]`); the result's `created` list grows |
| `IngestedCharacterCard` consumers | New fields added with defaults; existing serialization keys preserved |
| `_compose_body` callers | Body no longer contains the `## Alternate greetings` section; if any external code grepped for it, surface a warning in release notes |

## Open questions (deferred)

- **Auto-pruning previous imports.** Re-importing a card today appends `-2`/`-3` to the new lore/greeting ids. A flag `prune_previous_import=True` could find files matching `<char>--*.md` with `import_source.card_asset_id == card_asset_id` and delete those whose ids no longer correspond to a current entry. Useful for card-update workflows but loses any user edits. v2.
- **Recursion in lore activation.** SillyTavern recurses: a lore entry's content can trigger more lore entries' keywords. Grimoire's tier system already pins included content, so recursion would mostly grow the budget consumed. Could be added as `recurse: bool` on `LoreEntry` if a real card demonstrates the need.
- **Sticky / cooldown / delay.** SillyTavern's chat-loop features (an entry stays included for N messages after firing; can't refire for N messages after that; doesn't fire until N messages into the chat). The equivalent in Grimoire is the tier-pin mechanism Characters already exposes. Probably not worth porting field-for-field, but a `pin_for_turns: int` on a lore hit could be useful.
- **Vectorized lore.** SillyTavern's `vectorized: true` flag bypasses keyword matching and uses embedding similarity instead. Continuity already has hybrid keyword+vector search; reusing that infra for lore entries is a clean follow-up spec.
- **Per-greeting metadata in V3.** V3 cards can carry per-greeting `name`, `tags`, `mood`. v1 ignores these and uses an auto-derived name. If a community card relies on V3 greeting metadata, extract from `extensions.depth_prompt` shapes.
- **Author's Note as a first-class slot.** SillyTavern's Author's Note is a runtime user-edited string injected at a configured depth. Grimoire has no direct equivalent; the `at_depth` lore position is the structural cousin. A separate spec for per-campaign Author's Note as a steering knob is worth its own design pass.
- **`{{char}}` rebinding for multi-character scenes.** Today `{{char}}` is frozen at import to the card's owner. For a scene with three characters where one of them quotes the others' cards, the literal name is fine. If multi-character cards are added later, `{{char}}` may need late-binding too.
- **Macro-aware editing in the Library UI.** When the frontend lets a user edit lore/greeting bodies, should `{{user}}` be shown verbatim (true to the data) or rendered with the active PC name (true to the play experience)? Probably a UI toggle.
- **Token budgets per character_book.** SillyTavern's `character_book.token_budget` caps total lore from one card per turn. Could be honored as a `priority`-tier hint when packing the lore tier. Out of scope for v1 — deferred until we see a card where it matters.
- **Cross-setting lore imports.** If a card is imported into setting A, then the same card into setting B, both sets of lore co-exist with independent ids. That's fine. A "shared lore library" (lore visible to many settings) is a v2 concept that the Composition system already supports structurally.

## Out of scope (will not be added by this spec)

- Vector-search-based lore retrieval (separate spec)
- Slash commands / STScript
- Connection profiles, sampler presets, model routing
- Instruct-mode wrappers (Llama-3 / ChatML / Mistral)
- Prompt Manager-style drag-and-drop block reordering
- Personas as a separate entity (Grimoire uses Active PC)
- Quick Replies
- Regex post-processing rules on user input / AI output
- Author's Note as a first-class campaign-level slot
- Multi-character card formats
- Sticky / cooldown / delay-until-recursion / group scoring
- WI scan-scope flags (matchScenario, matchCreatorNotes, matchPersonaDescription)

## Implementation notes for the PR

A single PR can land all four items if scoped tightly. Suggested order of commits:

1. `macros.py` utility + `test_macros.py`
2. `LoreEntry` field extension + `_lore_from_entity` updates + `test_lore_triggers.py`
3. `lore_for_post` algorithm update + back-compat test
4. Ingestor: `character_book` parsing + `IngestedLoreEntry` type + greetings parsing + `IngestedGreeting` type
5. Service: `import_character_card` writes greetings + lore via LibraryService
6. Context Builder: late `{{user}}` substitution pass + position routing for lore hits
7. Integration test exercising the full path

Total estimated diff: ~600 lines of source + ~700 lines of tests.

Files touched:

- `backend/src/grimoire/characters/macros.py` (new)
- `backend/src/grimoire/characters/ingest.py` (extended)
- `backend/src/grimoire/characters/service.py` (extended `_finalize_import`)
- `backend/src/grimoire/characters/__init__.py` (re-exports)
- `backend/src/grimoire/types/characters.py` (new types + IngestOptions toggles)
- `backend/src/grimoire/types/setting.py` (extended LoreEntry, new enums)
- `backend/src/grimoire/setting/service.py` (extended `lore_for_post`, `_lore_from_entity`)
- `backend/src/grimoire/context/builder.py` (late `{{user}}` substitution; lore-by-position routing)
- `backend/tests/characters/test_macros.py` (new)
- `backend/tests/characters/test_ingest.py` (extended)
- `backend/tests/characters/test_import_integration.py` (new)
- `backend/tests/setting/test_lore_triggers.py` (new)
- `backend/tests/setting/test_service.py` (extended)

No frontend changes required for the import path itself. A separate follow-up can surface alternate greetings in the campaign-create flow; this spec only ensures the data is there.
