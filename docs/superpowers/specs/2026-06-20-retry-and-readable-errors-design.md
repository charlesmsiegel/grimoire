# Chat retry + readable errors — design

**Date:** 2026-06-20
**Status:** Approved

## Goal

When a chat turn fails, let the user retry it with one click, and show the error
as a readable sentence instead of a raw JSON blob.

## Background

`post_chat` persists the user message **before** calling OpenRouter, so a failed
turn leaves the conversation ending with a user message and no reply. Retry
therefore means: regenerate a reply for that already-stored last message (no new
user turn appended). The error currently shown is `resp.text` — the full
OpenRouter error JSON.

## Backend

### `routes.py`

- Factor the SSE generator out of `post_chat` into a shared helper
  `_chat_stream(cid, messages, cfg, client) -> StreamingResponse`.
- `post_chat` keeps current behavior: read conversation, 409 if no key, append
  the user message, build `messages` (stored + new user turn), stream via helper.
- New `POST /conversations/{cid}/retry`: read conversation (404 if missing),
  409 if no key, build `messages` from the **stored** messages only (no append),
  stream via the helper. 400 if the conversation has no messages.

### `openrouter.py` — readable errors

- Add `_extract_error(text) -> str`:
  - `json.loads(text)`; on failure return `text.strip()`.
  - If the parsed value is a dict, take its `error` field (or the dict itself);
    if that is a dict, return its `message` (or `detail`, else `str`); if it is a
    string, return it.
  - Fallback to `str(...)` of whatever remains.
- Use it for `status >= 400`:
  `raise OpenRouterError(_status_kind(resp.status_code), _extract_error(resp.text))`.
- Network/unexpected errors keep using `str(exc)` (already readable).

## Frontend

### `api/client.ts`

- `retry(id, onEvent)`: same SSE handling as `chat`, POSTing to
  `/api/conversations/${id}/retry` with no body.

### `ChatView.tsx`

- Extract the streaming/accumulate/finally logic shared by send and retry into a
  small helper `runStream(start: (onEvent) => Promise<void>)`.
- `send()` uses it after appending the user message.
- `retry()` uses it via `api.retry` — it appends only the assistant message on
  success and does **not** add a user message.
- Render a **Retry** button inside the error banner. It is visible only while
  `error` is set; starting any stream clears `error`, so it disappears on success
  or when a new message is sent.

## Testing (TDD)

- `backend/tests/test_openrouter.py`
  - nested `{"error":{"message":"X","code":404}}` → `OpenRouterError.detail == "X"`,
  - string `{"error":"bad key"}` → detail `"bad key"`,
  - non-JSON body → detail is the raw text.
- `backend/tests/test_routes.py`
  - `/retry` streams deltas and persists the assistant message without adding a
    user turn,
  - `/retry` with no key → 409.
- `frontend/src/api/client.test.ts` (new)
  - `retry` POSTs to the `/retry` URL and forwards parsed SSE events.
- `frontend/src/routes/ChatView.test.tsx`
  - the error banner shows a Retry button and clicking it calls `api.retry`
    without adding another user message.

## Out of scope (YAGNI)

- No retry count/backoff or auto-retry.
- No "regenerate" affordance for already-succeeded turns (Retry only appears on error).
