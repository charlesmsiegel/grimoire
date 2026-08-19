"""Campaign meta CRUD, copy-on-create from a world, and sync.md manifest IO."""

from __future__ import annotations

# Submodules before names, and the leaves before `lifecycle`: `lifecycle` imports
# `overlay`/`appearances`/`sheets`/`scenes`, each of which imports back into
# `campaigns.paths` / `campaigns.read`, so those two must already be bound here.
from . import paths, read, lifecycle  # noqa: F401
from .paths import (  # noqa: F401
    CampaignNotFound, _campaigns_dir, _manifest_path, campaign_exists,
    campaign_activity_path, campaign_meta_path, campaign_root, read_manifest, write_manifest,
)
from .read import (  # noqa: F401
    _NO_WORLD, best_stamp, has_campaigns, list_campaigns, read_activity, read_campaign, touch,
    touch_quietly, world_refs, world_root_of,
)
from .lifecycle import (  # noqa: F401
    _prune_duplicate_files, _tombstone_deleted_copied_assets, create_campaign,
    delete_campaign, ensure_campaign_slim, fork_campaign, rename_campaign,
    set_campaign_budget,
    set_campaign_response,
)
