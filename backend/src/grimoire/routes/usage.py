"""Cost and token rollups over the usage ledger (#152).

Two reads over the same `store.usage.summary`: one library-wide, one scoped to
a campaign. Both are pure reads — nothing here writes, and a rollup that has
never had a call to count answers with zeroes rather than a 404, because "you
have spent nothing yet" is an answer.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from .. import store
from .common import _campaign_root_or_404

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
