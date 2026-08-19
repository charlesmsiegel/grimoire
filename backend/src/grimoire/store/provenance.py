"""Why each continuity line is there: the quote, the speaker and the certainty
behind every edit that landed.

``absorb/parse.py`` already asks the extractor to cite itself — every staged
edit carries ``quote``, ``speaker`` and a 0–1 ``certainty`` (``CITATION_FIELDS``)
— and ``absorb/routing.py`` weighs those into a band the review panel routes on.
Until this module, all of it was thrown away the moment the edit applied. The
citation existed for exactly as long as the row you were judging it on.

So a dossier said *"Guarded. Will not be alone with the Reeve."* and there was
no way to find out what she said that made the model write it. Keeping the
citation costs disk and no tokens, and it turns every continuity line into
something you can check.

Keyed ``"<kind>/<id>#<field>"`` — the record and the field within it, which is
the granularity the panel renders and the same granularity ``changes.py`` keys
its diffs at, one level finer. Rolling, like ``changes``: only the latest
citation per field is kept, because a field's provenance is the provenance of
the value it currently holds. An older quote explains text that is no longer
there.

Absent provenance is normal and always will be: rows staged by the later absorb
phases (dossier, voice, sheet) rest on no transcript citation to weigh, edits
applied before this module existed have none, and a hand-edited record has none
either. The panel renders an uncited row rather than hiding it — "we do not know
why this is here" is itself worth showing, and it is the honest answer.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import atomic, locks
from .campaigns import paths as campaigns_paths
from .paths import now_iso


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "provenance.json"


def key(edit: dict) -> str | None:
    """``"<kind>/<id>#<field>"`` for one staged edit, or None when the edit
    names no field of no record.

    Every component is stringified for the reason ``conflicts.target_key``
    stringifies its own: these come off a client PUT body, and a `target` id
    that arrives as a dict would otherwise land in the JSON key.
    """
    if not isinstance(edit, dict):
        return None
    target = edit.get("target")
    if not isinstance(target, dict):
        return None
    kind, rid = target.get("kind"), target.get("id")
    if not isinstance(kind, str) or not isinstance(rid, str) or not kind or not rid:
        return None
    field = edit.get("field")
    field = field if isinstance(field, str) else ""
    return f"{kind}/{rid}#{field}"


def row(edit: dict, sid: str) -> dict | None:
    """The citation to keep for one applied edit, or None when it carries none.

    `band` is stored rather than recomputed on read: it is
    ``routing.band(certainty * WEIGHTS[authority])``, and a client deriving it
    would be a second copy of that table — which is exactly the kind of drift
    that ends with the panel and the review disagreeing about the same row.
    """
    review = edit.get("review")
    if not isinstance(review, dict):
        return None
    quote = review.get("quote")
    speaker = review.get("speaker")
    # A row with no quote at all is uncited, and an uncited row is what the
    # panel shows when there is no entry. Writing one would be storing the
    # absence of information.
    if not isinstance(quote, str) or not quote.strip():
        return None
    certainty = review.get("certainty")
    return {
        "quote": quote.strip(),
        "speaker": speaker.strip() if isinstance(speaker, str) else "",
        "certainty": float(certainty) if isinstance(certainty, (int, float))
                     and not isinstance(certainty, bool) else None,
        "authority": review.get("authority") if isinstance(review.get("authority"), str) else "",
        "band": review.get("band") if isinstance(review.get("band"), str) else "",
        "scene": sid,
        "recorded": now_iso(),
    }


def read(cid: str) -> dict:
    """Every citation on file. Tolerant of a garbled or hand-edited file for the
    reason `changes.read` is: this backs a display panel, and one bad byte must
    cost the markers rather than the page."""
    p = _path(cid)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def record(cid: str, rows: dict[str, dict]) -> None:
    """Upsert the cited fields, replacing any prior entry. No-op when nothing
    was cited.

    Takes the campaign lock, which the one caller (`absorb.apply.apply_edits`,
    under `PUT /chronicle`) already holds — the lock is an RLock, so the
    reentrant take is free, and taking it here is what lets this module be
    inside the domain rather than another entry on `locks.UNREVIEWED`'s frozen
    backlog. It matters on its own terms too: this is a read-modify-write of a
    whole file, so two unserialized callers lose one of the two writes.
    """
    if not rows:
        return
    with locks.campaign_lock(cid):
        data = read(cid)
        data.update(rows)
        atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


def forget(cid: str, keys) -> None:
    """Drop the citations for these fields. No-op for a field that has none.

    The module's rule is that a citation explains the value the field currently
    holds; keeping an older one would have the panel explaining text that is no
    longer there. Every writer upserts, which is enough while values only move
    forward -- and `store/undo.py` (#31) is the first thing that moves one
    BACKWARD. The quote that justified the edit does not justify the value the
    reversal put back, and there is no earlier citation to fall back to (the
    upsert overwrote it), so the honest state is uncited: "we do not know why
    this is here", which the panel already renders and which is now true.

    Takes the campaign lock for the reason `record` does -- a read-modify-write
    of one whole file -- and its caller holds it already, reentrantly.
    """
    keys = [k for k in keys if isinstance(k, str)]
    if not keys:
        return
    with locks.campaign_lock(cid):
        data = read(cid)
        if not any(k in data for k in keys):
            return
        for k in keys:
            data.pop(k, None)
        atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids in each row's ``scene`` field.

    A scene's id is its filename stem, so the first date set on a scene moves
    it (`scenes.moment._stamp_start_date`) and every store holding that id has
    to follow. This one was missing from `scene_refs.repoint`'s fan-out, so a
    renamed scene left its citations pointing at an id no longer on disk: the
    panel could still render the quote, but nothing could get back to the post
    it came from, and `forget_scene` would no longer find these rows to drop
    when that scene was deleted -- the citations would outlive the scene
    entirely.

    Takes the campaign lock for the reason `record` and `forget` do: a
    read-modify-write of one whole file.
    """
    mapping = {old: new for old, new in mapping.items() if old != new}
    if not mapping:
        return
    with locks.campaign_lock(cid):
        data = read(cid)
        hit = False
        for row in data.values():
            if isinstance(row, dict) and row.get("scene") in mapping:
                row["scene"] = mapping[row["scene"]]
                hit = True
        if hit:
            atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


def forget_scene(cid: str, sid: str) -> int:
    """Drop every citation this scene left. Returns how many went (#75).

    `forget`'s rule, applied by source instead of by field: a quote pulled from a
    post that has been deleted cannot explain anything, and a cascade delete is
    the one operation that can name the posts. Each row stores the scene it was
    cited from, so this needs no guessing.

    Deliberately independent of whether the field's VALUE was put back. A
    reversal that was refused leaves the value where the deleted scene left it,
    and the citation for it still quotes a post the player has erased -- an
    uncited value is the accurate state either way.
    """
    with locks.campaign_lock(cid):
        data = read(cid)
        doomed = [k for k, row in data.items()
                  if isinstance(row, dict) and row.get("scene") == sid]
        for k in doomed:
            del data[k]
        if doomed:
            atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")
        return len(doomed)
