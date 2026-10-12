"""Whether a play decision runs for a campaign (roadmap 02 §3.6).

Three switches, each a global key in config.md with a campaign override in
campaign.md, read the tracker's way (`store/tracker/settings.py`): the
campaign's own value wins when it is one the switch knows, and anything else
-- absent, blank, a hand-edited mistake -- defers to the global. A global
value the switch does not know reads as off, since each one adds a call or
changes who speaks, and a cost may not arrive by a typo.

- `decide_speaker_sampling` (`off` | `on`): draw the next speaker from the
  pick's reported distribution (02-C2a) rather than take its answer.
- `decide_turn_plan` (`off` | `intent` | `plan`): ask one pre-generation
  decision about how the assigned actor answers (02-C2b), or the plan that
  replaces it (02-C3). Never both.
- `decide_play_tool` (`off` | `on`): offer a contribution the decide tool
  (02-C4).

Defaults: the user ratified all three for Settings (02-S6) with the speaker
draw `on` and the other two `off` (`config.DEFAULT_DECIDE_*`). The pick was
already asked, so sampling it adds no call; the other two each add one, and a
cost may not arrive by upgrade. A global value a switch does not know reads as
off, whatever the default.

`set_campaign` writes a campaign's own values through
`campaigns.set_campaign_switches`, under the campaign lock.
"""

from __future__ import annotations

from . import config
from .campaigns import lifecycle as campaigns_lifecycle
from .campaigns import read as campaigns_read

#: The campaign.md keys, the same spellings as the global ones.
SPEAKER_SAMPLING_KEY = "decide_speaker_sampling"
TURN_PLAN_KEY = "decide_turn_plan"
PLAY_TOOL_KEY = "decide_play_tool"

ON_OFF = ("on", "off")
#: `decide_turn_plan`'s vocabulary, off first.
TURN_PLAN_MODES = ("off", "intent", "plan")

#: Each switch's key and vocabulary.
VOCABULARIES: dict[str, tuple[str, ...]] = {
    SPEAKER_SAMPLING_KEY: ON_OFF, TURN_PLAN_KEY: TURN_PLAN_MODES, PLAY_TOOL_KEY: ON_OFF}


def _setting(cid: str, key: str, vocabulary: tuple[str, ...]) -> str:
    """The effective value of `key` for `cid`: the campaign's own when it is
    in `vocabulary`, else the global's when it is, else "off"."""
    if cid:
        mine = campaign_setting(cid, key, vocabulary)
        if mine:
            return mine
    value = config.read_config().get(key, "off")
    return value if value in vocabulary else "off"


def campaign_setting(cid: str, key: str, vocabulary: tuple[str, ...]) -> str:
    """The campaign's own value of `key`, or "" when it has none (or one the
    switch does not know, which is no choice at all)."""
    value = campaigns_read.read_campaign(cid)["meta"].get(key)
    return value if value in vocabulary else ""


def speaker_sampling(cid: str) -> bool:
    """Whether the speaker pick's answer is sampled from its reported
    distribution for this campaign (02-C2a)."""
    return _setting(cid, SPEAKER_SAMPLING_KEY, ON_OFF) == "on"


def turn_plan_mode(cid: str) -> str:
    """`off`, `intent` or `plan`: the pre-generation decision this campaign's
    contributions ask (02-C2b, 02-C3)."""
    return _setting(cid, TURN_PLAN_KEY, TURN_PLAN_MODES)


def play_tool(cid: str) -> bool:
    """Whether a contribution is offered the decide tool (02-C4)."""
    return _setting(cid, PLAY_TOOL_KEY, ON_OFF) == "on"


def effective(cid: str) -> dict[str, str]:
    """Every switch's effective value for `cid` (`cid=""` reads the global
    alone)."""
    return {key: _setting(cid, key, vocabulary) for key, vocabulary in VOCABULARIES.items()}


def own(cid: str) -> dict[str, str]:
    """Every switch's own value in `cid`'s campaign.md, "" where it follows the
    global."""
    return {key: campaign_setting(cid, key, vocabulary)
            for key, vocabulary in VOCABULARIES.items()}


def set_campaign(cid: str, values: dict[str, str]) -> None:
    """Write `cid`'s own values (`ValueError` for a key or value no switch
    knows, `campaigns.CampaignNotFound` for a campaign that is not there)."""
    campaigns_lifecycle.set_campaign_switches(cid, values, VOCABULARIES)
