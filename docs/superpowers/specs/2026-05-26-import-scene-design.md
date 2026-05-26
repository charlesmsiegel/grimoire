# Import Scene

Add an "Import Scene" button to the campaign timeline that lets users import existing scene files (grimoire-format `.md` + optional `.yaml` sidecar) from anywhere on disk, review/edit metadata, and run the full post-processing pipeline with a progress bar.

## UI Flow

### Entry Point

"Import Scene" button on the TimelineView toolbar. Opens the file picker.

### Step 1 — Pick File

Reuse the existing `FilePathPicker` component with `glob="*.md"`. On selection, call the preview endpoint to parse the file and detect metadata.

### Step 2 — Review Metadata

A form pre-filled from:

- **YAML sidecar** (if a `.yaml` file exists alongside the `.md`): title, location, in-game start/end, mood, tags
- **Auto-detected characters**: parse `## Post N — pc:X` and `## Post N — npc:Y` headings to populate `present_character_refs` and `present_pc_refs`
- **Post count**: shown as read-only info

Editable fields: title, location_ref, in_game_start, in_game_end, mood, tags, present_character_refs, present_pc_refs.

### Step 3 — Import with Progress

A modal overlay blocks all interaction while the pipeline runs. A progress bar shows discrete ticks (N + 6 total for a scene with N posts):

| Step | Count | Description |
|------|-------|-------------|
| Parse & copy | 1 | Copy `.md` (and `.yaml` if present) into campaign `scenes/` dir with next ordinal |
| Index | 1 | Write scene + posts to SQLite via scene indexer |
| Fact extraction | N | One per post — extract facts, commitments, mechanical deltas |
| Thread detection | 1 | Run thread detector over all posts |
| Summarization | 1 | Generate running/final summary |
| Embedding | 1 | Enqueue scene body for embedding |
| Done | 1 | Finalize, refresh timeline |

On completion the modal dismisses and the timeline reloads with the new scene.

## API

### `POST /campaigns/{id}/scenes/import/preview`

**Request:**

```json
{ "path": "/absolute/path/to/scene.md" }
```

**Response:**

```json
{
  "post_count": 24,
  "detected_characters": {
    "pc_refs": ["alistair"],
    "npc_refs": ["gardner", "sera"]
  },
  "sidecar": {
    "title": "The Tower",
    "location_ref": "blackspire-tower",
    "in_game_start": "1247-10-31T22:00:00",
    "mood": "tense",
    "tags": ["night", "courtyard"]
  }
}
```

`sidecar` is `null` when no `.yaml` file exists alongside the `.md`.

### `POST /campaigns/{id}/scenes/import`

**Request:**

```json
{
  "path": "/absolute/path/to/scene.md",
  "title": "The Tower",
  "location_ref": "blackspire-tower",
  "in_game_start": "1247-10-31T22:00:00",
  "mood": "tense",
  "tags": ["night", "courtyard"],
  "present_character_refs": ["alistair", "gardner", "sera"],
  "present_pc_refs": ["alistair"]
}
```

**Response:** SSE stream (`text/event-stream`). Each event:

```
event: progress
data: {"step": "extract", "current": 5, "total": 30, "detail": "Extracting facts from post 5"}

event: progress
data: {"step": "done", "current": 30, "total": 30, "detail": "Import complete"}

event: result
data: {"scene_id": "my-campaign:0005-the-tower"}
```

Steps emitted: `copy`, `index`, `extract` (repeated N times), `threads`, `summarize`, `embed`, `done`.

### Copy-from-Campaign Variant

The same import endpoint accepts `source_scene_id` instead of `path`:

```json
{
  "source_scene_id": "other-campaign:0003-the-tower",
  "title": "The Tower (imported)",
  ...
}
```

Backend reads the source scene's `.md`/`.yaml` from its campaign directory. The metadata form and pipeline are identical.

## Modal Progress Overlay

The progress overlay:

- Covers the full viewport (same pattern as `file-browser-overlay`)
- Blocks all pointer events and keyboard navigation
- Shows: scene title, progress bar, current step label, post N/total counter during extraction
- No cancel button (pipeline should run to completion)
- Dismisses automatically on `done` event

## Error Handling

- **File not found / not readable:** preview endpoint returns 400, frontend shows inline error on the file picker
- **Parse failure (not grimoire format):** preview endpoint returns 400 with detail explaining the format issue
- **Pipeline step failure:** SSE emits an `error` event with detail; modal shows the error with a "Close" button; scene files already written to disk remain (user can re-trigger pipeline via rescan)
- **SSE connection drop:** modal shows a generic error; since the backend pipeline runs to completion regardless, a timeline refresh will show the imported scene (minus any pipeline steps that hadn't completed)

## File Handling Details

- The backend copies (not moves) the source `.md` file into `campaigns/{id}/scenes/`
- Filename: `{next_ordinal:04d}-{slug}.md` (slug derived from title)
- If a `.yaml` sidecar exists next to the source `.md`, it is read for metadata pre-fill but a fresh sidecar is written with the correct `id`, `campaign_id`, `branch_id`, and `ordinal`
- The source `.yaml`'s `posts:` block (per-post identity metadata) is preserved if present; otherwise post IDs are generated fresh
- Branch is always `main` for imported scenes

## Out of Scope

- Importing unstructured/bare prose (no `## Post N` headings) — see GitHub issue
- Drag-and-drop file upload
- Importing multiple scenes at once (batch import)
- Quick manual scene creation (separate improvement to the existing New Scene flow)
