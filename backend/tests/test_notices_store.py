"""Warn-once pre-notices (#106): the store half.

Two halves of one feature, and they fail differently. *What is imminent* is
recomputed every call, so its bugs are window bugs — an event a day too far out,
a holiday counted on the day it lands. *Whether the reader has been warned* is
the only state, so its bugs are memory bugs — a dismissal that does not stick,
or one that sticks past the occurrence it was about and swallows next year's.
Both are exercised here; the HTTP surface is `test_notices_routes.py`.
"""

from __future__ import annotations

import json

import pytest

from grimoire.store import calendars, campaigns, events, notices, scene_ids, worlds

# The campaign's present, and the days around it, in the primary calendar's own
# notation. A fixed literal rather than "today": every assertion below is
# arithmetic on a window, and a test whose answers move with the wall clock
# would pass on most days and fail near a real holiday.
NOW = "2026-05-10"


def _campaign(monkeypatch, tmp_path, warn=None, holidays=(), region=""):
    """A campaign whose calendar observes exactly `holidays` and nothing else.

    `region=""` switches the holiday library off, so the window under test holds
    what this test put in it — otherwise the answers depend on which country's
    observances happen to fall near NOW, and on the library's version.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid)
    cfg = calendars.read_calendar(campaigns.campaign_root(cid))
    cfg["primary"] = {**cfg["primary"], "region": region, "custom_holidays": list(holidays)}
    if warn is not None:
        cfg["warn_days"] = warn
    calendars.write_calendar(campaigns.campaign_root(cid), cfg)
    return cid


def _root(cid):
    return campaigns.campaign_root(cid)


def _pending(cid, native=NOW, **kw):
    return notices.pending(cid, _root(cid), native, **kw)


def _names(rows):
    return [r["name"] for r in rows]


def _rule(name, month, day):
    return {"name": name, "month": month, "day": day}


def _mark_many(cid, keys):
    """Acknowledge `keys` in requests no larger than one is allowed to be.

    `BATCH_LIMIT` bounds a single request, not a ledger — so a test about the
    ROW cap has to fill it the way a reader would, one dismissal at a time.
    """
    for i in range(0, len(keys), notices.BATCH_LIMIT):
        notices.mark(cid, keys[i:i + notices.BATCH_LIMIT])


# ---- the window: what counts as imminent ----------------------------------

def test_a_fresh_campaign_has_nothing_to_warn_about(monkeypatch, tmp_path):
    assert _pending(_campaign(monkeypatch, tmp_path)) == []


def test_a_holiday_inside_the_window_is_a_notice(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    rows = _pending(cid)
    assert _names(rows) == ["Saltmarch Eve"]
    assert rows[0]["kind"] == "holiday"
    assert rows[0]["in_days"] == 3
    assert rows[0]["friendly"]


def test_a_holiday_past_the_window_is_not(monkeypatch, tmp_path):
    """`warn_days` is the warn window, NOT `UPCOMING_WINDOW_DAYS`. A month of
    lead time is what the prompt's "Upcoming:" line reads; a banner that fires
    a month out is noise by the day it is about."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "06", 1)])
    assert _pending(cid) == []
    assert calendars.upcoming_holidays(
        calendars.read_calendar(_root(cid)), NOW)[0]["name"] == "Saltmarch Eve"


def test_the_last_day_of_the_window_still_warns(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, warn=7, holidays=[_rule("Saltmarch Eve", "05", 17)])
    assert [r["in_days"] for r in _pending(cid)] == [7]


def test_the_day_after_the_window_does_not(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, warn=7, holidays=[_rule("Saltmarch Eve", "05", 18)])
    assert _pending(cid) == []


def test_today_is_not_a_pre_notice(monkeypatch, tmp_path):
    """A thing happening today has no lead time left to warn about, and the
    scene panel already says what today's observances are."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 10)])
    assert _pending(cid) == []
    assert "Saltmarch Eve" in calendars.today_facts(
        calendars.read_calendar(_root(cid)), NOW)["holidays_today"]


def test_a_scheduled_event_inside_the_window_is_a_notice(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    events.create(cid, "The envoy arrives", "2026-05-12")
    rows = _pending(cid)
    assert _names(rows) == ["The envoy arrives"]
    assert rows[0]["kind"] == "event"
    assert rows[0]["in_days"] == 2


def test_a_fired_event_no_longer_warns(monkeypatch, tmp_path):
    """The clock reaching an event is what stops it being upcoming — and an
    event that already happened must not be warned about from a scene set
    before it."""
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "The envoy arrives", "2026-05-12")
    events.fire(cid, [eid], "2026-05-12")
    assert _pending(cid) == []


def test_events_and_holidays_share_one_list_soonest_first(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 14)])
    events.create(cid, "The envoy arrives", "2026-05-12")
    assert _names(_pending(cid)) == ["The envoy arrives", "Saltmarch Eve"]


def test_a_day_carrying_both_leads_with_the_authored_event(monkeypatch, tmp_path):
    """The reader wrote one of them down; that is the one a scene is likelier
    to be planned around."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 12)])
    events.create(cid, "The envoy arrives", "2026-05-12")
    assert [r["kind"] for r in _pending(cid)] == ["event", "holiday"]


def test_warn_days_of_zero_switches_the_warnings_off(monkeypatch, tmp_path):
    """Zero is a setting somebody can mean here, unlike a staleness threshold
    of zero — which is why the config's no-opinion value is None, not 0."""
    cid = _campaign(monkeypatch, tmp_path, warn=0, holidays=[_rule("Saltmarch Eve", "05", 11)])
    assert _pending(cid) == []


def test_an_explicit_window_overrides_the_configured_one(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, warn=2, holidays=[_rule("Saltmarch Eve", "05", 20)])
    assert _pending(cid) == []
    assert _names(_pending(cid, window=30)) == ["Saltmarch Eve"]


# ---- the memory: warn once, and only about this occurrence -----------------

def test_marking_silences_a_notice(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    key = _pending(cid)[0]["key"]
    assert notices.mark(cid, [key], scene="scene-1") == [key]
    assert _pending(cid) == []


def test_once_ness_is_campaign_wide_not_per_scene(monkeypatch, tmp_path):
    """The whole point of the ledger. Per-scene dismissals (the cast
    suggestions' shape) would put the banner back in every new scene, which is
    exactly the nag this feature exists to avoid."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    notices.mark(cid, [_pending(cid)[0]["key"]], scene="scene-1")
    # A second scene, a day later, still inside the window: same occurrence.
    assert _pending(cid, "2026-05-11") == []


def test_next_year_warns_again(monkeypatch, tmp_path):
    """The key names an occurrence, not an event — same name, different day."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    notices.mark(cid, [_pending(cid)[0]["key"]])
    assert _names(_pending(cid, "2027-05-10")) == ["Saltmarch Eve"]


def test_re_dating_an_event_warns_again(monkeypatch, tmp_path):
    """Same reason: the dismissal was about a day, and the day changed."""
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "The envoy arrives", "2026-05-12")
    notices.mark(cid, [_pending(cid)[0]["key"]])
    assert _pending(cid) == []
    events.update(cid, eid, date="2026-05-14")
    assert _names(_pending(cid)) == ["The envoy arrives"]


def test_marking_twice_keeps_the_first_stamp(monkeypatch, tmp_path):
    """The stamp says when the reader was first warned. Two surfaces showing
    one notice must not make the second dismissal rewrite that."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    key = _pending(cid)[0]["key"]
    notices.mark(cid, [key], scene="scene-1")
    first = notices.read(cid)[key]
    assert notices.mark(cid, [key], scene="scene-2") == []
    assert notices.read(cid)[key] == first


def test_forget_puts_a_notice_back(monkeypatch, tmp_path):
    """`mark` will not overwrite a stamp, so without this one misclick silences
    an occurrence until its day has gone by."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    key = _pending(cid)[0]["key"]
    notices.mark(cid, [key])
    assert notices.forget(cid, [key]) == [key]
    assert _names(_pending(cid)) == ["Saltmarch Eve"]
    assert notices.forget(cid, [key]) == []


def test_marking_nothing_writes_nothing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert notices.mark(cid, []) == []
    assert notices.mark(cid, ["   "]) == []
    assert not notices._path(cid).exists()


# ---- the file, which a reader may have edited ------------------------------

def test_a_garbled_ledger_reads_as_no_dismissals(monkeypatch, tmp_path):
    """A reader loses its dismissals — the banner comes back — never the panel
    around it. `events.read` and `plot.read` draw the same line."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    notices.mark(cid, [_pending(cid)[0]["key"]])
    notices._path(cid).write_text("{not json", encoding="utf-8")
    assert notices.read(cid) == {}
    assert _names(_pending(cid)) == ["Saltmarch Eve"]


def test_a_ledger_of_the_wrong_shape_reads_as_no_dismissals(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    notices._path(cid).write_text("[]", encoding="utf-8")
    assert notices.read(cid) == {}


def test_a_hand_written_row_still_silences(monkeypatch, tmp_path):
    """The ledger is a set of strings and knows nothing about events, which is
    what lets another domain's warnings dismiss into the same keyspace."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    key = _pending(cid)[0]["key"]
    notices._path(cid).write_text(json.dumps({key: {}}), encoding="utf-8")
    assert _pending(cid) == []


def test_the_ledger_is_capped(monkeypatch, tmp_path):
    """`POST .../notices` is public and the ledger only ever grows, so the file
    needs a ceiling. Oldest acknowledgement goes first."""
    cid = _campaign(monkeypatch, tmp_path)
    _mark_many(cid, [f"holiday:{n}:Old" for n in range(notices.LEDGER_LIMIT)])
    notices.mark(cid, ["holiday:999999:New"])
    data = notices.read(cid)
    assert len(data) == notices.LEDGER_LIMIT
    assert "holiday:999999:New" in data
    assert "holiday:0:Old" not in data


def test_eviction_can_re_warn_a_historical_scene(monkeypatch, tmp_path):
    """The cost the cap accepts, pinned so nobody re-derives the safety claim it
    used to carry. Eviction would be free if `pending` only looked forward from
    the campaign clock; it also answers from a SCENE's own moment, and a
    flashback dated before an evicted occurrence warns about it again. The
    defence is the size of the cap, not the order of eviction — see
    `LEDGER_LIMIT`."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    key = _pending(cid)[0]["key"]
    notices.mark(cid, [key])
    assert _pending(cid) == []
    _mark_many(cid, [f"holiday:{900000 + n}:Later" for n in range(notices.LEDGER_LIMIT)])
    assert key not in notices.read(cid)
    assert _names(_pending(cid)) == ["Saltmarch Eve"]


def test_a_calendar_that_will_not_load_warns_about_nothing(monkeypatch, tmp_path):
    """Every caller is a panel: it must degrade to showing nothing rather than
    failing the page around it."""
    cid = _campaign(monkeypatch, tmp_path)
    root = campaigns.campaign_root(cid)
    (root / "calendar.json").write_text(
        json.dumps({"primary": {"provider": "no-such-calendar"}}), encoding="utf-8")
    assert notices.pending(cid, root, NOW) == []


def test_a_moment_this_calendar_cannot_read_warns_about_nothing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    assert _pending(cid, "the night of the long knives") == []
    assert _pending(cid, "") == []


# ---- the warn window, as a hand-editable setting ---------------------------

def _write_raw_calendar(cid, **fields):
    (_root(cid) / "calendar.json").write_text(
        json.dumps({"primary": {"provider": "gregorian"}, **fields}), encoding="utf-8")


def test_a_missing_warn_days_is_the_default(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    _write_raw_calendar(cid)
    assert calendars.warn_days(_root(cid)) == calendars.WARN_DAYS


def test_an_unusable_warn_days_is_the_default(monkeypatch, tmp_path):
    """calendar.json is hand-editable and this number reaches fixed-day
    arithmetic — `"soon"` must not raise inside a panel read."""
    cid = _campaign(monkeypatch, tmp_path)
    for bad in ("soon", None, True, -3, {"days": 7}):
        _write_raw_calendar(cid, warn_days=bad)
        assert calendars.warn_days(_root(cid)) == calendars.WARN_DAYS


def test_a_warn_days_of_zero_is_kept(monkeypatch, tmp_path):
    """Unlike a staleness threshold of zero, this one is a setting somebody
    means: no warnings in this campaign."""
    cid = _campaign(monkeypatch, tmp_path)
    _write_raw_calendar(cid, warn_days=0)
    assert calendars.warn_days(_root(cid)) == 0


def test_an_absurd_warn_days_is_capped(monkeypatch, tmp_path):
    """Every day of the window is a day the calendar provider — possibly a
    user-authored plugin — is asked to enumerate."""
    cid = _campaign(monkeypatch, tmp_path)
    _write_raw_calendar(cid, warn_days=100000)
    assert calendars.warn_days(_root(cid)) == calendars.MAX_WARN_DAYS


def test_a_corrupt_calendar_answers_the_default_window(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (_root(cid) / "calendar.json").write_text("{not json", encoding="utf-8")
    assert calendars.warn_days(_root(cid)) == calendars.WARN_DAYS


# ---- keys stay bounded, and bounded means the SAME key on both sides -------

def test_a_long_holiday_name_still_dismisses(monkeypatch, tmp_path):
    """A holiday name has no length limit anywhere — `validate_rule` checks only
    that one is present. The key generated from it must still be one `mark` will
    store, or the dismissal reports success and the banner comes straight back.
    """
    cid = _campaign(monkeypatch, tmp_path,
                    holidays=[_rule("Saltmarch " * 40, "05", 13)])
    key = _pending(cid)[0]["key"]
    assert len(key) <= notices.KEY_LIMIT
    assert notices.mark(cid, [key]) == [key]
    assert _pending(cid) == []


def test_two_long_names_sharing_a_prefix_do_not_share_a_dismissal(monkeypatch, tmp_path):
    """Bounding by truncation alone would give these one key, so dismissing the
    first would silence the second without the reader ever seeing it."""
    prefix = "The Feast of " + "x" * 200
    cid = _campaign(monkeypatch, tmp_path,
                    holidays=[_rule(prefix + " Saltmarch", "05", 13),
                              _rule(prefix + " Winifred", "05", 13)])
    keys = [r["key"] for r in _pending(cid)]
    assert len(set(keys)) == 2
    notices.mark(cid, [keys[0]])
    assert [r["key"] for r in _pending(cid)] == [keys[1]]


def test_an_overlong_key_is_refused_not_truncated(monkeypatch, tmp_path):
    """Every key this app generates is bounded, so an overlong one is crafted —
    and storing a shortened version of it would record an acknowledgement of
    something no `pending` can ever match."""
    cid = _campaign(monkeypatch, tmp_path)
    assert notices.mark(cid, ["holiday:1:" + "x" * 5000]) == []
    assert notices.read(cid) == {}


def test_the_dismissing_scene_is_bounded(monkeypatch, tmp_path):
    """It arrives in a public request body and is written into every new row:
    capping the key and the row count bounds nothing if this one is free."""
    cid = _campaign(monkeypatch, tmp_path)
    notices.mark(cid, ["holiday:1:Saltmarch Eve"], scene="S" * 5000)
    assert len(notices.read(cid)["holiday:1:Saltmarch Eve"]["scene"]) == notices.SCENE_LIMIT


def test_the_scene_cap_is_not_below_the_longest_id_this_app_mints(monkeypatch, tmp_path):
    """Truncation is right for this field — nothing compares it — but only above
    `MAX_SID`. Below it, an id the app actually mints is stored short, and
    `repoint_scenes` then cannot repair the row when that scene is renamed: its
    mapping is keyed by the full old id, so the row is left naming a scene that
    no longer exists."""
    cid = _campaign(monkeypatch, tmp_path)
    sid = "0001--" + "a" * (scene_ids.MAX_SID - len("0001--"))
    notices.mark(cid, ["holiday:1:Saltmarch Eve"], scene=sid)
    assert notices.read(cid)["holiday:1:Saltmarch Eve"]["scene"] == sid
    notices.repoint_scenes(cid, {sid: "0002--after"})
    assert notices.read(cid)["holiday:1:Saltmarch Eve"]["scene"] == "0002--after"


def test_a_non_string_holiday_name_does_not_break_the_scan(monkeypatch, tmp_path):
    """`validate_rule` accepts any TRUTHY name, so a hand-written list reaches
    the dedup below — where an unhashable name would raise `TypeError` past
    every caller's `except CalendarError`, 500-ing the scene datetime route and
    failing prompt assembly. `today_facts` goes through the same helper."""
    cid = _campaign(monkeypatch, tmp_path)
    root = _root(cid)
    cfg = calendars.read_calendar(root)
    cfg["primary"] = {**cfg["primary"], "region": "",
                      "custom_holidays": [{"name": ["Saltmarch", "Eve"],
                                           "month": "05", "day": 13}]}
    calendars.write_calendar(root, cfg)
    assert calendars.today_facts(calendars.read_calendar(root), NOW)["upcoming"] is not None
    assert len(_pending(cid)) == 1


# ---- the writer refuses what the reader tolerates --------------------------

def test_a_writer_refuses_the_ledger_a_reader_tolerates(monkeypatch, tmp_path):
    """The asymmetry is the point. A mutator inheriting `read`'s tolerance would
    answer a corrupt file with `{}` and publish that over it — turning something
    a reader could still repair by hand into one acknowledgement, permanently.
    `events._mutable` draws the same line."""
    cid = _campaign(monkeypatch, tmp_path)
    notices._path(cid).write_text('{"holiday:1:Old": {} TRAILING', encoding="utf-8")
    with pytest.raises(notices.NoticeError):
        notices.mark(cid, ["holiday:2:New"])
    with pytest.raises(notices.NoticeError):
        notices.forget(cid, ["holiday:1:Old"])
    assert notices._path(cid).read_text(encoding="utf-8") == '{"holiday:1:Old": {} TRAILING'
    assert notices.read(cid) == {}          # the reader still degrades to silence


def test_a_ledger_of_the_wrong_shape_is_refused_by_a_writer(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    notices._path(cid).write_text("[]", encoding="utf-8")
    with pytest.raises(notices.NoticeError):
        notices.mark(cid, ["holiday:1:x"])


# ---- the dismissing scene follows its scene ------------------------------

def test_a_renamed_scene_is_followed(monkeypatch, tmp_path):
    """Nothing reads `scene` back to decide anything, but it is the only thing
    saying WHERE a dismissal happened — and a scene id goes stale on a title
    rename, the first date stamp, or a width re-pad."""
    cid = _campaign(monkeypatch, tmp_path)
    notices.mark(cid, ["holiday:1:Saltmarch Eve"], scene="001--s")
    notices.repoint_scenes(cid, {"001--s": "001--2026-05-13--s"})
    assert notices.read(cid)["holiday:1:Saltmarch Eve"]["scene"] == "001--2026-05-13--s"


def test_repointing_steps_over_a_ledger_it_cannot_read(monkeypatch, tmp_path):
    """This runs AFTER the scene file has been renamed, so raising would 500 the
    rename and leave every store later in the sweep pointing at an id that is
    already gone."""
    cid = _campaign(monkeypatch, tmp_path)
    notices._path(cid).write_text("{not json", encoding="utf-8")
    notices.repoint_scenes(cid, {"001--s": "002--s"})   # must not raise
    assert notices._path(cid).read_text(encoding="utf-8") == "{not json"


def test_an_absurd_warn_days_literal_does_not_break_the_read(monkeypatch, tmp_path):
    """`1e999` is legal JSON that Python parses as `float("inf")`, which `int()`
    refuses. Uncaught that raises out of every calendar read — the settings
    route, the scene panel, the notice path — instead of falling back."""
    cid = _campaign(monkeypatch, tmp_path)
    (_root(cid) / "calendar.json").write_text(
        '{"primary": {"provider": "gregorian"}, "warn_days": 1e999, '
        '"stale_after_days": 1e999}', encoding="utf-8")
    assert calendars.warn_days(_root(cid)) == calendars.WARN_DAYS
    assert calendars.stale_after_days(_root(cid)) == calendars.STALE_AFTER_DAYS


def test_forget_event_clears_every_day_it_was_warned_about(monkeypatch, tmp_path):
    """An event dismissed, re-dated and dismissed again holds an
    acknowledgement under EACH day. Clearing only the latest leaves the earlier
    key to suppress a recreation dated back to it."""
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "The envoy arrives", "2026-05-12")
    notices.mark(cid, [_pending(cid)[0]["key"]])
    events.update(cid, eid, date="2026-05-14")
    notices.mark(cid, [_pending(cid)[0]["key"]])
    assert len(notices.read(cid)) == 2
    assert len(notices.forget_event(cid, eid)) == 2
    assert notices.read(cid) == {}


def test_forget_event_leaves_other_events_alone(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    eid = events.create(cid, "The envoy arrives", "2026-05-12")
    other = events.create(cid, "Winifred returns", "2026-05-13")
    notices.mark(cid, [r["key"] for r in _pending(cid)])
    assert len(notices.forget_event(cid, eid)) == 1
    assert [k.rsplit(":", 1)[-1] for k in notices.read(cid)] == [other]


def test_forget_event_steps_over_a_ledger_it_cannot_read(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    notices._path(cid).write_text("{not json", encoding="utf-8")
    assert notices.forget_event(cid, "the-envoy-arrives") == []


def test_a_name_with_edge_whitespace_still_dismisses(monkeypatch, tmp_path):
    """`mark` strips the keys it is given — a key is opaque, but a blank one is
    not a key — so a name carrying edge whitespace generated a key that no
    longer equalled the stored one: the dismissal reported success and the
    banner came back on the next read. `validate_rule` accepts such a name and
    calendar.json is hand-written, so it is reachable."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve ", "05", 13)])
    key = _pending(cid)[0]["key"]
    assert key == key.strip()
    assert notices.mark(cid, [key]) == [key]
    assert _pending(cid) == []


def test_two_names_differing_only_in_whitespace_get_one_row_each(monkeypatch, tmp_path):
    """The strip that makes a dismissal stick (above) is also what lets two
    observances the calendar keeps distinct collide into one key — and two rows
    sharing a key means dismissing either silences both, so the reader
    acknowledges a warning they were never shown."""
    cid = _campaign(monkeypatch, tmp_path,
                    holidays=[_rule("Saltmarch Eve", "05", 13),
                              _rule("Saltmarch Eve ", "05", 13)])
    rows = _pending(cid)
    assert len(rows) == 1
    notices.mark(cid, [rows[0]["key"]])
    assert _pending(cid) == []


def test_one_request_cannot_restore_unboundedly_many(monkeypatch, tmp_path):
    """The undo route is public and takes a list too. Unbounded it costs a
    membership test per key while holding the lock every other mutator in the
    campaign is waiting on — the cost `mark`'s cap already refuses to pay."""
    cid = _campaign(monkeypatch, tmp_path)
    keys = [f"holiday:{n}:x" for n in range(notices.BATCH_LIMIT * 3)]
    _mark_many(cid, keys)
    assert len(notices.forget(cid, keys)) == notices.BATCH_LIMIT
    # And a key no `mark` would ever have written is not looked up at all.
    assert notices.forget(cid, ["x" * (notices.KEY_LIMIT + 1)]) == []


def test_one_request_cannot_dismiss_unboundedly_many(monkeypatch, tmp_path):
    """`mark` builds the whole updated ledger before `_trim` cuts it back, so
    the row cap alone bounds the FILE and not the work: a crafted batch would
    still cost the memory and the sort."""
    cid = _campaign(monkeypatch, tmp_path)
    done = notices.mark(cid, [f"holiday:{n}:x" for n in range(notices.BATCH_LIMIT * 20)])
    assert len(done) == notices.BATCH_LIMIT
    assert len(notices.read(cid)) == notices.BATCH_LIMIT


def test_a_broken_secondary_calendar_still_reports_events(monkeypatch, tmp_path):
    """`upcoming_holidays` builds EVERY configured calendar, so an unknown
    secondary raises even where the primary is fine. Folded in with the events
    it would cost the campaign its scheduled ones too — and those are its own
    authored rows, nothing to do with the calendar that broke."""
    cid = _campaign(monkeypatch, tmp_path)
    (_root(cid) / "calendar.json").write_text(json.dumps({
        "primary": {"provider": "gregorian", "region": "", "custom_holidays": [],
                    "anchor": None},
        "secondary": {"provider": "no-such-calendar"}}), encoding="utf-8")
    events.create(cid, "The envoy arrives", "2026-05-12")
    assert _names(_pending(cid)) == ["The envoy arrives"]


def test_forget_removes_a_row_whose_value_is_null(monkeypatch, tmp_path):
    """`pending` filters on the KEY, so a hand-edited `null` row silences a
    notice — but reading the value as the existence sentinel made the undo
    report nothing forgotten and never rewrite the file, leaving the reader no
    way back at all."""
    cid = _campaign(monkeypatch, tmp_path, holidays=[_rule("Saltmarch Eve", "05", 13)])
    key = _pending(cid)[0]["key"]
    notices._path(cid).write_text(json.dumps({key: None}), encoding="utf-8")
    assert _pending(cid) == []                      # the null row does silence it
    assert notices.forget(cid, [key]) == [key]      # and the undo reaches it
    assert _names(_pending(cid)) == ["Saltmarch Eve"]
