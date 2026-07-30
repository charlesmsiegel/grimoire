"""Campaign path resolution, existence checks and sync.md manifest IO."""

from __future__ import annotations

from pathlib import Path

from .. import atomic
from ..frontmatter import dump_frontmatter, parse_frontmatter
from ..paths import home, safe_id


class CampaignNotFound(Exception):
    pass


def _campaigns_dir() -> Path:
    return home() / "campaigns"


def campaign_root(cid: str) -> Path:
    """The campaign's own directory — nothing it inherits from its world.

    Correct for campaign-local state (scenes, sheets, proposals, chronicle,
    playstate, calendar.json, the climate default, ...) and for writes, which is how a
    record materializes. It is *not* a place to read a record the campaign
    inherits: `overlay.INHERITED_KINDS` / `INHERITED_FILES` say which those are,
    and `store/overlay.py` is the only thing that resolves them. Reading one
    here misses everything still live-inherited from the world, and misses the
    campaign's tombstones — silently, which is why `tests/test_overlay_guard.py`
    checks for it (#248).

    Raises CampaignNotFound for an id that doesn't name a child of the
    campaigns dir. The guard lives here rather than in the router so a caller
    that isn't an HTTP path parameter gets it too (#240).
    """
    if not safe_id(cid):
        raise CampaignNotFound(cid)
    return _campaigns_dir() / cid


def campaign_meta_path(cid: str) -> Path:
    return campaign_root(cid) / "campaign.md"


def campaign_exists(cid: str) -> bool:
    """Existence check that survives an id `campaign_root` refuses to resolve.

    Callers testing "is there such a campaign?" want False for an unusable id,
    not an exception -- see worlds.world_exists.
    """
    try:
        return campaign_meta_path(cid).exists()
    except CampaignNotFound:
        return False


def _manifest_path(cid: str) -> Path:
    return campaign_root(cid) / "sync.md"


def read_manifest(cid: str) -> dict[str, str]:
    p = _manifest_path(cid)
    if not p.exists():
        return {}
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return meta


def write_manifest(cid: str, manifest: dict[str, str]) -> None:
    atomic.write_text(_manifest_path(cid), dump_frontmatter(manifest, ""))
