"""Commitment and thread aging (#103): overdue and stale, computed at read time.

The classification is pure and takes its present from the caller, so these
tests hand `prepare` a moment rather than advancing a clock — which is also how
`clock.digest` uses it, to say what a skip is *about* to make overdue.
"""

from __future__ import annotations

import json

from grimoire.store import (
    aging,
    calendars,
    campaigns,
    chronicle,
    clock,
    commitments,
    plot,
    scenes,
    worlds,
)


def _campaign(monkeypatch, tmp_path, calendar="gregorian"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Run", wid, calendar=calendar)


def _dated_scene(cid, title, native):
    """A scene with a date — and the id it has AFTER being dated: the first
    date a scene takes is stamped into its filename, so the id changes."""
    sid = scenes.create_scene(cid, title)
    return scenes.set_datetime(cid, sid, native)["id"]


def _threshold(cid, days):
    croot = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(croot)
    calendars.write_calendar(croot, {**cfg, "stale_after_days": days})


def _aged(cid, now):
    ctx = aging.prepare(cid, now)
    return (ctx,
            {t["id"]: t["aging"] for t in aging.annotate(ctx, plot.open_threads(cid))},
            {c["id"]: c["aging"] for c in aging.annotate(ctx, commitments.open_commitments(cid))})


# ---- staleness -------------------------------------------------------------

def test_a_thread_untouched_past_the_threshold_is_stale(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _dated_scene(cid, "The meeting", "2026-05-01")
    plot.set_movement(cid, "the-map", "The map", "open", "Mara found it.", sid)
    _ctx, threads, _ = _aged(cid, "2026-06-15")
    assert threads["the-map"]["state"] == aging.STALE
    assert threads["the-map"]["days_since"] == 45


def test_a_thread_inside_the_threshold_is_ok(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _dated_scene(cid, "The meeting", "2026-05-01")
    plot.set_movement(cid, "the-map", "The map", "open", "Mara found it.", sid)
    _ctx, threads, _ = _aged(cid, "2026-05-10")
    assert threads["the-map"] == {"state": aging.OK, "days_since": 9,
                                  "days_over": None, "due_in": None}


def test_the_threshold_comes_from_the_campaign(monkeypatch, tmp_path):
    """One integer in calendar.json, beside the rest of the campaign's time
    config — a slow-burn chronicle and a three-night thriller do not agree
    about how long is too long."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _dated_scene(cid, "The meeting", "2026-05-01")
    plot.set_movement(cid, "the-map", "The map", "open", "Mara found it.", sid)
    _threshold(cid, 5)
    ctx, threads, _ = _aged(cid, "2026-05-10")
    assert ctx["stale_after"] == 5 and threads["the-map"]["state"] == aging.STALE


def test_an_unusable_threshold_falls_back(monkeypatch, tmp_path):
    """Zero would call every record stale on the day it was written, and a
    string would raise inside the comparison and empty the ledger section."""
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    (croot / "calendar.json").write_text(json.dumps(
        {"primary": {"provider": "gregorian"}, "stale_after_days": "soon"}), encoding="utf-8")
    assert calendars.stale_after_days(croot) == calendars.STALE_AFTER_DAYS
    (croot / "calendar.json").write_text(json.dumps(
        {"primary": {"provider": "gregorian"}, "stale_after_days": 0}), encoding="utf-8")
    assert calendars.stale_after_days(croot) == calendars.STALE_AFTER_DAYS


def test_a_scene_dated_ahead_of_now_is_not_stale(monkeypatch, tmp_path):
    """A flashforward, or a clock corrected backwards: negative days are not
    staleness, and sorting one to the top of the ledger would be nonsense."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _dated_scene(cid, "Later", "2026-07-01")
    plot.set_movement(cid, "the-map", "The map", "open", "Mara found it.", sid)
    _ctx, threads, _ = _aged(cid, "2026-05-01")
    assert threads["the-map"]["state"] == aging.OK
    assert threads["the-map"]["days_since"] == -61


def test_a_dateless_scene_leaves_the_age_unknown(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Undated")
    plot.set_movement(cid, "the-map", "The map", "open", "Mara found it.", sid)
    _ctx, threads, _ = _aged(cid, "2026-06-15")
    assert threads["the-map"] == {"state": aging.OK, "days_since": None,
                                  "days_over": None, "due_in": None}


def test_the_chronicle_answers_for_a_scene_nobody_dated(monkeypatch, tmp_path):
    """A scene's own history is authoritative and normalized; the chronicle is
    what answers for the scenes the reader never dated by hand, which is most
    of them."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Absorbed")
    chronicle.absorb(cid, {"id": sid, "one_line": "They met.", "date": "2026-05-01"})
    plot.set_movement(cid, "the-map", "The map", "open", "Mara found it.", sid)
    _ctx, threads, _ = _aged(cid, "2026-06-15")
    assert threads["the-map"]["days_since"] == 45


# ---- overdue ---------------------------------------------------------------

def test_a_deadline_behind_the_clock_is_overdue(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _dated_scene(cid, "The oath", "2026-05-01")
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "open",
                             "2026-05-20", "Mara swore to repay it.", sid)
    _ctx, _, owed = _aged(cid, "2026-06-01")
    assert owed["the-debt"]["state"] == aging.OVERDUE
    assert owed["the-debt"]["days_over"] == 12 and owed["the-debt"]["due_in"] is None


def test_a_deadline_ahead_reports_how_far_ahead(monkeypatch, tmp_path):
    """`due_in` is #106's to warn from; it falls out of the same subtraction."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _dated_scene(cid, "The oath", "2026-05-01")
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "open",
                             "2026-05-20", "Mara swore to repay it.", sid)
    _ctx, _, owed = _aged(cid, "2026-05-10")
    assert owed["the-debt"]["state"] == aging.OK and owed["the-debt"]["due_in"] == 10


def test_a_deadline_in_the_fiction_s_own_words_is_not_overdue(monkeypatch, tmp_path):
    """`due` is free text by design. "Before the harvest moon" is a real
    deadline no arithmetic can place, and inventing one would be worse than
    saying nothing."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _dated_scene(cid, "The oath", "2026-05-01")
    commitments.set_movement(cid, "the-moon", "The moon", "promise", "open",
                             "before the harvest moon", "Mara swore it.", sid)
    _ctx, _, owed = _aged(cid, "2026-06-01")
    assert owed["the-moon"]["days_over"] is None and owed["the-moon"]["due_in"] is None
    assert owed["the-moon"]["state"] == aging.STALE   # it can still go quiet


def test_overdue_outranks_stale_and_keeps_both_numbers(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _dated_scene(cid, "The oath", "2026-05-01")
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "open",
                             "2026-05-20", "Mara swore to repay it.", sid)
    _ctx, _, owed = _aged(cid, "2026-07-01")
    assert owed["the-debt"]["state"] == aging.OVERDUE
    assert owed["the-debt"]["days_over"] == 42 and owed["the-debt"]["days_since"] == 61


def test_a_resolved_commitment_is_not_aged_at_all(monkeypatch, tmp_path):
    """Aging reads `open_commitments`, which is the list of what is still owed."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _dated_scene(cid, "The oath", "2026-05-01")
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "fulfilled",
                             "2026-05-20", "Mara repaid it.", sid)
    _ctx, _, owed = _aged(cid, "2026-07-01")
    assert owed == {}


# ---- when there is nothing to measure from ---------------------------------

def test_no_clock_means_no_answer_rather_than_a_wrong_one(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _dated_scene(cid, "The oath", "2026-05-01")
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "open",
                             "2026-05-20", "Mara swore it.", sid)
    _ctx, _, owed = _aged(cid, "")
    assert owed["the-debt"] == {"state": aging.OK, "days_since": None,
                                "days_over": None, "due_in": None}


def test_a_calendar_that_will_not_load_costs_the_badges_only(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = _dated_scene(cid, "The oath", "2026-05-01")
    plot.set_movement(cid, "the-map", "The map", "open", "Mara found it.", sid)
    (campaigns.campaign_root(cid) / "calendar.json").write_text(
        json.dumps({"primary": {"provider": "no-such-calendar"}}), encoding="utf-8")
    _ctx, threads, _ = _aged(cid, "2026-06-15")
    assert threads["the-map"]["state"] == aging.OK


def test_annotate_keeps_the_caller_s_order_and_rows(monkeypatch, tmp_path):
    """Sorting the overdue to the top is the panel's decision, not this one's."""
    cid = _campaign(monkeypatch, tmp_path)
    rows = [{"id": "b", "last_scene": ""}, {"id": "a", "last_scene": ""}]
    aged = aging.annotate(aging.prepare(cid, ""), rows)
    assert [r["id"] for r in aged] == ["b", "a"]
    assert all("aging" in r for r in aged)


def test_summary_counts_what_the_digest_headlines(monkeypatch, tmp_path):
    rows = [{"aging": {"state": aging.OVERDUE}}, {"aging": {"state": aging.STALE}},
            {"aging": {"state": aging.STALE}}, {"aging": {"state": aging.OK}}]
    assert aging.summary(rows) == {"overdue": 1, "stale": 2}


# ---- what the digest does with it ------------------------------------------

def test_a_skip_reports_what_it_will_make_overdue(monkeypatch, tmp_path):
    """The question a reader actually has before confirming a month-long skip
    is about the far side of it, so the digest ages against the target."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = _dated_scene(cid, "The oath", "2026-05-01")
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "open",
                             "2026-05-20", "Mara swore to repay it.", sid)
    clock.advance(cid, to="2026-05-02", reason="the next morning")
    digest = clock.preview(cid, days=60)
    owed = {c["id"]: c["aging"] for c in digest["commitments"]}
    assert owed["the-debt"]["state"] == aging.OVERDUE
    assert digest["aging"] == {"overdue": 1, "stale": 0,
                               "stale_after": calendars.STALE_AFTER_DAYS}
