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
