"""Scene id grammar: <number>--<date-slug>--<title-slug>, date section optional.

The number comes first so lexicographic filename order equals play order
absolutely. Sections are separated by "--", which is unambiguous because
slugify collapses dash runs — no section can contain consecutive dashes.
"""

from __future__ import annotations

from .paths import slugify

MIN_WIDTH = 3


def parse_sid(sid: str) -> dict | None:
    """Split a scene id into its sections; None for ids outside the grammar
    (legacy real-date ids, foreign strings)."""
    parts = sid.split("--")
    if len(parts) == 2:
        num, date, title = parts[0], None, parts[1]
    elif len(parts) == 3:
        num, date, title = parts
    else:
        return None
    if not num.isdigit() or not title:
        return None
    return {"number": int(num), "width": len(num), "date_slug": date, "title_slug": title}


def format_sid(number: int, width: int, date_slug: str | None, title_slug: str) -> str:
    mid = f"{date_slug}--" if date_slug else ""
    return f"{number:0{width}d}--{mid}{title_slug}"


def date_slug_of(canonical: str) -> str:
    """Filename-safe slug of a canonical moment's date part. The time part is
    dropped — it contains a colon, illegal in Windows filenames."""
    return slugify(canonical.partition("T")[0])
