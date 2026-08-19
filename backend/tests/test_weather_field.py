import math

import pytest
from grimoire.store.weather.noise import field, quantile, window


def series(persistence, n=20_000, zone="saltmarch"):
    return [field("realm", zone, "condition", i, persistence) for i in range(n)]


def lag1(xs):
    mean = sum(xs) / len(xs)
    num = sum((xs[i] - mean) * (xs[i + 1] - mean) for i in range(len(xs) - 1))
    den = sum((x - mean) ** 2 for x in xs)
    return num / den


def test_window_is_zero_at_zero_persistence():
    assert window(0.0) == 0


def test_window_matches_the_documented_examples():
    assert window(0.9) == 38
    assert window(0.99) == 398


def test_window_is_clamped_at_the_upper_bound():
    assert window(1.0) == window(0.998)


def test_zero_persistence_gives_independent_blocks():
    assert abs(lag1(series(0.0))) < 0.03


@pytest.mark.parametrize("p", [0.0, 0.35, 0.5, 0.9])
def test_persistence_is_the_lag_one_autocorrelation(p):
    assert lag1(series(p)) == pytest.approx(p, abs=0.03)


def test_field_has_unit_variance():
    for p in (0.0, 0.5, 0.9):
        xs = series(p)
        mean = sum(xs) / len(xs)
        var = sum((x - mean) ** 2 for x in xs) / len(xs)
        assert var == pytest.approx(1.0, abs=0.05)


def test_higher_persistence_gives_longer_runs():
    def runs(p):
        xs = series(p)
        signs = [x > 0 for x in xs]
        changes = sum(1 for i in range(len(signs) - 1) if signs[i] != signs[i + 1])
        return len(signs) / max(changes, 1)
    assert runs(0.9) > 3 * runs(0.1)


def test_quantile_is_inside_the_unit_interval():
    for i in range(1000):
        u = quantile("realm", "saltmarch", "wind", i, 0.5)
        assert 0.0 < u < 1.0


def test_shared_zone_and_different_persistence_stay_correlated():
    # Both are smoothings of the same latent, so their fields must move together.
    a = [field("realm", "saltmarch", "condition", i, 0.3) for i in range(5000)]
    b = [field("realm", "saltmarch", "condition", i, 0.8) for i in range(5000)]
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    norm = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    assert cov / norm > 0.5


def test_different_zones_are_uncorrelated():
    a = [field("realm", "saltmarch", "condition", i, 0.5) for i in range(5000)]
    b = [field("realm", "highreach", "condition", i, 0.5) for i in range(5000)]
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    norm = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    assert abs(cov / norm) < 0.1


def test_no_seam_at_an_arbitrary_boundary():
    # Nothing distinguishes one ordinal from another: the correlation across a
    # chosen "boundary" ordinal matches the correlation everywhere else.
    #
    # One pair from each of many *independent* zones, not many pairs strided
    # along one series. Striding gives only a couple of dozen samples whose
    # noisy covariance is then normalized by the global variance, which
    # measures ~1.32 against a true 0.90 — a test that fails against a correct
    # implementation. Independent zones make each pair a fresh draw.
    boundary = 1825
    pairs = [(field("realm", f"seam-{n:04d}", "condition", boundary, 0.9),
              field("realm", f"seam-{n:04d}", "condition", boundary + 1, 0.9))
             for n in range(4000)]
    a = [p[0] for p in pairs]
    b = [p[1] for p in pairs]
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in pairs)
    norm = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    assert cov / norm == pytest.approx(0.9, abs=0.05)
