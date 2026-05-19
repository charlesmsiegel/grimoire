## Character Card Imports (SillyTavern v2/v3) — Design

> **Status:** Design ready for implementation plan. Independent of the other `specs/new/` features.

**Source idea:** `specs/new/card-imports.md`
**Module:** `backend/src/grimoire/characters/`, `backend/src/grimoire/world/`, `backend/src/grimoire/context/`

## Purpose

The current ingestor (`grimoire.characters.ingest`) parses SillyTavern character cards (v2 PNG / charx / v3 JSON) into a `CharacterData` and preserves the raw `character_book` + `alternate_greetings` + `system_prompt` etc. on `IngestedCharacterCard` — but never persists them. Macros (`{{char}}`, `{{user}}`, `{{random}}`, `{{roll}}`, …) remain literal in imported text. This spec closes those gaps and defines the canonical import shape.

## Scope (what changes)

1. **Macro engine** — new `grimoire.characters.macros` module: closed set of macros, deterministic seeding, late-stage `{{user}}` substitution in Context Builder.
2. **`character_book` → setting lore** — walk entries, write to `library/settings/<sid>/lore/<char_slug>--<entry_slug>.md` via `LibraryService.create_entity`.
3. **Richer `LoreEntry`** — secondary_keys, selective_logic, constant, enabled, case_sensitive, match_whole_words, priority, probability, position, at_depth, scan_depth, comment, import_source. Backwards-compatible (existing files parse unchanged with sensible defaults).
4. **`lore_for_post` overhaul** — implement the full SillyTavern-ish scoring algorithm; position routing in Context Builder.
5. **`first_mes` + `alternate_greetings` → Greeting library entries** — first-class entities in `library/settings/<sid>/greetings/`.
6. **Discarded input report** — `system_prompt`, `post_history_instructions`, V3 extensions logged in a per-import markdown report.

## 1. Macro substitution

Closed set, expanded once at ingest:

| Macro | Behaviour |
|---|---|
| `{{char}}` | Replaced with the card owner's name (case-insensitive original match preserved) |
| `{{user}}` | Preserved literal at ingest; substituted by Context Builder at runtime against the active PC name (else `"the player"`) |
| `{{random:a,b,c}}` / `{{random:a::b::c}}` | Seeded random pick |
| `{{pick:...}}` | Alias of `random` (same determinism) |
| `{{roll:NdM}}` | Sum of N M-sided rolls, seeded |
| `{{newline}}` | `\n` |
| `{{trim}}` | Empty; one whitespace char on each side consumed |
| `{{// comment}}` | Stripped |

**Public API:**
```python
def expand_macros(
    text: str,
    *,
    char_name: str,
    card_asset_id: str,
    field_name: str,
    keep_user: bool = True,
) -> tuple[str, list[str]]:
    """Returns (expanded_text, warnings)."""
```

**Determinism (the open question):** The macro_index used for seeding is the **position-in-field** counter incremented as we walk left-to-right. Same field → same seed sequence → same expansions across runs. Editing the field changes the index for subsequent macros (intended — if the user reorders macros, the seeds change with them).

Seed: `int.from_bytes(SHA-256(f"{card_asset_id}::{field_name}::{macro_index}".encode()).digest()[:8], "big")`. Fed into `random.Random(seed)` for pick/roll.

**Nesting:** explicitly disallowed in v1. A macro whose argument contains `{{` triggers a warning and is left literal (no recursive expansion). Forbidding nesting avoids the infinite-loop class of bug entirely.

**`{{trim}}` semantics (the open question):** The macro itself is deleted, then one whitespace character on each side is consumed (greedy single char). Consecutive trims chain: `"a {{trim}}{{trim}} b"` → `"ab"`. Implementation: regex `r" ?\{\{trim\}\} ?"` (one optional leading + one optional trailing space).

**Unknown macros** pass through unchanged with a warning per field on `IngestedCharacterCard.warnings`. Future macro families (`{{calendar}}`, `{{pc:pronoun}}`) get the same warning today, then get explicit handlers when their specs land.

**Reserved future namespaces:** `{{calendar}}`, `{{pc:*}}`, `{{weighted:...}}` are reserved (warned-on-unknown but documented as future), so card authors don't paint themselves into a corner.

## Late-stage `{{user}}` substitution

Context Builder gains a final pass after assembly:

```python
def _resolve_runtime_macros(messages: list[Message], active_pc: Character | None) -> list[Message]:
    pc_name = active_pc.name if active_pc else "the player"
    return [m.replace_content(m.content.replace("{{user}}", pc_name)) for m in messages]
```

Pure string replace, idempotent. Touches `Message.content` only. Called at the end of `build()` after all tier items are flattened (`backend/src/grimoire/context/builder.py:build` end).

## 2. `character_book` → setting lore

Walk `IngestedCharacterCard.character_book.entries[]`:

```python
@dataclass
class IngestedLoreEntry:
    source_index: int           # position in entries[]
    name: str | None
    keys: list[str]
    body: str
    secondary_keys: list[str]
    selective_logic: SelectiveLogic   # and_any | and_all | not_all | not_any
    constant: bool
    enabled: bool
    case_sensitive: bool
    match_whole_words: bool
    priority: int
    probability: int
    position: LorePosition       # before_cast | after_cast | at_depth | archive
    at_depth: int | None
    scan_depth: int | None
    comment: str
    import_source: ImportSource
```

Map each to a `library/settings/<sid>/lore/<char_slug>--<entry_slug>.md` file via `LibraryService.create_entity(kind="lore", ...)`. `char_slug` derives from the card name; `entry_slug` from the entry name or first slugifiable key, falling back to `entry-<source_index>`.

**Conflict resolution:** suffix `-2`, `-3`, … up to 99, with a warning. Never overwrite. Cap at 99 because a card with 100 colliding slugs is almost certainly malformed.

Frontmatter records `import_source: {kind: sillytavern_character_book, card_asset_id, source_index}` and `tags: [imported, from-card, <char_slug>]`. Macro pass applied to body, `keys`, `secondary_keys`, `comment`.

**Lore scope heuristic (the open question):** v1 is **always setting scope** (the default). The "character scope" path is documented in the spec but not implemented yet — adding it requires a `lore/<char_slug>/...` directory + Library indexer recognition of nested lore, and a UI affordance to switch. Out of scope for v1; the import dialog mentions "Character-scoped lore coming soon."

## 3. Richer `LoreEntry` model

Extend the Pydantic model in `backend/src/grimoire/types/world.py:LoreEntry`. Backwards-compatible defaults so existing files parse unchanged:

```python
class LoreEntry(BaseModel):
    id: str
    title: str
    body: str
    tags: list[str] = []
    keywords: list[str] = []
    related_locations: list[str] = []
    related_factions: list[str] = []
    related_characters: list[str] = []
    secrecy: str = "open"

    # New, all optional with safe defaults:
    secondary_keys: list[str] = []
    selective_logic: SelectiveLogic = SelectiveLogic.AND_ANY
    constant: bool = False
    enabled: bool = True
    case_sensitive: bool = False
    match_whole_words: bool = False
    priority: int = 100
    probability: int = 100
    position: LorePosition = LorePosition.AFTER_CAST   # safe default: background tier
    at_depth: int | None = None
    scan_depth: int | None = None                       # None = entire post history
    comment: str = ""
    import_source: ImportSource | None = None
```

The default `position=AFTER_CAST` (the open question) means existing hand-written lore files land in the Background tier in Context Builder. That's a behavior change from today (where lore-for-post is filtered by keyword and dumped into the Archive tier indiscriminately). Migration note in the COMPLETED doc will call this out.

## 4. `lore_for_post` overhaul

Replaces `backend/src/grimoire/world/service.py:lore_for_post` (line 647):

```python
async def lore_for_post(
    self,
    setting_id: str,
    scene: Scene,
    *,
    turn_id: str | None = None,
    max_results: int | None = None,
) -> list[LoreEntry]:
    cfg = self.config.lore
    turn_id = turn_id or scene.current_turn_id

    all_lore = await self.library.list_in_setting(setting_id, "lore")
    haystack = self._build_haystack(scene, scan_depth=None)   # full history; per-entry scan_depth narrows below
    hits: list[tuple[int, LoreEntry]] = []                     # (priority, entry)

    for entry in all_lore:
        if not entry.enabled:
            continue
        if entry.constant:
            hits.append((entry.priority, entry))
            continue
        local_haystack = self._build_haystack(scene, scan_depth=entry.scan_depth)
        if not self._primary_keyword_match(entry, local_haystack):
            continue
        if entry.secondary_keys and not self._evaluate_selective_logic(entry, local_haystack):
            continue
        if not self._probability_check(entry, turn_id):
            continue
        hits.append((entry.priority, entry))

    hits.sort(key=lambda x: (-x[0], x[1].id))
    if max_results is None:
        max_results = cfg.max_lore_in_archive
    return [e for _, e in hits[:max_results]]
```

**Probability roll** (deterministic):
```python
def _probability_check(self, entry: LoreEntry, turn_id: str) -> bool:
    if entry.probability >= 100:
        return True
    digest = hashlib.sha256(f"{entry.id}::{turn_id}".encode()).digest()
    roll = int.from_bytes(digest[:4], "big") % 100
    return roll < entry.probability
```

**Selective logic** (the open question on empty `secondary_keys`): if `secondary_keys == []`, treat as "no secondary requirement" regardless of `selective_logic` — return True. The four logics only meaningfully apply when there is a secondary list.

**`scan_depth` semantics** (the open question):
- `None`: scan entire post history of the scene.
- `0`: scan nothing — useful with `constant: true` to declare a lore entry that fires regardless of context (rare but valid).
- `> 0`: scan the last N posts.
- Negative: rejected at write time.

**Position routing** (Context Builder):

```python
def _route_lore_to_tier(entry: LoreEntry, scene: Scene) -> _TierItem:
    if entry.position == LorePosition.BEFORE_CAST:
        return _TierItem(tier=SPOTLIGHT, section="lore-before", text=..., priority=8)
    if entry.position == LorePosition.AFTER_CAST:
        return _TierItem(tier=BACKGROUND, section="lore-after", text=..., priority=5)
    if entry.position == LorePosition.AT_DEPTH:
        # inject into recent-posts as a system message at depth `entry.at_depth`
        return _TierItem(tier=LOCK_IN, section=f"lore-depth-{entry.at_depth}", text=..., priority=3)
    return _TierItem(tier=ARCHIVE, section="lore-archive", text=..., priority=2)
```

## 5. `first_mes` + `alternate_greetings` → Greetings

- `first_mes` → `library/settings/<sid>/greetings/<char_slug>--default.md`.
- `alternate_greetings[i]` → `library/settings/<sid>/greetings/<char_slug>--alt-<i+1:02>.md`.

Frontmatter:
```yaml
id: <char_slug>--default
name: "Default greeting from <Character Name>"
present_characters: [<char_slug>]
tags: [imported, from-card, <char_slug>, alternate-greeting]   # "alternate-greeting" only on alts
import_source:
  kind: sillytavern_first_mes      # or sillytavern_alternate_greeting
  card_asset_id: ...
  source_index: 0                  # 0 for default; 1.. for alts
```

Body goes through the macro pass: `{{char}}` resolves to the card name; `{{user}}` preserved.

The existing `## Alternate greetings` section in the character body is removed — greetings are now first-class entities. Release-note this in the COMPLETED doc.

## 6. Service wiring

`CharactersService.import_character_card` (`backend/src/grimoire/characters/service.py:1324`) gets a new method signature:

```python
async def import_character_card(
    self,
    payload: bytes | dict,
    target_setting_id: str,
    *,
    options: IngestOptions | None = None,
) -> tuple[ImportResult, IngestedCharacterCard]: ...
```

Where `IngestOptions` (`backend/src/grimoire/types/characters.py:227`) gains:

```python
class IngestOptions(BaseModel):
    # existing:
    extract_relationships: bool = True
    keep_embedded_avatar: bool = True
    derive_image_prompt: bool = True
    enrich_with_llm: bool = False

    # new (all default True):
    expand_macros: bool = True
    import_character_book: bool = True
    import_alternate_greetings: bool = True
    import_primary_greeting: bool = True
```

Atomicity (the open question on partial failure): validate everything in memory first → write character → write greetings → write lore. Character write failure aborts the import (transaction reverted). Greeting / lore write failures **append to `ImportResult.errors` without aborting**: the import returns successfully with errors listed. Rationale: a partial import is more useful than no import (the user gets the character + most greetings; the failed lore entries can be re-imported individually).

## 7. Discarded inputs + import report

| Field | Disposition |
|---|---|
| `system_prompt` | Saved to **campaign-scoped** `system_addendum.yaml` on a per-campaign basis; never written into the character card. Becomes a feature: "Add this card's system prompt to campaign X." |
| `post_history_instructions` | Discarded with warning ("anti-pattern: card author rewriting assembled prompt"). |
| V3 `extensions.depth_prompt` | Discarded with warning. |
| V3 `extensions.risuai`, `chub`, `regex_scripts` | Discarded; regex scripts rejected by policy. |
| Avatar PNG metadata | Stripped of non-essential chunks; only the `chara` / `ccv3` tEXt chunk preserved. **Always**, regardless of `keep_embedded_avatar` (privacy: we don't want to retain creator-tracking metadata). |

Each import run writes a markdown report to `data/library/imports/<timestamp>-<char_slug>.md` listing every file created, field discarded, collision resolved. The path is **outside** the campaign directory — `data/library/imports/` is a shared log. Retained indefinitely (small files; useful audit; manual deletion if disk space matters).

## REST + service surface

```
POST   /library/settings/{sid}/imports/sillytavern                # body: card bytes (multipart)
POST   /library/settings/{sid}/imports/sillytavern/preview         # parse only; returns IngestedCharacterCard
POST   /library/settings/{sid}/imports/sillytavern/commit          # commit a previewed card
GET    /library/imports                                            # list import reports
GET    /library/imports/{report_id}                                # read a specific report
```

The preview/commit split lets the UI render the parsed character + greetings + lore + warnings before the user accepts. Commit takes the preview's id and the (possibly edited) `IngestOptions`.

## Configuration

```yaml
world:
  lore:
    max_lore_in_archive: 5         # cap for lore_for_post
    keyword_min_length: 3
characters:
  imports:
    auto_strip_avatar_metadata: true
    write_import_reports: true
    char_book_scope: setting       # only setting in v1; character reserved for v2
```

## Cross-spec hooks

- **None in `specs/new/`** — this spec is self-contained.
- **Shipped specs**: Context Builder for the `_resolve_runtime_macros` pass; Library for entity creation/conflict resolution; World for lore.

## Failure handling

| Failure | Behavior |
|---|---|
| Card bytes don't parse | 400 with detected format + parse error |
| Required field missing (no name) | 400 with field list |
| Character ID collision in library | Suffix `-2`, `-3`, …, up to 99; surface in `ImportResult.warnings` |
| Greeting collision | Same suffix strategy; non-fatal |
| Lore collision | Same suffix strategy; non-fatal |
| Macro syntax error (e.g., malformed `{{random:}}`) | Leave literal; one warning per field |
| Avatar bytes corrupt | Skip avatar; warn; character still imported |
| Disk full mid-write | Roll back (delete partial files); 500 |

## Test wiring

`backend/tests/characters/test_macros.py` (new):
- Each macro type expands deterministically with the seed scheme.
- `{{trim}}` whitespace consumption (single + chained).
- Unknown macro → warning + literal pass-through.
- Nesting → warning + outer-only expansion.
- `{{user}}` preserved at ingest.

`backend/tests/context/test_runtime_macros.py`:
- `{{user}}` substituted at end of `build()`.
- Idempotent (running twice yields the same output).
- No active PC → `"the player"`.

`backend/tests/world/test_lore_for_post.py`:
- Enabled false → skipped.
- Constant → fires always.
- Primary + secondary key combinations across all four `selective_logic` values.
- Probability check is deterministic per `(entry.id, turn_id)`.
- `scan_depth` truncation.
- Priority sort + max_results truncation.
- Position routing to correct tier.

`backend/tests/characters/test_ingest_v2.py` (extend existing):
- character_book → lore files written with correct frontmatter.
- alternate_greetings → greeting files written.
- system_prompt → campaign system_addendum.yaml (or "not associated with a campaign" warning).
- Avatar metadata stripped.
- Atomicity: character write fail aborts; greeting fail doesn't abort.

`backend/tests/characters/test_import_report.py`:
- Report markdown contains every disposition.
- Report path is well-formed.

## Wiring touchpoints

- `backend/src/grimoire/characters/macros.py` (new): macro engine.
- `backend/src/grimoire/characters/ingest.py:218–280` and surrounding: thread macros through every text field; populate `IngestedLoreEntry`.
- `backend/src/grimoire/characters/service.py:1324`: extended `import_character_card`; orchestrate writes to library for characters + greetings + lore.
- `backend/src/grimoire/context/builder.py:build`: append `_resolve_runtime_macros` pass.
- `backend/src/grimoire/types/world.py:LoreEntry`: extended schema; new `LorePosition`, `SelectiveLogic` enums.
- `backend/src/grimoire/world/service.py:lore_for_post`: full rewrite per algorithm above.
- `backend/src/grimoire/context/builder.py:_route_lore_to_tier`: new helper; position-based tier routing.
- `backend/src/grimoire/api/library.py`: imports preview/commit routes.
- `frontend/src/routes/library/Imports/`: preview UI, options form, commit confirmation, report viewer.

## Out of scope (v1)

Vectorized lore (`vectorized: true`), recursion / sticky / cooldown / delay-until-recursion, group scoring / weights, scan-scope flags (`matchScenario`, `matchCreatorNotes`, `matchPersonaDescription`), slash commands / STScript, connection profiles / sampler presets / instruct templates, multi-character card formats, late-rebinding `{{char}}`, auto-pruning previous imports, live bidirectional sync, SillyTavern-format export, character-scoped lorebook.
