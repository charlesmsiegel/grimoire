# Model picker — design

**Date:** 2026-06-20
**Status:** Approved design, pending spec review

## Goal

Replace the free-text **Model** field in the config view with a searchable
combobox populated from OpenRouter's public model list, so the user can find a
model by name or id and see its cost, while still being able to type any id.

## Scope

Frontend-only. No backend changes. The save flow is unchanged: the config still
stores a single `model` string via the existing `PUT /api/config`.

## Data source

OpenRouter exposes a public, CORS-enabled endpoint (verified:
`Access-Control-Allow-Origin: *`):

```
GET https://openrouter.ai/api/v1/models
```

Response shape (relevant fields only):

```json
{ "data": [
  { "id": "anthropic/claude-opus-4.8-fast",
    "name": "Anthropic: Claude Opus 4.8 (Fast)",
    "pricing": { "prompt": "0.00001", "completion": "0.00005", "...": "..." } }
] }
```

`pricing.prompt` / `pricing.completion` are strings in **US dollars per token**.
There are ~340 models.

## Components

### `frontend/src/api/models.ts`

```ts
export type Model = { id: string; name: string; prompt: string; completion: string };

export async function fetchModels(): Promise<Model[]>
```

- Fetches the endpoint, maps `data[]` to `Model`, keeping `id`, `name`, and the
  raw `pricing.prompt` / `pricing.completion` strings.
- Returns the list sorted alphabetically by `id`.
- Throws on network error or non-OK response (caller handles degradation).

Price formatting helper (also in this module, unit-tested):

```ts
export function tokensPerDollar(price: string): string
```

- `n = Number(price)`. If `n` is `0` or not finite, return `"∞"` (infinitely many
  tokens per dollar — a free side).
- Otherwise `1 / n` tokens per dollar, formatted compactly: `1.2M`, `100K`, `950`.

Combining the two sides for display:

- If **both** prompt and completion are `"0"` → render `Free`.
- Otherwise render `"<in> / <out> tok/$"` using `tokensPerDollar` per side — e.g.
  `100K / 20K tok/$`, or `∞ / 50K tok/$` when only the prompt side is free.

### `frontend/src/routes/ModelCombobox.tsx`

A controlled component:

```ts
function ModelCombobox(props: { value: string; onChange: (id: string) => void }): JSX.Element
```

Behavior:

- On mount, calls `fetchModels()` into local state (`models`, `loading`, `error`).
- Renders a text `<input>` bound to `value`; typing calls `onChange(text)` so the
  field always reflects exactly what will be saved (**free text always accepted**).
- A dropdown of suggestions appears only when the input is focused **and** there is
  text or the user opened it — not a 340-row list on page load.
- Suggestions are `models` filtered case-insensitively where the query is a
  substring of `id` **or** `name`. Each row shows:
  - line 1: `name` (left) and price string (right, from the formatter),
  - line 2: `id`.
- Clicking a row calls `onChange(row.id)` and closes the dropdown.
- **Degradation:** while `loading` or when `error` is set, it behaves as a plain
  text input (today's behavior); on `error` a quiet inline note reads
  "couldn't load model list — type a model id". The user is never blocked.

### `frontend/src/routes/ConfigView.tsx`

Replace the current model input (line 44):

```tsx
<input type="text" value={model} onChange={(e) => setModel(e.target.value)} />
```

with:

```tsx
<ModelCombobox value={model} onChange={setModel} />
```

No other changes to ConfigView; `save({ model, ... })` is unchanged.

## Styling

Reuse the existing config styles and theme CSS variables already in use
(`var(--accent)`, etc.). Add minimal combobox CSS: a positioned dropdown panel
under the input, hover/active row highlight, two-line rows with the price
right-aligned. Keep it consistent with the existing minimalist look.

## Testing

Vitest + jsdom, matching existing patterns (`api/stream.test.ts`,
`theme/ThemeProvider.test.tsx`):

- `frontend/src/api/models.test.ts`
  - parses `data[]` into `Model[]` and sorts by id (mocked `fetch`),
  - `tokensPerDollar` formatting: compact units, and the `Free` case for `"0"`.
- `frontend/src/routes/ModelCombobox.test.tsx`
  - filters suggestions by id and by name,
  - selecting a row calls `onChange` with that id,
  - free-text typing passes through to `onChange` even for an unlisted id,
  - fetch failure degrades to a usable text input (no crash, note shown).

## Out of scope (YAGNI)

- No backend `/api/models` endpoint or caching.
- No per-model metadata beyond name + price (context length, modality, etc.).
- No validation that a typed id exists.
