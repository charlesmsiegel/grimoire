"""Legacy planning value types, retirement/format predicates and original mapping/derivation vocabulary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import NamedTuple

from .... import llm_reasoning, llm_sampling
from ... import inference_keys as keys
from ... import inference_retired as retired
from ... import llm_connections, routing

#: A raw connection by id (legacy fields included), or None. What
#: `lookup(mode=...)` builds; a plain function will do in a test.
#: `llm_connections.Lookup`, re-bound so the planner's callers can annotate
#: with either name.
Lookup = llm_connections.Lookup
#: A sampler preset by id (`sampler_presets.read_preset`'s shape), or None.
PresetRead = Callable[[str], dict | None]

#: The retirement marker (ruling 15): a scope carrying it is planned as empty.
RETIRED_KEY = keys.RETIRED_KEY

#: The legacy GLM efforts a preset can carry: the levels GLM takes that a
#: preset's reasoning effort can say. Computed, so the two vocabularies decide.
REPRESENTABLE: tuple[str, ...] = tuple(
    e for e in llm_reasoning.GLM_EFFORTS if e in llm_sampling.REASONING)

#: The legacy routes' preset keys: spelled the same in both layouts
#: (`inference_keys.GLOBAL_KEYS`), so the migration leaves them as they are.
SHARED_PRESET_KEYS = frozenset(routing.PRESET_CONFIG_KEYS)

#: The global keys the migration writes, every one on every switch: the
#: format-2 layout without the marker (written beside them) and without the
#: preset keys both layouts share.
OWNED_GLOBAL_KEYS: tuple[str, ...] = tuple(
    k for k in keys.GLOBAL_KEYS
    if k != keys.FORMAT_KEY and k not in SHARED_PRESET_KEYS)

#: A note's scope on `config.md`; a campaign's is `campaign_scope(cid)`
#: (`retired`'s, so the record and the view spell them alike).
GLOBAL_SCOPE = retired.GLOBAL_SCOPE
campaign_scope = retired.campaign_scope


class Derived(NamedTuple):
    """A derived reasoning preset: its id, name and params."""

    id: str
    name: str
    params: dict


class Plan(NamedTuple):
    #: The migration's fields: today's translation, byte for byte (N2).
    mapped: dict[str, str]
    #: The derived presets' ids, by the preset key of each slot they replace,
    #: applied over `mapped`.
    repoint: dict[str, str]
    #: provider -> model -> the facts its legacy fields state, `{}` for none:
    #: every connection the store lists, below format 2 only.
    facts: dict[str, dict[str, dict]]
    #: The presets `repoint` names, each once.
    presets: tuple[Derived, ...]
    notes: tuple[retired.Note, ...]


def empty() -> Plan:
    """What a retired scope (or one this build does not own) plans: nothing.
    A fresh plan on every call, so no caller can mutate one another holds."""
    return Plan({}, {}, {}, (), ())


def planned(meta: Mapping[str, str], plan: Plan) -> dict[str, str]:
    """A scope's settings as format 2 sees them under `plan`: `meta`, then
    `mapped`, then `repoint` over both. For a campaign plan's `glob`, pass
    `config.md`'s raw settings and its `global_plan`."""
    return {**meta, **plan.mapped, **plan.repoint}


def _retired(meta: Mapping) -> bool:
    return str(meta.get(RETIRED_KEY, "") or "").strip() == "1"


def is_retired(meta: Mapping) -> bool:
    """Whether a `config.md` or `campaign.md` carries the retirement marker
    (whatever its format marker says)."""
    return _retired(meta)


def _settled(meta: Mapping) -> bool:
    """Whether a scope plans nothing at all: a newer build's, or retired AND
    marked current. A retirement marker on a scope with no format marker (a
    copy restored beside an older `config.md`, a hand edit) does not settle
    it: the migration still maps that scope -- `migrate.campaign` and the
    switch go by the format marker alone -- so the planner maps it too
    (planned equals persisted; slice I, N1). Retirement then derives
    nothing there, because the scope says it is retired."""
    return keys.is_newer(meta) or (_retired(meta) and keys.is_current(meta))


class OverlayError(ValueError):
    """The overlay planned one derived preset id with two bodies. `derive`
    reads the global derived presets before the store's, so this cannot
    happen; raised rather than asserted, so a regression fails loudly under
    `-O` too, and as a ValueError rather than an AssertionError."""
