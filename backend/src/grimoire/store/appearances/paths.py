"""Appearance record path/IO primitives and actor-ref helpers.

``repoint_scenes`` lives here rather than in ``transitions.py``: it calls only
``record``/``_write``, and ``transitions.py`` keeps a module-top import of the
still-flat ``scenes`` module, which already imports ``scene_refs`` -- the
module that calls ``repoint_scenes``. Putting it in ``transitions.py`` would
close a fresh ``scenes -> scene_refs -> appearances.transitions -> scenes``
cycle.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import atomic
from ..campaigns import paths as campaigns_paths

ACTOR_KINDS = ("characters", "pcs")


class AppearError(Exception):
    pass


def _ref(kind: str, actor_id: str) -> str:
    return f"{kind}/{actor_id}"


def _split(ref: str) -> tuple[str, str]:
    kind, _, actor_id = ref.partition("/")
    return kind, actor_id


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "appearances.json"


def locked_actor_root(cid: str) -> Path:
    """The campaign root, for reading actors that are in the appearance record.

    Actors normally inherit from the world, so reading one off a raw campaign
    root is the mistake `store/overlay.py` exists to prevent (#248). Appeared
    actors are the documented exception: entering the record goes through
    `_lock`, which copies the picked version and the container meta into the
    campaign tree, and `campaigns.ensure_campaign_slim` keeps a locked actor's
    files there. So a campaign-side read at the locked version is authoritative
    -- for those actors only.

    Same path as `campaigns.campaign_root`; the name is the point. Call this
    when a `roster`/`scene_cast`/`locked_version` result is what you are
    reading, and `overlay.char_root` / `overlay.pc_root` for anything else.
    """
    return campaigns_paths.campaign_root(cid)


def record(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _write(cid: str, data: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


def forget(cid: str, kind: str, actor_id: str) -> None:
    """Drop an actor's appearance record because the actor itself is gone.

    A record holds a version lock and a per-version sync base, and
    `sync._actor_incoming` reads it in preference to sync.md -- so one left
    behind by a delete keeps offering updates for an actor this campaign no
    longer has, under an id a later create can hand back (#225).

    Idempotent: an actor that never appeared has no record to drop.
    """
    data = record(cid)
    if data.pop(_ref(kind, actor_id), None) is not None:
        _write(cid, data)


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids in every appearance's scenes list.

    Cast is keyed by actor here, not by scene, so a scene rename (which changes
    the sid) would otherwise orphan its cast under the old id."""
    data = record(cid)
    changed = False
    for rec in data.values():
        scenes_list = rec.get("scenes", [])
        if any(s in mapping for s in scenes_list):
            rec["scenes"] = [mapping.get(s, s) for s in scenes_list]
            changed = True
    if changed:
        _write(cid, data)
