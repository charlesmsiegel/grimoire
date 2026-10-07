"""Resolve a task to the provider, model, preset and fallback it runs on.

The impure half of `store/inference/`: this reads `config.md`, the campaign's
`campaign.md`, the connection files, the sampler presets and the model-catalog
sidecars, and hands the pure pieces -- `translate` (the legacy layout read as
the current one) and `cascade` (spec §5.1, §5.2, §5.5) -- dicts and predicates. What comes back is a
`ResolvedInference`, whose attempts are lowered to the connection dict the
facade reads today (`{**connection, "model", "sampling", "model_params"}`), so
nothing downstream of a call site changes.

Behaviour-neutral by construction for every store the app can hold today: the
equivalence tests compare each answer with the route layer's own
(`routes.common`), over the frozen baseline's states.

Never imports `llm`: the one thing borrowed from it, a connection's effective
model, differs from the stored one only for `claude`, and `model_params`
consults the catalog for OpenRouter alone. The two facade rules the fallback
attempt mirrors (`llm.fallback_sampling`, `llm._same_route`) are restated here,
with `ROUTE_SCOPES`, and the tests hold them to the facade's own answers.

Each attempt also carries what slice B knows of it -- its provider's kind, URL,
rev, billing and preset, its model's facts, its effective controls
(`llm_sampling.effective` over the lowered connection), and every capability
with its source (`capabilities.resolve_caps`, fed the catalog row the lowering
already read, so a sidecar is read once per attempt) -- and the resolution says
which of the route's needs the primary and the fallback are known not to meet
(`missing`, `fallback_missing`). Nothing here refuses on them: the seam does.
"""

from __future__ import annotations

from collections.abc import Callable

from ... import llm_sampling
from .. import campaigns, config, llm_connections, locks, routing, sampler_presets
from . import capabilities, cascade, facts, providers, translate
from .cascade import Selection
from .resolved import Attempt, ResolvedInference

#: What a connection carries when no preset could be resolved for it at all.
NO_SAMPLING = {"preset_id": "", "preset_name": "", "scope": "none", "params": {}}

#: Where a sampler preset came from, for the scopes that are about the ROUTE
#: rather than the connection: a preset from one of these follows the route onto
#: the fallback. `llm.ROUTE_SCOPES`, restated because this module never imports
#: `llm`; a test holds the two equal.
ROUTE_SCOPES = frozenset({"campaign", "global"})

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
    if conn["kind"] == "anthropic" and not conn.get("api_key"):
        return "Anthropic API key not set"
    if conn["kind"] == "openai_compatible" and not conn.get("base_url"):
        return "Endpoint base URL not set"
    return None


def _catalog_row(conn: dict, model: str) -> dict | None:
    """`model`'s row in `conn`'s cached catalog, or None when there is none
    (`llm_connections.cached_row`, which never raises)."""
    return llm_connections.cached_row(conn.get("id", ""), model)


def _params_of(conn: dict, row: dict | None) -> list[str] | None:
    """`model_params` from an already-read catalog row (see `model_params`)."""
    if conn.get("kind", "openrouter") != "openrouter" or row is None:
        return None
    params = row.get("params")
    if not isinstance(params, list):
        return None
    return [x for x in params if isinstance(x, str)]


def model_params(conn: dict) -> list[str] | None:
    """The request parameters `conn`'s model takes, from its cached catalog, or
    None when that is not known.

    OpenRouter only, because it is the one provider whose catalog says
    (`supported_parameters`, kept by `catalog.entry` as `params`) -- and the one
    that forwards a parameter a model does not take for the model to ignore,
    which is the silent drop sampler presets exist to report. Read from the
    sidecar the model picker already fills, so this costs one small file and
    never a request; no cached catalog (or a malformed one) means
    `llm_sampling` says "unverified" rather than guessing.

    The model looked up is the stored one: OpenRouter's effective model IS its
    stored model (`llm.effective_model` substitutes only for `claude`).
    """
    if conn.get("kind", "openrouter") != "openrouter" or not conn.get("id"):
        return None
    return _params_of(conn, _catalog_row(conn, conn.get("model", "")))


def campaign_meta(cid: str) -> dict:
    """A campaign's frontmatter, for the routing walk -- {} for none, and for
    anything unreadable.

    Never raises. A missing or damaged `campaign.md` is a 404 (or a 500) on the
    routes that need the campaign itself, and every one of them has already said
    so by the time a connection is resolved; re-deciding it here would let a
    stale read turn "this campaign routes elsewhere" into a failed generation.
    """
    if not cid:
        return {}
    try:
        return campaigns.read_campaign(cid)["meta"]
    except (campaigns.CampaignNotFound, locks.StoreBusy, OSError, UnicodeDecodeError):
        return {}


# ---- the per-call caches ----
def _connection_lookup() -> translate.Lookup:
    """`read_connection_raw`, memoised for one resolution and never raising.

    One resolution asks about the same few ids several times over (the legacy
    translation looks up the roles and the task's route pins, the cascade
    checks existence), so
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


def _lowered(raw: dict, sampling: dict,
             model: str | None = None) -> tuple[dict, dict | None]:
    """`raw` as the facade reads it, and the catalog row that was read for it:
    `sampling` attached, `model` set (when given), and `model_params` and
    `model_features` recomputed for that model.

    A copy, never a mutation: `raw` can be the dict the store handed back. Any
    `model_params` / `model_features` already on it is dropped first -- it may
    belong to another model, and inheriting it would report a parameter the
    new model was never checked for as verified, in either direction."""
    out = {k: v for k, v in raw.items() if k not in ("model_params", "model_features")}
    if model is not None:
        out["model"] = model
    out["sampling"] = sampling
    row = _catalog_row(out, str(out.get("model", "") or ""))
    params = _params_of(out, row)
    if params is not None:
        out["model_params"] = params
    features = row.get("features") if row is not None else None
    if isinstance(features, dict):
        out["model_features"] = dict(features)
    return out, row


def lower(raw: dict, sampling: dict, model: str | None = None) -> dict:
    """`_lowered`'s connection dict alone: `raw` as the facade reads it, with
    `sampling` attached and `model` (when given) and its catalog facts set.
    Public for `controls.preview`, which lowers a preset the same way."""
    return _lowered(raw, sampling, model)[0]


def preset_sampling(preset_id: str, scope: str = "connection") -> dict:
    """The `sampling` block for the sampler preset `preset_id` at `scope` -- the
    shape `_sampling` gives an attempt; no preset ("") is provider defaults.
    Never raises: an unreadable preset is no preset."""
    if not preset_id:
        return dict(NO_SAMPLING)
    return _sampling(lambda _known: (preset_id, scope), _preset_lookup())


def _model_facts(provider_id: str, model: str, rev: str) -> dict:
    """`facts.of`, never raising: unreadable facts say nothing."""
    try:
        return facts.of(provider_id, model, rev)
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _attempt(provider_id: str, model: str, sampling: dict, raw: dict) -> Attempt:
    """One attempt: the lowered connection, and what is known of it.

    Capabilities are resolved from the catalog row the lowering read and the
    model's facts, once -- never by `capabilities.caps_for`, which would read
    the same sidecar again."""
    conn, row = _lowered(raw, sampling, model)
    preset = providers.infer(conn)
    rev = conn.get("rev", "")
    rev = rev if isinstance(rev, str) else ""
    model_facts = _model_facts(provider_id, model, rev)
    base_url = conn.get("base_url", "")
    return Attempt(
        provider_id, model, sampling["preset_id"], conn,
        provider_kind=str(conn.get("kind", "") or ""),
        base_url=(base_url if isinstance(base_url, str) and base_url else preset.base_url),
        rev=rev, billing=providers.billing(conn), provider_preset=preset.id,
        facts=model_facts,
        capabilities=capabilities.resolve_caps(preset, model, catalog_row=row,
                                               facts=model_facts),
        controls=llm_sampling.effective(conn))


#: The capability an operation needs of itself. `decide` is not an operation
#: the facade runs yet: a decide route still generates.
OPERATION_CAPABILITY: dict[str, str] = {"generate": "generate", "embed": "embed",
                                        "decide": "generate"}


def _needs(route: routing.Route | None, operation: str) -> frozenset[str]:
    """What an attempt must be able to do for this route and operation."""
    own = OPERATION_CAPABILITY.get(operation, "generate")
    return frozenset({own, *(route.requires if route is not None else ())})


#: The sources whose `no` is a guess rather than knowledge: the name rule
#: reads "embed" in an id, and a chat model can carry that word. Such a `no`
#: hides a model in a picker (`capabilities.group_for`) but is never missing,
#: so the seam never refuses on it.
_GUESSES = frozenset({"name"})


def _missing(attempt: Attempt, needs: frozenset[str]) -> tuple[str, ...]:
    """The needs `attempt` is known (`no`) not to meet, in `capabilities.NAMES`
    order. `unknown` is never missing, and neither is a guess (`_GUESSES`)."""
    known_no = {cap for cap, found in attempt.capabilities.items()
                if found.value == capabilities.NO and found.source not in _GUESSES}
    return tuple(cap for cap in capabilities.NAMES if cap in needs and cap in known_no)


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
    return lower(conn, _sampling(_own_preset(own), _preset_lookup()))


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


def resolve(task: str, cid: str = "", *, operation: str = "generate",
            override: Selection | None = None) -> ResolvedInference:
    """Where `task` runs, for campaign `cid` ("" for none), and what it falls
    back to.

    The connection files are migrated BEFORE `config.md` is read: a store from
    before named connections holds only a flat key and model, and migration is
    what seeds the active connection those resolve to. The campaign's
    frontmatter is read here too (`campaign_meta`), never raising: one that
    cannot be read has no routing opinion.

    Only what the task can reach is looked up: the roles, the fallback, and the
    pins of the task's own route (`translate`'s `only`). The layout is decided
    once, from `config.md` (spec 11.1).

    Nothing here refuses. A primary that cannot send is still the primary --
    the seam reports it (`problem`) rather than walking past it; no selection
    at all is an empty `attempts`.

    The fallback attempt is the one the facade sends (spec 5.2, 5.4, 5.5): it
    is dropped when it cannot be read or cannot send (so a misconfigured
    fallback never replaces the primary's real error) and when it names the
    primary's own connection (`llm._same_route`: a second try on the connection
    that just failed is not a fallback); and when the primary's preset came
    from a ROUTE scope -- campaign or global, a `PRESET_CLEAR` included -- the
    fallback carries that same sampling rather than its own preset
    (`llm.fallback_sampling`). Its `model_params` stay its own model's.
    """
    llm_connections.ensure_migrated()
    cfg = config.read_config()
    meta = campaign_meta(cid)
    lookup = _connection_lookup()
    presets = _preset_lookup()
    route = routing.route(task)
    only = (route.key,) if route is not None else ()
    glob = translate.global_view(cfg, lookup, only=only)
    campaign = translate.campaign_view(meta, lookup, current=translate.is_current(cfg),
                                       only=only)
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
        first = _attempt(primary.provider, primary.model, sampling, raw)
        conn = first.conn
        attempts.append(first)
        fallback = choice.fallback
        fb_raw = lookup(fallback.provider) if fallback is not None else None
        if (fallback is not None and fb_raw is not None and problem(fb_raw) is None
                and not _same_provider(conn, fb_raw)):
            # A copy, so the two attempts never share a mutable block.
            fb_sampling = ({**sampling, "params": dict(sampling["params"])}
                           if sampling["scope"] in ROUTE_SCOPES
                           else _sampling(_own_preset(fallback), presets))
            attempts.append(_attempt(fallback.provider, fallback.model, fb_sampling,
                                     fb_raw))

    needs = _needs(route, operation)
    return ResolvedInference(
        task=task, operation=operation,
        route=route.key if route is not None else "",
        legacy_route=routing.legacy_key(route) if route is not None else "",
        role=choice.role, via=choice.via, scope=choice.scope,
        attempts=tuple(attempts), standing=choice.selection,
        missing=_missing(attempts[0], needs) if attempts else (),
        fallback_missing=_missing(attempts[1], needs) if len(attempts) > 1 else ())


def _same_provider(primary: dict, fallback: dict) -> bool:
    """Whether the fallback is the primary's own connection, by store id --
    `llm._same_route`'s rule (two dicts read from disk are never the same
    object, so the id is what decides)."""
    pid = primary.get("id", "")
    return bool(pid) and pid == fallback.get("id", "")
