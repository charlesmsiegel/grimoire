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


def test_roll_is_reproducible_from_seed():
    a = dice.roll("4d6kh3+2 vs 15", seed=42)
    b = dice.roll("4d6kh3+2 vs 15", seed=42)
    assert a == b
    assert a["seed"] == 42


def test_roll_generates_seed_when_absent():
    r = dice.roll("2d6")
    assert isinstance(r["seed"], int)
    assert dice.roll("2d6", seed=r["seed"]) == r


def test_roll_values_in_range_and_total():
    r = dice.roll("10d6", seed=7)
    assert len(r["dice"]) == 10
    assert all(1 <= d["value"] <= 6 for d in r["dice"])
    assert r["total"] == sum(d["value"] for d in r["dice"])
    assert r["successes"] is None and r["outcome"] is None


def test_roll_keep_highest():
    r = dice.roll("4d6kh3", seed=3)
    kept = [d["value"] for d in r["dice"] if d["kept"]]
    dropped = [d["value"] for d in r["dice"] if not d["kept"]]
    assert len(kept) == 3 and len(dropped) == 1
    assert min(kept) >= max(dropped)
    assert r["total"] == sum(kept)


def test_roll_drop_highest():
    r = dice.roll("3d6dh1", seed=3)
    dropped = [d["value"] for d in r["dice"] if not d["kept"]]
    assert len(dropped) == 1
    assert dropped[0] == max(d["value"] for d in r["dice"])


def test_roll_modifier_and_vs_outcomes():
    always = dice.roll("2d6+3 vs 1", seed=1)
    assert always["outcome"] == "success" and always["total"] >= 5
    never = dice.roll("2d6 vs 999", seed=1)
    assert never["outcome"] == "failure"


def test_roll_pool_counts_successes():
    r = dice.roll("7d10t6", seed=11)
    assert r["total"] is None
    assert r["successes"] == sum(1 for d in r["dice"] if d["value"] >= 6)
    assert r["pool_target"] == 6


def test_roll_sum_explosions_chain_onto_die():
    r = dice.roll("20d2!", seed=5)
    exploded = [d for d in r["dice"] if len(d["rolls"]) > 1]
    assert exploded, "20 d2 dice at seed 5 must include at least one max face"
    for d in exploded:
        assert all(x == 2 for x in d["rolls"][:-1])
        assert d["value"] == sum(d["rolls"])
    assert len(r["dice"]) == 20


def test_roll_pool_explosions_add_dice():
    r = dice.roll("20d2!t2", seed=5)
    assert len(r["dice"]) > 20
    assert all(len(d["rolls"]) == 1 for d in r["dice"])


class _MaxRng:
    """Always rolls the maximum face — forces endless explosions."""
    def randint(self, lo, hi):
        return hi


def test_explosion_budget_caps_hostile_notation():
    spec = dice.parse("1d2!")
    chain = dice._roll_dice(_MaxRng(), spec)
    assert len(chain) == 1
    assert len(chain[0]["rolls"]) == dice.MAX_EXPLOSIONS + 1
    pool_spec = dice.parse("1d2!t2")
    pool = dice._roll_dice(_MaxRng(), pool_spec)
    assert len(pool) == dice.MAX_EXPLOSIONS + 1
