# Sandboxed Creator Notes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render `creator_notes` on the character detail page inside a sandboxed, auto-height iframe so complex HTML displays fully but cannot affect the rest of the page.

**Architecture:** A new `HtmlNote` component wraps the raw note in a minimal `srcdoc` document inside `<iframe sandbox>` (no `allow-scripts`; `allow-same-origin` for parent-side height measurement). The CharacterEditor detail loop special-cases `creator_notes` to use it.

**Tech Stack:** React + TypeScript + vitest.

**Spec:** `docs/superpowers/specs/2026-07-03-sandboxed-creator-notes-design.md`

## Global Constraints

- Frontend commands MUST run from `frontend/`: `npx vitest run`, `npx tsc -b`.
- The iframe `sandbox` attribute MUST NOT include `allow-scripts`.
- No height cap — the frame stretches to fit content.

---

### Task 1: HtmlNote component + wiring

**Files:**
- Create: `frontend/src/components/HtmlNote.tsx`
- Modify: `frontend/src/components/CharacterEditor.tsx` (detail TEXT_FIELDS loop), `frontend/src/index.css`
- Test: `frontend/src/components/CharacterEditor.test.tsx`

**Interfaces:**
- Produces: `HtmlNote({ html, title }: { html: string; title: string })` — sandboxed iframe rendering of untrusted HTML.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/components/CharacterEditor.test.tsx`:

```tsx
test("creator notes render inside a sandboxed iframe", async () => {
  const card = {
    ...CARD,
    data: { ...CARD.data, creator_notes: "<style>body{color:red}</style><b>fancy</b> note" },
  };
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card, images: [] }],
  });
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  const frame = await screen.findByTitle("Creator notes");
  expect(frame.tagName).toBe("IFRAME");
  expect(frame.getAttribute("sandbox")).not.toContain("allow-scripts");
  expect(frame.getAttribute("srcdoc")).toContain("<b>fancy</b> note");
});

test("plain-text creator notes keep line breaks via pre-wrap", async () => {
  const card = { ...CARD, data: { ...CARD.data, creator_notes: "line one\nline two" } };
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card, images: [] }],
  });
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  const frame = await screen.findByTitle("Creator notes");
  expect(frame.getAttribute("srcdoc")).toContain("white-space:pre-wrap");
  expect(frame.getAttribute("srcdoc")).toContain("line one\nline two");
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: both new tests FAIL (`Unable to find element with title: Creator notes`).

- [ ] **Step 3: Create the component**

Create `frontend/src/components/HtmlNote.tsx`:

```tsx
import { useEffect, useMemo, useRef } from "react";

const TAG_RE = /<[a-z!][^>]*>/i;

/** Untrusted card HTML (creator notes) rendered inside a sandboxed iframe:
 *  scripts never run, its CSS cannot leak, and fixed overlays stay confined
 *  to the frame. The frame stretches to fit its content. */
export function HtmlNote({ html, title }: { html: string; title: string }) {
  const ref = useRef<HTMLIFrameElement>(null);
  const roRef = useRef<ResizeObserver | null>(null);

  const doc = useMemo(() => {
    const cs = getComputedStyle(document.body);
    const pre = TAG_RE.test(html) ? "" : "white-space:pre-wrap;";
    return `<!doctype html><html><head><base target="_blank"><style>` +
      `body{margin:0;font-family:${cs.fontFamily};font-size:${cs.fontSize};` +
      `line-height:1.5;color:${cs.color};${pre}overflow-wrap:anywhere}` +
      `img{max-width:100%;height:auto}` +
      `</style></head><body>${html}</body></html>`;
  }, [html]);

  useEffect(() => () => roRef.current?.disconnect(), []);

  function fit() {
    const frame = ref.current;
    const root = frame?.contentDocument?.documentElement;
    if (!frame || !root) return;
    frame.style.height = `${root.scrollHeight}px`;
    roRef.current?.disconnect();
    if (typeof ResizeObserver !== "undefined") {
      roRef.current = new ResizeObserver(() => {
        frame.style.height = `${root.scrollHeight}px`;
      });
      roRef.current.observe(root);
    }
  }

  return <iframe ref={ref} className="html-note" title={title} srcDoc={doc}
                 sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
                 onLoad={fit} />;
}
```

- [ ] **Step 4: Wire into CharacterEditor and style**

In `CharacterEditor.tsx`, import it with the other component imports:

```tsx
import { HtmlNote } from "./HtmlNote";
```

In the detail TEXT_FIELDS loop, extend the `first_mes` special case:

```tsx
                  {f.key === "first_mes"
                    ? <div className="detail-rendered"><Markdown remarkPlugins={[remarkGfm]}>{val}</Markdown></div>
                    : f.key === "creator_notes"
                      ? <HtmlNote html={val} title="Creator notes" />
                      : <div className="detail-text">{val}</div>}
```

In `frontend/src/index.css`, after the `.focus-modal input[type="range"]` rule:

```css
.html-note { width: 100%; border: none; display: block; }
```

- [ ] **Step 5: Run tests and typecheck**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx` then `npx tsc -b`
Expected: all PASS, no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/HtmlNote.tsx frontend/src/components/CharacterEditor.tsx frontend/src/index.css frontend/src/components/CharacterEditor.test.tsx
git commit -m "feat(characters): sandboxed creator-notes rendering"
```

---

### Task 2: Full verification

- [ ] **Step 1: Frontend suite + typecheck**

Run (from `frontend/`): `npx vitest run` then `npx tsc -b`
Expected: all PASS, no type errors.

- [ ] **Step 2: Backend suite (unchanged, sanity)**

Run (repo root): `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all PASS.
