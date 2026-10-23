"""Observe public state only while canonical writers publish a new tail post.

This leaf imports readers, never writers. Capture and checkpoint errors are
presentation failures and must not prevent canonical transcript publication.
"""

from __future__ import annotations

import hashlib
import json
import logging

from ..appearances import paths as appearances
from ..scenes import identity, read
from ..tracker import prompt
from . import records

log = logging.getLogger(__name__)


def input_digest(cid: str, ref: str) -> str:
    """Fingerprint live tracker newcomer inputs without storing private prose."""
    actor = appearances.record(cid).get(ref.replace(":", "/", 1), {})
    value = [actor.get("version"), prompt.newcomer(cid, ref)]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def current_public(cid: str, sid: str, *, location_id: str | None = None) -> tuple[str, dict] | None:
    """Caller holds campaign_lock; location_id supports an atomic move publication."""
    try:
        ident = identity.scene_identity(cid, sid)
        if not ident:
            return None
        locations = read.get_location_history(cid, sid)
        state: dict = {"actors": [{"ref": ref.replace("/", ":", 1), "version_id": rec["version"]}
                            for ref, rec in sorted(appearances.record(cid).items())
                            if sid in rec.get("scenes", [])],
                 "location_id": location_id if location_id is not None else (locations[-1] if locations else None)}
        state["input_digests"] = {a["ref"]: input_digest(cid, a["ref"]) for a in state["actors"]}
        return records.token_for(ident, state), state
    except Exception:
        log.warning("Could not capture public presentation state", exc_info=True)
        return None


def published(cid: str, sid: str, observation: tuple[str, dict] | None) -> None:
    if observation is None:
        return
    try:
        ident = identity.scene_identity(cid, sid)
        if ident:
            records.save_checkpoint(cid, ident, *observation)
    except Exception:
        log.warning("Could not save public presentation checkpoint", exc_info=True)


def stamped(message: dict, observation: tuple[str, dict] | None) -> dict:
    if "scene_state_token" in message or observation is None:
        return message
    return {**message, "scene_state_token": observation[0]}
