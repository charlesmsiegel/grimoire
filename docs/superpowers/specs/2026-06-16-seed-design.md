# grimoire Seed — Design

> The absolute-minimum starting point for grimoire (attempt 2): an OpenRouter-backed
> chat whose entire state lives as markdown files under `~/.grimoire/`. Chosen so that
> worlds, campaigns, and mechanics can grow back in later without re-laying the foundation.
> The reference implementation of the full system lives under `OLD/` (gitignored).

**Status:** Design — not yet implemented
**Date:** 2026-06-16

## Purpose

Stand up the smallest thing that is recognizably grimoire:

- A user configures their OpenRouter key, model, and theme.
- They have a streaming chat with the model.
- Every byte of user state is a markdown file under `~/.grimoire/` — the single source of truth.

This is a **seed**, not a throwaway demo. The stack (FastAPI + React/Vite/TS + SSE), the
`~/.grimoire/` file conventions, and the token-based theming system are deliberately the
same shapes the full app will use, so later features extend the seed rather than replace it.

## Non-goals

Explicitly **not** in this seed: worlds, characters, mechanics, inventory, embeddings,
SQLite, multi-provider routing/fallback, observability/audit logs, WebSocket transport,
authentication, packaging. Each can be added later (see "What grows later").

## Stack & repository layout

FastAPI backend, React 18 + Vite 5 + TypeScript frontend with hand-rolled CSS (no UI
framework, matching the OLD frontend's choice), SSE for streaming.

```
backend/
  src/grimoire/
    main.py            # FastAPI app; serves /api/* and the built frontend
    store.py           # ~/.grimoire read/write (markdown + frontmatter)
    openrouter.py      # thin OpenRouter client (complete + stream)
    routes.py          # /api/config, /api/conversations, /api/chat
  tests/
  pyproject.toml
frontend/
  src/
    api/client.ts      # typed fetch wrapper (ApiError), SSE helper
    theme/
      ThemeProvider.tsx  # applies the active theme's tokens to :root
      types.ts           # Theme = { name, label, tokens: Record<string,string> }
      themes/
        occult.ts        # one file per theme (default export)
        terminal.ts
        ink.ts
        index.ts         # registry: collects all themes by name
    routes/ChatView.tsx
    routes/ConfigView.tsx
    main.tsx
    App.tsx
  public/                  # static assets, sourced from OLD/frontend/public
    favicon.ico            # app/window icon + Windows shortcut icon
    grimoire-32.png
    grimoire-128.png
    grimoire-256.png       # desktop-entry icon on Linux/macOS
    grimoire-512.png
  index.html               # references favicon.ico + the wordmark logo
  package.json
  vite.config.ts
scripts/
  windows/                 # PowerShell (primary target)
    install.ps1
    run.ps1
    shutdown.ps1
    launch.vbs             # pinnable launcher target (runs run.ps1, no console flash)
  unix/                    # bash (macOS + Linux)
    install.sh
    run.sh
    shutdown.sh
```

## Data — `~/.grimoire/`

```
~/.grimoire/
  config.md                      # frontmatter only (no body)
  conversations/
    2026-06-16-first-chat.md     # frontmatter + transcript body
```

The directory is created on first run if absent.

### config.md

Frontmatter only; the body is unused (reserved for future human notes).

```markdown
---
openrouter_key: sk-or-...        # plaintext, local dotfile (same trust model as .env)
model: anthropic/claude-opus-4.1 # OpenRouter model id; sensible default on first run
theme: occult                    # occult | terminal | ink
---
```

On first run the file is written with an **empty** `openrouter_key`, a default `model`, and
`theme: occult`.

### conversation files

One file per conversation, named `YYYY-MM-DD-<slug>.md`. The **body is the source of
truth** for the transcript — the backend serializes messages into it and parses them back.

```markdown
---
title: First chat
model: anthropic/claude-opus-4.1
created: 2026-06-16T22:40:00Z
updated: 2026-06-16T22:41:12Z
---

**You:** Describe the keeper of the drowned library.

**Grimoire:** She is older than the salt — ink for blood, a lantern where her heart should beat.
```

Parsing rule: blocks are split on the `**You:**` / `**Grimoire:**` role markers; everything
up to the next marker (or EOF) is that message's content, trimmed. `You` → `user`,
`Grimoire` → `assistant`. The id is the filename without extension.

## Backend

### store.py — filesystem as database

Pure functions over `~/.grimoire/`; no global state beyond resolving the base dir
(overridable via `GRIMOIRE_HOME` env var, which the tests use to point at a temp dir).

- `read_config() -> Config` / `write_config(**fields)` — reads/merges config.md frontmatter,
  writing only the fields provided (so a model-only save never clears the key).
- `list_conversations() -> list[ConversationMeta]` — frontmatter + id for each file, newest first.
- `read_conversation(id) -> Conversation` — meta + parsed `messages: [{role, content}]`.
- `create_conversation(title) -> id` — writes a new file with frontmatter and empty body.
- `append_message(id, role, content)` — appends a role block to the body and bumps `updated`.

Frontmatter is read/written with a tiny dependency-light YAML handling (string scalars only —
sufficient for this schema). A small `frontmatter.py`-style split (`---` fences) is fine.

### openrouter.py — provider client

A thin `httpx` client against OpenRouter's OpenAI-compatible Chat Completions endpoint
(`https://openrouter.ai/api/v1/chat/completions`).

- `async def stream(messages, model, key) -> AsyncIterator[str]` — sets
  `stream: true`, yields content deltas as they arrive (parsing SSE lines from OpenRouter).
- `async def complete(messages, model, key) -> str` — non-streaming convenience (used by
  tests / fallback).

Errors are normalized into a small `OpenRouterError` with a `kind`
(`missing_key | auth | rate_limit | network | bad_response`) so routes can translate them
into clear HTTP responses.

### routes.py — HTTP surface

- `GET  /api/config` → `{ model, theme, key_set: bool }` — **never** returns the key.
- `PUT  /api/config` → body `{ model?, theme?, openrouter_key? }`; writes only provided fields.
- `GET  /api/conversations` → list of `{ id, title, model, created, updated }`.
- `POST /api/conversations` → body `{ title? }` → `{ id }` (default title derived from date).
- `GET  /api/conversations/{id}` → `{ meta, messages }`.
- `POST /api/conversations/{id}/chat` → body `{ content }`:
  1. Load config; if `openrouter_key` is empty → `409` with `kind: "missing_key"`.
  2. `append_message(id, "user", content)`.
  3. Stream assistant deltas back as **SSE**: `data: {"delta":"..."}` per token,
     terminating with `data: {"done":true}` (or `data: {"error":{...}}` on failure).
  4. On normal completion, `append_message(id, "assistant", full_text)` so the file
     reflects the finished exchange.

### main.py

Constructs the FastAPI app, mounts `routes.py` under `/api`, and (in production) serves the
built `frontend/dist` as static files with an SPA fallback to `index.html`. CORS open to the
Vite dev server in development.

## Frontend

### App shell & routing

`main.tsx` mounts `<App>`; `App.tsx` wraps everything in `<ThemeProvider>` and a minimal
router with two views: **Chat** (default) and **Config**. A slim top bar carries the
grimoire wordmark and a link to Config.

### ChatView

- Sidebar: conversation list (from `GET /api/conversations`) + "New conversation".
- Main: transcript of the selected conversation (`GET /api/conversations/{id}`) rendered as
  role-labeled blocks; markdown bodies rendered with `react-markdown` + `remark-gfm`.
- Input: textarea, Ctrl/Cmd+Enter to send. On send, POSTs to `/chat`, reads the SSE stream,
  accumulates deltas into an in-flight assistant buffer keyed by the request, renders it
  live, and commits to the transcript on `done`.
- Empty/error states: if `/api/config` reports `key_set: false`, or a chat call returns
  `missing_key`/`auth`, show an inline banner: "Set your OpenRouter key in Config" linking
  to ConfigView.

### ConfigView

- **OpenRouter key**: write-only field. Shows "A key is set" (from `key_set`) rather than the
  value; typing a new value and saving overwrites it; empty submit leaves it unchanged.
- **Model**: text field for the OpenRouter model id.
- **Theme**: picker over the three themes (radio cards mirroring the mockups).
- Save → `PUT /api/config` with changed fields; on success, apply the theme immediately.

### API client

`api/client.ts` is a typed `fetch` wrapper raising `ApiError(status, detail)` for non-2xx
(mirrors the OLD client), plus a small SSE reader helper for the chat stream.

## Dev scripts (`scripts/`)

Cross-platform install / run / shutdown so the seed launches on any machine with no manual
steps. **Windows is the primary target** (PowerShell `.ps1`); macOS and Linux share bash
(`.sh`). Each script is small, prints what it's doing, and fails loudly with a clear message
(e.g. "Python 3.11+ not found", "Node 18+ not found").

Layout:

```
scripts/windows/{install,run,shutdown}.ps1     # PowerShell 5.1+ compatible
scripts/unix/{install,run,shutdown}.sh         # bash; macOS + Linux
```

Behavior (identical across OSes; only the shell differs):

- **install** — verify prerequisites (Python 3.11+, Node 18+); create the backend virtualenv
  (`backend/.venv`) and `pip install` the backend (editable); `npm install` in `frontend/`;
  then **create a desktop shortcut/icon that launches `run`**, using the grimoire logo.
  Idempotent — safe to re-run (re-creates the shortcut).
  - **Windows**: a `Grimoire.lnk` created in **both** the Desktop and the Start Menu
    Programs folder (`%APPDATA%\Microsoft\Windows\Start Menu\Programs`), so it can be **pinned
    to the taskbar / Start** via right-click. Two things make it pinnable (Windows refuses to
    pin shortcuts that target `powershell.exe`/`cmd.exe` directly, and would otherwise group
    them under "Windows PowerShell"):
      - **Target a launcher, not powershell** — the shortcut points at
        `wscript.exe scripts\windows\launch.vbs`; `launch.vbs` invokes `run.ps1` with a
        hidden window (also avoids a console-window flash). `wscript`-backed shortcuts pin
        cleanly.
      - **Identity** — set an explicit `System.AppUserModel.ID` (`"Grimoire"`) on the
        shortcut's property store so the taskbar treats it as a first-class app rather than a
        generic script host.
      - icon = `frontend/public/favicon.ico`; working dir = repo root. Created via the
        `WScript.Shell` COM object (with the property-store AUMID set afterward).
  - **macOS**: a double-clickable `Grimoire.command` on the Desktop that runs
    `scripts/unix/run.sh` (icon set best-effort from `grimoire-512.png` where possible).
  - **Linux**: a `grimoire.desktop` entry on the Desktop and in
    `~/.local/share/applications`, `Exec=` running `scripts/unix/run.sh`, `Icon=` pointing
    at `frontend/public/grimoire-256.png`.
- **run** — start the app in **dev mode**: backend via `uvicorn grimoire.main:app --reload`
  (port 8173) and the Vite dev server (port 5173, proxying `/api` to 8173). Both are launched
  as background processes; their PIDs are written to `.run/pids` (gitignored) so `shutdown`
  can find them. Prints the URL and opens the app — preferring the browser's **app mode**
  where available (`msedge`/`chrome --app=http://localhost:5173`) for a chromeless,
  app-like window that pairs with the pinned launcher, falling back to the default browser
  otherwise. If `.run/pids` already has live processes, it reports "already running" instead
  of double-starting. (True window-grouping of the browser window under the pinned taskbar
  icon is a packaging concern deferred to a later PWA/webview wrapper.)
- **shutdown** — read `.run/pids`, terminate those processes (gracefully, then force if
  needed), and delete the pidfile. No-op with a friendly message if nothing is running.

A production path (build `frontend/dist`, serve everything from FastAPI on one port) is a
later addition; the seed's scripts target the dev loop you build on. `.run/` is added to
`.gitignore`.

## Theming (the deliberate, non-generic part)

A token-based theme system shipped from day one so the look is intentional and swappable —
the explicit antidote to "generic React site."

- **Each theme is its own file** under `theme/themes/` (`occult.ts`, `terminal.ts`,
  `ink.ts`), default-exporting a `Theme = { name, label, tokens }` where `tokens` is a set
  of CSS custom properties: `--bg, --surface, --fg, --muted, --accent, --font-display,
  --font-body, --radius`, plus per-theme treatment of the role label, message block, input
  bar, and the streaming cursor. `themes/index.ts` is a registry that collects every theme
  by `name` — **adding a theme later is dropping one file and registering it**, nothing else.
- `ThemeProvider` looks up the active theme by name from the registry and applies its
  `tokens` to `document.documentElement` (as inline custom properties), plus sets
  `data-theme` for any token-independent rules. The active name is hydrated from
  `/api/config`'s `theme` and persisted back via `PUT /api/config` when changed. An unknown
  or missing name falls back to the default.
- All components reference **only tokens** — never hardcoded colors or fonts.
- Config stores only the chosen theme **name** (a string); token definitions stay in code.

The three shipped themes (one file each, as mocked during brainstorming):

1. **Occult Grimoire** *(default)* — warm gold on near-black, elegant serif, small-caps,
   candlelit and ornamental.
2. **Terminal Arcana** — monospace console-meets-sorcery: phosphor-green on black, bracketed
   roles, blinking cursor.
3. **Ink & Paper** — literary and calm: warm paper, dark ink, oxblood accent, a flowing
   printed-dialogue transcript (no chat bubbles).

## Error handling

- Backend normalizes OpenRouter failures into typed `OpenRouterError.kind`; routes map these
  to `409` (missing key) or `502` (`auth/rate_limit/network/bad_response`) with a JSON
  `{ detail, kind }`. During streaming, a mid-stream failure emits `data: {"error":{...}}`.
- `store.py` is defensive about a missing `~/.grimoire/` (creates it) and a malformed
  conversation file (surfaces a clear error rather than corrupting it).
- Frontend `ApiError` surfaces to inline banners; the chat input re-enables after failure.

## Testing

- **Backend (pytest, temp `GRIMOIRE_HOME`)**:
  - `store.py`: round-trip config (write-merge preserves untouched fields; key redaction is
    a route concern, not store); round-trip a conversation whose transcript contains markdown,
    role-marker-like text, and blank lines.
  - `routes.py`: `GET /api/config` never leaks the key; chat happy path streams deltas and
    appends the assistant message to the file; missing-key returns `409 kind:missing_key`.
    OpenRouter is a fake injected client (no network).
- **Frontend (light)**: a unit test for the streaming reducer (deltas → committed message)
  and a test that selecting a theme sets `document.documentElement.dataset.theme`.

## What grows later (not built now)

- `~/.grimoire/worlds/` and `~/.grimoire/campaigns/` directories (markdown entities, mirroring
  the OLD world/character model).
- SQLite + embeddings if/when retrieval needs it (the gateway's embedding cache pattern).
- WebSocket transport (one per campaign) replacing/augmenting SSE.
- Multi-provider routing, fallback, audit logging (the OLD `llm_gateway`).
- The full library / cast / timeline / mechanics views and the campaign play loop.
