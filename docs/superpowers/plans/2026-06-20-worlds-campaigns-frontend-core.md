# Worlds & Campaigns — Frontend Core Loop Implementation Plan (Phase 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the frontend onto the new backend: navigation across Campaigns / Worlds / Config, CRUD for worlds and campaigns (create-campaign-from-world), and the chat loop re-homed as **scenes** inside a campaign.

**Architecture:** Rewrite `api/client.ts` to the new endpoints. Replace the single `ChatView` with `CampaignsView` (list + create), `CampaignView` (scenes sidebar + chat, a direct port of today's ChatView scoped to one campaign), and `WorldsView` (list + create). A small reusable `EditableRow` component carries the inline rename/delete pattern shared by all three lists. `App.tsx` becomes the router/nav shell.

**Tech Stack:** React 18, react-router-dom 6, Vite 5, TypeScript, Vitest + @testing-library/react (jsdom). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-20-worlds-campaigns-design.md` (Section 4: Frontend). Backend is already implemented and merged via PR #647.

## Global Constraints

- No new dependencies; React 18 + react-router-dom 6 only.
- All components reference **theme tokens only** (CSS custom properties like `var(--accent)`) — never hardcoded colors/fonts.
- Run tests from `frontend/`: `npm test` (vitest) and `npm run build` (tsc typecheck + vite build).
- The old flat conversation API is gone — `api/client.ts` must not reference `/api/conversations*`.
- **Phase 2 scope only:** navigation, world CRUD, campaign CRUD (create-from-world), and scene chat. **Deferred to Phase 3:** world/campaign entity (characters/locations/lore) management UI and the incoming/sync review UI. Do NOT add entity or sync client methods or UI in this phase (YAGNI).
- Backend endpoints this phase consumes (already live):
  - `GET/PUT /api/config`
  - `GET /api/worlds` · `POST /api/worlds {name}` · `PUT /api/worlds/{wid} {name}` · `DELETE /api/worlds/{wid}`
  - `GET /api/campaigns` · `POST /api/campaigns {name, world}` · `GET /api/campaigns/{cid}` · `PUT /api/campaigns/{cid} {name}` · `DELETE /api/campaigns/{cid}`
  - `GET /api/campaigns/{cid}/scenes` · `POST .../scenes {title?}` · `GET .../scenes/{sid}` · `PUT .../scenes/{sid} {title}` · `DELETE .../scenes/{sid}`
  - `POST .../scenes/{sid}/chat {content}` (SSE) · `POST .../scenes/{sid}/retry` (SSE)
- Commit after every task with the trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## File Structure

```
frontend/src/
  api/
    client.ts            # rewritten: new types + endpoints (worlds/campaigns/scenes)
    client.test.ts       # rewritten for the new endpoints
    stream.ts            # unchanged (SSE parser)
  components/
    EditableRow.tsx      # NEW: reusable inline-rename + delete row
    EditableRow.test.tsx # NEW
  routes/
    CampaignsView.tsx    # NEW: list campaigns + "new campaign" (name + world picker)
    CampaignsView.test.tsx
    CampaignView.tsx     # NEW: scenes sidebar + chat (port of ChatView, campaign-scoped)
    CampaignView.test.tsx
    WorldsView.tsx       # NEW: list worlds + create/rename/delete
    WorldsView.test.tsx
    ConfigView.tsx       # unchanged
    ModelCombobox.tsx    # unchanged
    ChatView.tsx         # DELETED
    ChatView.test.tsx    # DELETED
  App.tsx                # rewritten: router + nav shell
  index.css             # add .row-*, .view, .picker, .campaign-header; drop .conv-*
```

---

## Task 1: Rewrite `api/client.ts` (new endpoints)

**Files:**
- Modify (rewrite): `frontend/src/api/client.ts`
- Test (rewrite): `frontend/src/api/client.test.ts`

**Interfaces:**
- Consumes: `parseSSEChunk`, `ChatEvent` from `./stream` (unchanged); `ApiError` (kept).
- Produces (types + `api` object used by every view this phase):
  - `Config = { model: string; theme: string; key_set: boolean }`
  - `WorldMeta = { id: string; name: string; created: string; updated: string; counts: Record<string, number> }`
  - `CampaignMeta = { id: string; name: string; world: string; created: string; updated: string }`
  - `SceneMeta = { id: string; title: string; model: string; created: string; updated: string }`
  - `Message = { role: "user" | "assistant"; content: string }`
  - `Scene = { meta: { id: string; title: string }; messages: Message[] }`
  - `api.getConfig/putConfig` (unchanged signatures)
  - `api.listWorlds() / createWorld(name) / renameWorld(wid,name) / deleteWorld(wid)`
  - `api.listCampaigns() / createCampaign(name,world) / getCampaign(cid) / renameCampaign(cid,name) / deleteCampaign(cid)`
  - `api.listScenes(cid) / createScene(cid,title?) / getScene(cid,sid) / renameScene(cid,sid,title) / deleteScene(cid,sid)`
  - `api.chat(cid,sid,content,onEvent) / retry(cid,sid,onEvent)`

- [ ] **Step 1: Write the failing tests** (`frontend/src/api/client.test.ts`, full replacement)

```ts
import { api } from "./client";

function sseResponse(chunks: string[]) {
  let i = 0;
  return {
    ok: true,
    body: {
      getReader() {
        return {
          read: async () =>
            i < chunks.length
              ? { value: new TextEncoder().encode(chunks[i++]), done: false }
              : { value: undefined, done: true },
        };
      },
    },
  };
}

function jsonOk(value: unknown) {
  return { ok: true, json: async () => value };
}

test("createCampaign POSTs name + world", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ id: "run" }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.createCampaign("Run One", "drowned-realm");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ name: "Run One", world: "drowned-realm" }),
    }),
  );
});

test("createWorld POSTs the name", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ id: "w" }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.createWorld("Drowned Realm");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "Drowned Realm" }) }),
  );
});

test("renameScene PUTs to the scene under its campaign", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ id: "s2", title: "New" }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.renameScene("run", "s1", "New");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ title: "New" }) }),
  );
});

test("deleteScene issues DELETE under its campaign", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.deleteScene("run", "s1");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1",
    expect.objectContaining({ method: "DELETE" }),
  );
});

test("chat posts to the scene chat endpoint and forwards SSE events", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValue(sseResponse(['data: {"delta":"hi"}\n\n', 'data: {"done":true}\n\n']));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  const events: unknown[] = [];
  await api.chat("run", "s1", "hello", (e) => events.push(e));
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1/chat",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ content: "hello" }) }),
  );
  expect(events).toEqual([{ delta: "hi" }, { done: true }]);
});

test("retry posts to the scene retry endpoint", async () => {
  const fetchMock = vi.fn().mockResolvedValue(sseResponse(['data: {"done":true}\n\n']));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.retry("run", "s1", () => {});
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/campaigns/run/scenes/s1/retry",
    expect.objectContaining({ method: "POST" }),
  );
});
```

- [ ] **Step 2: Run — expect FAIL**

Run (from `frontend/`): `npm test -- client.test`
Expected: FAIL (`api.createCampaign` etc. are not functions).

- [ ] **Step 3: Rewrite `api/client.ts`** (full replacement)

```ts
import { parseSSEChunk, type ChatEvent } from "./stream";

export class ApiError extends Error {
  constructor(public status: number, public detail: string, public kind?: string) {
    super(detail);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.detail ?? res.statusText, data.kind);
  }
  return res.json() as Promise<T>;
}

export type Config = { model: string; theme: string; key_set: boolean };
export type WorldMeta = {
  id: string;
  name: string;
  created: string;
  updated: string;
  counts: Record<string, number>;
};
export type CampaignMeta = {
  id: string;
  name: string;
  world: string;
  created: string;
  updated: string;
};
export type SceneMeta = { id: string; title: string; model: string; created: string; updated: string };
export type Message = { role: "user" | "assistant"; content: string };
export type Scene = { meta: { id: string; title: string }; messages: Message[] };

async function streamPost(
  path: string,
  body: unknown,
  onEvent: (e: ChatEvent) => void,
): Promise<void> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.detail ?? res.statusText, data.kind);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = parseSSEChunk(buffer, decoder.decode(value, { stream: true }), onEvent);
  }
}

export const api = {
  getConfig: () => request<Config>("GET", "/api/config"),
  putConfig: (body: Partial<{ model: string; theme: string; openrouter_key: string }>) =>
    request<Config>("PUT", "/api/config", body),

  // worlds
  listWorlds: () => request<WorldMeta[]>("GET", "/api/worlds"),
  createWorld: (name: string) => request<{ id: string }>("POST", "/api/worlds", { name }),
  renameWorld: (wid: string, name: string) =>
    request<{ id: string; name: string }>("PUT", `/api/worlds/${wid}`, { name }),
  deleteWorld: (wid: string) => request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}`),

  // campaigns
  listCampaigns: () => request<CampaignMeta[]>("GET", "/api/campaigns"),
  createCampaign: (name: string, world: string) =>
    request<{ id: string }>("POST", "/api/campaigns", { name, world }),
  getCampaign: (cid: string) =>
    request<{ meta: CampaignMeta; body: string }>("GET", `/api/campaigns/${cid}`),
  renameCampaign: (cid: string, name: string) =>
    request<{ id: string; name: string }>("PUT", `/api/campaigns/${cid}`, { name }),
  deleteCampaign: (cid: string) => request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}`),

  // scenes
  listScenes: (cid: string) => request<SceneMeta[]>("GET", `/api/campaigns/${cid}/scenes`),
  createScene: (cid: string, title?: string) =>
    request<{ id: string }>("POST", `/api/campaigns/${cid}/scenes`, { title }),
  getScene: (cid: string, sid: string) =>
    request<Scene>("GET", `/api/campaigns/${cid}/scenes/${sid}`),
  renameScene: (cid: string, sid: string, title: string) =>
    request<{ id: string; title: string }>("PUT", `/api/campaigns/${cid}/scenes/${sid}`, { title }),
  deleteScene: (cid: string, sid: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/scenes/${sid}`),

  chat: (cid: string, sid: string, content: string, onEvent: (e: ChatEvent) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/chat`, { content }, onEvent),
  retry: (cid: string, sid: string, onEvent: (e: ChatEvent) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/retry`, undefined, onEvent),
};
```

- [ ] **Step 4: Run — expect PASS**

Run: `npm test -- client.test`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/client.test.ts
git commit -m "feat(frontend): rewrite api client for worlds/campaigns/scenes"
```

---

## Task 2: `EditableRow` reusable row component

A presentational row used by the scenes sidebar and the campaigns/worlds lists: shows a label (optionally a subtitle), a click-to-select target, and ✎ rename / 🗑 delete buttons. Rename toggles an inline input (Enter saves, Escape/blur cancels). Delete confirmation is the caller's responsibility (so each list can word its own prompt).

**Files:**
- Create: `frontend/src/components/EditableRow.tsx`
- Test: `frontend/src/components/EditableRow.test.tsx`
- Modify: `frontend/src/index.css` (add `.row*` styles)

**Interfaces:**
- Produces:
  - `EditableRow(props: { label: string; subtitle?: string; active?: boolean; onSelect?: () => void; onRename: (next: string) => void; onDelete: () => void })`
  - Renders buttons with `aria-label="Rename"` and `aria-label="Delete"`. `onRename` is called only when the trimmed draft is non-empty and differs from `label`.

- [ ] **Step 1: Write the failing tests** (`frontend/src/components/EditableRow.test.tsx`)

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { EditableRow } from "./EditableRow";

test("clicking the label selects", () => {
  const onSelect = vi.fn();
  render(<EditableRow label="Run" onSelect={onSelect} onRename={() => {}} onDelete={() => {}} />);
  fireEvent.click(screen.getByText("Run"));
  expect(onSelect).toHaveBeenCalled();
});

test("rename flow calls onRename with the new value on Enter", () => {
  const onRename = vi.fn();
  render(<EditableRow label="Old" onRename={onRename} onDelete={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(onRename).toHaveBeenCalledWith("New");
});

test("renaming to the same value does not call onRename", () => {
  const onRename = vi.fn();
  render(<EditableRow label="Same" onRename={onRename} onDelete={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Same");
  fireEvent.keyDown(input, { key: "Enter" });
  expect(onRename).not.toHaveBeenCalled();
});

test("Escape cancels the rename without calling onRename", () => {
  const onRename = vi.fn();
  render(<EditableRow label="Old" onRename={onRename} onDelete={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Escape" });
  expect(onRename).not.toHaveBeenCalled();
  expect(screen.getByText("Old")).toBeInTheDocument();
});

test("delete calls onDelete", () => {
  const onDelete = vi.fn();
  render(<EditableRow label="Doomed" onRename={() => {}} onDelete={onDelete} />);
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  expect(onDelete).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run — expect FAIL**

Run: `npm test -- EditableRow`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `components/EditableRow.tsx`**

```tsx
import { useState } from "react";

export function EditableRow({
  label,
  subtitle,
  active,
  onSelect,
  onRename,
  onDelete,
}: {
  label: string;
  subtitle?: string;
  active?: boolean;
  onSelect?: () => void;
  onRename: (next: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(label);

  function start() {
    setDraft(label);
    setEditing(true);
  }

  function save() {
    setEditing(false);
    const next = draft.trim();
    if (next && next !== label) onRename(next);
  }

  return (
    <div className={"row" + (active ? " active" : "")}>
      {editing ? (
        <input
          className="row-rename"
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") save();
            else if (e.key === "Escape") setEditing(false);
          }}
          onBlur={() => setEditing(false)}
        />
      ) : (
        <>
          <span className="row-label" onClick={onSelect}>
            <span className="row-name">{label}</span>
            {subtitle && <span className="row-subtitle">{subtitle}</span>}
          </span>
          <span className="row-actions">
            <button
              aria-label="Rename"
              title="Rename"
              onClick={(e) => {
                e.stopPropagation();
                start();
              }}
            >
              ✎
            </button>
            <button
              aria-label="Delete"
              title="Delete"
              onClick={(e) => {
                e.stopPropagation();
                onDelete();
              }}
            >
              🗑
            </button>
          </span>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Add `.row*` styles to `frontend/src/index.css`** — append these rules (they generalize the old `.conv-*` rules, which are removed in Task 6):

```css
.row {
  display: flex; align-items: center; gap: 6px; width: 100%;
  border: 1px solid var(--muted); border-radius: var(--radius);
  padding: 6px 8px; margin-bottom: 6px;
}
.row.active { border-color: var(--accent); color: var(--accent); }
.row-label { flex: 1; cursor: pointer; overflow: hidden; }
.row-name { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row-subtitle { display: block; font-size: 11px; color: var(--muted); }
.row-actions { display: flex; gap: 2px; flex: none; }
.row-actions button { background: transparent; border: none; color: var(--muted); cursor: pointer; padding: 0 4px; font-size: 13px; line-height: 1; }
.row-actions button:hover { color: var(--accent); }
.row-rename { width: 100%; background: var(--bg); color: var(--fg); border: 1px solid var(--accent); border-radius: var(--radius); padding: 3px 6px; font-family: var(--font-body); }
```

- [ ] **Step 5: Run — expect PASS**

Run: `npm test -- EditableRow`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/EditableRow.tsx frontend/src/components/EditableRow.test.tsx frontend/src/index.css
git commit -m "feat(frontend): reusable EditableRow (inline rename + delete)"
```

---

## Task 3: `CampaignView` — scenes sidebar + chat (port of ChatView)

The play space for one campaign: a scenes sidebar (using `EditableRow`) plus the transcript/input/retry chat loop, all scoped to the campaign id from the route. This is a direct adaptation of today's `ChatView` (conversation→scene, plus a campaign header). After this task, delete `ChatView.tsx`/`ChatView.test.tsx`.

**Files:**
- Create: `frontend/src/routes/CampaignView.tsx`
- Test: `frontend/src/routes/CampaignView.test.tsx`
- Delete: `frontend/src/routes/ChatView.tsx`, `frontend/src/routes/ChatView.test.tsx`
- Modify: `frontend/src/index.css` (add `.campaign-header`)

**Interfaces:**
- Consumes: `api` (listScenes/createScene/getScene/renameScene/deleteScene/chat/retry/getCampaign), `EditableRow`, `useParams` (`{ cid }`), `ChatEvent`.
- Produces: `default export CampaignView({ keySet }: { keySet: boolean })` rendered at route `/campaigns/:cid`.

- [ ] **Step 1: Write the failing tests** (`frontend/src/routes/CampaignView.test.tsx`)

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import CampaignView from "./CampaignView";

vi.mock("../api/client", () => ({
  api: {
    getCampaign: vi.fn(),
    listScenes: vi.fn(),
    getScene: vi.fn(),
    createScene: vi.fn(),
    renameScene: vi.fn(),
    deleteScene: vi.fn(),
    chat: vi.fn(),
    retry: vi.fn(),
  },
}));
import { api } from "../api/client";

const ONE_SCENE = [{ id: "s1", title: "Old", model: "", created: "", updated: "" }];

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "Run One", world: "w" }, body: "" });
  (api.listScenes as any).mockResolvedValue([]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [] });
  (api.createScene as any).mockResolvedValue({ id: "s1" });
  (api.renameScene as any).mockResolvedValue({ id: "s1", title: "New" });
  (api.deleteScene as any).mockResolvedValue({ ok: true });
  (api.chat as any).mockResolvedValue(undefined);
  (api.retry as any).mockResolvedValue(undefined);
});

function renderCampaign() {
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      <Routes>
        <Route path="/campaigns/:cid" element={<CampaignView keySet={true} />} />
      </Routes>
    </MemoryRouter>,
  );
}

test("shows the campaign name and loads its scenes", async () => {
  renderCampaign();
  await screen.findByText("Run One");
  await waitFor(() => expect(api.listScenes).toHaveBeenCalledWith("run"));
});

test("Enter sends a message in the active scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByText("Old");
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  await waitFor(() =>
    expect(api.chat).toHaveBeenCalledWith("run", "s1", "hello", expect.any(Function)),
  );
});

test("Shift+Enter does not send", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByText("Old");
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
  expect(api.chat).not.toHaveBeenCalled();
});

test("sending with no scene creates one first", async () => {
  (api.listScenes as any).mockResolvedValue([]);
  renderCampaign();
  await waitFor(() => expect(api.listScenes).toHaveBeenCalled());
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hi" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("run"));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "hi", expect.any(Function)));
});

test("the edit button renames a scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByText("Old");
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalledWith("run", "s1", "New"));
});

test("the delete button deletes a scene after confirm", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("Old");
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalledWith("run", "s1"));
});

test("an error shows a Retry button that retries the scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementation(async (_c: string, _s: string, _t: string, onEvent: any) => {
    onEvent({ error: { detail: "boom" } });
  });
  renderCampaign();
  await screen.findByText("Old");
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  const retryBtn = await screen.findByRole("button", { name: /retry/i });
  fireEvent.click(retryBtn);
  await waitFor(() => expect(api.retry).toHaveBeenCalledWith("run", "s1", expect.any(Function)));
  expect(screen.getAllByText("hello")).toHaveLength(1);
});
```

- [ ] **Step 2: Run — expect FAIL**

Run: `npm test -- CampaignView`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `routes/CampaignView.tsx`**

```tsx
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type SceneMeta, type Message } from "../api/client";
import type { ChatEvent } from "../api/stream";
import { EditableRow } from "../components/EditableRow";

export default function CampaignView({ keySet }: { keySet: boolean }) {
  const { cid = "" } = useParams();
  const [name, setName] = useState("");
  const [scenes, setScenes] = useState<SceneMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const streamRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.getCampaign(cid).then((c) => setName(c.meta.name));
    api.listScenes(cid).then((list) => {
      setScenes(list);
      if (list.length) selectScene(list[0].id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid]);

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight });
  }, [messages, streaming]);

  async function selectScene(id: string) {
    setActiveId(id);
    const scene = await api.getScene(cid, id);
    setMessages(scene.messages);
    setStreaming("");
  }

  async function newScene() {
    const { id } = await api.createScene(cid);
    setScenes(await api.listScenes(cid));
    selectScene(id);
  }

  async function renameScene(id: string, title: string) {
    const { id: newId } = await api.renameScene(cid, id, title);
    if (activeId === id) setActiveId(newId);
    setScenes(await api.listScenes(cid));
  }

  async function deleteScene(s: SceneMeta) {
    if (!window.confirm(`Delete '${s.title}'?`)) return;
    await api.deleteScene(cid, s.id);
    const list = await api.listScenes(cid);
    setScenes(list);
    if (activeId === s.id) {
      if (list.length) selectScene(list[0].id);
      else {
        setActiveId(null);
        setMessages([]);
      }
    }
  }

  async function runStream(start: (onEvent: (e: ChatEvent) => void) => Promise<void>) {
    setBusy(true);
    setError(null);
    let acc = "";
    try {
      await start((e) => {
        if (e.delta) {
          acc += e.delta;
          setStreaming(acc);
        } else if (e.error) {
          setError(e.error.detail);
        }
      });
      if (acc) setMessages((m) => [...m, { role: "assistant", content: acc }]);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setStreaming("");
      setBusy(false);
    }
  }

  async function send() {
    if (!input.trim() || busy) return;
    let id = activeId;
    if (!id) {
      id = (await api.createScene(cid)).id;
      setScenes(await api.listScenes(cid));
      setActiveId(id);
    }
    const content = input.trim();
    setInput("");
    setMessages((m) => [...m, { role: "user", content }]);
    await runStream((onEvent) => api.chat(cid, id!, content, onEvent));
  }

  async function retry() {
    if (!activeId || busy) return;
    await runStream((onEvent) => api.retry(cid, activeId, onEvent));
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <Link to="/" className="back-link">‹ Campaigns</Link>
        <button onClick={newScene}>+ New scene</button>
        {scenes.map((s) => (
          <EditableRow
            key={s.id}
            label={s.title}
            active={s.id === activeId}
            onSelect={() => selectScene(s.id)}
            onRename={(title) => renameScene(s.id, title)}
            onDelete={() => deleteScene(s)}
          />
        ))}
      </aside>
      <section className="main">
        <div className="campaign-header">{name}</div>
        {!keySet && (
          <div className="banner">
            No OpenRouter key set. <Link to="/config">Set your key in Config</Link>.
          </div>
        )}
        {error && (
          <div className="banner error-banner">
            <span>{error}</span>
            <button className="retry" onClick={retry} disabled={busy}>
              Retry
            </button>
          </div>
        )}
        <div className="stream" ref={streamRef}>
          {messages.map((m, i) => (
            <div className="msg" key={i}>
              <div className="role">{m.role === "user" ? "You" : "Grimoire"}</div>
              <Markdown remarkPlugins={[remarkGfm]}>{m.content}</Markdown>
            </div>
          ))}
          {streaming && (
            <div className="msg">
              <div className="role">Grimoire</div>
              <Markdown remarkPlugins={[remarkGfm]}>{streaming}</Markdown>
              <span className="cursor" />
            </div>
          )}
        </div>
        <div className="inputbar">
          <textarea
            rows={3}
            placeholder="Speak your intent…  (Enter to send, Shift+Enter for newline)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button className="send" onClick={send} disabled={busy}>
            {busy ? "…" : "Send"}
          </button>
        </div>
      </section>
    </div>
  );
}
```

- [ ] **Step 4: Add `.campaign-header` + `.back-link` styles** to `frontend/src/index.css`:

```css
.campaign-header { padding: 10px 16px; border-bottom: 1px solid var(--muted); font-family: var(--font-display); color: var(--accent); }
.back-link { display: block; margin-bottom: 8px; color: var(--muted); text-decoration: none; font-size: 13px; }
.back-link:hover { color: var(--accent); }
```

- [ ] **Step 5: Delete the old ChatView**

```bash
git rm frontend/src/routes/ChatView.tsx frontend/src/routes/ChatView.test.tsx
```

- [ ] **Step 6: Run — expect PASS**

Run: `npm test -- CampaignView`
Expected: PASS (7 tests).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/CampaignView.tsx frontend/src/routes/CampaignView.test.tsx frontend/src/index.css
git commit -m "feat(frontend): CampaignView (scenes sidebar + chat), drop ChatView"
```

---

## Task 4: `CampaignsView` — list + create-from-world

The default view: a list of campaigns (each linking into `CampaignView`), with a "New campaign" form (name + a world picker populated from `listWorlds`). Empty-state guidance when there are no worlds yet.

**Files:**
- Create: `frontend/src/routes/CampaignsView.tsx`
- Test: `frontend/src/routes/CampaignsView.test.tsx`
- Modify: `frontend/src/index.css` (add `.view`, `.picker` styles)

**Interfaces:**
- Consumes: `api` (listCampaigns/createCampaign/renameCampaign/deleteCampaign/listWorlds), `EditableRow`, `useNavigate`, `Link`.
- Produces: `default export CampaignsView()` rendered at route `/`.

- [ ] **Step 1: Write the failing tests** (`frontend/src/routes/CampaignsView.test.tsx`)

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import CampaignsView from "./CampaignsView";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<any>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("../api/client", () => ({
  api: {
    listCampaigns: vi.fn(),
    listWorlds: vi.fn(),
    createCampaign: vi.fn(),
    renameCampaign: vi.fn(),
    deleteCampaign: vi.fn(),
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listCampaigns as any).mockResolvedValue([]);
  (api.listWorlds as any).mockResolvedValue([
    { id: "w1", name: "Realm", created: "", updated: "", counts: {} },
  ]);
  (api.createCampaign as any).mockResolvedValue({ id: "run" });
  (api.renameCampaign as any).mockResolvedValue({ id: "c1", name: "New" });
  (api.deleteCampaign as any).mockResolvedValue({ ok: true });
});

function renderView() {
  render(
    <MemoryRouter>
      <CampaignsView />
    </MemoryRouter>,
  );
}

test("lists campaigns", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "c1", name: "Run One", world: "w1", created: "", updated: "" },
  ]);
  renderView();
  await screen.findByText("Run One");
});

test("creating a campaign posts name + selected world and navigates", async () => {
  renderView();
  await screen.findByText("Realm"); // world option loaded
  fireEvent.change(screen.getByPlaceholderText(/campaign name/i), { target: { value: "Run One" } });
  fireEvent.click(screen.getByRole("button", { name: /create campaign/i }));
  await waitFor(() => expect(api.createCampaign).toHaveBeenCalledWith("Run One", "w1"));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/campaigns/run"));
});

test("create is disabled with no name", async () => {
  renderView();
  await screen.findByText("Realm");
  expect(screen.getByRole("button", { name: /create campaign/i })).toBeDisabled();
});

test("shows guidance when there are no worlds", async () => {
  (api.listWorlds as any).mockResolvedValue([]);
  renderView();
  await screen.findByText(/create a world first/i);
});

test("deletes a campaign after confirm", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "c1", name: "Doomed", world: "w1", created: "", updated: "" },
  ]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderView();
  await screen.findByText("Doomed");
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  await waitFor(() => expect(api.deleteCampaign).toHaveBeenCalledWith("c1"));
});
```

- [ ] **Step 2: Run — expect FAIL**

Run: `npm test -- CampaignsView`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `routes/CampaignsView.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type CampaignMeta, type WorldMeta } from "../api/client";
import { EditableRow } from "../components/EditableRow";

export default function CampaignsView() {
  const navigate = useNavigate();
  const [campaigns, setCampaigns] = useState<CampaignMeta[]>([]);
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);
  const [name, setName] = useState("");
  const [world, setWorld] = useState("");

  useEffect(() => {
    api.listCampaigns().then(setCampaigns);
    api.listWorlds().then((ws) => {
      setWorlds(ws);
      if (ws.length) setWorld(ws[0].id);
    });
  }, []);

  async function create() {
    const trimmed = name.trim();
    if (!trimmed || !world) return;
    const { id } = await api.createCampaign(trimmed, world);
    navigate(`/campaigns/${id}`);
  }

  async function rename(id: string, next: string) {
    await api.renameCampaign(id, next);
    setCampaigns(await api.listCampaigns());
  }

  async function remove(c: CampaignMeta) {
    if (!window.confirm(`Delete '${c.name}'?`)) return;
    await api.deleteCampaign(c.id);
    setCampaigns(await api.listCampaigns());
  }

  return (
    <div className="view">
      <h2>Campaigns</h2>

      {worlds.length === 0 ? (
        <p className="muted">
          Create a world first in <Link to="/worlds">Worlds</Link>, then start a campaign from it.
        </p>
      ) : (
        <div className="picker">
          <input
            placeholder="Campaign name…"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <select value={world} onChange={(e) => setWorld(e.target.value)} aria-label="World">
            {worlds.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
          <button className="primary" onClick={create} disabled={!name.trim()}>
            Create campaign
          </button>
        </div>
      )}

      <div className="list">
        {campaigns.map((c) => (
          <EditableRow
            key={c.id}
            label={c.name}
            subtitle={c.world}
            onSelect={() => navigate(`/campaigns/${c.id}`)}
            onRename={(next) => rename(c.id, next)}
            onDelete={() => remove(c)}
          />
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Add `.view` + `.picker` + `.muted` styles** to `frontend/src/index.css`:

```css
.view { padding: 24px; max-width: 720px; }
.view h2 { font-family: var(--font-display); }
.view .muted { color: var(--muted); }
.view .muted a { color: var(--accent); }
.picker { display: flex; gap: 8px; margin: 12px 0 20px; }
.picker input, .picker select { background: var(--surface); color: var(--fg); border: 1px solid var(--muted); border-radius: var(--radius); padding: 8px; }
.picker input { flex: 1; }
.list { display: flex; flex-direction: column; }
```

- [ ] **Step 5: Run — expect PASS**

Run: `npm test -- CampaignsView`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/CampaignsView.tsx frontend/src/routes/CampaignsView.test.tsx frontend/src/index.css
git commit -m "feat(frontend): CampaignsView (list + create-from-world)"
```

---

## Task 5: `WorldsView` — list + create/rename/delete

Worlds management for this phase: list worlds (with a small entity-count subtitle) and create/rename/delete them, so campaigns have a world to fork from. World *entity* editing is Phase 3.

**Files:**
- Create: `frontend/src/routes/WorldsView.tsx`
- Test: `frontend/src/routes/WorldsView.test.tsx`

**Interfaces:**
- Consumes: `api` (listWorlds/createWorld/renameWorld/deleteWorld), `EditableRow`.
- Produces: `default export WorldsView()` rendered at route `/worlds`.

- [ ] **Step 1: Write the failing tests** (`frontend/src/routes/WorldsView.test.tsx`)

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import WorldsView from "./WorldsView";

vi.mock("../api/client", () => ({
  api: {
    listWorlds: vi.fn(),
    createWorld: vi.fn(),
    renameWorld: vi.fn(),
    deleteWorld: vi.fn(),
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listWorlds as any).mockResolvedValue([]);
  (api.createWorld as any).mockResolvedValue({ id: "w1" });
  (api.renameWorld as any).mockResolvedValue({ id: "w1", name: "New" });
  (api.deleteWorld as any).mockResolvedValue({ ok: true });
});

function renderView() {
  render(
    <MemoryRouter>
      <WorldsView />
    </MemoryRouter>,
  );
}

test("lists worlds", async () => {
  (api.listWorlds as any).mockResolvedValue([
    { id: "w1", name: "Realm", created: "", updated: "", counts: { characters: 2, locations: 0, lore: 1 } },
  ]);
  renderView();
  await screen.findByText("Realm");
});

test("creating a world posts the name and refreshes the list", async () => {
  renderView();
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalled());
  fireEvent.change(screen.getByPlaceholderText(/world name/i), { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("button", { name: /create world/i }));
  await waitFor(() => expect(api.createWorld).toHaveBeenCalledWith("Realm"));
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalledTimes(2));
});

test("create is disabled with no name", async () => {
  renderView();
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalled());
  expect(screen.getByRole("button", { name: /create world/i })).toBeDisabled();
});

test("renames a world", async () => {
  (api.listWorlds as any).mockResolvedValue([
    { id: "w1", name: "Old", created: "", updated: "", counts: {} },
  ]);
  renderView();
  await screen.findByText("Old");
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameWorld).toHaveBeenCalledWith("w1", "New"));
});

test("deletes a world after confirm", async () => {
  (api.listWorlds as any).mockResolvedValue([
    { id: "w1", name: "Doomed", created: "", updated: "", counts: {} },
  ]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderView();
  await screen.findByText("Doomed");
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  await waitFor(() => expect(api.deleteWorld).toHaveBeenCalledWith("w1"));
});
```

- [ ] **Step 2: Run — expect FAIL**

Run: `npm test -- WorldsView`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `routes/WorldsView.tsx`**

```tsx
import { useEffect, useState } from "react";
import { api, type WorldMeta } from "../api/client";
import { EditableRow } from "../components/EditableRow";

function countLabel(counts: Record<string, number>): string {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  return total === 1 ? "1 entity" : `${total} entities`;
}

export default function WorldsView() {
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);
  const [name, setName] = useState("");

  useEffect(() => {
    api.listWorlds().then(setWorlds);
  }, []);

  async function create() {
    const trimmed = name.trim();
    if (!trimmed) return;
    await api.createWorld(trimmed);
    setName("");
    setWorlds(await api.listWorlds());
  }

  async function rename(id: string, next: string) {
    await api.renameWorld(id, next);
    setWorlds(await api.listWorlds());
  }

  async function remove(w: WorldMeta) {
    if (!window.confirm(`Delete world '${w.name}'? Campaigns already made from it keep their copies.`)) return;
    await api.deleteWorld(w.id);
    setWorlds(await api.listWorlds());
  }

  return (
    <div className="view">
      <h2>Worlds</h2>

      <div className="picker">
        <input placeholder="World name…" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="primary" onClick={create} disabled={!name.trim()}>
          Create world
        </button>
      </div>

      <div className="list">
        {worlds.map((w) => (
          <EditableRow
            key={w.id}
            label={w.name}
            subtitle={countLabel(w.counts)}
            onRename={(next) => rename(w.id, next)}
            onDelete={() => remove(w)}
          />
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run — expect PASS**

Run: `npm test -- WorldsView`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/WorldsView.tsx frontend/src/routes/WorldsView.test.tsx
git commit -m "feat(frontend): WorldsView (list + create/rename/delete)"
```

---

## Task 6: `App.tsx` router + nav shell; CSS cleanup

Wire the new views into a router with a nav bar, fetch config once (for `theme` + `keySet`), and remove the now-dead `.conv-*` CSS. After this, the whole app builds and runs on the new backend.

**Files:**
- Modify (rewrite): `frontend/src/App.tsx`
- Modify: `frontend/src/index.css` (remove `.conv-*` block; add `.topbar nav`)
- Modify: `frontend/src/test-setup.ts` (update the stale ChatView comment)

**Interfaces:**
- Consumes: `CampaignsView`, `CampaignView`, `WorldsView`, `ConfigView`, `ThemeProvider`, `api.getConfig`.
- Produces: routes `/` (CampaignsView), `/campaigns/:cid` (CampaignView), `/worlds` (WorldsView), `/config` (ConfigView).

- [ ] **Step 1: Rewrite `App.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import { ThemeProvider } from "./theme/ThemeProvider";
import { DEFAULT_THEME } from "./theme/themes";
import CampaignsView from "./routes/CampaignsView";
import CampaignView from "./routes/CampaignView";
import WorldsView from "./routes/WorldsView";
import ConfigView from "./routes/ConfigView";

export default function App() {
  const [theme, setTheme] = useState<string | null>(null);
  const [keySet, setKeySet] = useState(false);

  useEffect(() => {
    api
      .getConfig()
      .then((c) => {
        setTheme(c.theme);
        setKeySet(c.key_set);
      })
      .catch(() => setTheme(DEFAULT_THEME));
  }, []);

  if (theme === null) return null;

  return (
    <ThemeProvider initial={theme}>
      <div className="topbar">
        <Link to="/" style={{ fontWeight: 600 }}>
          ✦ grimoire
        </Link>
        <nav>
          <Link to="/">Campaigns</Link>
          <Link to="/worlds">Worlds</Link>
          <Link to="/config">Config</Link>
        </nav>
      </div>
      <Routes>
        <Route path="/" element={<CampaignsView />} />
        <Route path="/campaigns/:cid" element={<CampaignView keySet={keySet} />} />
        <Route path="/worlds" element={<WorldsView />} />
        <Route path="/config" element={<ConfigView />} />
      </Routes>
    </ThemeProvider>
  );
}
```

- [ ] **Step 2: Update `frontend/src/index.css`** — remove the dead `.conv-item`, `.conv-title`, `.conv-actions`, `.conv-rename` rules (lines defining `.conv-*`; `.sidebar button, .conv-item { … }` becomes `.sidebar button { … }`), and add nav spacing:

Replace the rule:
```css
.sidebar button, .conv-item {
  display: block; width: 100%; text-align: left; margin-bottom: 6px;
  background: transparent; color: var(--fg); border: 1px solid var(--muted);
  border-radius: var(--radius); padding: 6px 8px; cursor: pointer;
}
```
with:
```css
.sidebar button {
  display: block; width: 100%; text-align: left; margin-bottom: 6px;
  background: transparent; color: var(--fg); border: 1px solid var(--muted);
  border-radius: var(--radius); padding: 6px 8px; cursor: pointer;
}
```
Delete these four now-unused rules entirely:
```css
.conv-item { display: flex; align-items: center; gap: 6px; cursor: default; }
.conv-item.active { border-color: var(--accent); color: var(--accent); }
.conv-title { flex: 1; cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.conv-actions { display: flex; gap: 2px; flex: none; }
.conv-actions button { background: transparent; border: none; color: var(--muted); cursor: pointer; padding: 0 4px; font-size: 13px; line-height: 1; }
.conv-actions button:hover { color: var(--accent); }
.conv-rename { width: 100%; background: var(--bg); color: var(--fg); border: 1px solid var(--accent); border-radius: var(--radius); padding: 3px 6px; font-family: var(--font-body); }
```
And add a nav rule next to `.topbar a`:
```css
.topbar nav { display: flex; gap: 16px; }
```

- [ ] **Step 3: Update the stale comment in `frontend/src/test-setup.ts`**

```ts
import "@testing-library/jest-dom";

// jsdom doesn't implement Element.scrollTo; CampaignView's autoscroll effect calls it.
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {};
}
```

- [ ] **Step 4: Run the full test suite + typecheck/build — expect PASS**

Run (from `frontend/`): `npm test`
Expected: PASS — `client.test`, `EditableRow`, `CampaignView`, `CampaignsView`, `WorldsView`, plus the untouched `models.test`, `stream.test`, `ThemeProvider.test`, `ModelCombobox.test`.

Run: `npm run build`
Expected: `tsc -b` reports no type errors and `vite build` succeeds (no lingering imports of the deleted `ChatView` or conversation API).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/index.css frontend/src/test-setup.ts
git commit -m "feat(frontend): router + nav shell for campaigns/worlds; drop dead conv CSS"
```

---

## Self-Review

**Spec coverage (Section 4 — Frontend):**
- Top nav Campaigns / Worlds / Config → Task 6. ✓
- CampaignsView: list + "New campaign" (name + world picker) → Task 4. ✓
- CampaignView = ChatView scoped to a campaign: scenes sidebar (list/new/rename/delete) + transcript/input/SSE/retry → Task 3. ✓
- WorldsView: list + create → Task 5. ✓ (entity management explicitly deferred to Phase 3.)
- Shared `EditableRow` for the repeated rename/delete row → Task 2. ✓
- `api/client.ts` gains worlds/campaigns/scenes functions; old conversation functions removed → Task 1. ✓
- Theme tokens only; no hardcoded colors → all CSS uses `var(--*)`. ✓
- **Deferred (correctly out of this plan):** campaign-entities panel, incoming badge, `IncomingReview`, world push panel — these are Phase 3 per the Global Constraints. ✓

**Placeholder scan:** none — every step has complete code/commands.

**Type consistency:** `api.chat`/`api.retry` take `(cid, sid, …)` everywhere they're defined (Task 1) and called (Task 3 + client.test). `SceneMeta` uses `title`; `WorldMeta`/`CampaignMeta` use `name`; `EditableRow` `label`/`subtitle`/`onRename(next)`/`onDelete` are consistent across Tasks 2–5. Routes (`/`, `/campaigns/:cid`, `/worlds`, `/config`) match between `App.tsx` (Task 6) and the `useNavigate('/campaigns/${id}')` calls (Tasks 3–4). `createScene(cid)` (no title) is what CampaignView calls and what the client defines (title optional).

## Out of scope (Phase 3, separate plan)

- World/campaign entity (characters/locations/lore) management UI + the entity client methods.
- The sync review UI: campaign **Incoming** badge + `IncomingReview` (accept/reject per object, conflict side-by-side) + the world **push panel** (`GET /api/worlds/{wid}/campaigns`), plus the `incoming`/`accept`/`reject`/`worldCampaigns` client methods.
```
