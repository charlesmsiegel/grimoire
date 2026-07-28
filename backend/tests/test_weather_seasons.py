from grimoire.store.calendars import get_provider
from grimoire.store.weather.seasons import season_for, year_fraction, year_length

GREG = {"provider": "gregorian", "region": "US", "custom_holidays": [], "anchor": None}


def climate(seasons):
    return {"id": "c", "name": "C", "persistence": 0.5, "seasons": seasons}


def s(name, frm, to):
    return {"name": name, "from": frm, "to": to,
            "temperature": [{"name": "mild", "weight": 1}],
            "conditions": [{"name": "clear", "weight": 1}],
            "wind": [{"name": "calm", "weight": 1}]}


def test_year_length_gregorian_common_and_leap():
    p = get_provider(GREG)
    assert year_length(p, 2026) == 365
    assert year_length(p, 2024) == 366


def test_year_fraction_is_zero_on_the_first_day():
    p = get_provider(GREG)
    assert year_fraction(p, p.parse("2026-01-01")) == 0.0


def test_year_fraction_is_monotonic_across_the_year():
    p = get_provider(GREG)
    jan, jul, dec = (p.parse(d) for d in ("2026-01-01", "2026-07-01", "2026-12-31"))
    assert 0.0 == year_fraction(p, jan) < year_fraction(p, jul) < year_fraction(p, dec) < 1.0


def test_single_full_year_season_matches_everywhere():
    c = climate([s("all year", 0.0, 0.0)])
    assert season_for(c, 0.0)["name"] == "all year"
    assert season_for(c, 0.99)["name"] == "all year"


def test_two_seasons_split_the_year():
    c = climate([s("wet", 0.0, 0.5), s("dry", 0.5, 0.0)])
    assert season_for(c, 0.1)["name"] == "wet"
    assert season_for(c, 0.5)["name"] == "dry"
    assert season_for(c, 0.99)["name"] == "dry"


def test_wrapping_season_covers_both_ends_of_the_year():
    c = climate([s("winter", 0.92, 0.21), s("rest", 0.21, 0.92)])
    assert season_for(c, 0.95)["name"] == "winter"
    assert season_for(c, 0.01)["name"] == "winter"
    assert season_for(c, 0.5)["name"] == "rest"


def test_season_boundaries_are_half_open():
    c = climate([s("wet", 0.0, 0.5), s("dry", 0.5, 0.0)])
    assert season_for(c, 0.4999)["name"] == "wet"
    assert season_for(c, 0.5)["name"] == "dry"
