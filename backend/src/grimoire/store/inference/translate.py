"""The legacy settings layout, read as the new one.

A store written before the roles format keeps `active_connection_id`,
`fallback_connection_id`, `embeddings_*`, and one `route_<k>` / `preset_<k>`
pair per legacy route. `global_view` and `campaign_view` answer what those
keys *mean* in the new vocabulary -- roles and pins -- so the resolver reads
one layout whichever a store holds. Both are pure: dicts and a lookup in, a
dict out. A store already at the current format is returned as it came.

Trim rule, kept from the legacy cascade: `route_*`, `preset_*` and
`embeddings_*` values are stripped; `active_connection_id` and
`fallback_connection_id` are used raw (so a padded id is later rejected by the
existence check exactly as it always was). A route naming a connection that
does not exist is still translated, to a pin on that id: walking past a
dangling choice is the cascade's job, not this module's.

Emitted key names come from `keys` only.
"""

from __future__ import annotations

from collections.abc import Callable

from .. import routing
from . import keys

#: A raw connection by id, or None. A lookup must never raise.
Lookup = Callable[[str], dict | None]


def is_current(meta: dict) -> bool:
    return str(meta.get(keys.FORMAT_KEY, "")).strip() == keys.CURRENT_FORMAT


def _selection(conn_id: str, conn: Lookup) -> dict[str, str]:
    """`{provider, model, preset}` for a connection id (model and preset are
    empty when the connection is unknown or sets none)."""
    raw = conn(conn_id) or {}
    return {
        "provider": conn_id,
        "model": str(raw.get("model") or ""),
        "preset": str(raw.get("sampler_preset") or ""),
    }


def _pins_and_presets(meta: dict, conn: Lookup, scoped_only: bool) -> dict:
    out: dict = {}
    for route in routing.ROUTES:
        if scoped_only and not route.campaign_scoped:
            continue
        legacy = routing.legacy_key(route)
        chosen = str(meta.get(routing.config_key(legacy), "") or "").strip()
        if chosen:
            out[keys.use_key(route.key)] = keys.PIN
            sel = _selection(chosen, conn)
            for part in keys.PARTS:
                out[keys.pin_key(route.key, part)] = sel[part]
        preset = str(meta.get(routing.preset_key(legacy), "") or "").strip()
        if preset:
            out[keys.preset_key(route.key)] = preset
    return out


def embedding_role(cfg: dict) -> tuple[str, str]:
    """`(provider, model)` of the embedding role. No lookup: the embedding
    model is read from config alone."""
    if is_current(cfg):
        provider = cfg.get(keys.role_key("embedding", "provider"), "")
        model = cfg.get(keys.role_key("embedding", "model"), "")
    else:
        provider = cfg.get("embeddings_connection_id", "")
        model = cfg.get("embeddings_model", "")
    return str(provider or "").strip(), str(model or "").strip()


def global_view(cfg: dict, conn: Lookup) -> dict:
    """The global settings in the current layout."""
    if is_current(cfg):
        return cfg
    out: dict = {}
    active = str(cfg.get("active_connection_id", "") or "")
    if active:
        for part, value in _selection(active, conn).items():
            out[keys.role_key("primary", part)] = value
    fallback = str(cfg.get("fallback_connection_id", "") or "")
    if fallback:
        sel = _selection(fallback, conn)
        for role in keys.GENERATIVE_ROLES:
            for part, value in sel.items():
                out[keys.fallback_key(role, part)] = value
    provider, model = embedding_role(cfg)
    if provider:
        out[keys.role_key("embedding", "provider")] = provider
        out[keys.role_key("embedding", "model")] = model
    out.update(_pins_and_presets(cfg, conn, scoped_only=False))
    return out


def campaign_view(meta: dict, conn: Lookup) -> dict:
    """A campaign's overrides in the current layout. A campaign carried only
    route and preset choices, for the campaign-scoped routes, so that is all
    there is to translate."""
    if is_current(meta):
        return meta
    return _pins_and_presets(meta, conn, scoped_only=True)
