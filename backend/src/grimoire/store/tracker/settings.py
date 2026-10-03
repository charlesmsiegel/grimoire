"""Whether the scene state tracker runs for a campaign.

The campaign's own `tracker` key in campaign.md ("on" / "off") wins over the
global config switch, and an absent or unrecognised value means "no opinion" so
the global decides. The campaign wins because the people who turn the tracker
off are doing it for one story -- a one-shot that does not need the bookkeeping,
or a long campaign whose cost they would rather not pay -- and a global default
must not undo that choice; the reverse holds too, so one campaign can opt in
while the global is off. Anything that is not exactly "on" or "off" reads as
unset, the same split `store.config` makes between a choice and a mistake.
"""

from __future__ import annotations

from .. import config
from ..campaigns import read as campaigns_read


def campaign_setting(cid: str) -> str:
    """The campaign's own setting: "on", "off", or "" when it has none."""
    value = campaigns_read.read_campaign(cid)["meta"].get("tracker")
    return value if value in ("on", "off") else ""


def enabled(cid: str) -> bool:
    """Does the tracker run for this campaign: its own setting if it has one,
    else the global switch."""
    setting = campaign_setting(cid)
    if setting:
        return setting == "on"
    return config.tracker_enabled()
