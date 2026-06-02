# Card Icon Bar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every card a bottom icon bar (shared `CardIconBar`), starting with a Delete (🗑) icon on every file-backed card and chat posts, and enforce the convention with docs + an ESLint rule.

**Architecture:** A new `CardIconBar` React component renders a right-aligned row of icon buttons at the bottom of a card; `deleteAction()` builds the standard trash. Every existing bespoke delete is re-expressed through it; cards without a delete render an empty (invisible) bar. A custom ESLint rule `no-bespoke-delete` bans raw delete buttons outside `CardIconBar`. One backend endpoint (`DELETE /library/style-guides/{id}`) is added because it was the only missing delete.

**Tech Stack:** React 18 + TypeScript (Vitest + React Testing Library), flat ESLint config (typescript-eslint), Python 3.12 + FastAPI + pytest (backend), `gh` CLI for the tracking issue.

**Reference spec:** `docs/superpowers/specs/2026-06-02-card-icon-bar-design.md`

---

## File Structure

**New files**
- `frontend/src/components/CardIconBar.tsx` — the component + `CardIconAction` type.
- `frontend/src/components/cardActions.ts` — `DELETE_ICON` + `deleteAction()` helper (kept out of the `.tsx` so `react-refresh/only-export-components` stays quiet).
- `frontend/src/components/__tests__/CardIconBar.test.tsx` — component unit test.
- `frontend/eslint-rules/no-bespoke-delete.js` — the lint rule.
- `frontend/eslint-rules/index.js` — local plugin wrapper.
- `frontend/eslint-rules/__tests__/no-bespoke-delete.test.ts` — rule test (via `Linter`).

**Modified — infra/docs**
- `frontend/src/index.css` — add `.card-icon-bar` / `.card-icon-button`; remove dead `.character-card`.
- `frontend/eslint.config.js` — register the local plugin + rule (last task).
- `CLAUDE.md`, `AGENTS.md` — document the rule.
- Backend: `backend/src/grimoire/library/service.py`, `backend/src/grimoire/api/library.py`, `backend/tests/...`, `frontend/src/api/library/*` — style-guide delete.

**Modified — conversions**
CampaignsView, WorldsListView, EntityListView, ImagePresetsView, CalendarsView, HolidaySetsView, StyleGuidesView, TimelineView, PostItem, CastView (`frontend/src/api/campaign/api.ts` for `removePc`), and the empty-bar set (PluginsView, library MechanicsView, LedgerView, ContentBrowser, campaign MechanicsView, WorldView, ProviderCard, SceneSuggestionView, WhyCharacterPanel).

---

## Task 1: Create the icon-library tracking issue

**Files:** none (creates a GitHub issue).

- [ ] **Step 1: Create the issue and capture its number**

Run:
```bash
gh issue create \
  --title "Create a shared SVG icon library" \
  --body "Replace ad-hoc emoji/glyph icons with inline SVG icon components (themeable via currentColor).

Icons used across the app today (to be catalogued/replaced):
- 🗑 delete (CardIconBar.DELETE_ICON; existing inline SVG trash in WorldsListView is the seed)
- ✎ edit, 🔄 regenerate, 🎯 guided regenerate, ➤ continue, 🌐 translate (PostItem post-icon-btn)
- ✦ language models, ⊕ embeddings, ◎ image generation (ProviderCard icons)
- ✕ discard (AuxInflightBadge), + new, … busy

First consumer: the trash icon in CardIconBar. Start by extracting the WorldsListView SVG trash into a TrashIcon component."
```
Record the printed issue number; later tasks reference it as `#<ICON_ISSUE>`.

- [ ] **Step 2: No commit** (issue creation has no repo changes).

---

## Task 2: `CardIconBar` component + helper (TDD)

**Files:**
- Create: `frontend/src/components/CardIconBar.tsx`
- Create: `frontend/src/components/cardActions.ts`
- Test: `frontend/src/components/__tests__/CardIconBar.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/__tests__/CardIconBar.test.tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { CardIconBar } from "../CardIconBar";
import { deleteAction } from "../cardActions";

describe("CardIconBar", () => {
  it("renders an action as an icon button with an accessible name and fires onClick", () => {
    const onClick = vi.fn();
    render(<CardIconBar actions={[deleteAction({ onClick, label: "Delete world" })]} />);
    const btn = screen.getByRole("button", { name: "Delete world" });
    expect(btn).toHaveAttribute("title", "Delete world");
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("disables the button while busy", () => {
    render(<CardIconBar actions={[deleteAction({ onClick: () => {}, busy: true })]} />);
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("renders an empty toolbar with no buttons when actions is empty", () => {
    render(<CardIconBar actions={[]} />);
    expect(screen.getByRole("toolbar", { name: "Card actions" })).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
```

- [ ] **Step 2: Run it and confirm failure**

Run: `cd frontend && pnpm test -- CardIconBar`
Expected: FAIL — cannot import `../CardIconBar` / `../cardActions`.

- [ ] **Step 3: Implement the component and helper**

```tsx
// frontend/src/components/CardIconBar.tsx
export interface CardIconAction {
  key: string;
  /** Emoji/glyph for now; see the icon-library issue. */
  icon: string;
  /** Becomes both aria-label and title. */
  label: string;
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
  variant?: "default" | "danger";
}

/**
 * The action bar pinned to a card's bottom edge. Every card renders one — even
 * with no actions (the empty bar is invisible and reserved for future icons).
 */
export function CardIconBar({ actions }: { actions: CardIconAction[] }) {
  return (
    <div className="card-icon-bar" role="toolbar" aria-label="Card actions">
      {actions.map((a) => (
        <button
          key={a.key}
          type="button"
          className={a.variant === "danger" ? "card-icon-button danger" : "card-icon-button"}
          aria-label={a.label}
          title={a.label}
          disabled={a.disabled || a.busy}
          onClick={a.onClick}
        >
          <span aria-hidden="true">{a.busy ? "…" : a.icon}</span>
        </button>
      ))}
    </div>
  );
}
```

```ts
// frontend/src/components/cardActions.ts
import type { CardIconAction } from "./CardIconBar";

// TODO(#<ICON_ISSUE>): replace emoji icons with shared SVG icon components.
export const DELETE_ICON = "🗑";

/** Build the standard Delete (trash) action for a card icon bar. */
export function deleteAction(opts: {
  onClick: () => void;
  label?: string;
  busy?: boolean;
  disabled?: boolean;
}): CardIconAction {
  return {
    key: "delete",
    icon: DELETE_ICON,
    label: opts.label ?? "Delete",
    variant: "danger",
    onClick: opts.onClick,
    busy: opts.busy,
    disabled: opts.disabled,
  };
}
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `cd frontend && pnpm test -- CardIconBar`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CardIconBar.tsx frontend/src/components/cardActions.ts frontend/src/components/__tests__/CardIconBar.test.tsx
git commit -m "feat(frontend): add shared CardIconBar component"
```

---

## Task 3: `CardIconBar` styles

**Files:**
- Modify: `frontend/src/index.css` (append a new block near the other card styles, e.g. after the `.campaign-card-delete` rules around line 996)

- [ ] **Step 1: Add the CSS**

```css
/* ----- Card icon bar (shared per-card action row) ----- */
.card-icon-bar {
  display: flex;
  justify-content: flex-end;
  gap: 0.25rem;
  margin-top: auto; /* pin to the card's bottom edge in flex-column cards */
}
.card-icon-bar:not(:empty) {
  padding-top: 0.4rem;
  border-top: 1px solid var(--border, rgba(255, 255, 255, 0.08));
}
.card-icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.9rem;
  height: 1.9rem;
  padding: 0;
  font-size: 0.95rem;
  line-height: 1;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  cursor: pointer;
  color: inherit;
}
.card-icon-button:hover:not(:disabled) {
  background: var(--surface-hover, rgba(255, 255, 255, 0.08));
}
.card-icon-button:disabled {
  opacity: 0.5;
  cursor: default;
}
.card-icon-button.danger:hover:not(:disabled) {
  background: rgba(220, 60, 60, 0.18);
  border-color: rgba(220, 60, 60, 0.4);
}
.card-icon-button:focus-visible {
  outline: 2px solid var(--focus-ring, #6ea8fe);
  outline-offset: 1px;
}
```

> Note: `--border`, `--surface-hover`, `--focus-ring` use fallbacks so they work even if those custom properties are not defined. Confirm against existing `:root` vars and drop the fallbacks if the vars exist.

- [ ] **Step 2: Verify the build still compiles**

Run: `cd frontend && pnpm build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/index.css
git commit -m "feat(frontend): style the card icon bar"
```

---

## Task 4: Remove dead `.character-card` CSS

**Files:**
- Modify: `frontend/src/index.css` (delete the `.character-card` block — the rules currently around lines 3043–3074: `.character-card`, `.character-card legend`, `.character-card label`, `.character-card input, .character-card textarea`)

- [ ] **Step 1: Confirm it is unused**

Run: `cd frontend && grep -rn "character-card" src --include=*.tsx | grep -v "why-character-card"`
Expected: no `className`-applying matches (only `why-character-card` and test ids remain). If any real usage appears, STOP and reassess.

- [ ] **Step 2: Delete the `.character-card` rules** (leave `.why-character-card*` untouched).

- [ ] **Step 3: Verify build**

Run: `cd frontend && pnpm build`
Expected: success.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/index.css
git commit -m "refactor(frontend): remove dead .character-card CSS"
```

---

## Task 5: ESLint rule `no-bespoke-delete` (TDD, not yet wired)

The rule flags `<button>`/`<a>`/`<Link>` that look like a delete control, outside `CardIconBar.tsx` and `*Dialog*`/`*Confirm*` files. It is **created and tested here** but only registered in `eslint.config.js` in the final task, so the build stays green until conversions are done.

**Files:**
- Create: `frontend/eslint-rules/no-bespoke-delete.js`
- Create: `frontend/eslint-rules/index.js`
- Test: `frontend/eslint-rules/__tests__/no-bespoke-delete.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/eslint-rules/__tests__/no-bespoke-delete.test.ts
import { describe, expect, it } from "vitest";
import { Linter } from "eslint";
import tsParser from "@typescript-eslint/parser";

import localPlugin from "../index.js";

function lint(code: string, filename: string) {
  const linter = new Linter({ configType: "flat" });
  return linter.verify(
    code,
    {
      files: ["**/*.tsx"],
      languageOptions: {
        parser: tsParser,
        parserOptions: { ecmaFeatures: { jsx: true }, ecmaVersion: 2022, sourceType: "module" },
      },
      plugins: { local: localPlugin },
      rules: { "local/no-bespoke-delete": "error" },
    },
    { filename },
  );
}

describe("no-bespoke-delete", () => {
  it("flags a bespoke delete button by className", () => {
    const msgs = lint(`const x = <button className="campaign-card-delete">Delete</button>;`, "Foo.tsx");
    expect(msgs).toHaveLength(1);
    expect(msgs[0].ruleId).toBe("local/no-bespoke-delete");
  });

  it("flags a button labelled Delete via aria-label", () => {
    const msgs = lint(`const x = <button aria-label="Delete world" />;`, "Foo.tsx");
    expect(msgs).toHaveLength(1);
  });

  it("flags a button whose only child is the trash emoji", () => {
    const msgs = lint(`const x = <button title="Remove">🗑</button>;`, "Foo.tsx");
    expect(msgs).toHaveLength(1);
  });

  it("does not flag inside CardIconBar.tsx", () => {
    const msgs = lint(`const x = <button className="card-icon-button danger">🗑</button>;`, "components/CardIconBar.tsx");
    expect(msgs).toHaveLength(0);
  });

  it("does not flag confirm buttons in *Dialog* files", () => {
    const msgs = lint(`const x = <button aria-label="Confirm delete">Delete</button>;`, "ConfirmDestructiveDialog.tsx");
    expect(msgs).toHaveLength(0);
  });

  it("does not flag non-delete buttons", () => {
    const msgs = lint(`const x = <button aria-label="Edit world">Edit</button>;`, "Foo.tsx");
    expect(msgs).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run it and confirm failure**

Run: `cd frontend && pnpm test -- no-bespoke-delete`
Expected: FAIL — cannot import `../index.js`.

- [ ] **Step 3: Implement the rule**

```js
// frontend/eslint-rules/no-bespoke-delete.js
/** @type {import("eslint").Rule.RuleModule} */
const rule = {
  meta: {
    type: "problem",
    docs: {
      description:
        "Disallow bespoke delete/remove buttons; route deletes through CardIconBar's deleteAction.",
    },
    schema: [],
    messages: {
      bespoke:
        "Bespoke delete control. Render deletes through CardIconBar (deleteAction) instead of a raw <{{tag}}>.",
    },
  },
  create(context) {
    const filename = context.filename ?? context.getFilename();
    const norm = filename.replace(/\\/g, "/");
    // Exempt the sanctioned implementation and confirm dialogs.
    if (/\/CardIconBar\.tsx$/.test(norm) || /(Dialog|Confirm)[^/]*\.tsx?$/.test(norm)) {
      return {};
    }

    const TAGS = new Set(["button", "a", "Link"]);
    const isDeleteText = (s) => /^\s*(delete|remove)\b/i.test(s) || s.includes("🗑");

    return {
      JSXElement(node) {
        const open = node.openingElement;
        if (open.name.type !== "JSXIdentifier" || !TAGS.has(open.name.name)) return;

        let hit = false;
        for (const attr of open.attributes) {
          if (attr.type !== "JSXAttribute" || !attr.name) continue;
          const attrName = attr.name.name;
          const v = attr.value;
          const str =
            v && v.type === "Literal" && typeof v.value === "string"
              ? v.value
              : v && v.type === "JSXExpressionContainer" && v.expression.type === "Literal"
                ? String(v.expression.value)
                : null;
          if (str === null) continue;
          if (attrName === "className" && /delete/i.test(str)) hit = true;
          if ((attrName === "aria-label" || attrName === "title") && isDeleteText(str)) hit = true;
        }
        if (!hit) {
          // Trash emoji as a direct text child.
          for (const child of node.children) {
            if (child.type === "JSXText" && child.value.includes("🗑")) hit = true;
          }
        }
        if (hit) {
          context.report({ node: open, messageId: "bespoke", data: { tag: open.name.name } });
        }
      },
    };
  },
};

export default rule;
```

```js
// frontend/eslint-rules/index.js
import noBespokeDelete from "./no-bespoke-delete.js";

export default {
  rules: {
    "no-bespoke-delete": noBespokeDelete,
  },
};
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `cd frontend && pnpm test -- no-bespoke-delete`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/eslint-rules
git commit -m "feat(frontend): add no-bespoke-delete eslint rule (not yet wired)"
```

---

## Task 6: Backend — `DELETE /library/style-guides/{id}`

**Files:**
- Modify: `backend/src/grimoire/library/service.py` (add `delete_style_guide`, mirroring `delete_image_preset` at ~line 486)
- Modify: `backend/src/grimoire/api/library.py` (add the route, mirroring `delete_image_preset` at ~line 590)
- Test: `backend/tests/` — add to the existing library style-guide test module (find with the grep in Step 1)

- [ ] **Step 1: Locate the existing style-guide test module**

Run: `cd backend && grep -rln "style_guide\|style-guides" tests`
Pick the module that already covers create/update of style guides; add the test there.

- [ ] **Step 2: Write the failing test** (adapt fixtures to the chosen module's style)

```python
async def test_delete_style_guide_removes_file(library_service):
    await library_service.create_style_guide("doomed", name="Doomed Guide")
    assert await library_service.get_style_guide("doomed")  # exists

    await library_service.delete_style_guide("doomed")

    with pytest.raises(LibraryNotFoundError):
        await library_service.get_style_guide("doomed")
```

- [ ] **Step 3: Run it and confirm failure**

Run: `cd backend && uv run pytest <path>::test_delete_style_guide_removes_file -v`
Expected: FAIL — `LibraryService` has no `delete_style_guide`.

- [ ] **Step 4: Implement the service method** (paste right after `delete_image_preset`)

```python
    async def delete_style_guide(self, id: str, *, source: str = "user") -> None:
        library_id = f"style-guides/{id}"
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"style guide {id!r} not found")
        await self.store.delete_library_file(library_id=library_id, source=source)
```

- [ ] **Step 5: Add the API route** (paste after `update_style_guide`, before the image-preset section)

```python
@router.delete("/library/style-guides/{guide_id}", status_code=204)
async def delete_style_guide(
    guide_id: str,
    library: LibraryDep,
    source: str = "user",
) -> None:
    try:
        await library.delete_style_guide(guide_id, source=source)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
```

- [ ] **Step 6: Run the test + lint**

Run: `cd backend && uv run pytest <path>::test_delete_style_guide_removes_file -v && uv run ruff check`
Expected: PASS, no lint errors.

- [ ] **Step 7: Add the frontend API method**

Find the module defining the other style-guide client calls:
Run: `cd frontend && grep -rln "style-guides" src/api`
In that module, add (mirroring `deleteImagePreset`):

```ts
deleteStyleGuide: (id: string) =>
  api.delete<void>(`/api/library/style-guides/${enc(id)}`),
```
Match the file's existing `api`/`enc` helper names exactly.

- [ ] **Step 8: Typecheck**

Run: `cd frontend && pnpm typecheck`
Expected: success.

- [ ] **Step 9: Commit**

```bash
git add backend/src/grimoire/library/service.py backend/src/grimoire/api/library.py backend/tests frontend/src/api
git commit -m "feat(library): add style-guide delete endpoint + client"
```

---

## Task 7: Convert CampaignsView delete → CardIconBar (reference conversion)

**Files:**
- Modify: `frontend/src/routes/CampaignsView.tsx` (the `CampaignCard` component, lines ~70–101)
- Test: `frontend/src/routes/__tests__/` — add `CampaignsView` delete test if none exists; otherwise extend.

- [ ] **Step 1: Write/extend a test asserting the trash triggers delete**

```tsx
// in a CampaignsView test (mock the api module's deleteCampaign + window.confirm)
it("deletes a campaign via the card icon bar", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  // ...render CampaignsView with one campaign named "Test Camp"...
  fireEvent.click(await screen.findByRole("button", { name: /delete campaign test camp/i }));
  await waitFor(() => expect(deleteCampaignMock).toHaveBeenCalled());
});
```
(Use the existing test harness/mocks in the file if present; otherwise model it on `CardFilters.test.tsx` for render + `vi.mock` for `../api/...`.)

- [ ] **Step 2: Run it and confirm failure**

Run: `cd frontend && pnpm test -- CampaignsView`
Expected: FAIL — the accessible name still says "Delete campaign Test Camp" on a non-bar button, or the test cannot find the bar. (If it passes incidentally because the label is unchanged, proceed — the refactor still must keep the test green.)

- [ ] **Step 3: Replace the bespoke delete with the bar**

Add import at top: `import { CardIconBar } from "../components/CardIconBar";` and `import { deleteAction } from "../components/cardActions";`

Replace lines ~83–98 (the `campaign-card-actions` div) with:

```tsx
      <div className="campaign-card-actions">
        <button type="button" onClick={() => onFork(node.campaign)}>
          Fork
        </button>
        <Link to={`/campaigns/${node.campaign.id}/settings`}>Settings</Link>
      </div>
      <CardIconBar
        actions={[
          deleteAction({
            onClick: () => onDelete(node.campaign),
            label: `Delete campaign ${node.campaign.name}`,
            busy: busyDeleting,
          }),
        ]}
      />
```

Also make the card a flex column so the bar sinks to the bottom — confirm `.campaign-card` is `display:flex; flex-direction:column`; if not, add those two declarations to the `.campaign-card` rule in `index.css`.

- [ ] **Step 4: Run the test + typecheck**

Run: `cd frontend && pnpm test -- CampaignsView && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/CampaignsView.tsx frontend/src/index.css frontend/src/routes/__tests__
git commit -m "feat(frontend): campaign card delete via CardIconBar"
```

---

## Task 8: Convert WorldsListView (SVG trash → bar)

**Files:** Modify `frontend/src/routes/library/WorldsListView.tsx` (lines ~259–282)

- [ ] **Step 1: Add imports**

`import { CardIconBar } from "../../components/CardIconBar";`
`import { deleteAction } from "../../components/cardActions";`

- [ ] **Step 2: Replace the `library-card-actions` div (the SVG trash button) with**

```tsx
              <CardIconBar
                actions={[
                  deleteAction({
                    onClick: () => onDelete(s.id, s.name || s.id),
                    label: `Delete world ${s.name || s.id}`,
                  }),
                ]}
              />
```

- [ ] **Step 3: Verify**

Run: `cd frontend && pnpm test -- WorldsListView && pnpm typecheck`
Expected: PASS (update the existing `WorldsListView.test.tsx` query if it looked for the SVG; the button is still found by name "Delete world …").

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/library/WorldsListView.tsx frontend/src/routes/library/__tests__
git commit -m "feat(frontend): world card delete via CardIconBar"
```

---

## Task 9: Convert EntityListView (text Delete → bar, keep Convert)

**Files:** Modify `frontend/src/routes/library/EntityListView.tsx` (lines ~304–328)

- [ ] **Step 1: Add imports** (`../../components/CardIconBar`, `../../components/cardActions`).

- [ ] **Step 2: Replace the trailing `Delete` button (lines ~317–327) with a bar; keep the lore `Convert` button where it is**

```tsx
                <CardIconBar
                  actions={[
                    deleteAction({
                      onClick: () => onDelete(id, name),
                      label: `Delete ${name}`,
                    }),
                  ]}
                />
```
(The `Convert` button stays as a `library-card-action`; only the delete moves.)

- [ ] **Step 3: Verify**

Run: `cd frontend && pnpm test -- EntityListView && pnpm typecheck`
Expected: PASS (the existing `EntityListView.test.tsx` finds delete by role/name).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/library/EntityListView.tsx frontend/src/routes/library/__tests__
git commit -m "feat(frontend): entity card delete via CardIconBar"
```

---

## Task 10: ImagePresetsView — add delete → bar

**Files:** Modify `frontend/src/routes/library/ImagePresetsView.tsx`

- [ ] **Step 1: Wire a delete handler.** The list component needs access to `deleteImagePreset` + a reload. Read the file's data-loading (it uses an `AsyncBoundary` with `reload`). Add a handler near the list:

```tsx
async function handleDelete(id: string, name: string) {
  if (!window.confirm(`Delete image preset "${name}"? This cannot be undone.`)) return;
  await libraryApi.deleteImagePreset(id);   // match the actual imported api symbol
  reload();
}
```
(Confirm the imported client object name — `deleteImagePreset` lives in `api/library/worlds.ts`; import it the same way other calls in this file are imported.)

- [ ] **Step 2: Add imports + replace the `library-card-actions` div (lines ~50–54) to keep Edit and add the bar**

```tsx
              <div className="library-card-actions">
                <Link to={`/library/image-presets/${encodeURIComponent(p.asset_id)}/edit`}>
                  Edit
                </Link>
              </div>
              <CardIconBar
                actions={[
                  deleteAction({
                    onClick: () => void handleDelete(p.asset_id, p.name || p.asset_id),
                    label: `Delete image preset ${p.name || p.asset_id}`,
                  }),
                ]}
              />
```

- [ ] **Step 3: Verify**

Run: `cd frontend && pnpm typecheck && pnpm test -- ImagePresets`
Expected: success (add a small render+click test if none exists, modelled on Task 7 Step 1).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/library/ImagePresetsView.tsx
git commit -m "feat(frontend): image preset delete via CardIconBar"
```

---

## Task 11: CalendarsView — add delete to custom calendars (built-ins: empty bar)

**Files:** Modify `frontend/src/routes/library/CalendarsView.tsx`

- [ ] **Step 1: Add a delete handler** using `deleteCalendar` (from `api/library/calendars.ts`) + reload, modelled on Task 10 Step 1 (confirm copy: `Delete calendar "<name>"?`).

- [ ] **Step 2: Built-in cards (lines ~112–119): add an empty bar** before `</li>`:

```tsx
              <CardIconBar actions={[]} />
```

- [ ] **Step 3: Custom cards (lines ~128–137): keep Edit, add the trash bar** after the `library-card-actions` div:

```tsx
                  <CardIconBar
                    actions={[
                      deleteAction({
                        onClick: () => void handleDelete(c.id, c.name || c.id),
                        label: `Delete calendar ${c.name || c.id}`,
                      }),
                    ]}
                  />
```

- [ ] **Step 4: Add imports + verify**

Run: `cd frontend && pnpm typecheck && pnpm test -- Calendars`
Expected: success (extend `CalendarsView.test.tsx`).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/CalendarsView.tsx frontend/src/routes/library/__tests__
git commit -m "feat(frontend): custom calendar delete via CardIconBar"
```

---

## Task 12: HolidaySetsView — add delete to custom sets (built-ins: empty bar)

**Files:** Modify `frontend/src/routes/library/HolidaySetsView.tsx`

- [ ] **Step 1: Add a delete handler** using `deleteHolidaySet` + reload (confirm copy: `Delete holiday set "<name>"?`).

- [ ] **Step 2: Built-in cards (lines ~102–109): add `<CardIconBar actions={[]} />`** before `</li>`.

- [ ] **Step 3: Custom cards (lines ~118–127): keep Edit, add the trash bar** after `library-card-actions`:

```tsx
                  <CardIconBar
                    actions={[
                      deleteAction({
                        onClick: () => void handleDelete(s.id, s.name || s.id),
                        label: `Delete holiday set ${s.name || s.id}`,
                      }),
                    ]}
                  />
```

- [ ] **Step 4: Add imports + verify**

Run: `cd frontend && pnpm typecheck && pnpm test -- Holiday`
Expected: success.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/HolidaySetsView.tsx
git commit -m "feat(frontend): custom holiday set delete via CardIconBar"
```

---

## Task 13: StyleGuidesView — add delete → bar (uses Task 6 endpoint)

**Files:** Modify `frontend/src/routes/library/StyleGuidesView.tsx` (lines ~60–74)

- [ ] **Step 1: Add a delete handler** using the new `deleteStyleGuide` client (Task 6 Step 7) + reload (confirm copy: `Delete style guide "<name>"?`).

- [ ] **Step 2: Keep Edit, add the trash bar** after the `library-card-actions` div:

```tsx
              <CardIconBar
                actions={[
                  deleteAction({
                    onClick: () => void handleDelete(g.asset_id, g.name || g.asset_id),
                    label: `Delete style guide ${g.name || g.asset_id}`,
                  }),
                ]}
              />
```

- [ ] **Step 3: Add imports + verify**

Run: `cd frontend && pnpm typecheck && pnpm test -- StyleGuides`
Expected: success.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/library/StyleGuidesView.tsx
git commit -m "feat(frontend): style guide delete via CardIconBar"
```

---

## Task 14: TimelineView — scene delete → bar (de-nest from card button)

The card root is a `<button className="timeline-card">`, so the bar must live in the wrapping `<li className="timeline-item">`, not inside the button.

**Files:** Modify `frontend/src/routes/campaign/TimelineView.tsx`

- [ ] **Step 1: Thread a delete down to `SceneCard`.** In `TimelineView`, add a handler and pass it in:

```tsx
const handleDeleteScene = (sceneId: string, title: string) => {
  if (!window.confirm(`Delete scene "${title}"? This removes it and its posts. This cannot be undone.`)) return;
  void campaignApi.deleteScene(campaignId, sceneId).then(() => state.reload());
};
```
Add `onDelete={() => handleDeleteScene(scene.id, scene.title || scene.slug)}` to the `<SceneCard .../>` props (line ~121–131) and to `SceneCard`'s prop type (line ~162–176): `onDelete: () => void;`.

- [ ] **Step 2: Render the bar inside the `<li>`, after the `</button>`** (around line 214):

```tsx
      </button>
      <CardIconBar
        actions={[deleteAction({ onClick: onDelete, label: `Delete scene ${scene.title || scene.slug}` })]}
      />
    </li>
```

- [ ] **Step 3: Add imports** (`../../components/CardIconBar`, `../../components/cardActions`).

- [ ] **Step 4: Verify**

Run: `cd frontend && pnpm typecheck && pnpm test -- Timeline`
Expected: success (add a render+click test if none exists).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/campaign/TimelineView.tsx
git commit -m "feat(frontend): scene delete via CardIconBar in timeline"
```

---

## Task 15: PostItem — migrate the existing icon bar onto CardIconBar

PostItem already has `post-actions-icons` with `post-icon-btn` buttons (edit ✎, regenerate 🔄, guided 🎯, continue ➤, translate 🌐, delete 🗑). Re-express the whole row as a `CardIconBar` so the delete is no longer a bespoke button.

**Files:** Modify `frontend/src/routes/campaign/PostItem.tsx` (lines ~320–399; the confirm dialog at ~401–433 stays)

- [ ] **Step 1: Add imports** (`../../components/CardIconBar`, `../../components/cardActions`).

- [ ] **Step 2: Replace the `<div className="post-actions post-actions-icons"> … </div>` block with a computed actions array + bar**

```tsx
      {campaignId && editDraft === null && (
        <CardIconBar
          actions={[
            ...(canEdit
              ? [{ key: "edit", icon: "✎", label: "Edit", onClick: () => setEditDraft(displayBody) }]
              : []),
            ...(canRegenerate
              ? [
                  { key: "regenerate", icon: "🔄", label: "Regenerate", disabled: busy, onClick: () => void regenerate() },
                  { key: "guided", icon: "🎯", label: "Guided regenerate…", disabled: busy, onClick: () => setGuidedHint("") },
                ]
              : []),
            ...(canContinue
              ? [{
                  key: "continue", icon: "➤", label: "Continue", disabled: auxBusy,
                  onClick: () => {
                    if (continueCandidates.length === 1) void runContinue(continueCandidates[0]!);
                    else setAuxForm({ kind: "continue", characterRef: continueCandidates[0]! });
                  },
                }]
              : []),
            { key: "translate", icon: "🌐", label: "Translate…", disabled: auxBusy, onClick: () => setAuxForm({ kind: "translate", targetLanguage: "" }) },
            ...(canDelete
              ? [deleteAction({ onClick: () => setConfirmingDelete(true), label: "Delete post", busy: deleteBusy })]
              : []),
          ]}
        />
      )}
```

> The `CardIconAction[]` literal above is type-checked against the exported interface — keep `key`/`icon`/`label`/`onClick` names exact.

- [ ] **Step 3: Keep the existing `post-delete-confirm` dialog block unchanged** (it is in a `*Confirm*`-ish inline region but lives in `PostItem.tsx`; the rule would flag its `post-delete-confirm-btn` "Delete" button — see Step 4).

- [ ] **Step 4: Exempt the confirm button.** The confirm-dialog "Delete" button (line ~414–422) is a legitimate confirm action, not a card control. Add directly above it:

```tsx
            {/* eslint-disable-next-line local/no-bespoke-delete -- confirm-dialog action, not a card control */}
```

- [ ] **Step 5: Optionally drop now-unused `post-icon-btn`/`post-actions-icons` CSS** only if no other component uses them — check first:
Run: `cd frontend && grep -rn "post-icon-btn\|post-actions-icons" src`
If PostItem was the only user, remove those rules from `index.css`; otherwise leave them.

- [ ] **Step 6: Verify**

Run: `cd frontend && pnpm typecheck && pnpm test -- PostItem`
Expected: PASS (the existing `PostItem.test.tsx` finds actions by accessible name — names are preserved: "Delete post", "Edit", etc.).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/campaign/PostItem.tsx frontend/src/index.css
git commit -m "refactor(frontend): post action bar via CardIconBar"
```

---

## Task 16: CastView — `removePc` API + PC-only trash (de-nest card button)

The cast list renders all `ResolvedCharacter` rows as a `<button className="entity-card">`. Show a trash only on rows that are PCs (cross-referenced via `listPCs`), using a new `removePc` client; the bar goes in the `<li>`, outside the selection button.

**Files:**
- Modify: `frontend/src/api/campaign/api.ts` (add `removePc`)
- Modify: `frontend/src/routes/campaign/CastView.tsx`

- [ ] **Step 1: Add the API method** (after `setActivePC`, line ~50):

```ts
  removePc: (id: string, characterRef: string) =>
    api.delete<void>(`/api/campaigns/${enc(id)}/pcs/${enc(characterRef)}`),
```

- [ ] **Step 2: Load PCs in `CastView`** to know which rows are PCs. Add near the other `useApi` calls (line ~25):

```tsx
const pcState = useApi(useCallback(() => campaignApi.listPCs(campaignId), [campaignId]));
```
Add imports: `import { campaignApi } from "../../api/campaign";`, `CardIconBar`, `deleteAction`.

- [ ] **Step 3: Build a set of PC refs and a ref-for-row helper** inside the render (the ref formula already exists at lines ~54–58):

```tsx
const pcRefs = pcState.status === "ok" ? new Set(pcState.data.map((p) => p.character_ref)) : new Set<string>();
const refForRow = (r: ResolvedCharacter) =>
  r.character.world_id !== null
    ? `library:worlds/${r.character.world_id}/characters/${r.character.id}`
    : `campaign:emergent/character/${r.character.id}`;
const handleRemovePc = (ref: string, name: string) => {
  if (!window.confirm(`Remove "${name}" as a player character? The character itself is not deleted.`)) return;
  void campaignApi.removePc(campaignId, ref).then(() => { state.reload(); pcState.reload(); });
};
```

- [ ] **Step 4: De-nest the bar.** Change the cast `<li>` (lines ~78–96) so the selection button and the bar are siblings:

```tsx
                    <li key={c.character.id} className="cast-entity">
                      <button
                        type="button"
                        className={selected?.character.id === c.character.id ? "entity-card active" : "entity-card"}
                        onClick={() => setSelectedId(c.character.id)}
                      >
                        <div className="entity-card-head">
                          <span className="entity-name">{c.character.name}</span>
                          <ChainBadge chain={c.source_chain} overrides={c.overrides_applied} />
                        </div>
                        <small className="entity-meta">
                          {c.character.role} · {c.character.tags.slice(0, 3).join(", ")}
                        </small>
                      </button>
                      <CardIconBar
                        actions={
                          pcRefs.has(refForRow(c))
                            ? [deleteAction({ onClick: () => handleRemovePc(refForRow(c), c.character.name), label: `Remove ${c.character.name} as PC` })]
                            : []
                        }
                      />
                    </li>
```

- [ ] **Step 5: CSS** — add a `.cast-entity { display:flex; flex-direction:column; }` rule (or reuse list styling) so the bar sits under the button. Confirm the `entity-list`/`entity-card` layout still looks right.

- [ ] **Step 6: Verify**

Run: `cd frontend && pnpm typecheck && pnpm test -- CastView`
Expected: success. Add a test: a PC row shows "Remove … as PC"; a non-PC row shows no delete button.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/campaign/api.ts frontend/src/routes/campaign/CastView.tsx frontend/src/index.css
git commit -m "feat(frontend): remove-PC action on cast cards via CardIconBar"
```

---

## Task 17: Empty bars on the remaining card roots

Add `<CardIconBar actions={[]} />` (invisible) to each remaining card root. An empty bar is just `<div role="toolbar">` with no buttons — valid even inside a `<button>`-rooted card, so no de-nesting is needed here.

**Files / insertion points** (add the import to each file; place `<CardIconBar actions={[]} />` just before the card element's closing tag):
- `frontend/src/routes/library/PluginsView.tsx` — `library-card` `<li>` (~line 95)
- `frontend/src/routes/library/MechanicsView.tsx` — `library-card` `<li>` (~line 79)
- `frontend/src/routes/campaign/LedgerView.tsx` — `entity-card` `<li>` (~lines 86, 142)
- `frontend/src/routes/campaign/ContentBrowser.tsx` — `entity-card` (~line 153)
- `frontend/src/routes/campaign/MechanicsView.tsx` — `entity-card` (~line 202)
- `frontend/src/routes/campaign/WorldView.tsx` — `entity-card-static` `<li>` (~lines 287, 311)
- `frontend/src/routes/appsettings/ProviderCard.tsx` — end of the `provider-card` `<section>` (~line 243, after `provider-card-actions`)
- `frontend/src/routes/campaign/SceneSuggestionView.tsx` — `suggestion-card` (~line 84)
- `frontend/src/routes/observability/WhyCharacterPanel.tsx` — `why-character-card` (~line 230)

- [ ] **Step 1: For each file, add the import and the empty bar.** Example (PluginsView):

```tsx
import { CardIconBar } from "../../components/CardIconBar";
// ...
            <li key={p.id} className="library-card">
              {/* ...existing content... */}
              <CardIconBar actions={[]} />
            </li>
```
Use the correct relative path per file (`../../components/...` for `routes/<area>/...`, `../../../components/...` if deeper — verify each).

- [ ] **Step 2: Typecheck + build**

Run: `cd frontend && pnpm typecheck && pnpm build`
Expected: success. Empty bars render nothing visible (CSS `:not(:empty)` keeps borders off).

- [ ] **Step 3: Run the affected tests**

Run: `cd frontend && pnpm test -- WhyCharacterPanel SceneSuggestion Plugins`
Expected: PASS (empty bar adds a `toolbar` role but no buttons; if any test counts buttons, adjust the query to be specific).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes
git commit -m "feat(frontend): empty CardIconBar on read-only/config cards"
```

---

## Task 18: Documentation — add the rule to CLAUDE.md and AGENTS.md

**Files:** Modify `CLAUDE.md` and `AGENTS.md`

- [ ] **Step 1: Add a "Card icon bar" subsection** under the frontend conventions in **both** files (identical text):

```markdown
### Card icon bar

Every card renders a `CardIconBar` (`frontend/src/components/CardIconBar.tsx`) at its
bottom edge — it is the single home for per-card actions. Cards are the block-level
`*-card` components (`campaign-card`, `library-card`, `entity-card`, `entity-card-static`,
`timeline-card`, `provider-card`, `suggestion-card`, `why-character-card`) plus chat posts
(`PostItem`). Cards backing a deletable artifact under `~/.grimoire/` start with a Delete
(🗑) icon built via `deleteAction()`; cards with no delete render an empty bar (invisible
until populated). **Never render a bespoke delete/remove button** — the `no-bespoke-delete`
ESLint rule enforces this. Card-root `<button>`s (timeline, cast) place the bar in the
wrapping `<li>`, not inside the button. (Sub-element classes like `*-card-actions`, the
`card-filters` toolbar, and grid wrappers are not cards.)
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md AGENTS.md
git commit -m "docs: document the card icon bar convention"
```

---

## Task 19: Wire and enforce the ESLint rule (final gate)

**Files:** Modify `frontend/eslint.config.js`

- [ ] **Step 1: Register the local plugin + rule**

```js
import localRules from "./eslint-rules/index.js";
// ...inside the config object's plugins/rules:
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
      local: localRules,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      "local/no-bespoke-delete": "error",
    },
```

- [ ] **Step 2: Run lint across the frontend**

Run: `cd frontend && pnpm lint`
Expected: PASS. If the rule flags any remaining bespoke delete, fix that call site (route it through `CardIconBar`) or, for a genuine non-card confirm button, add `// eslint-disable-next-line local/no-bespoke-delete -- <reason>`.

- [ ] **Step 3: Full verification**

Run: `cd frontend && pnpm typecheck && pnpm lint && pnpm test` then `cd ../backend && uv run pytest -q && uv run ruff check`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add frontend/eslint.config.js
git commit -m "feat(frontend): enforce no-bespoke-delete in eslint config"
```

---

## Self-review notes

- **Spec coverage:** component (T2), CSS (T3), empty-bar behaviour (T17 + `:not(:empty)` CSS in T3), dead `.character-card` removal (T4), lint rule (T5/T19), icon-library issue (T1), style-guide backend (T6), and every migration-table row (T7–T17). ✔
- **Ordering:** infra (T1–T6) → conversions (T7–T17) → docs (T18) → enable lint last (T19) so `pnpm lint` never goes red mid-stream.
- **Type consistency:** `CardIconAction` fields (`key`, `icon`, `label`, `onClick`, `disabled`, `busy`, `variant`) and `deleteAction({ onClick, label?, busy?, disabled? })` are used identically in every task.
- **Known soft spots for the implementer to confirm against live code:** exact import symbol for each library API client (`libraryApi` vs named exports), whether each target view exposes a `reload`, and the precise line anchors (they drift as files change). Always re-read the file before editing.
