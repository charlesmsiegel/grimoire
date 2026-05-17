# Characters — Design (Shipped)

> Captures the Characters module as actually built. The companion `2026-05-17-characters-COMPLETED.md` covers everything from the original `specs/08-characters.md` that did **not** land.

**Commit:** `cce2113` — "Implement Characters module (task 12)" (followed by `cd15e0e`, `0ba0ef4`)
**Module:** `backend/src/grimoire/characters/`
**Tests:** `backend/tests/characters/test_service.py`, `backend/tests/characters/test_ingest.py`

## Purpose

Characters is the behavior facade over the Library's character storage. The Library owns the on-disk `worlds/<world>/characters/<id>.md` files; this service adds the character-specific behaviors that don't generalize to other entity kinds: voice anchors and drift detection, context-tier recommendation, PC roster and multi-PC turn semantics, compressed card views, campaign-scoped relationships, mechanical capability surfacing, and SillyTavern / charx / plaintext imports.

It does not own data. CRUD is forwarded to `LibraryService`; sheets and capabilities come from `MechanicsService`; campaign-scoped state lives in `StateStore`.

## Module surface

```
backend/src/grimoire/characters/
  __init__.py        — public re-exports
  service.py         — CharactersService (the entire facade lives here)
  drift.py           — DriftChecker protocol, HeuristicDriftChecker, CallableDriftChecker
  ingest.py          — Character Card V2/V3 ingestor (PNG / charx / JSON)
  imports.py         — thin wrappers (parse_sillytavern, parse_charx, parse_plaintext)
  views.py           — render_full / _compressed / _voice_only / _capsule, rotate_samples
  errors.py          — CharactersError, CharacterNotFoundError, ImportError_, PromotionError
```

Types live in `backend/src/grimoire/types/characters.py`: `Character`, `CharacterData`, `CharacterRole`, `VoiceAnchor`, `ImagePromptTemplate`, `CharacterImage[Kind]`, `StructuralRelationship`, `ResolvedCharacter`, `PCEntry`, `CharacterFilter`, `AwarenessState`, `RelationshipState`, `RelationshipEvent`, `DriftReport`, `ImportResult`, `IngestOptions`, `IngestedCharacterCard`.

## Construction

```python
CharactersService(
    library: LibraryService,
    mechanics: MechanicsService,
    *,
    post_fetcher: PostFetcher | None = None,        # async (scene_id) -> list[Post]
    drift_checker: DriftChecker | None = None,      # defaults to HeuristicDriftChecker
    drift_threshold: float = 0.4,
    ingest_llm: LLMEnrichCallable | None = None,
)
```

`post_fetcher` is the Scene Manager hook used by `check_drift` when callers don't pass posts directly. `drift_checker` is the pluggable scorer — heuristic by default; production wires an LLM-backed callable via `set_drift_checker`. `ingest_llm` is the optional refinement pass for card imports.

Wired into the app in `backend/src/grimoire/main.py:136` (`CharactersService(container.library, container.mechanics)`).

## Public API (as shipped)

```python
class CharactersService:
    # CRUD (delegated to LibraryService)
    async def list_in_world(world_id) -> list[Character]
    async def get(world_id, character_id) -> Character
    async def create(world_id, payload: CharacterData) -> Character
    async def update(world_id, character_id, patch: dict) -> Character
    async def delete(world_id, character_id) -> None

    # Emergent (campaign-local) characters
    async def create_emergent(campaign_id, payload, *, source=...) -> str
    async def update_emergent(campaign_id, character_id, patch, *, source=...) -> Character
    async def delete_emergent(campaign_id, character_id) -> None

    # Library overrides
    async def upsert_override(campaign_id, character_ref, patch, *, source=...) -> None

    # Resolution
    async def resolve(character_ref, campaign_id) -> ResolvedCharacter
    async def list_for_campaign(campaign_id, filter=None) -> list[ResolvedCharacter]

    # Cross-world variants
    async def cross_world_lookup(character_id, exclude_world=None) -> list[Character]

    # Views
    async def get_full_card(ref, campaign_id) -> str
    async def get_compressed_card(ref, campaign_id) -> str
    async def get_voice_only(ref, campaign_id) -> str
    async def get_capsule(ref, campaign_id) -> str

    # Tier
    async def recommend_tiers(scene, campaign_id=None) -> dict[CharacterRef, ContextTier]
    async def pin_tier(ref, campaign_id, tier) -> None

    # Drift
    async def check_drift(ref, campaign_id, *, window=10, recent_posts=None) -> DriftReport
    async def drift_corrective_context(ref, campaign_id) -> str
    def set_drift_checker(checker) -> None

    # State
    async def update_state(ref, campaign_id, state, *, source=...) -> None
    async def mark_screen_time(ref, campaign_id, turn_id) -> None
    async def get_state(ref, campaign_id) -> CharacterState

    # PCs
    async def list_pcs(campaign_id) -> list[PCEntry]
    async def add_pc(campaign_id, character_ref, name, owner="local") -> PCEntry
    async def remove_pc(campaign_id, character_ref) -> None
    async def set_active_pc(campaign_id, character_ref) -> None
    async def active_pc(campaign_id) -> CharacterRef | None

    # Per-PC scene
    async def current_scene_for_pc(campaign_id, character_ref) -> str | None
    async def set_current_scene_for_pc(campaign_id, character_ref, scene_id) -> None

    # Multi-PC turn semantics
    async def present_pcs_in_scene(scene, campaign_id=None) -> list[PCEntry]
    async def should_auto_respond(scene, campaign_id=None) -> bool
    async def pending_pc_inputs_since_last_advance(scene, posts) -> list[Post]

    # Capabilities
    async def capabilities_of(ref, campaign_id) -> list[Capability]

    # Relationships (campaign-scoped)
    async def get_relationships(ref, campaign_id, *, branch_id=None) -> list[dict]
    async def update_relationship(from_ref, to_ref, campaign_id, delta,
                                  *, branch_id=None, types=None, turn_id=None) -> dict

    # Promotion
    async def promote_to_library(campaign_id, character_id, target_world_id,
                                 *, source=..., delete_emergent=False) -> str

    # Imports
    async def import_sillytavern(card: bytes, target_world_id, *, options=None) -> ImportResult
    async def import_charx(charx_bytes: bytes, target_world_id, *, options=None) -> ImportResult
    async def import_plaintext(text: str, target_world_id) -> ImportResult
    async def import_character_card(payload, target_world_id, *, options=None) \
        -> tuple[ImportResult, IngestedCharacterCard]
    async def add_character_image(world_id, character_id, image,
                                  *, image_bytes=None, source=...) -> Character

    # Search
    async def search(query, world_id=None, scope="all", campaign_id=None) -> list[Character]
```

The shape closely mirrors spec 08 §Interface. Notable adaptations vs. the spec text:

- `update_state` takes a single `CharacterState` payload (no `branch_id` positional — it lives on the state dataclass).
- `set_current_scene_for_pc` accepts a `scene_id: str`, not a `SceneRef`.
- `present_pcs_in_scene` / `should_auto_respond` take a `Scene` object plus optional `campaign_id` (the scene already carries it).
- `recommend_tiers` returns the per-character mapping for a single scene; see "Tier recommendation" for what's actually computed.
- `pending_pc_inputs_since_last_advance(scene, posts)` is host-driven — the caller supplies the post list, the service applies the threshold from `scene.last_advance_at_post`.

## Character schema (as shipped)

`Character` (`types/characters.py:83`) matches spec 08 with these refinements:
- `voice.voice_register` instead of `voice.register` (Python keyword avoidance); on-disk frontmatter is still `voice: { register: ... }` — round-tripped in `_frontmatter_from_payload` (`service.py:1149`).
- `images: list[CharacterImage]` gallery in addition to the single `image: ImagePromptTemplate` template. Each entry carries `kind` (portrait / avatar / expression / pose / scene / reference), `description` ("when to use this"), `tags`, optional `seed` + `prompt_used` for generated art, and a `source` string for provenance.
- `file_path`, `file_mtime`, `version` exist on the model but are populated by the Library, not Characters.

`CharacterState` (`types/state.py`) carries the spec 08 §Per-campaign character state fields plus `current_scene_id` (for per-PC scene tracking) and `updated_at_turn`.

## CRUD path

`create` / `update` / `delete` shape a frontmatter dict from `CharacterData` (`_frontmatter_from_payload`, `service.py:1146`) and call into `LibraryService.create_entity` / `update_entity` / `delete_entity` with `kind="character"`. Reads project the `LibraryEntity` back into `Character` via `_character_from_entity` → `_character_from_frontmatter` (`service.py:1084`-`1143`).

`create_emergent` writes through `StateStore.write_emergent` to the campaign's `emergent/character/<id>.md` tree and returns a `campaign:emergent/character/<id>` reference. `update_emergent` re-reads + merges the frontmatter patch + body. `delete_emergent` removes the file directly via `emergent_path(...)`.

`upsert_override` requires a `library:worlds/<w>/characters/<id>` ref (anything else raises `CharactersError` from `_library_id_from_ref`) and writes a campaign-local override via `StateStore.write_override`.

## Resolution cascade

`resolve(ref, campaign_id)` (`service.py:244`):

1. `_parse_character_ref` (`service.py:1341`) accepts `library:worlds/<w>/characters/<id>`, `campaign:emergent/character/<id>`, bare `emergent/<...>`, and bare `worlds/<w>/characters/<id>`.
2. **Emergent path:** read via `store.get_emergent(...)`, raise `CharacterNotFoundError` if missing; build a single-entry `ResolutionSource(layer=EMERGENT, scope="campaign-local")` chain.
3. **Library path:** call `LibraryService.resolve(...)` with the asset id; that returns a `LibraryEntity` with the full `source_chain` (cascade of campaign override → world entity) and `overrides_applied` list.
4. Load campaign-scoped `CharacterState` via `_load_state` (defaults to a zero-valued state if no row exists).
5. Ask Mechanics for `capabilities_of(...)` — returns `[]` when the campaign has no active mechanics module.
6. Pack into `ResolvedCharacter(character, current_state, capabilities, source_chain, overrides_applied)`.

`list_for_campaign(campaign_id, filter=None)` iterates `library.list_for_composition(campaign_id, "character")` and emergent rows; `_passes_filter` / `_passes_resolved_filter` apply `CharacterFilter` (roles, tags, world_ids, name_contains).

## Voice anchors and drift detection

`VoiceAnchor` holds `summary`, `voice_register`, `samples[]`, `speech_patterns[]`, `address_terms{}`, `dos[]`, `donts[]`. The on-disk YAML uses `register` (the more natural name); the service maps in and out.

`check_drift(ref, campaign_id, *, window=10, recent_posts=None)`:

1. Resolve the character (also gives us its current `CharacterState`).
2. Source posts: prefer the explicit `recent_posts` argument; else fall back to `post_fetcher(state.current_scene_id)` (taking the last `window` posts); else empty.
3. Hand `DriftInput(character, recent_posts, window)` to the configured `DriftChecker.evaluate(...)`.
4. Persist `report.drift_score` back to `CharacterState` via `_save_state(..., source="characters:drift-check")`.

`drift_corrective_context(ref, campaign_id)` returns `""` when the persisted score is below `drift_threshold`, otherwise renders the voice-reminder block via `drift._corrective_text` (summary + dos + don'ts + first 3 samples + any cached evidence).

### `HeuristicDriftChecker` (default)

Cheap, deterministic. Computes Jaccard overlap between the sample/dos/summary vocabulary and the recent post vocabulary (tokens ≥ 3 chars), inverts to a "distance" score, and adds `0.25` per forbidden phrase from `donts` found verbatim in the recent text. Score capped at `1.0`, rounded to 4dp. No external calls; works without an LLM.

### `CallableDriftChecker`

Adapter wrapping any `async (character, recent_posts, window) -> DriftReport` callable. `set_drift_checker` detects callables vs. protocol-shaped objects and wraps as needed.

## Tier recommendation

`recommend_tiers(scene, campaign_id=None)` (`service.py:353`) currently applies the spec rules **partially**:

- Present in the scene (`scene.present_character_refs`) → `ContextTier.SPOTLIGHT`.
- User tier pin (from `CharacterState.tier_pin`) wins over the spotlight assignment.

`pin_tier(ref, campaign_id, tier)` writes the pin into `CharacterState`. The "recent mentions → background", "open commitments → at least background", and "inactivity → demotion over time" rules from spec 08 §Tier management are not yet computed — see the remaining doc.

## PCs and multi-PC

PC roster lives in the `pcs` table (`StateStore.list_pcs/add_pc/remove_pc/set_active_pc`). `CharactersService` maintains an in-process `_active_pc: dict[campaign_id, character_ref]` fast-path that mirrors the DB; on `add_pc` the first PC is auto-promoted to active, and `set_active_pc` persists through to DB so the choice survives restart (`cd15e0e`).

`present_pcs_in_scene(scene, ...)` unions `scene.present_pc_refs` with PCs from the roster that appear in `scene.present_character_refs`.

`should_auto_respond(scene, ...)` returns `True` when ≤1 PC is present. The Orchestrator uses this to decide whether to LLM-respond immediately or wait for an explicit `advance`.

`pending_pc_inputs_since_last_advance(scene, posts)` filters `posts` to PC-authored entries whose `order_in_scene > scene.last_advance_at_post`.

Per-PC current scene is stored on `CharacterState.current_scene_id`; `current_scene_for_pc` reads it, `set_current_scene_for_pc` writes it.

## Cross-world variants

`cross_world_lookup(character_id, exclude_world=None)` calls `LibraryService.variants_of(character_id, "character")` and projects to `Character`. Identity is by shared `asset_id` across worlds — no `family_id` field, matching spec 08 §Cross-world variant lookup.

## Compressed views

`views.py` renders four depths from a `Character`:

- `render_full` — name + aliases + age + tags + description + body + voice block.
- `render_compressed` — name(aliases) — role / description / voice summary / one canonical sample.
- `render_voice_only(character, max_samples=3)` — voice anchor only (summary, register, patterns, samples, dos, don'ts, address terms).
- `render_capsule` — single line: `Name · role · first-tag`.

`rotate_samples(voice, *, seed)` rotates the sample list deterministically by `seed % len(samples)` — used for sample rotation in repeated prompts.

The `Characters` service wraps each renderer with `get_full_card` / `get_compressed_card` / `get_voice_only` / `get_capsule` (all resolve first, then render). Views are **not** cached today.

## Mechanical capabilities

`capabilities_of(ref, campaign_id)` delegates to `mechanics.capabilities_of(campaign_id, f"character:{asset_id}", entity_kind="character")`. The Mechanics service handles the `entity_ref` → sheet lookup itself; Characters does not pre-check for a sheet. Returns `[]` cleanly when there is no active mechanics module (`MechanicsService` returns empty).

## Relationships (campaign-scoped)

`get_relationships(ref, campaign_id)` queries the `relationships` table for rows where `from_character_ref = ref OR to_character_ref = ref` on the campaign's main branch (or the supplied `branch_id`), returns plain dicts via `_relationship_row_to_dict`.

`update_relationship(from_ref, to_ref, campaign_id, delta, *, branch_id, types, turn_id)`:

- Looks up the existing row; if absent, creates a new `RelationshipState()` and a fresh `rel_<uuid>` id.
- Numeric fields (`affection`, `trust`, `dominance`, `intimacy`) are **incremented** by `delta[key]`. `awareness` is **set**. `custom` dict is **merged**.
- `types` arg is unioned with the existing list (dedup-preserving).
- Upserts via `INSERT ... ON CONFLICT(id) DO UPDATE`; returns the merged row as a dict.

## Promotion (emergent → library)

`promote_to_library(campaign_id, character_id, target_world_id, *, source=..., delete_emergent=False)`:

1. Read the emergent character via `store.get_emergent(...)`; raise `PromotionError` if missing.
2. Ensure `frontmatter["id"]` is set.
3. `make_library_id(target_world_id, "character", character_id)` → write through `store.write_library_file`. The service skips `LibraryService.promote_to_library` deliberately — that path excludes `character` — and writes directly so the character-specific behavior stays here.
4. If `delete_emergent`, remove the emergent file on disk.
5. Return the new library path.

## Imports

The ingest pipeline is in `ingest.py` and is wrapped by three thin service methods:

- `import_sillytavern(card_bytes, target_world_id, *, options=None)` — JSON envelope, JSON data, PNG with embedded `chara` (v2) / `ccv3` (v3) tEXt chunk, **or** charx zip (the function sniffs the magic bytes and dispatches).
- `import_charx(charx_bytes, target_world_id, *, options=None)` — same code path; explicit method for clarity.
- `import_plaintext(text, target_world_id)` — heuristic plaintext via `parse_plaintext` (first non-empty line = name; quoted lines = samples; remaining prose = description+body).
- `import_character_card(payload, target_world_id, *, options=None) -> (ImportResult, IngestedCharacterCard)` — like `import_sillytavern` but also returns the full ingest envelope so a UI can render creator notes / alternate greetings / character book before committing.

### `ingest_character_card_v2` (`ingest.py:82`)

Deterministic, no I/O beyond decoding the payload:

1. **PNG bytes** (`\x89PNG\r\n\x1a\n`): walk chunks, decode the first `tEXt`/`iTXt` chunk whose key is `chara` (v2) or `ccv3` (v3); base64-decode + JSON-parse. Prefer `ccv3` over `chara` when both present.
2. **ZIP bytes** (`PK`): expect `card.json` / `character.json` / `data.json` at the root; pull `card.png` / `avatar.png` / `image.png` as the embedded avatar if present.
3. **JSON bytes**: parse directly; accept either the `{spec, data}` envelope or a bare data object.

Maps to `CharacterData` with:
- `asset_id` from `character_book_id` / `char_id` / `id` / name (slugified).
- `voice.summary` from `personality` or first sentence of `description`.
- `voice.samples` extracted from `mes_example` chunks (`<START>` splitter, `{{char}}:` and quoted-line patterns) or fallback to first quoted strings in `first_mes`.
- `voice.speech_patterns` regex-matched from `speaks/talks/uses/says <phrase>`.
- `body` = `## Description`/`## Personality`/`## Scenario`/`## System prompt`/`## Post-history instructions`/`## Alternate greetings`/`## Creator notes` sections in that order.
- `images` gets a placeholder `CharacterImage(kind=AVATAR, source="embedded_avatar")` when `IngestOptions.keep_embedded_avatar` is true; the actual bytes ride along in `IngestedCharacterCard.avatar_bytes` and get persisted to disk by `_finalize_import`.
- `image` (template) synthesized as a comma-joined `name, first-desc-sentence, first-personality-sentence, top-6-tags` when `derive_image_prompt` is true.
- `structural_relationships` from `extract_relationships_deterministic` when `extract_relationships` is true.

The function returns an `IngestedCharacterCard` carrying the projected `data`, the raw `creator`/`creator_notes`/`character_version`/`system_prompt`/`post_history_instructions`/`alternate_greetings`/`character_book`/`extensions`, the `avatar_bytes` + `avatar_mime`, and `warnings`.

### `extract_relationships_deterministic(text, *, known_characters=(), known_factions=())`

Regex-based extractor for unambiguous phrases:

- Relationships: `son/daughter/child of X` → parent, `brother/sister/sibling/twin of X` → sibling, `wife/husband/spouse of X` + `married to X` → spouse, `mentor/teacher/master of X` → mentor, `apprentice/student of X` → apprentice, `lover of X` → lover, `ally/rival/enemy/friend of X` → ally/rival/enemy/friend.
- Factions: `member/initiate/recruit of [the] X` → `faction:member`, `leader/head/chief/prince/primogen of [the] X` → `faction:leader`, `leads [the] X` → leader, `loyal/sworn to [the] X` / `serves [the] X` / `joined [the] X` → member, `opposes/opposing [the] X` → `faction:rival`.

When `known_characters` / `known_factions` slug pools are supplied, captured names are slugified and dropped unless they resolve. The `"the Tremere"` → `tremere` retry handles leading articles.

### `enrich_with_llm(card, llm, *, options)` (optional)

Honored only when `IngestOptions.enrich_with_llm` is true and an `ingest_llm` callable was supplied at service construction. The callable returns an `LLMEnrichment` dict; recognized keys (`voice_summary`, `voice_register`, `speech_patterns`, `dos`, `donts`, `tags`, `description`, `aliases`) are merged conservatively — empty strings / empty lists are skipped so the deterministic parse stays authoritative.

### `_finalize_import`

Refuses to overwrite existing characters (appends to `result.skipped` with a warning). Persists the embedded avatar bytes to `library/worlds/<w>/characters/<id>/<filename>` via `_write_image_bytes`, then writes the character via `self.create(...)`. Errors bubble into `result.errors`.

### `add_character_image`

Append-to-gallery helper used after ingest or by ImageGen output. When `image_bytes` are supplied, writes them under the character's directory and rewrites the `path` on the `CharacterImage` before persisting. Updates the frontmatter `images:` list through `LibraryService.update_entity`.

## Search

`search(query, world_id=None, scope="all", campaign_id=None)`:

- `scope="world"` + `world_id` → `library.list_in_world(world_id, "character")`.
- `scope="library"` or default-all with no world/campaign → raw `library_index` table scan, then project rows back to `LibraryEntity`.
- `scope="campaign"` or `campaign_id` provided → `library.list_for_composition(campaign_id, "character")`.
- Else falls back to world listing.

Match is substring on `name + aliases + tags` (lowercased).

## State plumbing

`_load_state(asset_id, ref, campaign_id, branch_id=None)` queries `StateStore.resolve_character_state(character_ref, branch_id)` and projects to `CharacterState`; missing rows produce a zero-valued state on the campaign's `<campaign_id>:main` branch.

`_save_state(ref, campaign_id, state, *, source, turn_id=None)` upserts directly into `character_state` (`INSERT ... ON CONFLICT(character_ref, branch_id) DO UPDATE`). Bypasses `apply_delta` deliberately — there is no turn-level audit pipeline for state writes yet; reversal-friendliness is a follow-up. The `source` arg is accepted for forward compatibility and currently ignored beyond logging intent.

## Error handling

- `CharactersError` base; `CharacterNotFoundError` (resolution miss), `ImportError_` (unparseable card), `PromotionError` (missing emergent on promote). All raise immediately — no recovery paths.
- `set_active_pc` raises `CharactersError("<ref> is not a PC in campaign <id>")` when the ref isn't in the roster.
- `upsert_override` and `_library_id_from_ref` raise `CharactersError` for non-library refs.
- Import: existing-id collisions are non-fatal (`skipped`); `_finalize_import.create(...)` exceptions append to `errors` rather than raising.

## Configuration

There is no separate `CharactersConfig` dataclass yet. Knobs surface as constructor kwargs (`drift_threshold`, `drift_checker`, `post_fetcher`, `ingest_llm`) and per-call options (`IngestOptions`). The spec 08 §Configuration knobs that don't map yet (sample rotation toggle, `check_every_n_appearances`, `drift_score_threshold` as a config block, `capsules.auto_generate`, `multi_pc.auto_advance_with_single_pc`, etc.) are tracked in the remaining doc.

## Wiring touchpoints

- App startup: `backend/src/grimoire/main.py:136` constructs the service.
- HTTP API: `backend/src/grimoire/api/campaigns.py` calls `list_pcs` / `add_pc` / `remove_pc` / `set_active_pc` / `set_current_scene_for_pc` / `list_for_campaign` / `promote_to_library`.
- Context Builder (`backend/src/grimoire/context/builder.py`) and Extractor (`backend/src/grimoire/extractor/heuristics.py`) consume character data; the wiring is via Library reads today, not direct CharactersService calls — see the remaining doc for the Context-Builder-via-Characters integration.

## Test wiring

`backend/tests/characters/conftest.py` constructs a fresh `Database` + `StateStore` + `LibraryService` + `MechanicsService` per test under `tmp_path`. `test_service.py` exercises CRUD, emergent + override, PC lifecycle, multi-PC `should_auto_respond`, per-PC scene, tier pinning, drift (in-voice + forbidden-phrase + below-threshold), compressed views, cross-world lookup, capabilities (null-mechanics path), relationship increment, promotion, three import paths, and search. `test_ingest.py` covers the deterministic PNG / charx / JSON parsers, the relationship and faction extractor with and without slug pools, the LLM enrichment hook, and the service integration (avatar persistence, gallery, structural relationships round-tripping through frontmatter).
