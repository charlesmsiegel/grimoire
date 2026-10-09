"""The one place the new inference settings' key names are spelled.

Everything that reads or writes a role, a route choice or the format marker
builds the key here, so a spelling cannot drift between the resolver, the
translation, `config.md`'s key list and (later) the migration.

A store-level leaf rather than a module of `store/inference/`: `config.py`
needs the key list, and `store/inference/__init__.py` imports `resolve`, which
imports `config` -- so `config` importing the package would initialise in a
circle. Imports nothing from the store but `routing`, itself a pure leaf.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from . import routing

ROLES: tuple[str, ...] = ("primary", "fast", "decision", "embedding")
GENERATIVE_ROLES: tuple[str, ...] = ("primary", "fast", "decision")

#: An unset role inherits from the one named here (Embedding inherits nothing).
INHERITS: dict[str, str] = {"fast": "primary", "decision": "fast"}

#: The `use_<route>` value that means "this route carries its own selection".
PIN = "model"
#: The fields of a selection.
PARTS: tuple[str, ...] = ("provider", "model", "preset")
#: The Embedding role's fields: no preset (an embedding call samples nothing)
#: and no fallback (embeddings never fall back, rule 4).
EMBEDDING_PARTS: tuple[str, ...] = ("provider", "model")

FORMAT_KEY = "inference_format"
CURRENT_FORMAT = "2"

#: The retirement marker (slice I, ruling 15): "1" on a `config.md` or a
#: `campaign.md` whose legacy keys retirement has removed, after which the
#: legacy layout is never read there again. Not part of `GLOBAL_KEYS`: the
#: migration does not own it, and nothing writes it before retirement does.
RETIRED_KEY = "inference_retired"

#: Set to "0" to keep the automatic layout switch off: the background
#: migration does not start. It gates that thread and nothing else -- a fresh
#: store is born at the current format either way (`born_current`). The suite
#: sets it in `tests/conftest.py` so nothing migrates behind a test's back.
AUTOMIGRATE_ENV = "GRIMOIRE_INFERENCE_AUTOMIGRATE"


def role_key(role: str, part: str) -> str:
    return f"role_{role}_{part}"


def fallback_key(role: str, part: str) -> str:
    return f"role_{role}_fallback_{part}"


def use_key(route_key: str) -> str:
    return f"use_{route_key}"


def pin_key(route_key: str, part: str) -> str:
    return f"use_{route_key}_{part}"


def preset_key(route_key: str) -> str:
    return f"preset_{route_key}"


def _role_keys() -> tuple[str, ...]:
    out = [role_key(r, p) for r in GENERATIVE_ROLES for p in PARTS]
    out += [fallback_key(r, p) for r in GENERATIVE_ROLES for p in PARTS]
    return tuple(out)


def route_keys(route: routing.Route) -> tuple[str, ...]:
    """Every key one route stores at a scope: its choice, its pin and its
    preset."""
    return (use_key(route.key), *(pin_key(route.key, p) for p in PARTS),
            preset_key(route.key))


#: Every new-layout key `config.md` may hold: the three generative roles and
#: their fallbacks, the Embedding role, each of the fifteen routes' choice, pin
#: and preset, and the format marker. The twelve legacy routes' `preset_<k>`
#: keys are spelled the same as before (`routing.PRESET_CONFIG_KEYS`): a
#: route's preset choice did not change meaning, so it did not change key.
GLOBAL_KEYS: tuple[str, ...] = (
    *_role_keys(),
    *(role_key("embedding", p) for p in EMBEDDING_PARTS),
    *(k for r in routing.ROUTES for k in route_keys(r)),
    FORMAT_KEY,
)

#: What a campaign may override: `GLOBAL_KEYS` without the Embedding role (one
#: library, one vector space), without the routes no campaign reaches, and
#: without the marker -- a caller never writes that as a field;
#: `campaigns.set_campaign_inference`, campaign creation and the migration
#: stamp it themselves.
CAMPAIGN_KEYS: tuple[str, ...] = (
    *_role_keys(),
    *(k for r in routing.ROUTES if r.campaign_scoped for k in route_keys(r)),
)

#: The baseline layout's inference keys in `config.md`, frozen for older builds
#: and refused on write once the store is current (spec 11.3).
LEGACY_GLOBAL_KEYS: tuple[str, ...] = (
    "active_connection_id", "fallback_connection_id",
    "embeddings_connection_id", "embeddings_model",
    *routing.CONFIG_KEYS,
)


def is_current(meta: Mapping) -> bool:
    """Whether `meta` (a `config.md` or `campaign.md`) carries the current
    format marker."""
    return str(meta.get(FORMAT_KEY, "")).strip() == CURRENT_FORMAT


def is_newer(meta: Mapping) -> bool:
    """Whether `meta` was written by a build newer than this one: its marker
    parses as a whole number above the current format. A malformed marker is
    not newer -- this build cannot know it means anything at all."""
    try:
        return int(str(meta.get(FORMAT_KEY, "")).strip()) > int(CURRENT_FORMAT)
    except (TypeError, ValueError):
        return False


def automigrate() -> bool:
    """Whether the background layout switch is on: unless `AUTOMIGRATE_ENV` is
    "0". Read on every call, so a test can flip it. It gates the background
    thread only; whether a new store is born current is `born_current`."""
    return os.environ.get(AUTOMIGRATE_ENV, "").strip() != "0"


def born_current() -> bool:
    """Whether a store this build creates from nothing starts at the current
    format (ruling 13): always. Not tied to `automigrate()`, which gates the
    background thread; product code never turns the birth off."""
    return True
