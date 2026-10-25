"""Conservative public projection at a stable canonical post address.

The checkpoint supplies historical actor versions and location; the newest
proven tracker result no later than the cursor supplies only present-aware
fields. Missing evidence yields neutral presentation without any inference.
"""

from __future__ import annotations

from .. import locks, responses
from ..scenes import identity, read
from ..tracker import expression_fields, fields, provenance, walk
from ..tracker import records as tracker_records
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
        # Narrative state may carry forward, but a sprite decision belongs only
        # to its owning tracked post (all response parts share that owner).
        keys, owners = walk._scan(messages, responses.variants_by_response(cid, sid))
        own_key = owners.get(at)
        own_at = next((i for i, key in keys if key == own_key), None)
        own_body = tracker_records.read_snapshot(cid, ident, own_key) if own_key is not None and own_key in proven else None
        selection = (own_body or {}).get("expressions")
        selected: dict = {}
        selection_versions: dict = {}
        if (isinstance(selection, dict) and own_at is not None
                and selection.get("scene_identity") == ident and selection.get("key") == own_key
                and selection.get("post_id") == messages[own_at].get("post_id")
                and isinstance(selection.get("versions"), dict)
                and public >= expression_fields.KEYS):
            selected = own_body["snapshot"] if own_body is not None else {}
            selection_versions = selection["versions"]
        actors = []
        for actor in checkpoint.get("actors", []):
            state = snapshot.get(actor["ref"], {})
            values = (state.get("fields", {}) if state.get("present")
                      and versions.get(actor["ref"]) == actor["version_id"] else {})
            mood = _visible(values, public, "visible_mood")
            chosen = selected.get(actor["ref"], {})
            chosen_values = (chosen.get("fields", {}) if chosen.get("present")
                             and selection_versions.get(actor["ref"]) == actor["version_id"] else {})
            actors.append(ActorAtPost(actor["ref"], actor["version_id"],
                                     mood if mood in fields.VISIBLE_MOODS else None,
                                     _visible(values, public, "clothing"),
                                     _visible(chosen_values, public, "expression-outfit"),
                                     _visible(chosen_values, public, "expression-mood")))
        return HistoricalPresentation(cursor, tuple(actors), checkpoint.get("location_id"), speaker)
