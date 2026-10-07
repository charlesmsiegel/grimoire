"""The selection, preset and fallback cascade (spec 5.1, 5.2, 5.5).

Pure: the two scope dicts (already in the current layout -- `translate` makes
them from a legacy store), an existence predicate and a preset predicate in, a
decision out. Nothing here reads a file or looks a provider up.

A slot "is set" when `exists(provider)` is true for the provider id as stored.
Ids are never stripped here: a padded id fails `exists`, exactly as a padded
`active_connection_id` always has. A reference to a provider that is gone, or
a preset id that names no preset, is "no opinion" and the walk goes on.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from .. import routing
from ..sampler_presets import PRESET_CLEAR
from . import keys


class Selection(NamedTuple):
    provider: str
    model: str
    preset: str


class Choice(NamedTuple):
    selection: Selection | None
    #: The role whose slot supplied the selection ("" for a pin).
    role: str
    #: "route" (a pin) or "role"; "" when nothing was selected.
    via: str
    #: Whose setting decided it: "campaign" | "global" | "none".
    scope: str
    fallback: Selection | None


_NONE = (None, "", "none")


def _slot(view: dict, prefix: Callable[[str], str],
          exists: Callable[[str], bool]) -> Selection | None:
    """The selection stored under `prefix(part)`, if its provider exists."""
    provider = str(view.get(prefix("provider"), "") or "")
    if not provider or not exists(provider):
        return None
    return Selection(provider, str(view.get(prefix("model"), "") or ""),
                     str(view.get(prefix("preset"), "") or ""))


def _role_slot(view: dict, role: str, exists: Callable[[str], bool]) -> Selection | None:
    return _slot(view, lambda part: keys.role_key(role, part), exists)


def _fallback_slot(view: dict, role: str, exists: Callable[[str], bool]) -> Selection | None:
    return _slot(view, lambda part: keys.fallback_key(role, part), exists)


def _pin_slot(view: dict, route_key: str, exists: Callable[[str], bool]) -> Selection | None:
    if str(view.get(keys.use_key(route_key), "") or "") != keys.PIN:
        return None
    return _slot(view, lambda part: keys.pin_key(route_key, part), exists)


def role_selection(role: str, *, campaign: dict, glob: dict,
                   exists: Callable[[str], bool]) -> tuple[Selection | None, str, str]:
    """`(selection, supplying_role, scope)` for `role`: campaign then global
    for the role itself, then the same for what it inherits. Embedding
    inherits nothing."""
    current: str | None = role
    while current is not None:
        for scope, view in (("campaign", campaign), ("global", glob)):
            got = _role_slot(view, current, exists)
            if got is not None:
                return got, current, scope
        current = keys.INHERITS.get(current)
    return _NONE


def role_fallback(role: str, *, campaign: dict, glob: dict,
                  exists: Callable[[str], bool]) -> Selection | None:
    """The fallback of `role`: campaign then global, then of what it inherits.
    Whether any role supplies a selection has no bearing on it."""
    current: str | None = role
    while current is not None:
        for view in (campaign, glob):
            got = _fallback_slot(view, current, exists)
            if got is not None:
                return got
        current = keys.INHERITS.get(current)
    return None


def _named_role(view: dict, route_key: str) -> str:
    """The generative role `use_<route>` names in `view`, or ""."""
    value = str(view.get(keys.use_key(route_key), "") or "")
    return value if value in keys.GENERATIVE_ROLES else ""


def choose(route: routing.Route | None, *, campaign: dict, glob: dict,
           exists: Callable[[str], bool]) -> Choice:
    """Spec 5.1: campaign route, campaign role, global route, global role."""

    def fallback_of(role: str) -> Selection | None:
        return role_fallback(role, campaign=campaign, glob=glob, exists=exists)

    if route is None:
        # An unknown task resolves as it always has: the Primary role.
        got, supplier, scope = role_selection("primary", campaign=campaign, glob=glob,
                                              exists=exists)
        if got is None:
            return Choice(None, "", "", "none", fallback_of("primary"))
        return Choice(got, supplier, "role", scope, fallback_of("primary"))

    scoped = campaign if route.campaign_scoped else {}

    # 2. Campaign route.
    named = _named_role(scoped, route.key)
    if named:
        got, supplier, scope = role_selection(named, campaign=scoped, glob=glob,
                                              exists=exists)
        if got is not None:
            return Choice(got, supplier, "role", scope, fallback_of(named))
    else:
        pinned = _pin_slot(scoped, route.key, exists)
        if pinned is not None:
            return Choice(pinned, "", "route", "campaign", fallback_of(route.default_role))

    # 3. Campaign role: the role the route uses at global scope.
    used = _named_role(glob, route.key) or route.default_role
    campaign_role = _role_slot(scoped, used, exists)
    if campaign_role is not None:
        return Choice(campaign_role, used, "role", "campaign", fallback_of(used))

    # 4. Global route.
    pinned = _pin_slot(glob, route.key, exists)
    if pinned is not None:
        return Choice(pinned, "", "route", "global", fallback_of(route.default_role))

    # 5. Global role.
    got, supplier, scope = role_selection(used, campaign={}, glob=glob, exists=exists)
    if got is None:
        return Choice(None, "", "", "none", fallback_of(used))
    return Choice(got, supplier, "role", scope, fallback_of(used))


def _preset_opinion(view: dict, key: str, known: Callable[[str], bool]) -> str:
    """A preset id, `PRESET_CLEAR`, or "" for no opinion (absent, blank or
    dangling)."""
    value = str(view.get(key, "") or "").strip()
    if value == PRESET_CLEAR:
        return PRESET_CLEAR
    return value if value and known(value) else ""


def preset_for(route: routing.Route | None, selection: Selection | None, *,
               campaign: dict, glob: dict,
               known: Callable[[str], bool]) -> tuple[str, str]:
    """Spec 5.2: `(preset_id, scope)`. campaign `preset_<route>` -> global ->
    the selection's own preset -> none. `scope` is one of
    `sampler_presets.SCOPES`; "connection" means the selection's own preset.
    `PRESET_CLEAR` at a scope stops the walk with an empty id."""
    if route is not None:
        key = keys.preset_key(route.key)
        scopes = ([("campaign", campaign)] if route.campaign_scoped else []) \
            + [("global", glob)]
        for scope, view in scopes:
            chosen = _preset_opinion(view, key, known)
            if chosen == PRESET_CLEAR:
                return "", scope
            if chosen:
                return chosen, scope
    own = selection.preset.strip() if selection is not None else ""
    if own and known(own):
        return own, "connection"
    return "", "none"
