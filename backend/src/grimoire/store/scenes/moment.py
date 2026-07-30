"""Where and when a scene is: the current location and the current moment.

Both mutators append a transition line to the transcript when they move a
scene that already had a setting, so this is the one part of the package that
reaches into `write.py`. `set_datetime` resolves the calendar BEFORE
delegating to its serialized inner — user-authored provider code must never
run under the campaign lock (see `lifecycle._date_hint`).

Named `moment.py` rather than after the stdlib `datetime` module: this
package's `__init__` binds every submodule by name, and shadowing that name
inside a namespace which also uses it is a trap not worth setting.
"""

from __future__ import annotations

from .. import atomic, calendars, overlay, scene_ids, scene_refs
from ..campaigns import paths as campaigns_paths
from ..frontmatter import dump_frontmatter, parse_frontmatter
from ..paths import safe_id, uniquify
from . import locking, paths, read, serialize, write


@locking._serialized
def set_location(cid: str, sid: str, eid: str) -> dict:
    """Make campaign location `eid` the scene's current setting.

    First setting on a location-less scene is silent; a real change appends an
    assistant transition line. Re-selecting the current location is a no-op.
    Returns {"moved": bool, "name": str}.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    name = overlay.read_entity(cid, "locations", eid)["meta"].get("name", eid)  # raises EntityNotFound
    history = read.get_location_history(cid, sid)
    if history and history[-1] == eid:
        return {"moved": False, "name": name}
    moved = bool(history)
    if moved:
        write.append_message(cid, sid, "assistant", f"*The scene moves to {name}.*",
                             speaker=serialize.TRANSITION_SPEAKER)
    # re-read after the possible append_message rewrite, then record the new current
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    history.append(eid)
    meta["location_history"] = ",".join(history)
    atomic.write_text(p, dump_frontmatter(meta, body))
    return {"moved": moved, "name": name}


def set_datetime(cid: str, sid: str, native: str) -> dict:
    """Set the scene's current moment (in the primary calendar). The first set is
    silent and stamps the start date into the filename (the id changes); later
    changes append an assistant transition line. Returns {"advanced", "friendly",
    "id"} where id is the possibly-renamed scene id."""
    if not safe_id(sid) or not paths._scene_path(cid, sid).exists():
        raise paths.SceneNotFound(sid)     # cheap pre-check; re-checked under the lock
    # Resolve the calendar BEFORE taking the lock — user-authored provider code
    # must not run under it (see _date_hint). Nothing here touches the scene.
    cfg = calendars.read_calendar(campaigns_paths.campaign_root(cid))
    provider = calendars.get_provider(cfg["primary"])
    canonical = calendars.normalize(provider, native)  # raises calendars.CalendarError
    return _apply_datetime(cid, sid, canonical, calendars.friendly(provider, canonical))


@locking._serialized
def _apply_datetime(cid: str, sid: str, canonical: str, friendly: str) -> dict:
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    history = read.get_time_history(cid, sid)
    if history and history[-1] == canonical:
        return {"advanced": False, "friendly": friendly, "id": sid}
    advanced = bool(history)
    if advanced:
        write.append_message(cid, sid, "assistant", f"*Time passes. It is now {friendly}.*",
                             speaker=serialize.TRANSITION_SPEAKER)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta.pop("suggested_date", None)  # the hint is stale once a real date exists
    history.append(canonical)
    meta["time_history"] = ",".join(history)
    atomic.write_text(p, dump_frontmatter(meta, body))
    if not advanced:
        sid = _stamp_start_date(cid, sid, canonical)
    return {"advanced": advanced, "friendly": friendly, "id": sid}


@locking._serialized
def _stamp_start_date(cid: str, sid: str, canonical: str) -> str:
    """First date set: insert the date section into the filename. The start date
    is fixed — later advances never touch the name. Legacy ids are left alone.

    Serialized in its own right, not just via its one caller: it renames the
    scene file and repoints every store that references it, which is exactly
    the sequence a concurrent append must not land in the middle of."""
    parsed = scene_ids.parse_sid(sid)
    if parsed is None or parsed["date_slug"] is not None:
        return sid
    base = scene_ids.format_sid(parsed["number"], parsed["width"],
                                scene_ids.date_slug_of(canonical), parsed["title_slug"])
    new_sid = uniquify(base, lambda c: c != sid and paths._scene_path(cid, c).exists())
    paths._scene_path(cid, sid).rename(paths._scene_path(cid, new_sid))
    scene_refs.repoint(cid, {sid: new_sid})
    return new_sid
