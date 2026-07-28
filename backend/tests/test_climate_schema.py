import warnings

import pytest

from grimoire.store.climates.schema import ClimateError, validate


def climate(**over):
    doc = {
        "id": "temperate-coastal", "name": "Temperate Coastal", "persistence": 0.35,
        "seasons": [{
            "name": "winter", "from": 0.0, "to": 0.0,
            "temperature": [{"name": "cold", "weight": 6}, {"name": "mild", "weight": 2}],
            "conditions": [{"name": "clear", "weight": 2},
                           {"name": "snow", "weight": 2, "requires_temp": ["cold"]}],
            "wind": [{"name": "calm", "weight": 1}],
        }],
    }
    doc.update(over)
    return doc


def season(**over):
    s = climate()["seasons"][0]
    s.update(over)
    return s


def test_valid_document_passes():
    assert validate(climate())["id"] == "temperate-coastal"


def test_empty_entry_name_rejected():
    with pytest.raises(ClimateError, match="winter"):
        validate(climate(seasons=[season(wind=[{"name": "  ", "weight": 1}])]))


def test_duplicate_entry_names_rejected():
    with pytest.raises(ClimateError, match="duplicate"):
        validate(climate(seasons=[season(
            wind=[{"name": "calm", "weight": 1}, {"name": "calm", "weight": 2}])]))


def test_negative_weight_rejected_even_with_positive_sibling():
    with pytest.raises(ClimateError, match="weight"):
        validate(climate(seasons=[season(
            wind=[{"name": "calm", "weight": 1}, {"name": "gale", "weight": -1}])]))


def test_all_zero_axis_rejected():
    with pytest.raises(ClimateError, match="positive"):
        validate(climate(seasons=[season(wind=[{"name": "calm", "weight": 0}])]))


def test_non_finite_axis_total_rejected():
    with pytest.raises(ClimateError, match="finite"):
        validate(climate(seasons=[season(
            wind=[{"name": "a", "weight": 1e308}, {"name": "b", "weight": 1e308}])]))


def test_season_without_positive_unconstrained_condition_rejected():
    with pytest.raises(ClimateError, match="unconstrained"):
        validate(climate(seasons=[season(
            conditions=[{"name": "clear", "weight": 0},
                        {"name": "snow", "weight": 2, "requires_temp": ["cold"]}])]))


def test_dangling_requires_temp_rejected():
    with pytest.raises(ClimateError, match="requires_temp"):
        validate(climate(seasons=[season(
            conditions=[{"name": "clear", "weight": 1},
                        {"name": "snow", "weight": 2, "requires_temp": ["freezng"]}])]))


def test_requires_temp_naming_only_zero_weight_band_rejected():
    with pytest.raises(ClimateError, match="requires_temp"):
        validate(climate(seasons=[season(
            temperature=[{"name": "cold", "weight": 0}, {"name": "mild", "weight": 3}],
            conditions=[{"name": "clear", "weight": 1},
                        {"name": "snow", "weight": 2, "requires_temp": ["cold"]}])]))


def test_non_string_requires_temp_element_rejected():
    with pytest.raises(ClimateError, match="requires_temp"):
        validate(climate(seasons=[season(
            conditions=[{"name": "clear", "weight": 1},
                        {"name": "snow", "weight": 2, "requires_temp": [{}]}])]))


def test_empty_requires_temp_rejected():
    with pytest.raises(ClimateError, match="requires_temp"):
        validate(climate(seasons=[season(
            conditions=[{"name": "clear", "weight": 1},
                        {"name": "snow", "weight": 2, "requires_temp": []}])]))


def test_persistence_one_is_accepted():
    with pytest.warns(UserWarning, match="clamped"):
        assert validate(climate(persistence=1))["persistence"] == 1


def test_persistence_out_of_range_rejected():
    with pytest.raises(ClimateError, match="persistence"):
        validate(climate(persistence=2))


def test_year_gap_rejected():
    with pytest.raises(ClimateError, match="cover"):
        validate(climate(seasons=[season(**{"from": 0.0, "to": 0.5})]))


def test_two_seasons_covering_the_year_pass():
    a = season(name="wet", **{"from": 0.0, "to": 0.5})
    b = season(name="dry", **{"from": 0.5, "to": 0.0})
    assert len(validate(climate(seasons=[a, b]))["seasons"]) == 2


def test_overlapping_seasons_that_still_cover_the_year_pass():
    # Overlaps are legal — the spec resolves them by array order. Only *gaps*
    # are an error, so exact tiling must not be required.
    a = season(name="long", **{"from": 0.0, "to": 0.6})
    b = season(name="late", **{"from": 0.5, "to": 0.0})
    assert len(validate(climate(seasons=[a, b]))["seasons"]) == 2


def test_non_object_document_rejected():
    with pytest.raises(ClimateError, match="object"):
        validate([{"id": "x"}])


def test_non_object_season_rejected():
    with pytest.raises(ClimateError, match="object"):
        validate(climate(seasons=[None]))


def test_non_object_table_entry_rejected():
    with pytest.raises(ClimateError, match="object"):
        validate(climate(seasons=[season(wind=["calm"])]))


def test_climate_without_a_name_rejected():
    doc = climate()
    del doc["name"]
    with pytest.raises(ClimateError, match="name"):
        validate(doc)


def test_season_without_a_name_rejected():
    s = season()
    del s["name"]
    with pytest.raises(ClimateError, match="name"):
        validate(climate(seasons=[s]))


def test_id_with_a_trailing_newline_rejected():
    # `$` matches before a final newline, so a `match`-based check would pass.
    with pytest.raises(ClimateError, match="id"):
        validate(climate(id="saltmarch\n"))


def test_persistence_one_warns_about_the_clamp():
    with pytest.warns(UserWarning, match="clamped"):
        validate(climate(persistence=1))


def test_ordinary_persistence_does_not_warn(recwarn):
    validate(climate(persistence=0.35))
    assert len(recwarn) == 0


def test_climate_id_with_slash_rejected():
    with pytest.raises(ClimateError, match="id"):
        validate(climate(id="a/b"))


def test_dot_only_climate_id_rejected():
    with pytest.raises(ClimateError, match="id"):
        validate(climate(id=".."))


def test_a_bignum_weight_is_a_validation_error_not_an_overflow():
    # 10**1000 is a valid Python int that cannot become a float, so a bare
    # math.isfinite raises OverflowError. At the save boundary that turns
    # malformed user input into an internal error instead of a 400.
    doc = climate()
    doc["seasons"][0]["wind"][0]["weight"] = 10 ** 1000
    with pytest.raises(ClimateError, match="finite number"):
        validate(doc)


def test_a_bignum_persistence_is_a_validation_error_not_an_overflow():
    doc = climate()
    doc["persistence"] = 10 ** 1000
    with pytest.raises(ClimateError, match="finite number"):
        validate(doc)


def test_the_clamp_warning_does_not_become_a_validation_failure():
    # Under `-W error` a bare warnings.warn raises, the registry's broad
    # handler catches it, and a valid climate silently disappears from the
    # merged list because of how the process happened to be started.
    doc = climate()
    doc["persistence"] = 1
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert validate(doc) is doc
