"""worlds: calendar responsibilities."""

from __future__ import annotations

from fastapi import HTTPException

from ... import store
from ..common import _world_root_or_404
from ..models import CalendarConfig
from ._router import router as router


# ---- the world's calendar (#223) ----
#
# The same calendar.json the campaign routes edit, one level up. A world's copy
# is the DEFAULT its campaigns are created with: `campaigns.create_campaign`
# reads it out of the world root and writes the whole file -- `confirmed`
# included -- into the new campaign. A creation that names a calendar of its own
# is an explicit choice and confirms itself, so what the world's flag actually
# carries is every other path: a campaign created without one starts confirmed
# only because this world says it is settled, and its clock and scene inspector
# never ask again.
#
# Declared in `worlds`, which `routes.__init__` includes before `entities`, so
# `/worlds/{wid}/{kind}` cannot capture `calendar`; `test_route_order.py` holds
# that rather than this comment.
@router.get("/worlds/{wid}/calendar")
def get_world_calendar_config(wid: str):
    return store.calendars.read_calendar(_world_root_or_404(wid))


@router.put("/worlds/{wid}/calendar")
def put_world_calendar_config(wid: str, body: CalendarConfig):
    root = _world_root_or_404(wid)
    # Field-by-field rather than `_dump(body)` for the same reason the campaign
    # route does it: the store re-normalizes and coerces every field anyway
    # (`stale_after_days` included -- 0 or a missing field means "no opinion",
    # not a threshold of zero), so this is the shape of the request, not its
    # validation. `warn_days` is the one field where 0 is a real answer ("no
    # warnings"), which is why its no-opinion value is None instead.
    cfg = {"primary": body.primary, "secondary": body.secondary, "confirmed": body.confirmed,
           "stale_after_days": body.stale_after_days,
           "warn_days": body.warn_days}
    try:
        store.calendars.validate_calendar(cfg)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    store.calendars.write_calendar(root, cfg)
    return {"ok": True}


@router.get("/worlds/{wid}/calendar/months")
def get_world_calendar_months(wid: str, year: int):
    if not store.worlds.world_exists(wid):
        raise HTTPException(status_code=404, detail="world not found")
    cfg = store.calendars.read_calendar(store.worlds.world_root(wid))
    try:
        return {"months": store.calendars.get_provider(cfg["primary"]).months(year)}
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
