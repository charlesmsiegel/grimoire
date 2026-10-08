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
with `ROUTE_SCOPES` and `FALLBACK_KEY`, and the tests hold them to the facade's
own answers.

The fallback the facade sends is the one resolved here (slice C): the
primary's lowered dict carries the fallback attempt's under `FALLBACK_KEY`,
unless that fallback is known unable to do what the route needs (spec 5.3) --
then it is reported (`fallback_missing`) and not attached. On a decide
resolution, each attempt whose `structured_output` is `yes` is flagged on its
own dict (`STRUCTURED_KEY`, slice F), which is how the facade asks that
attempt, and only that one, for its provider's structured mode. Each attempt
of a decide resolution also says which backend would answer it
(`decision_mode`), and a primary known unable to generate is skipped for a
fallback that can (`ResolvedInference.skipped`, spec 5.5) -- until slice H's
native backend can answer it.

Each attempt's dict carries an account block (`ACCOUNT_KEY`): what the ledger
files about the attempt that the wire does not say -- its `billing`, its
`operation` and the `role` whose slot supplied it (`_account`).

Each attempt also carries what slice B knows of it -- its provider's kind, URL,
rev, billing and preset, its model's facts, its effective controls
(`llm_sampling.effective` over the lowered connection), and every capability
with its source (`capabilities.resolve_caps`, fed the catalog row the lowering
already read, so a sidecar is read once per attempt) -- and the resolution says
which of the route's needs the primary and the fallback are known not to meet
(`missing`, `fallback_missing`). `resolve` refuses on none of it; whether the
seam serves a resolution is `refusal`, a pure function of it, which the seam
raises from and the settings view reports -- one decision, not two copies.

At format 2 the model's facts drive its behaviour: the lowering overlays the
facts' `vision`, `prefill` and `post_process` onto the connection dict
(`with_facts`), replacing the connection's legacy fields, so every consumer of
that dict answers per model unchanged. At format 1 the legacy fields stand.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import NamedTuple

from ... import llm_sampling
from .. import (
    campaigns,
    config,
    image_drafts,
    llm_connections,
    locks,
    routing,
    sampler_presets,
)
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

#: Where the primary's connection dict carries the fallback the facade sends
#: (`llm.FALLBACK_KEY`, restated for the same reason; a test holds them equal).
FALLBACK_KEY = "_fallback"

#: Why a fallback on the primary's own provider is left out of `attempts`
#: (`fallback_problem`): a second try on the connection that just failed is not
#: a fallback (`llm._same_route`). A name of its own, so a route that may come
#: to reach such a fallback -- a decide-only skip -- can lift this reason where
#: it lifts the drop, and leave the credential ones (`problem`) alone.
SAME_PROVIDER = "it is on the primary's own provider"

#: Where an attempt's connection dict says it may be asked for structured
#: output (`llm.STRUCTURED_KEY`, restated for the same reason; a test holds them
#: equal). Set only on a decide resolution's attempts whose `structured_output`
#: is `yes`, so a generate resolution's dicts are what they were before it.
STRUCTURED_KEY = "_structured"

#: Where a lowered connection dict carries its account block -- what the
#: ledger files about an attempt that the wire does not say: its `billing`
#: (stamped on every lowered dict), and, on a resolved attempt, the
#: `operation` and the `role` whose slot supplied it (spec 9.3).
#: `llm_usage.ACCOUNT_KEY`, restated for the same reason; a test holds them
#: equal. A block is never written in place (`llm_usage.with_account`): every
#: `{**conn}` copy shares it.
ACCOUNT_KEY = "_account"

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
def connection_lookup() -> translate.Lookup:
    """`read_connection_raw`, memoised for one resolution (or one settings
    view, `settings.view`) and never raising.

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


def _lowered(raw: dict, sampling: dict, model: str | None = None, *,
             catalog: bool = True) -> tuple[dict, dict | None]:
    """`raw` as the facade reads it, and the catalog row that was read for it:
    `sampling` attached, `model` set (when given), and `model_params` and
    `model_features` recomputed for that model. `catalog` False reads no row
    (`embed_attempt`: a record whose rev the cached catalog is not for).

    A copy, never a mutation: `raw` can be the dict the store handed back. Any
    `model_params` / `model_features` already on it is dropped first -- it may
    belong to another model, and inheriting it would report a parameter the
    new model was never checked for as verified, in either direction."""
    out = {k: v for k, v in raw.items() if k not in ("model_params", "model_features")}
    if model is not None:
        out["model"] = model
    out["sampling"] = sampling
    # A fresh block per lowering, never `raw`'s: `resolve` replaces it whole
    # with what the resolution knows, and `lower`'s callers (the model test,
    # `controls.preview`) carry the billing alone.
    out[ACCOUNT_KEY] = {"billing": providers.billing(out)}
    row = _catalog_row(out, str(out.get("model", "") or "")) if catalog else None
    params = _params_of(out, row)
    if params is not None:
        out["model_params"] = params
    features = row.get("features") if row is not None else None
    if isinstance(features, dict):
        out["model_features"] = dict(features)
    return out, row


#: What an unstated fact lowers to (spec 4.2): post images on auto, no
#: prefill, no post-processing -- the defaults a legacy connection carried.
_UNSTATED = {"vision": "", "prefill": False, "post_process": "none"}


def with_facts(conn: dict, model_facts: dict) -> dict:
    """`conn` with its model's stated behaviour -- `vision`, `prefill` and
    `post_process` -- taken from `model_facts` (`facts.of`) rather than the
    connection's legacy fields. A copy.

    The format-2 overlay. Every consumer reads these three off the connection
    dict -- `llm.prefill_capable`, the strict post-processing, and
    `post_images.capability` -- so replacing the values here is what makes them
    per model without touching any of them. An unstated fact is the default
    (`_UNSTATED`), never the connection's flag: a model nothing was said of
    runs as one nothing was said of.

    Two legacy fields are deliberately left as they are. `sampler_preset`: the
    selection's preset already comes from the cascade, never from here. And
    `reasoning_effort`: the GLM effort keeps riding when the effective preset
    sets none, until derived reasoning presets replace it (slice I).

    `vision` is the post-image preference and nothing more: `off` stops post
    images here and is NOT a capability `no` (`capabilities._stated`), so it
    never refuses an image description."""
    vision = model_facts.get("vision")
    prefill = model_facts.get("prefill")
    post_process = model_facts.get("post_process")
    return {**conn,
            "vision": vision if isinstance(vision, str) else _UNSTATED["vision"],
            "prefill": prefill if isinstance(prefill, bool) else _UNSTATED["prefill"],
            # Only a value the facts module would write: a hand-edited sidecar
            # lowers to the default rather than to a string nothing reads.
            "post_process": (post_process if post_process in facts.POST_PROCESS_VALUES
                             and post_process else _UNSTATED["post_process"])}


def _current_layout() -> bool:
    """Whether the store's global layout is the current one (format 2). Never
    raises: a `config.md` that cannot be read leaves the connection's own
    fields, which a migration keeps in place."""
    try:
        return translate.is_current(config.read_config())
    except (locks.StoreBusy, OSError, UnicodeDecodeError):
        return False


def lower(raw: dict, sampling: dict, model: str | None = None, *,
          current: bool | None = None) -> dict:
    """`_lowered`'s connection dict alone: `raw` as the facade reads it, with
    `sampling` attached and `model` (when given) and its catalog facts set --
    and, at format 2, its model's stated behaviour (`with_facts`). `current`
    is the layout, read from `config.md` when not given.

    Public for `controls.preview` and the model test, which lower a preset the
    same way, and for `own_sampling` (a connection outside any route): at
    format 2 each sends the model's own facts, as a resolved attempt does."""
    conn = _lowered(raw, sampling, model)[0]
    if not (_current_layout() if current is None else current):
        return conn
    return with_facts(conn, _model_facts(str(conn.get("id", "") or ""),
                                         facts.model_of(conn), _rev(conn)))


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


def _rev(conn: dict) -> str:
    rev = conn.get("rev", "")
    return rev if isinstance(rev, str) else ""


def _attempt(provider_id: str, model: str, sampling: dict, raw: dict, *,
             current: bool, retries: int = 0, catalog: bool = True,
             model_facts: dict | None = None) -> Attempt:
    """One attempt: the lowered connection, and what is known of it.

    Capabilities are resolved from the catalog row the lowering read and the
    model's facts, once -- never by `capabilities.caps_for`, which would read
    the same sidecar again. The facts are read under the model the attempt
    runs (`facts.model_of`: an unset Claude model is `opus`, where the
    migration wrote them).

    At format 2 (`current`) those same facts are overlaid onto the connection
    (`with_facts`), so an attempt carries ITS model's prefill, post-processing
    and post-image preference. A reroll onto another model therefore runs with
    that model's facts, not the connection's -- per-model facts are the design
    (spec 4.2). The baseline's override cells observe neither prefill nor
    vision, so nothing frozen records the difference.

    `model_facts` stands in for the read (`facts.of`'s shape): facts not yet
    written, which a facts write's guard judges (`embed_attempt`)."""
    conn, row = _lowered(raw, sampling, model, catalog=catalog)
    preset = providers.infer(conn)
    rev = _rev(conn)
    if model_facts is None:
        model_facts = _model_facts(provider_id, facts.model_of(conn), rev)
    if current:
        conn = with_facts(conn, model_facts)
    base_url = conn.get("base_url", "")
    return Attempt(
        provider_id, model, sampling["preset_id"], conn,
        provider_kind=str(conn.get("kind", "") or ""),
        base_url=(base_url if isinstance(base_url, str) and base_url else preset.base_url),
        rev=rev, billing=providers.billing(conn), provider_preset=preset.id,
        facts=model_facts,
        capabilities=capabilities.resolve_caps(preset, model, catalog_row=row,
                                               facts=model_facts),
        controls=llm_sampling.effective(conn), retries=retries)


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
    route: the connection editor's display. (A resolved fallback brings its own
    preset through the cascade instead, `resolve`.)"""
    own = Selection(str(conn.get("id", "") or ""), str(conn.get("model", "") or ""),
                    str(conn.get("sampler_preset", "") or ""))
    return lower(conn, _sampling(_own_preset(own), _preset_lookup()))


# ---- the resolver ----
def _overridden(standing: Selection | None, override: Selection | None,
                lookup: translate.Lookup, *, current: bool = False) -> Selection | None:
    """The selection one call runs on, given a per-call override (#77).

    A model alone drives the STANDING provider at that model, so with no
    standing selection there is nothing to drive. Both is the named provider at
    the named model. A provider alone is the one part that depends on the
    layout:

    - legacy keys: that provider at its OWN model and preset, needing no
      standing selection -- rerolling onto a working endpoint is how a broken
      standing route is fixed.
    - current layout (spec 5.6): that provider at the STANDING model and
      preset. A provider no longer has a model of its own, so what is kept is
      the part of the selection the override did not name. With no standing
      selection there is nothing to keep, and the model is left empty for the
      seam to ask the caller for.

    The override's preset is never merged into the selection: it outranks the
    route's preset, which no selection's own can, so `resolve` applies it on
    top of the cascade -- and only on a current layout, since a legacy store
    never took a preset from a request."""
    if override is None or not (override.provider or override.model):
        return standing
    if override.provider:
        raw = lookup(override.provider)
        if raw is None:
            return None
        if not current:
            base = Selection(override.provider, str(raw.get("model") or ""),
                             str(raw.get("sampler_preset") or ""))
        elif standing is not None and standing.provider == override.provider:
            base = standing
        elif standing is not None:
            base = Selection(override.provider, standing.model, standing.preset)
        else:
            base = Selection(override.provider, "", "")
    elif standing is None:
        return None
    else:
        base = standing
    return Selection(base.provider, override.model or base.model, base.preset)


class Silence(NamedTuple):
    """One scope's own choice left out of a resolution: what that scope's row
    would run on if it were cleared (`settings.view`'s `inherits`).

    The keys are dropped from that scope's view AFTER the legacy translation,
    so a legacy `route_<k>` is silenced as the pin it reads as."""

    #: "global" or "campaign".
    scope: str
    keys: frozenset[str]


def _silenced(silence: Silence | None, glob: dict, campaign: dict) -> tuple[dict, dict]:
    """`(glob, campaign)` with `silence`'s keys removed from its scope."""
    if silence is None:
        return glob, campaign

    def drop(view: dict) -> dict:
        return {k: v for k, v in view.items() if k not in silence.keys}

    if silence.scope == "campaign":
        return glob, drop(campaign)
    return drop(glob), campaign


def _refuse_embed(task: str, operation: str, role: str) -> None:
    """ValueError for embed work asked of `resolve` (see there): an embed
    operation, an embed task, or the Embedding role."""
    if operation == "embed" or task in routing.EMBED_TASKS or role == "embedding":
        raise ValueError("embed work resolves through resolve.embedding, "
                         f"not resolve (task={task!r}, operation={operation!r}, "
                         f"role={role!r})")


def resolve(task: str, cid: str = "", *, operation: str = "generate",
            override: Selection | None = None, role: str = "",
            silence: Silence | None = None) -> ResolvedInference:
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

    The fallback attempt is the one the facade sends (spec 5.2, 5.4, 5.5),
    attached to the primary's connection dict under `FALLBACK_KEY`: it is
    dropped when it cannot be read or cannot send (so a misconfigured fallback
    never replaces the primary's real error) and when it names the primary's
    own connection (`llm._same_route`: a second try on the connection that just
    failed is not a fallback; kept only where the decide skip lands on it,
    whose primary is never sent) -- either says why in `fallback_problem`
    (`problem`'s reason, or `SAME_PROVIDER`), which nothing refuses on; and
    when the route has a
    preset -- campaign or global scope, a `PRESET_CLEAR` included -- the
    fallback carries that same sampling rather than its own preset
    (`llm.fallback_sampling`). Its
    `model_params` stay its own model's. A fallback KNOWN unable to do what
    the route needs is kept in `attempts` and reported (`fallback_missing`) but
    not attached, so it is never sent (spec 5.3).

    A per-call preset (`override.preset`, scope `override`) is the primary's
    alone. The fallback gets what it would have had without it: the route's
    preset when the standing route has one, else the fallback's own.

    The primary attempt states the retry budget (`config.llm_retries`, read
    from the `config.md` already in hand); the fallback's is 0 (spec 5.4).

    Two arguments are for the settings view rather than a call site: `role`
    resolves that ROLE (`cascade.choose_role`) instead of `task`'s route, and
    `silence` leaves one scope's own choice out (`Silence`).

    Embed work is not this function's: an embed operation, an embed task
    (`routing.EMBED_TASKS`) or the Embedding role raises ValueError before
    anything is read. Each would otherwise resolve -- an unknown task to the
    Primary role, the role through `choose_role` -- and embed through a
    resolution that knows nothing of vector spaces. `embedding` is the
    Embedding role's one entry point.
    """
    _refuse_embed(task, operation, role)
    llm_connections.ensure_migrated()
    cfg = config.read_config()
    meta = campaign_meta(cid)
    lookup = connection_lookup()
    presets = _preset_lookup()
    route = None if role else routing.route(task)
    only = (route.key,) if route is not None else ()
    glob = translate.global_view(cfg, lookup, only=only)
    current = translate.is_current(cfg)
    campaign = translate.campaign_view(meta, lookup, current=current,
                                       only=only)
    glob, campaign = _silenced(silence, glob, campaign)

    def exists(conn_id: str) -> bool:
        return lookup(conn_id) is not None

    choice = (cascade.choose_role(role, campaign=campaign, glob=glob, exists=exists) if role
              else cascade.choose(route, campaign=campaign, glob=glob, exists=exists))

    # A preset in the override outranks the route's (spec 5.6) -- the most
    # specific choice there is -- on either layout: the legacy one lowers a
    # named preset exactly as the new one does, and a store whose migration
    # keeps failing stays legacy for as long as it fails.
    preset_override = override.preset.strip() if override is not None else ""

    def cascaded(selection: Selection | None) -> Callable[[Callable[[str], bool]],
                                                          tuple[str, str]]:
        return lambda known: cascade.preset_for(route, selection, campaign=campaign,
                                                glob=glob, known=known)

    def overriding(known: Callable[[str], bool], selection: Selection | None
                   ) -> tuple[str, str]:
        if preset_override == sampler_presets.PRESET_CLEAR:
            return "", "override"
        if preset_override and known(preset_override):
            return preset_override, "override"
        # An override naming no preset is no override of the preset: the seam
        # tells the two apart by the scope and refuses the body.
        return cascaded(selection)(known)

    attempts: list[Attempt] = []
    fallback_problem: str | None = None
    selection = _overridden(choice.selection, override, lookup, current=current)
    raw = lookup(selection.provider) if selection is not None else None
    standing_preset: str | None = None
    if selection is not None and raw is not None:
        primary = selection
        # `unforced`: what the cascade answers WITHOUT a per-call preset -- the
        # sampling the fallback follows when it came from a route scope. Under
        # an override preset it is the standing route's answer (a route-scoped
        # preset does not depend on the selection), and the override is the
        # primary's alone.
        if preset_override:
            unforced = _sampling(cascaded(choice.selection), presets)
            standing_preset = unforced["preset_id"]
            sampling = _sampling(lambda known: overriding(known, primary), presets)
        else:
            sampling = unforced = _sampling(cascaded(primary), presets)
        first = _attempt(primary.provider, primary.model, sampling, raw,
                         current=current, retries=config.llm_retries(cfg))
        conn = first.conn
        attempts.append(first)
        fallback = choice.fallback
        fb_raw = lookup(fallback.provider) if fallback is not None else None
        # A fallback on the primary's own provider is a retry (#144), which
        # the retry budget already covers -- except for the decide skip,
        # whose primary is never sent: there the fallback is the only call,
        # so it is built and `resolve` keeps it only if the skip lands on it.
        # The reason is lifted exactly where the drop is (`SAME_PROVIDER`).
        fallback_problem = (problem(fb_raw) if fb_raw is not None and _skippable(first, operation)
                            else _fallback_problem(conn, fb_raw))
        if fallback is not None and fb_raw is not None and fallback_problem is None:
            # A copy, so the two attempts never share a mutable block.
            fb_sampling = ({**unforced, "params": dict(unforced["params"])}
                           if unforced["scope"] in ROUTE_SCOPES
                           else _sampling(_own_preset(fallback), presets))
            attempts.append(_attempt(fallback.provider, fallback.model, fb_sampling,
                                     fb_raw, current=current))

    tail = _chain(attempts, operation, _needs(route, operation), current=current,
                  choice=choice, selection=selection)
    if (len(tail.attempts) > 1 and not tail.skipped
            and _same_provider(tail.attempts[0].conn, tail.attempts[1].conn)):
        # Built for a skip that did not land (the fallback cannot answer
        # either): a retry again, dropped as it always was, and said so.
        tail = tail._replace(attempts=tail.attempts[:1], fallback_missing=())
        tail.attempts[0].conn.pop(FALLBACK_KEY, None)
        fallback_problem = SAME_PROVIDER
    return ResolvedInference(
        task=task, operation=operation,
        route=route.key if route is not None else "",
        legacy_route=routing.legacy_key(route) if route is not None else "",
        role=choice.role, via=choice.via, scope=choice.scope,
        attempts=tail.attempts, standing=choice.selection, current=current,
        standing_preset=standing_preset, missing=tail.missing,
        fallback_missing=tail.fallback_missing, fallback_problem=fallback_problem,
        skipped=tail.skipped)


class _Tail(NamedTuple):
    """What `_chain` settles about a resolution's attempts."""

    attempts: tuple[Attempt, ...]
    missing: tuple[str, ...]
    fallback_missing: tuple[str, ...]
    skipped: tuple[str, ...]


def _chain(attempts: list[Attempt], operation: str, needs: frozenset[str], *,
           current: bool, choice: cascade.Choice, selection: Selection | None) -> _Tail:
    """The tail of `resolve`: stamp each attempt's account block, flag the
    structured-capable ones, say what each is known to lack, apply the decide
    skip, attach the fallback the facade sends, and give a decide
    resolution's attempts their `decision_mode`."""
    # Stamped BEFORE the fallback is attached, so the dict the facade sends
    # is the stamped one.
    _account(attempts, operation, choice, selection)
    _flag_structured(attempts, operation)
    # The "Images: on" bridge applies to a format-1 fallback as it does to the
    # primary (`_bridged`): what the legacy layout sent, it still sends.
    fallback_missing = (_bridged(_missing(attempts[1], needs), attempts[1], current=current)
                        if len(attempts) > 1 else ())
    missing = _missing(attempts[0], needs) if attempts else ()
    skipped = _skipped(attempts, operation, missing, needs)
    if skipped:
        missing = ()
    elif len(attempts) > 1 and not fallback_missing:
        # The facade sends what the primary's dict carries (spec 5.3: a
        # fallback known incapable is dropped from the chain, never sent). The
        # dict is this resolution's own copy (`_lowered`), so nothing the store
        # handed back is touched.
        attempts[0].conn[FALLBACK_KEY] = attempts[1].conn
    if operation == "decide":
        # The same dict objects, so the attach above still names them.
        attempts = [dataclasses.replace(a, decision_mode=decision_mode(a)) for a in attempts]
    return _Tail(tuple(attempts), missing, fallback_missing, skipped)


def decision_mode(attempt: Attempt) -> str:
    """The backend that would answer `attempt` first on a decide resolution:
    "structured" (`generate(schema=)` and the parser) unless the attempt is
    KNOWN unable to generate -- the `_missing` rule, so `unknown` and a
    name-rule guess both still generate. "" for an attempt that cannot.
    Slice H returns "native" where `decide_native` is `yes`."""
    return "" if _missing(attempt, frozenset({"generate"})) else "structured"


def _skippable(primary: Attempt, operation: str) -> bool:
    """Whether `primary` is a decide primary known unable to generate -- the
    one the decide skip passes over, if its fallback can answer."""
    return operation == "decide" and bool(_missing(primary, frozenset({"generate"})))


def _skipped(attempts: list[Attempt], operation: str, missing: tuple[str, ...],
             needs: frozenset[str]) -> tuple[str, ...]:
    """The decide skip (spec 5.5, I5): the primary's `missing`, when this is a
    decide resolution whose primary cannot generate and whose fallback is
    known to lack nothing the route needs; else ().

    Until native decisions arrive (slice H) nothing can answer a decide-only
    model, so the fallback answers in its place. With no fallback, or one that
    cannot generate either, nothing is skipped and the primary's `incapable`
    409 stands (spec 5.3).

    A skipped primary is never checked for a key: `unusable` reads `conn`,
    which is the fallback's, so a decide-only primary with no key is skipped
    silently. Nothing is sent to it, and its missing key surfaces once slice H
    serves it natively."""
    if (operation != "decide" or "generate" not in missing or len(attempts) < 2
            or _missing(attempts[1], needs)):
        return ()
    return missing


def _flag_structured(attempts: list[Attempt], operation: str) -> None:
    """Flag each attempt whose `structured_output` is `yes` (spec 7.2), on its
    own lowered dict -- the resolution's copy, as the fallback attach writes.
    A decide resolution only: a generate resolution's dicts stay byte-identical
    to what they were before slice F (plan Minor 4)."""
    if operation != "decide":
        return
    for attempt in attempts:
        found = attempt.capabilities.get("structured_output")
        if found is not None and found.value == capabilities.YES:
            attempt.conn[STRUCTURED_KEY] = True


def embed_endpoint(conn: dict, current: bool) -> str:
    """The base URL `conn` serves embeddings from, or "" when it serves none.

    An ``openai_compatible`` connection brings its own URL. OpenRouter serves
    ``/embeddings`` too, but only for a config already in the current layout: a
    legacy config naming an OpenRouter connection for embeddings has always
    meant "off", and turning it on would start sending text to a provider
    nobody chose it for. The URL is the preset's (an OpenRouter connection's
    own is locked), and one with no key is not set up.
    """
    if conn["kind"] == "openai_compatible":
        return conn["base_url"] or ""
    if conn["kind"] == "openrouter" and current and conn["api_key"]:
        return providers.PRESETS["openrouter"].base_url
    return ""


def space_of(conn: dict, model: str) -> str:
    """The vector space `conn` embeds `model` in: the key a cached vector is
    read and written under.

    Two endpoints can both serve a model called "embedding" and mean different
    weights, and vectors from different spaces are incomparable even at
    matching dimensionality -- so keying on the model name alone would reuse
    one provider's vectors against another's queries and rank silently wrongly.

    The connection's `rev` is in here for the case the URL does not cover: a
    gateway where the *credential* selects the tenant or deployment. Two
    connections to one URL with different keys are different spaces, and
    replacing a key can move an existing one. `rev` is restamped on every
    write that is not rev-neutral (`llm_connections.REV_NEUTRAL_FIELDS`: a
    name, a provider preset, billing and the sampler fields keep it), so it
    captures both. It over-invalidates -- a new address that serves the same
    deployment costs a full re-embed -- and that is the right direction:
    re-embedding costs money and latency, while a stale namespace costs
    silently wrong rankings with nothing to notice them by. A provider edit
    that moves it is confirmed first (`embed_space.moved_by`).
    `llm_connections.cached_models` gates its own sidecar on `rev` for exactly
    this reason.

    `model` stays explicit because it lives in config.md, not on the
    connection, so changing it does not move `rev`."""
    return f"{conn['id']}\0{conn['rev']}\0{model}"


#: What the Embedding role's one attempt must be able to do.
_EMBED_NEEDS = frozenset({OPERATION_CAPABILITY["embed"]})


class EmbedAttempt(NamedTuple):
    """The Embedding role's one attempt on a provider record, and what follows
    from it (`embed_attempt`)."""

    attempt: Attempt
    #: `embed`, when the attempt is known (`no`) not to make embeddings.
    missing: tuple[str, ...]
    #: The space it embeds in (`space_of`), or None when it embeds nothing.
    space_id: str | None


def embed_attempt(provider_id: str, model: str, raw: dict, *,
                  current: bool, catalog: bool = True,
                  model_facts: dict | None = None) -> EmbedAttempt:
    """The Embedding role's attempt on connection record `raw` at `model`.

    One attempt with no sampling, its `base_url` the embeddings endpoint
    (`embed_endpoint`, "" when the record serves none), what it is known not
    to do (`_missing`: a name-rule guess never counts), and its space -- set
    only when the record embeds: a model, an endpoint, and no known `no`.

    `raw` need not be on disk: `embed_space.moved_by` asks this of the record
    an edit is about to write. Reads that record's catalog row and its
    `facts.json` at `raw["rev"]` (`_attempt`). The catalog sidecar is gated on
    the rev ON DISK, so for a record whose rev the write will restamp the
    caller passes `catalog=False`: once the write lands that row is stale and
    says nothing, and judging the record by it would read a `no` the saved
    provider no longer has (the verdicts need no such flag -- `facts.of` is
    already read at `raw`'s own rev). `model_facts` judges it with facts not
    yet written instead of `facts.json`'s (`embed_space.facts_moved`)."""
    attempt = _attempt(provider_id, model, dict(NO_SAMPLING), raw, current=current,
                       catalog=catalog, model_facts=model_facts)
    endpoint = embed_endpoint(raw, current)
    attempt = dataclasses.replace(attempt, base_url=endpoint)
    missing = _missing(attempt, _EMBED_NEEDS)
    space_id = space_of(raw, model) if model and endpoint and not missing else None
    return EmbedAttempt(attempt, missing, space_id)


def _fallback_problem(primary: dict, fallback: dict | None) -> str | None:
    """Why a fallback that exists is left out of the chain: it is on the
    primary's own provider (`SAME_PROVIDER`), or it cannot send (`problem`).
    Said either way -- without a reason the settings view showed a dropped
    fallback as a working one. None for no fallback, or one that is sent."""
    if fallback is None:
        return None
    if _same_provider(primary, fallback):
        return SAME_PROVIDER
    return problem(fallback)


def embedding(cfg: dict | None = None, *,
              lookup: translate.Lookup | None = None) -> ResolvedInference:
    """The Embedding role's resolution: its one attempt, what that attempt is
    known not to do, and the vector space it embeds in (spec 5.4, slice D).

    The one reader of the role. `config.md` is read only when `cfg` is None;
    the role is global only (spec 4.4), so no campaign is read -- one space,
    one vector cache. The selection is `cascade.role_selection("embedding")`
    over `translate.embedding_view`, which keeps the `embeddings_*` trim rule
    at both formats. Embedding inherits nothing and has no fallback on any
    axis (rule 4: a vector is saved only under the space that produced it),
    so there is exactly one attempt or none (`embed_attempt`).

    Reads, per resolution: the config (when not given), the chosen provider's
    connection file once (the memoised lookup, which runs the connection
    migration as every `read_connection_raw` does), that provider's cached
    catalog row and `facts.json` -- the last two for the capability that says
    whether this model can embed at all. No other provider is read.

    `lookup` is a fresh `connection_lookup()` unless given: that one reads an
    unreadable provider (a busy store included) as no provider, which is "off".
    The settings migration hands its strict lookup instead, so a file a sync
    client held fails the run rather than clearing the legacy choice for good.

    `space_id` is set only when the role embeds: a model, an endpoint
    (`embed_endpoint`, which is also the attempt's `base_url`), and no known
    `no` for `embed` (`missing`). A known `no` turns embedding off exactly as an
    unset role does -- no request is sent that could only fail (ruling 4). A
    name-rule guess is never a known `no` (`_missing`).

    Raises what reading `config.md` or a malformed connection record raises;
    `embed_space.endpoint` is the never-raising door."""
    cfg = config.read_config() if cfg is None else cfg
    lookup = connection_lookup() if lookup is None else lookup
    current = translate.is_current(cfg)

    def exists(conn_id: str) -> bool:
        return lookup(conn_id) is not None

    selection, _, scope = cascade.role_selection(
        "embedding", campaign={}, glob=translate.embedding_view(cfg), exists=exists)
    raw = lookup(selection.provider) if selection is not None else None
    if selection is None or raw is None:
        return ResolvedInference(task="", operation="embed", route="", legacy_route="",
                                 role="", via="", scope="none", attempts=(),
                                 current=current)
    got = embed_attempt(selection.provider, selection.model, raw, current=current)
    # The account block a chat resolution's attempts get from `_account`: the
    # operation, and the role whose slot supplied the selection -- always the
    # Embedding role's, since nothing overrides it per call (spec 9.3).
    # Replaced, never updated in place: the block is shared by `{**conn}` copies.
    conn = got.attempt.conn
    conn[ACCOUNT_KEY] = {**conn.get(ACCOUNT_KEY, {}), "operation": "embed",
                         "role": "embedding"}
    return ResolvedInference(
        task="", operation="embed", route="", legacy_route="",
        role="embedding", via="role", scope=scope, attempts=(got.attempt,),
        standing=selection, current=current, missing=got.missing, space_id=got.space_id)


def _role_supplied(standing: Selection | None, selection: Selection | None,
                   kind: str) -> bool:
    """Whether the selection a call runs on is still the one the role's slot
    supplied: the same provider, at the same EFFECTIVE model (`facts.model_of`,
    on the provider's `kind`). A preset-only override keeps it.

    Effective, as `routes.common.override_inference` compares: a Claude
    selection with no model runs the default, so a reroll naming that default
    has not left the role's selection and its row keeps the role."""
    if standing is None or selection is None or standing.provider != selection.provider:
        return False
    return (facts.model_of({"kind": kind, "model": standing.model})
            == facts.model_of({"kind": kind, "model": selection.model}))


def _account(attempts: list[Attempt], operation: str, choice: cascade.Choice,
             selection: Selection | None) -> None:
    """Stamp each attempt's account block (spec 9.3): the `operation`, and the
    `role` whose slot supplied the resolution, on both attempts (ruling 9).

    No role for a pin (`choice.role` is empty), nor when a per-call override
    chose the provider or the model -- the user supplied that selection, not a
    role. The block is REPLACED, never `update()`d: `{**conn}` copies share it,
    and an in-place write would reach every one of them."""
    stamp = {"operation": operation}
    kind = str(attempts[0].conn.get("kind", "") or "") if attempts else ""
    if choice.role and _role_supplied(choice.selection, selection, kind):
        stamp["role"] = choice.role
    for attempt in attempts:
        attempt.conn[ACCOUNT_KEY] = {**attempt.conn.get(ACCOUNT_KEY, {}), **stamp}


def _same_provider(primary: dict, fallback: dict) -> bool:
    """Whether the fallback is the primary's own connection, by store id --
    `llm._same_route`'s rule (two dicts read from disk are never the same
    object, so the id is what decides)."""
    pid = primary.get("id", "")
    return bool(pid) and pid == fallback.get("id", "")


# ---- the refusal ----
#: A refusal: the HTTP status and the body the seam answers with. The body is
#: a `{detail, kind}` dict, or -- for an image route on a wire protocol that
#: cannot carry an image -- the bare `image_drafts.UNSUPPORTED` sentence that
#: route has always answered with.
Refusal = tuple[int, dict | str]

#: The sources of a vision `no` a connection's legacy "Images: on" overrides
#: at format 1 (`incapable`'s bridge). At format 2 that setting lives in the
#: model's facts, where `vision: on` is already the user's `yes`.
IMAGES_ON_OUTRANKS = frozenset({"catalog", "name", "preset"})


def unusable(resolved: ResolvedInference) -> Refusal | None:
    """Why this resolution cannot send, if it cannot: the 409 `missing_key`.

    Shared by the seam and by the per-call override, whose `model`-only reroll
    drives the standing route and must be refused on exactly the same terms --
    including the routed-connection wording below.
    """
    conn = resolved.conn
    if conn is None:
        return 409, {"detail": "No LLM connection selected", "kind": "missing_key"}
    why = problem(conn)
    name = conn.get("name") or conn["id"]
    if why is not None and resolved.current:
        # Spec 12: the provider, and the route or role that chose it --
        # today's wording, generalised. At format 2 nearly everything comes
        # through a role, and two providers of one kind would otherwise read
        # the same sentence. A pin is named by its own route (a split route's
        # pin is its own now, not its legacy parent's). A legacy store keeps
        # the sentences below exactly: they are what it always answered.
        where = (f"routed for {routing.label_for(resolved.route).lower()}"
                 if resolved.via == "route"
                 else f"the {resolved.role.capitalize()} role" if resolved.role else "")
        detail = f"{why} ({name}, {where})" if where else f"{why} ({name})"
        return 409, {"detail": detail, "kind": "missing_key"}
    if why is not None and resolved.via == "route":
        # A ROUTED connection that cannot send is reported, not walked past.
        # The walk skips a route naming a connection that no longer exists --
        # a delete cannot reach into every campaign's frontmatter, so that
        # reference is stale rather than meant. This one is meant: the user
        # pointed this task at this connection and it has no key. Falling back
        # to the active connection would generate a scene on a model they did
        # not choose and never say so.
        return 409, {
            "detail": f"{why} ({conn.get('name') or conn['id']}, routed for "
                      f"{routing.label_for(resolved.legacy_route).lower()})",
            "kind": "missing_key"}
    if why is not None:
        # Refused before the facade, so a configured fallback does not rescue
        # it. The facade falls back on `auth`, and the distinction is real: a
        # key the provider rejected is a runtime failure, worth routing around
        # silently, while no key at all is a setup mistake. Quietly serving it
        # from the fallback would leave someone playing for weeks on the wrong
        # connection, wondering why the model they picked never sounds right.
        return 409, {"detail": why, "kind": "missing_key"}
    return None


def incapable(resolved: ResolvedInference) -> Refusal | None:
    """The 409 for a primary that is KNOWN unable to do what its route needs.

    Only a known `no` (`resolved.missing`); `unknown` is let through, and a
    fallback's gaps are reported (`fallback_missing`) but never refused on.
    Asked after `unusable` (`refusal`), so a connection with no key says that
    first: it is the fix the reader has to make before anything else matters.

    A wire protocol that cannot carry an image (an `adapter` `no` on vision)
    answers with the sentence the image-description route always answered
    with, in the same body, so nothing that reads it sees a change. At format 1
    a connection set to "Images: on" is not refused over a catalog's vision
    `no` (the bridge, below); at format 2 that word is the model's facts, a
    user `yes` the capability resolution already ranks above the catalog.
    Anything else is `incapable` (`incapable_text`), naming the first missing
    capability in `capabilities.NAMES` order. A name-rule guess is never
    missing (`_GUESSES`), and a failed test call is `unknown`, so neither can
    refuse here.
    """
    if not resolved.missing or not resolved.attempts:
        return None
    primary = resolved.attempts[0]
    vision = primary.capabilities.get("vision")
    if "vision" in resolved.missing and vision is not None and vision.source == "adapter":
        return 409, image_drafts.UNSUPPORTED
    missing = _bridged(resolved.missing, primary, current=resolved.current)
    if not missing:
        return None
    return 409, {"detail": incapable_text(resolved, missing[0]), "kind": "incapable"}


def _bridged(missing: tuple[str, ...], attempt: Attempt, *,
             current: bool) -> tuple[str, ...]:
    """`missing` with the format-1 BRIDGE applied to `attempt`: a legacy
    store's "Images: on" sends image drafts whatever the catalog says, and
    nothing in that layout could undo a refusal or a dropped fallback. Only
    the sources the migrated setting outranks are waived -- the wire
    protocol's `no` stands, and so does the user's per-model word (a probe's
    failure is never a `no`). The primary's refusal (`incapable`) and the
    fallback's drop (`resolve`) both ask this, so the same connection is
    served the same way in either seat."""
    vision = attempt.capabilities.get("vision")
    if ("vision" in missing and not current
            and attempt.conn.get("vision") == "on"
            and vision is not None and vision.source in IMAGES_ON_OUTRANKS):
        return tuple(cap for cap in missing if cap != "vision")
    return missing


def refusal(resolved: ResolvedInference) -> Refusal | None:
    """The seam's one decision about `resolved`: `(status, body)` when it
    cannot be served, None when it can. Pure -- it reads nothing.

    `routes.common` raises from it, and the settings view reports it, so what
    the screen says is wrong with a row is the seam's own answer rather than a
    copy of it."""
    return unusable(resolved) or incapable(resolved)


def incapable_text(resolved: ResolvedInference, cap: str) -> str:
    """The `incapable` sentence (spec 5.3): the route, the role when one
    supplied the model, the model on its provider, what it cannot do, and
    what to do about it.

    "The <label> route ..." rather than "<label> runs ...", because half the
    route labels are plural ("Scene turns", "Image descriptions"). The role
    is named only when the model IS the role's: a pin names none, and a
    per-call override moved the call off whatever the role chose. The remedy
    follows: another model for the role, or a pin, where there is a route to
    pin; another model for the route where it is already pinned. The model is
    the one the connection runs (`facts.model_of`, `llm.effective_model`'s
    rule)."""
    subject, where, remedy = _primary_phrases(resolved)
    return (f"{subject} {where}, which cannot "
            f"{capabilities.CANNOT.get(cap, cap)} — {remedy}.")


def _on(attempt: Attempt) -> str:
    """"<model> on <provider>": the model the connection runs, and the
    provider's name (else its preset's label, else its id)."""
    conn = attempt.conn
    preset = providers.PRESETS.get(attempt.provider_preset)
    provider = conn.get("name") or (preset.label if preset is not None else conn.get("id", ""))
    return f"{facts.model_of(conn)} on {provider}"


def _primary_phrases(resolved: ResolvedInference) -> tuple[str, str, str]:
    """`(subject, where, remedy)` for the primary attempt -- what the route
    is, what it runs on, and what to do about it -- shared by
    `incapable_text` and `skip_text` so both say it the same way."""
    primary = resolved.attempts[0]
    on = _on(primary)
    standing = resolved.standing
    chosen = standing is not None and (standing.provider, standing.model) == (
        primary.provider_id, primary.model)
    subject = (f"The {routing.label_for(resolved.route)} route" if resolved.route
               else "This generation")
    pin = " or pin this route" if resolved.route else ""
    if chosen and resolved.role:
        role = resolved.role.capitalize()
        where = f"runs on the {role} role ({on})"
        remedy = f"choose another {role} model{pin}"
    elif chosen and resolved.via == "route":
        where, remedy = f"is pinned to {on}", "choose another model for this route"
    else:
        where, remedy = f"runs on {on}", f"choose another model{pin}"
    return subject, where, remedy


def skip_text(resolved: ResolvedInference) -> str | None:
    """The sentence for a `skipped` resolution (spec 5.5), in
    `incapable_text`'s register: what the route runs on, that it cannot
    generate, and which fallback answers in its place until native decisions
    arrive. None when nothing was skipped."""
    if not resolved.skipped or len(resolved.attempts) < 2:
        return None
    subject, where, _ = _primary_phrases(resolved)
    return (f"{subject} {where}, which cannot generate; until native decisions "
            f"arrive it is answered by the fallback ({_on(resolved.attempts[1])}).")
