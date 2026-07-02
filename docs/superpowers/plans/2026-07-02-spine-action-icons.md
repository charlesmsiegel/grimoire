# Spine Action Icons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Reroll/Edit into the spine column as ↻/✎ icon buttons with hovertext; re-anchor the guidance popover beside them.

**Architecture:** Pure frontend: restructure the message's left column into `.spine-col` (label + icons), delete the `.msg-actions` overlay, re-anchor `.reroll-pop` off the spine column. Behavior (popover flow, edit mode) is untouched.

**Tech Stack:** React + vitest.

**Spec:** `docs/superpowers/specs/2026-07-02-spine-action-icons-design.md`

## Global Constraints

- Icons: `↻` (`title="Reroll"`), `✎` (`title="Edit message"`); reveal on message hover/focus-within.
- Popover copy/behavior unchanged: placeholder `Guide the reroll (optional)…`, button `Reroll ▸`, Enter fires, empty = plain reroll, Esc closes.
- Zero border radius; tokens only.
- Run tests from `frontend/`: `npx vitest run`, `npx tsc -b`.

---

### Task 1: Spine column icons

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx` (message map JSX)
- Modify: `frontend/src/index.css` (`.msg-actions` block → `.spine-col`)
- Test: `frontend/src/routes/CampaignView.test.tsx`

- [ ] **Step 1: Re-target the tests (failing first)**

In `CampaignView.test.tsx`:
- In the three reroll tests, replace `screen.getByRole("button", { name: /^reroll$/i })` with `screen.getByTitle("Reroll")`.
- In `"editing a message saves and reloads"`, replace `screen.getAllByRole("button", { name: /edit/i })[0]` with `screen.getAllByTitle("Edit message")[0]`.
- In the popover-open assertion of the first reroll test, keep `getByRole("button", { name: /reroll ▸/i })` for the popover's submit button (unchanged).
- Add to the first reroll test, right after clicking the icon:

```tsx
  expect(screen.getByTitle("Reroll")).toBeInTheDocument(); // hovertext present
```

Run: `npx vitest run src/routes/CampaignView.test.tsx` — Expected: FAIL (`getByTitle("Reroll")` finds nothing; current button has no title).

- [ ] **Step 2: Implement the JSX**

In `CampaignView.tsx`, replace the message row's spine + actions markup:

```tsx
            <div className={`msg ${m.role}`} key={i}>
              <span className="spine-col">
                <span className="spine">{m.speaker ?? labels[m.role]}</span>
                {editing?.index !== i && !busy && (
                  <span className="spine-icons">
                    {m.role === "assistant" && i === messages.length - 1 && i > 0 && (
                      <button className="msg-edit" title="Reroll" aria-label="Reroll"
                              onClick={() => setRerollPrompt("")}>↻</button>
                    )}
                    <button className="msg-edit" title="Edit message" aria-label={`Edit message ${i + 1}`}
                            onClick={() => setEditing({ index: i, text: m.content })}>✎</button>
                  </span>
                )}
                {rerollPrompt !== null && m.role === "assistant" && i === messages.length - 1 && i > 0 && (
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
                )}
              </span>
              <div className="msg-body">
                {/* unchanged: edit form or RenderedMarkdown */}
              </div>
            </div>
```

(The old `.msg-actions` span after `.msg-body` is deleted; the streaming block's plain `<span className="spine">` stays as-is.)

- [ ] **Step 3: Replace the CSS**

In `index.css`, replace the `.msg-actions` block (and revert `.msg` padding):

```css
/* ---- spine column: vertical label + hover action icons ---- */
.spine-col { position: relative; display: flex; flex-direction: column; align-items: center; gap: 10px; flex: none; }
.spine-icons { display: flex; flex-direction: column; gap: 4px; opacity: 0; }
.msg:hover .spine-icons, .msg:focus-within .spine-icons { opacity: 1; }
.reroll-pop { position: absolute; left: 100%; bottom: 0; margin-left: 10px; z-index: 5;
  display: flex; background: var(--surface); border: var(--rw) solid var(--rule); box-shadow: var(--sh3); }
.reroll-pop input {
  width: 260px; background: transparent; color: var(--ink); border: none; outline: none;
  padding: 7px 10px; font-family: var(--fm); font-size: 12px;
}
.reroll-pop .btn-chrome { border: none; border-left: var(--rw) solid var(--rule); padding: 7px 12px; box-shadow: none; font-size: 11px; }
```

and change `.msg { … padding-bottom: 6px; }` back to no padding-bottom. Update `.msg-edit` font-size to 13px for legible glyphs (keep the rest of its rule).

- [ ] **Step 4: Run tests**

`npx vitest run src/routes/CampaignView.test.tsx && npx tsc -b && npx vitest run` — Expected: all PASS.

- [ ] **Step 5: Visual check**

In the running app (vite hot-reloads): hover the last assistant post — ↻ and ✎ appear under the name in the left column; hovertext shows; clicking ↻ opens the popover beside the spine; Esc closes.

- [ ] **Step 6: Commit**

```bash
git add frontend/src docs/superpowers
git commit -m "feat(transcript): reroll/edit become spine-column icons with hovertext"
```
