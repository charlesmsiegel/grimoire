"""Bulk record-ref repointing across every store that persists a `<kind>/<id>`.

The twin of `scene_refs`, for the other id in this store that can move.
Reclassification (#119) changes a generic entity's KIND and keeps its id, so
every ledger keyed by the pair has to follow — `store/reclassify.py` is what
decides a record moves, and this is what makes the rest of the campaign agree.

Five stores hold record refs in a shape a rename can follow: `changes` (the
rolling write-back log, whose KEY is the ref), `journal` (the append-only
history's display `ref` and, load-bearingly, the `undo.target` a reversal
resolves through), `pins` (each pin or exclude, which carries its ref in the
record *and* in its key), `provenance` (citations keyed `<ref>#<field>`) and
`sheets` (keyed by filename, so it moves a file rather than rewriting a field —
the position `alternates` is in for scene renames).

**What deliberately does not join this fan-out**, and why each is somebody
else's job rather than an oversight:

- `sync.md`, `deleted.json` and `detached.json`. Those three say what the
  campaign's record IS relative to its world — a base hash, a tombstone, a
  severance — so following a reclassify through them means knowing whether the
  world moved too. `store/reclassify.py` holds that context and rewrites them
  itself.
- The `owners:` line of other records, which is a `<kind>:<id>` ref inside a
  record rather than a ledger key: `entities.rewrite_owner_refs`.
- A scene's `location_history`. It stores bare location ids with no kind beside
  them, so a location leaving `locations` cannot be followed there — there is
  no ref to rewrite, only a history to falsify. It is left exactly as the play
  it records left it; see `reclassify`'s module docstring for what that costs.
"""

from __future__ import annotations

from . import changes, journal, pins, provenance, sheets


def repoint(cid: str, mapping: dict[str, str]) -> None:
    """Follow `{"<kind>/<id>": "<kind>/<id>"}` through every ledger above."""
    mapping = {old: new for old, new in mapping.items() if old != new}
    if not mapping:
        return
    for mod in (changes, journal, pins, provenance, sheets):
        mod.repoint_records(cid, mapping)
