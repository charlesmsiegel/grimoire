"""The campaign clock (#100): one "now" per campaign, advanced with a reason,
and the deterministic digest of what an advance crossed."""

from __future__ import annotations

import json

import pytest

from grimoire.store import (appearances, calendars, campaigns, characters, chronicle,
                            clock, plot, scenes, worlds)


def _campaign(monkeypatch, tmp_path, calendar="gregorian"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Run", wid, calendar=calendar)


# ---- reading and seeding ---------------------------------------------------

def test_missing_clock_reads_as_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert clock.read(cid) == {"now": "", "log": []}
    assert clock.now(cid) == ""


def test_garbled_clock_reads_as_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock._path(cid).write_text("{not json", encoding="utf-8")
    assert clock.read(cid) == {"now": "", "log": []}


def test_clock_of_wrong_shape_reads_as_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock._path(cid).write_text(json.dumps({"now": ["oops"], "log": "nope"}), encoding="utf-8")
    assert clock.read(cid) == {"now": "", "log": []}


def test_a_hand_edited_log_row_reads_back_as_text(monkeypatch, tmp_path):
    """Every field is a string or the panel that renders it blanks (plot._field)."""
    cid = _campaign(monkeypatch, tmp_path)
    clock._path(cid).write_text(json.dumps(
        {"now": "2026-05-01", "log": [{"from": {"oops": 1}, "to": "2026-05-01"}, "not a row"]}),
        encoding="utf-8")
    assert clock.read(cid) == {"now": "2026-05-01",
                               "log": [{"from": "", "to": "2026-05-01", "reason": "", "at": ""}]}


def test_now_seeds_from_the_latest_chronicle_date(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    chronicle.absorb(cid, {"id": "001--a", "one_line": "x", "summary": "y", "keywords": [],
                           "cast": [], "location": "", "date": "2026-03-01"})
    chronicle.absorb(cid, {"id": "002--b", "one_line": "x", "summary": "y", "keywords": [],
                           "cast": [], "location": "", "date": "2026-03-04T21:30"})
    assert clock.now(cid) == "2026-03-04T21:30"


def test_a_written_clock_wins_over_the_chronicle(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    chronicle.absorb(cid, {"id": "001--a", "one_line": "x", "summary": "y", "keywords": [],
                           "cast": [], "location": "", "date": "2026-03-01"})
    clock.advance(cid, to="2026-03-10", reason="a week on the road")
    assert clock.now(cid) == "2026-03-10"


def test_a_handed_in_fallback_replaces_the_chronicle_read_but_not_the_clock(monkeypatch, tmp_path):
    """The precedence stays `clock.now`'s; only the datum comes from the caller."""
    cid = _campaign(monkeypatch, tmp_path)
    chronicle.absorb(cid, {"id": "001--a", "one_line": "x", "summary": "y", "keywords": [],
                           "cast": [], "location": "", "date": "2026-03-01"})
    assert clock.now(cid, fallback="2026-09-09") == "2026-09-09"   # chronicle not consulted
    clock.advance(cid, to="2026-05-10", reason="the clock still wins")
    assert clock.now(cid, fallback="2026-09-09") == "2026-05-10"


def test_now_survives_a_garbled_chronicle(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "chronicle.json").write_text("{oops", encoding="utf-8")
    assert clock.now(cid) == ""


# ---- advancing ------------------------------------------------------------

def test_advance_to_a_date_sets_now_and_logs_the_reason(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    out = clock.advance(cid, to="2026-05-01", reason="the caravan reaches the coast")
    assert out["moved"] is True
    assert out["digest"]["to"] == "2026-05-01"
    assert out["digest"]["from"] == ""
    assert clock.now(cid) == "2026-05-01"
    log = clock.read(cid)["log"]
    assert len(log) == 1
    assert log[0]["from"] == "" and log[0]["to"] == "2026-05-01"
    assert log[0]["reason"] == "the caravan reaches the coast"
    assert log[0]["at"]


def test_advance_by_days_walks_the_fixed_day_axis(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-02-26", reason="start")
    out = clock.advance(cid, days=3, reason="three days of rain")
    assert out["digest"]["from"] == "2026-02-26"
    assert out["digest"]["to"] == "2026-03-01"
    assert out["digest"]["elapsed_days"] == 3
    assert clock.now(cid) == "2026-03-01"


def test_advance_by_days_keeps_the_time_of_day(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-02-26T21:30", reason="start")
    assert clock.advance(cid, days=2, reason="two nights")["digest"]["to"] == "2026-02-28T21:30"


def test_advance_by_days_needs_a_current_date(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(clock.ClockError):
        clock.advance(cid, days=3, reason="nowhere to start from")
    assert clock.read(cid) == {"now": "", "log": []}


def test_advance_needs_a_target_or_a_duration(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(clock.ClockError):
        clock.advance(cid, reason="neither")


def test_advance_rejects_an_unreachable_duration(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-01-01", reason="start")
    with pytest.raises(clock.ClockError):
        clock.advance(cid, days=10 ** 9, reason="past the end of the calendar")
    assert clock.now(cid) == "2026-01-01"


def test_a_garbled_stored_moment_refuses_a_duration_but_a_skip_repairs_it(monkeypatch, tmp_path):
    """A hand-edited `now` this calendar cannot read has no fixed day, so there is
    nothing to add days to — and saying so beats inventing an anchor. Skipping to
    a date needs no anchor, which is the way back out."""
    cid = _campaign(monkeypatch, tmp_path)
    clock._path(cid).write_text(json.dumps({"now": "banana", "log": []}), encoding="utf-8")
    with pytest.raises(clock.ClockError):
        clock.advance(cid, days=3, reason="from nowhere")
    out = clock.advance(cid, to="2026-05-01", reason="repairing the clock")
    assert out["moved"] is True and clock.now(cid) == "2026-05-01"
    assert out["digest"]["elapsed_days"] == 0        # no span from an unreadable moment
    assert out["digest"]["from"] == "banana"         # ...but it is still what we left


def test_advance_rejects_a_bad_date(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(calendars.CalendarError):
        clock.advance(cid, to="2026-13-40", reason="no such day")


def test_advance_to_the_current_moment_is_a_no_op_however_it_was_spelled(monkeypatch, tmp_path):
    """The moment being left is canonicalized before it is compared.

    A seeded `now` comes from the chronicle, whose dates are whatever the absorb
    wrote — and a calendar that canonicalizes its notation (Hebrew capitalizes
    the month) then makes a re-advance to the *same day* look like a move, logging
    a row whose `from` and `to` are one date spelled two ways.
    """
    cid = _campaign(monkeypatch, tmp_path, calendar="hebrew")
    chronicle.absorb(cid, {"id": "001--a", "one_line": "x", "summary": "y", "keywords": [],
                           "cast": [], "location": "", "date": "5786-kislev-25"})
    out = clock.advance(cid, to="5786-KISLEV-25", reason="the same day, shouted")
    assert out["moved"] is False
    assert clock.read(cid)["log"] == []


def test_advance_logs_the_canonical_form_of_the_moment_it_left(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, calendar="hebrew")
    chronicle.absorb(cid, {"id": "001--a", "one_line": "x", "summary": "y", "keywords": [],
                           "cast": [], "location": "", "date": "5786-kislev-25"})
    clock.advance(cid, days=1, reason="one day on")
    assert clock.read(cid)["log"][0]["from"] == "5786-Kislev-25"


def test_advance_to_the_current_moment_is_a_no_op(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-01", reason="start")
    out = clock.advance(cid, to="2026-05-01", reason="again")
    assert out["moved"] is False
    assert len(clock.read(cid)["log"]) == 1  # no second entry for a moment already reached


def test_advance_backward_is_recorded_with_a_negative_span(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-10", reason="start")
    out = clock.advance(cid, days=-4, reason="correcting a mis-set date")
    assert out["digest"]["elapsed_days"] == -4
    assert out["digest"]["backward"] is True
    assert clock.now(cid) == "2026-05-06"


def test_advance_truncates_an_overlong_reason(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-01", reason="x" * (clock.REASON_LIMIT + 50))
    assert clock.read(cid)["log"][0]["reason"] == "x" * clock.REASON_LIMIT


def test_the_log_keeps_the_newest_entries_when_it_reaches_its_cap(monkeypatch, tmp_path):
    """The cap drops the *oldest* rows, and what it drops is gone — a dropped
    row's reason is recorded nowhere else. Pinned because it is data loss, and a
    cap that silently kept the wrong end would be worse than no cap."""
    cid = _campaign(monkeypatch, tmp_path)
    monkeypatch.setattr(clock, "LOG_LIMIT", 3)
    for day in range(1, 6):
        clock.advance(cid, to=f"2026-05-{day:02d}", reason=f"day {day}")
    log = clock.read(cid)["log"]
    assert [e["reason"] for e in log] == ["day 3", "day 4", "day 5"]
    assert clock.now(cid) == "2026-05-05"   # the moment itself is never trimmed


def test_preview_computes_the_digest_without_writing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-12-20", reason="start")
    before = clock.read(cid)
    digest = clock.preview(cid, days=10)
    assert digest["to"] == "2026-12-30" and digest["elapsed_days"] == 10
    assert clock.read(cid) == before


# ---- the digest -----------------------------------------------------------

def test_digest_names_the_holidays_the_skip_crossed(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-12-24", reason="start")
    digest = clock.advance(cid, days=3, reason="through Christmas")["digest"]
    names = [h["name"] for h in digest["holidays"]]
    assert "Christmas Day" in names
    crossed = next(h for h in digest["holidays"] if h["name"] == "Christmas Day")
    assert crossed["native"] == "2026-12-25" and crossed["in_days"] == 1
    assert crossed["friendly"] == "25 December 2026"


def test_digest_excludes_a_holiday_on_the_moment_being_left(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-12-25", reason="start")
    digest = clock.advance(cid, days=1, reason="boxing day")["digest"]
    assert [h["name"] for h in digest["holidays"]] == []  # the 25th was already lived through


def test_digest_names_the_birthdays_the_skip_crossed(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(campaigns.read_campaign(cid)["meta"]["world"])
    chid, _ = characters.create_character(wroot, "Seraphine", "default",
                                          characters.blank_card("Seraphine"))
    characters.set_birthdate(wroot, chid, "1990-06-29")
    sid = scenes.create_scene(cid, "S")
    appearances.appear(cid, sid, "characters", chid, "default", "npc")
    clock.advance(cid, to="2026-06-20", reason="start")
    digest = clock.advance(cid, days=14, reason="a fortnight at sea")["digest"]
    assert digest["birthdays"] == [{"name": "Seraphine", "age": 36,
                                   "native": "2026-06-29", "friendly": "29 June 2026"}]


def test_digest_survives_a_provider_that_answers_a_range_with_rubbish(monkeypatch, tmp_path):
    """A calendar provider can be user-authored plugin code, so its `holidays`
    rows are validated rather than trusted: a non-integer `fixed` would reach
    `describe` as a TypeError, and a non-string `name` would reach React, which
    blanks the panel it was about to be rendered into."""
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-01", reason="start")
    provider = calendars.primary_provider(campaigns.campaign_root(cid))
    good = calendars.fixed_of(provider, "2026-05-03")
    monkeypatch.setattr(type(provider), "holidays", lambda self, lo, hi: [
        {"name": "Bad Fixed", "fixed": "2026-05-02"},   # a string where a day belongs
        {"name": {"oops": 1}, "fixed": good},           # a name React cannot render
        {"fixed": good + 1},                            # no name at all
    ])
    digest = clock.advance(cid, days=5, reason="on")["digest"]
    assert [(h["name"], h["native"]) for h in digest["holidays"]] == [
        ("", "2026-05-03"), ("", "2026-05-04")]   # the unusable row dropped, names blanked


def test_digest_lists_the_open_threads_the_skip_left_untouched(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    plot.set_movement(cid, "debt", "The moneylender's debt", "open", "Interest accrues.", "001--a")
    plot.set_movement(cid, "closed-one", "Settled", "closed", "Paid.", "001--a")
    clock.advance(cid, to="2026-05-01", reason="start")
    digest = clock.advance(cid, days=30, reason="a month of travel")["digest"]
    assert [t["id"] for t in digest["open_threads"]] == ["debt"]


def test_digest_survives_a_garbled_plot_file(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "plot.json").write_text("{oops", encoding="utf-8")
    clock.advance(cid, to="2026-05-01", reason="start")
    assert clock.advance(cid, days=5, reason="on")["digest"]["open_threads"] == []


def test_digest_of_an_overlong_skip_is_truncated_not_itemized(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-01-01", reason="start")
    digest = clock.advance(cid, days=clock.SCAN_LIMIT_DAYS + 1, reason="a generation")["digest"]
    assert digest["elapsed_days"] == clock.SCAN_LIMIT_DAYS + 1   # the span itself stays exact
    assert digest["truncated"] is True
    assert digest["holidays"] == [] and digest["birthdays"] == []


def test_a_skip_of_exactly_the_scan_limit_is_still_itemized(monkeypatch, tmp_path):
    """The boundary, so `> SCAN_LIMIT_DAYS` cannot quietly become `>=`."""
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-01-01", reason="start")
    digest = clock.advance(cid, days=clock.SCAN_LIMIT_DAYS, reason="a long year")["digest"]
    assert digest["truncated"] is False
    assert "Christmas Day" in [h["name"] for h in digest["holidays"]]


def test_a_capped_crossing_list_reports_itself_as_truncated(monkeypatch, tmp_path):
    """The row cap sets the same flag the span limit does — a trimmed list must
    never read as a complete one. Forced through the constant rather than by
    inventing sixty holidays, which is the cap's behaviour, not its threshold."""
    cid = _campaign(monkeypatch, tmp_path)
    monkeypatch.setattr(clock, "MAX_ROWS", 1)
    clock.advance(cid, to="2026-12-20", reason="start")
    digest = clock.advance(cid, days=40, reason="through the new year")["digest"]
    assert digest["truncated"] is True
    assert len(digest["holidays"]) == 1   # trimmed, not emptied


def test_digest_carries_both_ends_in_friendly_form(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-01", reason="start")
    digest = clock.advance(cid, days=1, reason="a day")["digest"]
    assert digest["from_friendly"] == "1 May 2026"
    assert digest["to_friendly"] == "2 May 2026"


def test_digest_works_in_a_non_gregorian_calendar(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, calendar="hebrew")
    clock.advance(cid, to="5786-kislev-24", reason="start")
    digest = clock.advance(cid, days=2, reason="two days")["digest"]
    assert digest["to"] == "5786-Kislev-26"   # canonicalized by the provider, not echoed
    assert "Chanuka" in " ".join(h["name"] for h in digest["holidays"])


# ---- reconciliation with per-scene time ------------------------------------

def test_observe_moves_the_clock_forward(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-01", reason="start")
    out = clock.observe(cid, "2026-05-09", "scene 002")
    # `fired` is the scheduled events this move crossed (#101) — none here,
    # and empty rather than absent so every caller can read one shape.
    assert out == {"moved": True, "now": "2026-05-09", "fired": []}
    row = clock.read(cid)["log"][-1]
    assert (row["from"], row["to"], row["reason"]) == ("2026-05-01", "2026-05-09", "scene 002")
    assert row["at"]   # stamped, not merely present — an unstamped row cannot be ordered


def test_observe_never_moves_the_clock_backward(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-01", reason="start")
    assert clock.observe(cid, "2026-04-01", "a flashback") == {
        "moved": False, "now": "2026-05-01", "fired": []}
    assert len(clock.read(cid)["log"]) == 1


def test_observe_moves_within_a_day_on_the_time_of_day(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-01T09:00", reason="start")
    assert clock.observe(cid, "2026-05-01T21:30", "later that evening")["moved"] is True
    assert clock.now(cid) == "2026-05-01T21:30"


def test_observe_seeds_an_unset_clock(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert clock.observe(cid, "2026-05-01", "first dated scene")["moved"] is True
    assert clock.now(cid) == "2026-05-01"


def test_observe_ignores_a_date_the_calendar_rejects(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    clock.advance(cid, to="2026-05-01", reason="start")
    assert clock.observe(cid, "2026-13-40", "nonsense")["moved"] is False
    assert clock.now(cid) == "2026-05-01"
