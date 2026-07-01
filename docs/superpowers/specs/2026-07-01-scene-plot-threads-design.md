# Scene Plot Threads (Phase 5a) — Design

**Date:** 2026-07-01
**Status:** Design — approved, ready for implementation plan
**Phase:** 5a of the scene lifecycle & continuity system (Phase 5 split into **5a plot
threads** and **5b suggested-next-scenes**; 5b builds on 5a and is brainstormed separately)
**Parent:** [`2026-06-30-scene-lifecycle-continuity-design.md`](2026-06-30-scene-lifecycle-continuity-design.md) (umbrella; see Plot threads + the read-forward)
**Builds on Phases 2–4:** the `absorb.py` extraction → `materialize` (StagedEdits) →
review checklist → `apply_edits` → context-injection pipeline.

## Problem

The continuity system tracks who characters *are* (state), what they *know* (knowledge),
and how they *feel* (relationships) — but nothing tracks the campaign's **open narrative
questions**: the forged map, the Duke's debt, the missing heir. Without them the model
loses the plot between scenes and the recap can't remind it what's unresolved. Phase 5a
adds tracked plot threads that the extraction proposes as *movements*, the user reviews,
and the context injects as a dedicated section — reusing the Phase 2–4 machinery.

## Scope

- **In:** a per-campaign set of plot threads (`plot.json`), each with a title, a
  lifecycle status (open / advanced / closed), an ordered list of dated **beats**, and a
  `last_scene`. The extraction proposes **movements** (open a new thread, advance an
  existing one with a beat, or close one); movements are reviewed (beat text editable),
  applied, and open/advanced threads are injected as `# Plot threads`.
- **Deferred:** suggested-next-scenes (5b); a standalone `plot.json` hand-editor page
  (the review + injection is the core); storing `plot_movements` in `chronicle.json`
  (`plot.json` is the source of truth). Campaign-vs-base world view remains Phase 6.

## Decisions

1. **`plot` is a new StagedEdit kind with an editable beat + structured payload.** Unlike
   the fully-structured `relationship`/`bond` rows (approve-only) and unlike the plain
   editable `character_state`/`lore`/`authored` rows, a `plot` row is a hybrid: the
   **beat sentence is the editable `after` text**, while id/title/status/scene ride the
   `payload`. This lets the user fix the model's beat wording while the thread identity
   stays fixed.
2. **Absolute status, fed current threads.** Like Phases 2–4, the extraction is fed the
   current open/advanced threads (id + title + latest beat) and returns each movement's
   **absolute** new status plus the beat describing this scene's contribution. Beats
   **accrete** (append-only per thread); status is replaced.
3. **New-thread ids are slugified from the title** (`paths.slugify`, as entities/
   characters do). `materialize` **resolves the pid first** (the given `id`, else
   `slugify(title)` only when the title has real content — a titleless or
   punctuation-only movement is dropped, since `slugify` would otherwise fall back to
   `"untitled"`), **then looks up the existing thread by that pid**. So a new-title
   movement whose slug collides with an existing thread merges into it honestly (the diff
   shows the existing `before` and keeps the stored title), rather than masquerading as
   new. Movements resolving to the same pid within one scene are de-duplicated to one edit.
4. **Own `# Plot threads` context section**, listing open/advanced threads (closed drop
   out), tolerant (omit-never-crash) like every continuity block.

## Storage — `plot.json`

`<campaign>/plot.json` (nested → JSON sidecar, per convention):

```jsonc
{
  "the-forged-map": {
    "title": "The forged map",
    "status": "advanced",                       // open | advanced | closed
    "beats": [
      {"scene": "s10", "text": "Elara obtained the map from the archive."},
      {"scene": "s12", "text": "Seraphine realized it's a forgery."}
    ],
    "last_scene": "s12"
  }
}
```

New module **`store/plot.py`** (pure JSON IO, mirrors `relationships.py`):
- `read(cid) -> dict` — missing ⇒ `{}`.
- `get(cid, pid) -> dict | None`.
- `set_movement(cid, pid, title, status, beat_text, scene) -> None` — create-or-update:
  append `{"scene": scene, "text": beat_text}` to `beats` **only when `beat_text` is
  non-empty**; set `status`; keep the existing `title` when the passed one is blank
  (so an advance/close needn't restate it); set `last_scene = scene`. A brand-new thread
  with an empty title falls back to `pid` as its title.
- `open_threads(cid) -> list[dict]` — threads with `status != "closed"`, each
  `{"id", "title", "status", "latest_beat"}` (`latest_beat` = last beat's text, `""` if
  none), sorted by `last_scene` then `id`.
- `render_open(cid, with_id) -> list[str]` — formatted open/advanced lines shared by the
  prompt snapshot (`with_id=True` → `"id: Title (status) — beat"`) and the `# Plot threads`
  context block (`with_id=False` → `"Title (status): beat"`). **Tolerant** (returns `[]`
  on a garbled `plot.json`) so both callers stay omit-never-crash. Mirrors the render
  helpers on `relationships.py`.
- Pure JSON IO (`indent=2, sort_keys=True`). Imports only `campaigns`/`paths`.

## Extraction (grows again, still one call)

`absorb.py`:
- **`EXTRACT_INSTRUCTION`** — add `"plot_movements"` (list of
  `{"id","title","status","beat"}`): for each plot thread this scene moved, the thread's
  **id from the context block** to advance or close it, or a NEW thread (omit `id`;
  provide a `title`); `status` is one of `open`/`advanced`/`closed`; `beat` is one
  sentence on how *this* scene moved it. Only emit a thread that actually moved.
- **`parse_output`** — parse `plot_movements` into
  `{"id","title","status","beat"}` (strings stripped; `status` coerced to the enum,
  defaulting to `open` when missing/invalid). Garbled ⇒ `[]`. The tolerant `parse_output`
  contract (all existing keys always present) is preserved.
- **`plot_snapshot(cid, sid) -> str`** — render the campaign's current open/advanced
  threads (`id` + `title` + latest beat) into a prompt block so the model advances the
  right thread instead of duplicating. Fed via a new `plot_snapshot` parameter on
  `build_prompt` (mirrors `rel_snapshot`). Tolerant of a garbled `plot.json` (returns
  `""`).

## StagedEdit — the new `plot` kind

`materialize(cid, sid, parsed)` gains a `plot_movements` loop. It reads `plot.json`
**once, tolerantly** (a garbled store ⇒ treat as empty rather than 500 after the paid
completion). For each movement:
- **Resolve the pid** (id → else `slugify(title)` when the title has real content → else
  drop), skip if already seen this scene, then **look up the existing thread by pid**: if
  found, `before` = readable prior `status` + latest beat and the payload keeps the stored
  title; otherwise it's new (`before = ""`). A movement with an **empty `beat`** is dropped
  (a movement always records a beat).
- **Status:** taken from the parsed movement (already enum-coerced).
- `scene = sid` is folded into the `payload` so `apply_edits` (which has no `sid`) can
  set `last_scene`.

```jsonc
{ "id": "plot:the-forged-map",
  "kind": "plot",
  "target": {"kind": "plot", "id": "the-forged-map"},
  "label": "The forged map — advanced",              // "{title} — {status}"
  "field": "beat",
  "before": "open — Elara obtained the map…",         // readable prior status + latest beat; "" when new
  "after":  "Seraphine realized it's a forgery.",     // EDITABLE beat textarea
  "authored": false,
  "payload": {"id": "the-forged-map", "title": "The forged map",
              "status": "advanced", "scene": "s12"} }
```

Existing kinds are unchanged. Only the `plot` row carries a `scene` in its payload.

## Apply

`apply_edits` gains a `plot` branch (best-effort, per the Phase-2 contract):
`p = e["payload"]; plot.set_movement(cid, p["id"], p["title"], p["status"], e.get("after", ""), p["scene"])`.
`after` is the (possibly user-edited) beat text. The frontend sends approved rows with
their `payload` and edited `after` intact.

## Context injection — new `# Plot threads` section

A new always-on section in `_assemble`, among **open/advanced** threads only (closed drop
out), placed alongside `# Story so far`:

```
# Plot threads
The forged map (advanced): Seraphine realized it's a forgery.
The Duke's debt (open): A creditor came asking after Doran.
```

Each line is `"{title} ({status}): {latest_beat}"`; a thread with no beats yet shows
`"{title} ({status})"`. Tolerant of a garbled `plot.json` (omit, never crash).

## Backend modules

- **`store/plot.py`** (new) — the JSON store + `set_movement`/`open_threads` above.
- **`store/absorb.py`** — `EXTRACT_INSTRUCTION` `plot_movements`; `parse_output`
  `plot_movements`; `build_prompt` `plot_snapshot` param; `materialize` the `plot` kind;
  `apply_edits` the `plot` branch; a `plot_snapshot(cid)` helper (campaign-wide — no
  `sid`; delegates to `plot.render_open`).
- **`store/context.py`** — the `# Plot threads` section (a `_plot_threads` helper).
- **`routes.py`** — no new endpoints (rides `POST /absorb` + `PUT /chronicle`). The
  extraction call site in `post_absorb` passes the new `plot_snapshot` to `build_prompt`.

No new cycles: `plot` imports only `campaigns`/`paths`; `absorb`/`context` import it at
module load (as they do `relationships`).

## Frontend

**One-line type change + a test.** `api/client.ts` — add `"plot"` to the `StagedEdit`
`kind` union. No other change: the review row already renders an **editable textarea**
for any kind except `relationship`/`bond` (so `plot` gets the beat textarea), and
`saveAbsorb` already forwards the full edit (including `payload` and edited `after`) for
approved rows.

## Testing

### Backend (pytest)
- **`plot`**: movement round-trip; `set_movement` appends a beat, preserves an existing
  title when passed blank, updates `last_scene`, and closes a thread; empty `beat_text`
  does not append a beat; `open_threads` excludes closed and sorts; missing file ⇒ `{}`.
- **`absorb.parse_output`**: `plot_movements` parse; bad/missing `status` ⇒ `open`;
  garbled ⇒ `[]`; existing keys still present.
- **`absorb.materialize`**: a new thread → `plot` edit with `slugify` id and `before ""`;
  an advance of an existing thread → `before` shows prior status + beat, `payload.status`
  updated; a close; an empty-beat movement dropped; an id-less title-less movement
  dropped; `payload` carries `scene = sid`.
- **`absorb.apply_edits`**: a `plot` edit writes `plot.json` (beat appended, status set,
  `last_scene` set); a malformed payload skipped.
- **`absorb.plot_snapshot` / `context`**: `# Plot threads` lists open/advanced with
  latest beat, omits closed, omitted entirely when none; both tolerant of a garbled
  `plot.json`.

### Frontend (vitest)
- A `plot` row renders an **editable** beat textarea (not a read-only diff) and Save sends
  the edited `after` **with its `payload`**.

## Out of scope

- Suggested-next-scenes (Phase 5b), the standalone `plot.json` editor, and any
  `chronicle.json` plot mirroring.
- Inline title/status editing in review (only the beat is editable this phase; approve/
  reject the model's status/title otherwise).
- Injecting closed threads (only open/advanced reach the prompt).

## Phasing (for the plan)

1. `plot.py` store + `set_movement`/`open_threads`.
2. `absorb`: `parse_output` `plot_movements` + `plot_snapshot` + `build_prompt` param +
   `EXTRACT_INSTRUCTION`.
3. `absorb.materialize` the `plot` kind (slug/resolve, editable beat, payload).
4. `absorb.apply_edits` the `plot` branch; wire `plot_snapshot` into `post_absorb`.
5. `# Plot threads` injection.
6. Frontend: `"plot"` in the `StagedEdit` kind union + the editable-row vitest.
