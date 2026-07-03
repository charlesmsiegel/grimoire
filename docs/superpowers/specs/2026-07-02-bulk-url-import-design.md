# Bulk add characters from URLs — design

2026-07-02

## Goal

From the Characters grid, paste a list of card URLs (chub.ai links or direct
card URLs) and have every character fully set up without further clicking:
imported, gallery downloaded, embedded images localized, lorebooks imported
(both the card's embedded `character_book` and related chub lorebooks), and
finally a tagline popup for each new character.

Motivation: today each of these is a separate button press per character, and
the single "Download from URL" flow only imports + localizes.

## Approach

Frontend orchestration in `CharacterEditor.tsx`. Every pipeline step already
has a backend endpoint; the bulk flow is a sequential loop over URLs calling
them in order, following the `runBulkLocalize` precedent for bulk file
imports. No backend changes.

Rejected alternative: a backend batch endpoint (`POST
/characters/import/chub-bulk`). Fewer round-trips, but it would duplicate
orchestration that already exists client-side, require a new composite
streaming-progress protocol, and make partial-failure reporting harder.

## Entry point

The grid toolbar's **Download from URL** button stops using `window.prompt`
and opens a small modal (same pattern as `TaglinePrompt`):

- Textarea, hint: "One URL per line — chub.ai links or direct card URLs."
- **Add** / **Cancel** buttons.
- Blank/whitespace-only lines are ignored.
- One URL or many: the same pipeline runs either way.

The detail-view buttons ("Download version from URL", "Link chub", etc.) are
unchanged.

## Pipeline (per URL, sequential)

1. `api.importCharacterFromChub(wid, url)` — creates the character. The
   backend (`store.characters.import_from_chub`) already downloads the
   avatar, the chub gallery, and related chub lorebooks as part of this call
   and reports their counts in `ChubImportResult.gallery` / `.lore`; for
   non-chub direct URLs those come back empty. No separate gallery or
   related-lorebook calls are needed (re-calling those endpoints would
   re-download everything a second time).
2. `api.localizeImages(wid, cid, vid, onEvent)` — downloads remote images
   referenced in the card text and rewrites the references to local paths.
3. `api.importCharacterBook(wid, cid, vid)` — embedded `character_book` →
   world lore. Backend returns `created: []` when the card has no book.
   (This is the one lorebook source the import call does not cover.)

Error handling: a failing step records the error and **continues** — to the
next step of the same character, and to the next URL. A failure in step 1
skips that URL entirely (there is no character to process). Nothing aborts
the run.

## Progress and summary

- The flow stays on the grid (no detail navigation mid-run).
- Toolbar hint during the run, e.g. `Adding 2/5 — Almis: localizing images…`
  (steps: importing, gallery, localizing images, lorebooks).
- `reload()` after each character so its card appears in the grid as it
  lands.
- Completion summary in the toolbar aggregating the run, e.g.
  `Added 4/5 characters · 37 images localized · 3 lorebooks imported ·
  failed: <url> (not found)`.

## Tagline queue

After the loop finishes, show the existing `TaglinePrompt` once per
successfully imported character, one at a time; Save or Skip advances to the
next. Successes only — failed URLs get no prompt.

Single-URL runs preserve today's behavior: after the pipeline, open that
character's detail view, then show its tagline prompt.

## Testing

Vitest with the mocked `api` module (existing pattern in
`CharacterEditor.test.tsx`):

- Multi-URL happy path: import, localize, and embedded-lorebook endpoints
  called per URL, in order.
- A URL whose import fails is skipped; later URLs still run; the summary
  reports the failure.
- A mid-pipeline step failure (e.g. gallery) still runs the remaining steps
  for that character.
- Tagline prompts appear sequentially for successes only; Save/Skip advances.
- Single URL: pipeline runs, detail view opens, tagline prompt shows.
- Modal: blank lines ignored; Cancel runs nothing.

## Out of scope

- Parallel processing (sequential keeps progress readable; volumes are
  small).
- Dedupe / re-import detection for already-imported characters.
- Campaign-side imports.
