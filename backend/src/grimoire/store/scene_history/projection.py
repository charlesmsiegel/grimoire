"""Conservative public projection at a stable canonical post address.

The checkpoint supplies historical actor versions and location; the newest
proven tracker result no later than the cursor supplies only present-aware
fields. Missing evidence yields neutral presentation without any inference.
"""

from __future__ import annotations

from .. import locks, responses
from ..scenes import identity, read
from ..tracker import fields, provenance, walk
from . import records
from .contracts import ActorAtPost, HistoricalPresentation, VNCursor


def _visible(values: dict, public: set[str], key: str) -> str | None:
    entry = values.get(key, {})
    value = entry.get("value")
    return value if key in public and entry.get("aware") == "present" and isinstance(value, str) else None


def at_post(cid: str, sid: str, post_id: str) -> HistoricalPresentation:
    with locks.campaign_lock(cid):
        ident = identity.scene_identity(cid, sid)
        messages = read.read_scene(cid, sid)["messages"]
        at = next((i for i, m in enumerate(messages) if m.get("post_id") == post_id), None)
        if at is None or not ident:
            raise ValueError("Post address is not in this scene")
        cursor = VNCursor(ident, post_id)
        rid = messages[at].get("response_id")
        speaker = responses.actor_refs(cid, sid).get(rid) if rid else None
        checkpoint = records.checkpoint(cid, ident, messages[at].get("scene_state_token"))
        if checkpoint is None:
            return HistoricalPresentation(cursor, (), speaker_ref=speaker)
        _, proven = provenance.certificates(cid, sid)
        applicable = [(index, proven[key]) for index, key in walk.ordered_keys(cid, sid)
                      if index <= at and key in proven]
        source_at, snapshot = applicable[-1] if applicable else (None, {})
        source = records.checkpoint(cid, ident, messages[source_at].get("scene_state_token")) if source_at is not None else None
        versions = {a["ref"]: a["version_id"] for a in (source or {}).get("actors", [])}
        public = {f["key"] for f in fields.active(fields.effective(cid, sid)) if f.get("aware") == "present"}
        actors = []
        for actor in checkpoint.get("actors", []):
            state = snapshot.get(actor["ref"], {})
            values = (state.get("fields", {}) if state.get("present")
                      and versions.get(actor["ref"]) == actor["version_id"] else {})
            mood = _visible(values, public, "visible_mood")
            actors.append(ActorAtPost(actor["ref"], actor["version_id"],
                                     mood if mood in fields.VISIBLE_MOODS else None,
                                     _visible(values, public, "clothing")))
        return HistoricalPresentation(cursor, tuple(actors), checkpoint.get("location_id"), speaker)
