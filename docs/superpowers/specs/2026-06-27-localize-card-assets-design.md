# Localize embedded card assets on import

**Date:** 2026-06-27
**Status:** Designed

## Problem

When a character card is imported, its text fields can reference images hosted
elsewhere — markdown image links, HTML `<img>` tags, inline data-URIs, or bare
URLs. Those references break when the remote host goes down, changes the URL, or
rate-limits us. We want imported cards to be self-contained: download every
referenced image into the per-version asset store and rewrite the text to point
at the local copy.

Today this already happens for the **avatar** only. This feature extends the
same treatment to images referenced anywhere in the card's text.

## Scope

- **In scope:** images only (`png`, `jpg`/`jpeg`, `gif`, `webp`) — matches the
  existing asset store, magic-byte sniffer, and SSRF-guarded downloader.
- **Out of scope:** audio, video, and arbitrary file types. The asset store and
  validators are image-only; expanding them is a separate effort.

### Reference forms detected

All four, scanned per text field:

1. **Data-URIs** — `data:image/...;base64,...` embedded inline.
2. **Markdown images** — `![alt](url)` (optional `"title"` ignored).
3. **HTML img tags** — `<img ... src="url" ...>`.
4. **Bare URLs** — `https?://…` sitting in text, *only* those not already
   consumed by forms 1–3 and not already pointing at a local
   `/api/worlds/…` path.

Bare-URL detection is made safe by the downloader: a URL whose bytes do not
sniff as an image (and whose content-type is not `image/*`) is left untouched.
Non-image links therefore cause no rewrite and no false-positive download.

### Localizable fields

`description`, `personality`, `scenario`, `first_mes`, `mes_example`,
`system_prompt`, `post_history_instructions`, `creator_notes`, every
`alternate_greetings[]`, and every `character_book.entries[].content`.

## Architecture

### Reuse over rebuild — extract a shared fetch module

The avatar pipeline in `store/characters.py` already contains the primitives we
need, currently private:

- `_decode_data_uri(uri)` — decode a `data:image/...;base64,...` URI (no network).
- `_host_is_blocked(host)` — SSRF guard (private/loopback/link-local/reserved).
- `_http_get_bytes(url)` — redirect-validating, 8 MB-capped HTTP GET.
- `_download_url(url)` — fetch + image validation, returns `(bytes, ext)` or `None`.
- `_sniff_ext(raw)` — magic-byte image type detection.

**Extract these into `store/fetch.py`** as public helpers so both the avatar
download and the new localizer depend on one tested fetcher. `characters.py`
keeps its avatar-specific glue (`_avatar_candidates`, `_download_avatar`) and
imports the moved primitives. This is a focused refactor of code we're already
working in — no behavior change to the avatar path.

Exported from `store/fetch.py`:

```python
IMG_EXTS: tuple[str, ...]
MAX_BYTES: int                          # 8 * 1024 * 1024
decode_data_uri(uri) -> tuple[bytes, str] | None
download_url(url) -> tuple[bytes, str] | None   # SSRF-guarded, image-validated
sniff_ext(raw) -> str | None
```

### New module: `store/localize.py`

```python
@dataclass(frozen=True)
class Ref:
    start: int          # char span in the field text
    end: int
    url: str            # raw url or data-uri

def find_refs(text: str) -> list[Ref]:
    """All image references in one field, in priority order, non-overlapping.
    Skips refs already pointing at a local /api/worlds/… path."""

def count_refs(card: dict) -> int:
    """Total refs across all localizable fields — drives the progress total."""

def localize_card(card, root, cid, vid, wid, *, fetch=fetch.download_url,
                  cap=None) -> Iterator[dict]:
    """Generator that downloads every referenced image, stores it, and rewrites
    the text in `card` in place — yielding progress events as it goes:
        {"total": N}                       # emitted first, after scanning
        {"done": k, "total": N}            # after each ref (downloaded or skipped)
        {"summary": {...}}                 # emitted last
    The caller drives the generator to completion and then persists `card`."""
```

`find_refs` stays a pure function (testable in isolation); `localize_card`
computes the total internally via `find_refs` over the localizable fields and
yields it first, so the progress total comes from the upfront regex scan.

`find_refs` matching order matters: data-URIs, then markdown, then HTML img,
then bare URLs over the spans not already claimed. Overlapping matches are
resolved by keeping the earliest/highest-priority and discarding any later
match whose span intersects one already kept.

For each ref, in field order:

1. Resolve bytes — data-URI → `fetch.decode_data_uri`; URL → injected `fetch`
   (default `fetch.download_url`, which SSRF-guards, caps size, and returns
   `None` for non-images).
2. On `None` → leave the original reference untouched; tally as `skipped` or
   `failed`; advance progress.
3. On success → store via `assets.put_image(root, cid, vid, name, bytes, ext)`
   with a **content-hash name** `embed-<sha256(bytes)[:12]>`. This name is
   `_safe_name`-compatible (no dots/glob chars). Identical bytes dedupe to one
   file; re-running is idempotent.
4. Rewrite the ref's span to the local serving URL:
   `/api/worlds/{wid}/characters/{cid}/versions/{vid}/images/embed-<hash>`.
   For a markdown ref `![alt](url)` the rewrite preserves `alt`; for an HTML
   `<img>` only the `src` value is swapped; a bare URL is replaced in place.
5. Yield `{"done": done, "total": total}` after each ref (downloaded or skipped).

Rewrites are applied per field from the **last span to the first** so earlier
spans' offsets stay valid.

#### Summary

```python
@dataclass
class Summary:
    total: int
    localized: int           # rewritten to a local copy
    skipped: int             # non-image / already local / blocked host
    failed: int              # network/timeout/too-large errors
    capped: bool             # hit the per-card download cap
```

### Download cap

`cap = 10 * (1 + len(alternate_greetings))` — i.e. ten downloads per greeting,
where `first_mes` is the implicit first greeting (floor 10). Once `cap`
downloads (attempts that hit the network) have run, remaining refs are left
untouched and `Summary.capped = True`. Data-URI decodes do **not** count against
the cap (no network). The cap bounds worst-case wall-clock for a pathological
card.

## Integration

### Import path

`import_card` is unchanged except that it **no longer downloads text-field
images inline** — a blocking call can't drive a progress bar. It still:

- parses the card, creates the character/version, and downloads the **avatar**
  synchronously (unchanged), then returns `(cid, vid)`.

The frontend, immediately after a successful import, calls the streaming
localize endpoint (below) for the new version. So localization still happens as
part of the import flow, just as a separate streamed call that drives the bar.

### Streaming localize endpoint

`POST /api/worlds/{wid}/characters/{cid}/versions/{vid}/localize`

Responds with **Server-Sent Events** (`text/event-stream`), matching the
existing chat/retry endpoints (`_chat_stream`). Each event is a
`data: {json}\n\n` frame; the frontend reuses `streamPost` + `parseSSEChunk`.
Event sequence:

```
data: {"total": N}
data: {"done": 1, "total": N}
...
data: {"done": N, "total": N}
data: {"summary": {...}}
```

Server flow: load the stored version JSON → drive `localize_card(...)` (a
generator), forwarding each yielded dict as an SSE `data:` frame → after the
generator is exhausted, persist the rewritten JSON via `update_version`. The
endpoint is idempotent: already-local refs are skipped, so the button is safe
to click repeatedly, and it doubles as the "run later if it didn't finish" and
"re-scan an existing card" path.

If there are no refs, the generator yields `{"total": 0}` then the `summary`
immediately (bar shows complete). The blocking `httpx` downloads run fine
because FastAPI iterates a sync generator in a threadpool.

### Frontend (`CharacterEditor.tsx`)

- After `onImport` / `onImportVersion` succeeds, auto-invoke the localize stream
  for the created version and render a progress bar from `progress.done/total`.
- A persistent **"Localize images"** button on the version (re-scan / retry),
  using the same stream + bar.
- On `done`, show a one-line summary (e.g. "Localized 7 images, 1 skipped").
- Bar counts assets processed against the upfront regex `total`, per the agreed
  model.

## Error handling

- Per-ref best-effort: blocked host, timeout, too-large, or non-image →
  original reference untouched, tallied, progress still advances.
- A field that isn't a string, or a missing field, is skipped silently.
- The endpoint never fails the import; a failed localize leaves the card with
  its original (remote) references, retryable via the button.

## Testing

`store/localize.py` unit tests:

- `find_refs`: each of the four forms; markdown with title; HTML with extra
  attributes; data-URI; bare URL; **skip already-local** `/api/worlds/…`; no
  double-match where a bare-URL pattern overlaps a markdown/html span;
  ordering/non-overlap resolution.
- total event: `localize_card`'s first yielded `{"total": N}` counts refs
  across multiple fields including `alternate_greetings[]` and
  `character_book.entries[]`.
- `localize_card` with an **injected fake `fetch`**: rewrites to the serving
  URL; content-hash naming; dedupe (same bytes → one `put_image`, both refs
  rewritten to the same name); idempotent re-scan (second pass is a no-op);
  non-image → ref untouched, `skipped`; blocked-host → `skipped`/`failed`;
  cap enforcement (`capped=True`, refs past cap untouched); data-URIs not
  counted against cap; last-to-first span rewriting preserves offsets.

`store/fetch.py`: the moved primitives keep their existing avatar-path tests
(re-pointed at the new module); add a `download_url` non-image rejection test if
not already covered.

Endpoint test: SSE event sequence (`{total}` → `{done}`× → `{summary}`) parsed
from the `data:` frames with a fake fetch; persisted card reflects rewrites;
no-refs short-circuit (`{total: 0}` then `{summary}`).

## Files touched

- `backend/src/grimoire/store/fetch.py` — new; extracted fetch/decode/sniff.
- `backend/src/grimoire/store/characters.py` — import the moved helpers; avatar
  glue unchanged.
- `backend/src/grimoire/store/localize.py` — new; scanner + localizer.
- `backend/src/grimoire/routes.py` — new streaming localize route.
- `frontend/src/components/CharacterEditor.tsx` — auto-run after import, manual
  button, progress bar, summary.
- Tests under the backend test suite for `fetch`, `localize`, and the route.
