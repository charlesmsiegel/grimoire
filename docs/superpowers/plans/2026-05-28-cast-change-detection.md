# Cast-Change Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect when narration introduces a character entering or leaving a scene during play, surface each change for explicit user confirmation, and apply confirmed changes to the scene cast through the Scene Manager.

**Architecture:** Extend the existing structured Extractor with a `cast_changes` category (no new LLM round-trip) across all three structured strategies. The Orchestrator resolves each proposal against the read cascade — known characters become scene-owned *pending cast changes* (never auto-applied), unknown names fold into the existing `new_characters` candidate flow. A dedicated scenes API confirms/dismisses pending changes, applying confirmations via the Scene Manager's existing idempotent presence methods.

**Tech Stack:** Python 3.12 / FastAPI / Pydantic / aiosqlite (backend); TypeScript / React / Zod / Vitest (frontend). Tests: `cd backend && uv run pytest <path> -v`; `cd frontend && pnpm test`.

**Spec:** `docs/superpowers/specs/2026-05-28-cast-change-detection-design.md`

---

## File Structure

**Backend — create:**
- `backend/src/grimoire/storage/migrations/037_pending_cast_changes.sql` — new SQLite table
- `backend/src/grimoire/scenes/cast_changes.py` — `CastChangeStore` (CRUD), mirrors `scenes/ledger.py`
- `backend/tests/scenes/test_cast_change_store.py`
- `backend/tests/scenes/test_manager_cast_changes.py`
- `backend/tests/extractor/test_cast_changes.py`
- `backend/tests/characters/test_find_cast_ref.py`
- `backend/tests/orchestrator/test_cast_change_resolution.py`
- `backend/tests/api/test_cast_change_routes.py`

**Backend — modify:**
- `backend/src/grimoire/types/scene.py` — `CastChange` enum, `CastChangeProposal`, `PendingCastChange`
- `backend/src/grimoire/types/extraction.py` — `ExtractionResult.cast_changes`
- `backend/src/grimoire/extractor/schema.py` — `cast_changes` in `output_schema()` + `empty_payload()`
- `backend/src/grimoire/extractor/llm_strategy.py` — `LLMStrategyOutput.cast_changes`, parse in `parse_llm_payload`
- `backend/src/grimoire/extractor/together.py` — `ParsedTracker.cast_changes`, parse + `project_tracker_to_cast_changes`
- `backend/src/grimoire/extractor/tool_use.py` — `UPDATE_CAST_TOOL`, `_update_cast`, project into list
- `backend/src/grimoire/extractor/service.py` — thread `cast_changes` through `_run` and `_merge_with_sanity`
- `backend/src/grimoire/scenes/manager.py` — queue/list/confirm/dismiss methods + store handle
- `backend/src/grimoire/characters/service.py` — `find_cast_ref`
- `backend/src/grimoire/orchestrator/service.py` — `characters` dep, resolution, emit pending in `TURN_COMPLETE`
- `backend/src/grimoire/bootstrap.py` — wire `CastChangeStore` into `SceneManager`, `characters` into orchestrator
- `backend/src/grimoire/api/campaigns/scenes.py` — GET/confirm/dismiss endpoints
- `backend/src/grimoire/api/campaigns/schemas.py` — response schema for pending cast changes

**Frontend — create:**
- `frontend/src/routes/campaign/CastChangePrompt.tsx`
- `frontend/src/routes/campaign/CastChangePrompt.test.tsx`

**Frontend — modify:**
- `frontend/src/api/campaign/` — `confirmCastChange` / `dismissCastChange` / `listCastChanges` + types
- `frontend/src/routes/campaign/ScenePane.tsx` — mount `<CastChangePrompt>`

---

## Phase 1 — Types & Schema

### Task 1: Cast-change types

**Files:**
- Modify: `backend/src/grimoire/types/scene.py`
- Modify: `backend/src/grimoire/types/extraction.py`
- Test: `backend/tests/extractor/test_cast_changes.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/extractor/test_cast_changes.py
from grimoire.types.scene import CastChange, CastChangeProposal, PendingCastChange
from grimoire.types.extraction import ExtractionResult


def test_cast_change_proposal_defaults():
    p = CastChangeProposal(character_ref="reyes", change=CastChange.ENTER)
    assert p.change == "enter"
    assert p.confidence == 0.0
    assert p.evidence == ""


def test_extraction_result_carries_cast_changes():
    r = ExtractionResult(cast_changes=[CastChangeProposal(character_ref="x", change=CastChange.LEAVE)])
    assert r.cast_changes[0].change == "leave"


def test_pending_cast_change_roundtrip():
    rec = PendingCastChange(
        id="cc-1", campaign_id="c", scene_id="s", character_ref="library:worlds/w/characters/reyes",
        change=CastChange.ENTER, is_pc=False, evidence="strides in", confidence=0.8,
        turn_id="t1", status="pending", created_at="2026-05-28T00:00:00+00:00",
    )
    assert rec.model_dump()["change"] == "enter"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/extractor/test_cast_changes.py -v`
Expected: FAIL with `ImportError` (`CastChange` not defined).

- [ ] **Step 3: Add types**

In `backend/src/grimoire/types/scene.py` (add near the top-level enums; import `StrEnum` from `enum` and `BaseModel, Field` from `pydantic` if not already imported):

```python
class CastChange(StrEnum):
    ENTER = "enter"
    LEAVE = "leave"


class CastChangeProposal(BaseModel):
    """A character entering/leaving a scene, proposed by the Extractor.

    ``character_ref`` is the raw id or name the model emitted; the
    Orchestrator resolves it against the read cascade before persisting.
    """

    character_ref: str
    change: CastChange
    evidence: str = ""
    confidence: float = 0.0


class PendingCastChange(BaseModel):
    """A resolved cast change awaiting user confirmation (scene-owned)."""

    id: str
    campaign_id: str
    scene_id: str
    character_ref: str          # resolved composite ref
    change: CastChange
    is_pc: bool
    evidence: str
    confidence: float
    turn_id: str | None
    status: str                 # "pending" | "confirmed" | "dismissed"
    created_at: str
```

In `backend/src/grimoire/types/extraction.py`, add the import and field:

```python
from .scene import CastChangeProposal
```

```python
class ExtractionResult(BaseModel):
    deltas: list[StateDelta] = Field(default_factory=list)
    candidates: list[EntityCandidate] = Field(default_factory=list)
    extras_proposals: list[ExtrasProposal] = Field(default_factory=list)
    flags: list[ExtractionFlag] = Field(default_factory=list)
    transient_updates: list[TransientUpdateProposal] = Field(default_factory=list)
    cast_changes: list[CastChangeProposal] = Field(default_factory=list)
    expression_changes: list[ExpressionChange] = Field(default_factory=list)
    confidence_overall: float = 0.0
    extraction_strategies_run: list[str] = Field(default_factory=list)
    duration_ms: int = 0
```

If `types/scene.py` does not already exist, check whether scene types live in `backend/src/grimoire/scenes/types.py` instead — if so, add `CastChange`/`CastChangeProposal`/`PendingCastChange` there and import from `grimoire.scenes.types` in the test and in `types/extraction.py`. (Verify with: `ls backend/src/grimoire/types/scene.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/extractor/test_cast_changes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/types/ backend/tests/extractor/test_cast_changes.py
git commit -m "feat(types): add CastChange/CastChangeProposal/PendingCastChange (#464)"
```

---

### Task 2: Extractor output schema

**Files:**
- Modify: `backend/src/grimoire/extractor/schema.py`
- Test: `backend/tests/extractor/test_cast_changes.py`

- [ ] **Step 1: Add the failing test (append to the file from Task 1)**

```python
from grimoire.extractor.schema import empty_payload, output_schema


def test_schema_includes_cast_changes():
    props = output_schema()["properties"]
    assert "cast_changes" in props
    item = props["cast_changes"]["items"]
    assert item["properties"]["change"]["enum"] == ["enter", "leave"]
    assert set(item["required"]) == {"character_id", "change", "confidence"}


def test_empty_payload_includes_cast_changes():
    assert empty_payload()["cast_changes"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/extractor/test_cast_changes.py::test_schema_includes_cast_changes -v`
Expected: FAIL with `KeyError: 'cast_changes'`.

- [ ] **Step 3: Add `cast_changes` to the schema**

In `backend/src/grimoire/extractor/schema.py`, inside `output_schema()` add this object before the `return`:

```python
    cast_change = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "character_id": {"type": "string"},
            "change": {"type": "string", "enum": ["enter", "leave"]},
            "evidence": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["character_id", "change", "confidence"],
    }
```

Add to the returned `properties` dict (after `"scene_changes"`):

```python
            "cast_changes": {"type": "array", "items": cast_change},
```

In `empty_payload()` add (after `"scene_changes": []`):

```python
        "cast_changes": [],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/extractor/test_cast_changes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/extractor/schema.py backend/tests/extractor/test_cast_changes.py
git commit -m "feat(extractor): add cast_changes to output schema (#464)"
```

---

## Phase 2 — Extractor Parsing (three strategies)

### Task 3: structured_llm strategy parsing

**Files:**
- Modify: `backend/src/grimoire/extractor/llm_strategy.py`
- Modify: `backend/src/grimoire/extractor/service.py:341` (the `_run` return)
- Test: `backend/tests/extractor/test_cast_changes.py`

- [ ] **Step 1: Add the failing test**

```python
from grimoire.extractor.llm_strategy import parse_llm_payload


def test_parse_llm_payload_extracts_cast_changes():
    payload = {
        "cast_changes": [
            {"character_id": "reyes", "change": "enter", "evidence": "strides in", "confidence": 0.9},
            {"character_id": "bad", "change": "teleport", "confidence": 0.5},  # invalid -> dropped
        ]
    }
    out = parse_llm_payload(payload, campaign_id="c", source="structured_llm", max_new_entities=5)
    assert len(out.cast_changes) == 1
    assert out.cast_changes[0].character_ref == "reyes"
    assert out.cast_changes[0].change == "enter"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/extractor/test_cast_changes.py::test_parse_llm_payload_extracts_cast_changes -v`
Expected: FAIL with `AttributeError: 'LLMStrategyOutput' object has no attribute 'cast_changes'`.

- [ ] **Step 3: Implement**

In `backend/src/grimoire/extractor/llm_strategy.py`:

Add imports near the existing type imports:

```python
from grimoire.types.scene import CastChange, CastChangeProposal
```

Add the field to `LLMStrategyOutput` (the `@dataclass` near line 61):

```python
    cast_changes: list[CastChangeProposal] = field(default_factory=list)
```

Add a builder above `parse_llm_payload`:

```python
def _make_cast_change(item: dict) -> CastChangeProposal | None:
    try:
        change = CastChange(str(item.get("change", "")))
    except ValueError:
        return None
    ref = str(item.get("character_id", "")).strip()
    if not ref:
        return None
    return CastChangeProposal(
        character_ref=ref,
        change=change,
        evidence=str(item.get("evidence", "")),
        confidence=float(item.get("confidence", 0.0)),
    )
```

In `parse_llm_payload`, after the `transient_updates` loop (around line 442), add:

```python
    for raw in payload.get("cast_changes", []) or []:
        if not isinstance(raw, dict):
            continue
        proposal = _make_cast_change(raw)
        if proposal is not None:
            out.cast_changes.append(proposal)
            confidences.append(proposal.confidence)
```

In `backend/src/grimoire/extractor/service.py`, in the `_run` return (`ExtractionResult(...)` at line 341), add:

```python
            cast_changes=list(llm_out.cast_changes),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/extractor/test_cast_changes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/extractor/llm_strategy.py backend/src/grimoire/extractor/service.py backend/tests/extractor/test_cast_changes.py
git commit -m "feat(extractor): parse cast_changes in structured_llm strategy (#464)"
```

---

### Task 4: TOGETHER (tracker) strategy parsing

**Files:**
- Modify: `backend/src/grimoire/extractor/together.py`
- Modify: `backend/src/grimoire/extractor/service.py` (`_run_together`, `_merge_with_sanity`)
- Test: `backend/tests/extractor/test_cast_changes.py`

- [ ] **Step 1: Add the failing test**

```python
from grimoire.extractor.together import parse_tracker_text, project_tracker_to_cast_changes


def test_tracker_projects_cast_changes():
    tracker = '{"cast_changes": [{"character_id": "reyes", "change": "enter", "confidence": 0.8}]}'
    parsed = parse_tracker_text(tracker)
    changes = project_tracker_to_cast_changes(parsed)
    assert len(changes) == 1
    assert changes[0].character_ref == "reyes"
    assert changes[0].change == "enter"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/extractor/test_cast_changes.py::test_tracker_projects_cast_changes -v`
Expected: FAIL (`AttributeError`/`ImportError` — no `cast_changes` on `ParsedTracker`, no `project_tracker_to_cast_changes`).

- [ ] **Step 3: Implement**

In `backend/src/grimoire/extractor/together.py`:

Add to the `ParsedTracker` dataclass (near line 40):

```python
    cast_changes: list[dict[str, Any]] = field(default_factory=list)
```

In `parse_tracker_text`, where the `ParsedTracker(...)` is constructed (near line 105), add a keyword:

```python
        cast_changes=_as_list_of_dicts(obj.get("cast_changes")),
```

Add a projection function (mirror `project_tracker_to_candidates`):

```python
def project_tracker_to_cast_changes(parsed: ParsedTracker) -> list["CastChangeProposal"]:
    """Map tracker ``cast_changes`` entries to `CastChangeProposal`s."""
    from grimoire.types.scene import CastChange, CastChangeProposal

    out: list[CastChangeProposal] = []
    for entry in parsed.cast_changes:
        ref = str(entry.get("character_id") or entry.get("id") or "").strip()
        if not ref:
            continue
        try:
            change = CastChange(str(entry.get("change", "")))
        except ValueError:
            continue
        out.append(
            CastChangeProposal(
                character_ref=ref,
                change=change,
                evidence=str(entry.get("evidence") or ""),
                confidence=float(entry.get("confidence") or 0.0),
            )
        )
    return out
```

In `backend/src/grimoire/extractor/service.py`, add a `primary_cast_changes` parameter to `_merge_with_sanity` (default `None`) and include it in the returned `ExtractionResult`:

```python
    async def _merge_with_sanity(
        self,
        *,
        primary_deltas: list[StateDelta],
        primary_candidates: list[EntityCandidate],
        sanity: _SanityOutput,
        scene: Scene,
        campaign_id: CampaignId,
        snapshot: StateSnapshot,
        turn_id: TurnId | None,
        strategies_run: list[str],
        primary_cast_changes: list | None = None,
    ) -> ExtractionResult:
        ...
        return ExtractionResult(
            deltas=merged_deltas,
            candidates=candidates,
            extras_proposals=extras_proposals,
            flags=flags,
            cast_changes=list(primary_cast_changes or []),
            confidence_overall=confidence_overall,
            extraction_strategies_run=strategies_run,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
```

In `_run_together`, compute and pass the cast changes:

```python
        tracker_candidates = project_tracker_to_candidates(parsed)
        tracker_cast_changes = project_tracker_to_cast_changes(parsed)
        ...
        return await self._merge_with_sanity(
            primary_deltas=tracker_deltas,
            primary_candidates=tracker_candidates,
            sanity=sanity,
            scene=scene,
            campaign_id=campaign_id,
            snapshot=snapshot,
            turn_id=turn_id,
            strategies_run=["together", *sanity.strategies_run],
            primary_cast_changes=tracker_cast_changes,
        )
```

Add the import at the top of `service.py` alongside the other `together` imports:

```python
from grimoire.extractor.together import project_tracker_to_cast_changes
```

(If `together` symbols are imported lazily inside methods rather than at module top, follow that existing pattern instead.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/extractor/test_cast_changes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/extractor/together.py backend/src/grimoire/extractor/service.py backend/tests/extractor/test_cast_changes.py
git commit -m "feat(extractor): parse cast_changes in TOGETHER tracker (#464)"
```

---

### Task 5: TOOL_USE strategy parsing

**Files:**
- Modify: `backend/src/grimoire/extractor/tool_use.py`
- Modify: `backend/src/grimoire/extractor/service.py` (`_run_tool_use` passes cast changes)
- Test: `backend/tests/extractor/test_cast_changes.py`

- [ ] **Step 1: Add the failing test**

```python
from grimoire.extractor.tool_use import ALL_TOOLS, UPDATE_CAST_TOOL, project_cast_changes
from grimoire.extractor.protocols import ToolCall  # adjust import to where ToolCall is defined


def test_update_cast_tool_registered():
    assert UPDATE_CAST_TOOL in ALL_TOOLS
    assert UPDATE_CAST_TOOL.name == "update_cast"


def test_project_cast_changes_from_tool_calls():
    calls = [ToolCall(name="update_cast", args={"character_id": "reyes", "change": "leave", "confidence": 0.7})]
    out = project_cast_changes(calls)
    assert out[0].character_ref == "reyes"
    assert out[0].change == "leave"
```

Verify where `ToolCall` is defined (`grep -rn "class ToolCall" backend/src/grimoire/extractor/`) and fix the import in the test accordingly before running.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/extractor/test_cast_changes.py::test_update_cast_tool_registered -v`
Expected: FAIL with `ImportError` (`UPDATE_CAST_TOOL` not defined).

- [ ] **Step 3: Implement**

In `backend/src/grimoire/extractor/tool_use.py`, mirror `CHANGE_LOCATION_TOOL`:

```python
UPDATE_CAST_TOOL = ToolDeclaration(
    name="update_cast",
    description=(
        "Record that a known character entered or left the current scene. "
        "Only use for characters already established in the world or campaign."
    ),
    parameters={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "character_id": {"type": "string"},
            "change": {"type": "string", "enum": ["enter", "leave"]},
            "evidence": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["character_id", "change", "confidence"],
    },
)
```

Add `UPDATE_CAST_TOOL` to the `ALL_TOOLS` list.

Add a handler + projector:

```python
def _update_cast(call: ToolCall) -> "CastChangeProposal | None":
    from grimoire.types.scene import CastChange, CastChangeProposal

    ref = str(call.args.get("character_id") or "").strip()
    if not ref:
        return None
    try:
        change = CastChange(str(call.args.get("change", "")))
    except ValueError:
        return None
    return CastChangeProposal(
        character_ref=ref,
        change=change,
        evidence=str(call.args.get("evidence") or ""),
        confidence=_confidence(call.args),
    )


def project_cast_changes(tool_calls: list[ToolCall]) -> list["CastChangeProposal"]:
    out = []
    for call in tool_calls:
        if call.name == UPDATE_CAST_TOOL.name:
            proposal = _update_cast(call)
            if proposal is not None:
                out.append(proposal)
    return out
```

In `backend/src/grimoire/extractor/service.py` `_run_tool_use`, compute `tool_cast_changes = project_cast_changes(tool_calls)` and pass `primary_cast_changes=tool_cast_changes` into the `_merge_with_sanity` call (same shape as Task 4). Add the import alongside the other tool_use imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/extractor/test_cast_changes.py -v`
Expected: PASS. Also run the full extractor suite to confirm no regressions: `cd backend && uv run pytest tests/extractor/ -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/extractor/tool_use.py backend/src/grimoire/extractor/service.py backend/tests/extractor/test_cast_changes.py
git commit -m "feat(extractor): add update_cast tool to TOOL_USE strategy (#464)"
```

---

## Phase 3 — Persistence

### Task 6: Migration for `pending_cast_changes`

**Files:**
- Create: `backend/src/grimoire/storage/migrations/037_pending_cast_changes.sql`
- Test: `backend/tests/scenes/test_cast_change_store.py`

- [ ] **Step 1: Write the migration**

```sql
-- backend/src/grimoire/storage/migrations/037_pending_cast_changes.sql
CREATE TABLE pending_cast_changes (
    id            TEXT PRIMARY KEY,
    campaign_id   TEXT NOT NULL,
    scene_id      TEXT NOT NULL,
    character_ref TEXT NOT NULL,
    change        TEXT NOT NULL CHECK (change IN ('enter', 'leave')),
    is_pc         INTEGER NOT NULL DEFAULT 0,
    evidence      TEXT NOT NULL DEFAULT '',
    confidence    REAL NOT NULL DEFAULT 0.0,
    turn_id       TEXT,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'confirmed', 'dismissed')),
    created_at    TEXT NOT NULL
);

CREATE INDEX idx_pending_cast_changes_scene_status
    ON pending_cast_changes (scene_id, status);
```

- [ ] **Step 2: Write a test that the migration applies cleanly**

```python
# backend/tests/scenes/test_cast_change_store.py
import pytest
from grimoire.storage.db import Database
from grimoire.storage import apply_migrations


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.sqlite")
    await database.connect()
    await apply_migrations(database)
    yield database
    await database.close()


async def test_pending_cast_changes_table_exists(db):
    rows = await db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pending_cast_changes'"
    )
    assert len(rows) == 1
```

Check `backend/tests/` for the existing migration-test fixture pattern (e.g. how `036` regression test builds a `Database`) and match its `connect`/`apply_migrations` signature exactly.

- [ ] **Step 3: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/scenes/test_cast_change_store.py::test_pending_cast_changes_table_exists -v`
Expected: PASS (migration auto-discovered by `discover_migrations`).

- [ ] **Step 4: Commit**

```bash
git add backend/src/grimoire/storage/migrations/037_pending_cast_changes.sql backend/tests/scenes/test_cast_change_store.py
git commit -m "feat(storage): add pending_cast_changes table (#464)"
```

---

### Task 7: `CastChangeStore`

**Files:**
- Create: `backend/src/grimoire/scenes/cast_changes.py`
- Test: `backend/tests/scenes/test_cast_change_store.py`

- [ ] **Step 1: Write the failing test (append)**

```python
from grimoire.scenes.cast_changes import CastChangeStore
from grimoire.types.scene import CastChange


async def test_store_add_list_get_set_status(db):
    store = CastChangeStore(db)
    cid = await store.add(
        campaign_id="c", scene_id="s", character_ref="library:worlds/w/characters/reyes",
        change=CastChange.ENTER, is_pc=False, evidence="strides in", confidence=0.8, turn_id="t1",
    )
    pending = await store.list_pending("s")
    assert len(pending) == 1
    assert pending[0].character_ref.endswith("reyes")
    assert pending[0].is_pc is False

    rec = await store.get(cid)
    assert rec is not None and rec.change == "enter"

    await store.set_status(cid, "confirmed")
    assert await store.list_pending("s") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/scenes/test_cast_change_store.py -v`
Expected: FAIL with `ImportError` (`CastChangeStore` not defined).

- [ ] **Step 3: Implement (mirror `scenes/ledger.py`)**

```python
# backend/src/grimoire/scenes/cast_changes.py
"""Scene-owned store of pending cast changes awaiting confirmation (#464)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from grimoire.storage.db import Database
from grimoire.types.scene import CastChange, PendingCastChange


def _row_to_model(row) -> PendingCastChange:
    return PendingCastChange(
        id=row["id"],
        campaign_id=row["campaign_id"],
        scene_id=row["scene_id"],
        character_ref=row["character_ref"],
        change=CastChange(row["change"]),
        is_pc=bool(row["is_pc"]),
        evidence=row["evidence"] or "",
        confidence=float(row["confidence"]),
        turn_id=row["turn_id"],
        status=row["status"],
        created_at=row["created_at"],
    )


class CastChangeStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(
        self,
        *,
        campaign_id: str,
        scene_id: str,
        character_ref: str,
        change: CastChange,
        is_pc: bool,
        evidence: str = "",
        confidence: float = 0.0,
        turn_id: str | None = None,
    ) -> str:
        item_id = f"cc-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            """
            INSERT INTO pending_cast_changes
                (id, campaign_id, scene_id, character_ref, change, is_pc,
                 evidence, confidence, turn_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                item_id, campaign_id, scene_id, character_ref, str(change),
                1 if is_pc else 0, evidence, confidence, turn_id,
                datetime.now(UTC).isoformat(),
            ),
        )
        return item_id

    async def list_pending(self, scene_id: str) -> list[PendingCastChange]:
        rows = await self._db.fetchall(
            """
            SELECT * FROM pending_cast_changes
            WHERE scene_id = ? AND status = 'pending'
            ORDER BY created_at
            """,
            (scene_id,),
        )
        return [_row_to_model(r) for r in rows]

    async def get(self, item_id: str) -> PendingCastChange | None:
        row = await self._db.fetchone(
            "SELECT * FROM pending_cast_changes WHERE id = ?", (item_id,)
        )
        return _row_to_model(row) if row else None

    async def set_status(self, item_id: str, status: str) -> None:
        await self._db.execute(
            "UPDATE pending_cast_changes SET status = ? WHERE id = ?",
            (status, item_id),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/scenes/test_cast_change_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/scenes/cast_changes.py backend/tests/scenes/test_cast_change_store.py
git commit -m "feat(scenes): add CastChangeStore (#464)"
```

---

## Phase 4 — Scene Manager

### Task 8: Scene Manager queue/list/confirm/dismiss

**Files:**
- Modify: `backend/src/grimoire/scenes/manager.py`
- Modify: `backend/src/grimoire/bootstrap.py`
- Test: `backend/tests/scenes/test_manager_cast_changes.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/scenes/test_manager_cast_changes.py
import pytest
from grimoire.scenes.manager import SceneManager
from grimoire.scenes.cast_changes import CastChangeStore
from grimoire.scenes.types import SceneInit
from grimoire.storage.db import Database
from grimoire.storage import apply_migrations
from grimoire.types.scene import CastChange


@pytest.fixture
async def manager(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    await db.connect()
    await apply_migrations(db)
    mgr = SceneManager(tmp_path, cast_change_store=CastChangeStore(db))
    yield mgr
    await db.close()


async def test_queue_and_confirm_npc_enter(manager):
    scene = await manager.start_scene(SceneInit(
        campaign_id="c", title="t", present_character_refs=[], present_pc_refs=[],
    ))
    cid = await manager.queue_cast_change(
        scene.id, character_ref="library:worlds/w/characters/reyes",
        change=CastChange.ENTER, is_pc=False, evidence="strides in", confidence=0.8, turn_id="t1",
    )
    assert len(await manager.list_pending_cast_changes(scene.id)) == 1

    await manager.confirm_cast_change(scene.id, cid)
    updated = await manager.get_scene(scene.id)
    assert "library:worlds/w/characters/reyes" in updated.present_character_refs
    assert await manager.list_pending_cast_changes(scene.id) == []


async def test_dismiss_does_not_touch_cast(manager):
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="t"))
    cid = await manager.queue_cast_change(
        scene.id, character_ref="x", change=CastChange.ENTER, is_pc=False,
    )
    await manager.dismiss_cast_change(scene.id, cid)
    updated = await manager.get_scene(scene.id)
    assert "x" not in updated.present_character_refs
    assert await manager.list_pending_cast_changes(scene.id) == []


async def test_confirm_pc_enter_uses_add_present_pc(manager):
    scene = await manager.start_scene(SceneInit(campaign_id="c", title="t"))
    cid = await manager.queue_cast_change(
        scene.id, character_ref="campaign:emergent/character/hero", change=CastChange.ENTER, is_pc=True,
    )
    await manager.confirm_cast_change(scene.id, cid)
    updated = await manager.get_scene(scene.id)
    assert "campaign:emergent/character/hero" in updated.present_pc_refs
```

Check `SceneInit`'s required fields (`backend/src/grimoire/scenes/types.py:124`) and adjust the constructor calls if `title`/`campaign_id` differ.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/scenes/test_manager_cast_changes.py -v`
Expected: FAIL (`TypeError: unexpected keyword 'cast_change_store'` then `AttributeError: queue_cast_change`).

- [ ] **Step 3: Implement**

In `backend/src/grimoire/scenes/manager.py`:

Add the constructor parameter (in `__init__`, after `state_store`):

```python
        cast_change_store: Any = None,
```
```python
        self._cast_change_store = cast_change_store
```

Add a setter near `set_continuity`:

```python
    def set_cast_change_store(self, store: Any) -> None:
        self._cast_change_store = store
```

Add the methods in the `# -- Presence ---` section (after `set_pov`):

```python
    # -- Cast-change review (#464) --------------------------------------

    async def queue_cast_change(
        self,
        scene_id: str,
        *,
        character_ref: str,
        change: "CastChange",
        is_pc: bool,
        evidence: str = "",
        confidence: float = 0.0,
        turn_id: str | None = None,
    ) -> str:
        if self._cast_change_store is None:
            raise RuntimeError("cast_change_store not wired")
        scene = await self.get_scene(scene_id)
        return await self._cast_change_store.add(
            campaign_id=scene.campaign_id,
            scene_id=scene_id,
            character_ref=character_ref,
            change=change,
            is_pc=is_pc,
            evidence=evidence,
            confidence=confidence,
            turn_id=turn_id,
        )

    async def list_pending_cast_changes(self, scene_id: str) -> list:
        if self._cast_change_store is None:
            return []
        return await self._cast_change_store.list_pending(scene_id)

    async def confirm_cast_change(self, scene_id: str, change_id: str) -> None:
        from grimoire.types.scene import CastChange

        if self._cast_change_store is None:
            raise RuntimeError("cast_change_store not wired")
        rec = await self._cast_change_store.get(change_id)
        if rec is None or rec.scene_id != scene_id:
            raise NotFoundError(f"cast change {change_id!r} not found in scene {scene_id!r}")
        if rec.status != "pending":
            raise ValueError(f"cast change {change_id!r} already {rec.status}")
        if rec.change == CastChange.ENTER:
            if rec.is_pc:
                await self.add_present_pc(scene_id, rec.character_ref)
            else:
                await self.add_present_character(scene_id, rec.character_ref)
        else:
            await self.remove_present_character(scene_id, rec.character_ref)
        await self._cast_change_store.set_status(change_id, "confirmed")

    async def dismiss_cast_change(self, scene_id: str, change_id: str) -> None:
        if self._cast_change_store is None:
            raise RuntimeError("cast_change_store not wired")
        rec = await self._cast_change_store.get(change_id)
        if rec is None or rec.scene_id != scene_id:
            raise NotFoundError(f"cast change {change_id!r} not found in scene {scene_id!r}")
        await self._cast_change_store.set_status(change_id, "dismissed")
```

Confirm `NotFoundError` is importable in `manager.py` (check existing imports; if the scenes module uses a different lookup error, use that). Add `from grimoire.types.scene import CastChange` to the module imports if you reference it in type hints at module load (the methods import it lazily above to avoid ordering issues — keep that).

In `backend/src/grimoire/bootstrap.py`, wire the store. After the `SceneIndexer` block (where `container.state_store.db` is available, ~line 232), construct and attach the store:

```python
    if container.scenes is not None and container.state_store is not None:
        from grimoire.scenes.cast_changes import CastChangeStore

        container.scenes.set_cast_change_store(CastChangeStore(container.state_store.db))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/scenes/test_manager_cast_changes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/scenes/manager.py backend/src/grimoire/bootstrap.py backend/tests/scenes/test_manager_cast_changes.py
git commit -m "feat(scenes): queue/confirm/dismiss cast changes via Scene Manager (#464)"
```

---

## Phase 5 — Resolution

### Task 9: `CharactersService.find_cast_ref`

**Files:**
- Modify: `backend/src/grimoire/characters/service.py`
- Test: `backend/tests/characters/test_find_cast_ref.py`

This method maps a raw id/name (as emitted by the extractor) to a canonical character ref + PC flag, or `None` if it doesn't resolve.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/characters/test_find_cast_ref.py
# Build the CharactersService the way existing characters tests do
# (see backend/tests/characters/ for the standard fixture that wires a
# library + store with one library character and one emergent character).
import pytest
from grimoire.characters.service import CastRef


async def test_find_cast_ref_matches_by_id(characters_service, campaign_id):
    ref = await characters_service.find_cast_ref(campaign_id, "reyes")
    assert isinstance(ref, CastRef)
    assert ref.character_ref.endswith("reyes")
    assert ref.is_pc in (True, False)


async def test_find_cast_ref_unknown_returns_none(characters_service, campaign_id):
    assert await characters_service.find_cast_ref(campaign_id, "nobody-xyz") is None
```

Reuse the existing characters test fixture (search `backend/tests/characters/` for one that composes a campaign with at least one character). If none is reusable, build the service per `backend/tests/characters/conftest.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/characters/test_find_cast_ref.py -v`
Expected: FAIL with `ImportError` (`CastRef` not defined).

- [ ] **Step 3: Implement**

In `backend/src/grimoire/characters/service.py`:

Add a small result type near the top (after imports):

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class CastRef:
    character_ref: str
    is_pc: bool
    name: str
```

Add the method (uses the existing `list_for_campaign` + `slugify_id` already imported in this module; `CharacterRole` is imported here too):

```python
    async def find_cast_ref(self, campaign_id: CampaignId, query: str) -> CastRef | None:
        """Resolve an extractor-emitted id/name to a canonical character ref.

        Matches against character id, slugified name, and exact name across
        every character composed into the campaign (library + emergent).
        Returns None when nothing resolves — the caller routes unknown names
        to the new-character candidate flow.
        """
        needle = slugify_id(query, fallback=query.lower()).strip()
        if not needle:
            return None
        for resolved in await self.list_for_campaign(campaign_id):
            ch = resolved.character
            candidates = {
                slugify_id(ch.id, fallback=ch.id.lower()),
                slugify_id(ch.name, fallback=ch.name.lower()),
            }
            if needle not in candidates:
                continue
            # Reconstruct the canonical ref from the resolution source chain.
            ref = self._ref_for_resolved(resolved)
            return CastRef(
                character_ref=ref,
                is_pc=(ch.role == CharacterRole.PC),
                name=ch.name,
            )
        return None

    def _ref_for_resolved(self, resolved) -> str:
        """Build the composite ref a ResolvedCharacter resolves from."""
        ch = resolved.character
        for src in resolved.source_chain:
            layer = src.get("layer") if isinstance(src, dict) else getattr(src, "layer", None)
            world_id = src.get("world_id") if isinstance(src, dict) else getattr(src, "world_id", None)
            if str(layer) == "emergent" or world_id is None:
                return f"campaign:emergent/character/{ch.id}"
            return f"library:worlds/{world_id}/characters/{ch.id}"
        return f"campaign:emergent/character/{ch.id}"
```

Verify the `source_chain` element shape (`ResolutionSource.model_dump()` keys: `layer`, `world_id`) against `backend/src/grimoire/types/characters.py` and adjust `_ref_for_resolved` if field names differ. The `resolve()` method (service.py:222) shows `library` refs use `worlds/{world_id}/characters/{asset_id}` and emergent uses `campaign:emergent/character/{asset_id}` — match those exactly.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/characters/test_find_cast_ref.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/characters/service.py backend/tests/characters/test_find_cast_ref.py
git commit -m "feat(characters): add find_cast_ref for cast-change resolution (#464)"
```

---

### Task 10: Orchestrator resolution + emit

**Files:**
- Modify: `backend/src/grimoire/orchestrator/service.py`
- Modify: `backend/src/grimoire/bootstrap.py` (pass `characters` to orchestrator)
- Test: `backend/tests/orchestrator/test_cast_change_resolution.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/orchestrator/test_cast_change_resolution.py
import pytest
from grimoire.types.scene import CastChange, CastChangeProposal
from grimoire.types.extraction import ExtractionResult


class _FakeCharacters:
    def __init__(self, mapping):
        self._mapping = mapping  # query -> CastRef | None

    async def find_cast_ref(self, campaign_id, query):
        return self._mapping.get(query)


class _RecordingScenes:
    def __init__(self):
        self.queued = []

    async def queue_cast_change(self, scene_id, *, character_ref, change, is_pc, evidence="", confidence=0.0, turn_id=None):
        self.queued.append((character_ref, change, is_pc))
        return f"cc-{len(self.queued)}"


async def test_known_character_queued_unknown_becomes_candidate():
    from grimoire.characters.service import CastRef
    from grimoire.orchestrator.service import resolve_cast_changes  # module-level helper

    chars = _FakeCharacters({"reyes": CastRef("library:worlds/w/characters/reyes", False, "Reyes")})
    scenes = _RecordingScenes()

    class _Scene:
        id = "s1"
        present_character_refs: list = []
        present_pc_refs: list = []

    extraction = ExtractionResult(cast_changes=[
        CastChangeProposal(character_ref="reyes", change=CastChange.ENTER, confidence=0.9),
        CastChangeProposal(character_ref="stranger", change=CastChange.ENTER, confidence=0.6),
    ])

    queued = await resolve_cast_changes(
        extraction=extraction, scene=_Scene(), campaign_id="c", turn_id="t1",
        characters=chars, scenes=scenes,
    )
    assert scenes.queued == [("library:worlds/w/characters/reyes", CastChange.ENTER, False)]
    assert len(queued) == 1
    # unknown name routed to candidate flow
    assert any(c.proposed_name == "stranger" for c in extraction.candidates)


async def test_noop_enter_already_present_is_dropped():
    from grimoire.characters.service import CastRef
    from grimoire.orchestrator.service import resolve_cast_changes

    chars = _FakeCharacters({"reyes": CastRef("ref:reyes", False, "Reyes")})
    scenes = _RecordingScenes()

    class _Scene:
        id = "s1"
        present_character_refs = ["ref:reyes"]
        present_pc_refs: list = []

    extraction = ExtractionResult(cast_changes=[
        CastChangeProposal(character_ref="reyes", change=CastChange.ENTER, confidence=0.9),
    ])
    queued = await resolve_cast_changes(
        extraction=extraction, scene=_Scene(), campaign_id="c", turn_id="t1",
        characters=chars, scenes=scenes,
    )
    assert queued == []
    assert scenes.queued == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/orchestrator/test_cast_change_resolution.py -v`
Expected: FAIL with `ImportError` (`resolve_cast_changes` not defined).

- [ ] **Step 3: Implement the module-level helper**

In `backend/src/grimoire/orchestrator/service.py`, add a module-level function (kept standalone so it is unit-testable without a full orchestrator):

```python
async def resolve_cast_changes(
    *,
    extraction: "ExtractionResult",
    scene: Any,
    campaign_id: CampaignId,
    turn_id: str | None,
    characters: Any,
    scenes: Any,
) -> list[str]:
    """Resolve extractor cast-change proposals and queue the known ones.

    Known characters → SceneManager.queue_cast_change (pending review).
    Unknown names → appended to ``extraction.candidates`` (new-character flow).
    No-ops (enter already-present / leave not-present) are dropped.
    Returns the list of queued pending-cast-change ids.
    """
    from grimoire.types.common import EntityKind
    from grimoire.types.extraction import EntityCandidate
    from grimoire.types.scene import CastChange
    from grimoire.utils.ids import slugify_id  # adjust to where slugify_id lives

    queued: list[str] = []
    if characters is None or scenes is None:
        return queued
    present = set(getattr(scene, "present_character_refs", []) or [])
    for proposal in extraction.cast_changes:
        cast_ref = await characters.find_cast_ref(campaign_id, proposal.character_ref)
        if cast_ref is None:
            name = proposal.character_ref.strip()
            if name and not any(c.proposed_name == name for c in extraction.candidates):
                extraction.candidates.append(
                    EntityCandidate(
                        kind=EntityKind.CHARACTER,
                        proposed_id=slugify_id(name, fallback="unknown"),
                        proposed_name=name,
                        evidence=proposal.evidence,
                        confidence=proposal.confidence,
                        suggested_card={"name": name, "scope": "campaign-local"},
                    )
                )
            continue
        ref = cast_ref.character_ref
        if proposal.change == CastChange.ENTER and ref in present:
            continue
        if proposal.change == CastChange.LEAVE and ref not in present:
            continue
        change_id = await scenes.queue_cast_change(
            scene.id,
            character_ref=ref,
            change=proposal.change,
            is_pc=cast_ref.is_pc,
            evidence=proposal.evidence,
            confidence=proposal.confidence,
            turn_id=turn_id,
        )
        queued.append(change_id)
    return queued
```

Resolve the correct `slugify_id` import (search `grep -rn "def slugify_id" backend/src/grimoire/`) and fix the import line.

Wire `characters` into the orchestrator: add `characters: Any | None = None` to `OrchestratorService.__init__` params and `self._characters = characters`.

Call the helper in the turn body. In `_continue_turn_after_pre_roll` after `apply_routing` (service.py ~1056), add:

```python
        pending_cast_change_ids: list[str] = []
        if extraction is not None and self._characters is not None:
            pending_cast_change_ids = await resolve_cast_changes(
                extraction=extraction,
                scene=scene_obj,
                campaign_id=campaign_id,
                turn_id=turn_id,
                characters=self._characters,
                scenes=self._scenes,
            )
            if pending_cast_change_ids:
                pending = await self._scenes.list_pending_cast_changes(scene_id)
                await self._emit_fragment(
                    turn_id,
                    campaign_id,
                    pending_cast_changes=[p.model_dump(mode="json") for p in pending],
                )
```

Add the same `pending_cast_changes` payload to the `TURN_COMPLETE` emit (service.py:1102) so a fresh client gets it:

```python
            pending_cast_changes=(
                [p.model_dump(mode="json") for p in await self._scenes.list_pending_cast_changes(scene_id)]
            ),
```

Also call `resolve_cast_changes` in `route_analysis_deltas` (service.py:603) for the re-analysis path, guarding on `self._characters`.

In `backend/src/grimoire/bootstrap.py`, pass `characters=container.characters` to the `OrchestratorService(...)` constructor (search for where it is built, ~line 607 region uses `db=container.db`; find the `OrchestratorService(` call and add the kwarg).

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/orchestrator/test_cast_change_resolution.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/orchestrator/service.py backend/src/grimoire/bootstrap.py backend/tests/orchestrator/test_cast_change_resolution.py
git commit -m "feat(orchestrator): resolve cast changes, queue known, candidate unknown (#464)"
```

---

## Phase 6 — API

### Task 11: Cast-change endpoints

**Files:**
- Modify: `backend/src/grimoire/api/campaigns/scenes.py`
- Modify: `backend/src/grimoire/api/campaigns/schemas.py`
- Test: `backend/tests/api/test_cast_change_routes.py`

- [ ] **Step 1: Write the failing test (scenario level)**

```python
# backend/tests/api/test_cast_change_routes.py
import pytest

pytestmark = pytest.mark.scenario

# Use the existing scenario fixture/client that builds the FastAPI app
# (search backend/tests/api/ for `async_client`/`app_client` fixtures).

async def test_list_confirm_dismiss_cast_changes(app_client, seeded_campaign_with_scene):
    campaign_id, scene_id = seeded_campaign_with_scene
    # Seed a pending cast change directly through the Scene Manager-backed store
    # exposed by the app container, then exercise the endpoints.
    r = await app_client.get(f"/campaigns/{campaign_id}/scenes/{scene_id}/cast-changes")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
```

Match the actual scenario harness in `backend/tests/api/` (router prefix, fixture names). Build a fuller test that POSTs `/confirm` and asserts the scene cast updated, mirroring an existing scenes-route test.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_cast_change_routes.py -v`
Expected: FAIL (404 — routes not registered).

- [ ] **Step 3: Implement endpoints**

In `backend/src/grimoire/api/campaigns/scenes.py`, add (using `ScenesDep` and the `_require_scene_owned` helper already imported):

```python
@router.get("/{campaign_id}/scenes/{scene_id}/cast-changes")
async def list_cast_changes(campaign_id: str, scene_id: str, scenes: ScenesDep) -> Any:
    await _require_scene_owned(scenes, campaign_id, scene_id)
    pending = await scenes.list_pending_cast_changes(scene_id)
    return [p.model_dump(mode="json") for p in pending]


@router.post("/{campaign_id}/scenes/{scene_id}/cast-changes/{change_id}/confirm")
async def confirm_cast_change(campaign_id: str, scene_id: str, change_id: str, scenes: ScenesDep) -> Any:
    await _require_scene_owned(scenes, campaign_id, scene_id)
    try:
        await scenes.confirm_cast_change(scene_id, change_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}


@router.post("/{campaign_id}/scenes/{scene_id}/cast-changes/{change_id}/dismiss")
async def dismiss_cast_change(campaign_id: str, scene_id: str, change_id: str, scenes: ScenesDep) -> Any:
    await _require_scene_owned(scenes, campaign_id, scene_id)
    try:
        await scenes.dismiss_cast_change(scene_id, change_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return {"ok": True}
```

`map_lookup_errors` maps `NotFoundError` → 404; if `confirm` raises `ValueError` for an already-resolved item, add a branch returning `HTTPException(status_code=409, ...)` (check how other routes in this file map conflict states and follow that).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/api/test_cast_change_routes.py -v`
Expected: PASS. Then run `cd backend && uv run pytest -m scenario -q` to confirm no route regressions.

- [ ] **Step 5: Lint + commit**

```bash
cd backend && uv run ruff check && uv run ruff format
git add backend/src/grimoire/api/campaigns/scenes.py backend/src/grimoire/api/campaigns/schemas.py backend/tests/api/test_cast_change_routes.py
git commit -m "feat(api): cast-change list/confirm/dismiss endpoints (#464)"
```

---

## Phase 7 — Frontend

### Task 12: API client + types

**Files:**
- Modify: `frontend/src/api/campaign/` (add functions + types; follow `resolveSceneBreak` in the same dir)
- Test: covered via component test in Task 13

- [ ] **Step 1: Add types and client functions**

Find where `campaignApi.resolveSceneBreak` is defined (`grep -rn "resolveSceneBreak" frontend/src/api/`). In the same module, add:

```typescript
export interface PendingCastChange {
  id: string;
  campaign_id: string;
  scene_id: string;
  character_ref: string;
  change: "enter" | "leave";
  is_pc: boolean;
  evidence: string;
  confidence: number;
  turn_id: string | null;
  status: string;
  created_at: string;
}
```

Add to the `campaignApi` object (mirror `resolveSceneBreak`'s fetch + ApiError handling):

```typescript
  listCastChanges(campaignId: string, sceneId: string): Promise<PendingCastChange[]> {
    return apiGet(`/campaigns/${campaignId}/scenes/${sceneId}/cast-changes`);
  },
  confirmCastChange(campaignId: string, sceneId: string, changeId: string): Promise<{ ok: boolean }> {
    return apiPost(`/campaigns/${campaignId}/scenes/${sceneId}/cast-changes/${changeId}/confirm`, {});
  },
  dismissCastChange(campaignId: string, sceneId: string, changeId: string): Promise<{ ok: boolean }> {
    return apiPost(`/campaigns/${campaignId}/scenes/${sceneId}/cast-changes/${changeId}/dismiss`, {});
  },
```

Use the actual `apiGet`/`apiPost` helpers this module already uses (match the existing calls). If responses are Zod-validated elsewhere, add a `pendingCastChangeSchema` in `frontend/src/api/schemas/` and parse it.

- [ ] **Step 2: Typecheck**

Run: `cd frontend && pnpm typecheck`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/
git commit -m "feat(frontend): cast-change api client (#464)"
```

---

### Task 13: `CastChangePrompt` component

**Files:**
- Create: `frontend/src/routes/campaign/CastChangePrompt.tsx`
- Create: `frontend/src/routes/campaign/CastChangePrompt.test.tsx`
- Modify: `frontend/src/routes/campaign/ScenePane.tsx` (mount it)

- [ ] **Step 1: Write the failing component test (mirror `SceneBreakPrompt.test.tsx`)**

```tsx
// frontend/src/routes/campaign/CastChangePrompt.test.tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { CastChangePrompt } from "./CastChangePrompt";
import { campaignApi } from "../../api/campaign";

// Mirror how SceneBreakPrompt.test.tsx mocks useCampaignEvent to push an event.
vi.mock("../../api/campaign", async (orig) => {
  const actual = await orig<typeof import("../../api/campaign")>();
  return { ...actual, campaignApi: { ...actual.campaignApi, confirmCastChange: vi.fn().mockResolvedValue({ ok: true }), dismissCastChange: vi.fn().mockResolvedValue({ ok: true }) } };
});

describe("CastChangePrompt", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders a pending change from turn_complete and confirms it", async () => {
    // Use the test's event-injection helper (copy the pattern from
    // SceneBreakPrompt.test.tsx) to emit a turn_complete carrying
    // pending_cast_changes: [{ id: "cc-1", character_ref: "...reyes", change: "enter", is_pc: false, ... }]
    render(<CastChangePrompt campaignId="c" sceneId="s" />);
    // fire the mocked event here per the precedent's helper
    await screen.findByText(/enters the scene/i);
    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));
    await waitFor(() => expect(campaignApi.confirmCastChange).toHaveBeenCalledWith("c", "s", "cc-1"));
  });
});
```

Open `frontend/src/routes/campaign/SceneBreakPrompt.test.tsx` first and copy its exact `useCampaignEvent` mocking / event-injection mechanism — reuse it verbatim so the event delivery matches production.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm test CastChangePrompt`
Expected: FAIL (component file does not exist).

- [ ] **Step 3: Implement the component (mirror `SceneBreakPrompt.tsx`)**

```tsx
// frontend/src/routes/campaign/CastChangePrompt.tsx
import { useCallback, useState } from "react";

import { campaignApi, type PendingCastChange } from "../../api/campaign";
import { ApiError } from "../../api/client";
import { useCampaignEvent } from "../../state/useCampaignEvent";

interface Props {
  campaignId: string;
  sceneId: string;
}

function label(c: PendingCastChange): string {
  const name = c.character_ref.split("/").pop() ?? c.character_ref;
  return c.change === "enter" ? `${name} enters the scene` : `${name} leaves the scene`;
}

export function CastChangePrompt({ campaignId, sceneId }: Props) {
  const [pending, setPending] = useState<PendingCastChange[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const handleEvent = useCallback((m: { type: string } & Record<string, unknown>) => {
    if (m.type !== "turn_complete") return;
    const changes = m.pending_cast_changes;
    if (Array.isArray(changes)) setPending(changes as PendingCastChange[]);
  }, []);

  useCampaignEvent("turn_complete", handleEvent);

  const remove = (id: string) => setPending((p) => p.filter((c) => c.id !== id));

  async function act(id: string, kind: "confirm" | "dismiss") {
    setBusy(id);
    try {
      if (kind === "confirm") await campaignApi.confirmCastChange(campaignId, sceneId, id);
      else await campaignApi.dismissCastChange(campaignId, sceneId, id);
      remove(id);
    } catch (err) {
      if (err instanceof ApiError && (err.status === 404 || err.status === 409)) remove(id);
    } finally {
      setBusy(null);
    }
  }

  if (pending.length === 0) return null;

  return (
    <div className="cast-change-prompt" role="region" aria-label="Pending cast changes">
      {pending.map((c) => (
        <div key={c.id} className="cast-change-row">
          <span>{label(c)}</span>
          <button type="button" disabled={busy === c.id} onClick={() => void act(c.id, "confirm")}>
            Confirm
          </button>
          <button type="button" disabled={busy === c.id} onClick={() => void act(c.id, "dismiss")}>
            Dismiss
          </button>
        </div>
      ))}
    </div>
  );
}
```

Verify the `useCampaignEvent` import path matches `SceneBreakPrompt.tsx` (`../../state/useCampaignEvent`).

In `frontend/src/routes/campaign/ScenePane.tsx`, mount it next to where `SceneBreakPrompt` is rendered (search for `<SceneBreakPrompt`), passing `campaignId` and the active `sceneId`.

- [ ] **Step 4: Run test + typecheck + lint**

Run: `cd frontend && pnpm test CastChangePrompt && pnpm typecheck && pnpm lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/campaign/CastChangePrompt.tsx frontend/src/routes/campaign/CastChangePrompt.test.tsx frontend/src/routes/campaign/ScenePane.tsx
git commit -m "feat(frontend): cast-change confirm/dismiss prompt (#464)"
```

---

## Phase 8 — Integration test & docs

### Task 14: End-to-end integration test + docs

**Files:**
- Create: `backend/tests/integration/test_cast_change_turn.py`
- Modify: `docs/superpowers/specs/2026-05-28-cast-change-detection-design.md` (mark Implemented)

- [ ] **Step 1: Write the integration test**

```python
# backend/tests/integration/test_cast_change_turn.py
import pytest

pytestmark = pytest.mark.integration

async def test_turn_with_cast_change_queues_then_confirm_updates_scene(orchestrator_harness):
    """A turn whose tracker block contains a cast_change for a known character
    creates a pending record; confirming it adds the character to the scene
    sidecar. For a PC, ADVANCE_DISABLED/ENABLED fire as appropriate."""
    # Use the existing golden/integration orchestrator harness (search
    # backend/tests/integration/ for the fixture that runs a full turn with a
    # fixture LLM response). Configure the fixture response to include a tracker
    # block:  <<<TRACKER {"cast_changes":[{"character_id":"reyes","change":"enter","confidence":0.9}]} TRACKER>>>
    # (match the real tracker delimiters used by together.py).
    ...
```

Build this against the real integration harness in `backend/tests/integration/` (mirror an existing test that drives `orchestrator.submit_post` with a fixture LLM response and inspects the scene). Assert: a pending cast change exists after the turn; after `scenes.confirm_cast_change`, `present_character_refs` contains the resolved ref.

- [ ] **Step 2: Run it**

Run: `cd backend && uv run pytest tests/integration/test_cast_change_turn.py -v`
Expected: PASS.

- [ ] **Step 3: Update the spec status + run the full suites**

Change the spec header `Status:` to `Implemented (2026-05-28)`.

Run the gates:
```bash
cd backend && uv run pytest -q && uv run ruff check && uv run ruff format --check
cd frontend && pnpm test && pnpm typecheck && pnpm lint
```
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_cast_change_turn.py docs/superpowers/specs/2026-05-28-cast-change-detection-design.md
git commit -m "test(integration): end-to-end cast-change turn; mark spec implemented (#464)"
```

---

## Self-Review Notes

- **Spec coverage:** schema (T2), all three strategies (T3–T5), always-review channel (T6–T8), resolution + unknown→candidate + no-op drop (T9–T10), PCs-and-NPCs dispatch (T8), dedicated endpoints (T11), frontend confirm/dismiss (T12–T13), integration + advance-gating assertion (T14). All spec sections map to a task.
- **Type consistency:** `CastChange`/`CastChangeProposal`/`PendingCastChange` (T1) used identically in T3–T11; `CastRef` (T9) consumed by `resolve_cast_changes` (T10); `queue_cast_change` signature defined in T8 matched by the `_RecordingScenes` fake and the helper call in T10; endpoint paths in T11 match the client in T12.
- **Verification-before-completion:** every task ends by running the named test; T14 runs the full gates. Several tasks ask the engineer to confirm an exact existing signature (fixtures, `slugify_id` location, `ToolCall`/`source_chain` shapes) before running — these are real lookups, not placeholders, because the surrounding code dictates the exact form.
