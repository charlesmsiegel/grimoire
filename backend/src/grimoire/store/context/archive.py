"""Archive retrieval: older absorbed scenes pulled back by keyword.

`story._story_entries` injects only the last `recap_depth` chronicle one-liners,
so a scene that has fallen out of that window is unreachable however relevant it
is to what is being said right now. This module matches the same scan window
`world_state.activate` selects world-info with against each chronicle record's
`keywords` -- which `absorb` already extracts -- and hands the matches back as
the "Archive" section.

Two rules that are not `activate`'s:

- **Keyword-only, never always-on.** A keyless lore entry is always on; a
  chronicle record with no keywords is silent. Every absorbed scene would
  otherwise qualify and the section would grow without bound as a campaign runs.
- **Never duplicates the recap.** Scenes inside the recap window (and the scene
  being played) are excluded by the caller, so the same summary cannot arrive
  twice under two headings.

Matches are capped at `archive_depth` and ordered newest scene id first, so the
cap keeps the most recent of an over-broad match rather than an arbitrary slice.
The section is the lowest tier under the packer (`pack.ARCHIVE`) -- the first
thing dropped when the context does not fit.
"""

from __future__ import annotations

from .. import chronicle, config
from . import world_state

def archive_depth() -> int:
    """How many archived scenes may be recalled at once. 0 disables retrieval."""
    try:
        return max(int(config.read_config().get("archive_depth",
                                                config.DEFAULT_ARCHIVE_DEPTH)), 0)
    except (TypeError, ValueError):
        return int(config.DEFAULT_ARCHIVE_DEPTH)


def _archive_entries(cid: str, recent_text: str, exclude: frozenset[str] = frozenset(),
                     depth: int | None = None) -> list[dict]:
    """Chronicle records whose keywords appear in `recent_text`, newest first.

    Same always-on failure policy as the rest of the context build: a garbled
    chronicle.json omits the block rather than failing the turn -- the store may
    live in a synced folder, and no scene is worth losing to a half-written file.
    """
    try:
        if depth is None:
            depth = archive_depth()
        if depth <= 0 or not recent_text.strip():
            return []
        hits = []
        for record in chronicle.read_chronicle(cid).values():
            rid = str(record.get("id") or "")
            if not rid or rid in exclude:
                continue
            keys = [str(k).strip() for k in (record.get("keywords") or []) if str(k).strip()]
            if not keys or not world_state.keyword_hit(keys, recent_text):
                continue
            # summary first: the archive's whole point is the detail the
            # one-line recap dropped. one_line is the fallback for a record
            # absorbed before summaries, or one whose summary came back empty.
            text = str(record.get("summary") or record.get("one_line") or "").strip()
            if not text:
                continue
            hits.append({"id": rid, "date": str(record.get("date") or "").strip(), "text": text})
        hits.sort(key=lambda h: h["id"], reverse=True)
        return hits[:depth]
    except Exception:  # noqa: BLE001 - corrupt chronicle.json / config: omit, don't crash
        return []
