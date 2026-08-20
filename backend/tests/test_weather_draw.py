import json
import math
import pathlib

import pytest

from grimoire.store.weather.draw import draw, inverse_cdf
from grimoire.store.weather.noise import field, latent_u, latent_z, quantile

WIND = [{"name": "calm", "weight": 1}, {"name": "breeze", "weight": 4},
        {"name": "strong", "weight": 3}, {"name": "gale", "weight": 1}]

# How far the regression fixture's `phi` column may drift, in ulp. Two
# independent normal CDFs (this machine's libm and scipy's Cephes `ndtr`)
# disagreed by under one ulp on the fixture's own rows, so this is headroom for
# another correctly-rounded implementation and nothing more: an algorithm change
# large enough to matter moves `g`, which is pinned exactly.
PHI_ULPS = 4


def season(**over):
    s = {"name": "winter", "from": 0.0, "to": 0.0,
         "temperature": [{"name": "freezing", "weight": 2}, {"name": "mild", "weight": 8}],
         "conditions": [{"name": "clear", "weight": 5},
                        {"name": "snow", "weight": 5, "requires_temp": ["freezing"]}],
         "wind": WIND}
    s.update(over)
    return s


def test_inverse_cdf_selects_by_cumulative_weight():
    assert inverse_cdf(WIND, 0.0) == "calm"
    assert inverse_cdf(WIND, 0.2) == "breeze"
    assert inverse_cdf(WIND, 0.6) == "strong"
    assert inverse_cdf(WIND, 0.95) == "gale"


def test_buckets_are_half_open_at_the_boundary():
    # calm occupies [0, 1/9); breeze starts exactly at 1/9.
    assert inverse_cdf(WIND, 1 / 9) == "breeze"


def test_zero_weight_entries_are_never_selected():
    table = [{"name": "a", "weight": 1}, {"name": "b", "weight": 9}, {"name": "off", "weight": 0}]
    drawn = {inverse_cdf(table, i / 1000) for i in range(1000)}
    assert "off" not in drawn


def test_largest_quantile_does_not_fall_through_to_a_disabled_row():
    # The spec's [1, 9, 0] case: the second entry's cumulative rounds to the
    # largest representable quantile, so closing on the physical last entry
    # would hand the draw to the zero-weight row.
    table = [{"name": "a", "weight": 1}, {"name": "b", "weight": 9}, {"name": "off", "weight": 0}]
    assert inverse_cdf(table, (2 * ((1 << 52) - 1) + 1) / (1 << 53)) == "b"


def test_huge_but_valid_weights_still_produce_their_distribution():
    table = [{"name": "a", "weight": 1e300}, {"name": "b", "weight": 1e300}]
    lows = sum(inverse_cdf(table, i / 1000) == "a" for i in range(1000))
    assert 450 < lows < 550


def test_snow_never_appears_outside_its_temperature_band():
    for i in range(4000):
        got = draw("realm", "saltmarch", season(), 0.5, i)
        if got["condition"] == "snow":
            assert got["temperature"] == "freezing"


def test_all_three_axes_are_populated():
    got = draw("realm", "saltmarch", season(), 0.5, 0)
    assert set(got) == {"temperature", "condition", "wind"}
    assert got["wind"] in {e["name"] for e in WIND}


def test_draw_is_deterministic():
    a = draw("realm", "saltmarch", season(), 0.5, 42)
    b = draw("realm", "saltmarch", season(), 0.5, 42)
    assert a == b


def test_degenerate_filtered_table_falls_back_to_an_unconstrained_condition():
    # `mild` has no eligible constrained condition; the fallback must be the
    # unconstrained one, never the constrained row it just filtered out.
    s = season(temperature=[{"name": "mild", "weight": 1}],
               conditions=[{"name": "drizzle", "weight": 3},
                           {"name": "snow", "weight": 5, "requires_temp": ["freezing"]}])
    for i in range(200):
        assert draw("realm", "saltmarch", s, 0.5, i)["condition"] == "drizzle"


def test_a_table_with_no_usable_row_yields_an_empty_name_rather_than_raising():
    # Validation forbids both of these documents. A hand-edited file can still
    # reach here, and weather must never raise into a turn — so the contract is
    # an empty name, which the resolver reports as "no weather". Emitting the
    # filtered-out row instead would print exactly the combination the
    # constraint forbids.
    assert inverse_cdf([], 0.5) == ""
    s = season(temperature=[{"name": "mild", "weight": 1}],
               conditions=[{"name": "snow", "weight": 5, "requires_temp": ["freezing"]}])
    assert draw("realm", "saltmarch", s, 0.5, 0)["condition"] == ""


def test_an_all_zero_weight_table_yields_an_empty_name():
    assert inverse_cdf([{"name": "a", "weight": 0}, {"name": "b", "weight": 0}], 0.5) == ""


@pytest.mark.parametrize("persistence", [0.0, 0.5, 0.9])
def test_weight_fidelity_over_independent_zones(persistence):
    """Declared weights must survive the whole chain, at every persistence.

    Two things this pins that a single value would not. Sampling across
    *independent zones* rather than along one run: consecutive blocks are
    autocorrelated by construction, so a long run carries far less information
    than its length suggests and the binomial bound would understate the
    spread. And running at *nonzero* persistence: at 0.0 the filter collapses
    to the raw latent, so a broken normalization or copula mapping in the
    smoothing path would never be exercised.

    N, the zone ids and the campaign id are the spec's fixed fixture and must
    not be reduced: the 3-sigma bound widens as N shrinks, so a smaller sample
    lets a real copula or normalization bias pass. Measured cost for all three
    persistences together is ~7s.
    """
    table = [{"name": "clear", "weight": 2}, {"name": "overcast", "weight": 5},
             {"name": "light rain", "weight": 4}, {"name": "storm", "weight": 1}]
    s = season(temperature=[{"name": "mild", "weight": 1}], conditions=table)
    n = 100_000
    counts = {e["name"]: 0 for e in table}
    for i in range(n):
        counts[draw("fidelity-check", f"fidelity-{i:05d}", s, persistence, 0)["condition"]] += 1
    total = sum(e["weight"] for e in table)
    for e in table:
        p = e["weight"] / total
        assert counts[e["name"]] / n == pytest.approx(p, abs=3 * (p * (1 - p) / n) ** 0.5)


def test_end_to_end_regression_fixture():
    """Pins the whole chain: hash, inv_cdf, filter order, phi, inverse CDF.

    Scoped to this installation (spec: Determinism scope) — it detects an
    accidental change to the algorithm, not cross-implementation conformance.
    If this fails, weather in every existing campaign has moved. Regenerate the
    fixture only when that was the intent.

    ``phi`` is the one column asserted to a tolerance rather than bit-exactly,
    because it is the one step that is not portable: ``NormalDist.cdf`` calls
    ``math.erf``, and CPython delegates that straight to the platform libm, so
    its last bit follows the C library rather than this code. Everything either
    side of it is pinned exactly and *is* portable — BLAKE2b is bit-stable, the
    ``latent_u`` mapping is exact power-of-two arithmetic, ``inv_cdf`` is
    pure-Python AS241, and ``field`` is IEEE arithmetic in an order this module
    pins deliberately. An accidental change to the algorithm therefore still
    fails here; only a last-bit difference in the platform's error function is
    forgiven. Nor can that tolerance hide a changed outcome: ``drawn`` stays
    bit-exact, so if a cumulative table boundary ever did sit within a few ulp
    of a pinned quantile, the ``drawn`` assertion is what would catch it.
    """
    data = json.loads((pathlib.Path(__file__).parent / "fixtures" /
                       "weather_vectors.json").read_text(encoding="utf-8"))
    for row in data["rows"]:
        cid, zone, i, p = row["cid"], row["zone"], row["ordinal"], row["persistence"]
        assert latent_u(cid, zone, "condition", i) == row["u"]
        assert latent_z(cid, zone, "condition", i) == row["z"]
        assert field(cid, zone, "condition", i, p) == row["g"]
        assert quantile(cid, zone, "condition", i, p) == pytest.approx(
            row["phi"], abs=PHI_ULPS * math.ulp(row["phi"]))
        assert draw(cid, zone, data["season"], p, i) == row["drawn"]
