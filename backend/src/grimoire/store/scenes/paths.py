"""Scene path resolution, the campaign precondition, and the not-found error.

The package's L1 floor: every other file here reaches a scene file through
``_scene_path``, and nothing in this module reads or writes one.
"""

from __future__ import annotations

from pathlib import Path

from ..campaigns import paths as campaigns_paths


class SceneNotFound(Exception):
    pass


def _scenes_dir(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "scenes"


def _scene_path(cid: str, sid: str) -> Path:
    return _scenes_dir(cid) / f"{sid}.md"


def _alts_path(cid: str, sid: str) -> Path:
    """The scene's reroll-alternates sidecar (`store/alternates.py`).

    Resolved here, beside the transcript it shadows, so the two files a scene
    id names stay in one place: `lifecycle.delete_scene` unlinks this without
    importing the store that owns its contents, and `alternates` never builds
    a scene path of its own. Enumeration is unaffected -- every scan of this
    directory globs `*.md`.
    """
    return _scenes_dir(cid) / f"{sid}.alts.json"


def _review_path(cid: str, sid: str) -> Path:
    """The scene's pending end-of-scene review (`store/pending_reviews.py`).

    Beside the transcript for `_alts_path`'s reasons, and it is the second
    sidecar rather than a second *kind* of thing: both are per-scene JSON that
    a deleted scene must take with it, both are keyed by filename rather than
    by a field inside a shared file, and both are therefore reachable only
    through this resolver. Enumeration is unaffected — every scan of this
    directory globs `*.md`.
    """
    return _scenes_dir(cid) / f"{sid}.review.json"


def _sid_taken(cid: str, sid: str) -> bool:
    """Whether an id is spoken for — by a transcript, or by a sidecar left
    beside one that is gone.

    Numbering comes from the `.md` files (`serialize._numbering`), so the id of
    a deleted scene is free for the next one to take. `delete_scene` removes the
    sidecars first so an orphan should not exist, but a crash between the
    unlinks, or one written by an older build, still could — and adopting that
    id would hand a fresh scene the deleted scene's parked transcripts, or its
    pending review: a whole end-of-scene generation describing a transcript the
    new scene has never had, offered against the new scene's own posts.
    """
    return (_scene_path(cid, sid).exists() or _alts_path(cid, sid).exists()
            or _review_path(cid, sid).exists())


def _require_campaign(cid: str) -> None:
    if not campaigns_paths.campaign_exists(cid):
        raise campaigns_paths.CampaignNotFound(cid)
