# New Scene Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let players start new scenes by picking from persistent scene ideas (the Scene Ledger) and fresh LLM-generated suggestions, with a preview/confirm step and automatic first-post generation.

**Architecture:** A new `scene_ledger` SQLite table stores per-campaign scene ideas (pre-populated from greetings, grown via LLM). Four new API endpoints handle suggest/preview/start/manage. The frontend adds a `SceneSuggestionView` that replaces the play area during scene selection, driven by a mode state machine in the play reducer.

**Tech Stack:** Python/FastAPI (backend), React/TypeScript (frontend), SQLite (storage), LLM gateway (suggestion + first-post generation)

**Spec:** `docs/superpowers/specs/2026-05-25-new-scene-workflow-design.md`

---

## File Map

### Backend — New Files
| File | Responsibility |
|------|---------------|
| `backend/src/grimoire/storage/migrations/031_scene_ledger.sql` | Migration: create `scene_ledger` table |
| `backend/src/grimoire/scenes/ledger.py` | `SceneLedger` service: CRUD operations on ledger items |
| `backend/src/grimoire/scenes/suggest.py` | `SceneSuggestionEngine`: context assembly + LLM prompt for suggestions, preview resolution, first-post generation |
| `backend/src/grimoire/api/campaigns/new_scene.py` | API routes: suggest, preview, start, ledger CRUD |
| `backend/tests/scenes/test_ledger.py` | Tests for ledger service |
| `backend/tests/scenes/test_suggest.py` | Tests for suggestion engine |
| `backend/tests/api/test_new_scene_api.py` | Integration tests for API endpoints |

### Backend — Modified Files
| File | Change |
|------|--------|
| `backend/src/grimoire/api/campaigns/__init__.py` | Register new_scene router |
| `backend/src/grimoire/api/deps.py` | Add `SceneLedgerDep` |
| `backend/src/grimoire/api/container.py` | Add `scene_ledger` field |
| `backend/src/grimoire/main.py` | Wire `SceneLedger` into container at startup |

### Frontend — New Files
| File | Responsibility |
|------|---------------|
| `frontend/src/routes/campaign/SceneSuggestionView.tsx` | Suggestion picker UI (cards + free-text + refresh) |
| `frontend/src/routes/campaign/ScenePreviewPanel.tsx` | Preview/confirm panel with editable fields |
| `frontend/src/routes/campaign/SceneLedgerDialog.tsx` | Ledger management dialog (dismiss/restore) |
| `frontend/src/api/campaign/newScene.ts` | API client functions for suggest/preview/start/ledger |

### Frontend — Modified Files
| File | Change |
|------|--------|
| `frontend/src/routes/campaign/playReducer.ts` | Add `mode` field + new action types for the scene selection state machine |
| `frontend/src/routes/campaign/PlayView.tsx` | Render `SceneSuggestionView` / `ScenePreviewPanel` when mode is not `play` |
| `frontend/src/routes/campaign/usePlayCommands.ts` | Add `newScene` and `endScene` → auto-transition to suggesting mode |
| `frontend/src/routes/campaign/SidePanel.tsx` | Add "New Scene" + "Scene Ledger" buttons |
| `frontend/src/api/campaign/types.ts` | Add `LedgerItem`, `SuggestResponse`, `PreviewResponse` types |

---

## Task 1: Database Migration — `scene_ledger` Table

**Files:**
- Create: `backend/src/grimoire/storage/migrations/031_scene_ledger.sql`

- [ ] **Step 1: Write the migration file**

```sql
CREATE TABLE scene_ledger (
    id               TEXT PRIMARY KEY,
    campaign_id      TEXT NOT NULL,
    summary          TEXT NOT NULL,
    greeting_id      TEXT,
    source           TEXT NOT NULL CHECK (source IN ('greeting', 'llm', 'user')),
    status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'used', 'dismissed')),
    created_at       TEXT NOT NULL,
    used_in_scene_id TEXT,
    proposed_location TEXT,
    proposed_cast    TEXT
);

CREATE INDEX idx_scene_ledger_campaign_status
    ON scene_ledger (campaign_id, status);
```

Note: `proposed_location` and `proposed_cast` (JSON array string) are stored so the preview endpoint can use them without re-deriving. They're populated by the LLM suggestion or from the greeting's metadata.

- [ ] **Step 2: Verify migration numbering**

Run: `ls backend/src/grimoire/storage/migrations/ | tail -3`

Expected: `031_scene_ledger.sql` is the highest-numbered file. If a `031_*.sql` already exists, renumber to 032.

- [ ] **Step 3: Run migrations to verify SQL is valid**

Run: `cd backend && uv run pytest tests/test_storage.py::test_default_migrations_dir_exists -v`

Expected: PASS (confirms migration file is discoverable)

- [ ] **Step 4: Commit**

```
git add backend/src/grimoire/storage/migrations/031_scene_ledger.sql
git commit -m "feat(db): add scene_ledger table migration"
```

---

## Task 2: Scene Ledger Service

**Files:**
- Create: `backend/src/grimoire/scenes/ledger.py`
- Create: `backend/tests/scenes/test_ledger.py`

- [ ] **Step 1: Write failing tests for ledger CRUD**

Create `backend/tests/scenes/test_ledger.py`:

```python
"""Tests for the SceneLedger service."""
from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.scenes.ledger import SceneLedger
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.sqlite", pool_size=2)
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def ledger(db):
    return SceneLedger(db)


async def test_add_and_list(ledger: SceneLedger) -> None:
    item_id = await ledger.add(
        campaign_id="c1",
        summary="The party investigates the ruins.",
        source="llm",
    )
    items = await ledger.list_active("c1")
    assert len(items) == 1
    assert items[0]["id"] == item_id
    assert items[0]["summary"] == "The party investigates the ruins."
    assert items[0]["source"] == "llm"
    assert items[0]["status"] == "active"


async def test_add_greeting_item(ledger: SceneLedger) -> None:
    item_id = await ledger.add(
        campaign_id="c1",
        summary="A quiet morning at the harbor.",
        source="greeting",
        greeting_id="gr-harbor",
    )
    items = await ledger.list_active("c1")
    assert items[0]["greeting_id"] == "gr-harbor"


async def test_dismiss_and_restore(ledger: SceneLedger) -> None:
    item_id = await ledger.add(
        campaign_id="c1",
        summary="Encounter in the forest.",
        source="llm",
    )
    await ledger.set_status(item_id, "dismissed")
    assert len(await ledger.list_active("c1")) == 0

    await ledger.set_status(item_id, "active")
    assert len(await ledger.list_active("c1")) == 1


async def test_mark_used(ledger: SceneLedger) -> None:
    item_id = await ledger.add(
        campaign_id="c1",
        summary="The tavern scene.",
        source="greeting",
        greeting_id="gr-tavern",
    )
    await ledger.mark_used(item_id, scene_id="scene-001")
    items = await ledger.list_all("c1")
    used = [i for i in items if i["status"] == "used"]
    assert len(used) == 1
    assert used[0]["used_in_scene_id"] == "scene-001"


async def test_list_all_returns_every_status(ledger: SceneLedger) -> None:
    id1 = await ledger.add(campaign_id="c1", summary="A", source="llm")
    id2 = await ledger.add(campaign_id="c1", summary="B", source="llm")
    id3 = await ledger.add(campaign_id="c1", summary="C", source="llm")
    await ledger.set_status(id2, "dismissed")
    await ledger.mark_used(id3, scene_id="s1")
    items = await ledger.list_all("c1")
    assert len(items) == 3


async def test_populate_from_greetings(ledger: SceneLedger) -> None:
    """Simulates campaign creation populating ledger from greetings."""
    greetings = [
        {"id": "gr-1", "name": "The Harbor", "body": "Dawn breaks..."},
        {"id": "gr-2", "name": "The Camp", "body": "Night falls..."},
    ]
    for g in greetings:
        await ledger.add(
            campaign_id="c1",
            summary=g["name"],
            source="greeting",
            greeting_id=g["id"],
        )
    items = await ledger.list_active("c1")
    assert len(items) == 2
    assert all(i["source"] == "greeting" for i in items)


async def test_campaign_isolation(ledger: SceneLedger) -> None:
    await ledger.add(campaign_id="c1", summary="A", source="llm")
    await ledger.add(campaign_id="c2", summary="B", source="llm")
    assert len(await ledger.list_active("c1")) == 1
    assert len(await ledger.list_active("c2")) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/scenes/test_ledger.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.scenes.ledger'`

- [ ] **Step 3: Implement the SceneLedger service**

Create `backend/src/grimoire/scenes/ledger.py`:

```python
"""Scene Ledger: per-campaign persistent store of scene ideas."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from grimoire.storage.db import Database


class SceneLedger:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(
        self,
        *,
        campaign_id: str,
        summary: str,
        source: str,
        greeting_id: str | None = None,
        proposed_location: str | None = None,
        proposed_cast: str | None = None,
    ) -> str:
        item_id = f"ledger-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            """
            INSERT INTO scene_ledger
                (id, campaign_id, summary, greeting_id, source, status,
                 created_at, proposed_location, proposed_cast)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                item_id,
                campaign_id,
                summary,
                greeting_id,
                source,
                datetime.now(UTC).isoformat(),
                proposed_location,
                proposed_cast,
            ),
        )
        return item_id

    async def list_active(self, campaign_id: str) -> list[dict]:
        rows = await self._db.fetchall(
            """
            SELECT * FROM scene_ledger
            WHERE campaign_id = ? AND status = 'active'
            ORDER BY created_at
            """,
            (campaign_id,),
        )
        return [dict(r) for r in rows]

    async def list_all(self, campaign_id: str) -> list[dict]:
        rows = await self._db.fetchall(
            """
            SELECT * FROM scene_ledger
            WHERE campaign_id = ?
            ORDER BY created_at
            """,
            (campaign_id,),
        )
        return [dict(r) for r in rows]

    async def set_status(self, item_id: str, status: str) -> None:
        await self._db.execute(
            "UPDATE scene_ledger SET status = ? WHERE id = ?",
            (status, item_id),
        )

    async def mark_used(self, item_id: str, scene_id: str) -> None:
        await self._db.execute(
            "UPDATE scene_ledger SET status = 'used', used_in_scene_id = ? WHERE id = ?",
            (scene_id, item_id),
        )

    async def get(self, item_id: str) -> dict | None:
        row = await self._db.fetchone(
            "SELECT * FROM scene_ledger WHERE id = ?",
            (item_id,),
        )
        return dict(row) if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/scenes/test_ledger.py -v`

Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```
git add backend/src/grimoire/scenes/ledger.py backend/tests/scenes/test_ledger.py
git commit -m "feat: add SceneLedger service with CRUD operations"
```

---

## Task 3: Wire SceneLedger into Container + Dependencies

**Files:**
- Modify: `backend/src/grimoire/api/container.py`
- Modify: `backend/src/grimoire/api/deps.py`
- Modify: `backend/src/grimoire/main.py`

- [ ] **Step 1: Add `scene_ledger` to ServiceContainer**

In `backend/src/grimoire/api/container.py`, add to the container class:

```python
scene_ledger: SceneLedger | None = None
```

Use the same pattern as `scene_indexer` — check existing fields to match the style (some use `Any`, some use concrete types under `TYPE_CHECKING`).

- [ ] **Step 2: Add dependency helper to deps.py**

In `backend/src/grimoire/api/deps.py`:

Add import (under `TYPE_CHECKING`):
```python
from grimoire.scenes.ledger import SceneLedger
```

Add getter function (after `get_scenes`):
```python
def get_scene_ledger(request: Request) -> SceneLedger:
    return _require(get_container(request), "scene_ledger")
```

Add annotated alias (after `ScenesDep`):
```python
SceneLedgerDep = Annotated[Any, Depends(get_scene_ledger)]
```

Add both to `__all__`.

- [ ] **Step 3: Wire into lifespan in main.py**

In `backend/src/grimoire/main.py`, after the scene indexer block (around line 299), add:

```python
if container.scene_ledger is None:
    from grimoire.scenes.ledger import SceneLedger
    container.scene_ledger = SceneLedger(db)
```

- [ ] **Step 4: Run existing tests to verify no regressions**

Run: `cd backend && uv run pytest tests/scenes/test_indexer.py tests/scenes/test_ledger.py -v`

Expected: All tests PASS

- [ ] **Step 5: Commit**

```
git add backend/src/grimoire/api/container.py backend/src/grimoire/api/deps.py backend/src/grimoire/main.py
git commit -m "feat: wire SceneLedger into DI container and FastAPI deps"
```

---

## Task 4: Ledger API Routes (CRUD)

**Files:**
- Create: `backend/src/grimoire/api/campaigns/new_scene.py`
- Modify: `backend/src/grimoire/api/campaigns/__init__.py`

- [ ] **Step 1: Write the ledger CRUD routes**

Create `backend/src/grimoire/api/campaigns/new_scene.py`:

```python
"""API routes for the new-scene workflow and Scene Ledger."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from grimoire.api.deps import SceneLedgerDep

router = APIRouter()


class LedgerStatusUpdate(BaseModel):
    status: str  # 'active' | 'dismissed'


@router.get("/{campaign_id}/scene-ledger")
async def list_ledger(
    campaign_id: str,
    ledger: SceneLedgerDep,
    status: str | None = None,
) -> list[dict[str, Any]]:
    if status == "active":
        return await ledger.list_active(campaign_id)
    return await ledger.list_all(campaign_id)


@router.patch("/{campaign_id}/scene-ledger/{item_id}")
async def update_ledger_item(
    campaign_id: str,
    item_id: str,
    body: LedgerStatusUpdate,
    ledger: SceneLedgerDep,
) -> dict[str, str]:
    await ledger.set_status(item_id, body.status)
    return {"id": item_id, "status": body.status}
```

- [ ] **Step 2: Register the router**

In `backend/src/grimoire/api/campaigns/__init__.py`, find where other scene routers are included (look for `scenes.router`). Add:

```python
from grimoire.api.campaigns import new_scene
# In the include_router calls:
router.include_router(new_scene.router)
```

Follow the exact pattern used for `scenes.router`.

- [ ] **Step 3: Run the app to verify routes register**

Run: `cd backend && uv run python -c "from grimoire.main import app; print([r.path for r in app.routes if 'ledger' in str(r.path)])""`

Expected: Prints routes containing `scene-ledger`

- [ ] **Step 4: Commit**

```
git add backend/src/grimoire/api/campaigns/new_scene.py backend/src/grimoire/api/campaigns/__init__.py
git commit -m "feat: add scene-ledger GET/PATCH API routes"
```

---

## Task 5: Suggestion Engine — Context Assembly + LLM Prompt

**Files:**
- Create: `backend/src/grimoire/scenes/suggest.py`
- Create: `backend/tests/scenes/test_suggest.py`

- [ ] **Step 1: Write failing test for suggestion generation**

Create `backend/tests/scenes/test_suggest.py`:

```python
"""Tests for the SceneSuggestionEngine."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from grimoire.scenes.ledger import SceneLedger
from grimoire.scenes.suggest import SceneSuggestionEngine, SuggestionContext
from grimoire.storage import Database, apply_migrations


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.sqlite", pool_size=2)
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def ledger(db):
    return SceneLedger(db)


def _mock_gateway(suggestions: list[dict]) -> AsyncMock:
    """Create a mock LLM gateway that returns structured suggestions."""
    gateway = AsyncMock()
    response = AsyncMock()
    response.text = json.dumps(suggestions)
    gateway.complete = AsyncMock(return_value=response)
    return gateway


async def test_suggest_returns_ledger_plus_generated(
    ledger: SceneLedger,
) -> None:
    # Pre-populate 2 ledger items
    await ledger.add(campaign_id="c1", summary="The harbor at dawn.", source="greeting", greeting_id="gr-1")
    await ledger.add(campaign_id="c1", summary="A meeting with the Archon.", source="llm")

    generated = [
        {"summary": "Bandits on the road.", "proposed_location": "South Road", "proposed_cast": ["alistair"]},
        {"summary": "A letter arrives.", "proposed_location": "Camp", "proposed_cast": ["mirella"]},
    ]
    gateway = _mock_gateway(generated)

    engine = SceneSuggestionEngine(ledger=ledger, gateway=gateway)
    ctx = SuggestionContext(
        campaign_id="c1",
        recent_summaries=["The party escaped the catacombs."],
        open_threads=["Missing shipment unresolved."],
        active_pcs=["alistair", "mirella"],
        last_location="Thornwall",
        in_game_time="Day 12, evening",
        unused_greeting_names=["The harbor at dawn"],
    )
    result = await engine.suggest(ctx)

    assert len(result["ledger_picks"]) == 2
    assert len(result["generated"]) >= 2
    assert result["generated"][0]["summary"] == "Bandits on the road."
    gateway.complete.assert_called_once()


async def test_suggest_caps_ledger_at_3(ledger: SceneLedger) -> None:
    for i in range(5):
        await ledger.add(campaign_id="c1", summary=f"Idea {i}", source="llm")

    generated = [
        {"summary": "Fresh idea 1.", "proposed_location": "X", "proposed_cast": []},
        {"summary": "Fresh idea 2.", "proposed_location": "Y", "proposed_cast": []},
    ]
    gateway = _mock_gateway(generated)
    engine = SceneSuggestionEngine(ledger=ledger, gateway=gateway)
    ctx = SuggestionContext(
        campaign_id="c1", recent_summaries=[], open_threads=[],
        active_pcs=[], last_location=None, in_game_time=None,
        unused_greeting_names=[],
    )
    result = await engine.suggest(ctx)

    assert len(result["ledger_picks"]) <= 3
    assert len(result["generated"]) >= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/scenes/test_suggest.py -v`

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement the suggestion engine**

Create `backend/src/grimoire/scenes/suggest.py`:

```python
"""Scene suggestion engine: assembles context and generates ideas via LLM."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from grimoire.scenes.ledger import SceneLedger

logger = logging.getLogger(__name__)

_MAX_LEDGER_PICKS = 3
_MIN_GENERATED = 2

_SUGGEST_SYSTEM_PROMPT = """\
You are a narrative assistant for a tabletop RPG campaign. Given the campaign \
context below, generate scene suggestions for what could happen next.

Each suggestion is a single sentence describing a scene hook. Include a \
proposed_location (where it takes place) and proposed_cast (character refs \
likely involved). Return a JSON array of objects with keys: summary, \
proposed_location, proposed_cast.

Generate diverse suggestions: some advancing the main plot, some exploring \
character relationships, some introducing new complications. Do NOT reference \
greeting IDs or invent them — only use the campaign context."""


@dataclass
class SuggestionContext:
    campaign_id: str
    recent_summaries: list[str]
    open_threads: list[str]
    active_pcs: list[str]
    last_location: str | None
    in_game_time: str | None
    unused_greeting_names: list[str]
    num_to_generate: int = _MIN_GENERATED


class SceneSuggestionEngine:
    def __init__(self, *, ledger: SceneLedger, gateway: object) -> None:
        self._ledger = ledger
        self._gateway = gateway

    async def suggest(self, ctx: SuggestionContext) -> dict:
        active = await self._ledger.list_active(ctx.campaign_id)
        ledger_picks = active[:_MAX_LEDGER_PICKS]

        user_prompt = self._build_user_prompt(ctx)

        from grimoire.types.llm import CompletionRequest, Message

        request = CompletionRequest(
            model="default",
            messages=[Message(role="user", content=user_prompt)],
            system=_SUGGEST_SYSTEM_PROMPT,
            max_tokens=1024,
            temperature=1.0,
        )
        response = await self._gateway.complete(
            "scene_suggest", request, campaign_id=ctx.campaign_id
        )

        try:
            generated = json.loads(response.text)
            if not isinstance(generated, list):
                generated = []
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse suggestion response: %s", response.text[:200])
            generated = []

        return {
            "ledger_picks": [
                {
                    "ledger_id": item["id"],
                    "summary": item["summary"],
                    "greeting_id": item.get("greeting_id"),
                    "source": item["source"],
                }
                for item in ledger_picks
            ],
            "generated": [
                {
                    "summary": g.get("summary", ""),
                    "proposed_location": g.get("proposed_location"),
                    "proposed_cast": g.get("proposed_cast", []),
                }
                for g in generated
            ],
        }

    def _build_user_prompt(self, ctx: SuggestionContext) -> str:
        parts = [f"Generate {ctx.num_to_generate} scene suggestions.\n"]
        if ctx.recent_summaries:
            parts.append("Recent scenes:")
            for s in ctx.recent_summaries:
                parts.append(f"- {s}")
        if ctx.open_threads:
            parts.append("\nOpen threads:")
            for t in ctx.open_threads:
                parts.append(f"- {t}")
        if ctx.active_pcs:
            parts.append(f"\nActive PCs: {', '.join(ctx.active_pcs)}")
        if ctx.last_location:
            parts.append(f"Last location: {ctx.last_location}")
        if ctx.in_game_time:
            parts.append(f"In-game time: {ctx.in_game_time}")
        if ctx.unused_greeting_names:
            parts.append("\nUnused greeting hooks (available for inspiration):")
            for name in ctx.unused_greeting_names:
                parts.append(f"- {name}")
        return "\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/scenes/test_suggest.py -v`

Expected: All tests PASS

- [ ] **Step 5: Commit**

```
git add backend/src/grimoire/scenes/suggest.py backend/tests/scenes/test_suggest.py
git commit -m "feat: add SceneSuggestionEngine with LLM prompt assembly"
```

---

## Task 6: Suggest + Preview + Start API Endpoints

**Files:**
- Modify: `backend/src/grimoire/api/campaigns/new_scene.py`

- [ ] **Step 1: Add Pydantic models for request/response**

Add to the top of `new_scene.py`:

```python
from grimoire.api.deps import (
    ContinuityDep,
    LibraryDep,
    LLMGatewayDep,
    SceneLedgerDep,
    ScenesDep,
    StateStoreDep,
)
from grimoire.scenes.suggest import SceneSuggestionEngine, SuggestionContext


class PreviewRequest(BaseModel):
    ledger_id: str | None = None
    generated_suggestion: dict[str, Any] | None = None
    custom_text: str | None = None
    greeting_id: str | None = None


class PreviewResponse(BaseModel):
    title: str
    location_ref: str | None = None
    in_game_start: str | None = None
    present_character_refs: list[str] = []
    present_pc_refs: list[str] = []
    greeting_id: str | None = None
    first_post_source: str  # 'greeting' | 'adapted_greeting' | 'generated'
    ledger_id: str | None = None


class StartRequest(BaseModel):
    title: str
    location_ref: str | None = None
    in_game_start: str | None = None
    present_character_refs: list[str] = []
    present_pc_refs: list[str] = []
    greeting_id: str | None = None
    first_post_source: str
    ledger_id: str | None = None
    unchosen_generated: list[dict[str, Any]] = []
```

- [ ] **Step 2: Add the suggest endpoint**

```python
@router.post("/{campaign_id}/scenes/suggest")
async def suggest_scenes(
    campaign_id: str,
    ledger: SceneLedgerDep,
    gateway: LLMGatewayDep,
    scenes: ScenesDep,
    continuity: ContinuityDep,
    state_store: StateStoreDep,
) -> dict[str, Any]:
    # Gather context
    recent_scenes = await scenes.list_scenes(campaign_id)
    closed = [s for s in recent_scenes if s.closed]
    recent_summaries = [
        s.final_summary or s.running_summary or ""
        for s in closed[-3:]
        if s.final_summary or s.running_summary
    ]

    # Get active PCs from state_store
    pcs = await state_store.list_campaign_pcs(campaign_id)
    active_pcs = [pc["character_ref"] for pc in pcs if pc.get("active")]

    # Get open threads from continuity
    continuity_svc = continuity.for_campaign(campaign_id)
    commitments = await continuity_svc.list_commitments(status="OPEN")
    open_threads = [c.text for c in commitments[:10]]

    # Unused greetings from ledger
    active_items = await ledger.list_active(campaign_id)
    greeting_names = [
        i["summary"] for i in active_items if i["source"] == "greeting"
    ]

    # Last scene context
    last_location = closed[-1].location_ref if closed else None
    last_time = None
    if closed and closed[-1].in_game_end:
        last_time = str(closed[-1].in_game_end)
    elif closed and closed[-1].in_game_start:
        last_time = str(closed[-1].in_game_start)

    engine = SceneSuggestionEngine(ledger=ledger, gateway=gateway)
    ctx = SuggestionContext(
        campaign_id=campaign_id,
        recent_summaries=recent_summaries,
        open_threads=open_threads,
        active_pcs=active_pcs,
        last_location=last_location,
        in_game_time=last_time,
        unused_greeting_names=greeting_names,
    )
    return await engine.suggest(ctx)
```

- [ ] **Step 3: Add the preview endpoint**

```python
@router.post("/{campaign_id}/scenes/preview")
async def preview_scene(
    campaign_id: str,
    body: PreviewRequest,
    ledger: SceneLedgerDep,
    gateway: LLMGatewayDep,
    state_store: StateStoreDep,
) -> PreviewResponse:
    from grimoire.types.llm import CompletionRequest, Message

    pcs = await state_store.list_campaign_pcs(campaign_id)
    active_pc_refs = [pc["character_ref"] for pc in pcs if pc.get("active")]

    # Determine the scene description and greeting_id
    description: str = ""
    greeting_id: str | None = None
    ledger_id: str | None = None

    if body.ledger_id:
        item = await ledger.get(body.ledger_id)
        if item:
            description = item["summary"]
            greeting_id = item.get("greeting_id")
            ledger_id = item["id"]
    elif body.generated_suggestion:
        description = body.generated_suggestion.get("summary", "")
    elif body.custom_text:
        description = body.custom_text

    if body.greeting_id:
        greeting_id = body.greeting_id

    first_post_source = "generated"
    if greeting_id:
        first_post_source = "greeting"

    # Use LLM to resolve description into scene metadata
    prompt = (
        f"Given this scene description for a TTRPG campaign, extract structured metadata.\n\n"
        f"Description: {description}\n\n"
        f"Return JSON with keys: title (short scene title), location_ref (place name or null), "
        f"in_game_start (time description or null), present_character_refs (list of character names)."
    )
    request = CompletionRequest(
        model="default",
        messages=[Message(role="user", content=prompt)],
        max_tokens=512,
        temperature=0.3,
    )
    response = await gateway.complete("scene_preview", request, campaign_id=campaign_id)

    import json
    try:
        meta = json.loads(response.text)
    except (json.JSONDecodeError, TypeError):
        meta = {}

    return PreviewResponse(
        title=meta.get("title", description[:50]),
        location_ref=meta.get("location_ref"),
        in_game_start=meta.get("in_game_start"),
        present_character_refs=meta.get("present_character_refs", []),
        present_pc_refs=active_pc_refs,
        greeting_id=greeting_id,
        first_post_source=first_post_source,
        ledger_id=ledger_id,
    )
```

- [ ] **Step 4: Add the start endpoint**

```python
@router.post("/{campaign_id}/scenes/start")
async def start_scene(
    campaign_id: str,
    body: StartRequest,
    scenes: ScenesDep,
    ledger: SceneLedgerDep,
    gateway: LLMGatewayDep,
    state_store: StateStoreDep,
    library: LibraryDep,
) -> dict[str, Any]:
    from grimoire.scenes.types import SceneInit

    init = SceneInit(
        campaign_id=campaign_id,
        title=body.title,
        location_ref=body.location_ref,
        greeting_id=body.greeting_id,
        present_character_refs=body.present_character_refs,
        present_pc_refs=body.present_pc_refs,
    )
    scene = await scenes.start_scene(init)

    # Generate first post
    first_post = None
    if body.first_post_source == "greeting" and body.greeting_id:
        # Use existing greeting seeding logic
        from grimoire.api.campaigns.helpers import _seed_greeting_first_post

        composition = await state_store.get_campaign_composition(campaign_id)
        world_id = composition.get("world_refs", [None])[0] if composition else None
        greeting = None
        if world_id:
            try:
                greeting = await library.get_greeting(world_id, body.greeting_id)
            except Exception:
                pass
        if greeting:
            await _seed_greeting_first_post(
                scenes=scenes,
                scene=scene,
                greeting=greeting,
                state_store=state_store,
                library=library,
                world_id=world_id,
            )
            posts = await scenes.get_posts(scene.id)
            first_post = posts[0] if posts else None
    else:
        # LLM-generated first post
        from grimoire.scenes.types import AuthorKind
        from grimoire.scenes import new_post
        from grimoire.types.llm import CompletionRequest, Message

        prompt = (
            f"Write the opening narrator post for a TTRPG scene.\n\n"
            f"Title: {body.title}\n"
            f"Location: {body.location_ref or 'unspecified'}\n"
            f"Present characters: {', '.join(body.present_character_refs) or 'unspecified'}\n\n"
            f"Write 2-3 paragraphs of atmospheric scene-setting in second person. "
            f"Do not include any metadata or headers — just the narrative text."
        )
        request = CompletionRequest(
            model="default",
            messages=[Message(role="user", content=prompt)],
            max_tokens=1024,
            temperature=0.9,
        )
        response = await gateway.complete(
            "scene_first_post", request, campaign_id=campaign_id
        )
        post = new_post(
            author_kind=AuthorKind.NARRATOR,
            body=response.text.strip(),
            is_player=False,
        )
        await scenes.append_post(scene.id, post)
        first_post = post

    # Mark ledger item used
    if body.ledger_id:
        await ledger.mark_used(body.ledger_id, scene_id=scene.id)

    # Save unchosen generated suggestions to ledger
    for suggestion in body.unchosen_generated:
        await ledger.add(
            campaign_id=campaign_id,
            summary=suggestion.get("summary", ""),
            source="llm",
            proposed_location=suggestion.get("proposed_location"),
            proposed_cast=json.dumps(suggestion.get("proposed_cast", [])),
        )

    from grimoire.api.campaigns.scenes import to_payload
    return {
        "scene": to_payload(scene),
        "first_post": {
            "id": first_post.id if first_post else None,
            "body": first_post.body if first_post else None,
        },
    }
```

- [ ] **Step 5: Add missing import at top of file**

Ensure `json` is imported at the top of the file:
```python
import json
```

- [ ] **Step 6: Commit**

```
git add backend/src/grimoire/api/campaigns/new_scene.py
git commit -m "feat: add suggest/preview/start API endpoints for new scene workflow"
```

---

## Task 7: Frontend Types + API Client

**Files:**
- Create: `frontend/src/api/campaign/newScene.ts`
- Modify: `frontend/src/api/campaign/types.ts`

- [ ] **Step 1: Add TypeScript types**

Add to `frontend/src/api/campaign/types.ts`:

```typescript
export interface LedgerItem {
  ledger_id: string;
  summary: string;
  greeting_id: string | null;
  source: "greeting" | "llm" | "user";
}

export interface GeneratedSuggestion {
  summary: string;
  proposed_location: string | null;
  proposed_cast: string[];
}

export interface SuggestResponse {
  ledger_picks: LedgerItem[];
  generated: GeneratedSuggestion[];
}

export interface PreviewResponse {
  title: string;
  location_ref: string | null;
  in_game_start: string | null;
  present_character_refs: string[];
  present_pc_refs: string[];
  greeting_id: string | null;
  first_post_source: "greeting" | "adapted_greeting" | "generated";
  ledger_id: string | null;
}

export interface LedgerEntry {
  id: string;
  campaign_id: string;
  summary: string;
  greeting_id: string | null;
  source: "greeting" | "llm" | "user";
  status: "active" | "used" | "dismissed";
  created_at: string;
  used_in_scene_id: string | null;
}
```

- [ ] **Step 2: Create API client module**

Create `frontend/src/api/campaign/newScene.ts`:

```typescript
import { api } from "../base";
import type {
  GeneratedSuggestion,
  LedgerEntry,
  PreviewResponse,
  SuggestResponse,
} from "./types";

const enc = encodeURIComponent;

export const newSceneApi = {
  suggest: (campaignId: string) =>
    api.post<SuggestResponse>(
      `/api/campaigns/${enc(campaignId)}/scenes/suggest`,
    ),

  preview: (
    campaignId: string,
    body: {
      ledger_id?: string;
      generated_suggestion?: GeneratedSuggestion;
      custom_text?: string;
      greeting_id?: string;
    },
  ) =>
    api.post<PreviewResponse>(
      `/api/campaigns/${enc(campaignId)}/scenes/preview`,
      body,
    ),

  start: (
    campaignId: string,
    body: PreviewResponse & {
      unchosen_generated: GeneratedSuggestion[];
    },
  ) =>
    api.post<{ scene: import("./types").ApiScene; first_post: { id: string; body: string } }>(
      `/api/campaigns/${enc(campaignId)}/scenes/start`,
      body,
    ),

  listLedger: (campaignId: string, status?: string) =>
    api.get<LedgerEntry[]>(
      `/api/campaigns/${enc(campaignId)}/scene-ledger`,
      { query: status ? { status } : undefined },
    ),

  updateLedger: (campaignId: string, itemId: string, status: "active" | "dismissed") =>
    api.patch<{ id: string; status: string }>(
      `/api/campaigns/${enc(campaignId)}/scene-ledger/${enc(itemId)}`,
      { status },
    ),
};
```

- [ ] **Step 3: Commit**

```
git add frontend/src/api/campaign/newScene.ts frontend/src/api/campaign/types.ts
git commit -m "feat: add frontend types and API client for new scene workflow"
```

---

## Task 8: Play Reducer — Mode State Machine

**Files:**
- Modify: `frontend/src/routes/campaign/playReducer.ts`

- [ ] **Step 1: Add mode and suggestion state to PlayState**

Add to the `PlayState` interface:

```typescript
mode: "play" | "suggesting" | "picking" | "previewing" | "creating";
suggestions: import("../../api/campaign/types").SuggestResponse | null;
preview: import("../../api/campaign/types").PreviewResponse | null;
```

- [ ] **Step 2: Add new action types to PlayAction**

Add these union members to `PlayAction`:

```typescript
| { type: "start-new-scene" }
| { type: "suggestions-loaded"; suggestions: import("../../api/campaign/types").SuggestResponse }
| { type: "preview-loaded"; preview: import("../../api/campaign/types").PreviewResponse }
| { type: "back-to-picking" }
| { type: "creating-scene" }
```

- [ ] **Step 3: Update initial state**

In the initial state object, add:

```typescript
mode: "play",
suggestions: null,
preview: null,
```

- [ ] **Step 4: Add reducer cases**

Add cases to the reducer switch:

```typescript
case "start-new-scene":
  return { ...state, mode: "suggesting", suggestions: null, preview: null };

case "suggestions-loaded":
  return { ...state, mode: "picking", suggestions: action.suggestions };

case "preview-loaded":
  return { ...state, mode: "previewing", preview: action.preview };

case "back-to-picking":
  return { ...state, mode: "picking", preview: null };

case "creating-scene":
  return { ...state, mode: "creating" };
```

When a `"loaded"` or `"set-scene"` action fires (scene is set), reset mode to `"play"`:

In the existing `"loaded"` case, add: `mode: "play", suggestions: null, preview: null,`

In the existing `"set-scene"` case, add: `mode: "play", suggestions: null, preview: null,`

- [ ] **Step 5: Commit**

```
git add frontend/src/routes/campaign/playReducer.ts
git commit -m "feat: add mode state machine to play reducer for new scene flow"
```

---

## Task 9: SceneSuggestionView Component

**Files:**
- Create: `frontend/src/routes/campaign/SceneSuggestionView.tsx`

- [ ] **Step 1: Create the component**

```tsx
import { type Dispatch, useCallback, useState } from "react";

import { newSceneApi } from "../../api/campaign/newScene";
import type {
  GeneratedSuggestion,
  LedgerItem,
  SuggestResponse,
} from "../../api/campaign/types";
import type { PlayAction } from "./playReducer";

interface Props {
  campaignId: string;
  suggestions: SuggestResponse;
  dispatch: Dispatch<PlayAction>;
}

export function SceneSuggestionView({ campaignId, suggestions, dispatch }: Props) {
  const [customText, setCustomText] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const pickLedgerItem = useCallback(
    async (item: LedgerItem) => {
      const resp = await newSceneApi.preview(campaignId, {
        ledger_id: item.ledger_id,
      });
      dispatch({ type: "preview-loaded", preview: resp });
    },
    [campaignId, dispatch],
  );

  const pickGenerated = useCallback(
    async (suggestion: GeneratedSuggestion, index: number) => {
      const resp = await newSceneApi.preview(campaignId, {
        generated_suggestion: suggestion,
      });
      dispatch({ type: "preview-loaded", preview: resp });
    },
    [campaignId, dispatch],
  );

  const submitCustom = useCallback(async () => {
    if (!customText.trim()) return;
    const resp = await newSceneApi.preview(campaignId, {
      custom_text: customText.trim(),
    });
    dispatch({ type: "preview-loaded", preview: resp });
  }, [campaignId, customText, dispatch]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const resp = await newSceneApi.suggest(campaignId);
      dispatch({ type: "suggestions-loaded", suggestions: resp });
    } finally {
      setRefreshing(false);
    }
  }, [campaignId, dispatch]);

  return (
    <div className="scene-suggestion-view">
      <div className="suggestion-header">
        <h2>What happens next?</h2>
        <p>Pick a suggestion, or describe the next scene yourself.</p>
      </div>

      <div className="suggestion-list">
        {suggestions.ledger_picks.map((item, i) => (
          <button
            key={item.ledger_id}
            className={`suggestion-card ${item.greeting_id ? "greeting" : ""}`}
            onClick={() => pickLedgerItem(item)}
          >
            <span className="suggestion-number">{i + 1}</span>
            <span className="suggestion-text">{item.summary}</span>
            {item.greeting_id && (
              <span className="greeting-badge">Greeting</span>
            )}
          </button>
        ))}

        {suggestions.generated.map((g, i) => (
          <button
            key={`gen-${i}`}
            className="suggestion-card generated"
            onClick={() => pickGenerated(g, i)}
          >
            <span className="suggestion-number">
              {suggestions.ledger_picks.length + i + 1}
            </span>
            <span className="suggestion-text">{g.summary}</span>
          </button>
        ))}
      </div>

      <div className="suggestion-footer">
        <input
          type="text"
          value={customText}
          onChange={(e) => setCustomText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submitCustom()}
          placeholder="Or describe the next scene in your own words..."
          className="custom-scene-input"
        />
        <button
          onClick={refresh}
          disabled={refreshing}
          className="refresh-btn"
        >
          {refreshing ? "..." : "Refresh"}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```
git add frontend/src/routes/campaign/SceneSuggestionView.tsx
git commit -m "feat: add SceneSuggestionView component"
```

---

## Task 10: ScenePreviewPanel Component

**Files:**
- Create: `frontend/src/routes/campaign/ScenePreviewPanel.tsx`

- [ ] **Step 1: Create the component**

```tsx
import { type Dispatch, useCallback, useState } from "react";

import { newSceneApi } from "../../api/campaign/newScene";
import type {
  GeneratedSuggestion,
  PreviewResponse,
  SuggestResponse,
} from "../../api/campaign/types";
import type { PlayAction } from "./playReducer";

interface Props {
  campaignId: string;
  preview: PreviewResponse;
  suggestions: SuggestResponse | null;
  dispatch: Dispatch<PlayAction>;
  onSceneCreated: () => Promise<void>;
}

export function ScenePreviewPanel({
  campaignId,
  preview,
  suggestions,
  dispatch,
  onSceneCreated,
}: Props) {
  const [title, setTitle] = useState(preview.title);
  const [location, setLocation] = useState(preview.location_ref ?? "");
  const [creating, setCreating] = useState(false);

  const back = useCallback(() => {
    dispatch({ type: "back-to-picking" });
  }, [dispatch]);

  const confirm = useCallback(async () => {
    setCreating(true);
    dispatch({ type: "creating-scene" });
    try {
      // Collect unchosen generated suggestions for ledger storage
      const unchosen: GeneratedSuggestion[] = suggestions?.generated ?? [];

      await newSceneApi.start(campaignId, {
        ...preview,
        title,
        location_ref: location || null,
        unchosen_generated: unchosen,
      });
      await onSceneCreated();
    } finally {
      setCreating(false);
    }
  }, [campaignId, preview, title, location, suggestions, dispatch, onSceneCreated]);

  const sourceLabel =
    preview.first_post_source === "greeting"
      ? `Opening from greeting`
      : preview.first_post_source === "adapted_greeting"
        ? `Adapted from greeting`
        : "Opening will be generated";

  return (
    <div className="scene-preview-panel">
      <h2>Scene Preview</h2>

      <div className="preview-fields">
        <label>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label>
          Location
          <input
            value={location}
            onChange={(e) => setLocation(e.target.value)}
          />
        </label>
        <div className="preview-row">
          <span className="preview-label">Time</span>
          <span>{preview.in_game_start ?? "Continuing from last scene"}</span>
        </div>
        <div className="preview-row">
          <span className="preview-label">Cast</span>
          <span>
            {preview.present_character_refs.length
              ? preview.present_character_refs.join(", ")
              : preview.present_pc_refs.join(", ")}
          </span>
        </div>
        <div className="preview-row">
          <span className="preview-label">First post</span>
          <span className="source-label">{sourceLabel}</span>
        </div>
      </div>

      <div className="preview-actions">
        <button onClick={back} disabled={creating}>
          Back
        </button>
        <button
          onClick={confirm}
          disabled={creating}
          className="primary"
        >
          {creating ? "Creating..." : "Start Scene"}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```
git add frontend/src/routes/campaign/ScenePreviewPanel.tsx
git commit -m "feat: add ScenePreviewPanel component"
```

---

## Task 11: SceneLedgerDialog Component

**Files:**
- Create: `frontend/src/routes/campaign/SceneLedgerDialog.tsx`

- [ ] **Step 1: Create the component**

```tsx
import { useCallback, useEffect, useState } from "react";

import { newSceneApi } from "../../api/campaign/newScene";
import type { LedgerEntry } from "../../api/campaign/types";

interface Props {
  campaignId: string;
  open: boolean;
  onClose: () => void;
}

export function SceneLedgerDialog({ campaignId, open, onClose }: Props) {
  const [items, setItems] = useState<LedgerEntry[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await newSceneApi.listLedger(campaignId);
      setItems(resp);
    } finally {
      setLoading(false);
    }
  }, [campaignId]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const toggleStatus = useCallback(
    async (item: LedgerEntry) => {
      const newStatus = item.status === "dismissed" ? "active" : "dismissed";
      await newSceneApi.updateLedger(campaignId, item.id, newStatus);
      await load();
    },
    [campaignId, load],
  );

  if (!open) return null;

  const active = items.filter((i) => i.status === "active");
  const used = items.filter((i) => i.status === "used");
  const dismissed = items.filter((i) => i.status === "dismissed");

  return (
    <div className="ledger-dialog-backdrop" onClick={onClose}>
      <div className="ledger-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="ledger-header">
          <h2>Scene Ledger</h2>
          <button onClick={onClose} className="close-btn">
            &times;
          </button>
        </div>

        {loading ? (
          <p>Loading...</p>
        ) : (
          <div className="ledger-content">
            {active.length > 0 && (
              <section>
                <h3>Active</h3>
                {active.map((item) => (
                  <LedgerRow
                    key={item.id}
                    item={item}
                    onToggle={() => toggleStatus(item)}
                    actionLabel="Dismiss"
                  />
                ))}
              </section>
            )}
            {used.length > 0 && (
              <section>
                <h3>Used</h3>
                {used.map((item) => (
                  <LedgerRow key={item.id} item={item} />
                ))}
              </section>
            )}
            {dismissed.length > 0 && (
              <section>
                <h3>Dismissed</h3>
                {dismissed.map((item) => (
                  <LedgerRow
                    key={item.id}
                    item={item}
                    onToggle={() => toggleStatus(item)}
                    actionLabel="Restore"
                  />
                ))}
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function LedgerRow({
  item,
  onToggle,
  actionLabel,
}: {
  item: LedgerEntry;
  onToggle?: () => void;
  actionLabel?: string;
}) {
  return (
    <div className="ledger-row">
      <span className={`source-badge ${item.source}`}>{item.source}</span>
      <span className="ledger-summary">{item.summary}</span>
      {onToggle && actionLabel && (
        <button onClick={onToggle} className="ledger-action">
          {actionLabel}
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```
git add frontend/src/routes/campaign/SceneLedgerDialog.tsx
git commit -m "feat: add SceneLedgerDialog component"
```

---

## Task 12: Wire into PlayView + SidePanel + Commands

**Files:**
- Modify: `frontend/src/routes/campaign/PlayView.tsx`
- Modify: `frontend/src/routes/campaign/SidePanel.tsx`
- Modify: `frontend/src/routes/campaign/usePlayCommands.ts`

- [ ] **Step 1: Update usePlayCommands — add newScene command**

In `frontend/src/routes/campaign/usePlayCommands.ts`, add a `newScene` command:

```typescript
const newScene = useCallback(async () => {
  dispatch({ type: "start-new-scene" });
  try {
    const resp = await newSceneApi.suggest(campaignId);
    dispatch({ type: "suggestions-loaded", suggestions: resp });
  } catch (err) {
    dispatch({ type: "loaded", scene: stateRef.current.scene, posts: stateRef.current.posts, pcs: stateRef.current.pcs, activePcRef: stateRef.current.activePcRef });
    throw err;
  }
}, [campaignId, dispatch, stateRef]);
```

Import `newSceneApi` at the top:
```typescript
import { newSceneApi } from "../../api/campaign/newScene";
```

Update `endScene` to auto-trigger the new scene flow after ending:

```typescript
const endScene = useCallback(async () => {
  const scene = stateRef.current.scene;
  if (!scene) return;
  await campaignApi.endScene(campaignId, scene.id);
  // Auto-transition to new scene suggestions
  dispatch({ type: "start-new-scene" });
  try {
    const resp = await newSceneApi.suggest(campaignId);
    dispatch({ type: "suggestions-loaded", suggestions: resp });
  } catch {
    await refresh();
  }
}, [campaignId, refresh, stateRef, dispatch]);
```

Add `newScene` to the return object:
```typescript
return { setActivePC, submit, advance, regenerate, undo, endScene, suppressDrift, newScene };
```

- [ ] **Step 2: Update SidePanel — add New Scene + Ledger buttons**

In `frontend/src/routes/campaign/SidePanel.tsx`:

Add to `QuickActions` interface:
```typescript
onNewScene: () => void;
onOpenLedger: () => void;
```

Add buttons after the "End scene" button:

```tsx
<button
  onClick={actions.onNewScene}
  disabled={actions.busy || (scene != null && !scene.closed)}
>
  New scene
</button>
<button onClick={actions.onOpenLedger} disabled={actions.busy}>
  Scene ledger
</button>
```

- [ ] **Step 3: Update PlayView — render suggestion/preview views**

In `frontend/src/routes/campaign/PlayView.tsx`:

Import the new components:
```typescript
import { SceneSuggestionView } from "./SceneSuggestionView";
import { ScenePreviewPanel } from "./ScenePreviewPanel";
import { SceneLedgerDialog } from "./SceneLedgerDialog";
```

Add ledger dialog state:
```typescript
const [ledgerOpen, setLedgerOpen] = useState(false);
```

In the JSX where `ScenePane` is rendered, wrap it with a mode check:

```tsx
{state.mode === "picking" && state.suggestions ? (
  <SceneSuggestionView
    campaignId={campaignId}
    suggestions={state.suggestions}
    dispatch={dispatch}
  />
) : state.mode === "previewing" && state.preview ? (
  <ScenePreviewPanel
    campaignId={campaignId}
    preview={state.preview}
    suggestions={state.suggestions}
    dispatch={dispatch}
    onSceneCreated={refresh}
  />
) : state.mode === "suggesting" || state.mode === "creating" ? (
  <div className="loading-scene">Loading...</div>
) : (
  <ScenePane /* ...existing props... */ />
)}
```

Add the ledger dialog:
```tsx
<SceneLedgerDialog
  campaignId={campaignId}
  open={ledgerOpen}
  onClose={() => setLedgerOpen(false)}
/>
```

Wire the new actions through to SidePanel:
```tsx
onNewScene: () => void runAction(() => play.newScene()),
onOpenLedger: () => setLedgerOpen(true),
```

- [ ] **Step 4: Commit**

```
git add frontend/src/routes/campaign/PlayView.tsx frontend/src/routes/campaign/SidePanel.tsx frontend/src/routes/campaign/usePlayCommands.ts
git commit -m "feat: wire new scene flow into PlayView, SidePanel, and commands"
```

---

## Task 13: CSS Styling for New Scene Components

**Files:**
- Find and modify the existing CSS/SCSS file used by the play view (likely `PlayView.css`, `PlayView.module.css`, or similar)

- [ ] **Step 1: Locate the existing play view styles**

Run: `find frontend/src/routes/campaign -name "*.css" -o -name "*.scss" | head -10`

- [ ] **Step 2: Add styles for the new components**

Add styles for `.scene-suggestion-view`, `.suggestion-card`, `.suggestion-card.greeting`, `.greeting-badge`, `.custom-scene-input`, `.scene-preview-panel`, `.preview-fields`, `.ledger-dialog-backdrop`, `.ledger-dialog`, `.ledger-row`, `.source-badge`.

Follow the existing design patterns (colors, spacing, typography) in the campaign view CSS. The mockup in the spec uses dark theme colors (#1a1a2e background, #c9a0dc accent, #252545 cards) — match these to whatever the app's actual theme variables are.

- [ ] **Step 3: Commit**

```
git add frontend/src/routes/campaign/*.css
git commit -m "style: add CSS for new scene suggestion and ledger components"
```

---

## Task 14: Populate Ledger on Campaign Creation

**Files:**
- Modify: `backend/src/grimoire/api/campaigns/scenes.py` (the seed endpoint)

- [ ] **Step 1: After the initial scene seed, populate the ledger with remaining greetings**

In the `seed_first_scene` endpoint (around line 260, after `_seed_greeting_first_post` is called), add:

```python
# Populate ledger with all greetings from the campaign's worlds
if container.scene_ledger is not None:
    used_greeting_id = greeting.id if greeting else None
    for wref in composition.get("world_refs", []):
        try:
            all_greetings = await library.list_greetings(wref)
        except Exception:
            continue
        for g in all_greetings:
            item_id = await container.scene_ledger.add(
                campaign_id=campaign_id,
                summary=g.name or g.body[:80],
                source="greeting",
                greeting_id=g.id,
                proposed_location=g.starting_location,
            )
            if g.id == used_greeting_id:
                await container.scene_ledger.mark_used(item_id, scene_id=scene.id)
```

You'll need to inject the container dependency. Check if the seed endpoint already has access to it; if not, add `ContainerDep` to the function parameters.

- [ ] **Step 2: Commit**

```
git add backend/src/grimoire/api/campaigns/scenes.py
git commit -m "feat: populate scene ledger with greetings on campaign creation"
```

---

## Task 15: End-to-End Smoke Test

**Files:**
- No new files — manual testing

- [ ] **Step 1: Start the dev server**

Run: `./scripts/run.sh`

- [ ] **Step 2: Create a new campaign through the wizard**

Verify: After campaign creation, check that the scene ledger is populated by calling `GET /api/campaigns/{id}/scene-ledger` in the browser devtools or via curl.

- [ ] **Step 3: End the current scene**

Click "End scene" in the side panel. Verify:
- The suggestion picker appears with ledger items + generated suggestions
- Greeting items show the 🎭 badge
- Free-text input is visible

- [ ] **Step 4: Pick a suggestion and preview**

Click a suggestion card. Verify:
- The preview panel shows title, location, cast, first-post source
- "Back" returns to the picker
- "Start Scene" creates the scene

- [ ] **Step 5: Confirm and verify scene creation**

Click "Start Scene". Verify:
- The play view transitions to the new scene
- A first post is visible (either from greeting or generated)
- The ledger item is marked "used" (check via GET /scene-ledger)

- [ ] **Step 6: Test the manual "New Scene" button**

End the current scene, then instead of picking a suggestion, test:
- The "New Scene" button in the side panel
- The "Refresh" button on the suggestion picker
- Typing custom text and pressing Enter
- Opening the Scene Ledger dialog and dismissing/restoring items

- [ ] **Step 7: Final commit**

```
git add -A
git commit -m "feat: complete new scene workflow implementation"
```
