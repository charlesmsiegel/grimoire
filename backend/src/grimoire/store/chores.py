"""Everything the app can notice is worth doing, and what the user has waved off.

Two halves, and the second is the reason the first is worth having.

**Every chore is a live count.** A chore's label is built from a number this
module computes now, and *a chore at zero is not in the list*. That is the
whole contract: a to-do list that can go stale is worse than no to-do list,
because the reader learns to distrust it and then cannot use the one entry that
mattered. Nothing here is stored, cached, or written down — the list is
recomputed on every read, from the same stores the rest of the app reads.

**Ignoring is real.** An ignored chore is not counted anywhere: not in the
rail's badge, not on the world overview's checklist, not in this list's own
total. It moves to its own section with a Restore, so the decision is
reversible rather than forgotten. The set lives in ``<home>/chores.json``,
beside `pricing.json` and for its reason: it is a per-library user judgement,
not a per-campaign fact, and config frontmatter is flat string-scalar.

What is deliberately NOT here: anything whose count would cost a scan
proportional to the library's age. A chore that made opening the page expensive
would be a chore nobody ever opens, and the honest response to "I cannot count
this cheaply" is to leave it out rather than to guess at it. `usage`'s all-time
rollup is the standing example -- its own docstring reserves it for the
all-time view.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import atomic, paths

#: Severity, lowest first. Only used for ordering — the caller decides how to
#: draw it, and nothing here reads it back.
SEVERITIES = ("note", "warn", "alert")


def _path() -> Path:
    return paths.home() / "chores.json"


def ignored() -> set[str]:
    """Chore ids the user has waved off, for this store.

    A missing or unreadable file is an empty set, never an error: this decides
    what a list *shows*, and a malformed judgement file must not stop the app
    telling the user what is waiting.
    """
    p = _path()
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    out = data.get("ignored", [])
    return {str(x) for x in out} if isinstance(out, list) else set()


def set_ignored(chore_id: str, on: bool) -> set[str]:
    """Add or remove one id, returning the new set.

    Read-modify-write of one small file, unlocked: this is a per-user display
    preference on a single-user store, and the worst a lost update can do is
    un-ignore something the reader can ignore again. It is not campaign state
    and takes no campaign lock -- there is no campaign in scope to take one on.
    """
    now = ignored()
    if on:
        now.add(chore_id)
    else:
        now.discard(chore_id)
    atomic.write_text(_path(),
                      json.dumps({"ignored": sorted(now)}, indent=2) + "\n")
    return now
