"""Where the scene state tracker keeps its files, and what a snapshot key looks like.

Every location here is derived from `worlds_paths.world_root` or
`campaigns_paths.campaign_root` rather than assembled from `home()`, because
those two are the only things that refuse an id that does not name a child of
the store (`WorldNotFound` / `CampaignNotFound`) -- a tracker path built by hand
from a request's `wid` or `cid` would be the way around that check, and
`test_paths_guard.py` exists to keep filesystem access on the resolvers.

The scene directory is keyed by the scene's *identity* token, not its `sid`:
a `sid` moves on rename and is reissued after a delete, so a directory keyed on
it would hand a replacement scene its predecessor's snapshots.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..campaigns import paths as campaigns_paths
from ..worlds import paths as worlds_paths


def world_layer_path(wid: str) -> Path:
    """The world's field layer. Not an overlay-inherited file: a campaign's layer
    is resolved on top of it at read time, so a world edit reaches every campaign
    that has not overridden the field."""
    return worlds_paths.world_root(wid) / "tracker.json"


def campaign_layer_path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "tracker.json"


def scene_dir(cid: str, identity: str) -> Path:
    """Everything the tracker stores for one scene, under the campaign so that
    forking or deleting the campaign takes it along."""
    return campaigns_paths.campaign_root(cid) / "tracker" / identity


def scene_layer_path(cid: str, identity: str) -> Path:
    return scene_dir(cid, identity) / "fields.json"


def response_key(rid: str, vid: str) -> str:
    """The key of a character post: a response and its active variant."""
    return f"r-{rid}-{vid}"


def post_key(post_id: str) -> str:
    """The key of a post that has no response record (a player post)."""
    return f"p-{post_id}"


# `\Z`, not `$`: `$` also matches before a trailing newline, and a key becomes a
# file name.
KEY_RE = re.compile(r"\A(r-[0-9a-f]{32}-[0-9a-f]{32}|p-[0-9a-f]{32})\Z")


def valid_key(key: str) -> bool:
    return bool(KEY_RE.match(key))
