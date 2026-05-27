# PC-Absent Scenes Design

**Issue:** #446
**Date:** 2026-05-27
**Status:** Design

## Problem

Grimoire assumes every scene has at least one PC present. The system prompt always includes PC agency rules ("never write the PC's dialogue"), the lock-in block always renders an `# Active PC` card, and `submit_post` requires a `pc_ref`. This prevents a fundamental RPG pattern: NPC-only cutscenes where the player watches or directs the action without acting through a character.

The rpg-engine skill file already describes this concept — secondary PCs "when absent, can be narrated with pre-authorized notes" and the session workflow presents "absent characters" and "side-scene seeds" as scene options — but the Grimoire backend and frontend don't implement the distinction.

## Design

### Approach

Derive PC-absent status from the existing `present_pc_refs` field on `Scene`. An empty list means no PCs are present. This drives different behavior across context assembly, prompts, the orchestrator, and the frontend. No new stored fields required.

### Data Model

**`Scene` dataclass** (`scenes/types.py`): Add a computed property:

```python
@property
def pc_absent(self) -> bool:
    return len(self.present_pc_refs) == 0
```

**`BuiltContext` dataclass** (`context/types.py`): Add fields:

```python
pc_absent: bool = False
scene_mode: str = ""
```

Both set during context building. `pc_absent` drives conditional logic in the assembler (skip PC card). `scene_mode` is the rendered instruction text passed to the system block template.

**API `ApiScene` type** (frontend `types.ts`): No changes needed — `present_pc_refs` is already exposed. The frontend derives `pcAbsent` via `scene.present_pc_refs.length === 0`.

### Context Assembly & Prompts

#### System Block Template

`context_system_block/default.j2` gains a new `scene_mode` variable. Two modes:

**PC-present** (current behavior, now explicit):
```
You are narrating a scene where the player acts through their character.
Never write the player character's dialogue, actions, or internal thoughts.
Stop at decision points and wait for the player.
```

**PC-absent**:
```
This is an NPC-only scene. The player is directing the scene but has no
character present. Write all characters freely — there are no PC agency
restrictions. The player's input is scene direction, not character dialogue.
```

The `scene_mode` text is assembled in `ContextBuilderService._build_context()` (alongside `style_text`, `voice_corrective`, etc.) and stored on `BuiltContext`. The assembler passes it through to the template.

#### Lock-In Block Template

`context_lock_in_block/default.j2`: The `# Active PC` chunk is conditionally skipped when `pc_absent` is true. The scene header line from `render_scene_header()` adds "(NPC-only)" when `pc_absent`.

#### Cast Resolver (`context/cast.py`)

- `active_pc_card()`: Returns empty `("", None)` when the scene is PC-absent (no active PC to render).
- `resolve()`: In PC-absent scenes, there's no `active_pc_ref` to skip from the spotlight list. All `present_character_refs` go to spotlight normally. Drift correction only applies to present characters.
- **Absent PC background cards**: The context builder fetches compressed cards for all campaign PCs that are NOT in `present_pc_refs` and adds them to the background tier. This lets the LLM reference/mention absent PCs naturally.

#### Context Builder (`context/builder.py`)

In `_build_context()`:

1. After resolving scene state, derive `pc_absent = scene is not None and scene.pc_absent`.
2. When `pc_absent`:
   - Skip `active_pc_card` and `active_pc_name` (leave as empty strings).
   - Fetch compressed cards for absent PCs (all `campaign_pcs` not in `present_pc_refs`) and add to `background_items`.
3. Pass `pc_absent` into `BuiltContext`.

### Orchestrator & API

#### New Endpoint

```
POST /api/campaigns/{id}/turns/direct
Body: { scene_id: string, text?: string }
Response: SubmitTurnResult
```

No `pc_ref` required. Empty or missing `text` means "continue narrating" (auto-narrate mode).

#### New Orchestrator Method: `submit_direction()`

```python
async def submit_direction(
    self,
    campaign_id: CampaignId,
    scene_id: SceneId,
    text: str | None = None,
) -> SubmitResult:
```

Flow:
1. Validate the campaign and scene exist.
2. Validate the scene is PC-absent (error if PCs are present — use `submit_post` instead).
3. If text is provided, append a post with `author_kind=SYSTEM` and `is_player=True` so it appears in the conversation as a direction.
4. Call `_run_turn()` with `triggering_pc=None` and `player_input=text or ""`.
5. Return `SubmitResult`.

#### Scene Break Detection

`_maybe_break_scene()` currently short-circuits when `triggering_pc is None`. For director mode, allow scene breaks from direction text by checking for the direction-post path (no `triggering_pc` but has `player_input`).

#### Advance Logic

`on_post_submitted` already handles `len(present_pcs) <= 1` as auto-respond. Zero PCs triggers this correctly. No change needed.

### Extractor

**POV resolution**: Already handles `None` POV when no `pov_character_ref` and no `present_pc_refs`. No change needed — extraction works without a POV anchor.

**Delta application**: Unchanged. Facts, commitments, relationships are scene-agnostic.

### Frontend

#### API Client (`api.ts`)

New function:
```typescript
submitDirection: (id: string, sceneId: string, text?: string) =>
  api.post<SubmitTurnResult>(
    `/api/campaigns/${enc(id)}/turns/direct`,
    { scene_id: sceneId, text: text || undefined }
  )
```

#### InputArea (`InputArea.tsx`)

Detects PC-absent via `scene.present_pc_refs.length === 0`:

- **PC selector**: Hidden (no PCs to choose).
- **Textarea placeholder**: Changes to "Direct the scene..." (instead of "What do you do?").
- **Continue button**: Always visible alongside the text input. Submits with empty text.
- **Submit**: Calls `submitDirection` instead of `submitTurn`.

#### PlayView / usePlayCommands

New `direct()` handler:
```typescript
async function direct(text?: string) {
  if (!state.scene) return;
  const result = await campaignApi.submitDirection(campaignId, state.scene.id, text);
  // same streaming/turn-complete handling as submit
}
```

`PlayView` passes either `submit` or `direct` to `InputArea` based on PC-absent status.

#### Post Rendering (`ScenePane.tsx` / `PostItem`)

Direction posts (`author_kind === "system"` with `is_player === true`) render as italicized direction text, visually distinct from both PC dialogue and narrator prose. Example styling: lighter color, italic, indented, with a small "Direction" label.

#### PlayState

No new top-level state. The scene's `present_pc_refs` flows through existing data, and the UI derives behavior from it.

### Testing

| Level | What to test |
|-------|-------------|
| **Unit** | `Scene.pc_absent` property (empty vs non-empty `present_pc_refs`) |
| **Unit** | Context builder skips PC card when `pc_absent`, adds absent-PC background cards |
| **Unit** | System block template renders correct scene-mode instruction |
| **Unit** | `submit_direction` rejects when scene has PCs present |
| **Integration** | Direction text flows through orchestrator → context build → LLM → extraction → post append |
| **Integration** | Scene break detection works from director input |
| **Scenario** | HTTP end-to-end: create PC-absent scene, submit direction, verify response |
| **Frontend** | `InputArea` renders director mode when `present_pc_refs` is empty |
| **Frontend** | Direction posts render with correct styling |

### Out of Scope

- Auto-advance without player input (the "continue" button is the auto-narrate mechanism).
- PC entering/leaving a scene mid-scene (cast changes are a separate feature).
- Narrator response mode interaction — `all_at_once` / `per_character` still applies independently of PC presence.
