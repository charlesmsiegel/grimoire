import math

import pytest
from grimoire.store.weather.noise import latent_u, latent_z

# Spec reference vectors. Determinism here is scoped to this installation
# (spec, Determinism scope); these pin the hash, the bit slice and the mapping.
U_VECTORS = [
    ("saltmarch-chronicle", "saltmarch", "temperature", 0, 0.45105387316006496),
    ("saltmarch-chronicle", "saltmarch", "condition", 0, 0.761560896101852),
    ("saltmarch-chronicle", "saltmarch", "wind", 0, 0.17774645354109275),
    ("saltmarch-chronicle", "saltmarch", "condition", 1, 0.9654995835326089),
    ("saltmarch-chronicle", "saltmarch", "condition", -1, 0.9510130394641975),
    ("saltmarch-chronicle", "highreach", "condition", 0, 0.21315957935313057),
]


@pytest.mark.parametrize("cid,zone,axis,i,expected", U_VECTORS)
def test_latent_u_matches_reference_vectors(cid, zone, axis, i, expected):
    assert latent_u(cid, zone, axis, i) == expected


def test_latent_u_is_strictly_inside_the_unit_interval():
    for i in range(2000):
        u = latent_u("realm", "saltmarch", "condition", i)
        assert 0.0 < u < 1.0


def test_latent_u_is_injective_over_a_large_sample():
    seen = {latent_u("realm", "saltmarch", "condition", i) for i in range(20_000)}
    assert len(seen) == 20_000


def test_axes_are_independent_streams():
    a = latent_u("realm", "saltmarch", "temperature", 7)
    b = latent_u("realm", "saltmarch", "condition", 7)
    assert a != b


def test_campaigns_do_not_share_skies():
    a = latent_u("realm-one", "saltmarch", "condition", 7)
    b = latent_u("realm-two", "saltmarch", "condition", 7)
    assert a != b


def test_unit_separator_prevents_key_collisions():
    # Without a separator, ("ab", "c") and ("a", "bc") would build one key.
    assert latent_u("ab", "c", "wind", 0) != latent_u("a", "bc", "wind", 0)


def test_latent_z_is_finite_and_roughly_standard_normal():
    xs = [latent_z("realm", "saltmarch", "wind", i) for i in range(20_000)]
    assert all(math.isfinite(x) for x in xs)
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    assert abs(mean) < 0.05
    assert abs(var - 1.0) < 0.05


def test_negative_ordinals_are_defined():
    assert math.isfinite(latent_z("realm", "saltmarch", "wind", -5000))
