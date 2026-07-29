"""Weather (#45, #195): the scene's current weather and the campaign's
override spans."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import store
from .common import _campaign_root_or_404, _require_scene
from .models import WeatherOverride, WeatherRange

router = APIRouter()


# ---- weather (#45, #195) ----
#
# Declared here, well before `@router.get("/campaigns/{cid}/{kind}")` at the
# bottom of this module. A weather GET registered after it is captured as an
# entity-list request for kind "weather" — and that applies to every route in
# this block, not just the ones with a {kind}-shaped path.

def _weather_now(cid: str, sid: str, location: str | None, native: str | None):
    """The scene's current location and moment, or the caller's preview pair."""
    scene = _require_scene(cid, sid)
    if location is None:
        history = store.scenes.get_location_history(cid, sid)
        location = history[-1] if history else None
    if native is None:
        moments = store.scenes.get_time_history(cid, sid)
        native = moments[-1] if moments else None
    return scene, location, native


@router.get("/campaigns/{cid}/scenes/{sid}/weather")
def get_scene_weather(cid: str, sid: str, location: str | None = None, native: str | None = None):
    """Resolved weather for one scene, with the popover's source data.

    The scene id is load-bearing rather than decorative: location and moment
    live per scene in location_history / time_history, so a campaign has as
    many "current moments" as it has scenes, and resolving from cid alone would
    return an arbitrary scene's sky.
    """
    _, location, native = _weather_now(cid, sid, location, native)
    got = store.weather.resolve(cid, location, native)
    if got is None:
        return {"weather": None, "location": location, "native": native}
    season = got["tables"]
    return {
        "weather": {a: got[a] for a in store.weather.AXES},
        "source": got["source"], "procedural": got["procedural"],
        "stack": got["stack"], "climate": got["climate"], "season": got["season"],
        "location": location, "native": native,
        # The popover's "rest of today" needs the position within the day, and
        # the client cannot derive it without the block grid.
        "ordinal": got["ordinal"],
        # ...and cannot derive the count at all between 00:00 and 03:59, where
        # the current block is the *previous* date's night. Position 4 there is
        # indistinguishable from an ordinary 22:00 night, so the client would
        # compute one block for a moment with a whole day still ahead of it.
        "blocks_left_today": got["blocks_left_today"],
        # The tables come from the server because the client cannot determine
        # them: the climate may be inherited from the campaign default or
        # fallen back from a dangling id, and the season depends on
        # year-fraction arithmetic over the campaign's calendar. Deriving them
        # client-side means reimplementing the fallback chain and the calendar
        # maths, and lets the popover disagree with the weather beside it.
        "tables": {
            "temperature": [e["name"] for e in season.get("temperature", []) if e["weight"] > 0],
            "condition": [e["name"] for e in season.get("conditions", []) if e["weight"] > 0],
            "wind": [e["name"] for e in season.get("wind", []) if e["weight"] > 0],
        },
    }


@router.put("/campaigns/{cid}/weather")
def put_weather(cid: str, body: WeatherOverride):
    """Set or clear an override span.

    The target is in the body rather than the route: weather.json holds it only
    as an outer object key and the span record has no location field, so a
    handler given just `cid` has nothing to key the write by. One endpoint
    therefore covers both a location and the campaign-wide default.
    """
    _campaign_root_or_404(cid)
    try:
        cfg = store.calendars.read_calendar(store.campaigns.campaign_root(cid))
        provider = store.calendars.get_provider(cfg["primary"])
        start = store.weather.overrides.ordinal_of(
            list(store.weather.overrides.resolve_endpoint(provider, body.start, end=False)))
        end = None
        if body.end is not None:
            end = store.weather.overrides.ordinal_of(
                list(store.weather.overrides.resolve_endpoint(provider, body.end, end=True)))
    except (store.calendars.CalendarError, KeyError, TypeError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=f"unparseable moment: {e}")
    if body.blocks is not None and body.blocks < 1:
        # max(1, ...) would turn an empty selection into a one-block override
        # and report success for it.
        raise HTTPException(status_code=400, detail="blocks must be at least 1")
    if end is not None and end <= start:
        # Such a span covers no block, so it appears in no covering stack and
        # its id is discoverable nowhere — success reported for an override
        # that can never apply.
        raise HTTPException(status_code=400,
                            detail="the override range ends before it starts")

    if body.clear:
        n = store.weather.overrides.clear(cid, provider, body.location, body.start, body.end,
                                          blocks=body.blocks)
        return {"cleared": n}

    axes = {a: getattr(body, a) for a in store.weather.AXES}
    if body.suppress is not None:
        # `put` filters unknown names out, so `suppress: ["humidity"]` alone
        # would store a record affecting no axis and report it as a successful
        # override.
        _weather_axes(body.suppress)
        if not body.suppress:
            raise HTTPException(status_code=400, detail="suppress names no axes")
        both = [a for a in body.suppress if axes.get(a)]
        if both:
            # Resolution checks suppression first, so the record would report a
            # successful authored value for an axis that stays procedural.
            raise HTTPException(
                status_code=400,
                detail=f"cannot set and suppress the same axis: {', '.join(both)}")
    if not any(axes.values()) and not body.suppress:
        # A span setting no axis appears in no covering stack, so its generated
        # id is discoverable nowhere and repeated calls would quietly accumulate
        # rows no client can see or delete.
        raise HTTPException(status_code=400, detail="at least one axis is required")
    if body.blocks is not None:
        start = store.weather.overrides.ordinal_of(
            list(store.weather.overrides.resolve_endpoint(provider, body.start, end=False)))
        return store.weather.overrides.put_ordinals(
            cid, body.location, body.start, start, start + max(1, body.blocks), axes,
            note=body.note or "", source="manual", suppress=body.suppress)
    record = store.weather.overrides.put(
        cid, provider, body.location, body.start, body.end, axes,
        note=body.note or "", source="manual", suppress=body.suppress)
    return record


def _weather_axes(axes) -> list[str] | None:
    """Reject anything that is not a real axis.

    The store filters too, but a caller sending `to_fixed` deserves a 400
    rather than a silently narrowed no-op — the value it asked to clear would
    otherwise appear to have been cleared.
    """
    if axes is None:
        return None
    bad = [a for a in axes if a not in store.weather.AXES]
    if bad:
        raise HTTPException(status_code=400,
                            detail=f"unknown weather axes: {', '.join(map(str, bad))}")
    return list(axes)


def _weather_bounds(provider, body) -> None:
    """Reject a range whose end does not follow its start.

    The same check `put_weather` makes, applied here too: `_cut` given an
    inverted interval processes it anyway, and for an open-ended override that
    means building the head and discarding everything after `start` — a
    malformed clear silently truncating a real override instead of failing.
    """
    try:
        lo = store.weather.overrides.ordinal_of(
            list(store.weather.overrides.resolve_endpoint(provider, body.start, end=False)))
        hi = None
        if getattr(body, "blocks", None) is not None:
            if body.blocks < 1:
                raise HTTPException(status_code=400, detail="blocks must be at least 1")
            hi = lo + body.blocks
        elif body.end is not None:
            hi = store.weather.overrides.ordinal_of(
                list(store.weather.overrides.resolve_endpoint(provider, body.end, end=True)))
    except (store.calendars.CalendarError, KeyError, TypeError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=f"unparseable moment: {e}")
    if hi is not None and hi <= lo:
        raise HTTPException(status_code=400, detail="the range ends before it starts")


def _weather_provider(cid: str):
    try:
        cfg = store.calendars.read_calendar(store.campaigns.campaign_root(cid))
        return store.calendars.get_provider(cfg["primary"])
    except (store.calendars.CalendarError, KeyError, TypeError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=f"unreadable calendar: {e}")


@router.post("/campaigns/{cid}/weather/clear")
def post_weather_clear(cid: str, body: WeatherRange):
    """Return named axes to procedural over a range, atomically.

    One server-side operation rather than client-orchestrated edits. Removing a
    single axis from a span that sets several means *mutating* that record
    while preserving its source, note, set_at and range — and the client has
    only a whole-record DELETE and a create-shaped PUT, so it would delete and
    recreate, losing exactly the fields precedence depends on, across a stack
    of spans non-atomically.
    """
    _campaign_root_or_404(cid)
    provider = _weather_provider(cid)
    _weather_bounds(provider, body)
    try:
        n = store.weather.overrides.clear(cid, provider, body.location, body.start,
                                          body.end, axes=_weather_axes(body.axes),
                                          blocks=body.blocks)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=f"unparseable moment: {e}")
    except store.weather.overrides.OverrideWriteError as e:
        raise HTTPException(status_code=500, detail=f"could not write weather: {e}")
    return {"cleared": n}


@router.post("/campaigns/{cid}/weather/resume")
def post_weather_resume(cid: str, body: WeatherRange):
    """Undo suppression, restoring an inherited override over a range.

    Axis-aware: one suppression routinely names several axes, since clearing
    all three at once produces exactly that, so dropping the record would
    restore inheritance for axes the user meant to keep suppressed.
    """
    _campaign_root_or_404(cid)
    provider = _weather_provider(cid)
    _weather_bounds(provider, body)
    try:
        n = store.weather.overrides.resume(cid, provider, body.location, body.start,
                                           body.end, axes=_weather_axes(body.axes),
                                           blocks=body.blocks)
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=400, detail=f"unparseable moment: {e}")
    except store.weather.overrides.OverrideWriteError as e:
        raise HTTPException(status_code=500, detail=f"could not write weather: {e}")
    return {"resumed": n}


@router.put("/campaigns/{cid}/weather/{storage_key}/{span_id}")
def put_weather_span(cid: str, storage_key: str, span_id: str, body: WeatherOverride):
    """Rewrite one span atomically, keeping its identity and precedence.

    The client-side alternative — DELETE then PUT — destroys the original if
    the create fails, which for a user who was only editing a note is the
    worst possible outcome.

    `blocks` is counted from `blocks_from` when given rather than from
    `start`, so changing the duration of a span that began days ago applies
    the new length from the moment being looked at while keeping the coverage
    it already had.
    """
    _campaign_root_or_404(cid)
    provider = _weather_provider(cid)
    axes = {a: getattr(body, a) for a in store.weather.AXES}
    if not any(axes.values()) and not body.suppress:
        raise HTTPException(status_code=400, detail="at least one axis is required")
    try:
        start = store.weather.overrides.ordinal_of(
            list(store.weather.overrides.resolve_endpoint(provider, body.start, end=False)))
        end = None
        if body.blocks is not None:
            if body.blocks < 1:
                raise HTTPException(status_code=400, detail="blocks must be at least 1")
            anchor = start
            if body.blocks_from:
                anchor = store.weather.overrides.ordinal_of(
                    list(store.weather.overrides.resolve_endpoint(
                        provider, body.blocks_from, end=False)))
            end = anchor + body.blocks
        elif body.end is not None:
            end = store.weather.overrides.ordinal_of(
                list(store.weather.overrides.resolve_endpoint(provider, body.end, end=True)))
    except (store.calendars.CalendarError, KeyError, TypeError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=f"unparseable moment: {e}")
    if end is not None and end <= start:
        raise HTTPException(status_code=400, detail="the override range ends before it starts")
    try:
        got = store.weather.overrides.replace(
            cid, storage_key, span_id, from_ordinal=start, to_ordinal=end,
            native=body.start, axes=axes, note=body.note or "", suppress=body.suppress)
    except store.weather.overrides.OverrideWriteError as e:
        raise HTTPException(status_code=500, detail=f"could not write weather: {e}")
    if got is None:
        raise HTTPException(status_code=404, detail="override not found")
    return got


@router.delete("/campaigns/{cid}/weather/{storage_key}/{span_id}")
def delete_weather(cid: str, storage_key: str, span_id: str):
    """Retract a span outright — unlike clearing, which takes a range.

    Keyed by storage key as well as id. The key is not the scene's location and
    cannot be inferred from it: a covering span may live under `_default`,
    which is why the read route returns each span's key alongside its id.
    """
    _campaign_root_or_404(cid)
    if not store.weather.overrides.delete(cid, span_id, storage_key):
        raise HTTPException(status_code=404, detail="override not found")
    return {"ok": True}
