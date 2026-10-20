"""Deterministic derived reasoning presets and scoped notes for effort which cannot be carried."""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Callable, Iterator, Mapping
from typing import NamedTuple

from .... import llm_reasoning, llm_sampling
from ... import inference_keys as keys
from ... import inference_retired as retired
from ... import paths, routing, sampler_presets
from .. import cascade
from . import _contracts


# ---- the derived presets ----
def derived_name(base_name: str, effort: str) -> str:
    """`"<base> · reasoning <effort>"`, or `"Reasoning <effort>"` with no base."""
    return f"{base_name} · reasoning {effort}" if base_name else f"Reasoning {effort}"


def _canonical(params: Mapping) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def derive(base: dict | None, effort: str, existing: _contracts.PresetRead) -> _contracts.Derived:
    """The preset a slot whose own preset is `base` (None: no readable one)
    is repointed at to carry `effort`: `base`'s params plus that effort.

    Its id is the name's slug. When a preset by that id already holds a
    different name or different params, the slug takes a suffix: the first
    eight hex digits of a digest over the derived NAME and params. So
    identical derivations (one name, one set of params) collapse to one file
    whatever base they came from, two devices pick the same id, and two
    different derivations whose names slug alike ("Warm", "Warm!") never
    share one."""
    name = derived_name(str(base.get("name") or "") if base else "", effort)
    own = base.get("params") if base else None
    merged = {**(own if isinstance(own, dict) else {}), "reasoning_effort": effort}
    params = {k: merged[k] for k in llm_sampling.CONTROLS if k in merged}
    pid = paths.slugify(name)
    taken = existing(pid)
    if taken is not None and (taken.get("name") != name or taken.get("params") != params):
        digest = hashlib.sha256(_canonical({"name": name, "params": params})
                                .encode("utf-8")).hexdigest()
        pid = f"{pid}-{digest[:8]}"
    return _contracts.Derived(pid, name, params)


def _legacy_effort(selection: cascade.Selection, conn: _contracts.Lookup,
                   levels: tuple[str, ...]) -> str:
    """The legacy GLM reasoning effort the legacy wire sent for `selection`,
    when it is one of `levels`; "" otherwise. Sent only by an
    `openai_compatible` provider, to a GLM model (`llm_reasoning.is_glm`)."""
    raw = conn(selection.provider)
    if raw is None or raw.get("kind") != "openai_compatible":
        return ""
    effort = str(raw.get("reasoning_effort") or "")
    if effort not in levels or not llm_reasoning.is_glm({"model": selection.model}):
        return ""
    return effort


def _slots(view: Mapping, *, campaign: bool) -> Iterator[tuple[str, cascade.Selection]]:
    """`(preset key, selection)` for every selection slot `view` sets: each
    generative role and its fallback, then each route pinned to a model (at a
    campaign, only the routes a campaign may override)."""
    def slot(prefix: Callable[[str], str]) -> cascade.Selection | None:
        provider = str(view.get(prefix("provider"), "") or "")
        if not provider:
            return None
        return cascade.Selection(provider, str(view.get(prefix("model"), "") or ""),
                                 str(view.get(prefix("preset"), "") or ""))

    for role in keys.GENERATIVE_ROLES:
        for prefix in (functools.partial(keys.role_key, role),
                       functools.partial(keys.fallback_key, role)):
            got = slot(prefix)
            if got is not None:
                yield prefix("preset"), got
    for route in routing.ROUTES:
        if campaign and not route.campaign_scoped:
            continue
        if str(view.get(keys.use_key(route.key), "") or "") != keys.PIN:
            continue
        prefix = functools.partial(keys.pin_key, route.key)
        got = slot(prefix)
        if got is not None:
            yield prefix("preset"), got


def _derivation(view: Mapping, *, campaign: bool, conn: _contracts.Lookup,
                presets: _contracts.PresetRead) -> tuple[dict[str, str], tuple[_contracts.Derived, ...]]:
    """`(repoint, presets)` for every slot of `view` the legacy effort rode on
    (ruling 4): its provider `openai_compatible`, its legacy effort one of
    `REPRESENTABLE`, its model GLM, and its own preset, read, setting no
    reasoning effort."""
    repoint: dict[str, str] = {}
    derived: dict[str, _contracts.Derived] = {}

    def existing(pid: str) -> dict | None:
        # A preset this plan derived already answers first, so two different
        # bases that derive one name never share an id.
        got = derived.get(pid)
        return {"name": got.name, "params": got.params} if got is not None else presets(pid)

    for preset_key, selection in _slots(view, campaign=campaign):
        made = _derived_for(selection, conn, presets, existing)
        if made is None:
            continue
        derived.setdefault(made.id, made)
        repoint[preset_key] = made.id
    return repoint, tuple(derived.values())


def _derived_for(selection: cascade.Selection, conn: _contracts.Lookup, presets: _contracts.PresetRead,
                 existing: _contracts.PresetRead) -> _contracts.Derived | None:
    """The derived preset one selection slot is repointed at (ruling 4), or
    None: its provider `openai_compatible` with a legacy effort one of
    `REPRESENTABLE`, its model GLM, and its own preset, read, setting no
    reasoning effort. `existing` is what `derive` checks an id against."""
    effort = _legacy_effort(selection, conn, _contracts.REPRESENTABLE)
    if not effort:
        return None
    own = selection.preset.strip()
    base = presets(own) if own else None
    params = base.get("params") if base is not None else None
    if isinstance(params, dict) and "reasoning_effort" in params:
        return None
    return derive(base, effort, existing)


class _RoutePreset(NamedTuple):
    """The route-level preset a route runs under: its name ("" for
    `PRESET_CLEAR`, no preset at all) and whether it sets a reasoning effort."""

    name: str
    sets_effort: bool


def _route_preset(view: Mapping, route: routing.Route,
                  presets: _contracts.PresetRead) -> _RoutePreset | None:
    """`view`'s opinion on `route`'s preset, by `cascade.preset_for`'s rule:
    None for none (absent, blank, or an id that names no preset, which the
    walk passes)."""
    chosen = str(view.get(keys.preset_key(route.key), "") or "").strip()
    if chosen == sampler_presets.PRESET_CLEAR:
        return _RoutePreset("", False)
    preset = presets(chosen) if chosen else None
    if preset is None:
        return None
    params = preset.get("params")
    return _RoutePreset(str(preset.get("name") or chosen),
                        isinstance(params, dict) and "reasoning_effort" in params)


def _under_route_preset(route: routing.Route, view: Mapping, glob: Mapping, *,
                        campaign: bool, from_global: bool,
                        conn: _contracts.Lookup) -> list[cascade.Selection]:
    """The selection and fallback `route` runs on at this scope, both under
    its route preset: the cascade over the scope (a campaign's over it and
    `glob`). When a campaign runs under the GLOBAL route preset, a provider
    the global plan NOTES on that route is left out -- the global note (one
    per route and provider, and a provider's legacy effort is the provider's)
    already says it. Only a noted one: whether a selection is noted turns on
    its model too, so a provider the global scope runs on a model that is not
    GLM is noted for the campaign that runs it on one that is."""
    def exists(provider: str) -> bool:
        return conn(provider) is not None

    if campaign:
        choice = cascade.choose(route, campaign=dict(view), glob=dict(glob), exists=exists)
    else:
        choice = cascade.choose(route, campaign={}, glob=dict(view), exists=exists)
    noted_globally: set[str] = set()
    if from_global:
        alone = cascade.choose(route, campaign={}, glob=dict(glob), exists=exists)
        noted_globally = {s.provider for s in (alone.selection, alone.fallback)
                          if s is not None
                          and _legacy_effort(s, conn, llm_reasoning.GLM_EFFORTS)}
    return [s for s in (choice.selection, choice.fallback)
            if s is not None and s.provider not in noted_globally]


def _route_notes(view: Mapping, *, glob: Mapping, scope: str, campaign: bool,
                 conn: _contracts.Lookup, presets: _contracts.PresetRead) -> tuple[retired.Note, ...]:
    """Ruling 5: a route whose route-level preset (`PRESET_CLEAR` included)
    sets no reasoning effort, and whose selection -- or whose fallback, which
    the route's preset follows -- is a GLM provider with a legacy effort. Not
    derived (ratification item 3), so noted, once per (scope, route,
    provider).

    At a campaign, the route is judged as the campaign runs it: its preset
    by the campaign-then-global walk, its selection by the cascade over the
    campaign and `glob` (the global settings as format 2 sees them). So a
    campaign preset over a selection the campaign inherits is noted, and so
    is a campaign's own selection under a global preset. A global preset
    over a selection the global scope chooses as well is the global plan's
    note, and is not noted again per campaign."""
    out: dict[str, retired.Note] = {}
    for route in routing.ROUTES:
        if campaign and not route.campaign_scoped:
            continue
        found = _route_preset(view, route, presets)
        from_global = False
        if found is None and campaign:
            found, from_global = _route_preset(glob, route, presets), True
        if found is None or found.sets_effort:
            continue
        for selection in _under_route_preset(route, view, glob, campaign=campaign,
                                             from_global=from_global, conn=conn):
            effort = _legacy_effort(selection, conn, llm_reasoning.GLM_EFFORTS)
            if not effort:
                continue
            raw = conn(selection.provider) or {}
            note = _route_note(scope, route, selection.provider,
                               str(raw.get("name") or selection.provider), effort,
                               found.name)
            out.setdefault(note.id, note)
    return tuple(out.values())


def _route_note(scope: str, route: routing.Route, provider: str, provider_name: str,
                effort: str, preset_name: str) -> retired.Note:
    where = "" if scope == _contracts.GLOBAL_SCOPE else " in this campaign"
    what = (f"the preset “{preset_name}” sets no reasoning effort" if preset_name
            else "no preset is set")
    text = (f"On the {route.label} route{where}, {what}, so the GLM provider "
            f"“{provider_name}” no longer sends its reasoning effort ({effort}) there "
            "— this was not carried over.")
    kind = "route_preset"
    return retired.Note(retired.note_id(scope, route.key, provider, effort, kind),
                        scope, route.key, provider, effort, kind, text)


def _slot_label(preset_key: str) -> str:
    """How a note names the selection slot whose preset key is `preset_key`."""
    for role in keys.GENERATIVE_ROLES:
        if preset_key == keys.role_key(role, "preset"):
            return f"the {role.capitalize()} role"
        if preset_key == keys.fallback_key(role, "preset"):
            return f"the {role.capitalize()} role's fallback"
    for route in routing.ROUTES:
        if preset_key == keys.pin_key(route.key, "preset"):
            return f"the {route.label} route"
    return preset_key


def _stranded(view: Mapping, *, scope: str, campaign: bool, conn: _contracts.Lookup,
              presets: _contracts.PresetRead) -> tuple[retired.Note, ...]:
    """A scope carrying the retirement marker but no format marker (N1, review
    M-2): mapped as the migration maps it, and derived nothing, because it
    says it is retired -- so a GLM slot whose legacy effort rode on the
    legacy wire loses it. Each such slot is noted (guarantee 7), once per
    (scope, slot, provider): kind `unrepresentable`, a legacy effort this
    scope's presets cannot carry."""
    out: dict[str, retired.Note] = {}
    for preset_key, selection in _slots(view, campaign=campaign):
        effort = _legacy_effort(selection, conn, llm_reasoning.GLM_EFFORTS)
        if not effort:
            continue
        own = selection.preset.strip()
        base = presets(own) if own else None
        params = base.get("params") if base is not None else None
        if isinstance(params, dict) and "reasoning_effort" in params:
            continue
        raw = conn(selection.provider) or {}
        name = str(raw.get("name") or selection.provider)
        where = "" if scope == _contracts.GLOBAL_SCOPE else " in this campaign"
        kind = "unrepresentable"
        text = (f"On {_slot_label(preset_key)}{where}, the GLM provider “{name}” no longer "
                f"sends its reasoning effort ({effort}), because no preset there sets one "
                "— this was not carried over.")
        note = retired.Note(retired.note_id(scope, preset_key, selection.provider, effort, kind),
                            scope, preset_key, selection.provider, effort, kind, text)
        out.setdefault(note.id, note)
    return tuple(out.values())


def _planned(view: Mapping, *, glob: Mapping, mapped: dict[str, str], model_facts: dict,
             scope: str, campaign: bool, conn: _contracts.Lookup, presets: _contracts.PresetRead) -> _contracts.Plan:
    repoint, made = _derivation(view, campaign=campaign, conn=conn, presets=presets)
    notes = _route_notes(view, glob=glob, scope=scope, campaign=campaign, conn=conn,
                         presets=presets)
    return _contracts.Plan(mapped, repoint, model_facts, made, notes)
