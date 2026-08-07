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


def campaign_activity_path(cid: str) -> Path:
    """The "something happened in this campaign" stamp.

    Its own file, deliberately, rather than a field in campaign.md. The stamp
    fires from every campaign-scoped write there is, and `touch` publishes the
    whole meta file from a copy it read a moment earlier -- so putting it in
    campaign.md would race `rename_campaign` and `set_campaign_response` and
    silently restore the name or the response settings they had just changed
    (see OUTSIDE_DOMAIN in locks.py, which records that hazard for `touch`).

    A single-value file has no such failure: nothing else writes it, so there
    is nothing for a stale copy to clobber. Last writer wins, which is the
    correct semantics for a high-water mark anyway.
    """
    return campaign_root(cid) / "activity.txt"


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
