# Sandboxed creator notes — design

2026-07-03

Creator notes on cards often carry complex HTML (own `<style>` blocks, layout,
fixed-position art) that must not affect the rest of the page. On the character
detail page, `creator_notes` renders **always** inside a sandboxed iframe: the
note gets full HTML fidelity inside its own document, scripts never execute,
its CSS cannot leak out, and the frame stretches vertically to fit its content.

## Component: `HtmlNote`

`frontend/src/components/HtmlNote.tsx` — props `{ html: string; title: string }`.

- Renders `<iframe className="html-note" title={title} srcDoc={doc}
  sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox">`.
  - **No `allow-scripts`** — JS (including inline handlers) never runs.
    `allow-same-origin` is safe without it and lets the parent measure the
    content.
  - `allow-popups` + `<base target="_blank">` in the doc so links open in a new
    tab instead of dying silently inside the sandbox.
- `doc` wraps the raw note in a minimal document: `body { margin: 0 }`, the
  app's font family/size and text color (read from `getComputedStyle(document.body)`
  at render), `line-height: 1.5`, `overflow-wrap: anywhere`,
  `img { max-width: 100%; height: auto }`.
- Plain-text notes (no HTML tag matched by `/<[a-z!][^>]*>/i`) additionally get
  `white-space: pre-wrap` so line breaks survive; HTML notes flow normally.
- **Auto-height:** `onLoad` sets the frame's height to the content document's
  `scrollHeight`, then attaches a parent-side `ResizeObserver` to the content
  root so late-loading images keep the frame fitted. Observer is disconnected
  on re-load and unmount; everything is guarded for environments without
  iframes/ResizeObserver (jsdom). No height cap — the frame stretches to fit
  and cannot affect anything outside itself.

## Wiring

In the CharacterEditor detail-page fields loop, `creator_notes` becomes a
special case (alongside `first_mes`): it renders `<HtmlNote html={val}
title="Creator notes" />` instead of `.detail-text`. All other fields are
unchanged.

CSS: `.html-note { width: 100%; border: none; display: block; }`

## Testing (vitest, run from `frontend/`)

- Creator notes render an iframe titled "Creator notes" whose `sandbox`
  attribute excludes `allow-scripts` and whose `srcdoc` contains the note HTML.
- A plain-text note's `srcdoc` includes `white-space:pre-wrap` and the raw
  text with its newline.
- Other card fields still render as plain `.detail-text`.
