"""The pre-scene briefing (#118): what a scene opens owing, narrowed to the
people standing in it.

The continuity ledger (#117, ``routes.campaigns.get_ledger``) answers the
campaign-wide question — everything still open, in one read. This is its
per-scene sibling and answers a narrower one: *of what is still open, which
touches the cast in front of me right now.* Same sources, same tolerance
contract, one extra join.

That join is the whole feature. A plot thread records no actors; it records
beats, and each beat records the scene it landed in. Who was standing in that
scene is knowable two ways, and this module unions them because neither alone
covers the ground:

- ``chronicle.json``'s per-scene ``cast`` — the snapshot
  ``chronicle.scene_facts`` took when the scene was absorbed. This is the
  historical truth, and the only source that still knows a PC was present in a
  scene she has since been removed from (``appearances.leave`` drops the scene
  from her record).
- ``appearances.json`` — current membership, and the only source for a scene
  that was never absorbed: a hand-written thread, or one moved in the scene
  being briefed, has no chronicle record to consult at all.

Neither is authoritative over the other, and each one's blind spot is the
other's ordinary case, so the union is the answer rather than a fallback chain.
It errs toward flagging: a row this cannot connect still *lists*, merely
unflagged, so a miss costs a hint and never hides an obligation. Flagged rows
sort first for the same reason — narrowing is presentation here, not filtering.

Read-only. Nothing here writes, so it takes ``campaign_lock`` for the reason
the ledger route does rather than the usual one: a save writes the chronicle
and then the absorb's plot and commitment edits under one hold, and a briefing
that catches that sequence half-done reports a fact beside the commitment the
very same save resolved. Scene open is exactly when the previous scene's save
has just run, which makes that window this view's normal case, not a remote one.
"""

from __future__ import annotations

from . import chronicle, commitments, locks, plot, relationships
from .appearances import cast as appearances_cast
from .scenes import read as scenes_read


def _text(value, fallback: str = "") -> str:
    """A projected field as text. Same guard as ``plot._field`` /
    ``commitments._field``, applied to the fields this module projects itself:
    the section renders these directly, and React refuses an object as a child,
    so one hand-edited chronicle record would blank the panel rather than show
    one odd row. No ``try`` around the read can catch that — the read succeeds.
    """
    return value.strip() if isinstance(value, str) else fallback


def _touched_scenes(records) -> dict[str, set[str]]:
    """Every scene id each record has touched, keyed by record id.

    One function for plot.json and commitments.json because they are one shape:
    ``commitments.py``'s own docstring says it "mirrors plot.py's shape", and
    beats are the part the two share exactly. A ``beat_scenes`` in each store
    would be this code twice, differing only in which file it read.

    Read raw rather than taken from ``open_threads`` / ``open_commitments``,
    which project only the LATEST beat: a thread the player opened and someone
    else has since advanced is still hers, and ``last_scene`` alone says it is
    not.

    Wrong shapes are stepped over rather than trusted, for the reason
    ``commitments.repoint_scenes`` gives — these files are hand-editable, and a
    beat whose ``scene`` is a list is *unhashable*, so putting it in a set
    raises rather than simply missing.
    """
    out: dict[str, set[str]] = {}
    for rid, rec in (records.items() if isinstance(records, dict) else ()):
        if not isinstance(rec, dict):
            continue
        beats = rec.get("beats")
        scenes = {b["scene"] for b in (beats if isinstance(beats, list) else ())
                  if isinstance(b, dict) and isinstance(b.get("scene"), str)}
        last = rec.get("last_scene")
        if isinstance(last, str):
            scenes.add(last)
        out[rid] = scenes - {""}
    return out


def _focus(present: list[dict], pcless: bool) -> list[dict]:
    """The actors the involvement flag is about: the scene's players, or —
    in an offscreen scene — its whole cast.

    The widening is for offscreen (``pcless``) scenes, which seat only NPCs. A
    flag computed against ``role == "player"`` alone would be empty for every
    row in exactly the scenes where the director steers the cast directly and
    most needs to know what each of them is carrying.

    Keyed on the scene's own ``pcless`` flag rather than on "the player list came
    back empty", which is what this asked first and which was wrong (Codex
    review). An ordinary scene has no players *yet* while it is being set up, and
    momentarily none again if its player is removed — so the loose test made
    `involves` mean one thing before the PC was seated and another after, with
    NPC threads losing their flags the instant she arrived. A reader cannot be
    expected to explain that. ``pcless`` is a property of the scene, so the
    meaning now holds still for as long as the scene does.
    """
    return present if pcless else [a for a in present if a.get("role") == "player"]


def _stage_history(cid: str, refs: set[str]) -> dict[str, set[str]]:
    """For each ref, every scene it has stood in — from both sources the module
    docstring names, unioned.

    Refs are ``"<kind>/<id>"``: the appearance record's own key form, and the
    form ``chronicle.scene_facts`` writes into a record's ``cast``. Kept per-ref
    rather than pooled so a row can name *which* of several players it belongs
    to, which is the case the flag exists for.
    """
    seen: dict[str, set[str]] = {ref: set() for ref in refs}
    for a in appearances_cast.roster(cid):
        ref = f"{a['kind']}/{a['id']}"
        if ref in seen:
            seen[ref].update(s for s in a["scenes"] if isinstance(s, str))
    try:
        chron = chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: the appearance record still answers
        return seen
    # `read_chronicle` is a bare `json.loads`, so valid JSON of the wrong shape
    # arrives without raising -- the same correction `get_ledger` needed. The
    # KEY is the scene id and is guaranteed a string; the record's own `id`
    # field repeats it and is not.
    for sid, rec in (chron.items() if isinstance(chron, dict) else ()):
        if not isinstance(sid, str) or not isinstance(rec, dict):
            continue
        cast = rec.get("cast")
        for ref in (cast if isinstance(cast, list) else ()):
            # `isinstance(ref, str)` BEFORE the membership test, the same rule
            # `_touched_scenes` applies to beat scenes and for the same reason:
            # `seen` is a dict, so a list-valued cast entry is unhashable and
            # `in` RAISES rather than missing. That raise reaches this function's
            # tolerant caller, which replaces the whole result -- throwing away
            # the history already collected from appearances.json and unflagging
            # every row, for one hand-edited record (Codex review).
            if isinstance(ref, str) and ref in seen:
                seen[ref].add(sid)
    return seen


def _flagged(rows: list[dict], touched: dict[str, set[str]],
             stage: dict[str, set[str]], names: dict[str, str]) -> list[dict]:
    """Attach ``involves`` to every row, then float the flagged ones up.

    Sorted, never filtered: an unflagged commitment is still owed, and a
    briefing that hid it would be worse than none. ``sorted`` is stable, so
    inside each group the store's own ordering — by the scene that last moved
    the row — survives untouched.
    """
    out = []
    for row in rows:
        hit = touched.get(row["id"], frozenset())
        # A set, not a list comprehension: two actors can share a display name
        # (two characters both called "the Watchman"), and "involves the
        # Watchman, the Watchman" is noise rather than information.
        out.append({**row, "involves": sorted({names[ref] for ref, scenes in stage.items()
                                               if hit & scenes})})
    return sorted(out, key=lambda r: not r["involves"])


def _last_time(cid: str, sid: str) -> dict | None:
    """The newest absorbed fact from *before* this scene, or None.

    Strictly before, by scene id: ``scene_ids`` puts the sequence number first
    precisely so "lexicographic filename order equals play order absolutely",
    so this is chronological and not merely alphabetical. Excluding the scene's
    own record is the point of the comparison rather than a side effect —
    re-opening an absorbed scene must brief what led *into* it, not hand back
    its own summary, and must not leak a later scene's either.

    ``one_line or summary`` is the fallback every other chronicle consumer uses
    (``context.story._story_entries``): a save may leave ``one_line`` empty, and
    a row with only its scene label is a blank line rather than a fact. A record
    with *neither* is skipped rather than returned empty — this section is one
    slot, and spending it on a scene absorbed with nothing written down would
    hide the real fact sitting right behind it.
    """
    chron = chronicle.read_chronicle(cid)
    prior = sorted(key for key, rec in (chron.items() if isinstance(chron, dict) else ())
                   if isinstance(key, str) and isinstance(rec, dict) and key < sid)
    for key in reversed(prior):
        rec = chron[key]
        one_line = _text(rec.get("one_line")) or _text(rec.get("summary"))
        if not one_line:
            continue
        # Inside the loop on purpose: `list_scenes` stats the whole scene
        # directory, and it is only ever needed on the iteration that returns —
        # a campaign with no prior fact should not pay for a label it will not
        # print. The loop leaves on the next line, so it runs at most once.
        titles = {s["id"]: s.get("title", s["id"]) for s in scenes_read.list_scenes(cid)}
        return {"id": key, "one_line": one_line,
                "title": _text(titles.get(key), key), "date": _text(rec.get("date"))}
    return None


def build(cid: str, sid: str) -> dict:
    """The briefing for one scene.

    Never raises on a garbled store file: every section degrades to empty on its
    own, the contract ``plot.render_open`` set and ``get_ledger`` kept.
    """
    def _tolerant(read, empty):
        try:
            return read()
        except Exception:  # noqa: BLE001 — a garbled file empties its section, not the view
            return empty

    with locks.campaign_lock(cid):
        present = _tolerant(lambda: appearances_cast.scene_cast(cid, sid), [])
        # Reads the frontmatter head only. Tolerant because the scene file can
        # go between the route's `_require_scene` and here; a scene this cannot
        # read is treated as an ordinary one, which is the narrower reading.
        pcless = _tolerant(lambda: scenes_read.is_pcless(cid, sid), False)
        focus = _focus(present, pcless)
        names = {f"{a['kind']}/{a['id']}": a.get("name") or a["id"] for a in focus}
        stage = _tolerant(lambda: _stage_history(cid, set(names)), {})
        # Two reads of one file, both inside the hold: `open_threads` projects
        # the rows and `_touched_scenes` needs the beats it drops.
        #
        # Guarded SEPARATELY, though, because they do not fail together. A file
        # that will not parse takes both, and the section is empty either way.
        # But `_touched_scenes` can fail on data the projection reads fine --
        # some shape neither of them anticipated -- and pairing them would let
        # that cost every row in the section rather than every flag. Losing the
        # narrowing is this view degrading; losing the obligations is it lying.
        threads = _tolerant(lambda: plot.open_threads(cid), [])
        thread_scenes = _tolerant(lambda: _touched_scenes(plot.read(cid)), {})
        owed = _tolerant(lambda: commitments.open_commitments(cid), [])
        owed_scenes = _tolerant(lambda: _touched_scenes(commitments.read(cid)), {})
        # The whole present cast, not just `focus`: a feeling between two NPCs
        # in the room is exactly the kind of thing a briefing is for, and
        # `context.story._relationship_lines` renders the same block from the
        # same set for the model.
        lines = _tolerant(lambda: relationships.render_present(
            cid, [f"{a['kind']}:{a['id']}" for a in present],
            lambda t: relationships.actor_name(cid, t)), [])
        last = _tolerant(lambda: _last_time(cid, sid), None)

    return {
        "focus": [names[ref] for ref in sorted(names)],
        "plot": _flagged(threads, thread_scenes, stage, names),
        "commitments": _flagged(owed, owed_scenes, stage, names),
        "relationships": lines,
        "last_time": last,
    }
