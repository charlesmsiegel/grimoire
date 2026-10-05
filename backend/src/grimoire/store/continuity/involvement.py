"""Which actors and scenes a thread or commitment has touched (capstone spec §8).

A plot thread records no actors; it records beats, and each beat records the
scene it landed in. Who stood in that scene is knowable two ways, and this module
unions them because neither alone covers the ground -- the reasoning
`store/briefing.py` first wrote down for its per-scene flag:

- ``chronicle.json``'s per-scene ``cast``, the snapshot taken when the scene was
  absorbed, and the only source that still knows a PC was present in a scene she
  has since been removed from;
- ``appearances.json``, current membership, and the only source for a scene
  that was never absorbed.

`touched_scenes` and `stage_history` are briefing's join, moved here unchanged so
the briefing and every newer reader share one implementation. `of` is the
generalisation the capstone needs: for any thread or commitment, every actor
across the whole roster who stood in a scene it touched, aggregated over its
merged group. Nothing here calls a model, and nothing takes a lock -- callers
needing a consistent read hold `campaign_lock`, as `briefing.build` does.
"""

from __future__ import annotations

from .. import chronicle, fieldtext
from ..appearances import cast as appearances_cast
from . import canon, effective


def touched_scenes(records) -> dict[str, set[str]]:
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


def stage_history(cid: str, refs: set[str]) -> dict[str, set[str]]:
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
            # `touched_scenes` applies to beat scenes and for the same reason:
            # `seen` is a dict, so a list-valued cast entry is unhashable and
            # `in` RAISES rather than missing. That raise reaches this function's
            # tolerant caller, which replaces the whole result -- throwing away
            # the history already collected from appearances.json and unflagging
            # every row, for one hand-edited record (Codex review).
            if isinstance(ref, str) and ref in seen:
                seen[ref].add(sid)
    return seen


def scene_actors(cid: str) -> dict[str, set[str]]:
    """Scene id -> every actor (``<kind>:<id>``) known to have stood in it, from
    the appearance record and the chronicle's cast snapshots, unioned.

    Each source is guarded on its own: one that will not read contributes
    nothing and the other still answers, and a wrongly shaped entry is stepped
    over rather than trusted (`stage_history` explains why a non-string in a
    set raises rather than missing)."""
    out: dict[str, set[str]] = {}
    try:
        for a in appearances_cast.roster(cid):
            ref = canon.actor_ref(f"{a['kind']}/{a['id']}")
            scenes = a.get("scenes")
            for sid in scenes if isinstance(scenes, list) else ():
                if isinstance(sid, str) and sid:
                    out.setdefault(sid, set()).add(ref)
    except Exception:  # noqa: BLE001 -- garbled appearances.json: the chronicle still answers
        pass
    try:
        chron = chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 -- garbled chronicle.json: the appearance record still answers
        chron = {}
    for sid, rec in (chron.items() if isinstance(chron, dict) else ()):
        if not isinstance(sid, str) or not isinstance(rec, dict):
            continue
        cast = rec.get("cast")
        for token in (cast if isinstance(cast, list) else ()):
            if isinstance(token, str) and token:
                out.setdefault(sid, set()).add(canon.actor_ref(token))
    return out


def of(cid: str, refs) -> dict[str, dict]:
    """For each thread or commitment ref: the actors who stood in a scene it
    touched and those scenes, over its whole merged group, keyed by the ref the
    caller asked about. Anything else -- another kind, an unparseable ref, a
    record that is not there, a ledger that will not read -- is empty."""
    ledgers = effective.Ledgers.load(cid)
    live = effective.live_canon(cid, ledgers)
    raw = {"thread": ledgers.threads, "commitment": ledgers.commitments}
    groups: dict[str, dict | None] = {}
    index = scene_actors(cid)
    out: dict[str, dict] = {}
    for ref in refs:
        out[ref] = {"actors": [], "scenes": []}
        try:
            prefix, _ = canon.split_ref(ref)
        except ValueError:
            continue
        if prefix not in effective.ALIASABLE or raw[prefix] is None:
            continue
        if prefix not in groups:
            try:
                groups[prefix] = effective.records(cid, prefix)
            except Exception:  # noqa: BLE001 -- a garbled ledger answers empty
                groups[prefix] = None
        found = (groups[prefix] or {}).get(live.get(ref, ref))
        if found is None:
            continue
        scenes = {fieldtext.text(b.get("scene")) for b in found["beats"]}
        for member in found["members"]:
            record = raw[prefix].get(member)
            if isinstance(record, dict):
                scenes.add(fieldtext.text(record.get("last_scene")))
        scenes.discard("")
        actors = set().union(*(index.get(s, set()) for s in scenes))
        out[ref] = {"actors": sorted(actors), "scenes": sorted(scenes)}
    return out
