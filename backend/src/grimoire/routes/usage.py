"""Cost and token rollups over the usage ledger (#152), and what a campaign is
allowed to spend (#153).

Four reads over the same ledger — library-wide, one campaign, one campaign
scene by scene, one scene turn by turn — plus the campaign budget that turns
the second into a warning, and the per-model rate table (#158) that decides
what an unpriced call is reported as. Every rollup here is a pure read, and one
that has never had a call to count answers with zeroes rather than a 404,
because "you have spent nothing yet" is an answer.

Two writes: the budget, which is campaign metadata like a rename, and the rate
table, which is library-wide configuration. Both are whole-value PUTs rather
than patches — see each for why.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import store
from .common import _campaign_root_or_404, _require_scene
from .models import BudgetBody, PricingBody

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

    `turns` is capped (`truncated` says when), while `totals`, `by_task` and
    `by_post` are summed over every row in the window — so a long scene's
    numbers do not change when its list is cut.

    `by_post` is keyed by transcript index, and holds every call made answering
    that player post — the first reply and each reroll of it. That is what makes
    a per-post figure worth reading: a post rerolled five times cost five
    generations, and only the fifth is still on screen.

    `clamped` says the scan could not reach all the way back to the scene's
    start (a scene played across more than `store.usage.MAX_DAYS`). Every
    figure here is a floor when it is set, and `by_post` in particular is then
    incomplete in a way a transcript cannot see — a post with no bucket looks
    exactly like a post that cost nothing.
    """
    scene = _require_scene(cid, sid)
    return store.usage.scene_usage(cid, sid, since=scene["meta"].get("created", ""))


_ORDER = Query("cost", description="How to order the scenes before the list is "
                                   "capped: cost | recent | turns. An unknown "
                                   "value falls back to cost.")


@router.get("/campaigns/{cid}/usage/scenes")
def get_campaign_scene_costs(cid: str, order: str = _ORDER):
    """What each of this campaign's scenes has cost, over the whole ledger.

    The all-time view, and the only read here that is not windowed: "what has
    this campaign cost me" is a question about a campaign's whole life, and
    answering it with the last 30 days would be answering a different one. The
    window it settled on comes back as `since`/`until` all the same — a library
    whose oldest month file has been deleted by hand cannot be scanned further
    back than the files that are left, and the view says so rather than
    implying the total is complete.

    Each bucket carries the scene's own totals plus the title and dates read off
    the scene file, joined here rather than in the store: the ledger knows scene
    *ids*, and a rollup that opened campaign files to name them would make an
    accounting read depend on a transcript store. A bucket whose id no longer
    resolves to a scene keeps its id and is marked `missing` — the spend
    happened, and hiding a deleted scene's cost would make the rows stop adding
    up to the total above them.

    `order` is applied by the store, over every bucket, *before* the list is
    capped. It is a server parameter rather than a client-side re-sort for
    exactly that reason: a campaign with more buckets than the cap would
    otherwise have "most recent" mean "the most recent of the most expensive",
    with a recent cheap scene missing from a list that claims to show it.
    """
    _campaign_root_or_404(cid)
    rollup = store.usage.campaign_scenes(cid, order=order)
    titles = {s["id"]: s for s in store.scenes.list_scenes(cid)}
    named = []
    for bucket in rollup["scenes"]:
        meta = titles.get(bucket["scene"])
        named.append({**bucket,
                      "title": (meta or {}).get("title", ""),
                      "created": (meta or {}).get("created", ""),
                      "updated": (meta or {}).get("updated", ""),
                      # `NO_SCENE` is not missing -- it is the bucket for the
                      # calls that never named a scene (a cast suggestion, an
                      # intent classification), which is a real category rather
                      # than a scene that has gone.
                      "missing": bool(bucket["scene"]) and meta is None})
    # Which model strings the ledger holds that no pricing entry matches. The
    # page's "N calls reported no price" is a count; this is the reason, and
    # without it the reader has no way to see that their table says
    # `z.ai/...` where the ledger recorded `z-ai/...`.
    return {**rollup, "scenes": named,
            "unpriced_models": store.usage.unpriced_models()}


@router.get("/pricing")
def get_pricing():
    """The user's per-model rate table (#158).

    Answers `{}` when there is none, which is the default state: the table is
    opt-in, and every rollup treats an empty one as "model nothing".

    A file that exists but cannot be parsed answers `unreadable: true` and no
    rates — a **strict** read, unlike the one rollups take. The editor is the
    caller, and for it the two cases are opposites: an empty table is a form to
    fill in, while an unreadable one is a form that must not be offered at all,
    because saving it would replace rates the user still has with the nothing
    this side could read.
    """
    shape = {"fields": list(store.pricing.FIELDS),
             "default_key": store.pricing.DEFAULT_KEY,
             "max_entries": store.pricing.MAX_ENTRIES}
    try:
        return {"rates": store.pricing.read_pricing(strict=True),
                "unreadable": False, **shape}
    except store.pricing.PricingUnreadableError as exc:
        # 200 with a flag rather than a 500: the file is the user's own, this is
        # a state they can fix by hand, and the editor has something specific to
        # say about it. A 500 would reach the same view as a network failure.
        return {"rates": {}, "unreadable": True, "detail": str(exc), **shape}


@router.put("/pricing")
def put_pricing(body: PricingBody):
    """Replace the whole table, and answer with what was stored.

    A PUT is the whole table, not a patch — the same rule as the budget below,
    and for the same reason: an entry is removed by sending a table without it,
    so there is no partial shape whose meaning a reader would have to guess.
    Entries that name no usable rate are dropped on the way in, and what comes
    back is what a rollup will actually read.
    """
    try:
        return {"rates": store.pricing.write_pricing(body.rates)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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

    **A PUT is the whole budget, not a patch.** A body carrying only
    `budget_period` clears the limit, because a period with no limit beside it
    describes nothing — the two are written and cleared together
    (`campaigns.lifecycle.BUDGET_KEYS`). Send both, or send neither to clear.
    """
    try:
        store.campaigns.set_campaign_budget(cid, body.budget_usd or 0,
                                            body.budget_period or "")
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    meta = store.campaigns.read_campaign(cid)["meta"]
    return store.usage.budget(cid, meta.get("budget_usd"), meta.get("budget_period"))
