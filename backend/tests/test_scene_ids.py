from grimoire.store.scene_ids import date_slug_of, format_sid, parse_sid


def test_parse_dated():
    assert parse_sid("007--1023-05-12--the-ambush") == {
        "number": 7, "width": 3, "date_slug": "1023-05-12", "title_slug": "the-ambush"}


def test_parse_undated():
    assert parse_sid("007--the-ambush") == {
        "number": 7, "width": 3, "date_slug": None, "title_slug": "the-ambush"}


def test_parse_uniquify_suffix_stays_in_title():
    assert parse_sid("007--the-ambush-2")["title_slug"] == "the-ambush-2"


def test_parse_rejects_legacy_and_garbage():
    assert parse_sid("2026-06-28-the-ambush") is None  # legacy real-date id
    assert parse_sid("nope") is None
    assert parse_sid("--x") is None            # empty number
    assert parse_sid("7--") is None            # empty title
    assert parse_sid("a--b--c--d") is None     # too many sections
    assert parse_sid("x7--slug") is None       # non-numeric number


def test_format_round_trips():
    assert format_sid(7, 3, None, "the-ambush") == "007--the-ambush"
    assert format_sid(7, 3, "1023-05-12", "the-ambush") == "007--1023-05-12--the-ambush"
    assert format_sid(1000, 4, None, "x") == "1000--x"
    for sid in ("007--the-ambush", "0042--1023-05-12--x"):
        p = parse_sid(sid)
        assert format_sid(p["number"], p["width"], p["date_slug"], p["title_slug"]) == sid


def test_date_slug_of_strips_time_and_slugifies():
    assert date_slug_of("2026-07-04T09:00") == "2026-07-04"
    assert date_slug_of("2026-07-04") == "2026-07-04"
    assert date_slug_of("12 Frostfall 892") == "12-frostfall-892"  # fantasy calendars
