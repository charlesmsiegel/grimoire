# Cast-Change Detection — Design

**Issue:** #464 — Play / Processing Posts
**Date:** 2026-05-28
**Status:** Approved (pre-implementation)

## Problem

During play, narration frequently introduces a character walking into a scene
("The door swings open and Captain Reyes strides in") or having one leave. Today
nothing in the turn loop updates a scene's cast in response. `present_character_refs`
and `present_pc_refs` are mutated only by:

- manual scene creation (API / YAML sidecar),
- a PC authoring a post (auto-added on post),
- scene-break logic proposing a new scene's cast,
- explicit Scene Manager API calls.

The existing extractor `scene_changes` delta carries only `to_location`; it never
touches the cast. `new_characters` are surfaced as review candidates for emergent
content but are not connected to scene presence. So a character the prose clearly
places in the scene is invisible to context assembly, the cast HUD, and the advance
trigger until someone updates presence by hand.

The issue poses the design question directly: *"Light LLM call to determine if new
characters arrive? Or perhaps merge into the main LLM call?"*

## Decision Summary

The following choices were made during brainstorming and are fixed for this spec:

| Decision | Choice |
|----------|--------|
| Detection mechanism | **Extend the Extractor** — add a `cast_changes` category to the existing structured-analysis schema. No new LLM round-trip. |
| Transitions | **Arrivals + departures** (`enter` / `leave`). |
| Unknown characters | **Reuse the `new_characters` candidate flow** — known characters get cast changes; an unrecognized name routes to the existing candidate/emergent flow and gets no cast change in this issue. |
| Application policy | **Always review** — every cast change requires explicit user confirmation; nothing auto-applies. |
| PC scope | **PCs and NPCs both** — PC presence changes route through `add_present_pc` / `remove_present_character` with their advance-gating side effects. |
| Approval path | **Dedicated cast-change channel** — not the generic `review_queue`. Scene-owned pending store + dedicated confirm/dismiss endpoints that call the Scene Manager. |

## Why these choices

- **Extending the extractor** reuses the structured analysis that already runs every
  turn (`TOGETHER` = tracker block merged into the main response; `TOOL_USE` = a
  separate structured call). Both forms answer the issue's "merge into the main LLM
  call" / "light LLM call" framing without adding a third pass. `SEPARATE`
  (rule-based, no LLM) emits no cast changes — acceptable, since the feature is
  fundamentally a comprehension task.
- **A dedicated channel** is required because the generic `review_queue` approval
  (`StateStore.approve_review_item`) applies an approved delta with a hardcoded
  `upsert_row` into the target SQLite table. Cast presence lives in the scene YAML
  sidecar (the source of truth) and carries advance-gating side effects; it *must*
  go through the Scene Manager. A generic upsert into the `scenes` index table would
  bypass the sidecar write and be clobbered by the watcher on reindex. Rather than
  couple `state_store` to `scenes`, cast changes get their own scene-owned store and
  confirm path.

## Architecture & Data Flow

```
main turn → Extractor (cast_changes added to output schema)
  → ExtractionResult.cast_changes : list[CastChangeProposal]
  → Orchestrator resolves each character_ref via the read cascade (CharacterService)
       ├─ resolves to a known character → SceneManager.queue_cast_change(...)
       │                                   (pending, scene-owned, never auto-applied)
       └─ does not resolve (new name)    → merge into ExtractionResult.candidates
       │                                   (existing new_characters flow); no cast change
       └─ no-op (enter already-present / leave not-present) → dropped at queue time
  → TURN_COMPLETE emits pending_cast_changes[]   (frontend renders confirm / dismiss)
  → user confirms → POST /{campaign}/scenes/{scene}/cast-changes/{id}/confirm
       → SceneManager.confirm_cast_change dispatches:
            enter + PC  → add_present_pc            (emits ADVANCE_DISABLED when ≥2 PCs)
            enter + NPC → add_present_character
            leave       → remove_present_character  (PC-leave advance-watermark flush)
  → user dismisses → POST .../cast-changes/{id}/dismiss   (status=dismissed, no scene write)
```

## Components

### 1. Extractor output schema — `extractor/schema.py`

Add a `cast_changes` array to `output_schema()` and `empty_payload()`:

```jsonc
"cast_changes": {
  "type": "array",
  "items": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "character_id": {"type": "string"},
      "change": {"type": "string", "enum": ["enter", "leave"]},
      "evidence": {"type": "string"},
      "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0}
    },
    "required": ["character_id", "change", "confidence"]
  }
}
```

`character_id` is the model's best reference to the character (an id or a name);
the orchestrator resolves it against the read cascade. The schema instruction text
(system prompt / tracker-block / tool description in `llm_strategy.py`,
`together.py`, `tool_use.py`) gains a one-line description of when to emit a cast
change, anchored to the `present:` line already provided to the extractor
(`llm_strategy.py:108`).

### 2. Types — `types/scene.py`, `types/extraction.py`

```python
class CastChange(StrEnum):
    ENTER = "enter"
    LEAVE = "leave"

class CastChangeProposal(BaseModel):
    character_ref: str          # raw ref/name as emitted; resolved by orchestrator
    change: CastChange
    evidence: str = ""
    confidence: float = 0.0
```

`ExtractionResult` gains `cast_changes: list[CastChangeProposal] = Field(default_factory=list)`.

A persisted, scene-owned record (in the `scenes` module) carries the resolved form:

```python
class PendingCastChange(BaseModel):
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

### 3. Extractor parsing — `extractor/`

- `LLMStrategyOutput` (in `llm_strategy.py`) gains `cast_changes: list[CastChangeProposal]`.
- `parse_llm_payload` parses the `cast_changes` array (mirrors the `transient_updates`
  loop at `llm_strategy.py:436`), skipping items with an unknown `change` value.
- `together.py` and `tool_use.py` parsers populate `cast_changes` from their bespoke
  payloads (they build deltas directly today, e.g. `together.py:272`, `tool_use.py:274`).
- `service.py` threads `cast_changes=list(llm_out.cast_changes)` into the returned
  `ExtractionResult` (alongside `transient_updates` at `service.py:346`).

No confidence gating in the extractor — `cast_changes` are proposals; the always-review
policy means confidence is informational only.

### 4. Orchestrator resolution — `orchestrator/`

After extraction (near the `apply_routing` / transient-routing block at
`service.py:1044`), and in the analysis-deltas path (`route_analysis_deltas`,
`service.py:603`):

1. For each `CastChangeProposal`, resolve `character_ref` via `CharacterService`
   (`resolve` / `cross_world_lookup` / campaign roster).
2. **Resolved → known character:** determine `is_pc` from the campaign PC roster,
   drop no-ops (`enter` when already in `present_character_refs`; `leave` when absent),
   and call `SceneManager.queue_cast_change(...)`.
3. **Unresolved → new name:** synthesize/merge an `EntityCandidate` of kind
   `CHARACTER` into `ExtractionResult.candidates` (dedup against existing candidates),
   producing no cast change. This reconnects to the existing emergent-content flow;
   re-proposing presence after emergence is out of scope for #464.
4. Emit the resulting pending cast changes in the `TURN_COMPLETE` event payload
   (and a turn fragment), parallel to `queued_for_review` at `service.py:1055`.

### 5. Scene Manager — `scenes/` (manager, storage/indexer)

Scene Manager owns the cast, so it owns pending cast changes. New methods:

- `queue_cast_change(scene_id, *, character_ref, change, is_pc, evidence, confidence, turn_id) -> PendingCastChange`
- `list_pending_cast_changes(scene_id, *, status="pending") -> list[PendingCastChange]`
- `confirm_cast_change(scene_id, change_id) -> None` — loads the record, dispatches:
  - `ENTER` + `is_pc` → `add_present_pc`
  - `ENTER` + not pc → `add_present_character`
  - `LEAVE` (pc or npc) → `remove_present_character`
  then marks the record `confirmed`. The underlying presence methods are already
  idempotent, so a stale confirmation is a safe no-op.
- `dismiss_cast_change(scene_id, change_id) -> None` — marks `dismissed`; no scene write.

Backed by a new SQLite table `pending_cast_changes` (campaign + scene scoped). This is
queue state derived from a turn, not human-authored content, so SQLite-only storage is
correct — the same justification the `review_queue` table relies on. A schema migration
adds the table.

### 6. Scenes API — `api/campaigns/scenes.py`

- `GET  /{campaign_id}/scenes/{scene_id}/cast-changes` → pending list.
- `POST /{campaign_id}/scenes/{scene_id}/cast-changes/{change_id}/confirm` → applies via Scene Manager.
- `POST /{campaign_id}/scenes/{scene_id}/cast-changes/{change_id}/dismiss` → marks dismissed.

Each guarded by the campaign/scene-ownership check pattern used for reviews
(`api/campaigns/helpers.py:_require_review_owned`).

### 7. Frontend — `frontend/src/`

Surface pending cast changes in the turn/HUD UI: a compact prompt per change
("Captain Reyes enters the scene") with **Confirm** and **Dismiss** actions calling
the new endpoints, plus a Zod schema for the payload. Pending changes arrive in the
`TURN_COMPLETE` event and via the `GET` endpoint on scene load.

## Error Handling

- **Unresolvable reference** → routed to the candidate flow; never a silent cast write.
- **No-op proposal** (enter already-present / leave not-present) → dropped at queue time;
  confirm is idempotent as a second line of defense.
- **PC presence changes** → confirmation flows through `add_present_pc` /
  `remove_present_character`, preserving `ADVANCE_ENABLED` / `ADVANCE_DISABLED`
  emissions and the multi-PC advance watermark.
- **Confidence** is informational (display/sort), not a gate.
- **Stale confirm** (record already confirmed/dismissed, or scene closed) → rejected
  with a clear error from the Scene Manager; the API maps it to a 4xx.

## Module Ownership

- **Scene Manager** owns cast presence and therefore the pending-cast-change store and
  its confirm/dismiss application. Cast writes never bypass it.
- **Orchestrator** owns resolution (it already wires Characters + Scenes + Extractor)
  and the candidate-flow fallback.
- **Extractor** owns detection (schema + parsing). It emits proposals only; it does not
  resolve or apply them.
- `state_store` is untouched — no coupling to `scenes`.

## Testing

- **Unit:** `output_schema()`/`empty_payload()` include `cast_changes`;
  `parse_llm_payload`, `together.py`, and `tool_use.py` parse `cast_changes`;
  orchestrator resolution (known → queued, unknown → candidate, `is_pc` detection,
  no-op dedup); Scene Manager `queue`/`confirm`/`dismiss` dispatch + idempotency.
- **Integration (`integration`):** a full turn whose tracker block contains a
  `cast_change` → pending record created → confirm → sidecar `present_character_refs`
  updated; for a PC, `ADVANCE_DISABLED`/`ADVANCE_ENABLED` emitted as appropriate.
- **Scenario (`scenario`):** confirm/dismiss endpoints end-to-end through HTTP,
  including the ownership guard.
- **Frontend:** component test for the confirm/dismiss control (React Testing Library).
- **Regression:** unresolved name produces a candidate and *no* cast change.

## Out of Scope

- Auto-applying cast changes (explicitly review-gated).
- Re-proposing presence automatically after a `new_characters` candidate is promoted to
  emergent content.
- Rule-based (`SEPARATE`-mode) cast detection.
- Bulk confirm/dismiss UI affordances.
