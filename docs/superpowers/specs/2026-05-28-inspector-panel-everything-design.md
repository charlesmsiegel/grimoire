# Inspector Panel — Show Everything Going Into the Next Prompt

**Issue:** #486 — Revisit Inspector panel UX
**Date:** 2026-05-28
**Status:** Design — approved, pending implementation plan
**Supersedes UX of:** `2026-05-19-context-inspector-design.md` (the preview/explain/pin/diff backend surface from that spec stays; this changes what the panel shows and adds per-source text).

## Problem

The Context Inspector is meant to answer "what will the LLM see on the next post?" Today it shows only *metadata about* the context:

- per-tier token bars,
- a **Sources** tab (source rows with inclusion reasons + pin/exclude),
- a **Diff** tab (this preview vs the previous one).

It never shows the **actual prompt content**. The backend already assembles a full `AssembledPrompt` for the preview (messages, params, tools, sources, budget) and the frontend even has an `inspectorApi.getPreview()` client for it — but the panel never calls it. So the inspector describes the context without revealing it.

Two concrete gaps:

1. **No precise text.** A `ContextSource` carries a token count and a short `summary`, but not the exact text the source injected. That text exists transiently during assembly (`TierItem.text`, `active_pc_card`, `commitments_block`) and is then discarded.
2. **Sources are incomplete.** `ctx.sources` covers the PC card, the commitments block, and the spotlight/background/archive tier items (cast, world, lore, continuity, factions, relationships, archive). It does **not** include the system block (style guide, content boundaries, worlds-in-play), the scene header, the mechanics block, the **recent verbatim posts** (scene history — often the largest chunk), or the **player's drafted input**. A source-centric view built from `ctx.sources` would silently omit a large fraction of the real prompt.

## Goal

Make the inspector show **everything** going into the next prompt, organized by source, with each source expandable to its precise text — while fitting the unified info-block HUD style.

Non-goals (YAGNI): surfacing model params / tool declarations; keeping the live diff feature; removing the backend `/context/diff` endpoint.

## Design Overview

A two-surface design, both driven by the existing live-preview hook:

- **Inline HUD block** (always on) — quick, glanceable. Single total token bar; a comprehensive, grouped source list; each row expands in place to reveal inclusion reasons, a precise-text scroll box, and pin/exclude. An **Expand** button opens the overlay.
- **Full-page overlay** (on demand) — deep reading. Per-tier budget breakdown; master/detail source list with full untruncated precise text; pin/exclude; and a "raw messages" toggle showing the verbatim assembled prompt as ground truth.

The **Diff** tab is removed. (Turn-vs-turn diffing already lives in the post-hoc `PromptDebugView`, "What did the model see?".)

## Backend Changes

### 1. `ContextSource` carries its text

Add a field to `grimoire/types/context.py`:

```python
class ContextSource(BaseModel):
    ...
    text: str = ""   # exact rendered text this source contributed
```

`text` defaults to empty so existing call sites and serialized audits remain valid. It is included in `model_dump` and therefore flows through `explain` and `getPreview` automatically.

### 2. Populate `text` on existing sources

- **Tier items** (`PromptAssembler._pack_tier`): when a packed item's cost is recorded (`item.source.tokens = cost`), also set `item.source.text = item.text`. Only packed (non-evicted) items keep their source in the list, so text is set exactly for sources that actually ship.
- **Active PC card** (`ContextBuilderService._build_context`): set `active_pc_source.text = active_pc_card`.
- **Commitments block**: set `commitments_source.text = commitments_block`.

### 3. Add sources for the currently-unrepresented blocks

In the builder/assembler, emit a `ContextSource` (with `text`, `tokens`, `tier`, a `kind`, and a fitting `InclusionReason`) for each always-on block that today has no source:

| Block | `kind` | tier | notes |
|-------|--------|------|-------|
| System block (style guide + content boundaries + worlds-in-play) | `system` | lock-in (or a `system` pseudo-tier — see Open Questions) | one source; text is the rendered system message |
| Scene header | `scene_header` | lock-in | |
| Mechanics block | `mechanics` | lock-in | only when non-empty |
| Recent verbatim posts (scene history) | `recent_posts` | lock-in | the verbatim-posts + older-recent text; often the largest chunk |
| Player's drafted input | `player_input` | (player-input) | the user's current draft |

These are built where the text is rendered (the assembler knows each block's final text and token cost). The cleanest implementation: have `PromptAssembler.assemble` append these sources to the returned `AssembledPrompt.sources` as it builds each block, so token costs are computed once and stay consistent with `budget_used`. New `InclusionReason` values may be needed (e.g. `system_prompt`, `scene_header`, `verbatim_recent`, `player_input`); reuse existing ones where they fit (`style_guide_active`, `mechanics_relevant`).

After this, **the source list reconstructs the whole prompt** — the sum of source texts equals the assembled messages (modulo template framing), and nothing is silently omitted.

### 4. No new endpoints

`POST …/context/preview`, `GET …/preview/{handle}` (full `AssembledPrompt`), and `GET …/preview/{handle}/explain` already exist and now return richer data for free. Pin/exclude endpoints are unchanged. The `/context/diff` endpoint remains but is no longer called by the panel.

## Frontend Changes

### Types (`api/inspector.ts`)
- Add `text: string` to `ContextSourceExplanation`.
- No client changes needed beyond that; `explain()` and `getPreview()` already exist.

### Inline HUD block (`InspectorPanel.tsx` + `TokenBars.tsx` + `SourceList.tsx`)
- Replace the four per-tier bars with a **single total bar** (sum of `per_tier_tokens` / sum of `per_tier_budget`). Hover/focus reveals the per-tier split (title attr or a small expandable popover). `TokenBars` is rewritten or replaced by a `TotalTokenBar`.
- Remove the `sources`/`diff` tab nav and the `DiffView` usage. The source list becomes the block's primary body (no tab gating).
- `SourceList` rows gain a precise-text scroll box in the expanded detail (from `source.text`), capped in height with internal scroll; reasons and `PinControls` stay.
- Add an **Expand** button that opens the overlay.
- Delete `DiffView.tsx` and its test; drop `computeDiff`, the diff refs, and `inspectorApi.diff` usage.

### Full-page overlay (new component, e.g. `InspectorOverlay.tsx`)
- A modal/slide-over (reuse Radix `Dialog` per project conventions) rendered at full viewport.
- Header: title, total + per-tier budget strip, a **raw messages** toggle, close button.
- Body (default, "by source"): master/detail. Left = the full source list grouped by tier; right = the selected source's **full precise text** (no truncation), inclusion reasons, and pin/exclude.
- Body ("raw messages" mode): the verbatim `AssembledPrompt.messages` rendered message-by-message (role + tier from `metadata.tier` + content), fetched via `inspectorApi.getPreview(handle)`. This is the literal prompt for ground-truth verification; it can reuse the rendering approach from `PromptDebugView`.
- Pin/exclude actions call the same `onChanged` path so the preview re-fires.

### Styling
Follow the existing `scene-setting-block` / info-block idiom for the inline block. New `inspector-overlay` styles in `index.css` for the full-page view (consistent with existing inspector classes).

## Data Flow

1. User types → `useLivePreview` debounces → `POST /preview` → `{handle, summary}`.
2. Inline block renders total bar from `summary`; `explain(handle)` fills the grouped source list (now comprehensive, each with `text`).
3. Expanding a row shows `source.text` inline.
4. Clicking **Expand** opens the overlay against the same `handle`; "raw messages" lazily calls `getPreview(handle)`.
5. Pin/exclude → `POST /pins` → `onChanged` → `useLivePreview.refresh()` → new handle → list refreshes.

## Testing

- **Backend unit:** `ContextSource.text` populated for tier items, PC card, commitments; sources emitted for system/scene_header/mechanics/recent_posts/player_input with correct text + tokens; sum of source texts is consistent with assembled messages. Empty blocks (e.g. no mechanics) emit no source.
- **Backend regression:** assembled `messages_hash` and `budget_used` unchanged by these additions (text capture must not alter assembly output). A frozen-campaign check guards prompt stability.
- **Frontend component (Vitest + RTL):** total token bar math + per-tier hover; source row expands to show `text`; Expand opens overlay; overlay master/detail selection; raw-messages toggle fetches and renders messages; pin/exclude triggers refresh. Remove `DiffView` test.
- **Scenario:** preview → explain returns comprehensive sources including `system`/`recent_posts`/`player_input` with non-empty `text`.

## Open Questions (resolve during planning, not blocking)

- **Tier for always-on blocks:** assign system/scene_header/mechanics/recent_posts to `lock-in` (they are lock-in content) vs. introducing a display-only `system` grouping in the UI. Leaning: keep them `lock-in` in data; group/label in the UI.
- **`player_input` tier:** it has no `ContextTier`; represent with a synthetic UI group ("Player input") rather than a real tier, or add a nominal tier mapping for display only.
- **New `InclusionReason` values** vs. reusing existing ones — finalize the enum additions in the plan.

## Out of Scope

- Model params / tool-declaration display.
- Live diff in the inspector (removed; post-hoc diff remains in `PromptDebugView`).
- Removing the backend `/context/diff` endpoint.
