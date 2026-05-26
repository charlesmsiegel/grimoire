# Import Scene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "Import Scene" button to the campaign timeline that imports grimoire-format `.md`/`.yaml` scene files from disk, runs the full post-processing pipeline (extraction, threads, summary, embedding), and shows progress.

**Architecture:** A backend import pipeline exposed as an SSE-streaming POST endpoint. The pipeline uses `SceneManager.start_scene()` + `append_post()` to create the scene, then runs `ExtractorService` per post, `detect_threads()`, and `generate_summary()`. Frontend: a dialog with `FilePathPicker`, metadata form, and modal progress overlay.

**Tech Stack:** FastAPI `StreamingResponse` (SSE), React, existing grimoire `SceneManager`/`ExtractorService` infrastructure.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `backend/src/grimoire/scenes/importer.py` | Parse import source files, run import pipeline as async generator |
| Create | `backend/src/grimoire/api/campaigns/import_scene.py` | Preview + import SSE endpoints |
| Create | `backend/tests/scenes/test_importer.py` | Unit tests for parser and pipeline |
| Create | `frontend/src/api/campaign/importScene.ts` | API client with SSE stream parsing |
| Create | `frontend/src/routes/campaign/ImportSceneDialog.tsx` | File picker → metadata form → progress overlay |
| Modify | `backend/src/grimoire/api/campaigns/__init__.py` | Register import_scene router |
| Modify | `frontend/src/routes/campaign/TimelineView.tsx` | Add Import button |
| Modify | `frontend/src/index.css` | Import dialog + progress overlay styles |

---

### Task 1: Backend — Import file parser

**Files:**
- Create: `backend/src/grimoire/scenes/importer.py`
- Test: `backend/tests/scenes/test_importer.py`

- [ ] **Step 1: Write failing test for `parse_import_source`**

```python
# backend/tests/scenes/test_importer.py
from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.scenes.importer import ImportParseResult, parse_import_source


def test_parse_import_source_md_only(tmp_path: Path) -> None:
    md = tmp_path / "scene.md"
    md.write_text(
        "## Post 1 — narrator\n\nThe tower looms.\n\n"
        "## Post 2 — pc:alistair\n\nI step inside.\n\n"
        "## Post 3 — npc:gardner\n\nWelcome, my lord.\n",
        encoding="utf-8",
    )
    result = parse_import_source(md)
    assert result.post_count == 3
    assert result.detected_pc_refs == ["alistair"]
    assert result.detected_npc_refs == ["gardner"]
    assert result.sidecar_metadata is None


def test_parse_import_source_with_sidecar(tmp_path: Path) -> None:
    md = tmp_path / "0001-tower.md"
    md.write_text("## Post 1 — narrator\n\nHello.\n", encoding="utf-8")
    yaml = tmp_path / "0001-tower.yaml"
    yaml.write_text(
        "title: The Tower\nlocation_ref: blackspire\nmood: tense\ntags:\n  - night\n",
        encoding="utf-8",
    )
    result = parse_import_source(md)
    assert result.post_count == 1
    assert result.sidecar_metadata is not None
    assert result.sidecar_metadata["title"] == "The Tower"
    assert result.sidecar_metadata["mood"] == "tense"


def test_parse_import_source_bad_format(tmp_path: Path) -> None:
    md = tmp_path / "plain.md"
    md.write_text("Just some prose with no post headings.", encoding="utf-8")
    result = parse_import_source(md)
    assert result.post_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/scenes/test_importer.py -v`
Expected: ImportError — module not found.

- [ ] **Step 3: Implement `parse_import_source`**

```python
# backend/src/grimoire/scenes/importer.py
"""Import scene files from arbitrary disk locations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.files import load_yaml
from grimoire.scenes.storage import PostTuple, parse_body
from grimoire.scenes.types import AuthorKind

logger = logging.getLogger(__name__)


@dataclass
class ImportParseResult:
    post_count: int
    posts: list[PostTuple]
    detected_pc_refs: list[str]
    detected_npc_refs: list[str]
    sidecar_metadata: dict[str, Any] | None = None


def parse_import_source(md_path: Path) -> ImportParseResult:
    """Parse a grimoire-format scene .md and optional .yaml sidecar."""
    text = md_path.read_text(encoding="utf-8")
    posts = parse_body(text, scene_id="__import_preview__")

    pc_refs: list[str] = []
    npc_refs: list[str] = []
    for _order, kind, pc_ref, npc_ref, _body in posts:
        if kind == AuthorKind.PC and pc_ref and pc_ref not in pc_refs:
            pc_refs.append(pc_ref)
        if kind == AuthorKind.NPC and npc_ref and npc_ref not in npc_refs:
            npc_refs.append(npc_ref)

    sidecar_meta: dict[str, Any] | None = None
    yaml_path = md_path.with_suffix(".yaml")
    if yaml_path.is_file():
        raw = load_yaml(yaml_path)
        if isinstance(raw, dict):
            sidecar_meta = {
                k: raw[k]
                for k in (
                    "title", "location_ref", "in_game_start", "in_game_end",
                    "mood", "tags", "present_character_refs", "present_pc_refs",
                )
                if k in raw
            }

    return ImportParseResult(
        post_count=len(posts),
        posts=posts,
        detected_pc_refs=pc_refs,
        detected_npc_refs=npc_refs,
        sidecar_metadata=sidecar_meta,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/scenes/test_importer.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```
git add backend/src/grimoire/scenes/importer.py backend/tests/scenes/test_importer.py
git commit -m "feat(import): add scene file parser with character detection"
```

---

### Task 2: Backend — Import preview endpoint

**Files:**
- Create: `backend/src/grimoire/api/campaigns/import_scene.py`
- Test: `backend/tests/scenes/test_importer.py` (extend)

- [ ] **Step 1: Write failing test for the preview endpoint**

Append to `backend/tests/scenes/test_importer.py`:

```python
from starlette.testclient import TestClient

from grimoire.api.campaigns.import_scene import router as import_router


def _make_test_app():
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(import_router, prefix="/campaigns")
    return app


def test_preview_endpoint(tmp_path: Path) -> None:
    md = tmp_path / "0002-tavern.md"
    md.write_text(
        "## Post 1 — narrator\n\nRain falls.\n\n"
        "## Post 2 — pc:beatrice\n\nI enter.\n",
        encoding="utf-8",
    )
    yaml = tmp_path / "0002-tavern.yaml"
    yaml.write_text("title: Tavern Rain\nmood: melancholy\n", encoding="utf-8")

    client = TestClient(_make_test_app())
    resp = client.post(
        "/campaigns/test-campaign/scenes/import/preview",
        json={"path": str(md)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["post_count"] == 2
    assert data["detected_characters"]["pc_refs"] == ["beatrice"]
    assert data["sidecar"]["title"] == "Tavern Rain"


def test_preview_endpoint_not_found() -> None:
    client = TestClient(_make_test_app())
    resp = client.post(
        "/campaigns/test-campaign/scenes/import/preview",
        json={"path": "/nonexistent/scene.md"},
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/scenes/test_importer.py::test_preview_endpoint -v`
Expected: ImportError — import_scene module not found.

- [ ] **Step 3: Implement preview endpoint**

```python
# backend/src/grimoire/api/campaigns/import_scene.py
"""Scene import endpoints — preview and streaming import."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from grimoire.scenes.importer import parse_import_source

router = APIRouter()


class ImportPreviewRequest(BaseModel):
    path: str


class ImportPreviewResponse(BaseModel):
    post_count: int
    detected_characters: dict[str, list[str]]
    sidecar: dict[str, Any] | None


@router.post("/{campaign_id}/scenes/import/preview")
async def preview_import(
    campaign_id: str,
    body: ImportPreviewRequest,
) -> ImportPreviewResponse:
    md_path = Path(body.path).resolve()
    if not md_path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {body.path}")
    try:
        parsed = parse_import_source(md_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if parsed.post_count == 0:
        raise HTTPException(
            status_code=400,
            detail="No posts found. File must use grimoire format: ## Post N — author",
        )
    return ImportPreviewResponse(
        post_count=parsed.post_count,
        detected_characters={
            "pc_refs": parsed.detected_pc_refs,
            "npc_refs": parsed.detected_npc_refs,
        },
        sidecar=parsed.sidecar_metadata,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/scenes/test_importer.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```
git add backend/src/grimoire/api/campaigns/import_scene.py backend/tests/scenes/test_importer.py
git commit -m "feat(import): add import preview endpoint"
```

---

### Task 3: Backend — Import pipeline

**Files:**
- Modify: `backend/src/grimoire/scenes/importer.py`
- Test: `backend/tests/scenes/test_importer.py` (extend)

This is the core pipeline that creates the scene, appends posts, runs extraction, threads, and summarization. It yields progress events for the SSE stream.

- [ ] **Step 1: Write failing test for `run_import_pipeline`**

Append to `backend/tests/scenes/test_importer.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock

from grimoire.scenes.importer import ImportProgress, run_import_pipeline


@pytest.mark.asyncio
async def test_run_import_pipeline_progress_events(tmp_path: Path) -> None:
    """Verify the pipeline yields the right progress steps."""
    md = tmp_path / "scene.md"
    md.write_text(
        "## Post 1 — narrator\n\nHello.\n\n"
        "## Post 2 — pc:alice\n\nHi.\n",
        encoding="utf-8",
    )
    scene_manager = AsyncMock()
    scene_mock = MagicMock()
    scene_mock.id = "camp:0001-test"
    scene_mock.campaign_id = "camp"
    scene_mock.branch_id = "main"
    scene_manager.start_scene.return_value = scene_mock
    scene_manager.detect_threads.return_value = []
    scene_manager.generate_summary.return_value = ("Summary", ["beat1"])

    events: list[ImportProgress] = []
    async for progress in run_import_pipeline(
        scene_manager=scene_manager,
        extractor=None,
        delta_applier=None,
        md_path=md,
        campaign_id="camp",
        title="Test",
        metadata={},
    ):
        events.append(progress)

    steps = [e.step for e in events]
    assert "copy" in steps
    assert "index" in steps
    assert steps.count("extract") == 2
    assert "threads" in steps
    assert "summarize" in steps
    assert "done" in steps
    assert scene_manager.start_scene.called
    assert scene_manager.append_post.call_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/scenes/test_importer.py::test_run_import_pipeline_progress_events -v`
Expected: ImportError — `ImportProgress` and `run_import_pipeline` not found.

- [ ] **Step 3: Implement `run_import_pipeline`**

Add to `backend/src/grimoire/scenes/importer.py`:

```python
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from grimoire.scenes.types import AuthorKind, Post, SceneInit


@dataclass
class ImportProgress:
    step: str      # "import", "extract", "threads", "summarize", "embed", "done"
    current: int
    total: int
    detail: str


async def run_import_pipeline(
    *,
    scene_manager: Any,
    extractor: Any | None,
    delta_applier: Any | None,
    md_path: Path,
    campaign_id: str,
    title: str,
    metadata: dict[str, Any],
) -> AsyncIterator[ImportProgress]:
    """Run the full import pipeline, yielding progress events.

    ``metadata`` may contain: location_ref, in_game_start, in_game_end, mood,
    tags, present_character_refs, present_pc_refs.
    """
    parsed = parse_import_source(md_path)
    n_posts = parsed.post_count
    # N posts + 6 non-extraction steps (copy, index, threads, summarize, embed, done)
    total = n_posts + 6

    # --- Step 1: copy (parse + create scene) ---
    from grimoire.scenes.storage import slugify

    in_game_start = metadata.get("in_game_start")
    if isinstance(in_game_start, str):
        try:
            in_game_start = datetime.fromisoformat(in_game_start)
        except ValueError:
            in_game_start = None

    init = SceneInit(
        campaign_id=campaign_id,
        branch_id="main",
        title=title,
        slug=slugify(title),
        location_ref=metadata.get("location_ref"),
        in_game_start=in_game_start,
        present_character_refs=metadata.get("present_character_refs", []),
        present_pc_refs=metadata.get("present_pc_refs", []),
        mood=metadata.get("mood"),
        tags=metadata.get("tags", []),
    )
    scene = await scene_manager.start_scene(init)

    tick = 1
    yield ImportProgress(step="copy", current=tick, total=total, detail="Scene created")

    # --- Step 2: index (append posts — triggers scene indexer) ---
    now = datetime.now(UTC)
    posts: list[Post] = []
    for order, kind, pc_ref, npc_ref, body in parsed.posts:
        post = Post(
            id=uuid.uuid4().hex,
            scene_id=scene.id,
            order_in_scene=order,
            author_kind=kind,
            body=body,
            is_player=(kind == AuthorKind.PC),
            created_at=now,
            turn_id=uuid.uuid4().hex,
            author_pc_ref=pc_ref,
            author_npc_ref=npc_ref,
        )
        await scene_manager.append_post(scene.id, post)
        posts.append(post)

    tick += 1
    yield ImportProgress(step="index", current=tick, total=total, detail=f"Indexed {n_posts} posts")

    # --- Step 2: extraction per post ---
    for i, post in enumerate(posts):
        tick += 1
        if extractor is not None:
            try:
                result = await extractor.extract_from_user_text(
                    user_text=post.body,
                    scene=scene,
                    campaign_id=campaign_id,
                    player_pc_ref=post.author_pc_ref,
                    turn_id=post.turn_id,
                )
                if delta_applier is not None and result and result.deltas:
                    try:
                        await delta_applier.apply_routing(
                            campaign_id=campaign_id,
                            branch_id="main",
                            turn_id=post.turn_id,
                            extraction=result,
                        )
                    except Exception:
                        logger.warning("import: delta routing failed for post %d", i + 1, exc_info=True)
            except Exception:
                logger.warning("import: extraction failed for post %d", i + 1, exc_info=True)
        yield ImportProgress(step="extract", current=tick, total=total, detail=f"Extracted post {i + 1}/{n_posts}")

    # --- Step 3: thread detection ---
    tick += 1
    try:
        threads = await scene_manager.detect_threads(scene.id)
        for thread, kind in threads:
            await scene_manager.add_thread(scene.id, thread, kind)
    except Exception:
        logger.warning("import: thread detection failed", exc_info=True)
    yield ImportProgress(step="threads", current=tick, total=total, detail="Thread detection complete")

    # --- Step 4: summarization ---
    tick += 1
    try:
        await scene_manager.generate_summary(scene.id, force=True)
    except Exception:
        logger.warning("import: summarization failed", exc_info=True)
    yield ImportProgress(step="summarize", current=tick, total=total, detail="Summary generated")

    # --- Step 5: embedding (file watcher picks up the .md) ---
    tick += 1
    yield ImportProgress(step="embed", current=tick, total=total, detail="Embedding enqueued")

    # --- Done ---
    tick += 1
    yield ImportProgress(step="done", current=tick, total=total, detail=scene.id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/scenes/test_importer.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```
git add backend/src/grimoire/scenes/importer.py backend/tests/scenes/test_importer.py
git commit -m "feat(import): add import pipeline with progress events"
```

---

### Task 4: Backend — Import SSE endpoint + router registration

**Files:**
- Modify: `backend/src/grimoire/api/campaigns/import_scene.py`
- Modify: `backend/src/grimoire/api/campaigns/__init__.py`

- [ ] **Step 1: Add the import endpoint to `import_scene.py`**

Append to `backend/src/grimoire/api/campaigns/import_scene.py`:

```python
import json
from dataclasses import asdict

from starlette.responses import StreamingResponse

from grimoire.api.deps import ContainerDep, ScenesDep
from grimoire.scenes.importer import ImportProgress, run_import_pipeline


class ImportRequest(BaseModel):
    path: str
    title: str
    location_ref: str | None = None
    in_game_start: str | None = None
    in_game_end: str | None = None
    mood: str | None = None
    tags: list[str] = []
    present_character_refs: list[str] = []
    present_pc_refs: list[str] = []


@router.post("/{campaign_id}/scenes/import")
async def import_scene(
    campaign_id: str,
    body: ImportRequest,
    scenes: ScenesDep,
    container: ContainerDep,
) -> StreamingResponse:
    md_path = Path(body.path).resolve()
    if not md_path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {body.path}")

    extractor = getattr(container, "extractor", None)

    # Build a DeltaApplier if we have the required services.
    delta_applier = None
    if extractor is not None:
        try:
            from grimoire.extractor.config import ExtractorConfig
            from grimoire.orchestrator.config import OrchestratorConfig
            from grimoire.orchestrator.delta_applier import DeltaApplier

            delta_applier = DeltaApplier(
                state_store=container.state_store,
                continuity=container.continuity,
                extractor=extractor,
                world=getattr(container, "world", None),
                event_bus=container.event_bus,
                gateway=container.llm_gateway,
                extractor_config=ExtractorConfig(),
                config=OrchestratorConfig(),
                auto_disable=None,
            )
        except Exception:
            pass  # proceed without delta routing

    metadata = body.model_dump(exclude={"path", "title"})

    async def event_stream():
        scene_id = ""
        try:
            async for progress in run_import_pipeline(
                scene_manager=scenes,
                extractor=extractor,
                delta_applier=delta_applier,
                md_path=md_path,
                campaign_id=campaign_id,
                title=body.title,
                metadata=metadata,
            ):
                yield f"event: progress\ndata: {json.dumps(asdict(progress))}\n\n"
                if progress.step == "done":
                    scene_id = progress.detail
            yield f"event: result\ndata: {json.dumps({'scene_id': scene_id})}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

- [ ] **Step 2: Register the router in campaigns `__init__.py`**

In `backend/src/grimoire/api/campaigns/__init__.py`, add the import:

```python
from grimoire.api.campaigns.import_scene import router as import_scene_router
```

And add the include line alongside the other sub-routers:

```python
router.include_router(import_scene_router, prefix=_PREFIX, tags=_TAGS)
```

- [ ] **Step 3: Verify the server starts**

Run: `python -m grimoire` (or the project's dev server command)
Expected: no import errors, server starts.

- [ ] **Step 4: Commit**

```
git add backend/src/grimoire/api/campaigns/import_scene.py backend/src/grimoire/api/campaigns/__init__.py
git commit -m "feat(import): add SSE import endpoint and register router"
```

---

### Task 5: Frontend — Import API client with SSE stream parsing

**Files:**
- Create: `frontend/src/api/campaign/importScene.ts`

- [ ] **Step 1: Create the API client**

```typescript
// frontend/src/api/campaign/importScene.ts
import { api, ApiError } from "../client";

function enc(s: string): string {
  return encodeURIComponent(s);
}

export interface ImportPreviewResponse {
  post_count: number;
  detected_characters: {
    pc_refs: string[];
    npc_refs: string[];
  };
  sidecar: Record<string, unknown> | null;
}

export interface ImportRequest {
  path: string;
  title: string;
  location_ref?: string | null;
  in_game_start?: string | null;
  mood?: string | null;
  tags?: string[];
  present_character_refs?: string[];
  present_pc_refs?: string[];
}

export interface ImportProgress {
  step: string;
  current: number;
  total: number;
  detail: string;
}

export const importSceneApi = {
  preview: (campaignId: string, path: string) =>
    api.post<ImportPreviewResponse>(
      `/api/campaigns/${enc(campaignId)}/scenes/import/preview`,
      { path },
    ),

  import: async (
    campaignId: string,
    body: ImportRequest,
    onProgress: (p: ImportProgress) => void,
  ): Promise<string> => {
    const res = await fetch(`/api/campaigns/${enc(campaignId)}/scenes/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new ApiError(res.status, detail.detail ?? "Import failed");
    }
    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let sceneId = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop()!;
      for (const part of parts) {
        const eventMatch = part.match(/^event:\s*(\w+)\ndata:\s*(.+)$/s);
        if (!eventMatch) continue;
        const [, type, data] = eventMatch;
        if (type === "progress") {
          onProgress(JSON.parse(data));
        }
        if (type === "result") {
          sceneId = JSON.parse(data).scene_id;
        }
        if (type === "error") {
          const err = JSON.parse(data);
          throw new ApiError(500, err.detail ?? "Import pipeline error");
        }
      }
    }
    if (!sceneId) throw new Error("Import ended without result");
    return sceneId;
  },
};
```

- [ ] **Step 2: Commit**

```
git add frontend/src/api/campaign/importScene.ts
git commit -m "feat(import): add frontend import API client with SSE parsing"
```

---

### Task 6: Frontend — ImportSceneDialog component

**Files:**
- Create: `frontend/src/routes/campaign/ImportSceneDialog.tsx`

This component has three phases: file selection, metadata form, and progress overlay.

- [ ] **Step 1: Create the dialog component**

```tsx
// frontend/src/routes/campaign/ImportSceneDialog.tsx
import { useCallback, useState } from "react";

import {
  type ImportPreviewResponse,
  type ImportProgress,
  importSceneApi,
} from "../../api/campaign/importScene";
import { FilePathPicker } from "../../components/FilePathPicker";

type Phase = "pick" | "metadata" | "importing" | "done" | "error";

interface Props {
  campaignId: string;
  onClose: () => void;
  onImported: () => void;
}

export function ImportSceneDialog({ campaignId, onClose, onImported }: Props) {
  const [phase, setPhase] = useState<Phase>("pick");
  const [filePath, setFilePath] = useState("");
  const [preview, setPreview] = useState<ImportPreviewResponse | null>(null);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Metadata form state
  const [title, setTitle] = useState("");
  const [locationRef, setLocationRef] = useState("");
  const [inGameStart, setInGameStart] = useState("");
  const [mood, setMood] = useState("");
  const [tags, setTags] = useState("");
  const [pcRefs, setPcRefs] = useState("");
  const [npcRefs, setNpcRefs] = useState("");

  // Progress state
  const [progress, setProgress] = useState<ImportProgress | null>(null);
  const [importErr, setImportErr] = useState<string | null>(null);

  const handleFileSelected = useCallback(
    async (path: string) => {
      setFilePath(path);
      if (!path) return;
      setLoading(true);
      setPreviewErr(null);
      try {
        const res = await importSceneApi.preview(campaignId, path);
        setPreview(res);
        // Pre-fill from sidecar
        const s = res.sidecar;
        if (s) {
          if (typeof s.title === "string") setTitle(s.title);
          if (typeof s.location_ref === "string") setLocationRef(s.location_ref);
          if (typeof s.in_game_start === "string") setInGameStart(s.in_game_start);
          if (typeof s.mood === "string") setMood(s.mood);
          if (Array.isArray(s.tags)) setTags((s.tags as string[]).join(", "));
          if (Array.isArray(s.present_pc_refs))
            setPcRefs((s.present_pc_refs as string[]).join(", "));
          if (Array.isArray(s.present_character_refs)) {
            const all = s.present_character_refs as string[];
            const pcs = new Set(
              Array.isArray(s.present_pc_refs) ? (s.present_pc_refs as string[]) : [],
            );
            setNpcRefs(all.filter((r) => !pcs.has(r)).join(", "));
          }
        }
        // Auto-fill detected characters if sidecar didn't have them
        if (!s?.present_pc_refs && res.detected_characters.pc_refs.length) {
          setPcRefs(res.detected_characters.pc_refs.join(", "));
        }
        if (!s?.present_character_refs && res.detected_characters.npc_refs.length) {
          setNpcRefs(res.detected_characters.npc_refs.join(", "));
        }
        // Default title from filename
        if (!s?.title) {
          const stem = path.replace(/\\/g, "/").split("/").pop()?.replace(/\.md$/, "") ?? "";
          const cleaned = stem.replace(/^\d+-/, "").replace(/[-_]/g, " ");
          setTitle(cleaned || "Imported Scene");
        }
        setPhase("metadata");
      } catch (err) {
        setPreviewErr(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [campaignId],
  );

  const splitRefs = (s: string) =>
    s
      .split(",")
      .map((r) => r.trim())
      .filter(Boolean);

  const handleImport = useCallback(async () => {
    setPhase("importing");
    setImportErr(null);
    const allPcRefs = splitRefs(pcRefs);
    const allNpcRefs = splitRefs(npcRefs);
    try {
      await importSceneApi.import(
        campaignId,
        {
          path: filePath,
          title,
          location_ref: locationRef || null,
          in_game_start: inGameStart || null,
          mood: mood || null,
          tags: splitRefs(tags),
          present_character_refs: [...allPcRefs, ...allNpcRefs],
          present_pc_refs: allPcRefs,
        },
        (p) => setProgress(p),
      );
      setPhase("done");
      onImported();
    } catch (err) {
      setImportErr(err instanceof Error ? err.message : String(err));
      setPhase("error");
    }
  }, [campaignId, filePath, title, locationRef, inGameStart, mood, tags, pcRefs, npcRefs, onImported]);

  // --- Phase: importing (modal overlay blocks everything) ---
  if (phase === "importing" || phase === "done" || phase === "error") {
    const pct = progress ? Math.round((progress.current / progress.total) * 100) : 0;
    return (
      <div className="import-overlay">
        <div className="import-progress-card">
          <h3>{phase === "done" ? "Import Complete" : phase === "error" ? "Import Failed" : `Importing: ${title}`}</h3>
          <div className="import-progress-bar-track">
            <div className="import-progress-bar-fill" style={{ width: `${pct}%` }} />
          </div>
          <p className="import-progress-detail">
            {phase === "error" ? importErr : (progress?.detail ?? "Starting…")}
          </p>
          {progress && phase === "importing" && (
            <p className="import-progress-count">{progress.current} / {progress.total}</p>
          )}
          {(phase === "done" || phase === "error") && (
            <button type="button" className="primary" onClick={onClose}>
              Close
            </button>
          )}
        </div>
      </div>
    );
  }

  // --- Phase: metadata form ---
  if (phase === "metadata" && preview) {
    return (
      <div className="import-overlay">
        <div className="import-dialog">
          <h3>Import Scene</h3>
          <p className="import-post-count">{preview.post_count} posts detected</p>
          <label>
            <span>Title <em>*</em></span>
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label>
            <span>Location</span>
            <input type="text" value={locationRef} onChange={(e) => setLocationRef(e.target.value)} placeholder="e.g. blackspire-tower" />
          </label>
          <label>
            <span>In-game start</span>
            <input type="text" value={inGameStart} onChange={(e) => setInGameStart(e.target.value)} placeholder="e.g. 1247-10-31T22:00:00" />
          </label>
          <label>
            <span>Mood</span>
            <input type="text" value={mood} onChange={(e) => setMood(e.target.value)} placeholder="e.g. tense" />
          </label>
          <label>
            <span>Tags</span>
            <input type="text" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="comma-separated" />
          </label>
          <label>
            <span>PC characters</span>
            <input type="text" value={pcRefs} onChange={(e) => setPcRefs(e.target.value)} placeholder="comma-separated" />
          </label>
          <label>
            <span>NPC characters</span>
            <input type="text" value={npcRefs} onChange={(e) => setNpcRefs(e.target.value)} placeholder="comma-separated" />
          </label>
          <div className="import-form-actions">
            <button type="button" onClick={() => { setPhase("pick"); setPreview(null); }}>
              Back
            </button>
            <button type="button" className="primary" onClick={handleImport} disabled={!title.trim()}>
              Import
            </button>
          </div>
        </div>
      </div>
    );
  }

  // --- Phase: file picker ---
  return (
    <div className="import-overlay">
      <div className="import-dialog">
        <div className="import-dialog-header">
          <h3>Import Scene</h3>
          <button type="button" onClick={onClose}>&times;</button>
        </div>
        <p>Select a grimoire-format scene file (.md) to import.</p>
        <FilePathPicker
          label="Scene file"
          description="Path to a .md scene file"
          required
          value={filePath}
          glob="*.md"
          onChange={setFilePath}
        />
        {previewErr && <p className="import-error">{previewErr}</p>}
        <div className="import-form-actions">
          <button type="button" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="primary"
            disabled={!filePath || loading}
            onClick={() => handleFileSelected(filePath)}
          >
            {loading ? "Parsing…" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```
git add frontend/src/routes/campaign/ImportSceneDialog.tsx
git commit -m "feat(import): add ImportSceneDialog component"
```

---

### Task 7: Frontend — TimelineView integration + CSS

**Files:**
- Modify: `frontend/src/routes/campaign/TimelineView.tsx`
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Add import button to TimelineView**

In `frontend/src/routes/campaign/TimelineView.tsx`:

Add the import and state:

```tsx
import { ImportSceneDialog } from "./ImportSceneDialog";
```

Add state inside the component:

```tsx
const [showImport, setShowImport] = useState(false);
```

Add the button to the toolbar (next to the existing search/filter controls) and the dialog:

```tsx
<button type="button" className="import-scene-btn" onClick={() => setShowImport(true)}>
  Import Scene
</button>
```

And render the dialog when open:

```tsx
{showImport && (
  <ImportSceneDialog
    campaignId={campaignId}
    onClose={() => setShowImport(false)}
    onImported={() => { setShowImport(false); state.reload(); }}
  />
)}
```

- [ ] **Step 2: Add CSS for import dialog and progress overlay**

Append to `frontend/src/index.css`:

```css
/* ---- Import scene dialog ---- */

.import-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  z-index: 1100;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: all;
}

.import-dialog {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  width: min(500px, 90vw);
  max-height: 80vh;
  overflow-y: auto;
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  box-shadow: var(--shadow-lg, 0 4px 24px rgba(0, 0, 0, 0.25));
}

.import-dialog-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.import-dialog-header button {
  background: none;
  border: none;
  font-size: 1.2rem;
  cursor: pointer;
  color: var(--fg-muted);
}

.import-post-count {
  font-size: 0.85rem;
  color: var(--fg-muted);
}

.import-form-actions {
  display: flex;
  gap: var(--space-2);
  justify-content: flex-end;
  padding-top: var(--space-2);
}

.import-error {
  color: var(--danger, #d9534f);
  font-size: 0.85rem;
}

.import-scene-btn {
  padding: var(--space-1) var(--space-3);
  background: var(--bg-elev);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  color: var(--fg);
  cursor: pointer;
  font-size: 0.85rem;
}

.import-scene-btn:hover {
  background: var(--bg-hover, var(--bg-elev));
  border-color: var(--border);
}

/* ---- Import progress overlay ---- */

.import-progress-card {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  width: min(400px, 85vw);
  padding: var(--space-5);
  text-align: center;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  box-shadow: var(--shadow-lg, 0 4px 24px rgba(0, 0, 0, 0.25));
}

.import-progress-bar-track {
  width: 100%;
  height: 8px;
  background: var(--border-subtle);
  border-radius: 4px;
  overflow: hidden;
}

.import-progress-bar-fill {
  height: 100%;
  background: var(--accent, #4a9eff);
  border-radius: 4px;
  transition: width 0.3s ease;
}

.import-progress-detail {
  font-size: 0.85rem;
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.import-progress-count {
  font-size: 0.8rem;
  color: var(--fg-muted);
}
```

- [ ] **Step 3: Verify in browser**

Run the dev server. Navigate to a campaign's timeline. Confirm:
1. "Import Scene" button appears in the toolbar
2. Clicking opens the file picker dialog
3. Selecting a .md file shows the metadata form
4. Import runs with progress bar
5. Progress overlay blocks all interaction
6. Timeline refreshes on completion

- [ ] **Step 4: Commit**

```
git add frontend/src/routes/campaign/TimelineView.tsx frontend/src/index.css frontend/src/routes/campaign/ImportSceneDialog.tsx
git commit -m "feat(import): wire import button into timeline with progress overlay"
```

---

### Task 8: Create GitHub issue for bare prose import

- [ ] **Step 1: Create the issue**

```bash
gh issue create \
  --title "feat: import unstructured prose as scenes" \
  --body "## Summary

Add support for importing plain markdown or text that doesn't use grimoire's \`## Post N — author\` format. The importer should use heuristics or an LLM to detect post boundaries, identify speakers, and split the text into structured posts.

## Context

The current import feature (added in this branch) requires grimoire-format files. This enhancement would accept:
- Plain prose (treat as single narrator post)
- Chat logs (detect speaker patterns like \`Name:\` or \`**Name**:\`)
- Forum RP posts (detect post boundaries)

## Acceptance criteria

- [ ] Accept \`.md\` and \`.txt\` files without post headings
- [ ] Detect speaker boundaries using heuristics first, LLM fallback
- [ ] Preview parsed posts before import so user can correct splits
- [ ] Reuse the existing import pipeline (extraction, threads, summary) after parsing"
```

- [ ] **Step 2: Commit** (no code changes — issue only)
