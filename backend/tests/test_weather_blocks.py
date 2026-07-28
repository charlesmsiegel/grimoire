from grimoire.store.weather.blocks import POSITIONS, block_of, ordinal

DAY = 700_000  # an arbitrary fixed day


def pos(minutes, day=DAY):
    return POSITIONS[block_of(day, minutes)[1]]


def test_block_names_by_minute():
    assert pos(5 * 60) == "dawn"
    assert pos(9 * 60) == "morning"
    assert pos(13 * 60) == "afternoon"
    assert pos(19 * 60) == "evening"
    assert pos(22 * 60) == "night"


def test_after_midnight_belongs_to_the_previous_date_night():
    assert block_of(DAY + 1, 2 * 60) == (DAY, POSITIONS.index("night"))
    assert block_of(DAY, 22 * 60) == (DAY, POSITIONS.index("night"))


def test_late_evening_and_early_morning_share_one_ordinal():
    assert ordinal(DAY, 23 * 60) == ordinal(DAY + 1, 1 * 60)


def test_dawn_boundary_is_not_night():
    assert ordinal(DAY, 3 * 60 + 59) != ordinal(DAY, 4 * 60 + 1)
    assert pos(4 * 60 + 1) == "dawn"


def test_missing_clock_resolves_to_afternoon():
    assert block_of(DAY, None) == (DAY, POSITIONS.index("afternoon"))


def test_consecutive_blocks_differ_by_one_across_a_day_boundary():
    evening = ordinal(DAY, 19 * 60)
    night = ordinal(DAY, 22 * 60)
    dawn_next = ordinal(DAY + 1, 5 * 60)
    assert night - evening == 1
    assert dawn_next - night == 1


def test_ordinals_are_defined_for_negative_days():
    assert ordinal(-3, 9 * 60) == 5 * -3 + POSITIONS.index("morning")
