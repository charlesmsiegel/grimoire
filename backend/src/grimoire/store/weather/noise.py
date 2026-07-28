"""The correlated noise field behind every draw.

An i.i.d. standard-normal latent at every block ordinal, smoothed by a
one-sided exponential filter whose coefficient *is* `persistence` — so
`persistence` is the lag-1 autocorrelation between adjacent blocks, a real unit
rather than a dial. Sampling is random access: block 4,500 costs the same as
block 3, which is why a campaign's age never enters into it.
"""

from __future__ import annotations

import hashlib
import math
from statistics import NormalDist

_ND = NormalDist()

_MANTISSA = 1 << 53

CLAMP = 0.998


def latent_u(cid: str, zone: str, axis: str, i: int) -> float:
    """A uniform in (0, 1), strictly interior and injective.

    ``(2n + 1) / 2**53`` over 52 digest bits: the numerator is an odd integer
    below 2**53 so it is exactly representable, and dividing by a power of two
    is exact. ``n / 2**53`` would emit 0, which has no normal quantile; the
    obvious repair of midpoints over 53 bits is not representable at the top of
    the range and collapses distinct inputs onto one value.
    """
    key = f"{cid}\x1f{zone}\x1f{axis}\x1f{i}".encode("utf-8")
    digest = hashlib.blake2b(key, digest_size=32).digest()
    n = int.from_bytes(digest[:8], "big") >> 12  # leading 52 bits
    return (2 * n + 1) / _MANTISSA


def latent_z(cid: str, zone: str, axis: str, i: int) -> float:
    """Standard normal latent at block ordinal ``i``."""
    return _ND.inv_cdf(latent_u(cid, zone, axis, i))


def window(persistence: float) -> int:
    """Maximum lag W. The filter runs k = 0..W inclusive, so W + 1 taps."""
    a = min(max(persistence, 0.0), CLAMP)
    if a <= 0.0:
        return 0
    return math.ceil(4 / math.log(1 / a))


def field(cid: str, zone: str, axis: str, i: int, persistence: float) -> float:
    """The smoothed field g(i): a normalized one-sided exponential filter.

    One ascending pass with carried powers. ``a**k`` and repeated
    multiplication differ in their last bits, and the numerator and denominator
    accumulate together, so the arithmetic stays put when this module is
    refactored. Normalizing by the *finite* weight sum keeps the variance at 1
    for any W; the infinite form leaves a systematic error that grows as
    persistence falls.
    """
    a = min(max(persistence, 0.0), CLAMP)
    w, num, den = 1.0, 0.0, 0.0
    for k in range(window(a) + 1):
        num += w * latent_z(cid, zone, axis, i - k)
        den += w * w
        w *= a
    return num / math.sqrt(den)


def quantile(cid: str, zone: str, axis: str, i: int, persistence: float) -> float:
    """The field pushed through the normal CDF: a uniform quantile in (0, 1).

    This is the copula step. Inverse-CDF sampling reproduces a table's declared
    weights only from uniform quantiles, and a smoothed Gaussian is not uniform
    until Phi is applied.
    """
    return _ND.cdf(field(cid, zone, axis, i, persistence))
