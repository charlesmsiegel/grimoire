# Scene Workspace — Design

**Date:** 2026-06-29
**Status:** Approved, ready for implementation plan

## Problem

The scene page (`CampaignView`) is a two-pane editor: a scene rail on the left
and the transcript in the center. It gives no at-a-glance view of who's in the
scene, where it's set, or what is actually being sent to the model, and the
transcript is an undifferentiated stream you can't edit. There is also no way to
add global authoring instructions (e.g. "Never speak for the PC").

This redesign turns the scene page into a play-and-inspect workspace:

- A uniform **3-column layout**: scene rail | transcript | read-only **inspector**.
- The inspector shows the **active cast**, the **location**, and a **context
  breakdown** (per-section token estimates and percent of the model's context),
  with clickable cast/location opening a read-only **drawer**.
- The transcript renders each message as a **card**, with inline **editing**.
- A global **system prompt** config setting, injected at the top of the context.
- A **quote-coloring** toggle that highlights quoted dialogue.

## Existing structure (what this builds on)

- `CampaignView.tsx`: `.layout` → `.sidebar` (scenes) + `.main` (header, banners,
  `CastPanel`, `.stream` of `.msg`, `.inputbar`). `CastPanel` is the per-scene
  *editing* surface (add cast, set location, greetings, opener) and **stays in the
  center** — the inspector is purely read-only.
- `context.build_messages(cid, sid)` assembles `parts` (system) then joins them,
  appends history, then post-history. `_world_info` already supports an `exclude`
  set and the current-setting block (from the scene-location feature).
- `scenes.py` parses/serializes the transcript via `**You:**` / `**Grimoire:**`
  markers (`ROLE_TO_LABEL`, `LABEL_TO_ROLE`, `_MARKER`, `_parse_messages`,
  `append_message`).
- `config.py` stores global settings as frontmatter scalars (`_CONFIG_KEYS`,
  `read_config`, `write_config`); the `/api/config` response omits the key.
- The frontend already fetches the OpenRouter model list with `context_length`
  (`api/models.ts` → `Model.context`), so "% of context" is computed client-side.

## A. Layout (3-column)

`CampaignView` becomes three columns inside `.layout`: the existing `.sidebar`
(scene rail), the center `.main` (header, banners, `CastPanel`, `.stream`,
`.inputbar`), and a new right `.inspector` column. The inspector scrolls
independently (`overflow-y: auto`) like the rail. On narrow widths (≤ ~1100px) the
inspector drops below the transcript (CSS, no JS).

```
┌──────────┬─────────────────────────┬──────────────────────┐
│ Scenes   │ campaign header          │ INSPECTOR (read-only)│
│  + New   │ [Cast & scene setup ▸]   │ Active characters    │
│  · S1    │ ┌ card: You ───────────┐ │  • Seraphine (npc) → │ drawer
│  · S2    │ │ … [Edit]             │ │  • Elara (player)  → │
│          │ └──────────────────────┘ │ Location             │
│          │ ┌ card: Grimoire ──────┐ │  • Salt Cathedral  → │ drawer
│          │ │ "quoted dialogue"    │ │ Context   12.4k 38%  │
│          │ └──────────────────────┘ │  ▸ Global system …   │
│          │ [ input … ] [ Send ]     │  ▸ World info  3.1k … │
└──────────┴─────────────────────────┴──────────────────────┘
```

## B. Inspector — cast, location, drawer

New component `SceneInspector.tsx` (props `{ cid, sid, refreshKey }`; `refreshKey`
bumps after each send/retry/edit to refetch the context).

- **Active characters**: from `api.getCast(cid, sid)` (`Actor[]` of `{kind,id,role}`).
  Names are resolved client-side from the characters/PCs lists the inspector loads
  (mirroring `CastPanel`: world characters + world/campaign PCs), falling back to
  the id. Each row is a button → opens the drawer for `{kind, id}`.
- **Location**: from `api.getSceneLocation(cid, sid)` — the current setting name,
  a button opening the drawer for the location; "No setting" when unset.
- **Drawer** (`RecordDrawer.tsx`): a right-side overlay over the scene showing a
  read-only view of the campaign's in-play copy. Closeable via a close button and
  backdrop click. Sources:
  - actor → new `GET /api/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}`
  - location → existing `GET /api/campaigns/{cid}/locations/{id}`

  Actor drawer shows name, version, avatar (characters, via `campaignImageUrl`),
  and a markdown `body`. Location drawer shows name + rendered markdown body.

### Cast-detail endpoint

`GET /api/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}` returns the actor's
display info from the **campaign copy** at its locked version:

```json
{"kind": "characters", "id": "seraphine", "name": "Seraphine",
 "version": "corrupted", "has_avatar": true,
 "body": "**Description**\n…\n\n**Personality**\n…"}
```

- `kind` ∈ `characters` | `pcs`. For characters, `body` joins the non-empty card
  fields description / personality / scenario under bold labels; `has_avatar`
  reflects an avatar asset in the campaign copy. For PCs, `body` joins
  summary / description; `has_avatar` is `false`.
- 404 if the actor is not in the scene cast, or its campaign copy/version is
  missing. Backed by a store helper `appearances.cast_detail(cid, sid, kind, id)`.

## C. Context breakdown

### Backend refactor

Refactor `context.py` so the assembled prompt is built from **labeled sections**.
Add `context_sections(cid, sid) -> list[dict]` returning ordered, substituted,
non-empty `{"label", "text"}` entries:

1. **Global system prompt** — `config.read_config()["system_prompt"]`
2. **System prompt** — NPC `system_prompt` fields, joined
3. **Character descriptions** — NPC description/personality/scenario blocks
4. **Player personas** — player persona/character blocks
5. **Message examples** — NPC `mes_example` fields, joined
6. **Current setting** — current location body
7. **World info** — activated lore/locations (current setting excluded)
8. **Off-scene cast** — the cast directory
9. **Conversation history** — the scene's message contents, joined
10. **Post-history instructions** — NPC `post_history_instructions`, joined

`build_messages` is refactored to reuse the same computation: it joins sections
1–8 into the system message (preserving today's order and output), appends the
history messages, then the post-history as a trailing system message. {{user}} /
{{char}} substitution is applied in both paths, so the breakdown reflects the real
payload. A characterization test asserts `build_messages` output is unchanged.

### Token counting

Add `context.count_tokens(text) -> int` using **tiktoken** (`cl100k_base`),
labeled "est." in the UI. The encoder is created once and cached. **If tiktoken or
its encoding can't load (e.g. offline), fall back to `len(text) // 4`** so the
feature never crashes and tests are deterministic. `tiktoken` is added to the
backend dependencies.

### Endpoint

`GET /api/campaigns/{cid}/scenes/{sid}/context` →

```json
{"model": "anthropic/claude-…", "total_tokens": 12431,
 "sections": [{"label": "Global system prompt", "text": "…", "tokens": 42}, …]}
```

`total_tokens` is the sum of section tokens. `model` is the scene's model.

### Inspector UI

A **Context** block: a header showing `total_tokens` and the overall percent of
the model's context (`total_tokens / model.context` from the fetched model list;
omit the percent if the model/context is unknown). Then one **collapsible**
subsection per section (`<details>`), each showing its label, token count, and
percent; expanding reveals the exact `text` being sent (preformatted). Fetched on
scene load and whenever `refreshKey` changes.

## D. Response cards

Each transcript message renders as a `.msg-card` (replacing the bare `.msg`),
carrying its role label. User and assistant are visually distinguished — assistant
cards use the surface background with an accent left-border; user cards a quieter
bordered treatment. Markdown rendering is unchanged. The streaming-in-progress
message uses the assistant card style.

## E. Edit previous posts

- **Backend:** add `scenes._serialize_messages(messages) -> str` (the inverse of
  `_parse_messages`, using the marker format `append_message` already produces) and
  `scenes.edit_message(cid, sid, index, content)` — parse, validate `0 <= index <
  len`, replace that message's content, re-serialize, write with a bumped
  `updated`. Out-of-range raises `IndexError`; missing scene raises `SceneNotFound`.
- **Route:** `PUT /api/campaigns/{cid}/scenes/{sid}/messages/{index}` `{content}` →
  `{ok: True}`; 404 for a missing scene, 400 for an out-of-range index.
- **Frontend:** each `.msg-card` has a small **Edit** action → the body becomes a
  textarea with Save / Cancel → on Save, `PUT` then reload the scene messages and
  bump the inspector `refreshKey`. Works for both roles. Editing is disabled while
  a stream is in flight.

## F. Global system prompt config

- **Backend:** add `system_prompt` to `_CONFIG_KEYS` (default `""`); include it in
  `read_config` and the `/api/config` response and the `ConfigUpdate` model. It is
  section 1 of `context_sections` (see C).
- **Frontend:** `Config` type gains `system_prompt: string`; `ConfigView` adds a
  labeled `<textarea>` saved via the existing **Save** button.

## G. Quote-color toggle

- **Backend:** add `quote_color` to `_CONFIG_KEYS` (default `"off"`); include in
  `read_config`, the `/api/config` response, and `ConfigUpdate`.
- **Frontend:**
  - `Config` type gains `quote_color: string`. `ConfigView` adds a checkbox saved
    immediately (like theme), value `"on"` / `"off"`.
  - A small rehype plugin (`markdown/quotePlugin.ts`) wraps text inside double
    quotes (straight `"…"` and curly `“…”`) within a single hast text node in
    `<span class="quoted">`. It is always applied to the transcript `<Markdown>`
    (and the streaming preview).
  - `CampaignView` reads `quote_color` (via `api.getConfig`) and adds a
    `color-quotes` class to the `.stream` when on; CSS `.color-quotes .quoted {
    color: var(--accent); }` colors them. Off ⇒ spans present but uncolored.
    (Cross-node quotes — e.g. spanning emphasis — are not wrapped; acceptable.)

## Files

**Backend**
- `store/context.py` — `context_sections`, `count_tokens`, `build_messages` refactor.
- `store/scenes.py` — `_serialize_messages`, `edit_message`.
- `store/appearances.py` — `cast_detail`.
- `store/config.py` — add `system_prompt`, `quote_color`.
- `routes.py` — `/scenes/{sid}/context`, `/scenes/{sid}/cast/{kind}/{id}`,
  `PUT /scenes/{sid}/messages/{index}`; extend config model/response.
- `pyproject.toml` — add `tiktoken`.

**Frontend**
- `api/client.ts` — `Config` fields; `getSceneContext`, `getCastDetail`,
  `editMessage`; types `ContextSection`, `SceneContext`, `CastDetail`.
- `routes/CampaignView.tsx` — 3-column layout, message cards + edit, quote class.
- `components/SceneInspector.tsx` — new.
- `components/RecordDrawer.tsx` — new.
- `routes/ConfigView.tsx` — system-prompt textarea, quote-color checkbox.
- `markdown/quotePlugin.ts` — new rehype plugin.
- `index.css` — `.inspector`, `.msg-card`, drawer, context block, `.quoted`.

## Testing

### Backend (pytest)
- `context.context_sections` returns the expected labeled, non-empty sections in
  order, with the global system prompt first and substitutions applied; a
  characterization test confirms `build_messages` output is byte-for-byte unchanged
  by the refactor.
- `count_tokens` returns a positive int for non-empty text and `0` for `""`
  (whichever backend is active).
- `/context` endpoint: sections carry positive token counts and `total_tokens`
  equals their sum; `model` matches the scene.
- `cast_detail` / its endpoint: returns a world character (with `has_avatar`), a
  campaign-local PC, and 404s an actor not in the scene.
- `scenes.edit_message`: round-trips an edit at a valid index; out-of-range raises;
  the `PUT messages/{index}` route returns 400 out-of-range, 404 missing scene.
- `config`: `system_prompt` and `quote_color` round-trip through
  `write_config`/`read_config` and appear in `/api/config` without leaking the key.

### Frontend (vitest)
- `SceneInspector` lists cast (resolved names) and the location; clicking a cast
  row / the location opens the drawer (mocked fetch); the context block renders
  sections with token counts and a percent, and a section collapses/expands.
- `CampaignView`: messages render as role-distinguished `.msg-card`s; the Edit
  action saves via `editMessage` and reloads; the `.stream` gets `color-quotes`
  only when `quote_color` is on.
- `ConfigView`: saving the system prompt calls `putConfig` with `system_prompt`;
  toggling quote color calls `putConfig` with `quote_color`.
- The quote rehype plugin wraps `"…"` and `“…”` text in `span.quoted`.

## Out of scope

- Deleting messages (edit only).
- Per-campaign system prompt (global only).
- Editing campaign records from the drawer (read-only) or moving scene setup out of
  `CastPanel` (the inspector stays read-only).
- Exact per-model tokenization (tiktoken is an estimate with a char/4 fallback).
- Coloring single-quoted text or quotes spanning multiple markdown nodes.

## Phasing (for the implementation plan)

1. Backend: config (`system_prompt`, `quote_color`) + `context_sections` refactor +
   `count_tokens` + `build_messages` characterization.
2. Backend endpoints: `/context`, `/cast/{kind}/{id}` (`cast_detail`),
   `PUT messages/{index}` (`edit_message`, `_serialize_messages`).
3. Frontend plumbing: `api/client.ts` types/methods; `ConfigView` settings.
4. Frontend layout + inspector + drawer.
5. Frontend transcript: message cards, inline edit, quote plugin + toggle wiring.

Each phase ends in passing tests and its own commit(s).
