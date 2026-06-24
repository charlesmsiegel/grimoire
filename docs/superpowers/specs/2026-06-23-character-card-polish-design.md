# Character Card Polish — Design

> Rounds out the character card editor and adds a **general per-version image store**. The V3
> card schema already round-trips every field involved (`creator`, `creator_notes`, `tags`,
> `alternate_greetings`, `character_book`) — most of this spec is **editor/display work**, not
> schema work. The one genuinely new capability is **images**: today import discards all image
> bytes and nothing is stored or served. This spec adds a small image-storage module, three ways
> to populate an avatar, a proper alternate-greetings editor, the missing editor fields, and a
> one-click path to import a card's embedded `character_book` into world lore.

**Status:** Design — not yet implemented
**Date:** 2026-06-23
**Branch:** `character-card-polish` (off `main`)
**Builds on:**
[`2026-06-21-character-cards-design.md`](2026-06-21-character-cards-design.md) (the character
container / V3 card model, `cards` parser, `appear()` asset copy) and
[`2026-06-22-lorebook-import-design.md`](2026-06-22-lorebook-import-design.md) (the `lorebook`
normalize/commit path for a `character_book`).

## Purpose

The character editor currently exposes a partial slice of the V3 card and the system stores no
images at all. This spec closes both gaps:

- **Editor completeness** — surface `creator`, `creator_notes`, and free-form `tags`; fix the
  broken alternate-greetings editor (a newline-join textarea that mangles multi-line greetings).
- **A general character-image store** — per-version image files under `assets/`, with the
  **avatar** as one named image. Other image kinds (emotions, backgrounds, …) will land in the
  same store later with no schema change.
- **Embedded lorebook reuse** — a card carried into the world may ship a `character_book`; offer
  a one-click import of those entries into world lore through the existing `lorebook` path.

## Non-goals (this iteration)

- **No faithful `data.assets` / `embeded://` rewriting.** The image store is a sidecar; we do not
  mutate the card's `data.assets` array or synthesize `embeded://` URIs. (PNG export still embeds
  the stored avatar — see below — but JSON export does not carry image bytes.)
- **No CHARX embedded-asset import.** CHARX import still reads `card.json` only; its bundled
  images are ignored this iteration.
- **No gallery UI for non-avatar images.** The store + serve routes are image-generic and ship
  ready, but the only image with display/editing UI this iteration is the **avatar**.
- **No `character_book` review/route UI.** The embedded-book import is a blind one-click commit to
  the `lore` category; the parse→review→re-route UI rides with the deferred lorebook frontend.
- **No tie between character `tags` and the world gating vocabulary.** Card `tags` are free-form
  discovery labels (ST/Chub semantics), independent of `tags.md` (which gates PCs/greetings).

## A character is unchanged on disk except for images

```
worlds/<wid>/characters/<cid>/
  character.md                 # frontmatter: name, default_version  (unchanged)
  <vid>.json                   # V3 card  (unchanged)
  assets/<vid>/<name>.<ext>    # NEW shape — per-version images: avatar.png, …
```

- Images live under a **per-version subfolder** `assets/<vid>/`. The avatar is the image named
  `avatar` (`assets/<vid>/avatar.<ext>`). Any future image kind is another `<name>.<ext>` in the
  same folder.
- `appear()` already `copytree`s the whole `assets/` tree into the campaign, so a campaign's
  locked version keeps its images with **no change to `appearances.py`**.
- Images are **not** hashed into the card; `card_hash` is unchanged (still the `<vid>.json` text),
  so the sync engine is untouched by image edits.

## Module — `store/assets.py` (general per-version image store)

Root-based (operates on a world **or** campaign root, exactly like `characters.py`), single
responsibility: store/list/fetch/delete images for one character version.

```python
AVATAR = "avatar"                                   # the distinguished image role

def list_images(root, cid, vid) -> list[dict]       # [{"name": "avatar", "ext": "png"}, …]
def image_path(root, cid, vid, name) -> Path | None # existing file, or None
def put_image(root, cid, vid, name, data: bytes, ext: str) -> None   # write/replace
def delete_image(root, cid, vid, name) -> None      # no-op if absent
```

- `name` and `ext` are slugified/validated with the same `_safe`-style guard `characters.py`
  already uses (reject `""`, `.`, `..`, path separators). `ext` is normalized (strip leading dot,
  lowercase, an allowlist of image extensions `png/jpg/jpeg/gif/webp`).
- `put_image` replaces any existing image of the same `name` regardless of prior ext (it removes
  `assets/<vid>/<name>.*` first), so an upload never leaves a stale duplicate-ext file.
- `image_path` globs `assets/<vid>/<name>.*` and returns the single match (or `None`).
- Avatar access is just these functions with `name=AVATAR`; no avatar-specific helpers needed.

`assets.py` depends only on `paths` (slug/safety). It does not import `characters` — the routes
compose the two.

## Card-store exposure (`characters.py`)

Add image presence to the read surface so the frontend knows what to render:

- `read_character(root, cid)` → each version gains `images: ["avatar", …]`
  (`[i["name"] for i in assets.list_images(root, cid, vid)]`).
- `list_characters(root)` → each character gains `has_avatar: bool` for its **default version**
  (the roster/world thumbnail check). Cheap: `assets.image_path(root, cid, default, "avatar") is
  not None`.

`import_card` is extended to populate the avatar from the import itself (see next section). No
other `characters.py` behavior changes.

## Three ways to populate an avatar

All three land via `assets.put_image(root, cid, vid, "avatar", bytes, ext)`.

1. **PNG import = avatar.** `import_card(root, data, "png", …)` already holds the raw upload bytes
   that the loader discards. After creating the character/version, also `put_image(…, "avatar",
   data, "png")`. The PNG *is* the avatar.
2. **URL download (best-effort).** On import of any format, scan the parsed card for an avatar URL
   — `data.assets` entries whose `type` is `icon`/`avatar` with an `http(s)` `uri`, falling back
   to a `data.avatar` string if it is an `http(s)` URL. If one is found, fetch it with `httpx`
   (already a dependency) under a **timeout** and a **size cap**, verify an `image/*`
   content-type, and store it as the avatar with the ext derived from the URL/content-type.
   **Import never fails on a download error** — a timeout, non-image, oversize body, or non-2xx
   response simply leaves the character without an avatar. This is the only network call; it is
   opt-out-safe (no URL ⇒ no call).
3. **Manual upload / replace / remove.** The image-generic routes below with `name=avatar`.

Characters may legitimately have **no avatar**; that is a first-class state everywhere (routes
404, the frontend shows a neutral placeholder).

## Embedded `character_book` → world lore

A stored card may carry `data.character_book`. Reuse `lorebook` (no new lore mechanism):

- `lorebook.py` exposes a thin public helper over its existing private normalizer:
  `from_character_book(book: dict) -> list[dict]` (wraps `_normalize`), so callers do not reach
  into a private function.
- New route parses the **stored** card's book and commits it in one step (blind import to the
  default `lore` category):

```
POST /worlds/{wid}/characters/{cid}/versions/{vid}/lorebook/import
        → {created: [{kind, id}, …]}        # [] when the card has no character_book
```

The route reads the card via `characters.read_card`, calls
`lorebook.commit(world_root, lorebook.from_character_book(card["data"].get("character_book") or
{}))`, and returns the created refs. Disabled/blank/constant entries follow 2c's existing
normalization rules. The two-call parse/review flow from 2c is unchanged and still available for
file uploads; this is a convenience for a book already on disk.

## API (deltas, all under `/api`)

```
# General per-version character images (name selects the image; "avatar" is the role)
GET    /worlds/{wid}/characters/{cid}/versions/{vid}/images          → [{name, ext}]
GET    /worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}   → image bytes (404 if none)
PUT    /worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}   (multipart file) → {name, ext}
DELETE /worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}
GET    /campaigns/{cid}/characters/{char}/versions/{vid}/images/{name} → image bytes  # locked display

# Embedded lorebook import
POST   /worlds/{wid}/characters/{cid}/versions/{vid}/lorebook/import → {created:[{kind,id}]}
```

- Image bytes are returned with `Response(content=…, media_type=…)` (the existing byte-serving
  pattern), media type derived from the stored ext.
- The campaign image route reads the **campaign** root so it serves the locked card's copied
  avatar (which may differ from the world's after a sync conflict was kept).
- `GET …/images/{name}` for an absent image → `404`. `PUT` with a non-image / disallowed ext →
  `400`. Unknown character/version → `404` (existing `CharacterNotFound`/`VersionNotFound` map).

`read_character` / `list_characters` JSON gain `images` / `has_avatar` as above; existing callers
ignore unknown fields.

## Frontend (deltas — `CharacterEditor.tsx`, roster, cast)

**Editor field additions.** Add `creator` (line input) and `creator_notes` (textarea) to the
authored fields. Add a **Tags** control: free-form, comma-separated input ↔ `data.tags` array (no
world-vocabulary coupling).

**Alternate greetings — repeatable editor.** Replace the single newline-join textarea (which
corrupts multi-line greetings) with a list: each greeting is its own textarea with a **Remove**
button, plus **+ Add greeting**. Maps directly to/from `data.alternate_greetings` (no join/split).

**Avatar block (selected version).** Show the selected version's avatar (`GET …/versions/{vid}/
images/avatar`) or a neutral placeholder when absent, with **upload/replace** (`PUT`) and
**remove** (`DELETE`) controls. On **PNG/URL import** the avatar appears automatically.

**Display rules.**
- **World** — the character list/roster thumbnail uses the **default version's** avatar
  (`has_avatar` gates it).
- **Editor** — the **currently-selected version's** avatar.
- **Campaign cast panel** — the **locked version's** avatar via the campaign image route, built
  from the roster's `version`. Missing avatar → placeholder.

**Embedded lorebook.** When the selected version's card has a non-empty `character_book`, the
editor shows the entry count and an **"Import N entries to world lore"** button calling
`POST …/lorebook/import`, then a brief result (`Imported N entries`).

**`api/client.ts`** gains typed functions for image list/get-url/put/delete (world + campaign) and
the embedded-lorebook import; `Card`/`CharacterDetail`/`CharacterSummary` types gain `images` /
`has_avatar`. All components reference **theme tokens only** — no hardcoded colors or fonts.

## Error handling

- Absent image → `404`; absent character/version → `404`; bad upload (non-image / disallowed ext)
  → `400`.
- Avatar **URL download** failure (timeout, non-image, oversize, non-2xx, connection error) is
  swallowed — import succeeds with no avatar. Never raises into the import path.
- `…/lorebook/import` on a card with no `character_book` → `{created: []}` (not an error).
- `put_image` replacing a different-ext image removes the old file first (no stale duplicate).
- Image `name`/`ext` path-safety reuses the existing `_safe`/slug guards; collisions overwrite by
  design (an avatar is a single image, replaced in place).

## Testing

**Backend (pytest, temp `GRIMOIRE_HOME`):**
- `assets.py` — `put_image`/`image_path`/`list_images`/`delete_image` round-trip; replacing an
  avatar with a different ext leaves exactly one file; unsafe `name`/`ext` rejected; absent image
  → `None`.
- `import_card` — PNG import saves the uploaded bytes as the avatar (`has_avatar` true); a JSON
  card with an `http` avatar URI in `data.assets` triggers a download (mock `httpx`) stored as the
  avatar; a download failure (mock raises / non-image / oversize) leaves **no** avatar and does
  **not** raise; a card with no avatar URL makes **no** network call.
- `read_character`/`list_characters` expose `images` / `has_avatar` correctly.
- `appear()` copies the locked version's avatar into the campaign (existing copy path covers the
  new per-version subfolder).
- routes — `GET/PUT/DELETE …/images/{name}` happy paths + `404`/`400`; the campaign image route
  serves the campaign copy; embedded `…/lorebook/import` creates lore entities and returns refs,
  and `{created: []}` when the card has no book.

**Frontend (light):**
- alternate-greetings list adds/removes and round-trips a multi-line greeting intact;
- `creator` / `creator_notes` / `tags` save and reload;
- avatar block renders a placeholder when absent and the image when present; upload calls `PUT`;
- the import-book button appears only when the selected version has a non-empty `character_book`.

## Phasing (for the implementation plan)

1. **`assets.py` + card-store exposure** — the image store module; `read_character`/
   `list_characters` gain `images`/`has_avatar`; unit tests. App stays green.
2. **Image routes** — world `GET/PUT/DELETE …/images/{name}` + campaign `GET`; byte serving; route
   tests.
3. **Avatar population** — PNG-import-saves-avatar + best-effort URL download in `import_card`;
   tests (mocked `httpx`).
4. **Embedded lorebook import** — `lorebook.from_character_book` + the import route; tests.
5. **Frontend** — editor fields (`creator`/`creator_notes`/`tags`), repeatable greetings editor,
   avatar block, world/campaign avatar display, import-book button; light tests.

## What grows later (not built now)

- A gallery UI for non-avatar images (emotions, backgrounds) — the store already holds them.
- Faithful `data.assets`/`embeded://` rewriting and CHARX embedded-asset import/export.
- The `character_book` parse→review→re-route UI (with the deferred lorebook frontend).
