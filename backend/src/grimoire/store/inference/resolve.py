"""Resolve a task to the provider, model, preset and fallback it runs on.

The impure half of `store/inference/`: this reads `config.md`, the connection
files, the sampler presets and the model-catalog sidecars, and hands the pure
pieces -- `translate` (the legacy layout read as the current one) and
`cascade` (spec §5.1, §5.2, §5.5) -- dicts and predicates. What comes back is a
`ResolvedInference`, whose attempts are lowered to the connection dict the
facade reads today (`{**connection, "model", "sampling", "model_params"}`), so
nothing downstream of a call site changes.

Behaviour-neutral by construction for every store the app can hold today: the
equivalence tests compare each answer with the route layer's own
(`routes.common`), over the frozen baseline's states.

Never imports `llm`: the one thing borrowed from it, a connection's effective
model, differs from the stored one only for `claude`, and `model_params`
consults the catalog for OpenRouter alone.
"""

from __future__ import annotations

from collections.abc import Callable

from .. import config, llm_connections, locks, routing, sampler_presets
from . import cascade, translate
from .cascade import Selection
from .resolved import Attempt, ResolvedInference

#: What a connection carries when no preset could be resolved for it at all.
NO_SAMPLING = {"preset_id": "", "preset_name": "", "scope": "none", "params": {}}

#: The ways reading one connection can fail, every one of which reads as "no
#: such connection" -- a dangling reference is walked past, never raised.
_UNREADABLE = (llm_connections.ConnectionNotFound, locks.StoreBusy,
               OSError, UnicodeDecodeError)


def problem(conn: dict) -> str | None:
    """Why this connection cannot send, or None if it can.

    The credential check the seam turns into a 409 and the fallback turns into
    "there is no fallback" -- one function, because a fallback that is
    silently unusable is exactly the failure a fallback exists to prevent, and
    two copies of this rule would drift.
    """
    if conn["kind"] == "openrouter" and not conn.get("api_key"):
        return "OpenRouter key not set"
    if conn["kind"] == "openai_compatible" and not conn.get("base_url"):
        return "Endpoint base URL not set"
    return None


def model_params(conn: dict) -> list[str] | None:
    """The request parameters `conn`'s model takes, from its cached catalog, or
    None when that is not known.

    OpenRouter only, because it is the one provider whose catalog says
    (`supported_parameters`, kept by `catalog.entry` as `params`) -- and the one
    that forwards a parameter a model does not take for the model to ignore,
    which is the silent drop sampler presets exist to report. Read from the
    sidecar the model picker already fills, so this costs one small file and
    never a request; no cached catalog means `llm_sampling` says "unverified"
    rather than guessing.

    The model looked up is the stored one: OpenRouter's effective model IS its
    stored model (`llm.effective_model` substitutes only for `claude`).
    """
    if conn.get("kind", "openrouter") != "openrouter" or not conn.get("id"):
        return None
    try:
        models = llm_connections.cached_models(conn["id"])["models"]
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        # Belt and braces: `cached_models` validates the sidecar's shape, and
        # this runs on every OpenRouter turn, so whatever slips past that costs
        # the catalog, never the turn.
        return None
    # The sidecar is a file a sync or a hand can mangle: a malformed one reads
    # as "no catalog" (unverified), never as an exception that fails the turn.
    if not isinstance(models, list):
        return None
    model = conn.get("model", "")
    for entry in models:
        if isinstance(entry, dict) and entry.get("id") == model:
            params = entry.get("params")
            if not isinstance(params, list):
                return None
            return [x for x in params if isinstance(x, str)]
    return None


# ---- the per-call caches ----
def _connection_lookup() -> translate.Lookup:
    """`read_connection_raw`, memoised for one resolution and never raising.

    One resolution asks about the same few ids many times over (the legacy
    translation looks up every route's pin, the cascade checks existence), so
    each file is read at most once -- which also means every question in one
    resolution is answered from the same read."""
    seen: dict[str, dict | None] = {}

    def lookup(conn_id: str) -> dict | None:
        if conn_id not in seen:
            try:
                seen[conn_id] = llm_connections.read_connection_raw(conn_id)
            except _UNREADABLE:
                seen[conn_id] = None
        return seen[conn_id]

    return lookup


def _preset_lookup() -> Callable[[str], dict | None]:
    """`sampler_presets.read_preset`, memoised for one resolution."""
    seen: dict[str, dict | None] = {}

    def lookup(pid: str) -> dict | None:
        if pid not in seen:
            seen[pid] = sampler_presets.read_preset(pid)
        return seen[pid]

    return lookup


# ---- lowering ----
def _sampling(choose: Callable[[Callable[[str], bool]], tuple[str, str]],
              presets: Callable[[str], dict | None]) -> dict:
    """`{preset_id, preset_name, scope, params}` for the preset `choose` picks.

    Never raises: a preset that cannot be read is no preset, and the report
    the reader sees says "provider defaults" -- which is then the truth about
    what was sent."""
    try:
        preset_id, scope = choose(lambda pid: presets(pid) is not None)
        preset = presets(preset_id) if preset_id else None
    except (locks.StoreBusy, OSError, UnicodeDecodeError):
        return dict(NO_SAMPLING)
    return {"preset_id": preset_id,
            "preset_name": preset["name"] if preset else "",
            "scope": scope,
            "params": dict(preset["params"]) if preset else {}}


def _lower(raw: dict, sampling: dict, model: str | None = None) -> dict:
    """`raw` as the facade reads it: `sampling` attached, `model` set (when
    given), and `model_params` recomputed for that model.

    A copy, never a mutation: `raw` can be the dict the store handed back. Any
    `model_params` already on it is dropped first -- it may belong to another
    model, and inheriting it would report a parameter the new model was never
    checked for as verified, in either direction."""
    out = {k: v for k, v in raw.items() if k != "model_params"}
    if model is not None:
        out["model"] = model
    out["sampling"] = sampling
    params = model_params(out)
    if params is not None:
        out["model_params"] = params
    return out


def _own_preset(selection: Selection) -> Callable[[Callable[[str], bool]], tuple[str, str]]:
    """The preset choice for a selection with no route: its own, or none."""
    return lambda known: cascade.preset_for(None, selection, campaign={}, glob={},
                                            known=known)


def own_sampling(conn: dict) -> dict:
    """`conn` with its OWN sampler preset (scope `connection`, or `none`) and its
    model's `model_params` attached -- what a connection carries outside any
    route: the standing fallback, and the connection list's display."""
    own = Selection(str(conn.get("id", "") or ""), str(conn.get("model", "") or ""),
                    str(conn.get("sampler_preset", "") or ""))
    return _lower(conn, _sampling(_own_preset(own), _preset_lookup()))


# ---- the resolver ----
def _overridden(standing: Selection | None, override: Selection | None,
                lookup: translate.Lookup) -> Selection | None:
    """The selection one call runs on, given a per-call override (#77).

    A provider alone runs that provider at its own model and preset, and needs
    no standing selection -- rerolling onto a working endpoint is how a broken
    standing route is fixed. A model alone drives the STANDING provider at that
    model, so with no standing selection there is nothing to drive. Both is the
    named provider at the named model. A preset in the override replaces the
    selection's own; empty keeps it."""
    if override is None or not (override.provider or override.model):
        return standing
    if override.provider:
        raw = lookup(override.provider)
        if raw is None:
            return None
        base = Selection(override.provider, str(raw.get("model") or ""),
                         str(raw.get("sampler_preset") or ""))
    elif standing is None:
        return None
    else:
        base = standing
    return Selection(base.provider, override.model or base.model,
                     override.preset or base.preset)


def resolve(task: str, *, campaign_meta: dict, operation: str = "generate",
            override: Selection | None = None) -> ResolvedInference:
    """Where `task` runs, for a campaign whose frontmatter is `campaign_meta`
    (`{}` for none), and what it falls back to.

    The connection files are migrated BEFORE `config.md` is read: a store from
    before named connections holds only a flat key and model, and migration is
    what seeds the active connection those resolve to.

    Nothing here refuses. A primary that cannot send is still the primary --
    the seam reports it (`problem`) rather than walking past it; no selection
    at all is an empty `attempts`. The fallback is dropped when it cannot be
    read or cannot send, so a misconfigured fallback never replaces the
    primary's real error.
    """
    llm_connections.ensure_migrated()
    cfg = config.read_config()
    lookup = _connection_lookup()
    presets = _preset_lookup()
    route = routing.route(task)
    glob = translate.global_view(cfg, lookup)
    campaign = translate.campaign_view(campaign_meta, lookup)
    choice = cascade.choose(route, campaign=campaign, glob=glob,
                            exists=lambda conn_id: lookup(conn_id) is not None)

    attempts: list[Attempt] = []
    selection = _overridden(choice.selection, override, lookup)
    raw = lookup(selection.provider) if selection is not None else None
    if selection is not None and raw is not None:
        primary = selection
        sampling = _sampling(
            lambda known: cascade.preset_for(route, primary, campaign=campaign,
                                             glob=glob, known=known), presets)
        attempts.append(Attempt(primary.provider, primary.model, sampling["preset_id"],
                                _lower(raw, sampling, primary.model)))
        fallback = choice.fallback
        fb_raw = lookup(fallback.provider) if fallback is not None else None
        if fallback is not None and fb_raw is not None and problem(fb_raw) is None:
            # The fallback brings its own preset (spec §5.2); a route's preset
            # is carried onto it by the facade (`llm.fallback_sampling`).
            fb_sampling = _sampling(_own_preset(fallback), presets)
            attempts.append(Attempt(fallback.provider, fallback.model,
                                    fb_sampling["preset_id"],
                                    _lower(fb_raw, fb_sampling, fallback.model)))

    return ResolvedInference(
        task=task, operation=operation,
        route=route.key if route is not None else "",
        legacy_route=routing.legacy_key(route) if route is not None else "",
        role=choice.role, via=choice.via, scope=choice.scope,
        attempts=tuple(attempts), standing=choice.selection)
