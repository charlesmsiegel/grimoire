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


def _require_campaign(cid: str) -> None:
    if not campaigns_paths.campaign_exists(cid):
        raise campaigns_paths.CampaignNotFound(cid)
