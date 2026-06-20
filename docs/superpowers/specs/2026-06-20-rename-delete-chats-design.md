# Rename + delete chats — design

**Date:** 2026-06-20
**Status:** Approved

## Goal

Each conversation in the sidebar gets a rename (inline) and a delete (confirmed)
action.

## Backend

### `store.py`

- `rename_conversation(cid, title)`: load the file, set `meta["title"] = title`,
  rewrite. **Keep the filename/id stable** (the id becomes opaque relative to the
  title, which is fine — it's only an identifier). Does **not** change `updated`,
  so renaming does not reorder the list. Raises `ConversationNotFound` if missing.
- `delete_conversation(cid)`: remove the file; raise `ConversationNotFound` if it
  doesn't exist.

### `routes.py`

- `PUT /conversations/{cid}` body `{title}`: 400 if title is blank/whitespace;
  otherwise rename and return `{"id": cid, "title": title}`. 404 if missing.
- `DELETE /conversations/{cid}`: delete and return `{"ok": true}`. 404 if missing.

## Frontend

### `api/client.ts`

- `renameConversation(id, title)` → `PUT /api/conversations/${id}` with `{title}`.
- `deleteConversation(id)` → `DELETE /api/conversations/${id}`.

### `ChatView.tsx` sidebar

Each `.conv-item` becomes a flex row:

- a clickable title region that selects the chat (existing behavior),
- a **✎ Rename** button and a **🗑 Delete** button (with `title`/aria labels).
  Their click handlers call `e.stopPropagation()` so selection doesn't fire.

State: `editingId: string | null` and `draft: string`.

- **Rename**: ✎ sets `editingId` to the row id and `draft` to its title, replacing
  the title with an `<input>` (autofocused). Enter → if `draft.trim()` is
  non-empty, `renameConversation` then `setConvs(await listConversations())` and
  clear `editingId`; Esc or blur → cancel (clear `editingId`). Blank draft cancels.
- **Delete**: 🗑 → `window.confirm("Delete '<title>'?")`; on OK,
  `deleteConversation`, then refresh the list. If the deleted id was `activeId`,
  select the first remaining conversation (or clear `activeId`/`messages` if none).

### Styling (`index.css`)

`.conv-item` becomes `display: flex; align-items: center; gap` with the title
`flex: 1` and an `.conv-actions` group on the right holding small, muted icon
buttons (hover to accent). The inline rename input matches the sidebar width.

## Testing (TDD)

- `store`: rename updates title, keeps the same id, leaves `updated` unchanged;
  delete removes the file; delete of a missing id raises `ConversationNotFound`.
- `routes`: `PUT` renames (the new title shows in the list); blank title → 400;
  `DELETE` removes it (gone from the list); delete missing → 404.
- `api/client`: `renameConversation` issues PUT with the title; `deleteConversation`
  issues DELETE — both to the right URL.
- `ChatView`: clicking ✎ shows an input and Enter calls `renameConversation` and
  refreshes; clicking 🗑 with `window.confirm` mocked true calls
  `deleteConversation` and refreshes.

## Out of scope (YAGNI)

- No drag-reorder, no multi-select, no undo.
- Renaming does not rename the underlying file (id stays put).
