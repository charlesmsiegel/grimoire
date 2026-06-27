# Localize Embedded Card Assets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a character card is imported, download every image referenced in its text fields into the per-version asset store and rewrite the text to point at the local copy, with a live progress bar.

**Architecture:** Extract the existing avatar-download primitives into a shared `store/fetch.py`. Add `store/localize.py` that scans the card's text fields for image references (markdown, HTML `<img>`, data-URIs, bare URLs), downloads each via the shared fetcher, stores it under a content-hash name, and rewrites the text in place — implemented as a generator that yields progress events. Expose it through an SSE endpoint (matching the existing chat-stream pattern); the frontend auto-fires it after import and offers a manual re-scan button, both driving a progress bar.

**Tech Stack:** Python 3 / FastAPI (backend), httpx, pytest; TypeScript / React / Vite (frontend), vitest.

## Global Constraints

- Images only: allowed extensions `png`, `jpg`, `jpeg`, `gif`, `webp` (verbatim from `assets._EXTS` / `fetch.IMG_EXTS`).
- Per-download size cap: `8 * 1024 * 1024` bytes (`fetch.MAX_BYTES`), 10 s HTTP timeout, max 5 redirects — all already enforced by the moved fetcher; do not change.
- SSRF guard (`fetch.host_is_blocked`) must run on every request and every redirect hop; never weaken it.
- Per-card download cap: `10 * (1 + len(alternate_greetings))`. `first_mes` is the implicit first greeting (floor 10). Data-URI decodes do NOT count against the cap.
- Localizable fields, exact keys under `card["data"]`: `description`, `personality`, `scenario`, `first_mes`, `mes_example`, `system_prompt`, `post_history_instructions`, `creator_notes`, each string in `alternate_greetings`, and each `character_book["entries"][i]["content"]`.
- Stored asset name: `embed-<sha256(bytes).hexdigest()[:12]>` — no dots/glob chars, satisfies `assets._safe_name`.
- Rewrite target (root-relative, exact shape): `/api/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}`.
- Streaming uses SSE: backend yields `data: {json}\n\n` frames with `StreamingResponse(..., media_type="text/event-stream")`; frontend reuses `parseSSEChunk`/`streamPost`.
- Best-effort: any per-ref failure leaves the original reference untouched; localization never raises into the import or breaks the card.
- Run backend tests with `python -m pytest` from `backend/`; frontend tests with `npm test` from `frontend/`.

---

### Task 1: Extract shared fetch module

Move the avatar pipeline's network/decode/sniff primitives out of `characters.py` into a new `store/fetch.py` as public functions, re-pointing `characters.py` at them. No behavior change.

**Files:**
- Create: `backend/src/grimoire/store/fetch.py`
- Modify: `backend/src/grimoire/store/characters.py` (remove moved helpers, import from `fetch`)
- Test: `backend/tests/test_fetch_store.py`

**Interfaces:**
- Produces:
  - `fetch.IMG_EXTS: tuple[str, ...]` = `("png", "jpg", "jpeg", "gif", "webp")`
  - `fetch.MAX_BYTES: int` = `8 * 1024 * 1024`
  - `fetch.sniff_ext(raw: bytes) -> str | None`
  - `fetch.decode_data_uri(uri: str) -> tuple[bytes, str] | None`
  - `fetch.host_is_blocked(host: str) -> bool`
  - `fetch.download_url(url: str) -> tuple[bytes, str] | None`  (SSRF-guarded, image-validated; returns `None` on any failure or non-image)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_fetch_store.py`:

```python
from grimoire.store import fetch


def test_sniff_ext_detects_png_and_rejects_text():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    assert fetch.sniff_ext(png) == "png"
    assert fetch.sniff_ext(b"not an image") is None


def test_decode_data_uri_returns_bytes_and_ext():
    # 1x1 gif, base64
    uri = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="
    got = fetch.decode_data_uri(uri)
    assert got is not None
    raw, ext = got
    assert ext == "gif"
    assert raw[:6] in (b"GIF87a", b"GIF89a")


def test_decode_data_uri_rejects_non_data_uri():
    assert fetch.decode_data_uri("https://example.com/a.png") is None


def test_host_is_blocked_blocks_loopback():
    assert fetch.host_is_blocked("127.0.0.1") is True
    assert fetch.host_is_blocked("localhost") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fetch_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.fetch'`

- [ ] **Step 3: Create `fetch.py` with the moved code**

Create `backend/src/grimoire/store/fetch.py` by moving the bodies verbatim from `characters.py` (lines ~209–340), renaming the public ones (drop leading underscore where listed). Full content:

```python
"""Best-effort image fetching shared by avatar download and asset localization.

Decodes embedded data-URIs, downloads remote images over HTTP(S) with an SSRF
guard and a size cap, and validates results by magic bytes. Never raises into a
caller's happy path — a miss returns None.
"""

from __future__ import annotations

import base64
import ipaddress
import socket
import ssl
from urllib.parse import urlparse

import certifi
import httpx

MAX_BYTES = 8 * 1024 * 1024
IMG_EXTS = ("png", "jpg", "jpeg", "gif", "webp")
_CT_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
           "image/gif": "gif", "image/webp": "webp"}
_MAX_REDIRECTS = 5
_UA = "Mozilla/5.0 (grimoire image fetch)"
# Trust certifi's CA bundle explicitly, independent of any ambient SSL_CERT_FILE.
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())


def sniff_ext(raw: bytes) -> str | None:
    """Identify an image by its magic bytes (some hosts mislabel content-type)."""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return None


def decode_data_uri(uri: str) -> tuple[bytes, str] | None:
    """Decode a `data:image/...;base64,...` URI (no network)."""
    if not uri.startswith("data:"):
        return None
    header, _, b64 = uri.partition(",")
    if "base64" not in header:
        return None
    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception:  # noqa: BLE001
        return None
    if not raw or len(raw) > MAX_BYTES:
        return None
    mime = header[len("data:"):].split(";")[0].strip().lower()
    ext = _CT_EXT.get(mime) or sniff_ext(raw)
    return (raw, ext) if ext else None


def host_is_blocked(host: str) -> bool:
    """True if the host resolves to (or is) a private/loopback/link-local/reserved address."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True  # unresolvable -> block
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return True
    return False


def _http_get_bytes(url: str) -> tuple[bytes, str | None]:
    """Fetch bytes, validating each redirect hop and aborting early past the cap."""
    headers = {"User-Agent": _UA, "Accept": "image/*,*/*"}
    with httpx.Client(timeout=10.0, follow_redirects=False, verify=_SSL_CTX, headers=headers) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("bad url")
            if host_is_blocked(parsed.hostname):
                raise ValueError("blocked host")
            with client.stream("GET", url) as r:
                if r.is_redirect:
                    loc = r.headers.get("location")
                    if not loc:
                        raise ValueError("redirect without location")
                    url = str(r.url.join(loc))
                    continue
                r.raise_for_status()
                cl = r.headers.get("content-length")
                if cl and cl.isdigit() and int(cl) > MAX_BYTES:
                    raise ValueError("too large")
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_BYTES:
                        raise ValueError("too large")
                return bytes(buf), r.headers.get("content-type")
        raise ValueError("too many redirects")


def download_url(url: str) -> tuple[bytes, str] | None:
    """Download an image; return (bytes, ext) or None on any failure / non-image."""
    try:
        content, ctype = _http_get_bytes(url)
    except Exception:  # noqa: BLE001 — best-effort; callers never fail on a miss
        return None
    if not content or len(content) > MAX_BYTES:
        return None
    sniff = sniff_ext(content)
    ct = (ctype or "").split(";")[0].strip().lower()
    if sniff is None and not ct.startswith("image/"):
        return None
    ext = sniff or _CT_EXT.get(ct) or url.rsplit(".", 1)[-1].lower()
    if ext not in IMG_EXTS:
        ext = "png"
    return content, ext
```

- [ ] **Step 4: Re-point `characters.py` at the new module**

In `backend/src/grimoire/store/characters.py`:

1. Delete the now-moved definitions: `_AVATAR_MAX_BYTES`, `_CT_EXT`, `_IMG_EXTS`, `_sniff_ext`, `_decode_data_uri`, `_MAX_REDIRECTS`, `_AVATAR_UA`, `_SSL_CTX`, `_host_is_blocked`, `_http_get_bytes`, `_download_url` (the block spanning roughly lines 209–340, but KEEP `_avatar_candidates` and `_download_avatar`).
2. Remove now-unused imports from the top of the file if they are no longer referenced anywhere else in `characters.py`: `base64`, `ipaddress`, `socket`, `ssl`, `certifi`, `httpx`, and `urlparse` (verify each with a search before removing — leave any still used).
3. Add near the other store imports: `from . import fetch`
4. Update `_download_avatar` to call the shared helpers:

```python
def _download_avatar(card: dict) -> tuple[bytes, str] | None:
    """Best-effort avatar bytes from a card: embedded data-URI first, else a URL fetch."""
    for uri in _avatar_candidates(card):
        embedded = fetch.decode_data_uri(uri)
        if embedded:
            return embedded
        if uri.startswith(("http://", "https://")):
            got = fetch.download_url(uri)
            if got:
                return got
    return None
```

- [ ] **Step 5: Run the new and existing tests**

Run: `python -m pytest tests/test_fetch_store.py tests/test_characters_store.py -v`
Expected: PASS (new fetch tests pass; characters tests unchanged and green).

- [ ] **Step 6: Run the full backend suite to catch import fallout**

Run: `python -m pytest -q`
Expected: PASS — no regressions from the move.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/fetch.py backend/src/grimoire/store/characters.py backend/tests/test_fetch_store.py
git commit -m "refactor: extract shared image-fetch helpers into store/fetch.py"
```

---

### Task 2: Reference scanner (`find_refs`)

Pure function that finds every image reference in one text field, non-overlapping, in priority order, skipping already-local refs.

**Files:**
- Create: `backend/src/grimoire/store/localize.py`
- Test: `backend/tests/test_localize_store.py`

**Interfaces:**
- Produces:
  - `localize.Ref` — `@dataclass(frozen=True)` with `start: int`, `end: int`, `url: str`
  - `localize.find_refs(text: str) -> list[Ref]` — ordered by `start`, spans non-overlapping; excludes refs whose URL already starts with `/api/worlds/`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_localize_store.py`:

```python
from grimoire.store import localize


def test_find_markdown_image():
    refs = localize.find_refs("see ![cat](https://h/cat.png) here")
    assert [r.url for r in refs] == ["https://h/cat.png"]


def test_find_markdown_image_with_title():
    refs = localize.find_refs('![a](https://h/a.png "title")')
    assert [r.url for r in refs] == ["https://h/a.png"]


def test_find_html_img():
    refs = localize.find_refs('<img alt="x" src="https://h/b.jpg" width="2">')
    assert [r.url for r in refs] == ["https://h/b.jpg"]


def test_find_data_uri():
    text = "x ![p](data:image/png;base64,AAAA) y"
    refs = localize.find_refs(text)
    assert [r.url for r in refs] == ["data:image/png;base64,AAAA"]


def test_find_bare_url():
    refs = localize.find_refs("look at https://h/pic.gif now")
    assert [r.url for r in refs] == ["https://h/pic.gif"]


def test_bare_url_does_not_double_match_markdown_url():
    # the URL inside the markdown image must be matched once, not also as a bare url
    refs = localize.find_refs("![a](https://h/a.png)")
    assert len(refs) == 1
    assert refs[0].url == "https://h/a.png"


def test_skips_already_local_ref():
    refs = localize.find_refs("![a](/api/worlds/w/characters/c/versions/v/images/embed-abc)")
    assert refs == []


def test_spans_are_non_overlapping_and_ordered():
    text = "![a](https://h/a.png) and <img src='https://h/b.png'>"
    refs = localize.find_refs(text)
    assert [r.url for r in refs] == ["https://h/a.png", "https://h/b.png"]
    assert all(refs[i].end <= refs[i + 1].start for i in range(len(refs) - 1))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_localize_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.localize'`

- [ ] **Step 3: Implement `Ref` and `find_refs`**

Create `backend/src/grimoire/store/localize.py`:

```python
"""Scan a card's text fields for image references and localize them.

Finds markdown images, HTML <img> tags, data-URIs, and bare URLs; downloads each
into the per-version asset store; rewrites the text to the local serving URL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Order matters: earlier patterns win overlapping spans. Each has one capture
# group holding the URL/data-uri.
_PATTERNS = [
    re.compile(r"!\[[^\]]*\]\(\s*(<[^>]+>|[^)\s]+)"),          # markdown image: ![alt](url ...)
    re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE),  # <img src="url">
    re.compile(r"(data:image/[^\s)\"'>]+)"),                    # bare/standalone data-uri
    re.compile(r"(https?://[^\s)\"'>\]]+)"),                    # bare url
]

_LOCAL_PREFIX = "/api/worlds/"


@dataclass(frozen=True)
class Ref:
    start: int
    end: int
    url: str


def _clean_url(u: str) -> str:
    u = u.strip()
    if u.startswith("<") and u.endswith(">"):  # markdown <url> form
        u = u[1:-1]
    return u.rstrip(".,);")  # trailing punctuation that commonly abuts bare urls


def find_refs(text: str) -> list[Ref]:
    if not isinstance(text, str) or not text:
        return []
    taken: list[Ref] = []
    occupied: list[tuple[int, int]] = []

    def overlaps(s: int, e: int) -> bool:
        return any(s < eo and so < e for so, eo in occupied)

    for pat in _PATTERNS:
        for m in pat.finditer(text):
            s, e = m.start(1), m.end(1)
            if overlaps(s, e):
                continue
            url = _clean_url(m.group(1))
            if not url or url.startswith(_LOCAL_PREFIX):
                occupied.append((s, e))  # claim span so a later bare-url pass skips it
                continue
            taken.append(Ref(s, e, url))
            occupied.append((s, e))

    taken.sort(key=lambda r: r.start)
    return taken
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_localize_store.py -v`
Expected: PASS (all `find_refs` tests green).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/localize.py backend/tests/test_localize_store.py
git commit -m "feat: add image-reference scanner for card localization"
```

---

### Task 3: Localizer generator (`localize_card`)

Walk localizable fields, download/store each ref, rewrite spans, yield progress; enforce the cap and dedupe by content hash.

**Files:**
- Modify: `backend/src/grimoire/store/localize.py`
- Test: `backend/tests/test_localize_store.py`

**Interfaces:**
- Consumes: `Ref`, `find_refs` (Task 2); `assets.put_image` (existing); `fetch.download_url`, `fetch.decode_data_uri` (Task 1).
- Produces:
  - `localize.localize_card(card: dict, root, cid: str, vid: str, wid: str, *, fetch=..., cap: int | None = None) -> Iterator[dict]` — yields `{"total": N}`, then `{"done": k, "total": N}` per ref, then `{"summary": {...}}`. Mutates `card` in place. The `fetch` kwarg defaults to `grimoire.store.fetch.download_url` and is `(url) -> tuple[bytes, str] | None`.
  - Summary dict shape: `{"total": int, "localized": int, "skipped": int, "failed": int, "capped": bool}`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_localize_store.py`:

```python
import re as _re

from grimoire.store import assets


_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_GIF = b"GIF89a" + b"\x00" * 32


def _fake_fetch(mapping):
    def f(url):
        return mapping.get(url)
    return f


def _run(card, tmp_path, cid="c", vid="v", wid="w", **kw):
    events = list(localize.localize_card(card, tmp_path, cid, vid, wid, **kw))
    return events


def test_localizes_markdown_image_and_rewrites(tmp_path):
    card = {"data": {"description": "x ![a](https://h/a.png) y", "alternate_greetings": []}}
    fetch = _fake_fetch({"https://h/a.png": (_PNG, "png")})
    events = _run(card, tmp_path, fetch=fetch)
    assert events[0] == {"total": 1}
    summary = events[-1]["summary"]
    assert summary["localized"] == 1 and summary["failed"] == 0
    desc = card["data"]["description"]
    m = _re.search(r"/api/worlds/w/characters/c/versions/v/images/(embed-[0-9a-f]{12})", desc)
    assert m, desc
    # the stored file exists with the hashed name
    assert assets.image_path(tmp_path, "c", "v", m.group(1)) is not None


def test_non_image_is_left_untouched(tmp_path):
    card = {"data": {"description": "see https://h/page now", "alternate_greetings": []}}
    fetch = _fake_fetch({})  # returns None -> not an image
    events = _run(card, tmp_path, fetch=fetch)
    assert card["data"]["description"] == "see https://h/page now"
    assert events[-1]["summary"]["skipped"] == 1
    assert events[-1]["summary"]["localized"] == 0


def test_dedupes_identical_bytes(tmp_path):
    card = {"data": {
        "description": "![a](https://h/a.png)",
        "personality": "![b](https://h/b.png)",
        "alternate_greetings": [],
    }}
    fetch = _fake_fetch({"https://h/a.png": (_PNG, "png"), "https://h/b.png": (_PNG, "png")})
    _run(card, tmp_path, fetch=fetch)
    names = {p["name"] for p in assets.list_images(tmp_path, "c", "v")}
    assert len(names) == 1  # same bytes -> one stored file
    # both fields rewritten to that same name
    name = names.pop()
    assert name in card["data"]["description"]
    assert name in card["data"]["personality"]


def test_rescan_is_idempotent(tmp_path):
    card = {"data": {"description": "![a](https://h/a.png)", "alternate_greetings": []}}
    fetch = _fake_fetch({"https://h/a.png": (_PNG, "png")})
    _run(card, tmp_path, fetch=fetch)
    after_first = card["data"]["description"]
    events = _run(card, tmp_path, fetch=fetch)  # second pass
    assert card["data"]["description"] == after_first
    assert events[0] == {"total": 0}  # nothing left to localize


def test_data_uri_is_decoded_without_fetch(tmp_path):
    uri = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="
    card = {"data": {"description": f"![p]({uri})", "alternate_greetings": []}}

    def boom(url):  # fetch must NOT be called for data-uris
        raise AssertionError("fetch called for data-uri")

    _run(card, tmp_path, fetch=boom)
    assert "/api/worlds/w/characters/c/versions/v/images/embed-" in card["data"]["description"]


def test_cap_scales_with_greetings(tmp_path):
    # 0 alt greetings -> cap 10; make 12 refs, expect 10 localized + capped
    urls = [f"https://h/{i}.png" for i in range(12)]
    body = " ".join(f"![{i}]({u})" for i, u in enumerate(urls))
    card = {"data": {"description": body, "first_mes": "hi", "alternate_greetings": []}}
    # each distinct url -> distinct bytes so no dedupe masks the cap
    fetch = _fake_fetch({u: (_PNG[:8] + bytes([i]) + _PNG[9:], "png") for i, u in enumerate(urls)})
    events = _run(card, tmp_path, fetch=fetch)
    summary = events[-1]["summary"]
    assert summary["localized"] == 10
    assert summary["capped"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_localize_store.py -k "localize or non_image or dedupe or rescan or data_uri or cap" -v`
Expected: FAIL — `AttributeError: module 'grimoire.store.localize' has no attribute 'localize_card'`

- [ ] **Step 3: Implement `localize_card`**

Append to `backend/src/grimoire/store/localize.py` (add `import hashlib` and `from collections.abc import Iterator` to the imports at the top; add `from . import assets, fetch as _fetch`):

```python
_TEXT_FIELDS = ("description", "personality", "scenario", "first_mes",
                "mes_example", "system_prompt", "post_history_instructions",
                "creator_notes")


def _iter_fields(card: dict):
    """Yield (getter, setter) for every localizable text field of the card.

    getter() -> str; setter(new_text) writes it back into the card structure.
    """
    data = card.get("data") or {}

    for key in _TEXT_FIELDS:
        if isinstance(data.get(key), str):
            yield (lambda k=key: data[k]), (lambda v, k=key: data.__setitem__(k, v))

    greetings = data.get("alternate_greetings")
    if isinstance(greetings, list):
        for i, g in enumerate(greetings):
            if isinstance(g, str):
                yield (lambda i=i: greetings[i]), (lambda v, i=i: greetings.__setitem__(i, v))

    book = data.get("character_book")
    entries = (book or {}).get("entries") if isinstance(book, dict) else None
    if isinstance(entries, list):
        for i, ent in enumerate(entries):
            if isinstance(ent, dict) and isinstance(ent.get("content"), str):
                yield (lambda i=i: entries[i]["content"]), (lambda v, i=i: entries[i].__setitem__("content", v))


def _serving_url(wid: str, cid: str, vid: str, name: str) -> str:
    return f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}"


def localize_card(card, root, cid, vid, wid, *, fetch=None, cap=None):
    if fetch is None:
        fetch = _fetch.download_url
    data = card.get("data") or {}
    if cap is None:
        alts = data.get("alternate_greetings")
        n_greetings = 1 + (len(alts) if isinstance(alts, list) else 0)
        cap = 10 * n_greetings

    fields = list(_iter_fields(card))
    plan = [(getter, setter, ref) for getter, setter in fields for ref in find_refs(getter())]
    total = len(plan)
    yield {"total": total}

    localized = skipped = failed = 0
    capped = False
    seen: dict[str, str] = {}        # raw url/data-uri -> stored asset name
    edits: dict[int, list[tuple[Ref, str]]] = {}  # field index -> [(ref, name)]
    field_index = {id(setter): idx for idx, (_, setter) in enumerate(fields)}
    downloads = 0

    for done, (getter, setter, ref) in enumerate(plan, start=1):
        name = None
        if ref.url in seen:
            name = seen[ref.url]
        elif ref.url.startswith("data:"):
            got = _fetch.decode_data_uri(ref.url)
            if got is None:
                skipped += 1
            else:
                name = _store(root, cid, vid, got)
        else:
            if downloads >= cap:
                capped = True
                skipped += 1
            else:
                downloads += 1
                got = fetch(ref.url)
                if got is None:
                    skipped += 1
                else:
                    name = _store(root, cid, vid, got)
        if name is not None:
            seen[ref.url] = name
            idx = field_index[id(setter)]
            edits.setdefault(idx, []).append((ref, name))
            localized += 1
        yield {"done": done, "total": total}

    # apply rewrites per field, last span first so offsets stay valid
    for idx, items in edits.items():
        getter, setter = fields[idx]
        text = getter()
        for ref, name in sorted(items, key=lambda it: it[0].start, reverse=True):
            url = _serving_url(wid, cid, vid, name)
            text = text[:ref.start] + url + text[ref.end:]
        setter(text)

    yield {"summary": {"total": total, "localized": localized,
                       "skipped": skipped, "failed": failed, "capped": capped}}


def _store(root, cid, vid, got) -> str:
    raw, ext = got
    name = "embed-" + hashlib.sha256(raw).hexdigest()[:12]
    assets.put_image(root, cid, vid, name, raw, ext)
    return name
```

Note: `failed` is reserved for future distinction; current code counts every miss as `skipped`. Keep the field in the summary for the frontend contract.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_localize_store.py -v`
Expected: PASS (all scanner + localizer tests green).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/localize.py backend/tests/test_localize_store.py
git commit -m "feat: localize_card downloads, stores, and rewrites card image refs"
```

---

### Task 4: SSE localize endpoint

Expose `localize_card` over an SSE route that persists the rewritten card after streaming.

**Files:**
- Modify: `backend/src/grimoire/routes.py`
- Modify: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.localize.localize_card`, `store.characters.read_card`, `store.characters.update_version`, `store.characters.CharacterNotFound`, `store.characters.VersionNotFound`, `_world_root_or_404` (existing).
- Produces: `POST /api/worlds/{wid}/characters/{cid}/versions/{vid}/localize` → `text/event-stream`, frames: `data: {"total": N}`, `data: {"done": k, "total": N}`, `data: {"summary": {...}}`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_routes.py` (it already imports `json`, `io`, has the `client` fixture and `store`):

```python
def test_localize_endpoint_streams_and_rewrites(client, monkeypatch):
    # create a world + character with a remote image in the description
    wid = client.post("/api/worlds", json={"name": "W"}).json()["id"]
    card = {
        "spec": "chara_card_v3", "spec_version": "3.0",
        "data": {"name": "Img", "description": "![a](https://h/a.png)",
                 "alternate_greetings": []},
    }
    blob = io.BytesIO(json.dumps(card).encode())
    r = client.post(f"/api/worlds/{wid}/characters/import",
                    files={"file": ("c.json", blob, "application/json")},
                    data={"format": "json"})
    cid, vid = r.json()["character"], r.json()["version"]

    # stub the network download so the test is offline + deterministic
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    monkeypatch.setattr(store.fetch, "download_url", lambda url: (png, "png"))

    resp = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/localize")
    assert resp.status_code == 200
    events = []
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:"):].strip()))
    assert events[0] == {"total": 1}
    assert events[-1]["summary"]["localized"] == 1

    # the persisted card now points at a local image
    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    saved = next(v for v in detail["versions"] if v["id"] == vid)
    # re-read the card via export to confirm rewrite persisted
    exported = client.get(
        f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/export?format=json").json()
    assert "/api/worlds/" in exported["data"]["description"]


def test_localize_endpoint_no_refs_short_circuits(client, monkeypatch):
    wid = client.post("/api/worlds", json={"name": "W2"}).json()["id"]
    card = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Plain", "description": "no images", "alternate_greetings": []}}
    blob = io.BytesIO(json.dumps(card).encode())
    r = client.post(f"/api/worlds/{wid}/characters/import",
                    files={"file": ("c.json", blob, "application/json")},
                    data={"format": "json"})
    cid, vid = r.json()["character"], r.json()["version"]
    resp = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/localize")
    events = [json.loads(l[len("data:"):].strip())
              for l in resp.text.splitlines() if l.startswith("data:")]
    assert events[0] == {"total": 0}
    assert events[-1]["summary"]["total"] == 0
```

Note: if `export` returns raw card bytes rather than parsed JSON, adjust the assertion to `json.loads(resp_export.content)`. Verify the export route's response shape first (`get_character_export` returns a `Response` with JSON bytes), and read it accordingly in the test.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_routes.py -k localize -v`
Expected: FAIL — 404 / route not found (`localize` endpoint doesn't exist yet).

- [ ] **Step 3: Implement the endpoint**

In `backend/src/grimoire/routes.py`, add after the export route (near line 451). `json`, `StreamingResponse`, and `store` are already imported:

```python
@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/localize")
def post_character_localize(wid: str, cid: str, vid: str):
    root = _world_root_or_404(wid)
    try:
        card = store.characters.read_card(root, cid, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")

    def event_stream():
        changed = False
        for ev in store.localize.localize_card(card, root, cid, vid, wid):
            if ev.get("summary", {}).get("localized"):
                changed = True
            yield f"data: {json.dumps(ev)}\n\n"
        if changed:
            store.characters.update_version(root, cid, vid, card)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

- [ ] **Step 4: Ensure `store.localize` is importable as `store.localize`**

Check `backend/src/grimoire/store/__init__.py` — confirm submodules are exposed the same way `fetch`/`localize` siblings are (e.g. `characters`, `assets`). If the package uses explicit imports, add `from . import localize` (and `fetch`) there so `store.localize` / `store.fetch` resolve. Run: `python -c "from grimoire import store; store.localize; store.fetch"` — expected: no error.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_routes.py -k localize -v`
Expected: PASS (both localize endpoint tests green).

- [ ] **Step 6: Run the full backend suite**

Run: `python -m pytest -q`
Expected: PASS — no regressions.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py backend/src/grimoire/store/__init__.py
git commit -m "feat: SSE endpoint to localize a character version's image refs"
```

---

### Task 5: Frontend API client method

Add a streaming `localizeImages` to the api client and a typed progress event.

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/stream.ts`
- Test: `frontend/src/api/client.test.ts`

**Interfaces:**
- Consumes: `streamPost` (existing in `client.ts`), `parseSSEChunk` (existing in `stream.ts`).
- Produces:
  - In `stream.ts`: `export type LocalizeEvent = { total?: number; done?: number; summary?: LocalizeSummary }` and `export type LocalizeSummary = { total: number; localized: number; skipped: number; failed: number; capped: boolean }`.
  - In `client.ts`: `api.localizeImages(wid, cid, vid, onEvent: (e: LocalizeEvent) => void): Promise<void>`.

- [ ] **Step 1: Write the failing test**

Look at `frontend/src/api/client.test.ts` for the existing fetch-mock pattern and mirror it. Add:

```ts
import { describe, it, expect, vi } from "vitest";
import { api } from "./client";

function sseResponse(frames: string[]): Response {
  const body = frames.map((f) => `data: ${f}\n\n`).join("");
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body));
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

describe("localizeImages", () => {
  it("emits parsed progress + summary events", async () => {
    const frames = [
      JSON.stringify({ total: 1 }),
      JSON.stringify({ done: 1, total: 1 }),
      JSON.stringify({ summary: { total: 1, localized: 1, skipped: 0, failed: 0, capped: false } }),
    ];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse(frames));
    const events: any[] = [];
    await api.localizeImages("w", "c", "v", (e) => events.push(e));
    expect(events[0]).toEqual({ total: 1 });
    expect(events[2].summary.localized).toBe(1);
    vi.restoreAllMocks();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npm test -- client.test`
Expected: FAIL — `api.localizeImages is not a function`.

- [ ] **Step 3: Extend the SSE event type**

`parseSSEChunk` is typed to `ChatEvent`. Widen it to accept localize fields without breaking chat. In `frontend/src/api/stream.ts`, change the `ChatEvent` type's usage by adding a generic, OR add the localize fields. Minimal approach — make `parseSSEChunk` generic:

```ts
export type ChatEvent = { delta?: string; done?: boolean; error?: { detail: string; kind: string } };
export type LocalizeSummary = {
  total: number; localized: number; skipped: number; failed: number; capped: boolean;
};
export type LocalizeEvent = { total?: number; done?: number; summary?: LocalizeSummary };

export function parseSSEChunk<T = ChatEvent>(
  buffer: string,
  chunk: string,
  emit: (event: T) => void,
): string {
  buffer += chunk;
  let idx: number;
  while ((idx = buffer.indexOf("\n\n")) !== -1) {
    const raw = buffer.slice(0, idx);
    buffer = buffer.slice(idx + 2);
    const line = raw.split("\n").find((l) => l.startsWith("data:"));
    if (!line) continue;
    const data = line.slice("data:".length).trim();
    if (!data) continue;
    try {
      emit(JSON.parse(data) as T);
    } catch {
      // ignore malformed event fragments
    }
  }
  return buffer;
}
```

- [ ] **Step 4: Add a generic `streamPost` overload + `localizeImages`**

In `frontend/src/api/client.ts`, make `streamPost` generic so it can carry `LocalizeEvent`, and add the method. Update the `streamPost` signature and its `parseSSEChunk` call:

```ts
async function streamPost<T = ChatEvent>(
  path: string,
  body: unknown,
  onEvent: (e: T) => void,
): Promise<void> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.detail ?? res.statusText, data.kind);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = parseSSEChunk<T>(buffer, decoder.decode(value, { stream: true }), onEvent);
  }
}
```

Add to the imports from `./stream`: `LocalizeEvent`. Add to the `api` object (next to `importCharacter`):

```ts
  localizeImages: (wid: string, cid: string, vid: string, onEvent: (e: LocalizeEvent) => void) =>
    streamPost<LocalizeEvent>(
      `/api/worlds/${wid}/characters/${cid}/versions/${vid}/localize`, undefined, onEvent),
```

- [ ] **Step 5: Run tests to verify they pass**

Run (from `frontend/`): `npm test -- client.test stream.test`
Expected: PASS — `localizeImages` test green; existing chat/stream tests still green.

- [ ] **Step 6: Type-check**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no errors (the generic change is backward-compatible with chat usage).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/stream.ts frontend/src/api/client.test.ts
git commit -m "feat: api.localizeImages streams localize progress over SSE"
```

---

### Task 6: CharacterEditor — progress bar, auto-run on import, manual button

Wire the UI: a progress bar fed by localize events, auto-fired after import, plus a manual "Localize images" button and a one-line summary.

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx`

**Interfaces:**
- Consumes: `api.localizeImages` and `LocalizeEvent`/`LocalizeSummary` (Task 5); existing `detail`, `vid`, `wid`, `select`, `reload`, `loadVersion` state/handlers.
- Produces: no new exports; internal component state only.

- [ ] **Step 1: Add localize state and a runner**

Near the other `useState` hooks in `CharacterEditor`, add:

```tsx
const [localizeProg, setLocalizeProg] = useState<{ done: number; total: number } | null>(null);
const [localizeMsg, setLocalizeMsg] = useState<string | null>(null);

async function runLocalize(cid: string, version: string) {
  setLocalizeMsg(null);
  setLocalizeProg({ done: 0, total: 0 });
  try {
    await api.localizeImages(wid, cid, version, (e) => {
      if (typeof e.total === "number" && e.done === undefined && !e.summary) {
        setLocalizeProg({ done: 0, total: e.total });
      } else if (typeof e.done === "number") {
        setLocalizeProg((p) => ({ done: e.done!, total: e.total ?? p?.total ?? 0 }));
      } else if (e.summary) {
        const s = e.summary;
        setLocalizeMsg(
          s.total === 0
            ? "No remote images found"
            : `Localized ${s.localized} image${s.localized === 1 ? "" : "s"}` +
              (s.skipped ? `, skipped ${s.skipped}` : "") +
              (s.capped ? " (download cap reached)" : ""),
        );
      }
    });
  } catch (err: any) {
    setLocalizeMsg(`Localize failed: ${err.detail ?? String(err)}`);
  } finally {
    setLocalizeProg(null);
  }
}
```

- [ ] **Step 2: Auto-run after single-file import**

In `onImport`, after the loop sets `last` and before/after `openDetail`, when exactly one file imported, run localize for the created version. Modify the tail of `onImport`:

```tsx
    e.target.value = "";
    await reload();
    if (failures.length) setError(`Could not import — ${failures.join("; ")}`);
    else if (files.length === 1 && last) {
      await openDetail(last);
      // openDetail loads detail+vid; localize the just-imported default version
      const d = await api.readCharacter(wid, last);
      await runLocalize(last, d.meta.default_version);
    }
```

And in `onImportVersion`, after `loadVersion(d, version)`:

```tsx
      loadVersion(d, version);
      await reload();
      await runLocalize(detail.meta.id, version);
```

(Place `runLocalize` call inside the existing `try`, before the `catch`.)

- [ ] **Step 3: Add the manual button + progress bar to the render**

Find where the avatar controls / version actions render (near `onAvatar`/`removeAvatar`/`importBook` buttons). Add a button and a progress/summary area:

```tsx
<button type="button" onClick={() => detail && runLocalize(detail.meta.id, vid)}
        disabled={!!localizeProg}>
  {localizeProg ? "Localizing…" : "Localize images"}
</button>
{localizeProg && (
  <div className="localize-progress">
    <progress value={localizeProg.done} max={localizeProg.total || 1} />
    <span>{localizeProg.done}/{localizeProg.total}</span>
  </div>
)}
{localizeMsg && <div className="localize-msg">{localizeMsg}</div>}
```

- [ ] **Step 4: After a successful localize, refresh the version view**

So rewritten text shows in the editor, after the `await api.localizeImages(...)` resolves in `runLocalize` (in the `try`, after the await), re-load the current version if it's the one being viewed:

```tsx
    await api.localizeImages(wid, cid, version, (e) => { /* ...handlers above... */ });
    if (detail && detail.meta.id === cid) {
      const d = await api.readCharacter(wid, cid);
      setDetail(d);
      loadVersion(d, version);
    }
```

(Fold this into Step 1's `runLocalize` body rather than duplicating — the snippet here shows the added refresh after the stream completes.)

- [ ] **Step 5: Type-check and build**

Run (from `frontend/`): `npx tsc --noEmit && npm run build`
Expected: no type errors; build succeeds.

- [ ] **Step 6: Manual smoke (optional but recommended)**

Start the app, import a JSON card whose `description` contains a public markdown image, confirm the progress bar appears and the description rewrites to a `/api/worlds/...` URL that renders. (Use the `/run` skill or the project's dev command.)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/CharacterEditor.tsx
git commit -m "feat: localize card images on import with a progress bar + manual rescan"
```

---

## Self-Review

**Spec coverage:**
- Images only, four reference forms → Task 2 (`find_refs` patterns) + Task 1 (image validation in `download_url`). ✓
- Localizable fields incl. greetings + lorebook entries → Task 3 (`_iter_fields`). ✓
- Content-hash naming + dedupe + idempotent re-scan → Task 3 (`_store`, `seen`, tests). ✓
- Rewrite to local serving URL, last-span-first → Task 3 (`_serving_url`, reversed apply). ✓
- Cap = 10×(1+alt greetings), data-URIs exempt → Task 3 (`cap` calc, `downloads` counter, test). ✓
- SSRF / size / timeout preserved → Task 1 (verbatim move) + Global Constraints. ✓
- Bare-URL safety (non-image untouched) → Task 3 `test_non_image_is_left_untouched`. ✓
- SSE streaming endpoint, persist after → Task 4. ✓
- Frontend progress bar from upfront total, auto-run + manual button + summary → Tasks 5–6. ✓
- Shared fetch extraction → Task 1. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. The one "verify the export response shape" note in Task 4 Step 1 is a real instruction with the fallback spelled out, not a placeholder.

**Type consistency:** `localize_card(card, root, cid, vid, wid, *, fetch, cap)` and its event shapes (`{total}`, `{done,total}`, `{summary}`) match across Tasks 3–4; `LocalizeEvent`/`LocalizeSummary` fields match the Python summary keys (`total/localized/skipped/failed/capped`); `find_refs`/`Ref` used consistently in Tasks 2–3; `api.localizeImages` signature matches its call sites in Task 6.
