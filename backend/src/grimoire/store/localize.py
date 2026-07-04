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
from . import greetings as _greetings

# Order matters: earlier patterns win overlapping spans. Each has one capture
# group holding the URL/data-uri.
_MD_IMG = re.compile(r"!\[[^\]]*\]\(\s*(<[^>]+>|[^)\s]+)")                      # ![alt](url ...)
_IMG_TAG = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)  # <img src="url">
_DATA_URI = re.compile(r"(data:image/[^\s)\"'>\]]+)")                          # bare/standalone data-uri
_BARE_URL = re.compile(r"(https?://[^\s)\"'>\]]+)")                            # bare url
_PATTERNS = [_MD_IMG, _IMG_TAG, _DATA_URI, _BARE_URL]

_LOCAL_PREFIX = "/api/worlds/"


@dataclass(frozen=True)
class Ref:
    start: int
    end: int
    url: str
    # The span actually rewritten during localization, and how. Defaults to the URL
    # span ([start, end]) with the local URL spliced in place. An HTML <img> tag sets
    # a wider span covering the whole tag plus `as_markdown`, so the tag is replaced
    # with a markdown image — react-markdown drops raw HTML, so a localized <img>
    # would otherwise never render.
    repl_start: int = -1
    repl_end: int = -1
    as_markdown: bool = False

    @property
    def span(self) -> tuple[int, int]:
        s = self.start if self.repl_start < 0 else self.repl_start
        e = self.end if self.repl_end < 0 else self.repl_end
        return s, e


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
            if pat is _IMG_TAG:
                # Replace the whole <img …> tag with a markdown image. Claim the full
                # tag span so a second URL attribute can't spawn an overlapping ref.
                close = text.find(">", m.end())
                if close != -1:
                    taken.append(Ref(s, e, url, m.start(), close + 1, True))
                    occupied.append((m.start(), close + 1))
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


def _store(root, cid, vid, got, base: str = "characters") -> str | None:
    """Store downloaded bytes under a content-hash name; None if the store
    rejects them (keeps localization best-effort even for an odd fetcher)."""
    raw, ext = got
    name = "embed-" + hashlib.sha256(raw).hexdigest()[:12]
    try:
        assets.put_image(root, cid, vid, name, raw, ext, base=base)
    except Exception:  # noqa: BLE001 — e.g. unsupported ext from a caller's fetch
        return None
    return name


def _apply_field(getter, setter, items, wid, cid, vid) -> None:
    """Splice each (ref, name) into the field's text, last span first so
    earlier offsets stay valid."""
    text = getter()
    for ref, name in sorted(items, key=lambda it: it[0].span[0], reverse=True):
        local = _serving_url(wid, cid, vid, name)
        start, end = ref.span
        repl = f"![]({local})" if ref.as_markdown else local
        text = text[:start] + repl + text[end:]
    setter(text)


def localize_card(card, root, cid, vid, wid, *, fetch=None, cap=None):
    """Generator: download every referenced image, store it, and rewrite the
    text in `card` in place — yielding {"total": N}, then {"done": k, "total": N,
    "applied": j} per ref, then {"summary": {...}}. The caller persists `card`.

    Each field's rewrites are applied as soon as its last ref finishes (before
    that ref's progress event), so a generator closed mid-stream — a client
    disconnect — keeps every completed field's rewrites; `applied` counts the
    refs whose rewrite has landed in `card`, letting the caller decide whether
    a partial run is worth persisting."""
    if fetch is None:
        fetch = _fetch.download_url
    data = card.get("data") or {}
    if cap is None:
        alts = data.get("alternate_greetings")
        n_greetings = 1 + (len(alts) if isinstance(alts, list) else 0)
        cap = 10 * n_greetings

    fields = list(_iter_fields(card))
    # refs found up front, before any rewrite — fields are independent strings,
    # so applying one field never shifts another field's spans
    plan = [(getter, setter, find_refs(getter())) for getter, setter in fields]
    total = sum(len(refs) for _g, _s, refs in plan)
    yield {"total": total}

    localized = skipped = failed = 0
    applied = done = downloads = 0
    capped = False
    seen: dict[str, str] = {}  # raw url/data-uri -> stored asset name

    for getter, setter, refs in plan:
        items: list[tuple[Ref, str]] = []  # this field's pending rewrites
        for i, ref in enumerate(refs):
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
                items.append((ref, name))
                localized += 1
            done += 1
            if i == len(refs) - 1 and items:  # field complete: land its rewrites
                _apply_field(getter, setter, items, wid, cid, vid)
                applied += len(items)
            yield {"done": done, "total": total, "applied": applied}

    yield {"summary": {"total": total, "localized": localized,
                       "skipped": skipped, "failed": failed, "capped": capped}}


def localize_greeting(root, gid, wid, *, fetch=None, cap=10) -> dict:
    """Download every image referenced in a world greeting's body into the
    per-greeting asset store (<root>/greetings/<gid>/assets/default/) and
    rewrite the body to the local serving URLs. Best-effort per ref, like
    localize_card; persists via greetings.update_greeting only when at least
    one ref localized. Returns {"total","localized","skipped","failed","capped"}.
    """
    if fetch is None:
        fetch = _fetch.download_url
    text = _greetings.read_greeting(root, gid)["body"]
    refs = find_refs(text)
    localized = skipped = failed = downloads = 0
    capped = False
    seen: dict[str, str] = {}  # raw url/data-uri -> stored asset name
    items: list[tuple[Ref, str]] = []
    for ref in refs:
        name = None
        if ref.url in seen:
            name = seen[ref.url]
        elif ref.url.startswith("data:"):
            got = _fetch.decode_data_uri(ref.url)
            if got is None:
                skipped += 1
            else:
                name = _store(root, gid, "default", got, base="greetings")
                if name is None:
                    failed += 1  # bytes decoded but the store rejected them
        elif downloads >= cap:
            capped = True
            skipped += 1
        else:
            downloads += 1
            try:
                got = fetch(ref.url)
            except Exception:  # noqa: BLE001 — best-effort; a miss never breaks the greeting
                got = None
                failed += 1
            else:
                if got is None:
                    skipped += 1  # download returned None (non-image / blocked host)
            if got is not None:
                name = _store(root, gid, "default", got, base="greetings")
                if name is None:
                    failed += 1  # downloaded but the store rejected the bytes
        if name is not None:
            seen[ref.url] = name
            items.append((ref, name))
            localized += 1
    for ref, name in sorted(items, key=lambda it: it[0].span[0], reverse=True):
        local = f"/api/worlds/{wid}/greetings/{gid}/images/{name}"
        start, end = ref.span
        repl = f"![]({local})" if ref.as_markdown else local
        text = text[:start] + repl + text[end:]
    if items:
        _greetings.update_greeting(root, gid, body=text)
    return {"total": len(refs), "localized": localized, "skipped": skipped,
            "failed": failed, "capped": capped}
