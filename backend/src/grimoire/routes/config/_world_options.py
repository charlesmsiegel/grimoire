"""config: world_options responsibilities."""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException

from ... import store
from ._router import router as router


# ---- the entity kinds an import may route a row to (#138) ----
@router.get("/entity-kinds")
def get_entity_kinds():
    """The categories a review-table row may be reclassified to.

    `store.entities.ENTITY_KINDS` itself, in its own order, so the import
    dialogs stop keeping a second copy of the list: the per-row Category
    dropdown is built from this, and a kind added to the tuple reaches it
    without either dialog being edited.

    What the dropdown shows is this list INTERSECTED with the bundle's own
    (`useEntityKinds`), not this list outright. A kind the bundle has never
    heard of has no tab, label or editor there, so offering it would let a user
    file a row somewhere they could not then look at it -- correctly written
    and effectively lost. So the endpoint is not a way to introduce a kind
    ahead of the frontend; it is the half of the answer that keeps the dialog
    from offering a category this server would refuse.

    Not "with no frontend edit at all" -- adding a kind still means adding it
    to the frontend's own `ENTITY_KINDS`, which the tabs, labels and per-kind
    field table are keyed by and which
    `test_entities_store.py::test_the_frontend_ships_the_same_kind_list`
    requires. What this removes is a *hand-kept list of options*, in two
    components, that had to be found and edited each time; and what it adds is
    the one case that edit cannot cover -- a bundle older than the backend
    serving it, which still offers exactly the categories that backend accepts
    instead of the ones it shipped believing in.

    Deliberately NOT world-scoped (the issue floated
    `/worlds/{wid}/entity-kinds`). The kinds are a property of the code, not of
    a world, and a world-scoped path would promise a per-world answer that does
    not exist. That is the whole argument -- route order is NOT part of it:
    `routes.__init__` includes this module well before `entities`, so a
    world-scoped handler declared here would sit ahead of the generic
    `/worlds/{wid}/{kind}` catch-all for free, and `test_route_order.py` would
    say so if it did not.

    The issue offered two shapes for this and named the other one first:
    put the kinds *on the parse response* instead. That variant is genuinely
    cheaper here -- the list would arrive with the rows it applies to, from the
    process that will validate the commit, so it could not disagree with them
    in either direction, and the fallback, `kindOptions` and the whole
    bundle-skew case downstream would all be unnecessary. It was not taken for
    two reasons. `ScenarioProposal` is both the parse response AND the body the
    reviewer edits and posts back to `/scenario/import`, so a `kinds` field on
    it would be a key that is not part of the proposal, hung on the model
    anyway and lifted back out before use -- the same wart `art` already is
    (mirrored: `art` rides inbound and `post_scenario_import` pops it, `kinds`
    would ride outbound), and that one carries a comment apologising for
    itself. And the list has readers that never go
    through a parse at all: #27 wants this review table driven from a stored
    card, #119 wants it after the fact on committed records. A standalone GET
    serves those without either of them growing a parse step. The cost is real
    and is paid in the frontend, where `useEntityKinds` has a fallback and
    `kindOptions` has a seam that the parse-response shape would not need.

    `lorebook.commit` and `scenario.apply` validate an incoming category
    against the same tuple, so what this offers is exactly what they accept;
    `test_every_offered_kind_is_a_category_both_imports_accept` is that
    guarantee, taken against both commit paths rather than argued from the
    shared constant.
    """
    return {"kinds": list(store.entities.ENTITY_KINDS)}


@router.get("/calendars/providers")
def get_calendar_providers():
    return {"providers": store.calendars.list_providers()}


@router.get("/calendars/{provider}/year")
def get_calendar_year(provider: str, year: int = 0, region: str = ""):
    """One calendar's months and observances for a year, as a reference view.

    A calendar on its own has no holidays. They fall out of a *configured*
    calendar: `gregorian` produces none until it is given a region, `hebrew`
    switches its set on Israel-vs-diaspora, and every provider adds whatever
    `custom_holidays` its config carries. The Library has neither a world nor a
    campaign to take that config from, so it supplies one here -- which is why
    this route takes `region` and why the page has a picker for it.

    Read-only, and deliberately builds a throwaway config rather than reading
    any world's: this answers "what does this calendar do", not "what is that
    world's new year". Nothing is written.

    Holidays are asked for across the whole year in ONE call, because that is
    the shape the protocol offers (`holidays(start_fixed, end_fixed)`) and
    asking month by month would make a provider that scans days -- `hebrew`
    does -- do it twelve times over.
    """
    try:
        cal = store.calendars.get_provider({"provider": provider, "region": region})
    except store.calendars.CalendarError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    # `year` defaults to THIS calendar's current year, not to a Gregorian one.
    # A Hebrew year is around 5786 and a homebrew calendar's could be anything,
    # so a hardcoded default is a date most calendars cannot represent -- which
    # is exactly how this route first failed on `hebrew`.
    if not year:
        try:
            # The reader's own day, deliberately: "what year is it in this
            # calendar" is a wall-clock question about where they are, not a
            # UTC one. `astimezone()` takes the local zone explicitly so this
            # is a stated choice rather than a naive `today()`.
            today = datetime.now().astimezone().date()
            year = int(cal.describe(today.toordinal())["year"])
        except (store.calendars.CalendarError, KeyError, TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        months = cal.months(year)
        # The year's bounds in fixed days, taken from the calendar's own first
        # and last day rather than assumed: a provider's year is not
        # necessarily 365 days or twelve months, which is the entire reason
        # `months()` exists.
        first = cal.parse(f"{year}-{months[0]['key']}-01")
        last = cal.parse(f"{year}-{months[-1]['key']}-{months[-1]['days']:02d}")
        found = cal.holidays(first, last)
    except (store.calendars.CalendarError, IndexError, KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Each observance placed in the calendar's own terms, and carrying the key
    # of the month it lands in.
    #
    # `month_key`, not `describe()["month"]`, and the difference is the whole
    # reason this loop is not two lines. The protocol's two halves disagree
    # about what names a month: `months()` yields a `key` (`"01"`, `"Tishrei"`)
    # and `describe()` yields a `month` NUMBER (8, 12). A reader grouping
    # observances under months by that number finds no month with that key and
    # silently renders a year with no holidays in it -- the worst kind of
    # wrong, because it looks like an answer.
    #
    # Matched on the display name, which both halves do agree on, with the
    # month's own key carried through. `fixed` rides along so a caller that
    # wants to sort or diff has the number rather than a string to parse back.
    by_name = {str(m.get("name", "")): m.get("key") for m in months}
    out = []
    for h in found:
        try:
            d = cal.describe(h["fixed"])
        except store.calendars.CalendarError:
            continue
        key = by_name.get(str(d.get("month_name", "")))
        if key is None:
            # A month `describe` names that `months` does not list. Nothing in
            # the shipped calendars does this; a plugin could. Dropping it
            # would hide it, so it is carried with no key and the reader can
            # still show it outside the month grid.
            key = ""
        out.append({"name": h["name"], "fixed": h["fixed"],
                    "month_key": key,
                    "month": d.get("month"), "month_name": d.get("month_name", ""),
                    "day": d.get("day"), "friendly": d.get("friendly", "")})

    names = {p["id"]: p["name"] for p in store.calendars.list_providers()}
    return {"id": provider, "name": names.get(provider, provider),
            "year": year, "region": region,
            "months": months, "holidays": out}


# ---- climates (#40) ----

@router.get("/climates")
def get_climates():
    """The merged list, each entry carrying *both* tier flags.

    `builtin` and `custom` rather than one label: a single `custom` tag cannot
    distinguish a custom climate that shadows a preset from one that stands
    alone, and the editor needs that to choose between *Revert to preset* and
    *Delete*, and to know whether deleting frees the id.
    """
    return {"climates": store.climates.list_climates()}


@router.get("/climates/{climate_id}")
def get_climate(climate_id: str):
    doc = store.climates.get(climate_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="climate not found")
    return {"climate": doc, "builtin": store.climates.is_builtin(climate_id),
            "custom": store.climates.custom_path(climate_id).exists()}


@router.put("/climates/{climate_id}")
def put_climate(climate_id: str, body: dict):
    """Write a climate to the private tier, copying a preset on first edit.

    Validation is strict here on purpose. The resolver is lenient so a bad
    document can never take a turn down, which makes this the only place a
    mistake can be reported at all.
    """
    doc = dict(body or {})
    doc["id"] = climate_id  # the route is authoritative; a mismatched body id
                            # would write to a file the editor cannot reopen
    try:
        return {"climate": store.climates.save(doc)}
    except store.climates.ClimateError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not write climate: {e}") from e


def _climate_referrers(climate_id: str) -> dict:
    """Which campaigns default to this climate, and which locations name it.

    Disclosed rather than blocking. Deleting a custom-only climate silently
    moves every *untagged* location in a campaign that defaults to it, and the
    editor can only warn about that if it is told.
    """
    campaigns_using, locations_using = [], []
    # Not caught: an unreadable campaign list is an *unknown* result, and
    # returning an empty one would tell the editor nothing uses the climate —
    # defeating the fail-closed guard on the other side of the call.
    rows = store.campaigns.list_campaigns()
    for row in rows:
        cid = row["id"]
        # The *effective* default, not whatever the file literally says: a
        # campaign with no stored default (every one predating the weather work)
        # or an unreadable one still falls back to the shipped preset, and
        # reading the file here reported such a campaign as using nothing at
        # all — precisely the case the editor needs warning about.
        if store.campaign_climate.resolve_default(cid)["id"] == climate_id:
            campaigns_using.append({"id": cid, "name": row.get("name", cid)})
        # Not caught: one unreadable location file aborting the scan
        # would drop every valid reference in that campaign, and the editor
        # would report no impact for a climate those locations use.
        for loc in store.overlay.list_entities(cid, "locations"):
            if loc.get("climate") == climate_id:
                locations_using.append({"campaign": cid, "id": loc["id"],
                                        "name": loc.get("name", loc["id"])})
    return {"campaigns": campaigns_using, "locations": locations_using}


@router.get("/climates/{climate_id}/referrers")
def get_climate_referrers(climate_id: str):
    try:
        return _climate_referrers(climate_id)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not scan campaigns: {e}") from e


@router.delete("/climates/{climate_id}")
def delete_climate(climate_id: str):
    """Drop the private copy, reverting to the preset if there is one."""
    try:
        referrers = _climate_referrers(climate_id)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not scan campaigns: {e}") from e
    try:
        removed = store.climates.remove(climate_id)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not delete climate: {e}") from e
    if not removed:
        raise HTTPException(status_code=404, detail="no custom climate to delete")
    return {"ok": True, "reverted_to_preset": store.climates.is_builtin(climate_id),
            "referrers": referrers}
