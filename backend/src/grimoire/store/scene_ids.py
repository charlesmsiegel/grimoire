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


#: What one directory entry can hold, the conventional POSIX/NTFS limit.
_MAX_COMPONENT = 255
#: The longest suffix a scene id wears. The transcript's own `.md` is shorter,
#: so budgeting for it alone is what let an id fit `<sid>.md` and overflow
#: `<sid>.alts.json` — and since `_sid_taken` stats the sidecar on every
#: allocation, such an id could not even be created, let alone deleted.
_LONGEST_SUFFIX = len(".alts.json")
#: Room left for what is appended to a finished id: `uniquify`'s `-2`.. tail
#: when a truncation collides, and `repad` widening the number prefix.
_ID_HEADROOM = 8
#: Longest id this will mint. Slugs come from titles and are unbounded, and a
#: title long enough to overflow is a paste, not a name.
MAX_SID = _MAX_COMPONENT - _LONGEST_SUFFIX - _ID_HEADROOM
#: How much title a date section must leave behind. The date is formatted by a
#: calendar provider — plugins live in `<GRIMOIRE_HOME>/calendars/` and are
#: user-authored — so it is no more bounded than a pasted title, and an id that
#: is all date names the day rather than the scene.
_MIN_TITLE = 16


def fit_sid(head: str, title_slug: str) -> str:
    """`head` followed by as much of `title_slug` as `MAX_SID` allows.

    Every id the app mints goes through here, in both spellings — the numbered
    scheme below and `rename_scene`'s legacy `<created>-<slug>` branch. Capping
    inside `format_sid` alone left that second branch unbounded, which is the
    same bug one layer down: the rename lands, then repointing the sidecar
    raises ENAMETOOLONG with the transcript already moved.

    The title is trimmed first, since it is what a caller can shorten by
    renaming. But `head` is not trusted to fit either — `format_sid` bounds the
    date section before assembling it, and this clamps whatever arrives anyway,
    so no caller can hand back an id that overflows. `slugify` emits
    `[a-z0-9-]` only, so one character is one byte and `len` is the size on
    disk. Two long titles can truncate alike; `uniquify` settles that exactly
    as it does for two short ones that match.
    """
    title_slug = title_slug[:max(1, MAX_SID - len(head))].rstrip("-") or "untitled"
    return f"{head}{title_slug}"[:MAX_SID].rstrip("-") or "untitled"


def format_sid(number: int, width: int, date_slug: str | None, title_slug: str) -> str:
    head = f"{number:0{width}d}--"
    if date_slug:
        # Bounded like the title, and for the same reason: a calendar plugin
        # formats this, so nothing upstream caps it. Only the number is truly
        # fixed — it is what orders the scene and what `repad` rewrites.
        date_slug = date_slug[:max(0, MAX_SID - len(head) - len("--") - _MIN_TITLE)]
        date_slug = date_slug.rstrip("-")
    return fit_sid(f"{head}{date_slug}--" if date_slug else head, title_slug)


def date_slug_of(canonical: str) -> str:
    """Filename-safe slug of a canonical moment's date part. The time part is
    dropped — it contains a colon, illegal in Windows filenames."""
    return slugify(canonical.partition("T")[0])
