# Import Dialog Reclassification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire a per-row category dropdown into the SillyTavern card import dialog so users can promote `character_book` entries to Character/Location/Faction/Item (or skip) at import time, instead of one-by-one after the fact.

**Architecture:** Backend computes a `suggest_kind` suggestion for every ingested lore row and returns it on the preview response. The commit payload grows a `lore_overrides` array that `_write_lore_entries` dispatches into three branches (lore-as-today, skip, or promote-via-`apply_mapping`). The frontend turns the lore preview list into per-row dropdowns, seeded from the server's suggestion, with inline inputs for required target-kind overrides.

**Tech Stack:** Python 3.12 / FastAPI / pydantic (backend), React 18 / TypeScript / vitest (frontend), pytest (backend tests), `uv` and `pnpm` for tool invocation.

**Spec:** `docs/superpowers/specs/2026-05-20-import-dialog-reclassify-design.md`.

**Branch / worktree:** Already on `2026-05-20-import-dialog-reclassify` at `.worktrees/2026-05-20-import-dialog-reclassify/`. All commands assume that directory as cwd.

---

## Task 1: Fix `_DEFAULT_VALUES` sentinels for `position` + `selective_logic`

**Why first:** Independent of everything else. Closes a latent bug that would otherwise produce spurious "matching metadata discarded" warnings on every reclassification once real lore entries (which always carry non-None values for these fields) flow through `apply_mapping`. Doing it first keeps later test output clean.

**Files:**
- Modify: `backend/src/grimoire/library/reclassify.py` (imports at top, `_DEFAULT_VALUES` at ~line 141)
- Modify: `backend/tests/library/test_reclassify.py` (add regression test)

- [ ] **Step 1: Write the failing regression test**

Append this test to `backend/tests/library/test_reclassify.py` (placement: after the existing `test_apply_mapping_overrides_win_over_defaults` around line 137):

```python
def test_apply_mapping_no_dropped_warning_for_lore_at_defaults() -> None:
    """Regression: a LoreEntry left at every default should NOT trip the
    'matching metadata discarded' warning. The position and selective_logic
    sentinels in _DEFAULT_VALUES must match the model defaults
    (LorePosition.AFTER_CAST, SelectiveLogic.AND_ANY), not None.
    """
    lore = _lore()  # all defaulted matching-metadata fields
    _fm, _body, _kept, dropped, _into_notes, warnings = apply_mapping(
        lore, EntityKind.CHARACTER, overrides=None,
    )
    assert dropped == []
    assert not any("matching metadata" in w for w in warnings)
```

- [ ] **Step 2: Run it and verify it fails**

```bash
cd backend && uv run pytest tests/library/test_reclassify.py::test_apply_mapping_no_dropped_warning_for_lore_at_defaults -v
```

Expected: FAIL with `assert dropped == []` failing (the dropped list contains `"position"` and `"selective_logic"`).

- [ ] **Step 3: Fix the sentinels**

In `backend/src/grimoire/library/reclassify.py`, add the enum imports near the existing `from grimoire.types.world import LoreEntry`:

```python
from grimoire.types.world import LorePosition, LoreEntry, SelectiveLogic
```

Then replace the two offending entries in `_DEFAULT_VALUES`:

```python
_DEFAULT_VALUES: dict[str, Any] = {
    "priority": 100,
    "probability": 100,
    "position": LorePosition.AFTER_CAST,
    "at_depth": None,
    "scan_depth": None,
    "constant": False,
    "enabled": True,
    "case_sensitive": False,
    "match_whole_words": False,
    "selective_logic": SelectiveLogic.AND_ANY,
}
```

- [ ] **Step 4: Run the regression test plus the existing reclassify suite**

```bash
cd backend && uv run pytest tests/library/test_reclassify.py -v
```

Expected: all tests pass, including the new one. No existing test should break — the existing tests that exercise the dropped-fields path set fields to non-default values explicitly (`priority=2`, etc.).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/library/reclassify.py backend/tests/library/test_reclassify.py
git commit -m "$(cat <<'EOF'
fix(reclassify): correct _DEFAULT_VALUES for position and selective_logic

The two sentinels were `None`, but LoreEntry defaults them to
LorePosition.AFTER_CAST and SelectiveLogic.AND_ANY. With None as the
default, every real lore entry tripped the "matching metadata discarded"
warning on reclassification even when the user had touched nothing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `_lore_entry_from_ingested` adapter in `reclassify.py`

**Why:** Both the preview route (Task 4) and `_write_lore_entries` (Task 6) need to turn an `IngestedLoreEntry` (preview-side type, has `name` + `body` + matching-metadata) into a `LoreEntry` (library-side type, required by `suggest_kind` and `apply_mapping`). Putting the adapter in `reclassify.py` puts it next to the other transforms.

**Files:**
- Modify: `backend/src/grimoire/library/reclassify.py` (new function near the bottom of the module, after `apply_mapping`)
- Modify: `backend/tests/library/test_reclassify.py` (unit tests)

- [ ] **Step 1: Write the failing unit tests**

Append to `backend/tests/library/test_reclassify.py`:

```python
from grimoire.library.reclassify import _lore_entry_from_ingested
from grimoire.types.characters import IngestedLoreEntry


def test_lore_entry_from_ingested_copies_fields() -> None:
    ingested = IngestedLoreEntry(
        source_index=3,
        name="Brackhollow Inn",
        keys=["Brackhollow", "inn"],
        body="A quiet inn on the road north.",
        secondary_keys=["alehouse"],
        selective_logic="and_any",
        priority=200,
        probability=50,
        position="before_cast",
        at_depth=2,
        scan_depth=4,
        comment="cosy",
    )
    proxy = _lore_entry_from_ingested(ingested, world_id="w1")
    assert proxy.world_id == "w1"
    assert proxy.title == "Brackhollow Inn"
    assert proxy.body == "A quiet inn on the road north."
    assert proxy.keywords == ["Brackhollow", "inn"]
    assert proxy.secondary_keys == ["alehouse"]
    assert proxy.priority == 200
    assert proxy.probability == 50
    assert proxy.at_depth == 2
    assert proxy.scan_depth == 4
    assert proxy.comment == "cosy"


def test_lore_entry_from_ingested_falls_back_to_keys_or_index_for_title() -> None:
    no_name = IngestedLoreEntry(source_index=7, name=None, keys=["solo-key"], body="body")
    proxy = _lore_entry_from_ingested(no_name, world_id="w1")
    assert proxy.title == "solo-key"

    bare = IngestedLoreEntry(source_index=9, name=None, keys=[], body="body")
    proxy2 = _lore_entry_from_ingested(bare, world_id="w1")
    assert proxy2.title == "entry-9"
```

- [ ] **Step 2: Run and verify failure**

```bash
cd backend && uv run pytest tests/library/test_reclassify.py::test_lore_entry_from_ingested_copies_fields tests/library/test_reclassify.py::test_lore_entry_from_ingested_falls_back_to_keys_or_index_for_title -v
```

Expected: FAIL with `ImportError` for `_lore_entry_from_ingested`.

- [ ] **Step 3: Implement the adapter**

Add this near the bottom of `backend/src/grimoire/library/reclassify.py` (after `apply_mapping`, before the audit helpers):

```python
def _lore_entry_from_ingested(
    entry: "IngestedLoreEntry",
    *,
    world_id: str,
) -> LoreEntry:
    """Adapt an ``IngestedLoreEntry`` (preview-side) into a ``LoreEntry``.

    The classifier (``suggest_kind``) and the mapping (``apply_mapping``)
    both want a real ``LoreEntry``; the importer carries the raw card-side
    ``IngestedLoreEntry`` instead. This thin adapter bridges them. Only
    the fields the classifier + mapping read are copied; the rest stay at
    LoreEntry defaults.

    ``id`` is filled with a stable placeholder derived from ``source_index``
    — callers that persist the result must re-derive a real id (the
    ``_write_lore_entries`` path goes through ``_slug_for_lore_entry`` +
    ``_unique_id`` to do exactly that).
    """
    title = entry.name or (entry.keys[0] if entry.keys else f"entry-{entry.source_index}")
    return LoreEntry(
        world_id=world_id,
        id=f"ingested-{entry.source_index}",
        title=title,
        body=entry.body,
        keywords=list(entry.keys),
        secondary_keys=list(entry.secondary_keys),
        selective_logic=SelectiveLogic(entry.selective_logic),
        constant=entry.constant,
        enabled=entry.enabled,
        case_sensitive=entry.case_sensitive,
        match_whole_words=entry.match_whole_words,
        priority=entry.priority,
        probability=entry.probability,
        position=LorePosition(entry.position),
        at_depth=entry.at_depth,
        scan_depth=entry.scan_depth,
        comment=entry.comment,
    )
```

Add the `TYPE_CHECKING`-guarded import at the top of `reclassify.py` to avoid a circular import (characters imports library indirectly):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grimoire.types.characters import IngestedLoreEntry
```

- [ ] **Step 4: Run the new tests + full reclassify suite**

```bash
cd backend && uv run pytest tests/library/test_reclassify.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/library/reclassify.py backend/tests/library/test_reclassify.py
git commit -m "$(cat <<'EOF'
feat(reclassify): add _lore_entry_from_ingested adapter

Bridges the preview-side IngestedLoreEntry and the library-side LoreEntry
so suggest_kind / apply_mapping can be invoked at import time before any
lore file exists on disk.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add the `LoreOverride` pydantic type

**Files:**
- Modify: `backend/src/grimoire/types/characters.py` (new model alongside existing import-related types)
- Modify: `backend/tests/characters/test_ingest.py` or a new tiny tests file — actually a unit test belongs alongside the type. Use the existing `tests/characters/test_ingest.py` for proximity.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/characters/test_ingest.py`:

```python
import pytest
from pydantic import ValidationError


def test_lore_override_defaults_to_lore() -> None:
    from grimoire.types.characters import LoreOverride
    override = LoreOverride(source_index=0)
    assert override.kind == "lore"
    assert override.overrides == {}


def test_lore_override_accepts_known_kinds() -> None:
    from grimoire.types.characters import LoreOverride
    for kind in ("lore", "character", "location", "faction", "item", "skip"):
        LoreOverride(source_index=0, kind=kind)  # no raise


def test_lore_override_rejects_unknown_kind() -> None:
    from grimoire.types.characters import LoreOverride
    with pytest.raises(ValidationError):
        LoreOverride(source_index=0, kind="quest")
```

- [ ] **Step 2: Run and verify failure**

```bash
cd backend && uv run pytest tests/characters/test_ingest.py::test_lore_override_defaults_to_lore tests/characters/test_ingest.py::test_lore_override_accepts_known_kinds tests/characters/test_ingest.py::test_lore_override_rejects_unknown_kind -v
```

Expected: FAIL with `ImportError: cannot import name 'LoreOverride'`.

- [ ] **Step 3: Add the type**

In `backend/src/grimoire/types/characters.py`, near the other ingest-related types (after `IngestOptions`, around line 314), add:

First, change the existing top-of-file import from
`from typing import Any`
to
`from typing import Any, Literal`

Then add at module scope (after `IngestOptions`):

```python
LoreOverrideKind = Literal["lore", "character", "location", "faction", "item", "skip"]


class LoreOverride(BaseModel):
    """Per-row user choice in the import dialog (spec
    2026-05-20-import-dialog-reclassify §2).

    The frontend builds one of these per lore row whose kind diverged from
    the default ``"lore"`` and sends them on the commit payload. The
    backend dispatches in ``_write_lore_entries`` (kind=lore writes lore
    as today, kind=skip skips and warns, target kinds run ``apply_mapping``
    and write the target-kind entity).

    ``overrides`` patches the target-kind frontmatter after the mapping
    runs (see ``apply_mapping``). The only required override in v1 is
    ``kind`` for target=Location; the backend re-checks via
    ``required_overrides_for`` and rejects the commit if any are missing.
    """

    source_index: int
    kind: LoreOverrideKind = "lore"
    overrides: dict[str, Any] = Field(default_factory=dict)
```

(`Any` is already imported at the top of the file — confirmed at line 7. Just add `Literal` next to it.)

- [ ] **Step 4: Run the new tests**

```bash
cd backend && uv run pytest tests/characters/test_ingest.py -v -k lore_override
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/types/characters.py backend/tests/characters/test_ingest.py
git commit -m "$(cat <<'EOF'
feat(characters): add LoreOverride model for import-time reclassification

Carries the user's per-row choice from the import dialog: target kind
(or skip) and any required overrides for the target schema.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Compute `lore_suggestions` on the preview response

**Files:**
- Modify: `backend/src/grimoire/api/imports.py` (preview route)
- Modify: `backend/tests/api/test_imports_routes.py` (extend coverage)

- [ ] **Step 1: Write the failing test**

Append this to `backend/tests/api/test_imports_routes.py` (after `test_preview_returns_ingest_and_id`):

```python
async def test_preview_returns_lore_suggestions_parallel_to_entries(client) -> None:
    ac, _ = client
    card = {
        "spec": "chara_card_v2",
        "data": {
            "name": "Beatrice",
            "description": "A witch.",
            "first_mes": "Hi.",
            "character_book": {
                "entries": [
                    {"name": "Brackhollow Cathedral", "keys": ["cathedral"], "content": "A village cathedral located on the hill."},
                    {"name": "obscure note", "keys": ["x"], "content": "tt."},
                ],
            },
        },
    }
    png = _png_with_card(card)
    response = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/preview",
        files={"file": ("card.png", png, "image/png")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    suggestions = payload["lore_suggestions"]
    assert len(suggestions) == 2
    by_index = {s["source_index"]: s for s in suggestions}
    # "Brackhollow Cathedral" has a place noun in the title → should suggest location.
    cathedral = by_index[0]
    assert cathedral["kind"] == "location"
    assert cathedral["confidence"] >= 0.6
    assert "place noun" in cathedral["reason"]
    # No-signal entry stays at lore.
    weak = by_index[1]
    assert weak["kind"] == "lore"
    assert weak["confidence"] == 0.0
```

- [ ] **Step 2: Run and verify failure**

```bash
cd backend && uv run pytest tests/api/test_imports_routes.py::test_preview_returns_lore_suggestions_parallel_to_entries -v
```

Expected: FAIL with `KeyError: 'lore_suggestions'`.

- [ ] **Step 3: Wire suggestions into the preview route**

Edit `backend/src/grimoire/api/imports.py`:

Add imports at the top (under the existing imports):

```python
from grimoire.api.deps import CharactersDep, LibraryDep, StateStoreDep
from grimoire.library.classify import suggest_kind
from grimoire.library.reclassify import _lore_entry_from_ingested
```

Change the preview handler signature to also take `library: LibraryDep` and append the suggestions to the returned dict. Replace the existing function body's tail (from `summary = ingested.model_dump(...)` down to the `return`) with:

```python
    summary = ingested.model_dump(mode="json", exclude={"avatar_bytes"})

    threshold = library.config.reclassification.suggestion_threshold
    lore_suggestions: list[dict[str, Any]] = []
    for entry in ingested.lore_entries:
        proxy = _lore_entry_from_ingested(entry, world_id=world_id)
        suggestion = suggest_kind(proxy, threshold=threshold)
        lore_suggestions.append(
            {
                "source_index": entry.source_index,
                "kind": suggestion.kind.value,
                "confidence": suggestion.confidence,
                "reason": suggestion.reason,
            }
        )

    return {
        "preview_id": preview_id,
        "expires_in_seconds": PREVIEW_TTL_SECONDS,
        "ingested": summary,
        "lore_suggestions": lore_suggestions,
    }
```

And update the function signature:

```python
@router.post("/library/worlds/{world_id}/imports/sillytavern/preview")
async def preview_sillytavern_import(
    world_id: str,
    characters: CharactersDep,
    library: LibraryDep,
    file: UploadFile,
) -> dict[str, Any]:
```

The test fixture in `test_imports_routes.py` already wires `container.library = library`, so the dep resolves.

- [ ] **Step 4: Run the new + existing preview tests**

```bash
cd backend && uv run pytest tests/api/test_imports_routes.py -v
```

Expected: all green (the new test plus all five existing ones).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/api/imports.py backend/tests/api/test_imports_routes.py
git commit -m "$(cat <<'EOF'
feat(api): return lore_suggestions on the card-import preview

Runs suggest_kind over every IngestedLoreEntry server-side and returns a
parallel array of {source_index, kind, confidence, reason}, so the import
dialog can seed its per-row dropdowns without per-row HTTP roundtrips.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Extend `CommitPayload` with `lore_overrides` + validation

**Files:**
- Modify: `backend/src/grimoire/api/imports.py` (`CommitPayload`, `commit_sillytavern_import`)
- Modify: `backend/tests/api/test_imports_routes.py` (validation cases)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/api/test_imports_routes.py`:

```python
def _card_with_two_lore() -> dict:
    return {
        "spec": "chara_card_v2",
        "data": {
            "name": "Beatrice",
            "description": "A witch.",
            "first_mes": "Hi.",
            "character_book": {
                "entries": [
                    {"name": "Brackhollow Cathedral", "keys": ["cathedral"], "content": "A village cathedral located on the hill."},
                    {"name": "Some Note", "keys": ["note"], "content": "Random fact."},
                ],
            },
        },
    }


async def test_commit_rejects_unknown_lore_override_kind(client) -> None:
    ac, _ = client
    png = _png_with_card(_card_with_two_lore())
    response = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/preview",
        files={"file": ("card.png", png, "image/png")},
    )
    preview_id = response.json()["preview_id"]
    commit = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/commit",
        json={
            "preview_id": preview_id,
            "options": {},
            "lore_overrides": [{"source_index": 0, "kind": "quest"}],
        },
    )
    assert commit.status_code == 422  # pydantic Literal mismatch


async def test_commit_rejects_duplicate_source_index(client) -> None:
    ac, _ = client
    png = _png_with_card(_card_with_two_lore())
    preview = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/preview",
        files={"file": ("card.png", png, "image/png")},
    )
    preview_id = preview.json()["preview_id"]
    commit = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/commit",
        json={
            "preview_id": preview_id,
            "options": {},
            "lore_overrides": [
                {"source_index": 0, "kind": "skip"},
                {"source_index": 0, "kind": "character"},
            ],
        },
    )
    assert commit.status_code == 400
    assert "declared twice" in commit.text


async def test_commit_rejects_out_of_range_source_index(client) -> None:
    ac, _ = client
    png = _png_with_card(_card_with_two_lore())
    preview = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/preview",
        files={"file": ("card.png", png, "image/png")},
    )
    preview_id = preview.json()["preview_id"]
    commit = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/commit",
        json={
            "preview_id": preview_id,
            "options": {},
            "lore_overrides": [{"source_index": 99, "kind": "character"}],
        },
    )
    assert commit.status_code == 400
    assert "source_index" in commit.text


async def test_commit_rejects_missing_required_override(client) -> None:
    ac, _ = client
    png = _png_with_card(_card_with_two_lore())
    preview = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/preview",
        files={"file": ("card.png", png, "image/png")},
    )
    preview_id = preview.json()["preview_id"]
    commit = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/commit",
        json={
            "preview_id": preview_id,
            "options": {},
            "lore_overrides": [{"source_index": 0, "kind": "location"}],  # missing 'kind' override
        },
    )
    assert commit.status_code == 400
    assert "required override" in commit.text.lower() or "kind" in commit.text
```

- [ ] **Step 2: Run and verify failure**

```bash
cd backend && uv run pytest tests/api/test_imports_routes.py -v -k "rejects_unknown_lore_override_kind or rejects_duplicate_source_index or rejects_out_of_range_source_index or rejects_missing_required_override"
```

Expected: FAIL — `CommitPayload` rejects the unknown field `lore_overrides` (extra-forbid hits, or the field is silently ignored — either way the duplicate/range checks aren't there yet).

- [ ] **Step 3: Extend `CommitPayload` and add validation**

Edit `backend/src/grimoire/api/imports.py`:

Add imports near the top:

```python
from grimoire.library.reclassify import required_overrides_for, _lore_entry_from_ingested
from grimoire.types.characters import LoreOverride
from grimoire.types.common import EntityKind
```

Replace the `CommitPayload` class:

```python
class CommitPayload(BaseModel):
    preview_id: str
    options: dict[str, Any] = Field(default_factory=dict)
    lore_overrides: list[LoreOverride] = Field(default_factory=list)
```

Replace the `commit_sillytavern_import` function body. Slot validation between the world-id check and the `IngestOptions` validation:

```python
@router.post("/library/worlds/{world_id}/imports/sillytavern/commit", status_code=201)
async def commit_sillytavern_import(
    world_id: str,
    payload: CommitPayload,
    characters: CharactersDep,
) -> dict[str, Any]:
    """Commit a previously previewed card to ``world_id``."""
    _gc_expired()
    slot = _PREVIEW_CACHE.pop(payload.preview_id, None)
    if slot is None:
        raise HTTPException(status_code=404, detail="preview not found or expired")
    if slot.world_id != world_id:
        raise HTTPException(
            status_code=400,
            detail=f"preview was created for {slot.world_id!r}, not {world_id!r}",
        )

    valid_indices = {entry.source_index for entry in slot.ingested.lore_entries}
    seen_indices: set[int] = set()
    for override in payload.lore_overrides:
        if override.source_index in seen_indices:
            raise HTTPException(
                status_code=400,
                detail=f"lore override for source_index={override.source_index} declared twice",
            )
        seen_indices.add(override.source_index)
        if override.source_index not in valid_indices:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"lore override source_index={override.source_index} not present "
                    f"in preview (valid: {sorted(valid_indices)})"
                ),
            )
        if override.kind not in ("lore", "skip"):
            required = required_overrides_for(EntityKind(override.kind))
            missing = [k for k in required if k not in override.overrides]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"lore override for source_index={override.source_index} "
                        f"(kind={override.kind}) missing required override(s): {missing}"
                    ),
                )

    try:
        options = IngestOptions.model_validate(payload.options or {})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"bad options: {exc}") from exc
    result = await characters._finalize_import(
        world_id,
        slot.ingested,
        options=options,
    )
    return {"result": to_payload(result)}
```

(Note: the route validates `lore_overrides` but does NOT yet forward them into `_finalize_import` — Task 6 adds both the kwarg to the service method and the call-site forwarding. Keeping the call-site unchanged here means the existing route tests continue to pass after Task 5; the validation tests pass because they 400 before reaching the service call.)

- [ ] **Step 4: Run the new validation tests + the full route file**

```bash
cd backend && uv run pytest tests/api/test_imports_routes.py -v
```

Expected: all green — the four new 400/422 tests pass, and the existing six route tests still pass (the service call is unchanged in this task; Task 6 wires forwarding through).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/api/imports.py backend/tests/api/test_imports_routes.py
git commit -m "$(cat <<'EOF'
feat(api): validate lore_overrides on commit payload

CommitPayload accepts a list of LoreOverride. The route validates kind
against the literal set (via pydantic), rejects duplicate or out-of-range
source_index values, and re-checks required_overrides_for any non-lore /
non-skip kind. The payload is forwarded to _finalize_import; the service
adopts it in the next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `_write_lore_entries` dispatch (lore / skip / promote)

**Files:**
- Modify: `backend/src/grimoire/characters/service.py` (`_finalize_import`, `_write_lore_entries`)
- Modify: `backend/tests/characters/test_import_card_writes.py` (dispatch coverage)
- Modify: `backend/tests/api/test_imports_routes.py` (end-to-end happy path)

- [ ] **Step 1: Write the failing tests (service-level)**

Append to `backend/tests/characters/test_import_card_writes.py`:

```python
from grimoire.types.characters import LoreOverride


async def test_lore_override_promotes_to_character(
    characters: CharactersService, library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "w1")
    book = {
        "entries": [
            {"name": "Lyra Wynn", "keys": ["Lyra"], "content": "She rides at dusk."},
            {"name": "Tremere", "keys": ["Tremere"], "content": "A vampire clan."},
        ]
    }
    raw = json.dumps(_card(character_book=book)).encode("utf-8")
    ingested = await characters._ingest(raw)
    result = await characters._finalize_import(
        "w1",
        ingested,
        lore_overrides=[LoreOverride(source_index=0, kind="character")],
    )
    assert any(ref.startswith("character:beatrice--lyra-wynn") for ref in result.created)
    assert any(ref == "lore:beatrice--tremere" for ref in result.created)
    promoted = await library.get_entity("w1", "character", "beatrice--lyra-wynn")
    assert promoted.frontmatter["name"] == "Lyra Wynn"
    assert promoted.frontmatter["import_source"]["kind"] == "sillytavern_character_book"
    assert promoted.frontmatter["import_source"]["source_index"] == 0


async def test_lore_override_promotes_to_location_with_required_kind(
    characters: CharactersService, library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "w1")
    book = {"entries": [{"name": "Brackhollow Inn", "keys": ["inn"], "content": "Cosy."}]}
    raw = json.dumps(_card(character_book=book)).encode("utf-8")
    ingested = await characters._ingest(raw)
    result = await characters._finalize_import(
        "w1",
        ingested,
        lore_overrides=[
            LoreOverride(source_index=0, kind="location", overrides={"kind": "building"}),
        ],
    )
    assert any(ref.startswith("location:beatrice--brackhollow-inn") for ref in result.created)
    loc = await library.get_entity("w1", "location", "beatrice--brackhollow-inn")
    assert loc.frontmatter["name"] == "Brackhollow Inn"
    assert loc.frontmatter["kind"] == "building"
    assert loc.frontmatter["import_source"]["kind"] == "sillytavern_character_book"


async def test_lore_override_skip_omits_entry_and_warns(
    characters: CharactersService, library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "w1")
    book = {
        "entries": [
            {"name": "Tremere", "keys": ["Tremere"], "content": "A clan."},
            {"name": "Camarilla", "keys": ["Camarilla"], "content": "A sect."},
        ]
    }
    raw = json.dumps(_card(character_book=book)).encode("utf-8")
    ingested = await characters._ingest(raw)
    result = await characters._finalize_import(
        "w1",
        ingested,
        lore_overrides=[LoreOverride(source_index=0, kind="skip")],
    )
    assert not any("tremere" in ref for ref in result.created)
    assert any("camarilla" in ref for ref in result.created)
    assert any("skipped" in w.lower() and "0" in w for w in result.warnings)


async def test_lore_override_lore_kind_behaves_as_default(
    characters: CharactersService, library: LibraryService, store: StateStore
) -> None:
    """Explicit kind='lore' must produce identical output to no override."""
    await _seed_world(store, "w1")
    book = {"entries": [{"name": "Tremere", "keys": ["Tremere"], "content": "A clan."}]}
    raw = json.dumps(_card(character_book=book)).encode("utf-8")
    ingested = await characters._ingest(raw)
    result = await characters._finalize_import(
        "w1",
        ingested,
        lore_overrides=[LoreOverride(source_index=0, kind="lore")],
    )
    assert any(ref == "lore:beatrice--tremere" for ref in result.created)


async def test_lore_override_promotion_uses_unique_id_on_collision(
    characters: CharactersService, library: LibraryService, store: StateStore
) -> None:
    await _seed_world(store, "w1")
    # Pre-seed a character whose id collides with what the import will derive.
    await library.create_entity(
        "w1",
        "character",
        "beatrice--lyra-wynn",
        {"id": "beatrice--lyra-wynn", "name": "Existing Lyra"},
        body="placeholder",
        source="test:seed",
    )
    book = {"entries": [{"name": "Lyra Wynn", "keys": ["lyra"], "content": "Rides at dusk."}]}
    raw = json.dumps(_card(character_book=book)).encode("utf-8")
    ingested = await characters._ingest(raw)
    result = await characters._finalize_import(
        "w1",
        ingested,
        lore_overrides=[LoreOverride(source_index=0, kind="character")],
    )
    # Should suffix-2 instead of overwriting the seeded character.
    assert any(ref == "character:beatrice--lyra-wynn-2" for ref in result.created)
```

Also append this end-to-end happy-path test to `backend/tests/api/test_imports_routes.py`:

```python
async def test_commit_with_lore_override_promotes_entity(client) -> None:
    ac, _ = client
    png = _png_with_card(_card_with_two_lore())
    preview = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/preview",
        files={"file": ("card.png", png, "image/png")},
    )
    preview_id = preview.json()["preview_id"]
    commit = await ac.post(
        "/api/library/worlds/w1/imports/sillytavern/commit",
        json={
            "preview_id": preview_id,
            "options": {},
            "lore_overrides": [
                {"source_index": 0, "kind": "location", "overrides": {"kind": "building"}},
                {"source_index": 1, "kind": "skip"},
            ],
        },
    )
    assert commit.status_code == 201, commit.text
    created = commit.json()["result"]["created"]
    assert any(c.startswith("location:beatrice--brackhollow-inn") for c in created)
    assert not any(c.startswith("lore:") for c in created)
```

- [ ] **Step 2: Run and verify failure**

```bash
cd backend && uv run pytest tests/characters/test_import_card_writes.py -v -k "lore_override"
cd backend && uv run pytest tests/api/test_imports_routes.py::test_commit_with_lore_override_promotes_entity -v
```

Expected: FAIL with `TypeError: _finalize_import() got an unexpected keyword argument 'lore_overrides'`.

- [ ] **Step 3: Thread `lore_overrides` through the route → `_finalize_import` → `_write_lore_entries`**

First, update the route call site in `backend/src/grimoire/api/imports.py` — at the end of `commit_sillytavern_import`, change the `_finalize_import` call to pass `lore_overrides`:

```python
    result = await characters._finalize_import(
        world_id,
        slot.ingested,
        options=options,
        lore_overrides=payload.lore_overrides,
    )
```

Then in `backend/src/grimoire/characters/service.py`:

Add the imports at the top:

```python
from grimoire.library.reclassify import apply_mapping, _lore_entry_from_ingested
from grimoire.types.characters import LoreOverride
from grimoire.types.common import EntityKind
```

Change `_finalize_import` signature and the `_write_lore_entries` call site:

```python
    async def _finalize_import(
        self,
        target_world_id: str,
        ingested: IngestedCharacterCard,
        *,
        options: IngestOptions | None = None,
        lore_overrides: list[LoreOverride] | None = None,
    ) -> ImportResult:
        opts = options or IngestOptions()
        data = ingested.data
        result = ImportResult(warnings=list(ingested.warnings))
        ...
        if opts.import_character_book and ingested.lore_entries:
            await self._write_lore_entries(
                target_world_id=target_world_id,
                char_slug=data.id,
                ingested=ingested,
                result=result,
                lore_overrides=lore_overrides or [],
            )
```

Then replace `_write_lore_entries` with the dispatching version. Keep the existing lore-write logic intact for the default branch; add skip and promote branches:

```python
    async def _write_lore_entries(
        self,
        *,
        target_world_id: str,
        char_slug: str,
        ingested: IngestedCharacterCard,
        result: ImportResult,
        lore_overrides: list[LoreOverride] = (),
    ) -> None:
        overrides_by_index = {o.source_index: o for o in lore_overrides}
        for entry in ingested.lore_entries:
            override = overrides_by_index.get(entry.source_index)
            target_kind = override.kind if override else "lore"

            if target_kind == "skip":
                result.warnings.append(
                    f"lore entry {entry.source_index} skipped by user override"
                )
                continue

            if target_kind == "lore":
                await self._write_one_lore_entry(
                    target_world_id=target_world_id,
                    char_slug=char_slug,
                    entry=entry,
                    result=result,
                )
                continue

            await self._promote_lore_entry(
                target_world_id=target_world_id,
                char_slug=char_slug,
                entry=entry,
                target_kind=EntityKind(target_kind),
                overrides=override.overrides if override else {},
                result=result,
            )

    async def _write_one_lore_entry(
        self,
        *,
        target_world_id: str,
        char_slug: str,
        entry: IngestedLoreEntry,
        result: ImportResult,
    ) -> None:
        entry_slug = _slug_for_lore_entry(entry, char_slug)
        base_id = f"{char_slug}--{entry_slug}"
        entity_id = await self._unique_id(target_world_id, "lore", base_id, result)
        frontmatter: dict[str, Any] = {
            "id": entity_id,
            "name": entry.name or entity_id,
            "title": entry.name or entity_id,
            "keywords": entry.keys,
            "secondary_keys": entry.secondary_keys,
            "selective_logic": entry.selective_logic,
            "constant": entry.constant,
            "enabled": entry.enabled,
            "case_sensitive": entry.case_sensitive,
            "match_whole_words": entry.match_whole_words,
            "priority": entry.priority,
            "probability": entry.probability,
            "position": entry.position,
            "comment": entry.comment,
            "tags": ["imported", "from-card", char_slug],
            "import_source": {
                "kind": "sillytavern_character_book",
                "card_asset_id": char_slug,
                "source_index": entry.source_index,
            },
        }
        if entry.at_depth is not None:
            frontmatter["at_depth"] = entry.at_depth
        if entry.scan_depth is not None:
            frontmatter["scan_depth"] = entry.scan_depth
        try:
            await self.library.create_entity(
                target_world_id,
                "lore",
                entity_id,
                frontmatter,
                body=entry.body,
                source="characters:import",
            )
            result.created.append(f"lore:{entity_id}")
        except Exception as exc:
            result.errors.append(f"lore {entity_id!r}: {exc}")

    async def _promote_lore_entry(
        self,
        *,
        target_world_id: str,
        char_slug: str,
        entry: IngestedLoreEntry,
        target_kind: EntityKind,
        overrides: dict[str, Any],
        result: ImportResult,
    ) -> None:
        proxy = _lore_entry_from_ingested(entry, world_id=target_world_id)
        fm, body, _kept, _dropped, _into_notes, warnings = apply_mapping(
            proxy, target_kind, overrides
        )
        entry_slug = _slug_for_lore_entry(entry, char_slug)
        base_id = f"{char_slug}--{entry_slug}"
        kind_str = target_kind.value
        entity_id = await self._unique_id(target_world_id, kind_str, base_id, result)
        fm["id"] = entity_id
        fm["import_source"] = {
            "kind": "sillytavern_character_book",
            "card_asset_id": char_slug,
            "source_index": entry.source_index,
        }
        try:
            await self.library.create_entity(
                target_world_id,
                kind_str,
                entity_id,
                fm,
                body=body,
                source="characters:import",
            )
            result.created.append(f"{kind_str}:{entity_id}")
            for w in warnings:
                result.warnings.append(f"{kind_str} {entity_id}: {w}")
        except Exception as exc:
            result.errors.append(f"{kind_str} {entity_id!r}: {exc}")
```

(The existing single big `_write_lore_entries` body becomes the new `_write_one_lore_entry`. Remove the old body from `_write_lore_entries` and replace with the dispatching version above.)

- [ ] **Step 4: Run the new + existing import tests**

```bash
cd backend && uv run pytest tests/characters/ tests/api/test_imports_routes.py -v
```

Expected: all green. The existing `test_character_book_entries_become_lore_files`, `test_macro_pass_applied_to_lore_body`, etc. continue to pass because the no-override path delegates straight to `_write_one_lore_entry` with identical behavior.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/api/imports.py backend/src/grimoire/characters/service.py backend/tests/characters/test_import_card_writes.py backend/tests/api/test_imports_routes.py
git commit -m "$(cat <<'EOF'
feat(characters): dispatch lore_overrides in _write_lore_entries

_finalize_import accepts lore_overrides and forwards them. Per-row
dispatch: skip drops the entry with a warning; lore-as-lore stays
bit-for-bit identical to today (extracted into _write_one_lore_entry);
target kinds run apply_mapping and write via LibraryService.create_entity
with a re-derived unique id and an import_source pointing back at the
card.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Frontend types + `commitSillyTavernImport` signature

**Files:**
- Modify: `frontend/src/api/imports.ts` (types + commit signature)

- [ ] **Step 1: Update types and the commit function**

Replace the existing `frontend/src/api/imports.ts` content for the affected sections:

Add the new exported types after `PreviewResponse`:

```typescript
export type LoreOverrideKind =
  | "lore"
  | "character"
  | "location"
  | "faction"
  | "item"
  | "skip";

export interface LoreSuggestion {
  source_index: number;
  kind: LoreOverrideKind;          // "lore" when below threshold, never "skip"
  confidence: number;
  reason: string;
}

export interface LoreOverridePayload {
  source_index: number;
  kind: LoreOverrideKind;
  overrides?: Record<string, string>;
}
```

Modify `PreviewResponse`:

```typescript
export interface PreviewResponse {
  preview_id: string;
  expires_in_seconds: number;
  ingested: IngestedCardPreview;
  lore_suggestions: LoreSuggestion[];
}
```

Add the required-overrides helper (mirrors the backend's `_REQUIRED_OVERRIDES` table):

```typescript
/**
 * Mirrors backend reclassify._REQUIRED_OVERRIDES. Keep in sync.
 * Source of truth: backend/src/grimoire/library/reclassify.py.
 */
export function requiredOverridesFor(kind: LoreOverrideKind): string[] {
  switch (kind) {
    case "location":
      return ["kind"];
    case "character":
    case "faction":
    case "item":
    case "lore":
    case "skip":
      return [];
  }
}
```

Modify `commitSillyTavernImport`:

```typescript
export async function commitSillyTavernImport(
  worldId: string,
  previewId: string,
  options: IngestOptionsPayload,
  loreOverrides: LoreOverridePayload[] = [],
): Promise<CommitResponse> {
  const res = await fetch(
    `${API_BASE}/library/worlds/${encodeURIComponent(worldId)}/imports/sillytavern/commit`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        preview_id: previewId,
        options,
        lore_overrides: loreOverrides,
      }),
    },
  );
  if (!res.ok) {
    throw new ApiError(res.status, await res.text().catch(() => ""));
  }
  return (await res.json()) as CommitResponse;
}
```

- [ ] **Step 2: Verify the frontend still typechecks**

```bash
cd frontend && pnpm typecheck
```

Expected: no errors. The existing `ImportDialog.tsx` still calls `commitSillyTavernImport(worldId, preview.preview_id, options)` (three args); the new fourth arg defaults to `[]`, so the call compiles unchanged.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/imports.ts
git commit -m "$(cat <<'EOF'
feat(frontend): types and commit signature for lore_overrides

PreviewResponse carries lore_suggestions, commitSillyTavernImport accepts
a parallel lore_overrides list (default empty so existing callers keep
working), and requiredOverridesFor mirrors the backend's per-kind table.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Frontend `ImportDialog` per-row dropdown UI + tests

**Files:**
- Modify: `frontend/src/routes/library/ImportDialog.tsx`
- Create: `frontend/src/routes/library/__tests__/ImportDialog.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/routes/library/__tests__/ImportDialog.test.tsx`:

```typescript
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ImportDialog } from "../ImportDialog";
import * as importsModule from "../../../api/imports";

vi.mock("../../../api/imports", async () => {
  const actual = await vi.importActual<typeof importsModule>("../../../api/imports");
  return {
    ...actual,
    previewSillyTavernImport: vi.fn(),
    commitSillyTavernImport: vi.fn(),
  };
});

function makePreview() {
  return {
    preview_id: "pid-1",
    expires_in_seconds: 900,
    ingested: {
      data: { id: "beatrice", name: "Beatrice", description: "A witch.", tags: ["witch"] },
      spec: "chara_card_v2",
      spec_version: "",
      creator: "",
      creator_notes: "",
      system_prompt: "",
      post_history_instructions: "",
      alternate_greetings: [],
      extensions: {},
      warnings: [],
      lore_entries: [
        {
          source_index: 0,
          name: "Brackhollow Inn",
          keys: ["inn"],
          body: "A village inn.",
          secondary_keys: [],
          selective_logic: "and_any",
          constant: false,
          enabled: true,
          case_sensitive: false,
          match_whole_words: false,
          priority: 100,
          probability: 100,
          position: "after_cast",
          at_depth: null,
          scan_depth: null,
          comment: "",
        },
        {
          source_index: 1,
          name: "Random Note",
          keys: ["note"],
          body: "Some random fact.",
          secondary_keys: [],
          selective_logic: "and_any",
          constant: false,
          enabled: true,
          case_sensitive: false,
          match_whole_words: false,
          priority: 100,
          probability: 100,
          position: "after_cast",
          at_depth: null,
          scan_depth: null,
          comment: "",
        },
      ],
      greetings: [],
    },
    lore_suggestions: [
      { source_index: 0, kind: "location", confidence: 0.82, reason: "title contains a place noun" },
      { source_index: 1, kind: "lore", confidence: 0.0, reason: "" },
    ],
  };
}

describe("ImportDialog reclassification flow", () => {
  beforeEach(() => {
    vi.mocked(importsModule.previewSillyTavernImport).mockResolvedValue(makePreview());
    vi.mocked(importsModule.commitSillyTavernImport).mockResolvedValue({
      result: { created: ["beatrice"], updated: [], skipped: [], warnings: [], errors: [] },
    });
  });

  async function uploadFile() {
    render(<ImportDialog worldId="w1" onClose={() => {}} />);
    const input = await screen.findByLabelText(/Select a SillyTavern card/i);
    fireEvent.change(input, {
      target: { files: [new File(["x"], "card.png", { type: "image/png" })] },
    });
    await waitFor(() =>
      expect(importsModule.previewSillyTavernImport).toHaveBeenCalledTimes(1),
    );
    // Wait for the lore-row UI to render.
    await screen.findByText(/Brackhollow Inn/);
  }

  it("defaults the dropdown to the server suggestion when above threshold", async () => {
    await uploadFile();
    const selects = screen.getAllByLabelText(/Category for/i);
    expect((selects[0] as HTMLSelectElement).value).toBe("location");
    expect((selects[1] as HTMLSelectElement).value).toBe("lore");
  });

  it("reveals the Location 'kind' input when a row's target is Location", async () => {
    await uploadFile();
    const kindInput = await screen.findByLabelText(/Location kind \(row 0\)/i);
    expect(kindInput).toBeInTheDocument();
  });

  it("blocks commit and shows an error when a required override is missing", async () => {
    await uploadFile();
    const commit = await screen.findByRole("button", { name: /^Commit$/ });
    fireEvent.click(commit);
    expect(
      await screen.findByText(/Location row 0 requires kind/i),
    ).toBeInTheDocument();
    expect(importsModule.commitSillyTavernImport).not.toHaveBeenCalled();
  });

  it("commits with the right lore_overrides shape and excludes lore-as-lore rows", async () => {
    await uploadFile();
    const kindInput = await screen.findByLabelText(/Location kind \(row 0\)/i);
    fireEvent.change(kindInput, { target: { value: "building" } });
    // Set the second row to skip.
    const selects = screen.getAllByLabelText(/Category for/i);
    fireEvent.change(selects[1], { target: { value: "skip" } });

    fireEvent.click(await screen.findByRole("button", { name: /^Commit$/ }));
    await waitFor(() =>
      expect(importsModule.commitSillyTavernImport).toHaveBeenCalledTimes(1),
    );
    const args = vi.mocked(importsModule.commitSillyTavernImport).mock.calls[0];
    expect(args[0]).toBe("w1");
    expect(args[1]).toBe("pid-1");
    expect(args[3]).toEqual([
      { source_index: 0, kind: "location", overrides: { kind: "building" } },
      { source_index: 1, kind: "skip", overrides: {} },
    ]);
  });
});
```

- [ ] **Step 2: Run and verify failure**

```bash
cd frontend && pnpm test -- --run src/routes/library/__tests__/ImportDialog.test.tsx
```

Expected: FAIL — the dropdown / accessible labels don't exist yet.

- [ ] **Step 3: Update `ImportDialog.tsx`**

Replace the file's contents with this implementation:

```tsx
/**
 * Character-card import dialog (specs 2026-05-19-card-imports §REST/§UI
 * and 2026-05-20-import-dialog-reclassify §4).
 *
 * Two-step flow: upload → preview (parsed character + greetings + lore
 * + warnings) → toggles + per-row category dropdowns → commit. The
 * component is self-contained; callers render it as a modal under
 * WorldDetailView and pass an ``onClose`` callback that closes the
 * dialog and triggers a reload of the library list.
 */

import { useMemo, useState } from "react";

import {
  type CommitResponse,
  type IngestOptionsPayload,
  type LoreOverrideKind,
  type LoreOverridePayload,
  type LoreSuggestion,
  type PreviewResponse,
  commitSillyTavernImport,
  previewSillyTavernImport,
  requiredOverridesFor,
} from "../../api/imports";

interface Props {
  worldId: string;
  onClose: (committed: boolean) => void;
}

type Mode = "pick" | "previewing" | "preview" | "committing" | "done" | "error";

const DEFAULT_OPTIONS: Required<IngestOptionsPayload> = {
  expand_macros: true,
  import_character_book: true,
  import_alternate_greetings: true,
  import_primary_greeting: true,
  keep_embedded_avatar: true,
  extract_relationships: true,
  derive_image_prompt: true,
};

const KIND_OPTIONS: { value: LoreOverrideKind; label: string }[] = [
  { value: "lore", label: "Lore" },
  { value: "character", label: "Character" },
  { value: "location", label: "Location" },
  { value: "faction", label: "Faction" },
  { value: "item", label: "Item" },
  { value: "skip", label: "Skip" },
];

interface LoreRowState {
  source_index: number;
  kind: LoreOverrideKind;
  overrides: Record<string, string>;
}

function initialRows(preview: PreviewResponse): LoreRowState[] {
  const byIndex = new Map<number, LoreSuggestion>();
  for (const s of preview.lore_suggestions) byIndex.set(s.source_index, s);
  return preview.ingested.lore_entries.map((entry) => {
    const suggestion = byIndex.get(entry.source_index);
    return {
      source_index: entry.source_index,
      kind: (suggestion?.kind ?? "lore") as LoreOverrideKind,
      overrides: {},
    };
  });
}

export function ImportDialog({ worldId, onClose }: Props) {
  const [mode, setMode] = useState<Mode>("pick");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [commitResult, setCommitResult] = useState<CommitResponse | null>(null);
  const [options, setOptions] = useState<Required<IngestOptionsPayload>>(
    DEFAULT_OPTIONS,
  );
  const [loreRows, setLoreRows] = useState<LoreRowState[]>([]);

  const suggestionsByIndex = useMemo(() => {
    const m = new Map<number, LoreSuggestion>();
    if (preview) for (const s of preview.lore_suggestions) m.set(s.source_index, s);
    return m;
  }, [preview]);

  async function handleFile(file: File) {
    setMode("previewing");
    setErrorMsg("");
    try {
      const response = await previewSillyTavernImport(worldId, file);
      setPreview(response);
      setLoreRows(initialRows(response));
      setMode("preview");
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
      setMode("error");
    }
  }

  function setRowKind(sourceIndex: number, kind: LoreOverrideKind) {
    setLoreRows((rows) =>
      rows.map((row) =>
        row.source_index === sourceIndex
          ? { ...row, kind, overrides: {} }
          : row,
      ),
    );
  }

  function setRowOverride(sourceIndex: number, key: string, value: string) {
    setLoreRows((rows) =>
      rows.map((row) =>
        row.source_index === sourceIndex
          ? { ...row, overrides: { ...row.overrides, [key]: value } }
          : row,
      ),
    );
  }

  async function handleCommit() {
    if (!preview) return;
    // Validate required overrides per row.
    for (const row of loreRows) {
      const required = requiredOverridesFor(row.kind);
      const missing = required.filter((k) => !row.overrides[k]?.trim());
      if (missing.length > 0) {
        setErrorMsg(
          `${row.kind === "location" ? "Location" : row.kind} row ${row.source_index} requires ${missing.join(", ")}`,
        );
        setMode("preview");
        return;
      }
    }
    const overridesPayload: LoreOverridePayload[] = loreRows
      .filter((row) => row.kind !== "lore")
      .map((row) => ({
        source_index: row.source_index,
        kind: row.kind,
        overrides: row.overrides,
      }));
    setMode("committing");
    setErrorMsg("");
    try {
      const response = await commitSillyTavernImport(
        worldId,
        preview.preview_id,
        options,
        overridesPayload,
      );
      setCommitResult(response);
      setMode("done");
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
      setMode("error");
    }
  }

  function toggle<K extends keyof Required<IngestOptionsPayload>>(key: K) {
    setOptions((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  return (
    <div className="import-dialog" role="dialog" aria-label="Import character card">
      <header className="import-dialog-header">
        <h3>Import character card</h3>
        <button type="button" onClick={() => onClose(mode === "done")}>
          {mode === "done" ? "Close" : "Cancel"}
        </button>
      </header>

      {mode === "pick" && (
        <label className="import-dialog-pick">
          <span>Select a SillyTavern card (PNG / charx / JSON):</span>
          <input
            type="file"
            accept=".png,.json,.charx,application/json,image/png,application/zip"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void handleFile(file);
            }}
          />
        </label>
      )}

      {mode === "previewing" && <p>Parsing card…</p>}
      {mode === "committing" && <p>Writing to library…</p>}

      {(mode === "preview" || mode === "error") && preview && (
        <section className="import-dialog-preview">
          <h4>{preview.ingested.data.name}</h4>
          {preview.ingested.data.description && (
            <p className="import-dialog-description">
              {preview.ingested.data.description}
            </p>
          )}

          <fieldset className="import-dialog-options">
            <legend>What to import</legend>
            <label>
              <input
                type="checkbox"
                checked={options.expand_macros}
                onChange={() => toggle("expand_macros")}
              />
              Expand {"{{char}}/{{random}}/{{roll}}"} macros at ingest
            </label>
            <label>
              <input
                type="checkbox"
                checked={options.import_primary_greeting}
                onChange={() => toggle("import_primary_greeting")}
              />
              Import primary greeting (first_mes)
            </label>
            <label>
              <input
                type="checkbox"
                checked={options.import_alternate_greetings}
                onChange={() => toggle("import_alternate_greetings")}
              />
              Import alternate greetings ({preview.ingested.alternate_greetings.length})
            </label>
            <label>
              <input
                type="checkbox"
                checked={options.import_character_book}
                onChange={() => toggle("import_character_book")}
              />
              Import character_book entries (
              {preview.ingested.lore_entries.length})
            </label>
          </fieldset>

          {preview.ingested.greetings.length > 0 && (
            <details>
              <summary>
                Greetings ({preview.ingested.greetings.length})
              </summary>
              <ul>
                {preview.ingested.greetings.map((g) => (
                  <li key={g.source_index}>
                    <strong>{g.is_primary ? "Primary" : `Alt ${g.source_index}`}:</strong>{" "}
                    {g.body.slice(0, 120)}
                    {g.body.length > 120 ? "…" : ""}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {preview.ingested.lore_entries.length > 0 && options.import_character_book && (
            <fieldset className="import-dialog-lore-rows">
              <legend>
                Lore entries ({preview.ingested.lore_entries.length}) — pick a category per row
              </legend>
              <ul>
                {preview.ingested.lore_entries.map((entry) => {
                  const row = loreRows.find(
                    (r) => r.source_index === entry.source_index,
                  );
                  if (!row) return null;
                  const suggestion = suggestionsByIndex.get(entry.source_index);
                  const required = requiredOverridesFor(row.kind);
                  const label =
                    entry.name || entry.keys[0] || `entry-${entry.source_index}`;
                  return (
                    <li key={entry.source_index} className="import-dialog-lore-row">
                      <span className="import-dialog-lore-row-name">
                        <strong>{label}</strong>
                        {entry.keys.length > 0 && (
                          <> — keys: {entry.keys.join(", ")}</>
                        )}
                      </span>
                      <label>
                        <span className="visually-hidden">
                          Category for row {entry.source_index}
                        </span>
                        <select
                          aria-label={`Category for row ${entry.source_index}`}
                          value={row.kind}
                          onChange={(e) =>
                            setRowKind(
                              entry.source_index,
                              e.target.value as LoreOverrideKind,
                            )
                          }
                        >
                          {KIND_OPTIONS.map((opt) => (
                            <option key={opt.value} value={opt.value}>
                              {opt.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      {suggestion && suggestion.kind !== "lore" && suggestion.reason && (
                        <span
                          className="import-dialog-lore-row-why"
                          title={suggestion.reason}
                        >
                          Why?
                        </span>
                      )}
                      {required.map((key) => (
                        <label key={key} className="import-dialog-lore-row-override">
                          <span>
                            {row.kind.charAt(0).toUpperCase() + row.kind.slice(1)} {key}
                          </span>
                          <input
                            type="text"
                            aria-label={`${row.kind.charAt(0).toUpperCase() + row.kind.slice(1)} ${key} (row ${entry.source_index})`}
                            value={row.overrides[key] ?? ""}
                            onChange={(e) =>
                              setRowOverride(
                                entry.source_index,
                                key,
                                e.target.value,
                              )
                            }
                          />
                        </label>
                      ))}
                    </li>
                  );
                })}
              </ul>
            </fieldset>
          )}

          {preview.ingested.warnings.length > 0 && (
            <details>
              <summary>Warnings ({preview.ingested.warnings.length})</summary>
              <ul>
                {preview.ingested.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </details>
          )}

          <p className="import-dialog-note">
            Character-scoped lore coming in a future release; for now lore
            lands at world scope.
          </p>

          {errorMsg && (
            <p className="import-dialog-error" role="alert">
              {errorMsg}
            </p>
          )}

          <div className="import-dialog-actions">
            <button type="button" onClick={() => void handleCommit()}>
              Commit
            </button>
          </div>
        </section>
      )}

      {mode === "done" && commitResult && (
        <section className="import-dialog-done">
          <h4>Import complete</h4>
          <p>
            Created {commitResult.result.created.length} entries
            {commitResult.result.errors.length > 0
              ? `, ${commitResult.result.errors.length} errors`
              : ""}
            .
          </p>
          <details>
            <summary>Files created</summary>
            <ul>
              {commitResult.result.created.map((c) => (
                <li key={c}>
                  <code>{c}</code>
                </li>
              ))}
            </ul>
          </details>
          {commitResult.result.errors.length > 0 && (
            <details open>
              <summary>Errors</summary>
              <ul>
                {commitResult.result.errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}

      {mode === "error" && !preview && (
        <p className="import-dialog-error" role="alert">
          {errorMsg}
        </p>
      )}
    </div>
  );
}
```

Notes about the rewrite:
- The lore preview block changes from a `<details>` summary to a `<fieldset>` of structured rows, each with its dropdown and optional override input. The summary text remains in the legend so users still see the count.
- The error state for missing-overrides re-enters `mode="preview"` so the user can fix the field without losing their dropdown selections. The error string surfaces inline in red (`role="alert"`).
- The label `Category for row {source_index}` is the testing seam.
- `requiredOverridesFor` is single-source-of-truth-mirrored from the backend.

- [ ] **Step 4: Run the new + existing frontend tests**

```bash
cd frontend && pnpm test -- --run
```

Expected: all tests pass — the new `ImportDialog.test.tsx` cases plus the 61 existing tests (none of which exercised `ImportDialog`, so nothing should regress).

- [ ] **Step 5: Run typecheck**

```bash
cd frontend && pnpm typecheck
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/library/ImportDialog.tsx frontend/src/routes/library/__tests__/ImportDialog.test.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): per-row category dropdown in card import dialog

Each lore row gets a Lore/Character/Location/Faction/Item/Skip select,
seeded from the server-side suggest_kind result. When the chosen target
needs required overrides (only Location.kind in v1), an inline input
appears and commit is blocked until it is filled. Skipped rows are
excluded from the commit payload; lore-as-lore rows produce no override
entry at all.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Close TODO.md

**Files:**
- Modify: `TODO.md`

- [ ] **Step 1: Replace TODO.md with a minimal "no open work" note**

Overwrite `TODO.md` with:

```markdown
# TODO

Last updated 2026-05-20. The remaining card-imports + lore-reclassification follow-up
(spec `docs/superpowers/specs/2026-05-20-import-dialog-reclassify-design.md`) shipped:
ImportDialog now offers per-row category dropdowns with server-side suggestions,
`apply_mapping` runs at import time, and the `_DEFAULT_VALUES` sentinel mismatch is fixed.

No open items.
```

- [ ] **Step 2: Run the full backend + frontend suites one more time to confirm a clean tree**

```bash
cd backend && uv run pytest -q
cd ../frontend && pnpm test -- --run
cd ../frontend && pnpm typecheck
cd .. && cd backend && uv run ruff check && uv run ruff format --check
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add TODO.md
git commit -m "$(cat <<'EOF'
docs(todo): close the import-dialog reclassification follow-up

The last open item from the card-imports / lore-reclassification line
shipped this branch. TODO.md is empty.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wrap-up

After Task 9 lands, the branch is ready to merge. Memory says to use **rebase-merge** (not `merge --no-ff`) when integrating feature branches to `main`. Do that with a fast-forward / rebase from the worktree, then `git worktree remove` to clean up.

```bash
# From the worktree:
git fetch origin
git rebase origin/main           # resolve any conflicts if main moved
cd ../../                        # back to main worktree at C:/Users/charl/github/grimoire
git checkout main
git pull --ff-only
git merge --ff-only 2026-05-20-import-dialog-reclassify
git worktree remove .worktrees/2026-05-20-import-dialog-reclassify
```

If the user wants a PR instead of direct integration, push the branch and open one — but they typically merge locally per the memory note.
