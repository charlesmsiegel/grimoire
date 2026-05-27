# PC-Absent Scenes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support NPC-only scenes where no player character is present, with director/auto-narrate interaction modes, different prompt instructions, and a frontend UI that adapts to the scene's PC presence.

**Architecture:** Derive `pc_absent` from the existing `present_pc_refs` field on Scene (empty = no PCs). This drives three layers of change: (1) context assembly generates different system prompt instructions and skips the Active PC card, (2) the orchestrator gains a `submit_direction()` method with a new `/turns/direct` API endpoint, (3) the frontend InputArea switches to director mode when no PCs are present.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, Jinja2 templates, TypeScript, React 18

---

### Task 1: Scene.pc_absent property + BuiltContext fields

**Files:**
- Modify: `backend/src/grimoire/scenes/types.py:80-97` (Scene dataclass)
- Modify: `backend/src/grimoire/context/types.py:53-70` (BuiltContext dataclass)
- Test: `backend/tests/scenes/test_types.py` (create)
- Test: `backend/tests/context/test_builder.py:159-176` (update _Scene stub)

- [ ] **Step 1: Write failing tests for Scene.pc_absent**

Create `backend/tests/scenes/test_types.py`:

```python
"""Tests for Scene type additions."""

from grimoire.scenes.types import Scene


def test_pc_absent_true_when_no_pcs():
    scene = Scene(
        id="s1",
        campaign_id="c1",
        branch_id="main",
        ordinal=1,
        slug="test",
        title="Test",
        present_character_refs=["npc-a", "npc-b"],
        present_pc_refs=[],
    )
    assert scene.pc_absent is True


def test_pc_absent_false_when_pcs_present():
    scene = Scene(
        id="s1",
        campaign_id="c1",
        branch_id="main",
        ordinal=1,
        slug="test",
        title="Test",
        present_character_refs=["pc-alice", "npc-b"],
        present_pc_refs=["pc-alice"],
    )
    assert scene.pc_absent is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/scenes/test_types.py -v`
Expected: FAIL — `AttributeError: 'Scene' object has no attribute 'pc_absent'`

- [ ] **Step 3: Add pc_absent property to Scene**

In `backend/src/grimoire/scenes/types.py`, add after line 117 (after `narrator_response_mode`):

```python
@property
def pc_absent(self) -> bool:
    return len(self.present_pc_refs) == 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/scenes/test_types.py -v`
Expected: PASS

- [ ] **Step 5: Add pc_absent and scene_mode to BuiltContext**

In `backend/src/grimoire/context/types.py`, add two fields to `BuiltContext` after `extra`:

```python
pc_absent: bool = False
scene_mode: str = ""
```

- [ ] **Step 6: Add present_pc_refs to the test stub _Scene**

In `backend/tests/context/test_builder.py`, update the `_Scene` dataclass (around line 166) to add:

```python
present_pc_refs: list[str] = field(default_factory=list)
```

- [ ] **Step 7: Run full test suite to confirm nothing breaks**

Run: `cd backend && uv run pytest tests/scenes/ tests/context/test_builder.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```
git add backend/src/grimoire/scenes/types.py backend/src/grimoire/context/types.py backend/tests/scenes/test_types.py backend/tests/context/test_builder.py
git commit -m "feat: add Scene.pc_absent property and BuiltContext.pc_absent/scene_mode fields"
```

---

### Task 2: Context builder — scene mode text and PC card gating

**Files:**
- Modify: `backend/src/grimoire/context/builder.py:290-467` (_build_context method)
- Test: `backend/tests/context/test_builder.py`

- [ ] **Step 1: Write failing test — pc_absent scene skips Active PC card**

Add to `backend/tests/context/test_builder.py`:

```python
async def test_pc_absent_scene_skips_active_pc_card() -> None:
    chars = StubCharacters(
        cards={"library:worlds/wod/characters/alistair": _Card(full="# Alistair\nElder Tremere.")},
        active="library:worlds/wod/characters/alistair",
    )
    scene = _Scene(
        present_character_refs=["npc-winifred"],
        present_pc_refs=[],
    )
    scenes = StubScenes(scene=scene)
    builder = _builder(characters=chars, scenes=scenes)
    prompt = await builder.build("scene begins", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Elder Tremere" not in body
    assert not any(
        s.tier == ContextTier.LOCK_IN and s.kind == "character" for s in prompt.sources
    )
```

- [ ] **Step 2: Write failing test — pc_absent scene includes scene_mode instruction**

Add to `backend/tests/context/test_builder.py`:

```python
async def test_pc_absent_scene_includes_director_instruction() -> None:
    scene = _Scene(
        present_character_refs=["npc-winifred"],
        present_pc_refs=[],
    )
    scenes = StubScenes(scene=scene)
    builder = _builder(scenes=scenes)
    prompt = await builder.build("the NPCs argue", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "NPC-only scene" in body


async def test_pc_present_scene_includes_agency_instruction() -> None:
    scene = _Scene(
        present_character_refs=["pc-alistair", "npc-winifred"],
        present_pc_refs=["pc-alistair"],
    )
    scenes = StubScenes(scene=scene)
    builder = _builder(scenes=scenes)
    prompt = await builder.build("I bow", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Never write the player character" in body
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/context/test_builder.py::test_pc_absent_scene_skips_active_pc_card tests/context/test_builder.py::test_pc_absent_scene_includes_director_instruction tests/context/test_builder.py::test_pc_present_scene_includes_agency_instruction -v`
Expected: FAIL

- [ ] **Step 4: Implement pc_absent logic in _build_context**

In `backend/src/grimoire/context/builder.py`, modify `_build_context()`:

After the scene state resolution (around line 313 where `scene_header` is set), add:

```python
pc_absent = scene is not None and getattr(scene, "pc_absent", len(getattr(scene, "present_pc_refs", []) or []) == 0)
```

Then modify the cast resolution block (around lines 317-324). Replace:

```python
if pc_ref is None:
    active_pc_ref = await self._safe_call(self._characters.active_pc, campaign_id)
else:
    active_pc_ref = pc_ref
active_pc_card, active_pc_source = await self._cast.active_pc_card(
    active_pc_ref, campaign_id
)
active_pc_name = await self._cast.active_pc_name(active_pc_ref, campaign_id)
```

With:

```python
if pc_absent:
    active_pc_ref = None
    active_pc_card = ""
    active_pc_source = None
    active_pc_name = ""
else:
    if pc_ref is None:
        active_pc_ref = await self._safe_call(self._characters.active_pc, campaign_id)
    else:
        active_pc_ref = pc_ref
    active_pc_card, active_pc_source = await self._cast.active_pc_card(
        active_pc_ref, campaign_id
    )
    active_pc_name = await self._cast.active_pc_name(active_pc_ref, campaign_id)
```

Build the scene_mode text. Add this right after the `pc_absent` derivation:

```python
if pc_absent:
    scene_mode = (
        "This is an NPC-only scene. The player is directing the scene but has "
        "no character present. Write all characters freely — there are no PC "
        "agency restrictions. The player's input is scene direction, not "
        "character dialogue."
    )
else:
    scene_mode = (
        "You are narrating a scene where the player acts through their character. "
        "Never write the player character's dialogue, actions, or internal thoughts. "
        "Stop at decision points and wait for the player."
    )
```

Update the `BuiltContext` construction (around line 450) to include the new fields:

```python
pc_absent=pc_absent,
scene_mode=scene_mode,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/context/test_builder.py -v`
Expected: All PASS

- [ ] **Step 6: Write test for absent-PC background cards**

Add to `backend/tests/context/test_builder.py`:

```python
async def test_pc_absent_scene_adds_absent_pc_background_cards() -> None:
    chars = StubCharacters(
        cards={
            "pc-alistair": _Card(full="# Alistair\nFull", compressed="Alistair (compressed)"),
            "npc-winifred": _Card(full="# winifred\nFull"),
        },
        active="pc-alistair",
    )
    scene = _Scene(
        present_character_refs=["npc-winifred"],
        present_pc_refs=[],
    )
    scenes = StubScenes(scene=scene)
    continuity = StubContinuity()
    continuity._pc_refs = {"pc-alistair"}
    builder = _builder(characters=chars, scenes=scenes, continuity=continuity)
    prompt = await builder.build("the NPCs talk", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Alistair (compressed)" in body
```

Note: `StubContinuity` needs a `_pc_refs` attribute and `pc_refs()` must return it. Check whether `StubContinuity` already has `pc_refs()` — if not, add:

```python
async def pc_refs(self, campaign_id: str | None = None) -> set[str]:
    return getattr(self, "_pc_refs", set())
```

- [ ] **Step 7: Implement absent-PC background cards in _build_context**

In `backend/src/grimoire/context/builder.py`, inside `_build_context()`, after the cast resolution block and when `pc_absent` is true, add:

```python
if pc_absent:
    all_pc_refs = pc_refs  # already fetched above for commitments
    present_refs = set(getattr(scene, "present_pc_refs", []) or [])
    absent_pc_refs = all_pc_refs - present_refs
    for ref in sorted(absent_pc_refs):
        compressed = await self._cast._try_compressed_card(ref, campaign_id)
        if compressed:
            background_items.append(
                TierItem(
                    tier=ContextTier.BACKGROUND,
                    section="absent_pc",
                    text=compressed,
                    priority=4,
                    source=ContextSource(
                        kind="character",
                        scope="campaign-local",
                        owner_id=campaign_id,
                        tier=ContextTier.BACKGROUND,
                        summary=f"absent-pc:{ref}",
                    ),
                )
            )
```

- [ ] **Step 8: Run tests to verify absent-PC cards work**

Run: `cd backend && uv run pytest tests/context/test_builder.py::test_pc_absent_scene_adds_absent_pc_background_cards -v`
Expected: PASS

- [ ] **Step 9: Commit**

```
git add backend/src/grimoire/context/builder.py backend/tests/context/test_builder.py
git commit -m "feat: context builder gates PC card, generates scene_mode, and adds absent-PC background cards"
```

---

### Task 3: System block template — scene_mode injection

**Files:**
- Modify: `backend/src/grimoire/templates/context_system_block/default.j2`
- Modify: `backend/src/grimoire/context/assembler.py:167-174` (_system_block method)

- [ ] **Step 1: Write failing test for scene_mode in system block**

Add to `backend/tests/context/test_builder.py`:

```python
async def test_scene_mode_appears_in_system_block() -> None:
    scene = _Scene(
        present_character_refs=["npc-winifred"],
        present_pc_refs=[],
    )
    scenes = StubScenes(scene=scene)
    builder = _builder(scenes=scenes)
    prompt = await builder.build("direct", "camp")
    system_msgs = [m for m in prompt.messages if m.role == "system"]
    assert any("NPC-only scene" in m.content for m in system_msgs)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/context/test_builder.py::test_scene_mode_appears_in_system_block -v`
Expected: FAIL (the template doesn't render scene_mode yet)

- [ ] **Step 3: Pass scene_mode through the assembler to the template**

In `backend/src/grimoire/context/assembler.py`, modify `_system_block()` (line 167):

```python
async def _system_block(self, ctx: BuiltContext) -> str:
    return render_template(
        "context_system_block",
        style_text=ctx.style_text,
        content_boundaries=ctx.content_boundaries,
        system_meta=ctx.system_meta,
        voice_corrective=ctx.voice_corrective,
        scene_mode=ctx.scene_mode,
    ).strip()
```

- [ ] **Step 4: Update the system block template**

In `backend/src/grimoire/templates/context_system_block/default.j2`, add `scene_mode` to the input docs and append to blocks:

```jinja2
{#-
  Top-of-prompt system block. Inputs:
    style_text          str
    content_boundaries  str
    system_meta         str  (rendered as-is, no header)
    voice_corrective    str
    scene_mode          str
-#}
{%- set blocks = [] -%}
{%- if scene_mode %}{% set _ = blocks.append("# Scene mode\n" ~ scene_mode) %}{% endif -%}
{%- if style_text %}{% set _ = blocks.append("# Style\n" ~ style_text) %}{% endif -%}
{%- if content_boundaries %}{% set _ = blocks.append("# Content boundaries\n" ~ content_boundaries) %}{% endif -%}
{%- if system_meta %}{% set _ = blocks.append(system_meta) %}{% endif -%}
{%- if voice_corrective %}{% set _ = blocks.append("# Voice corrective\n" ~ voice_corrective) %}{% endif -%}
{{ blocks | join("\n\n") }}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/context/test_builder.py::test_scene_mode_appears_in_system_block -v`
Expected: PASS

- [ ] **Step 6: Run full context test suite**

Run: `cd backend && uv run pytest tests/context/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```
git add backend/src/grimoire/context/assembler.py backend/src/grimoire/templates/context_system_block/default.j2
git commit -m "feat: inject scene_mode instruction into system block template"
```

---

### Task 4: Scene header — NPC-only label

**Files:**
- Modify: `backend/src/grimoire/context/cast.py:35-49` (render_scene_header)

- [ ] **Step 1: Write failing test**

Add to `backend/tests/context/test_builder.py`:

```python
async def test_scene_header_labels_npc_only() -> None:
    scene = _Scene(
        title="Secret Meeting",
        present_character_refs=["npc-winifred", "npc-drake"],
        present_pc_refs=[],
    )
    scenes = StubScenes(scene=scene)
    builder = _builder(scenes=scenes)
    prompt = await builder.build("NPCs talk", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "(NPC-only)" in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/context/test_builder.py::test_scene_header_labels_npc_only -v`
Expected: FAIL

- [ ] **Step 3: Implement NPC-only label in render_scene_header**

In `backend/src/grimoire/context/cast.py`, modify `render_scene_header()` (line 35):

```python
def render_scene_header(self, scene: Any) -> str:
    if scene is None:
        return "No active scene."
    title = getattr(scene, "title", None) or getattr(scene, "slug", "")
    pc_absent = len(getattr(scene, "present_pc_refs", []) or []) == 0
    label = f"{title} (NPC-only)" if pc_absent else title
    lines = [f"Scene: {label}"]
    if getattr(scene, "location_ref", None):
        lines.append(f"Location: {scene.location_ref}")
    igt = getattr(scene, "in_game_start", None)
    if igt is not None:
        lines.append(f"In-game start: {igt}")
    if getattr(scene, "mood", None):
        lines.append(f"Mood: {scene.mood}")
    present = list(getattr(scene, "present_character_refs", []) or [])
    if present:
        lines.append("Present cast: " + ", ".join(present))
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/context/test_builder.py::test_scene_header_labels_npc_only tests/context/ -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```
git add backend/src/grimoire/context/cast.py
git commit -m "feat: add (NPC-only) label to scene header when no PCs present"
```

---

### Task 5: Orchestrator — submit_direction method

**Files:**
- Modify: `backend/src/grimoire/orchestrator/service.py:292-352` (add submit_direction)
- Modify: `backend/src/grimoire/orchestrator/service.py:1042-1052` (_maybe_break_scene)
- Test: `backend/tests/orchestrator/test_service.py`

- [ ] **Step 1: Write failing test for submit_direction**

Add to `backend/tests/orchestrator/test_service.py`:

```python
async def _seed_pc_absent(
    scene_manager: SceneManager,
    fake_store: FakeStateStore,
    *,
    campaign_id: str = "c1",
):
    fake_store.db.campaigns.add(campaign_id)
    fake_store.db.pcs[campaign_id] = set()
    scene = await scene_manager.start_scene(
        SceneInit(
            campaign_id=campaign_id,
            title="NPC Meeting",
            present_pc_refs=[],
            present_character_refs=["npc-winifred", "npc-drake"],
        )
    )
    return scene


async def test_submit_direction_runs_turn(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed_pc_absent(scene_manager, fake_store)
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    result = await orch.submit_direction("c1", scene.id, text="winifred confronts Drake")
    assert result.accepted is True
    assert result.turn_id is not None
    assert result.auto_responding is True
    assert fake_context_builder.calls[0]["player_input"] == "winifred confronts Drake"


async def test_submit_direction_continue_with_empty_text(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed_pc_absent(scene_manager, fake_store)
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    result = await orch.submit_direction("c1", scene.id)
    assert result.accepted is True
    assert result.turn_id is not None
    assert fake_context_builder.calls[0]["player_input"] == ""


async def test_submit_direction_rejects_pc_present_scene(
    scene_manager, event_bus, fake_store, fake_gateway, fake_extractor, fake_context_builder
):
    scene = await _seed(scene_manager, fake_store)
    orch = _build_orch(
        scene_manager=scene_manager,
        event_bus=event_bus,
        fake_store=fake_store,
        fake_gateway=fake_gateway,
        fake_extractor=fake_extractor,
        fake_context_builder=fake_context_builder,
    )
    from grimoire.orchestrator.errors import OrchestratorError

    with pytest.raises(OrchestratorError, match="not a PC-absent scene"):
        await orch.submit_direction("c1", scene.id, text="Direct something")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/orchestrator/test_service.py::test_submit_direction_runs_turn tests/orchestrator/test_service.py::test_submit_direction_continue_with_empty_text tests/orchestrator/test_service.py::test_submit_direction_rejects_pc_present_scene -v`
Expected: FAIL — `AttributeError: 'OrchestratorService' object has no attribute 'submit_direction'`

- [ ] **Step 3: Implement submit_direction on OrchestratorService**

In `backend/src/grimoire/orchestrator/service.py`, add after `submit_post` (after line 352):

```python
async def submit_direction(
    self,
    campaign_id: CampaignId,
    scene_id: SceneId,
    text: str | None = None,
) -> SubmitResult:
    await self._require_campaign(campaign_id)

    scene = await self._scenes.get_scene(scene_id)
    if scene.present_pc_refs:
        raise OrchestratorError(
            f"scene {scene_id!r} is not a PC-absent scene; use submit_post instead"
        )

    player_input = text or ""

    if player_input:
        post = self._new_post(
            author_kind=SceneAuthorKind.SYSTEM,
            body=player_input,
            is_player=True,
        )
        await self._scenes.append_post(scene.id, post)

    turn_id = await self._run_turn(
        campaign_id=campaign_id,
        scene_id=scene.id,
        player_input=player_input,
        triggering_pc=None,
    )
    return SubmitResult(
        accepted=True,
        turn_id=turn_id,
        auto_responding=True,
        reason="direction",
    )
```

- [ ] **Step 4: Fix _maybe_break_scene to allow director scene breaks**

In `backend/src/grimoire/orchestrator/service.py`, modify `_maybe_break_scene` (around line 1052). Change:

```python
if not player_input or triggering_pc is None:
    return scene_id
```

To:

```python
if not player_input:
    return scene_id
```

This allows scene breaks from direction text even without a `triggering_pc`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/orchestrator/test_service.py -v`
Expected: All PASS

- [ ] **Step 6: Run ruff**

Run: `cd backend && uv run ruff check src/grimoire/orchestrator/service.py && uv run ruff format --check src/grimoire/orchestrator/service.py`
Expected: Clean

- [ ] **Step 7: Commit**

```
git add backend/src/grimoire/orchestrator/service.py backend/tests/orchestrator/test_service.py
git commit -m "feat: add submit_direction() to orchestrator for PC-absent scenes"
```

---

### Task 6: API endpoint and schema

**Files:**
- Modify: `backend/src/grimoire/api/campaigns/schemas.py` (add SubmitDirectionPayload)
- Modify: `backend/src/grimoire/api/campaigns/turns.py` (add route)
- Test: `backend/tests/api/test_campaigns_routes.py`

- [ ] **Step 1: Add SubmitDirectionPayload schema**

In `backend/src/grimoire/api/campaigns/schemas.py`, add after `AdvanceTurnPayload` (after line 104):

```python
class SubmitDirectionPayload(BaseModel):
    scene_id: str
    text: str | None = None
```

- [ ] **Step 2: Add the /turns/direct route**

In `backend/src/grimoire/api/campaigns/turns.py`, add the import:

```python
from .schemas import (
    AdvanceTurnPayload,
    ResolveProposalsPayload,
    ResolveSceneBreakPayload,
    SubmitDirectionPayload,
    SubmitTurnPayload,
    UndoPayload,
)
```

Add the route after `advance_turn` (after line 48):

```python
@router.post("/{campaign_id}/turns/direct")
async def submit_direction(
    campaign_id: str,
    payload: SubmitDirectionPayload,
    orchestrator: OrchestratorDep,
) -> Any:
    try:
        result = await orchestrator.submit_direction(
            campaign_id, payload.scene_id, text=payload.text
        )
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)
```

- [ ] **Step 3: Add submit_direction to FakeOrchestrator**

In `backend/tests/mocks.py`, add after `submit_post` (after line 18):

```python
async def submit_direction(
    self, campaign_id: str, scene_id: str, text: str | None = None
) -> Any:
    from grimoire.types.orchestrator import SubmitResult

    self.calls.append(("submit_direction", campaign_id, scene_id, text))
    return SubmitResult(
        accepted=True, turn_id="t_dir_1", auto_responding=True, reason="direction"
    )
```

- [ ] **Step 4: Write API route test**

Add to `backend/tests/api/test_campaigns_routes.py`:

```python
def test_submit_direction_dispatches_to_orchestrator(client, container) -> None:
    fake = FakeOrchestrator()
    container.orchestrator = fake
    response = client.post(
        "/api/campaigns/c1/turns/direct",
        json={"scene_id": "s1", "text": "winifred confronts Drake"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["turn_id"] == "t_dir_1"
    assert body["auto_responding"] is True
    assert fake.calls == [("submit_direction", "c1", "s1", "winifred confronts Drake")]


def test_submit_direction_with_no_text(client, container) -> None:
    fake = FakeOrchestrator()
    container.orchestrator = fake
    response = client.post(
        "/api/campaigns/c1/turns/direct",
        json={"scene_id": "s1"},
    )
    assert response.status_code == 200
    assert fake.calls == [("submit_direction", "c1", "s1", None)]
```

- [ ] **Step 5: Run the API tests**

Run: `cd backend && uv run pytest tests/api/test_campaigns_routes.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```
git add backend/src/grimoire/api/campaigns/schemas.py backend/src/grimoire/api/campaigns/turns.py backend/tests/mocks.py backend/tests/api/test_campaigns_routes.py
git commit -m "feat: add POST /turns/direct API endpoint for PC-absent scene direction"
```

---

### Task 7: Frontend API client + types

**Files:**
- Modify: `frontend/src/api/campaign/api.ts:88-92`
- Modify: `frontend/src/api/campaign/types.ts:30-43`

- [ ] **Step 1: Add submitDirection to the API client**

In `frontend/src/api/campaign/api.ts`, add after the `advance` function (after line 92):

```typescript
submitDirection: (id: string, sceneId: string, text?: string) =>
  api.post<SubmitTurnResult>(`/api/campaigns/${enc(id)}/turns/direct`, {
    scene_id: sceneId,
    ...(text ? { text } : {}),
  }),
```

- [ ] **Step 2: Add "direction" to ApiPost.author_kind union**

In `frontend/src/api/campaign/types.ts`, update the `author_kind` field on `ApiPost` (line 34):

```typescript
author_kind: "pc" | "narrator" | "npc" | "system" | "direction";
```

Also update `ApiAlternate`'s `author_kind` (line 20):

```typescript
author_kind: "pc" | "narrator" | "npc" | "system" | "direction";
```

- [ ] **Step 3: Run typecheck**

Run: `cd frontend && pnpm typecheck`
Expected: PASS (no type errors from the union extension)

- [ ] **Step 4: Commit**

```
git add frontend/src/api/campaign/api.ts frontend/src/api/campaign/types.ts
git commit -m "feat: add submitDirection API function and direction author_kind type"
```

---

### Task 8: Frontend usePlayCommands — direct() handler

**Files:**
- Modify: `frontend/src/routes/campaign/usePlayCommands.ts`

- [ ] **Step 1: Add direct() handler**

In `frontend/src/routes/campaign/usePlayCommands.ts`, add after the `advance` callback (after line 48):

```typescript
const direct = useCallback(
  async (text?: string) => {
    const scene = stateRef.current.scene;
    if (!scene) return;
    await campaignApi.submitDirection(campaignId, scene.id, text || undefined);
  },
  [campaignId, stateRef],
);
```

Update the return object to include `direct`:

```typescript
return {
  setActivePC,
  submit,
  advance,
  direct,
  regenerate,
  undo,
  endScene,
  deleteScene,
  newScene,
  suppressDrift,
};
```

- [ ] **Step 2: Run typecheck**

Run: `cd frontend && pnpm typecheck`
Expected: PASS

- [ ] **Step 3: Commit**

```
git add frontend/src/routes/campaign/usePlayCommands.ts
git commit -m "feat: add direct() handler to usePlayCommands for PC-absent scenes"
```

---

### Task 9: Frontend InputArea — director mode

**Files:**
- Modify: `frontend/src/routes/campaign/InputArea.tsx`
- Modify: `frontend/src/routes/campaign/PlayView.tsx:168-181`

- [ ] **Step 1: Add onDirect prop and director mode to InputArea**

In `frontend/src/routes/campaign/InputArea.tsx`, update the Props interface (line 9):

```typescript
interface Props {
  campaignId: string;
  scene: ApiScene | null;
  pcs: PCEntry[];
  activePcRef: string | null;
  text: string;
  onTextChange: (text: string) => void;
  onChangePC: (ref: string) => void;
  onSubmit: (text: string, emotion?: string) => Promise<void>;
  onAdvance: () => Promise<void>;
  onDirect: (text?: string) => Promise<void>;
  advanceEnabled: boolean;
  advanceReason: string;
  busy: boolean;
}
```

Add `onDirect` to the destructured props (line 24).

Add the director mode detection after `isMultiPC` (around line 86):

```typescript
const isMultiPC = (scene?.present_pc_refs.length ?? 0) >= 2;
const isPcAbsent = (scene?.present_pc_refs.length ?? 0) === 0;
const canSubmit = isPcAbsent
  ? text.trim().length > 0 && !submitting && !busy
  : !!activePcRef && text.trim().length > 0 && !submitting && !busy;
const showAdvance = isMultiPC;
```

Add a `directSubmit` handler after the existing `submit` handler:

```typescript
const directSubmit = useCallback(async () => {
  if (submitting || busy) return;
  setSubmitting(true);
  const snapshot = text;
  onTextChange("");
  taRef.current?.focus();
  try {
    await onDirect(snapshot || undefined);
  } finally {
    setSubmitting(false);
  }
}, [submitting, busy, onDirect, onTextChange, text]);

const directContinue = useCallback(async () => {
  if (submitting || busy) return;
  setSubmitting(true);
  try {
    await onDirect();
  } finally {
    setSubmitting(false);
  }
}, [submitting, busy, onDirect]);
```

Update the form's `onSubmit` to use the right handler:

```typescript
onSubmit={(e) => {
  e.preventDefault();
  if (isPcAbsent) {
    void directSubmit();
  } else {
    void submit();
  }
}}
```

Update the textarea's `onKeyDown` similarly:

```typescript
onKeyDown={(e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
    e.preventDefault();
    if (isPcAbsent) {
      void directSubmit();
    } else {
      void submit();
    }
  }
}}
```

Update the placeholder:

```typescript
placeholder={
  isPcAbsent
    ? "Direct the scene..."
    : activePcRef
      ? `Posting as ${pcs.find((p) => p.character_ref === activePcRef)?.name ?? activePcRef}`
      : "Add a PC to begin posting."
}
```

Conditionally hide PCSwitcher and show Continue button when PC-absent. In the `input-meta` div:

```tsx
<div className="input-meta">
  {!isPcAbsent && (
    <PCSwitcher pcs={pcs} activePcRef={activePcRef} onChange={onChangePC} campaignId={campaignId} />
  )}
  {isPcAbsent && (
    <span className="input-director-hint" aria-live="polite">
      NPC-only scene — directing as narrator.
    </span>
  )}
  {isMultiPC && (
    <span className="input-multi-hint" aria-live="polite">
      Multi-PC scene — posts queue locally, click Advance to call the narrator.
    </span>
  )}
</div>
```

In the `input-actions` div, hide the ExpressionPicker and Suggest/Polish buttons when PC-absent, and add a Continue button:

```tsx
<div className="input-actions">
  {!isPcAbsent && (
    <ExpressionPicker
      value={emotion}
      onChange={setEmotion}
      disabled={submitting || busy}
    />
  )}
  <button type="submit" disabled={!canSubmit} className="input-submit">
    {submitting ? "Submitting…" : isPcAbsent ? "Direct" : "Submit"}
  </button>
  {isPcAbsent && (
    <button
      type="button"
      onClick={() => void directContinue()}
      disabled={submitting || busy}
      className="input-continue"
      title="Continue the scene without specific direction"
    >
      {submitting ? "Continuing…" : "Continue"}
    </button>
  )}
  {showAdvance && (
    <button
      type="button"
      onClick={advance}
      disabled={!advanceEnabled || advancing || busy}
      className="input-advance"
      title={advanceEnabled ? "Run the narrator on queued posts" : advanceReason}
    >
      {advancing ? "Advancing…" : "Advance"}
    </button>
  )}
  {!isPcAbsent && (
    <>
      <button
        type="button"
        onClick={() => void requestSuggestion()}
        disabled={!activePcRef || suggesting || busy}
        className="input-suggest"
        title="Generate a draft post in the active PC's voice"
      >
        {suggesting ? "Drafting…" : "Suggest a post"}
      </button>
      <button
        type="button"
        onClick={() => setPolishInstr("")}
        disabled={!text.trim() || polishing || busy}
        className="input-polish"
        title="Polish or rewrite the current draft"
      >
        {polishing ? "Polishing…" : "Polish"}
      </button>
    </>
  )}
</div>
```

- [ ] **Step 2: Wire onDirect in PlayView**

In `frontend/src/routes/campaign/PlayView.tsx`, update the InputArea usage (around line 168):

```tsx
<InputArea
  campaignId={campaignId}
  scene={play.state.scene}
  pcs={play.state.pcs}
  activePcRef={play.state.activePcRef}
  text={draft}
  onTextChange={setDraft}
  onChangePC={(ref) => void play.setActivePC(ref)}
  onSubmit={(text, emotion) => runAction(() => play.submit(text, emotion))}
  onAdvance={() => runAction(() => play.advance())}
  onDirect={(text) => runAction(() => play.direct(text))}
  advanceEnabled={play.state.advanceEnabled}
  advanceReason={play.state.advanceReason}
  busy={busy}
/>
```

- [ ] **Step 3: Run typecheck**

Run: `cd frontend && pnpm typecheck`
Expected: PASS

- [ ] **Step 4: Commit**

```
git add frontend/src/routes/campaign/InputArea.tsx frontend/src/routes/campaign/PlayView.tsx
git commit -m "feat: InputArea switches to director mode for PC-absent scenes"
```

---

### Task 10: Frontend PostItem — direction post rendering + CSS

**Files:**
- Modify: `frontend/src/routes/campaign/PostItem.tsx:20-25`
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Add direction label to PostItem**

In `frontend/src/routes/campaign/PostItem.tsx`, update `AUTHOR_LABELS` (line 20):

```typescript
const AUTHOR_LABELS: Record<ApiPost["author_kind"], string> = {
  pc: "PC",
  narrator: "Narrator",
  npc: "NPC",
  system: "System",
  direction: "Direction",
};
```

Direction posts use `author_kind === "system"` with `is_player === true` from the backend. The `PostItem` already renders based on `post.author_kind`, and system posts with `is_player=true` will get the `post-system` class. We can style direction posts by checking both conditions. Add a helper near the top:

```typescript
const isDirection = post.author_kind === "system" && post.is_player;
```

Apply a special CSS class on the article element (line 184):

```tsx
<article
  className={`post ${isDirection ? "post-direction" : `post-${post.author_kind}`}`}
  aria-label={`${isDirection ? "Direction" : `Post by ${name}`}`}
>
```

Override the name display for direction posts:

```typescript
function authorName(post: ApiPost, pcs: PCEntry[]): string {
  if (post.author_kind === "system" && post.is_player) return "Direction";
  if (post.author_pc_ref) {
    const pc = pcs.find((p) => p.character_ref === post.author_pc_ref);
    return pc?.name ?? post.author_pc_ref;
  }
  if (post.author_npc_ref) return post.author_npc_ref;
  return AUTHOR_LABELS[post.author_kind];
}
```

- [ ] **Step 2: Add CSS for direction posts**

In `frontend/src/index.css`, add after the `.post-npc::before` rule (after line 1704):

```css
.post-direction::before {
  background: var(--fg-muted);
  opacity: 0.5;
}

.post-direction .post-body {
  font-style: italic;
  opacity: 0.85;
}

.post-direction .post-author-kind {
  font-style: italic;
}
```

Also add a style for the Continue button and director hint:

```css
.input-continue {
  background: var(--bg-raised);
  color: var(--fg);
  border: 1px solid var(--border);
  padding: 0.25rem 0.75rem;
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.input-continue:hover:not(:disabled) {
  background: var(--bg-hover);
}

.input-director-hint {
  font-size: 0.85rem;
  color: var(--fg-muted);
  font-style: italic;
}
```

- [ ] **Step 3: Run typecheck and lint**

Run: `cd frontend && pnpm typecheck && pnpm lint`
Expected: PASS

- [ ] **Step 4: Commit**

```
git add frontend/src/routes/campaign/PostItem.tsx frontend/src/index.css
git commit -m "feat: render direction posts with distinct styling in PostItem"
```

---

### Task 11: Backend lint + format pass

**Files:**
- All modified backend files

- [ ] **Step 1: Run ruff check and format**

Run: `cd backend && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`

Fix any issues that come up.

- [ ] **Step 2: Run full backend test suite**

Run: `cd backend && uv run pytest -x -q`
Expected: All PASS

- [ ] **Step 3: Run frontend checks**

Run: `cd frontend && pnpm typecheck && pnpm lint && pnpm format:check`

Fix any issues.

- [ ] **Step 4: Commit any lint/format fixes**

```
git add -u
git commit -m "style: fix formatting from ruff and prettier"
```
