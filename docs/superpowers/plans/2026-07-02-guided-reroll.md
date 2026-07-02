# Guided Reroll Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reroll opens an inline popover for an optional guidance hint that steers the regeneration; message actions (Reroll/Edit) move to the bottom-right of a post.

**Architecture:** The `/regenerate` route gains an optional JSON body whose `guidance` is appended to the built context as one transient system message (never persisted to the transcript). The frontend gates `api.regenerate` behind a small popover anchored in `.msg-actions`; a single `rerollPrompt: string | null` state drives it.

**Tech Stack:** FastAPI + pytest (backend), React + vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-07-02-guided-reroll-design.md`

## Global Constraints

- Guidance is **transient** — never written to the scene transcript.
- Injected instruction text, verbatim: `Regenerate your previous reply. Guidance from the player: {guidance}`
- Popover copy: placeholder `Guide the reroll (optional)…`, button `Reroll ▸`.
- Codex design laws: zero border radius; components reference only `var(--…)` tokens; buttons set `color` explicitly.
- Run frontend tests **from `frontend/`**: `npx vitest run`, `npx tsc -b`. Backend: `backend/.venv/Scripts/python.exe -m pytest backend -q` from the repo root.
- Empty/whitespace-only guidance = plain reroll (no body posted).

---

### Task 1: Backend — regenerate accepts optional guidance

**Files:**
- Modify: `backend/src/grimoire/routes.py` (`post_regenerate`, ~line 1168; model classes near line 23)
- Test: `backend/tests/test_routes.py` (regenerate tests near line 709; `CapturingOpenRouter` at line 647)

**Interfaces:**
- Produces: `POST /api/campaigns/{cid}/scenes/{sid}/regenerate` accepting optional JSON `{"guidance": "<text>"}`. With non-empty guidance, the model request gains a final system message per the Global Constraints template. Task 2's client relies on exactly this body shape.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_routes.py` after `test_regenerate_excludes_the_dropped_post_from_the_prompt`:

```python
def test_regenerate_with_guidance_appends_a_system_steer(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "hi")
    store.scenes.append_message(cid, sid, "assistant", "old reply")
    cap = CapturingOpenRouter()
    client.app.dependency_overrides[routes.get_openrouter] = lambda: cap
    with client.stream("POST", f"/api/campaigns/{cid}/scenes/{sid}/regenerate",
                       json={"guidance": "make her angrier"}) as r:
        for _ in r.iter_lines():
            pass
    assert cap.messages[-1] == {
        "role": "system",
        "content": "Regenerate your previous reply. Guidance from the player: make her angrier",
    }
    # the dropped assistant reply still isn't in the prompt
    assert {"role": "assistant", "content": "old reply"} not in cap.messages
    # and the guidance is transient — not in the stored transcript
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert all("make her angrier" not in m["content"] for m in msgs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k guidance`
Expected: FAIL — `cap.messages[-1]` is the user turn, not the system steer.

- [ ] **Step 3: Implement**

In `backend/src/grimoire/routes.py`, add near the other request models (after `ConfigUpdate`, ~line 30):

```python
class RegenerateBody(BaseModel):
    guidance: str | None = None
```

Replace `post_regenerate`:

```python
@router.post("/campaigns/{cid}/scenes/{sid}/regenerate")
def post_regenerate(cid: str, sid: str, body: RegenerateBody | None = None,
                    client: OpenRouterClient = Depends(get_openrouter)):
    """Redo the most recent post: drop a trailing assistant reply, stream a fresh one."""
    scene = _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    msgs = scene["messages"]
    if not msgs:
        raise HTTPException(status_code=400, detail="nothing to regenerate")
    if msgs[-1]["role"] == "assistant":
        if len(msgs) == 1:
            raise HTTPException(status_code=400, detail="cannot regenerate the opening post")
        store.scenes.remove_last_message(cid, sid)
    messages = store.context.build_messages(cid, sid)
    guidance = (body.guidance or "").strip() if body else ""
    if guidance:
        messages.append({
            "role": "system",
            "content": f"Regenerate your previous reply. Guidance from the player: {guidance}",
        })
    return _chat_stream(cid, sid, messages, cfg, client)
```

- [ ] **Step 4: Run the backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all PASS (the existing no-body regenerate tests prove unchanged behavior).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(regenerate): optional guidance steers the reroll via a transient system message"
```

---

### Task 2: Frontend — guidance popover and bottom-right actions

**Files:**
- Modify: `frontend/src/api/client.ts` (`regenerate`, ~line 254)
- Modify: `frontend/src/routes/CampaignView.tsx` (`reroll` fn ~line 158, actions JSX ~line 350)
- Modify: `frontend/src/index.css` (`.msg-actions` block)
- Test: `frontend/src/routes/CampaignView.test.tsx` (reroll tests, lines 241–282)

**Interfaces:**
- Consumes: Task 1's `{ guidance }` body.
- Produces: `api.regenerate(cid: string, sid: string, onEvent: (e: ChatEvent) => void, guidance?: string)` — posts `{ guidance }` only when `guidance` is a non-empty string, otherwise `undefined` body (unchanged wire behavior).

- [ ] **Step 1: Update the reroll tests (failing first)**

In `frontend/src/routes/CampaignView.test.tsx`, replace the body of
`test("Reroll on the last assistant post replaces it with a fresh reply", …)` with the popover flow, and add two tests after it:

```tsx
test("Reroll on the last assistant post replaces it with a fresh reply", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "old reply" }] });
  (api.regenerate as any).mockImplementation(async (_c: string, _s: string, onEvent: any) => {
    onEvent({ delta: "fresh reply" });
  });
  renderCampaign();
  await screen.findByText("old reply");
  fireEvent.click(screen.getByRole("button", { name: /^reroll$/i }));
  // clicking Reroll opens the popover instead of firing immediately
  expect(api.regenerate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i })); // empty = plain reroll
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith("run", "s1", expect.any(Function)));
  await screen.findByText("fresh reply");
  expect(screen.queryByText("old reply")).toBeNull();
});

test("typed guidance is passed to regenerate", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "old reply" }] });
  renderCampaign();
  await screen.findByText("old reply");
  fireEvent.click(screen.getByRole("button", { name: /^reroll$/i }));
  const input = screen.getByPlaceholderText(/guide the reroll/i);
  fireEvent.change(input, { target: { value: "make her angrier" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), "make her angrier"));
});

test("Escape closes the reroll popover without firing", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "old reply" }] });
  renderCampaign();
  await screen.findByText("old reply");
  fireEvent.click(screen.getByRole("button", { name: /^reroll$/i }));
  fireEvent.keyDown(screen.getByPlaceholderText(/guide the reroll/i), { key: "Escape" });
  expect(screen.queryByPlaceholderText(/guide the reroll/i)).toBeNull();
  expect(api.regenerate).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run to verify failure**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx`
Expected: FAIL — no popover exists; the first click still fires `api.regenerate`.

- [ ] **Step 3: Implement**

`frontend/src/api/client.ts` — replace the `regenerate` entry:

```ts
  regenerate: (cid: string, sid: string, onEvent: (e: ChatEvent) => void, guidance?: string) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/regenerate`,
               guidance ? { guidance } : undefined, onEvent),
```

`frontend/src/routes/CampaignView.tsx` — state (next to `editing`):

```tsx
  const [rerollPrompt, setRerollPrompt] = useState<string | null>(null); // null = popover closed
```

Replace `reroll`:

```tsx
  async function reroll() {
    if (!activeId || busy) return;
    const guidance = (rerollPrompt ?? "").trim();
    setRerollPrompt(null);
    setMessages((m) => m.slice(0, -1));
    // omit the 4th argument entirely for a plain reroll (an explicit
    // undefined would change the mock call shape the tests assert)
    await runStream((onEvent) => guidance
      ? api.regenerate(cid, activeId!, onEvent, guidance)
      : api.regenerate(cid, activeId!, onEvent));
  }
```

Replace the actions span in the message map (the popover renders in place of the two buttons while open):

```tsx
              {editing?.index !== i && !busy && (
                <span className="msg-actions">
                  {m.role === "assistant" && i === messages.length - 1 && i > 0 && (
                    rerollPrompt !== null ? (
                      <span className="reroll-pop">
                        <input
                          autoFocus
                          placeholder="Guide the reroll (optional)…"
                          aria-label="Reroll guidance"
                          value={rerollPrompt}
                          onChange={(e) => setRerollPrompt(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") reroll();
                            if (e.key === "Escape") setRerollPrompt(null);
                          }}
                        />
                        <button className="btn-chrome" onClick={reroll}>Reroll ▸</button>
                      </span>
                    ) : (
                      <button className="msg-edit" onClick={() => setRerollPrompt("")}>Reroll</button>
                    )
                  )}
                  <button className="msg-edit" aria-label={`Edit message ${i + 1}`} title="Edit"
                          onClick={() => setEditing({ index: i, text: m.content })}>✎</button>
                </span>
              )}
```

`frontend/src/index.css` — replace the `.msg-actions` block (actions to bottom-right; popover skin; keep actions visible while the popover is open):

```css
/* ---- transcript message actions (hover, bottom-right) ---- */
.msg { padding-bottom: 6px; }
.msg-actions { position: absolute; bottom: 0; right: 0; display: flex; align-items: center; gap: 8px; opacity: 0; }
.msg:hover .msg-actions, .msg:focus-within .msg-actions { opacity: 1; }
.msg-actions:has(.reroll-pop) { opacity: 1; }
.msg-actions .msg-edit { position: static; opacity: 1; }
.reroll-pop { display: flex; background: var(--surface); border: var(--rw) solid var(--rule); box-shadow: var(--sh3); }
.reroll-pop input {
  width: 260px; background: transparent; color: var(--ink); border: none; outline: none;
  padding: 7px 10px; font-family: var(--fm); font-size: 12px;
}
.reroll-pop .btn-chrome { border: none; border-left: var(--rw) solid var(--rule); padding: 7px 12px; box-shadow: none; font-size: 11px; }
```

(The old block being replaced is the one containing `.msg-actions { position: absolute; top: 0; right: 0; … }`.)

- [ ] **Step 4: Run tests**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx && npx tsc -b`
Expected: PASS (all 3 new/updated reroll tests plus the untouched ones).

Then the full suite: `npx vitest run` — Expected: PASS.

- [ ] **Step 5: Visual check**

With the app running (backend 8173 + `npx vite` from `frontend/`), open a campaign scene, hover the last assistant post: Reroll/✎ appear at the **bottom-right**; clicking Reroll swaps in the joined input + `REROLL ▸` popover; Esc restores the buttons. Verify in Astral (current saved theme) that the popover renders with hairline border + glow shadow.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/routes/CampaignView.tsx frontend/src/routes/CampaignView.test.tsx frontend/src/index.css
git commit -m "feat(transcript): guided reroll popover; message actions move to bottom-right"
```
