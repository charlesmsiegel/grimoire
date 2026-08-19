"""Cost and token rollups over the usage ledger (#152), and what a campaign is
allowed to spend (#153).

Three reads over the same ledger — library-wide, one campaign, one scene — and
the campaign budget that turns the second into a warning. Every rollup here is
a pure read, and one that has never had a call to count answers with zeroes
rather than a 404, because "you have spent nothing yet" is an answer. The one
write is the budget itself, which is campaign metadata like a rename.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import store
from .common import _campaign_root_or_404, _require_scene
from .models import BudgetBody

router = APIRouter()

#: Shared by both routes so the two windows cannot drift. The upper bound is
#: `store.usage.MAX_DAYS`, and a request past it is clamped rather than
#: rejected — see `store.usage.summary`.
_DAYS = Query(30, description="Calendar days to roll up, counting back inclusive of today "
                              "(UTC). Clamped to [1, 366].")


@router.get("/usage/summary")
def get_usage_summary(days: int = _DAYS):
    """Everything this library has spent in the window, however it was spent.

    Three windows come back together, because they answer three different
    questions and re-deriving one from another is not possible on the client:

    - `totals` — the whole `days` window, and the breakdowns beside it
      (`by_day`, `by_model`, `by_task`, `by_campaign`) partition exactly that.
    - `today` — the current UTC calendar day.
    - `session` — calls made since this backend process started, with
      `session_started` saying when that was. Grimoire has no server-side notion
      of a session (there is no login, and a browser tab is invisible from
      here), so the process is the one boundary this side genuinely knows.
      Restarting the app resets it; `today` and the window do not move.

    Money is reported as two numbers per bucket, never one. `cost_usd` is what
    a provider charged. `estimated_usd` is what a subscription-billed call
    (the Claude Agent path) *would* have cost at API rates and did not — real
    usage, but not spend, so summing the two would report money nobody paid.
    `unpriced_calls` counts what neither covers: calls whose provider reported
    no price at all, which is every arbitrary OpenAI-compatible endpoint today.
    A total is only the whole story when that count is zero.
    """
    return store.usage.summary(days=days)


@router.get("/campaigns/{cid}/usage")
def get_campaign_usage(cid: str, days: int = _DAYS):
    """The same rollup, scoped to one campaign.

    404s on a campaign that does not exist, unlike the library-wide route:
    there, an empty answer means "nothing spent", but here it would also be what
    a typo'd id returns, and silently reporting $0 for a campaign nobody has is
    worse than saying so.

    Calls that belong to no campaign — a tagline is generated against a world,
    a voice anchor against a character — are absent by construction, so this
    total is a campaign's own spend rather than a share of the library's.
    """
    _campaign_root_or_404(cid)
    return store.usage.summary(days=days, campaign=cid)


@router.get("/campaigns/{cid}/scenes/{sid}/usage")
def get_scene_usage(cid: str, sid: str):
    """What this scene's turns cost, one row each, newest first (#153).

    The window is the **scene's own lifetime**, not a fixed number of days: the
    ledger has no index, so "this scene" is a filter over a scan, and scanning
    the last 30 days would report $0.00 for a scene played in the spring while
    scanning a year would make every panel open pay for a library's whole
    history. The scene's `created` stamp is the one bound that is neither —
    `store.usage.scene_usage` clamps it at both ends, and the window it settled
    on comes back as `since`/`until` so the view can say what it covers.

    `turns` is capped (`truncated` says when), while `totals` and `by_task` are
    summed over every row in the window — so a long scene's numbers do not
    change when its list is cut.
    """
    scene = _require_scene(cid, sid)
    return store.usage.scene_usage(cid, sid, since=scene["meta"].get("created", ""))


@router.get("/campaigns/{cid}/budget")
def get_campaign_budget(cid: str):
    """This campaign's budget and where it stands against it (#153).

    404s on a campaign that does not exist, like the rollup above and for the
    same reason. A campaign that has set no budget answers `{"level": "off"}`
    with no spend figures at all — see `store.usage.budget` for why an unasked
    question gets no number rather than a zero.
    """
    _campaign_root_or_404(cid)
    meta = store.campaigns.read_campaign(cid)["meta"]
    return store.usage.budget(cid, meta.get("budget_usd"), meta.get("budget_period"))


@router.put("/campaigns/{cid}/budget")
def put_campaign_budget(cid: str, body: BudgetBody):
    """Set or clear the budget, and answer with where that leaves the campaign.

    Returns the same shape as the GET rather than `{"ok": true}`: the caller
    setting a budget is the surface that has to render the result of setting it,
    and a second round trip to find out whether the campaign is already over the
    number just typed is a round trip for nothing.
    """
    try:
        store.campaigns.set_campaign_budget(cid, body.budget_usd or 0,
                                            body.budget_period or "")
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    meta = store.campaigns.read_campaign(cid)["meta"]
    return store.usage.budget(cid, meta.get("budget_usd"), meta.get("budget_period"))
