# Download from chub.ai — Design

> Adds a one-click "Download from chub.ai" action: paste a chub.ai character URL (or
> `creator/slug` path) and the app fetches the card PNG, any gallery images, and any **linked**
> (non-embedded) lorebooks, importing all of it in one step — either as a brand-new character or,
> for two chub cards that are variants of the same character, as a new version on an existing one.
> A character that already exists in grimoire some other way can also be manually linked to a chub
> URL with no download at all. All three actions write the same `chub_source` field, laying the
> groundwork for a later "re-sync all chub-sourced cards" world action, which is **out of scope
> here** and will get its own spec.

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
POST   /worlds/{wid}/characters/import/chub
  body: {"url": str, "into"?: str}
  -> {character, version, gallery: {attempted, stored}, lore: {lorebooks_found, created}}

POST   /worlds/{wid}/characters/{cid}/chub-source
  body: {"url": str} -> {"chub_source": fullPath}
DELETE /worlds/{wid}/characters/{cid}/chub-source -> {"chub_source": ""}
```

- Unparseable `url` (either route) → `400`.
- chub.ai unreachable / character not found (`import/chub` only — `chub-source` makes no network
  call) → `404` with a clear detail message (e.g. "could not fetch from chub.ai").
- `into` behaves exactly like the existing file-import `into` param (creates a new version on an
  existing character instead of a new character).
- Unknown `cid` on the `chub-source` routes → `404` (existing `CharacterNotFound` mapping).

## Source tracking (for a future sync spec — not built here)

`character.md` frontmatter gains an optional `chub_source: <fullPath>` field, written only by
`import_from_chub`. Plain file/PNG/CHARX imports never set or clear it. `read_character`'s `meta`
gains `chub_source` (empty string when absent) so the frontend can know a character's origin.

This spec **does not** build a sync/re-download UI or flow — only the field a later spec needs.
`import_from_chub` accepting `into_cid` (mirroring the existing file-import `into` param) costs
nothing extra now and means a future "re-sync" action can most likely call this same function
per chub-sourced character.

**Manual linking, for already-imported characters.** Most existing characters were imported from
chub.ai *before* this feature existed (a manual PNG download, or imported some other way), so they
have no `chub_source` and never went through `import_from_chub`. Without a way to attach a chub URL
after the fact, none of those characters could ever benefit from a future sync — re-running the
chub download flow on them would fork a duplicate character or a redundant version, not just
record the link. So a character with no `chub_source` needs a lightweight way to *just record* the
association, with no download/import/version side effects at all (routes in the API section above):

- `POST` validates the URL/path shape via `chub.parse_full_path` (`400` if unparseable) and calls
  `characters.set_chub_source(root, cid, full_path)` — the same setter `import_from_chub` uses
  internally. **No network call to chub.ai** here; this is pure bookkeeping; a typo'd URL is only
  caught later, when something tries to use it (e.g. the future sync feature).
- `DELETE` calls a new `characters.clear_chub_source(root, cid)` (removes the frontmatter key) —
  lets the user undo a mistaken link.

## Frontend (`CharacterEditor.tsx`)

Three entry points, all using `window.prompt("chub.ai character URL or path?")` — the same pattern
already used in this file for "New character name?" / "New version name?", so no new UI component
is introduced:

1. **New character.** A third button next to the existing `+ New` / `Import card`: **"Download
   from chub.ai"**. Calls the chub endpoint with no `into`, creating a new character.
2. **New version of the open character — variant support.** A second button next to the existing
   **"Import version"** in the version-picker row (`CharacterEditor.tsx:471`): **"Download version
   from chub.ai"**. Calls the chub endpoint with `into: detail.meta.id`, landing the result as a
   new version on the *currently open* character — this is how two chub cards that are slightly
   different takes on the same character (e.g. a creator's revision, or another creator's variant)
   get attached together instead of becoming two separate characters. Matching is manual: the user
   decides they're "the same character" by being in that character's editor when they paste the
   URL, exactly like today's file-based "Import version" already works. No automatic
   name/similarity matching is attempted.

Both call the same new typed client function, `api.importCharacterFromChub(wid, urlOrPath, into?)`
(mirroring `importCharacter` / `importCharacterBook`):

- on success: for (1), open the new character's detail (matching the existing single-PNG-import
  flow); for (2), reload the open character and select the new version. Both show a result line via
  the existing `importMsg` state, composed from the response, e.g. `"Imported Monika — avatar + 13
  gallery images, 1 lorebook (42 entries) added to world lore"` (each clause included only when
  applicable — no gallery clause when `gallery.attempted` is 0, no lorebook clause when
  `lore.lorebooks_found` is 0)
- on failure: surface via the existing `setError` path, same as other import failures

Gallery images attach to whichever version was just created (the existing per-version image store
already scopes `assets/<vid>/` this way — no special-casing needed for the version-target case).
Lorebook entries always commit to **world** lore regardless of which version triggered the
download — lore is world-scoped, not version-scoped, so this needs no extra handling either.

3. **Manual link, no download.** Next to the version-picker row, a small chub-source control keyed
   off `detail.meta.chub_source`:
   - **unset:** a `"Link to chub.ai"` button → `window.prompt` → `api.setCharacterChubSource(wid,
     cid, url)` (`POST .../chub-source`) → reload the character so `chub_source` shows.
   - **set:** a `field-hint`-style line showing the linked path (e.g. `Linked to chub.ai:
     creator/slug`) with an `"Unlink"` button → `api.clearCharacterChubSource(wid, cid)` (`DELETE
     .../chub-source`) → reload.
   This makes **no** request to chub.ai and creates no version/images/lore — it only lets a
   character that was imported some other way (the common case today, since this feature didn't
   exist yet) carry the same `chub_source` bookkeeping a fresh chub download would set, so it isn't
   permanently excluded from whatever the future sync feature ends up doing.

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
- `set_chub_source` / `clear_chub_source` — round-trip through `character.md` frontmatter;
  `clear_chub_source` on a character with no source is a no-op.
- routes — `POST/DELETE .../chub-source` happy paths, `400` on unparseable `url`, `404` on unknown
  `cid`; `POST` makes no outbound HTTP call (assert the mocked `httpx` client is never invoked).

**Frontend (light):**
- clicking "Download from chub.ai" prompts, calls `importCharacterFromChub`, opens the resulting
  character, and renders a result message built from the response's gallery/lore counts;
  a rejected/empty prompt makes no API call; an API error surfaces via the existing error display.
- clicking "Download version from chub.ai" on an open character calls `importCharacterFromChub`
  with `into` set and selects the new version afterward.
- the chub-source control renders the "Link to chub.ai" button when `chub_source` is empty and the
  linked-path + "Unlink" button when set; both call their respective endpoint and reload.

## Non-goals (this iteration)

- **No re-sync / bulk-redownload UI.** Only the `chub_source` field this needs; the sync flow,
  conflict handling (e.g. local edits vs. a re-fetched version), and its UI are a separate,
  later spec.
- **No gallery viewer.** Images are stored, not displayed — same non-goal carried over from the
  character-card-polish spec.
- **No chub.ai authentication.** Anonymous fetches only; login-gated content is not supported.
- **No retry/backoff on chub API failures.** A failure (primary or best-effort) is final for that
  call; the user can just paste the URL again.
