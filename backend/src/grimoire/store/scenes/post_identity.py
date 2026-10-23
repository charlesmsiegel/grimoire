"""Stable addresses for transcript posts, including non-speaking stage beats.

An address belongs to one post, not its text or position. Writers mint it once;
edits preserve it and replacement variants bring their own. Legacy initialization
adds metadata only, so it cannot manufacture historical presentation evidence.
"""

from __future__ import annotations

import re
import uuid

from .. import atomic, locks
from ..frontmatter import dump_frontmatter, parse_frontmatter
from . import paths, read, serialize

_ID = re.compile(r"[0-9a-f]{32}\Z")


def address_of(message: dict) -> str | None:
    value = message.get("post_id")
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def identified(message: dict) -> dict:
    """A new post with an address, preserving an explicit valid identity."""
    return {**message, "post_id": address_of(message) or uuid.uuid4().hex}


def missing(messages: list[dict]) -> bool:
    addresses = [address_of(m) for m in messages]
    return None in addresses or len(set(addresses)) != len(addresses)


def ensure_post_ids(cid: str, sid: str) -> dict:
    scene = read.read_scene(cid, sid)
    if not missing(scene["messages"]):
        return scene
    with locks.campaign_lock(cid):
        scene = read.read_scene(cid, sid)
        if not missing(scene["messages"]):
            return scene
        seen: set[str] = set()
        for message in scene["messages"]:
            address = address_of(message)
            if address is None or address in seen:
                message["post_id"] = address = uuid.uuid4().hex
            seen.add(address)
        path = paths._scene_path(cid, sid)
        meta, _ = parse_frontmatter(atomic.read_text(path))
        atomic.write_text(path, dump_frontmatter(meta, serialize._serialize_messages(scene["messages"])))
        return scene
