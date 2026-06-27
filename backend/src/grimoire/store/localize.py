"""Scan a card's text fields for image references and localize them.

Finds markdown images, HTML <img> tags, data-URIs, and bare URLs; downloads each
into the per-version asset store; rewrites the text to the local serving URL.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from . import assets
from . import fetch as _fetch

# Order matters: earlier patterns win overlapping spans. Each has one capture
# group holding the URL/data-uri.
_PATTERNS = [
    re.compile(r"!\[[^\]]*\]\(\s*(<[^>]+>|[^)\s]+)"),          # markdown image: ![alt](url ...)
    re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE),  # <img src="url">
    re.compile(r"(data:image/[^\s)\"'>\]]+)"),                  # bare/standalone data-uri
    re.compile(r"(https?://[^\s)\"'>\]]+)"),                    # bare url
]

_LOCAL_PREFIX = "/api/worlds/"


@dataclass(frozen=True)
class Ref:
    start: int
    end: int
    url: str


def _clean_span(raw: str, s0: int, e0: int) -> tuple[str, int, int]:
    """Trim a raw capture down to the bare URL, keeping the span exact so that
    `text[start:end] == url` (Task 3 splices the local URL into that span).

    Strips surrounding whitespace, a markdown `<url>` wrapper, and trailing
    punctuation (`.,);`) that commonly abuts a bare URL in prose. A URL that
    legitimately ends in `)` (e.g. a Wikipedia `..._(disambiguation)` link) is
    truncated — an accepted best-effort limitation.
    """
    s, e = s0, e0
    while s < e and raw[s - s0].isspace():
        s += 1
    while e > s and raw[e - s0 - 1].isspace():
        e -= 1
    if e - s >= 2 and raw[s - s0] == "<" and raw[e - s0 - 1] == ">":
        s, e = s + 1, e - 1
    while e > s and raw[e - s0 - 1] in ".,);":
        e -= 1
    return raw[s - s0:e - s0], s, e


def find_refs(text: str) -> list[Ref]:
    if not isinstance(text, str) or not text:
        return []
    taken: list[Ref] = []
    occupied: list[tuple[int, int]] = []

    def overlaps(s: int, e: int) -> bool:
        return any(s < eo and so < e for so, eo in occupied)

    for pat in _PATTERNS:
        for m in pat.finditer(text):
            url, s, e = _clean_span(m.group(1), m.start(1), m.end(1))
            if overlaps(s, e):
                continue
            if not url or url.startswith(_LOCAL_PREFIX):
                occupied.append((s, e))  # claim span so a later bare-url pass skips it
                continue
            taken.append(Ref(s, e, url))
            occupied.append((s, e))

    taken.sort(key=lambda r: r.start)
    return taken


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


def _store(root, cid, vid, got) -> str | None:
    """Store downloaded bytes under a content-hash name; None if the store
    rejects them (keeps localize_card best-effort even for an odd fetcher)."""
    raw, ext = got
    name = "embed-" + hashlib.sha256(raw).hexdigest()[:12]
    try:
        assets.put_image(root, cid, vid, name, raw, ext)
    except Exception:  # noqa: BLE001 — e.g. unsupported ext from a caller's fetch
        return None
    return name


def localize_card(card, root, cid, vid, wid, *, fetch=None, cap=None):
    """Generator: download every referenced image, store it, and rewrite the
    text in `card` in place — yielding {"total": N}, then {"done": k, "total": N}
    per ref, then {"summary": {...}}. The caller persists `card` afterward."""
    if fetch is None:
        fetch = _fetch.download_url
    data = card.get("data") or {}
    if cap is None:
        alts = data.get("alternate_greetings")
        n_greetings = 1 + (len(alts) if isinstance(alts, list) else 0)
        cap = 10 * n_greetings

    fields = list(_iter_fields(card))
    # plan: (field index, ref) for every ref in every field
    plan = [(idx, ref) for idx, (getter, _setter) in enumerate(fields)
            for ref in find_refs(getter())]
    total = len(plan)
    yield {"total": total}

    localized = skipped = failed = 0
    capped = False
    seen: dict[str, str] = {}                      # raw url/data-uri -> stored asset name
    edits: dict[int, list[tuple[Ref, str]]] = {}   # field index -> [(ref, name)]
    downloads = 0

    for done, (idx, ref) in enumerate(plan, start=1):
        name = None
        if ref.url in seen:
            name = seen[ref.url]
        elif ref.url.startswith("data:"):
            got = _fetch.decode_data_uri(ref.url)
            if got is None:
                skipped += 1
            else:
                name = _store(root, cid, vid, got)
                if name is None:
                    failed += 1  # bytes decoded but the store rejected them
        elif downloads >= cap:
            capped = True
            skipped += 1
        else:
            downloads += 1
            try:
                got = fetch(ref.url)
            except Exception:  # noqa: BLE001 — best-effort; a miss never breaks the card
                got = None
                failed += 1
                got_failed = True
            else:
                got_failed = False
            if got is not None:
                name = _store(root, cid, vid, got)
                if name is None:
                    failed += 1  # downloaded but the store rejected the bytes
            elif not got_failed:
                skipped += 1  # download returned None (non-image / blocked host)
        if name is not None:
            seen[ref.url] = name
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
