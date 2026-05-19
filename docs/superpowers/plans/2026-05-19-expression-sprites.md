# Expression Sprites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Land everything in `docs/superpowers/specs/2026-05-19-expression-sprites-design.md`. Directory-form character cards with sprites; GEV enum; mechanics vocab extensions; extractor `expression_changed` deltas; sprite serving with fallback chain.

**Architecture:** Six branches.

- **A** `feature/sprites-A-directory-form` — Library indexer + watcher classifier recognise `characters/<id>/card.md`.
- **B** `feature/sprites-B-types-manifest` — `CoreExpression` enum; `ModuleManifest.expression_vocabulary_extensions`; validator.
- **C** `feature/sprites-C-state-table` — migration for `expression_state`; service for read/write.
- **D** `feature/sprites-D-extractor` — `ExpressionChange` typed candidate; rule-based + LLM classifier strategies.
- **E** `feature/sprites-E-rest` — sprite-resolve endpoint with fallback chain + path validation.
- **F** `feature/sprites-F-frontend` — `CharacterSprite` component; PC expression picker in composer.

**Tech Stack:** Python 3.12, FastAPI, pytest-asyncio, Pydantic v2; React/TS frontend.

---

## Conventions

Standard. No hard cross-spec deps.

---

## Branch setup

- [ ] **Step S1: Worktrees**

```powershell
git worktree add .worktrees/sprites-A-directory-form  -b feature/sprites-A-directory-form  main
git worktree add .worktrees/sprites-B-types           -b feature/sprites-B-types           main
git worktree add .worktrees/sprites-C-state-table     -b feature/sprites-C-state-table     main
git worktree add .worktrees/sprites-D-extractor       -b feature/sprites-D-extractor       main
git worktree add .worktrees/sprites-E-rest            -b feature/sprites-E-rest            main
git worktree add .worktrees/sprites-F-frontend        -b feature/sprites-F-frontend        main
```

---

# Branch A — Directory-form character cards

### Task A1: Path helpers + indexer + classifier

**Files:**
- Modify: `backend/src/grimoire/state_store/paths.py` — `character_dir_layout` helper.
- Modify: `backend/src/grimoire/state_store/indexers.py:upsert_library_index` — detect directory form.
- Modify: `backend/src/grimoire/watcher/classifier.py:_classify_library`.
- Test: `backend/tests/library/test_directory_form.py`.

- [ ] **Step 1: Failing tests**

```python
async def test_flat_and_directory_form_produce_same_entity(store, library, tmp_path):
    # Flat: data/library/worlds/w/characters/alistair.md
    # Directory: data/library/worlds/w/characters/beatrice/card.md
    await write_flat_character(tmp_path / "data" / "library" / "worlds" / "w", "alistair", ...)
    await write_dir_character(tmp_path / "data" / "library" / "worlds" / "w", "beatrice", ...)
    e1 = await library.get_entity("w", "character", "alistair")
    e2 = await library.get_entity("w", "character", "beatrice")
    assert e1 is not None
    assert e2 is not None


async def test_watcher_classifies_directory_card(tmp_path):
    path = tmp_path / "data/library/worlds/w/characters/beatrice/card.md"
    classification = _classify_library(path)
    assert classification.kind == "character"
    assert classification.asset_id == "beatrice"


async def test_directory_without_card_md_ignored(tmp_path, caplog):
    path = tmp_path / "data/library/worlds/w/characters/beatrice/"
    classification = _classify_library(path / "avatar.png")
    assert classification is None


async def test_sprite_path_resolution(store):
    layout = character_dir_layout("w", "beatrice", root=tmp_path)
    assert layout.sprites_dir == tmp_path / "data/library/worlds/w/characters/beatrice/sprites"
    assert layout.avatar == tmp_path / "data/library/worlds/w/characters/beatrice/avatar.png"
    assert layout.card_md == tmp_path / "data/library/worlds/w/characters/beatrice/card.md"
```

- [ ] **Step 2: Implement** — extend classifier to handle path depth 5 (`worlds/<w>/characters/<id>/card.md`); paths.py helper resolves layout (flat vs directory).

```python
# backend/src/grimoire/state_store/paths.py
@dataclass(frozen=True, slots=True)
class CharacterLayout:
    asset_id: str
    form: str        # "flat" | "directory"
    card_md: Path
    avatar: Path | None
    sprites_dir: Path | None


def character_dir_layout(world_id: str, asset_id: str, *, root: Path) -> CharacterLayout:
    base = root / "data" / "library" / "worlds" / world_id / "characters"
    flat = base / f"{asset_id}.md"
    if flat.exists():
        return CharacterLayout(asset_id, "flat", flat, None, None)
    directory = base / asset_id
    return CharacterLayout(
        asset_id, "directory",
        directory / "card.md",
        directory / "avatar.png" if (directory / "avatar.png").exists() else None,
        directory / "sprites" if (directory / "sprites").is_dir() else None,
    )
```

Watcher classifier change: in `_classify_library`, when the path's parent dir has a `card.md` sibling and the file is under that directory, treat the parent dir name as the `asset_id` and the kind as `character` (for `card.md` itself); ignore sibling `avatar.png` and `sprites/*.png` for classification (they're data, not entities).

- [ ] **Step 3: Tests PASS + commit + merge A.**

---

# Branch B — Types + manifest extension

### Task B1: `CoreExpression` enum + manifest field

**Files:**
- Create: `backend/src/grimoire/types/expressions.py`.
- Modify: `backend/src/grimoire/types/mechanics.py:ModuleManifest` — add `expression_vocabulary_extensions: list[str]`.
- Modify: `backend/src/grimoire/validation/manifests.py` — validator.
- Test: `backend/tests/types/test_expressions.py`, `backend/tests/validation/test_manifest_expressions.py`.

- [ ] **Step 1: Failing tests**

```python
def test_core_expression_complete():
    assert {e.value for e in CoreExpression} == {
        "neutral", "happy", "sad", "angry", "surprised", "fearful",
        "disgusted", "smug", "thoughtful", "embarrassed", "determined",
        "hurt", "tired", "suspicious",
    }


def test_manifest_extension_collision_rejected():
    manifest = ModuleManifest(id="wod", expression_vocabulary_extensions=["happy"])  # collides
    with pytest.raises(ManifestError, match="happy"):
        validate_manifest(manifest)


def test_inter_module_collision_warned(caplog):
    # Two modules declare "seductive" — campaign load logs a warning
    ...


def test_namespace_resolution():
    from grimoire.types.expressions import resolve_label
    assert resolve_label("seductive", modules=["wod"]) == "wod.seductive"
    assert resolve_label("happy", modules=[]) == "happy"
    # Ambiguous (two modules) → namespaced first match logged
    ...
```

- [ ] **Step 2: Implement** types + validation.

- [ ] **Step 3: Tests PASS + commit + merge B.**

---

# Branch C — `expression_state` table + service

### Task C1: Migration + service

**Files:**
- Create: `backend/src/grimoire/storage/migrations/028_expression_state.sql`.
- Create: `backend/src/grimoire/expressions/service.py`, `__init__.py`.
- Test: `backend/tests/expressions/test_state.py`.

- [ ] **Step 1: SQL**

```sql
-- backend/src/grimoire/storage/migrations/028_expression_state.sql
CREATE TABLE expression_state (
    id           INTEGER PRIMARY KEY,
    campaign_id  TEXT NOT NULL,
    scene_id     TEXT NOT NULL,
    character_id TEXT NOT NULL,
    turn_id      TEXT NOT NULL,
    post_id      TEXT NOT NULL,
    emotion      TEXT NOT NULL,
    provenance   TEXT NOT NULL,
    confidence   REAL NOT NULL DEFAULT 1.0,
    set_at       TEXT NOT NULL
);
CREATE INDEX ix_expr_current
    ON expression_state(campaign_id, character_id, turn_id DESC);
```

- [ ] **Step 2: Failing service tests**

```python
async def test_set_and_get_current(service, seeded_campaign):
    await service.set(
        campaign_id=seeded_campaign, scene_id="s_1",
        character_id="char_florence", turn_id="t_1", post_id="p_1",
        emotion="determined", provenance="user:pc",
    )
    current = await service.current_for(seeded_campaign, "char_florence")
    assert current.emotion == "determined"


async def test_as_of_turn_query(service, seeded_campaign):
    await service.set(... turn_id="t_1", emotion="happy" ...)
    await service.set(... turn_id="t_5", emotion="angry" ...)
    current_at_t1 = await service.current_for(seeded_campaign, "char_florence", as_of_turn="t_1")
    assert current_at_t1.emotion == "happy"


async def test_extractor_label_outside_current_vocab_discarded(service, seeded_campaign, caplog):
    # No mechanics module declares "stale.label"
    await service.set(seeded_campaign, "s_1", "char_x", "t_1", "p_1",
                      emotion="stale.label", provenance="extractor:auto")
    # Should log + skip
    current = await service.current_for(seeded_campaign, "char_x")
    assert current is None
```

- [ ] **Step 3: Implement service** — read returns `None` when no row; vocab validation against installed modules' extensions.

- [ ] **Step 4: Tests PASS + commit + merge C.**

---

# Branch D — Extractor strategies

### Task D1: `ExpressionChange` candidate + heuristic strategy

**Files:**
- Modify: `backend/src/grimoire/types/extraction.py:ExtractionResult` — add `expression_changes`.
- Create: `backend/src/grimoire/expressions/heuristic.py`.
- Create: `backend/src/grimoire/expressions/llm_classifier.py`.
- Modify: `backend/src/grimoire/extractor/service.py` — route changes.
- Test: `backend/tests/expressions/test_classifier.py`.

- [ ] **Step 1: Failing tests**

```python
def test_heuristic_detects_happy_keywords():
    changes = heuristic_classify(
        scene_post_text='"I knew you\'d come!" winifred laughed, eyes bright with joy.',
        present_characters=[("char_florence", "winifred")],
    )
    winifred = next(c for c in changes if c.character_id == "char_florence")
    assert winifred.emotion == "happy"


def test_heuristic_detects_angry_punctuation_intensifies():
    changes = heuristic_classify(
        scene_post_text='"Get out!" winifred snapped.',
        present_characters=[("char_florence", "winifred")],
    )
    assert any(c.emotion == "angry" and c.confidence > 0.7 for c in changes)


def test_terminal_emotion_wins_in_multi_emotion_paragraph():
    changes = heuristic_classify(
        scene_post_text='winifred smiled, then her face fell.',
        present_characters=[("char_florence", "winifred")],
    )
    assert next(c for c in changes if c.character_id == "char_florence").emotion in {"sad", "neutral"}


async def test_high_confidence_routed_to_state(extractor, expressions_service, ...):
    ...


async def test_medium_confidence_routed_to_review_queue(...):
    ...


async def test_low_confidence_discarded(...):
    ...
```

- [ ] **Step 2: Implement heuristic** — keyword tables + punctuation analysis + terminal-emotion-wins logic.

- [ ] **Step 3: Implement LLM classifier** — single Haiku call per post emitting `{character_id: emotion}`; merges with heuristic via standard merge.

- [ ] **Step 4: Routing in `extractor/service.py`** — after merge, walk `expression_changes` and dispatch per confidence band.

- [ ] **Step 5: Tests PASS + commit + merge D.**

---

# Branch E — Sprite-resolve endpoint

### Task E1: REST route with fallback chain

**Files:**
- Create: `backend/src/grimoire/api/expressions.py`.
- Test: `backend/tests/api/test_expressions.py`.

```
GET /campaigns/{id}/characters/{char_id}/expression?as_of_turn={turn_id}
```

Response:
```json
{ "emotion": "happy", "sprite_url": "/library/.../sprites/happy.png", "fallback_used": false }
```

- [ ] **Step 1: Failing tests**

```python
async def test_returns_requested_sprite_when_present(client, library_with_directory_char):
    r = await client.get(f"/campaigns/{c_id}/characters/{c_id}/expression?as_of_turn=t_1")
    body = r.json()
    assert body["emotion"] == "happy"
    assert body["sprite_url"].endswith("/sprites/happy.png")
    assert body["fallback_used"] is False


async def test_falls_back_to_neutral_when_emotion_sprite_missing(...):
    # Has sprites/neutral.png but not sprites/smug.png
    ...


async def test_falls_back_to_avatar_when_no_neutral(...):


async def test_returns_null_sprite_url_when_nothing_available(...):


async def test_path_traversal_rejected(client):
    r = await client.get(f"/campaigns/{c_id}/characters/..%2F..%2Fetc%2Fpasswd/expression")
    assert r.status_code in {400, 404}


async def test_as_of_turn_returns_historical_emotion(...):
```

- [ ] **Step 2: Implement** — query `expression_state` for current/as-of emotion; resolve sprite via `character_dir_layout`; walk fallback chain; validate path is under the library directory (reject `..` / `\`).

- [ ] **Step 3: Tests PASS + commit + merge E.**

### Task E2: PC expression PATCH route

```
PATCH /campaigns/{id}/characters/{char_id}/expression
  body: { emotion: "determined", post_id: "p_4711" }
```

Writes directly to `expression_state` with provenance `user:pc`. Bypasses extractor.

- [ ] **Step 1: Failing test + implement.**

---

# Branch F — Frontend

### Task F1: `CharacterSprite` component

**Files:**
- Create: `frontend/src/components/CharacterSprite.tsx`.
- Create: `frontend/src/api/expressions.ts` — `useExpression(charId, turnId)` hook.
- Modify: `frontend/src/routes/campaign/PostItem.tsx` — render sprite next to character header.

- [ ] **Step 1: Failing component test** — render with mocked endpoint returning happy sprite; assert <img src> contains "/sprites/happy.png".

- [ ] **Step 2: Implement** — fixed container with `object-fit: contain` (~120×180 px in scene pane); responsive scaling; alt text falls back to character name.

- [ ] **Step 3: Failing test for fallback handling** — endpoint returns `sprite_url: null` → component renders character name only.

- [ ] **Step 4: Implement** — hook caches `(charId, turnId) → emotion + URL` keyed on those params.

### Task F2: `ExpressionPicker` in composer

**Files:**
- Create: `frontend/src/routes/campaign/Composer/ExpressionPicker.tsx`.
- Modify: `frontend/src/routes/campaign/Composer.tsx` — embed picker for active PC.

UI: dropdown listing CoreExpression labels + any active mechanics-module extensions; default "neutral". On post submit, the chosen emotion is sent to the new PATCH route with the just-submitted post_id.

- [ ] **Step 1-N: Failing test + implement + commit + merge F.**

---

# Integration check

- [ ] **Step end1: Full suite + frontend tests.**
- [ ] **Step end2: Smoke** — create a directory-form character with sprites; run a turn with extractor; observe sprite changes; user-set the PC expression.
- [ ] **Step end3: COMPLETED doc + delete design.**
