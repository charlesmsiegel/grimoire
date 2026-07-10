import pytest

from grimoire.store import dice


def test_parse_basic():
    assert dice.parse("2d6") == {"count": 2, "sides": 6, "keep": None, "explode": False,
                                 "modifier": 0, "pool": None, "vs": None}


def test_parse_count_defaults_to_one():
    assert dice.parse("d20")["count"] == 1


def test_parse_case_and_spaces():
    spec = dice.parse("  4D6 KH3 ! +2 VS 15 ")
    assert spec == {"count": 4, "sides": 6, "keep": ("kh", 3), "explode": True,
                    "modifier": 2, "pool": None, "vs": 15}


def test_parse_negative_modifier():
    assert dice.parse("2d8-3")["modifier"] == -3


def test_parse_pool():
    spec = dice.parse("7d10t6")
    assert spec["pool"] == 6 and spec["vs"] is None and spec["modifier"] == 0


def test_parse_drop_lowest():
    assert dice.parse("4d6dl1")["keep"] == ("dl", 1)


@pytest.mark.parametrize("bad", [
    "", "garbage", "2x6", "0d6", "101d6", "2d1", "2d1001",
    "4d6kh5",      # keep more than rolled
    "4d6dh4",      # drop every die
    "4d6kh0",      # zero keep
    "3d10t7+2",    # pool with modifier
])
def test_parse_rejects(bad):
    with pytest.raises(dice.DiceError):
        dice.parse(bad)
