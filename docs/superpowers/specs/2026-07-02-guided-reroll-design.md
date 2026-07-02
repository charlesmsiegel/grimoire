# Guided reroll + message-action placement — design

**Date:** 2026-07-02

## Goal

Two small transcript changes:

1. **Reroll can take optional guidance.** Clicking Reroll opens an inline
   popover; the player may type a steering hint ("make her angrier", "don't
   skip the fight") or fire it empty for today's plain reroll.
2. **Message actions (Reroll / Edit) move to the bottom-right** of a post,
   from the top-right.

## 1. Guidance popover (frontend, `CampaignView.tsx`)

- Clicking **Reroll** no longer calls the API. It sets
  `rerollPrompt: string | null` state from `null` to `""`, which renders a
  popover anchored at that message's bottom-right (next to the actions):
  a text input (placeholder "Guide the reroll (optional)…", auto-focused)
  joined to a **Reroll ▸** button.
- Enter in the input, or the button, fires `reroll(rerollPrompt.trim())` —
  guidance included only when non-empty. Esc closes without firing.
- Only the last assistant message shows Reroll (unchanged), so a single
  state field suffices; opening it while busy is prevented as today.
- Codex styling: `--surface` background, `--rw` border, `--sh3` shadow,
  `--fm` input at 12px; zero radius. Renders identically under Manuscript
  and Astral via the variables.

## 2. Guidance plumbing (API + backend)

- `frontend/src/api/client.ts`:
  `regenerate: (cid, sid, onEvent, guidance?: string)` — posts
  `{ guidance }` when provided, `undefined` body otherwise.
- `backend/src/grimoire/routes.py`: `post_regenerate` gains an optional
  pydantic body `RegenerateBody { guidance: str | None }`. After
  `build_messages`, when guidance is a non-empty string, append one system
  message:

  > `Regenerate your previous reply. Guidance from the player: {guidance}`

- Guidance is **transient**: it is never written to the scene transcript.
  The dropped-assistant-reply behavior is unchanged.

## 3. Action placement (CSS, `index.css`)

- `.msg-actions`: `top: 0` → `bottom: 0` (still absolute, right-aligned,
  reveal on hover/focus-within).
- `.msg` gets a small `padding-bottom` so the revealed buttons don't sit on
  the last line of text.

## Testing

- **vitest** (`CampaignView.test.tsx`): Reroll click shows the popover and
  does not call `api.regenerate`; submitting empty calls
  `api.regenerate(cid, sid, fn)` with no guidance; typing text passes it as
  the 4th argument; Esc closes without a call.
- **pytest** (`test_routes.py`): regenerate with `{"guidance": "..."}`
  appends the system message to the model request (assert via the fake
  OpenRouter client's captured messages) and still removes the trailing
  assistant reply; no body behaves exactly as today.

## Out of scope

- Persisting or replaying guidance; per-message reroll history; guidance on
  Retry.
