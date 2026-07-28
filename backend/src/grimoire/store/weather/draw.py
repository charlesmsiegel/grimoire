"""Turning a quantile into weather.

Three axes, drawn in order. Temperature first, because a condition's
`requires_temp` filters against it — which is why an ineligible combination is
not representable here rather than being prevented by a rule someone has to
remember.
"""

from __future__ import annotations

from .noise import quantile


def inverse_cdf(entries: list[dict], u: float) -> str:
    """The entry whose half-open bucket contains ``u``.

    Zero-weight rows are skipped rather than given empty buckets, and the last
    *positive-weight* row closes the range at 1.0. Closing on the physical last
    entry instead lets floating-point drift hand the draw to a disabled row —
    and if that row was disabled by `requires_temp`, to an ineligible condition.

    Weights are scaled by the largest before summing, so a table of large but
    individually finite weights cannot overflow on the way to a distribution.

    A table with nothing selectable — empty, or every weight zero — draws the
    empty name. Validation forbids both, and a hand-edited document that
    reaches here must degrade rather than raise into a turn. Returning the
    first row instead would emit a name its own weight had switched off.
    """
    live = [e for e in entries if e["weight"] > 0]
    if not live:
        return ""
    scale = max(e["weight"] for e in live)
    total = sum(e["weight"] / scale for e in live)
    cumulative = 0.0
    for entry in live[:-1]:
        cumulative += (entry["weight"] / scale) / total
        if u < cumulative:
            return entry["name"]
    return live[-1]["name"]


def _eligible(conditions: list[dict], temperature: str) -> list[dict]:
    out = []
    for c in conditions:
        required = c.get("requires_temp")
        if required is None or temperature in required:
            out.append(c)
    if not any(c["weight"] > 0 for c in out):
        # A validated climate cannot reach here; a hand-edited one can. Fall
        # back to the best unconstrained row — never to a filtered-out one,
        # which would emit exactly the combination the constraint forbids. When
        # there is no unconstrained row either, `out` goes through unchanged
        # and the draw comes back empty, which is the only answer left that
        # still honours the constraint.
        unconstrained = [c for c in conditions if c.get("requires_temp") is None and c["weight"] > 0]
        if unconstrained:
            best = max(e["weight"] for e in unconstrained)
            return [next(e for e in unconstrained if e["weight"] == best)]
    return out


def draw(cid: str, zone: str, season: dict, persistence: float, ordinal: int) -> dict:
    """The three resolved axes for one block."""
    temperature = inverse_cdf(
        season["temperature"], quantile(cid, zone, "temperature", ordinal, persistence))
    condition = inverse_cdf(
        _eligible(season["conditions"], temperature),
        quantile(cid, zone, "condition", ordinal, persistence))
    wind = inverse_cdf(
        season["wind"], quantile(cid, zone, "wind", ordinal, persistence))
    return {"temperature": temperature, "condition": condition, "wind": wind}
