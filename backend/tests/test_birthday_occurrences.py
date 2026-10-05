"""`birthdays.occurrences`: the one birthday scan, and `upcoming` as its projection.

The scan is lazy (a generator) so that `upcoming`, which wants only the first
hit per actor, stops on the day it always stopped on; `occurrences` takes the
whole window per actor and loses an actor whose walk raises anywhere in it.
"""

from __future__ import annotations

from grimoire.store import birthdays, calendars, campaigns, characters, clock, overlay, worlds


def _campaign(monkeypatch, tmp_path, *, calendar="gregorian", now="2026-05-08"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, calendar=calendar)
    if now is not None:
        clock.advance(cid, to=now)
    return cid


def _provider(cid):
    return calendars.primary_provider(campaigns.campaign_root(cid))


def _now_fixed(cid):
    return calendars.fixed_of(_provider(cid), clock.now(cid))


def _actor(cid, name, birth):
    aid, _vid = overlay.create_character(cid, name)
    characters.set_birthdate(campaigns.campaign_root(cid), aid, birth)
    return aid


def _occ(cid, window=calendars.UPCOMING_WINDOW_DAYS):
    return birthdays.occurrences(cid, _now_fixed(cid), window)


def test_gather_rows_carry_their_ref(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    aid = _actor(cid, "Mara", "1985-05-09")
    rows = birthdays.gather(cid, [], visible_characters=True)
    assert rows
    assert all(r["ref"] == f"characters:{aid}" for r in rows)


def test_exact_birthday_occurrence(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    aid = _actor(cid, "Mara", "1985-05-09")
    p = _provider(cid)
    f = calendars.fixed_of(p, "2026-05-09")
    assert _occ(cid) == [{
        "ref": f"birthday:characters:{aid}:{f}", "name": "Mara", "actor": f"characters:{aid}",
        "precision": "exact", "fixed": f, "year": 2026, "month_key": None,
        "month_name": None, "in_days": 1, "age": 41, "native": "2026-05-09",
        "friendly": "9 May 2026"}]


def test_yearless_birthday_has_no_age(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    aid = _actor(cid, "Mara", "--05-09")
    f = calendars.fixed_of(_provider(cid), "2026-05-09")
    [row] = _occ(cid)
    assert row["ref"] == f"birthday:characters:{aid}:{f}"
    assert (row["precision"], row["age"], row["in_days"], row["fixed"]) == ("yearless", None, 1, f)


def test_month_only_birthday_has_no_day(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    aid = _actor(cid, "Mara", "--05")
    [row] = _occ(cid)
    assert row == {
        "ref": f"birthday:characters:{aid}:month:2026-05", "name": "Mara",
        "actor": f"characters:{aid}", "precision": "month", "fixed": None, "year": 2026,
        "month_key": "05", "month_name": "May", "in_days": None, "age": None,
        "native": "", "friendly": "May 2026"}


def test_today_is_inside_the_window(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _actor(cid, "Mara", "--05-08")
    [row] = _occ(cid)
    assert row["in_days"] == 0
    assert [r["in_days"] for r in _occ(cid, window=0)] == [0]


def test_window_zero_drops_tomorrow(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _actor(cid, "Mara", "--05-09")
    assert _occ(cid, window=0) == []


def test_year_only_and_unmatched_keys_yield_nothing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    aid = _actor(cid, "Mara", "1985")
    assert _occ(cid) == []
    for birth in ("--5", "--Mirtul"):
        characters.set_birthdate(campaigns.campaign_root(cid), aid, birth)
        assert _occ(cid) == [], birth


def test_leap_only_birthdays_never_invent_a_day_gregorian(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, now="2027-02-20")
    aid = _actor(cid, "Mara", "2000-02-29")
    assert _occ(cid) == []
    characters.set_birthdate(campaigns.campaign_root(cid), aid, "--02-29")
    assert _occ(cid) == []


def test_leap_only_birthdays_never_invent_a_day_hebrew_common_year(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, calendar="hebrew", now="5786-Shevat-20")
    root = campaigns.campaign_root(cid)
    aid = _actor(cid, "Mara", "--Adar1")
    assert _occ(cid) == []   # the literal month-key match: a common year has no Adar1
    characters.set_birthdate(root, aid, "--Adar")
    [row] = _occ(cid)
    assert (row["precision"], row["month_key"]) == ("month", "Adar")
    characters.set_birthdate(root, aid, "--Adar2-14")
    [row] = _occ(cid)
    assert (row["precision"], row["in_days"]) == ("yearless", 24)


def test_leap_only_birthdays_never_invent_a_day_hebrew_leap_year(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, calendar="hebrew", now="5787-Adar1-20")
    root = campaigns.campaign_root(cid)
    aid = _actor(cid, "Mara", "--Adar")
    assert _occ(cid) == []   # the same literal match, the other way (an Open Question)
    characters.set_birthdate(root, aid, "--Adar-14")
    [row] = _occ(cid)
    assert (row["precision"], row["in_days"]) == ("yearless", 24)
    characters.set_birthdate(root, aid, "--Adar1")
    [row] = _occ(cid)
    assert row["precision"] == "month"
    assert birthdays.upcoming(cid, clock.now(cid), [], visible_characters=True) == [
        {"name": "Mara", "age": None, "when": "this month"}]


def test_a_bad_birthdate_skips_only_that_actor(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _actor(cid, "Mara", "--05-32")
    _actor(cid, "Winifred", "--05-09")
    assert [r["name"] for r in _occ(cid)] == ["Winifred"]


def _project(cid, row):
    """`upcoming`'s rendering of an occurrence row, written out independently."""
    if row["precision"] == "month":
        p = _provider(cid)
        today = p.describe(_now_fixed(cid))
        key = p.months(today["year"])[today["month"] - 1]["key"]
        when = ("this month" if (row["year"], row["month_key"]) == (today["year"], key)
                else f"in {row['month_name']}")
    else:
        when = "today" if row["in_days"] == 0 else f"in {row['in_days']} days"
    return {"name": row["name"], "age": row["age"], "when": when}


def test_upcoming_takes_the_first_hit_per_actor(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _actor(cid, "Mara", "--05-09")
    _actor(cid, "Winifred", "--06")
    got = birthdays.upcoming(cid, clock.now(cid), [], visible_characters=True)
    assert got == [{"name": "Mara", "age": None, "when": "in 1 days"},
                   {"name": "Winifred", "age": None, "when": "in June"}]
    occ = _occ(cid)
    firsts = []
    for name in ("Mara", "Winifred"):
        first = next(r for r in occ if r["name"] == name)
        assert first["year"] == 2026
        firsts.append(_project(cid, first))
    assert got == firsts


class _LaterDaysRaise:
    """The Gregorian provider, refusing every day after `limit`."""

    def __init__(self, inner, limit):
        self._inner, self._limit = inner, limit

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def describe(self, fixed):
        if fixed > self._limit:
            raise calendars.CalendarError("past the limit")
        return self._inner.describe(fixed)

    def is_anniversary(self, birth_fixed, asof_fixed):
        if asof_fixed > self._limit:
            raise calendars.CalendarError("past the limit")
        return self._inner.is_anniversary(birth_fixed, asof_fixed)


def test_upcoming_stops_at_the_first_hit(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _actor(cid, "Mara", "--05-09")
    now_fixed = _now_fixed(cid)
    wrapped = _LaterDaysRaise(_provider(cid), now_fixed + 1)
    monkeypatch.setattr(calendars, "primary_provider", lambda _root: wrapped)
    assert birthdays.upcoming(cid, clock.now(cid), [], visible_characters=True) == [
        {"name": "Mara", "age": None, "when": "in 1 days"}]
    assert birthdays.occurrences(cid, now_fixed, 30, provider=wrapped) == []


def test_upcoming_forwards_visible_characters(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _actor(cid, "Mara", "--05-09")
    now = clock.now(cid)
    assert birthdays.upcoming(cid, now, []) == []
    assert birthdays.upcoming(cid, now, [], visible_characters=True) == [
        {"name": "Mara", "age": None, "when": "in 1 days"}]
