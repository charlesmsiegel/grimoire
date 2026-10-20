"""todo: report responsibilities."""

from __future__ import annotations

from fastapi import HTTPException

from ... import store
from ...store import readstats
from ..overview_log import overview_read
from . import _catalog, _context
from ._router import router as router


def _run(builders: tuple, ctx: _context._Ctx) -> list[dict]:
    """`builders` over `ctx`, in order, keeping the chores that answered."""
    out = []
    for chore_id, builder in builders:
        readstats.bump(f"todo.chore.{chore_id}")
        chore = builder(ctx)
        if chore:
            out.append(chore)
    return out


def _campaign_scope(ctx: _context._Ctx) -> list[dict]:
    """One campaign's scope (spec 04 section 4.4): `CAMPAIGN_BUILDERS` over a
    ctx whose history-proportional reads go through the overview projections.
    A composition, not an artifact: the ignore set is applied after it."""
    readstats.bump("todo.scope.campaign")
    return _run(_catalog.CAMPAIGN_BUILDERS, ctx)


def _library_chores() -> list[dict]:
    """Library chores once per global report, independent of an open campaign."""
    readstats.bump("todo.scope.library")
    return _run(_catalog.LIBRARY_BUILDERS, _context._Ctx(""))


def badge_count(cid: str, ctx: _context._Ctx | None = None) -> int:
    """A compatibility read for callers of the old rail count.

    The global rail omits its badge because counting every campaign on each
    navigation would defeat its cheap-read budget. A direct count still agrees
    with the page's current scope, including its scoped ignore preferences.
    """
    if ctx is not None and ctx.cid != cid:
        raise ValueError(f"a to-do context for {ctx.cid!r} cannot count for {cid!r}")
    return live(cid)["count"]


def live(cid: str) -> dict:
    """The chore list split into what counts and what has been waved off."""
    off = store.chores.ignored()
    if cid:
        ctx = _context._Ctx(cid)
        if not ctx.has_campaign():
            raise HTTPException(404, f"campaign not found: {cid}")
        every = _campaign_scope(ctx)
        name = store.campaigns.read_campaign(cid)["meta"].get("name") or cid
        for chore in every:
            chore["campaign_id"] = cid
            chore["campaign_name"] = name
    else:
        every = _library_chores()
        for campaign in store.campaigns.read.list_campaigns():
            campaign_id = campaign["id"]
            ctx = _context._Ctx(campaign_id)
            if not ctx.has_campaign():
                continue
            for candidate in _campaign_scope(ctx):
                candidate["campaign_id"] = campaign_id
                candidate["campaign_name"] = campaign.get("name") or campaign_id
                every.append(candidate)
    def is_ignored(chore: dict) -> bool:
        campaign_id = chore.get("campaign_id", "")
        key = f"{campaign_id}:{chore['id']}" if campaign_id else chore["id"]
        return key in off or (campaign_id and chore["id"] in off)
    live_chores = [c for c in every if not is_ignored(c)]
    return {
        "chores": live_chores,
        "ignored": [c for c in every if is_ignored(c)],
        # The badge number, and it is the one the reader still cares about:
        # an ignored chore is not counted anywhere.
        "count": len(live_chores),
        # The headings, in reading order, and only the ones that have
        # something under them. Sent rather than inferred by the view: a view
        # that derived the order from the chore list would reorder its own
        # headings whenever the data moved, which is the defect this replaces.
        "groups": _groups(live_chores),
    }


def _groups(chores: list[dict]) -> list[str]:
    """The groups present in `chores`, in `GROUP_ORDER`, unknowns last.

    Unknowns are appended in chore order rather than dropped: a heading this
    module can emit but did not think to list is a bug in `GROUP_ORDER`, and
    hiding the chores under it would make that bug invisible instead of merely
    misplaced.
    """
    present = {c["group"] for c in chores}
    known = [g for g in _catalog.GROUP_ORDER if g in present]
    rest: list[str] = []
    for c in chores:
        if c["group"] not in _catalog.GROUP_ORDER and c["group"] not in rest:
            rest.append(c["group"])
    return known + rest


@router.get("/todo")
def get_todo(campaign: str = ""):
    with overview_read("todo", "campaign" if campaign else "library"):
        return live(campaign)


@router.get("/todo/{chore_id}/items")
def get_todo_items(chore_id: str, campaign: str = ""):
    """The instances behind one chore's count.

    On demand, because this is the expensive half: `sheets` sweeps the cast and
    `taglines` walks the roster, and doing that for every chore whenever the
    page opens is what would make the page not worth opening. The page asks for
    the previews it is showing; its full active scene list uses the existing
    uncapped scene listing instead.
    """
    if chore_id not in _catalog.KNOWN:
        raise HTTPException(400, f"unknown chore: {chore_id}")
    if not campaign and chore_id not in _catalog.LIBRARY_IDS:
        # A campaign chore with no campaign has nothing to enumerate, and its
        # item function would be asked to resolve an empty id.
        return {"items": [], "total": 0, "truncated": False}
    items = _catalog.ITEMS[chore_id](campaign)
    return {"items": items[:_catalog.ITEM_CAP], "total": len(items),
            # Said out loud rather than left to be inferred from a short list:
            # a cap nobody mentions reads as "that is all of them".
            "truncated": len(items) > _catalog.ITEM_CAP}


def _campaign_readable(cid: str) -> bool:
    """`_Ctx.has_campaign`'s mapping, read live: a write never reaches an
    overview projection (spec 04 section 3.8), so the ignore route asks the
    store's own listing whether `cid` is a campaign it could answer about."""
    try:
        store.scenes.read.list_scenes(cid)
    except (store.CampaignNotFound, OSError):
        return False
    return True


@router.put("/todo/{chore_id}/ignored")
def put_todo_ignored(chore_id: str, body: dict):
    """Wave a chore off, or take it back.

    The id is checked against the chores this store can actually produce rather
    than accepted blind: an ignore set that accumulates ids nothing emits is a
    file that grows forever and silences things nobody can name.
    """
    if chore_id not in _catalog.KNOWN:
        raise HTTPException(400, f"unknown chore: {chore_id}")
    campaign = str(body.get("campaign") or "")
    if chore_id in _catalog.CAMPAIGN_IDS:
        if not campaign or not _campaign_readable(campaign):
            raise HTTPException(404, f"campaign not found: {campaign}")
    elif campaign:
        raise HTTPException(400, "library chores cannot have a campaign")
    on = bool(body.get("ignored"))
    ids = tuple(row["id"] for row in store.campaigns.read.list_campaigns())
    store.chores.set_ignored(chore_id, on, campaign=campaign,
                             campaign_ids=ids, campaign_chore_ids=_catalog.CAMPAIGN_IDS)
    return {"ok": True, "ignored": sorted(store.chores.ignored())}
