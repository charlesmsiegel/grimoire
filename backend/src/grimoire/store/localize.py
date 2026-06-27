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
