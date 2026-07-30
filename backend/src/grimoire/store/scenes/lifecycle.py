"""Scene creation, renaming, deletion, and the id re-pad that follows a
campaign outgrowing its number width.

The only file in the package that reaches `audit.capture_baseline` and
`scene_refs.repoint` for anything but a datetime stamp. `repad` lives here
rather than beside the other id helpers for exactly that reason: it calls
`scene_refs.repoint`, and `scene_refs` imports `chronicle`, which reads
`serialize.TRANSITION_SPEAKER` — keeping `repad` in `serialize.py` would close
that loop.
"""

from __future__ import annotations

from .. import atomic, calendars, scene_ids, scene_refs
from ..audit import baselines
from ..campaigns import paths as campaigns_paths
from ..frontmatter import dump_frontmatter, parse_frontmatter
from ..llm_connections import get_active as _get_active_connection
from ..paths import now_iso, safe_id, slugify, uniquify
from . import locking, paths, serialize


@locking._serialized
def repad(cid: str, width: int) -> None:
    """Re-pad every scene number to `width` digits (renames files, repoints all
    referencing stores). Keeps widths uniform so lexicographic order stays exact."""
    mapping = {}
    for p in paths._scenes_dir(cid).glob("*.md"):
        parsed = scene_ids.parse_sid(p.stem)
        if parsed and parsed["width"] != width:
            mapping[p.stem] = scene_ids.format_sid(
                parsed["number"], width, parsed["date_slug"], parsed["title_slug"])
    for old, new in mapping.items():
        paths._scene_path(cid, old).rename(paths._scene_path(cid, new))
    scene_refs.repoint(cid, mapping)


def create_scene(cid: str, title: str, suggested_date: str | None = None,
                 pcless: bool = False) -> str:
    paths._require_campaign(cid)   # before _date_hint: no calendar plugin runs for a
                                   # campaign that doesn't exist. Re-checked under the
                                   # lock, which is where it actually has to hold.
    # The date hint is normalized before the lock, not inside it — see _date_hint.
    return _create_scene(cid, title, pcless, _date_hint(cid, suggested_date))


def _date_hint(cid: str, suggested_date: str | None) -> str:
    """The creation-time date hint in canonical form, resolved OUTSIDE the
    campaign lock (see `_serialized`).

    `get_provider` imports every user-authored provider in
    `<home>/calendars/` and `normalize` then runs that provider's own code.
    None of it touches the scene file, and nothing bounds how long a
    hand-written plugin takes — running it under a campaign-wide lock would
    let one bad calendar stall every writer in the campaign.

    Only a hint: a bad one is dropped, never an error.
    """
    if not suggested_date:
        return ""
    try:
        provider = calendars.get_provider(
            calendars.read_calendar(campaigns_paths.campaign_root(cid))["primary"])
        return calendars.normalize(provider, suggested_date)
    except (calendars.CalendarError, KeyError):
        return ""


@locking._serialized
def _create_scene(cid: str, title: str, pcless: bool, date_hint: str) -> str:
    paths._require_campaign(cid)
    d = paths._scenes_dir(cid)
    d.mkdir(parents=True, exist_ok=True)
    number, width = serialize._numbering(cid)
    if len(str(number)) > width:  # 999 -> 1000: widen the whole campaign first
        width = len(str(number))
        repad(cid, width)
    now = now_iso()
    base = scene_ids.format_sid(number, width, None, slugify(title))
    sid = uniquify(base, lambda c: paths._scene_path(cid, c).exists())
    active = _get_active_connection()
    meta = {"title": title, "model": active["model"] if active else "",
             "created": now, "updated": now}
    if pcless:
        meta["pcless"] = "true"
    if date_hint:
        meta["suggested_date"] = date_hint
    atomic.write_text(paths._scene_path(cid, sid), dump_frontmatter(meta, ""))
    baselines.capture_baseline(cid, sid)
    return sid


@locking._serialized
def rename_scene(cid: str, sid: str, title: str) -> str:
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["title"] = title
    parsed = scene_ids.parse_sid(sid)
    if parsed:  # keep number and date sections verbatim; only the title re-slugs
        base = scene_ids.format_sid(
            parsed["number"], parsed["width"], parsed["date_slug"], slugify(title))
    else:  # legacy (pre-migration) id: keep the old created-date prefix scheme
        base = f"{meta.get('created', now_iso())[:10]}-{slugify(title)}"
    new_sid = uniquify(base, lambda c: c != sid and paths._scene_path(cid, c).exists())
    atomic.write_text(p, dump_frontmatter(meta, body))
    if new_sid != sid:
        p.rename(paths._scene_path(cid, new_sid))
        # a scene's id is its filename: carry every store's references across
        scene_refs.repoint(cid, {sid: new_sid})
    return new_sid


@locking._serialized
def delete_scene(cid: str, sid: str) -> None:
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        raise paths.SceneNotFound(sid)
    p.unlink()
