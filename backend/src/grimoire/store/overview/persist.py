"""The persisted layer under an overview projection (spec 04 sections 3.3, 3.7).

`derive(kind, inputs, params, compute, live)` answers through spec 03's
compiled cache when it can, and through `live` -- today's code over the store's
own reads -- whenever it cannot:

- the kind is not registered (`compiled.KINDS`): it lost spec 03's section 14
  measurement, or it has not been measured, so nothing is looked up, hashed or
  stored and the read costs exactly what it cost before 04;
- the layer is off (`GRIMOIRE_COMPILED_CACHE=0`, no SQLite) or unavailable
  for this request (on the event loop, under a campaign lock, BUILD unknown);
- an input cannot be read (`compiled.InputUnavailable`, 03-C4 as amended):
  the derivation is uncacheable for this request and nothing is stored.

The last two count `overview.live_path.<kind>` (the debug row's `degraded`).
A busy, locked or corrupt database is a miss inside `Batch.derive`, which then
runs `compute` over the bytes it validated -- the same answer, stored nowhere.

`compute` receives the bytes of each input by role, never a path (spec 03
section 6), and None for an absent-ok input that is absent. Failures that come
from the bytes (a decode error, a garbled ledger) are the compute's to fold
into the value or to raise: one that raises propagates exactly as the live
read would have raised it, and nothing is stored.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TypeVar

from .. import compiled, readstats

T = TypeVar("T")


def registered(kind: compiled.Kind) -> bool:
    """Whether `kind` is in the compiled registry, i.e. won its measurement."""
    return compiled.KINDS.get(kind.name) == kind


def derive(kind: compiled.Kind, inputs: Sequence[compiled.Input], params: Mapping[str, object],
           compute: Callable[[Mapping[str, bytes | None]], T], live_read: Callable[[], T]) -> T:
    def live() -> T:
        readstats.bump(f"overview.computed.{_short(kind)}")
        return live_read()
    if not registered(kind) or not compiled.enabled():
        return live()
    if not compiled.available():
        readstats.bump(f"overview.live_path.{kind.name}")
        return live()
    try:
        with compiled.batch() as request:
            value = request.derive(
                kind, list(inputs), params=params,
                compute=lambda view, _params: _computed(kind, compute, view))
    except compiled.InputUnavailable:
        readstats.bump(f"overview.live_path.{kind.name}")
        return live()
    return value  # type: ignore[return-value]


def _short(kind: compiled.Kind) -> str:
    return kind.name.removeprefix("overview.")


def _computed(kind: compiled.Kind, compute: Callable[[Mapping[str, bytes | None]], T],
              view: compiled.DerivationInputs) -> T:
    readstats.bump(f"overview.computed.{_short(kind)}")
    return compute({item.role: item.data for item in view.files})
