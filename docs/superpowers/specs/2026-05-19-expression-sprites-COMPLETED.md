## Expression Sprites — Design

> **Status:** SHIPPED. `backend/src/grimoire/expressions/` (heuristic + LLM classifier + routing) plus migration `029_expression_state.sql`, `expression_vocabulary_extensions` on `ModuleManifest`, sprite-serving REST endpoint, and the closed 14-label `CoreExpression` enum all landed. Note: §1 says the extractor strategy "emits `expression_changed` deltas"; the actual code uses a typed `ExpressionChange` payload threaded through `ExtractionResult.expression_changes` rather than a delta-named event — same mechanism, different wire name.

**Source idea:** `specs/new/expression-sprites.md`
**Module:** `backend/src/grimoire/expressions/` (new), additions to `library/`, `extractor/`, `frontend/`

## Purpose

Per-character expression sprite display in the scene pane, fed by an Extractor strategy that classifies the emotion of each character's spoken paragraphs. Presentation-layer feature: touches nothing in deterministic context assembly, state model, or mechanics.

## Scope (what changes)

1. **Directory-form character cards** — Library indexer recognises `characters/<id>.md` and `characters/<id>/card.md` equivalently, with a sibling `sprites/{neutral,happy,...}.png` directory.
2. **Grimoire Expression Vocabulary (GEV)** — fourteen core labels, defined here as a closed enum.
3. **Mechanics module vocabulary extensions** — `manifest.yaml.expression_vocabulary_extensions` (per Theme D, an inline addition to `ModuleManifest`).
4. **Extractor strategy** — emits `expression_changed` deltas; rule-based + LLM-classifier behind the existing confidence routing.
5. **Storage** — `expression_state` table (turn_id, scene_id, character_id, emotion, set_at).
6. **Sprite serving** — REST endpoint that resolves emotion → sprite path with fallback chain.
7. **Frontend** — sprite rendered per post; PC expression set by the player, not the extractor.

## 1. Directory-form character cards

Library currently expects flat `characters/<id>.md`. The Library indexer (`backend/src/grimoire/state_store/indexers.py:upsert_library_index`) and the watcher classifier (`backend/src/grimoire/watcher/classifier.py:_classify_library`) gain a new path shape:

```
data/library/worlds/<world>/characters/
├── alistair-hyde-smythe.md            # flat form, no sprites
└── beatrice/
    ├── card.md                         # frontmatter + body
    ├── avatar.png                      # fallback portrait
    └── sprites/
        ├── neutral.png
        ├── happy.png
        ├── angry.png
        └── ...
```

The Library indexer extracts the character id from the directory name (when `card.md` is present) or from the filename stem (flat form). Both shapes write the same `library_index` row keyed on `(world_id, "character", asset_id)`. The watcher classifier returns the same `ContentKind.CHARACTER` regardless of shape.

`paths.py` gains helpers:

```python
def character_dir_layout(world_id: str, char_id: str) -> CharacterLayout:
    """Returns the resolved layout for a character: flat | directory; locates avatar, sprites dir."""
```

Mixed forms (a character moving from flat to directory) requires manual file moves; not handled by tooling in v1.

## 2. Grimoire Expression Vocabulary (GEV)

The 14 core labels are stable for v1:

```python
class CoreExpression(StrEnum):
    NEUTRAL      = "neutral"
    HAPPY        = "happy"
    SAD          = "sad"
    ANGRY        = "angry"
    SURPRISED    = "surprised"
    FEARFUL      = "fearful"
    DISGUSTED    = "disgusted"
    SMUG         = "smug"
    THOUGHTFUL   = "thoughtful"
    EMBARRASSED  = "embarrassed"
    DETERMINED   = "determined"
    HURT         = "hurt"
    TIRED        = "tired"
    SUSPICIOUS   = "suspicious"
```

Extending this enum is a versioned codebase change (rare). Module-contributed extensions go through the namespaced extension path.

## 3. Mechanics module vocabulary extensions

A mechanics module declares additional labels in its manifest:

```yaml
# data/library/mechanics-modules/wod/manifest.yaml
expression_vocabulary_extensions: [seductive, terrified, awakened]
```

`ModuleManifest` (`backend/src/grimoire/types/mechanics.py:131–145`) gains:

```python
class ModuleManifest(BaseModel):
    ...
    expression_vocabulary_extensions: list[str] = []
```

Validator in `backend/src/grimoire/validation/manifests.py`:
- snake_case, 1–32 chars per label.
- No overlap with `CoreExpression` (reject collision).
- No overlap between two installed modules in the same campaign (collision warning at campaign load).

Extensions namespaced under module id when stored / referenced: the manifest above produces effective labels `wod.seductive`, `wod.terrified`, `wod.awakened`. The frontend resolves the sprite filename as `wod.seductive` → `sprites/wod.seductive.png` (with namespace dot preserved in filename, since dots are valid in filenames on every supported OS).

**Module-switch behavior (the open question on GEV stability):** when a campaign's mechanics module changes or is removed, existing `expression_state` rows referencing the old module's labels are **preserved as-is** (not migrated, not deleted) so historical posts retain their expressions when re-rendered. New writes can only use labels valid for the current module set. The frontend's fallback chain handles unresolvable sprites gracefully (see §5).

## 4. Extractor strategy

New strategy `expression_classifier` parallel to the existing three (rule_based, structured_llm, heuristic_flags). Outputs `expression_changed` deltas in the typed candidate channel — per Theme E (typed candidate lists per feature):

```python
@dataclass
class ExpressionChange:
    character_id: str
    scene_id: str
    post_id: str
    emotion: str                      # core enum value or "<module>.<label>"
    confidence: float
    evidence: str                     # post excerpt that drove the classification
```

`ExtractionResult` gains:

```python
expression_changes: list[ExpressionChange] = []
```

Routing in `extractor/service.py`:
- `confidence >= 0.7` → write to `expression_state` table immediately (provenance: `extractor:auto`).
- `0.5 <= confidence < 0.7` → enqueue in `review_queue` with `kind="expression_change"`; on user approve, write with provenance `extractor:reviewed`.
- `< 0.5` → discard.

Threshold knobs: `expressions.auto_apply_threshold` and `expressions.review_threshold` in campaign config; defaults documented (open question — 0.7 / 0.5 are the picks).

**Multi-emotion paragraphs (the open question):** terminal emotion wins per spec. The classifier scans each character's spoken paragraphs in the post and emits one `ExpressionChange` per character per post, using the last detected emotion. Multi-frame tracking is reserved for a future spec.

Two implementations behind the strategy interface:

- **Rule-based** (`expressions/heuristic.py`): keyword + punctuation patterns. Cheap, deterministic, ~70% recall on obvious cases. Fast path; runs by default.
- **LLM classifier** (`expressions/llm_classifier.py`): one Haiku-level call per post that emits a JSON `{character_id: emotion}` map. Higher recall + better at subtle cases; runs when `expressions.use_llm_classifier=true`.

Both produce `ExpressionChange` items; the standard merge (highest confidence wins) handles overlap.

## 5. Storage

Migration (number picked at plan time):

```sql
CREATE TABLE expression_state (
    id           INTEGER PRIMARY KEY,
    campaign_id  TEXT NOT NULL,
    scene_id     TEXT NOT NULL,
    character_id TEXT NOT NULL,
    turn_id      TEXT NOT NULL,
    post_id      TEXT NOT NULL,
    emotion      TEXT NOT NULL,        -- core enum value or "<module>.<label>"
    provenance   TEXT NOT NULL,        -- extractor:auto | extractor:reviewed | user:pc
    confidence   REAL NOT NULL DEFAULT 1.0,
    set_at       TEXT NOT NULL
);

CREATE INDEX ix_expr_current
    ON expression_state(campaign_id, character_id, turn_id DESC);
```

"Current expression" for a character is `SELECT emotion FROM expression_state WHERE campaign_id=? AND character_id=? ORDER BY turn_id DESC LIMIT 1`. No supersession bookkeeping — every post can produce a new row; the latest wins on read. Differs from transient state's `superseded_by` pattern because expressions are deliberately granular and we don't need "preserve all history for conflict resolution."

For long campaigns: a campaign with 10,000 posts × 5 characters = 50,000 rows. Acceptable. No vacuum scheduled in v1 (the data is small and useful for replay).

## 6. Sprite serving

REST:

```
GET /campaigns/{id}/characters/{char_id}/expression?as_of_turn={turn_id}
```

Returns `{ emotion: "happy", sprite_url: "/library/.../sprites/happy.png", fallback_used: false }`. Fallback chain:

1. Requested emotion → `sprites/<emotion>.png`.
2. → `sprites/neutral.png`.
3. → `avatar.png` (flat or directory form).
4. → no sprite (frontend shows name only); response has `sprite_url: null`.

`fallback_used: true` flag set if any step beyond the first kicked in. The frontend can show a one-time hint to the GM: "Beatrice is missing a 'smug' sprite — currently showing neutral."

The endpoint serves URLs only; sprite bytes go through the existing static-file handler under `/library/...`. Sprite paths are filesystem-resolved and **path-traversal validated** (reject `../`, `\`) — this is the open-question pitfall noted in research.

`as_of_turn` is optional (defaults to "latest"). With it, the endpoint resolves the expression that was current at that turn — useful for re-rendering past posts.

**Caching:** the frontend caches `(char_id, turn_id) → emotion` mappings; sprite files themselves are cached by the browser via standard HTTP. Server emits `Cache-Control: public, max-age=86400` for sprite files (versioned via file mtime as ETag).

## 7. PC expression entry

PCs' expressions are user-set; the extractor never overwrites them.

Entry mechanism (the open question): inline UI dropdown in the composer. While drafting a post, the player picks an emotion from a dropdown (defaulting to "neutral"). On submit, the chosen emotion is recorded with provenance `user:pc` against the new post.

Backend route:
```
PATCH /campaigns/{id}/characters/{char_id}/expression
  body: { emotion: "determined", post_id: "p_4711" }
```

This writes an `expression_state` row directly; no extractor involvement. The frontend ensures the dropdown is shown only for the active PC.

Cross-scene persistence: PC expression carries across scenes (newest-wins read), but a configurable `pc_expression_reset_on_scene_end: false` knob exists for users who want fresh expressions per scene. Default keeps expressions until the player changes them.

## Frontend display rules

Spec rules formalized:
- The **speaker of the latest paragraph** in each post displays their current expression (from `expression_state` at the post's turn).
- Recent speakers in the same scene retain their last-known.
- PC expressions are player-set (composer dropdown).
- Characters not in scene show no sprite.

Implementation: each rendered post's character header includes the sprite element; the React component takes `character_id` + `as_of_turn` and resolves the expression via `useExpression(charId, turnId)` hook, which calls the REST endpoint and caches.

Sprite size validation (the open question): recommended profile (~512×768 transparent PNG, centered subject) is documented in setup docs but **not enforced**. The frontend renders whatever size + composes a fixed-height container with `object-fit: contain`. Sprites smaller than the container letterbox; larger ones scale down. Card authors are advised but not forced.

## Cross-spec hooks

- **`transient-state`**: emotion is conceptually a transient field, but stored separately because of the per-post granularity + sprite fallback complexity. The privacy model (`Character.privacy.expressions.surface_inline`) could gate inline sprite rendering — wire-up is planned but deferred to a follow-up since v1 doesn't have a clear privacy use case for expressions specifically.
- **`scene-hud`**: the present-cast widget could surface current emotion as part of the chip; wired through the same `/expression` endpoint. Owned by `scene-hud-design.md`'s widget definition.
- **`narrative-extras`**: orthogonal — extras are stable colors; expressions are per-post.

## Configuration

```yaml
expressions:
  enabled: true
  auto_apply_threshold: 0.7
  review_threshold: 0.5
  use_llm_classifier: false             # rule-based by default
  pc_expression_reset_on_scene_end: false
```

## Failure handling

| Failure | Behavior |
|---|---|
| Module declares a label colliding with core | Manifest validation fails; module fails to load |
| Two installed modules share an extension label | Campaign load logs a warning; namespaced fully-qualified names disambiguate |
| Sprite file missing | Fallback chain; `fallback_used: true` in response |
| Sprite filename has illegal chars | Path validator rejects; emotion treated as missing → fallback |
| Extractor classifies a label not in current vocab | Discard (likely a stale label after module removal); log once per campaign |
| Concurrent PC expression updates | Last-write-wins by `set_at`; no transaction needed (no atomicity invariant violated) |
| Library directory-form character without `card.md` | Indexer ignores the directory; logs once |

## Test wiring

`backend/tests/library/test_directory_form.py` (new):
- Flat and directory forms produce identical `LibraryEntity` rows.
- Watcher classifier recognises both.
- Mixed forms in same world.

`backend/tests/expressions/test_classifier.py` (new):
- Rule-based heuristic catches the obvious cases (happy / angry / sad keywords + punctuation).
- LLM classifier merges with rule-based via standard merge.
- Confidence thresholds route correctly (auto / review / discard).

`backend/tests/expressions/test_state.py`:
- Insert + current-value query.
- `as_of_turn` query returns historically correct emotion.
- PC expression update path bypasses extractor.

`backend/tests/api/test_expressions.py`:
- Sprite endpoint resolves with fallback chain.
- Path traversal rejected.
- `as_of_turn` parameter honored.
- `fallback_used` flag accurate.

`backend/tests/validation/test_manifest_expressions.py`:
- Extension labels validated.
- Core collision rejected.
- Inter-module collision warned.

## Wiring touchpoints

- `backend/src/grimoire/state_store/paths.py`: `character_dir_layout` helper.
- `backend/src/grimoire/state_store/indexers.py:upsert_library_index`: handle directory form.
- `backend/src/grimoire/watcher/classifier.py:_classify_library`: detect `card.md` in directories.
- `backend/src/grimoire/types/mechanics.py:ModuleManifest`: add `expression_vocabulary_extensions`.
- `backend/src/grimoire/validation/manifests.py`: validate extensions.
- `backend/src/grimoire/expressions/service.py` (new): read/write expression state.
- `backend/src/grimoire/expressions/heuristic.py` (new): rule-based classifier.
- `backend/src/grimoire/expressions/llm_classifier.py` (new): LLM-backed classifier.
- `backend/src/grimoire/extractor/service.py`: thread expression strategy + route `expression_changes`.
- `backend/src/grimoire/types/extraction.py`: add `expression_changes: list[ExpressionChange]`.
- `backend/src/grimoire/api/expressions.py` (new): REST routes.
- Migration adds `expression_state` table.
- `frontend/src/components/CharacterSprite.tsx` (new): sprite container with fallback handling.
- `frontend/src/routes/campaign/PostItem.tsx`: integrate `CharacterSprite`.
- `frontend/src/routes/campaign/Composer/ExpressionPicker.tsx` (new): PC expression dropdown.
- `frontend/src/api/expressions.ts` (new): client + `useExpression` hook.

## Out of scope (v1)

- Animated sprites (GIF/APNG).
- Multi-emotion within a single paragraph (terminal-wins).
- Auto-derived expression from voice anchor (would require richer NLP).
- AI-generated sprites (the imagegen module can produce them but the integration is a separate concern).
- Sprite versioning (file mtime is sufficient).
