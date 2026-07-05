---
name: verify
description: Launch grimoire against an isolated store with a mocked OpenRouter so changes can be driven end-to-end in a browser without touching the user's real ~/.grimoire data or the LLM.
---

# Verifying grimoire end-to-end

The user usually has their own instance running on the default ports
(backend **8173**, vite **5173**) against their real library — never reuse
those ports and never let a verification backend default to `~/.grimoire`.

## Launch (isolated)

1. **Backend + OpenRouter mock** — write a launcher that (a) sets
   `GRIMOIRE_HOME` to a scratch dir *before* importing grimoire, (b) patches
   `grimoire.openrouter.API_URL` to a local mock route mounted on the same
   app, (c) runs uvicorn on a free port (e.g. 8199). The mock streams
   OpenAI-style SSE: `data: {"choices":[{"delta":{"content": ...}}]}` then
   `data: [DONE]`. Branch on the request's system prompt: if it contains
   `"suggestions"` return a JSON suggestions object, else return script-format
   posts (`**Name:** line`). A working copy lives in past session scratchpads
   as `verify_launcher.py`.
2. **Frontend** — from `frontend/`:
   `GRIMOIRE_API=http://127.0.0.1:8199 npx vite --port 5199 --strictPort`
   (the vite proxy reads `GRIMOIRE_API`).
3. Confirm isolation: `curl http://127.0.0.1:8199/api/worlds` must return `[]`
   on first run — if it returns real worlds you've hit the user's instance.

## Seed via API

Config key first (`PUT /api/config {"openrouter_key":"sk-or-x","model":"test/model"}`
— any key works, calls go to the mock). Then world → world character (with a
V3 card) → campaign → campaign PC (`POST /api/campaigns/{cid}/pcs`) → scenes
(`POST .../scenes`), cast (`POST .../scenes/{sid}/cast`), campaign-scope
greetings (`POST /api/campaigns/{cid}/greetings`). Note: suggestion cast
tokens only validate against **campaign** characters — seat a world character
into any scene once to copy it into the campaign.

## Drive

Playwright MCP against `http://127.0.0.1:5199/campaigns/{cid}`. Artifacts
(snapshots, console logs, screenshots) land in `.playwright-mcp/` at the main
repo root (gitignored) — screenshots saved with a bare filename land in the
repo root, so move them out afterwards.

## Gotchas

- Missing avatar images 404 in the console; benign for seeded test data.
- Stop both background servers when done (`TaskStop`).
