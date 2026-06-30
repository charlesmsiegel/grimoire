# Download from chub.ai — Design

> Adds a one-click "Download from chub.ai" action to character creation: paste a chub.ai
> character URL (or `creator/slug` path) and the app fetches the card PNG, any gallery images,
> and any **linked** (non-embedded) lorebooks, importing all of it in one step. This also lays
> the groundwork — a `chub_source` field on the character — for a later "re-sync all chub-sourced
> cards" world action, which is **out of scope here** and will get its own spec.

**Status:** Design — not yet implemented
**Date:** 2026-06-30
**Branch:** `chub-download` (off `main`)
**Builds on:**
[`2026-06-23-character-card-polish-design.md`](2026-06-23-character-card-polish-design.md) (the
per-version image store `store/assets.py`, PNG-import-saves-avatar, and the embedded
`character_book` → world-lore import path) and
[`2026-06-22-lorebook-import-design.md`](2026-06-22-lorebook-import-design.md) (the `lorebook`
normalize/commit path).

## Purpose

Most of the user's imported cards originate on chub.ai. Today, getting a card into grimoire means
manually downloading the PNG from the chub.ai page, and separately tracking down any linked
gallery images or lorebooks the creator published alongside it (chub.ai supports a "linked
lorebooks" relationship that is distinct from the V2/V3 spec's *embedded* `character_book`, which
the app already imports). This spec automates that: one URL paste fetches the card, its gallery,
and its linked lorebooks.

## chub.ai API surface (reverse-engineered, verified live)

chub.ai has no official public API docs. The endpoints below were confirmed by live testing
against two real cards (a gallery example and a linked-lorebook example) — not just inferred from
third-party tools. **This is a fragility risk**: if chub changes its API shape, this feature fails
closed (see Error handling).

- **Character lookup** — `GET https://api.chub.ai/api/characters/{fullPath}?full=true` →
  `{"node": {...}}` with `id`, `hasGallery: bool`, `related_lorebooks: number[]`, `max_res_url`
  (the downloadable `chara_card_v2.png`), `avatar_url`.
- **Gallery** — `GET https://gateway.chub.ai/api/gallery/project/{id}?limit=48&count=false` →
  `{"count", "nodes": [...], "page"}`, each node's `primary_image_path` is a direct image URL.
- **Linked lorebooks** — each id in `related_lorebooks` (chub sometimes includes a `-1` sentinel;
  filter to `id > 0`) resolves directly via
  `GET https://api.chub.ai/api/lorebooks/{id}?full=true` → `node.definition.embedded_lorebook`.
  This is **the exact same shape** as a character's `data.character_book` — no new
  lorebook-parsing code is needed; it reuses `lorebook.from_character_book()` as-is.
- A chub.ai character page URL looks like `https://chub.ai/characters/{creator}/{slug}`; the
  `fullPath` used by the API is just `{creator}/{slug}`.

## Backend — `store/chub.py` (new, pure API client)

Mirrors how `store/fetch.py` is just bytes-fetching with no filesystem writes — this module only
talks to chub.ai and returns plain data; it never touches disk.

```python
def parse_full_path(url_or_path: str) -> str | None
    # Accepts "https://chub.ai/characters/<creator>/<slug>" or a bare "<creator>/<slug>".
    # Returns None if the input doesn't match either shape.

def fetch_character_node(full_path: str) -> dict | None
    # GET https://api.chub.ai/api/characters/{full_path}?full=true -> node dict, or None on
    # any failure (network error, non-200, malformed JSON).

def fetch_lorebook_node(lorebook_id: int) -> dict | None
    # GET https://api.chub.ai/api/lorebooks/{lorebook_id}?full=true -> node dict, or None.

def fetch_gallery_paths(project_id: int) -> list[str]
    # GET https://gateway.chub.ai/api/gallery/project/{project_id}?limit=48&count=false
    # -> [node["primary_image_path"], ...]. Capped at one page of 48 (chub's own default
    # page size) -- the caller surfaces "attempted" vs "stored" counts rather than silently
    # truncating. Returns [] on any failure.
```

Uses `httpx` (already a dependency, same as `fetch.py`) with a timeout; not subject to the
SSRF host-blocking in `fetch.py` (these are fixed, known chub.ai/charhub.io hosts, not
user-supplied URLs) but does share `fetch.py`'s size cap and content-type/magic-byte validation
when downloading the actual image bytes.

## Backend — orchestration (`store/characters.py`)

```python
def import_from_chub(root: Path, url_or_path: str, into_cid: str | None = None) -> dict
```

1. `full_path = chub.parse_full_path(url_or_path)`; if `None` → raise `ChubParseError`.
2. `node = chub.fetch_character_node(full_path)`; if `None` → raise `ChubFetchError`. **Not
   best-effort** — this is the action the user asked for, so failure must surface, unlike the
   existing silent-miss avatar download in the polish spec.
3. Download PNG bytes from `node["max_res_url"]` via `fetch.download_url` (already has the size
   cap + magic-byte check); `import_card(root, png_bytes, "png", into_cid)` → `(cid, vid)`. This
   reuses the entire existing PNG-import path, including avatar population.
4. `set_chub_source(root, cid, full_path)` — writes `chub_source` into `character.md`
   frontmatter (new function, modeled on `set_birthdate`).
5. **Best-effort** (each sub-step is independently swallowed; failures here never undo the
   character import from step 3):
   - if `node["hasGallery"]`: `paths = chub.fetch_gallery_paths(node["id"])`; for each, download
     via `fetch.download_url` and `assets.put_image(root, cid, vid, f"gallery_{i}", data, ext)`.
     Track `attempted = len(paths)`, `stored = <successful count>`.
   - for each distinct `id > 0` in `node["related_lorebooks"]`: `lb = chub.fetch_lorebook_node(id)`;
     if present, `book = lb["definition"]["embedded_lorebook"]`; if non-empty,
     `lorebook.commit(root, lorebook.from_character_book(book))`, accumulating created refs.
6. Return
   `{"character": cid, "version": vid, "gallery": {"attempted": int, "stored": int}, "lore": {"lorebooks_found": int, "created": [{"kind", "id"}, ...]}}`.

`ChubParseError` / `ChubFetchError` are new exceptions in `store/chub.py`.

## API

```
POST /worlds/{wid}/characters/import/chub
  body: {"url": str, "into"?: str}
  -> {character, version, gallery: {attempted, stored}, lore: {lorebooks_found, created}}
```

- Unparseable `url` → `400`.
- chub.ai unreachable / character not found → `404` with a clear detail message (e.g. "could not
  fetch from chub.ai").
- `into` behaves exactly like the existing file-import `into` param (creates a new version on an
  existing character instead of a new character).

## Source tracking (for a future sync spec — not built here)

`character.md` frontmatter gains an optional `chub_source: <fullPath>` field, written only by
`import_from_chub`. Plain file/PNG/CHARX imports never set or clear it. `read_character`'s `meta`
gains `chub_source` (empty string when absent) so the frontend can know a character's origin.

This spec **does not** build a sync/re-download UI or flow — only the field a later spec needs.
`import_from_chub` accepting `into_cid` (mirroring the existing file-import `into` param) costs
nothing extra now and means a future "re-sync" action can most likely call this same function
per chub-sourced character.

## Frontend (`CharacterEditor.tsx`)

A third button next to the existing `+ New` / `Import card`: **"Download from chub.ai"**. Uses
`window.prompt("chub.ai character URL or path?")` — the same pattern already used in this file
for "New character name?" / "New version name?", so no new UI component is introduced. On a
non-empty answer:

- call `api.importCharacterFromChub(wid, urlOrPath)` (new typed client function in
  `api/client.ts`, mirroring `importCharacter` / `importCharacterBook`)
- on success: open the new character's detail (matching the existing single-PNG-import flow) and
  show a result line via the existing `importMsg` state, composed from the response, e.g.
  `"Imported Monika — avatar + 13 gallery images, 1 lorebook (42 entries) added to world lore"`
  (each clause included only when applicable — no gallery clause when `gallery.attempted` is 0, no
  lorebook clause when `lore.lorebooks_found` is 0)
- on failure: surface via the existing `setError` path, same as other import failures

Gallery images are stored (per the existing general per-version image store) but **not** given any
viewer UI in this spec, consistent with the polish spec's existing non-goal ("no gallery UI for
non-avatar images" — the store already ships ready for this).

## Error handling

- Bad/unparseable URL or unreachable/missing chub character → the whole call fails, nothing is
  created (this is the primary action, not a best-effort enhancement).
- Gallery image download failures and linked-lorebook fetch failures are independently
  swallowed and counted, never raise — the character import has already succeeded by that point.
- Known limitation: chub gates some NSFW/private/unlisted content behind a login session; this
  app makes anonymous requests only, so such cards fail the same as a 404. Both of the URLs used
  to verify this design work anonymously, covering the common case.
- Gallery fetch is capped at one page (48 images); the response's `gallery.attempted` count makes
  a truncation visible to the user rather than silently dropping images past the cap.

## Testing

**Backend (pytest, temp `GRIMOIRE_HOME`, mocked `httpx`):**
- `chub.py` — `parse_full_path` accepts a full URL and a bare path, rejects garbage;
  `fetch_character_node` / `fetch_lorebook_node` return `None` on non-200/malformed JSON;
  `fetch_gallery_paths` returns the path list and respects the page cap, `[]` on failure.
- `import_from_chub` — happy path creates character + avatar + gallery images + lore entries;
  a gallery-download failure or a lorebook-fetch failure does not undo the character import and is
  reflected in the returned counts; a `related_lorebooks` entry of `-1` is skipped; bad URL raises
  `ChubParseError`; unreachable character raises `ChubFetchError`.
- route — `POST .../characters/import/chub` happy path, `400` on bad URL, `404` on fetch failure,
  `into` creates a version on an existing character instead of a new one.

**Frontend (light):**
- clicking "Download from chub.ai" prompts, calls `importCharacterFromChub`, opens the resulting
  character, and renders a result message built from the response's gallery/lore counts;
  a rejected/empty prompt makes no API call; an API error surfaces via the existing error display.

## Non-goals (this iteration)

- **No re-sync / bulk-redownload UI.** Only the `chub_source` field this needs; the sync flow,
  conflict handling (e.g. local edits vs. a re-fetched version), and its UI are a separate,
  later spec.
- **No gallery viewer.** Images are stored, not displayed — same non-goal carried over from the
  character-card-polish spec.
- **No chub.ai authentication.** Anonymous fetches only; login-gated content is not supported.
- **No retry/backoff on chub API failures.** A failure (primary or best-effort) is final for that
  call; the user can just paste the URL again.
