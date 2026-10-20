"""Generative/decision cascade orchestration, per-call overrides/silencing, fallback retention and account provenance."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

from .... import llm_sampling, wire
from ... import config, llm_connections, routing, sampler_presets
from .. import cascade, facts
from ..cascade import Selection
from ..resolved import Attempt, ResolvedInference
from . import _capability_policy, _contracts, _targeting


# ---- the resolver ----
def _overridden(standing: Selection | None, override: Selection | None,
                lookup: llm_connections.Lookup, *,
                own: Callable[[str, str], Selection] | None = None,
                derive: Callable[[Selection], Selection] | None = None) -> Selection | None:
    """The selection one call runs on, given a per-call override (#77).

    A model alone drives the STANDING provider at that model, so with no
    standing selection there is nothing to drive. Both is the named provider at
    the named model. A provider alone is the one part that depends on the
    layout (spec 5.6):

    - format 2: that provider at the STANDING model and preset. A provider
      has no model of its own, so what is kept is the part of the selection
      the override did not name. With no standing selection there is nothing
      to keep, and the model is left empty for the seam to ask the caller for.
    - format 1 (`own`, the planner's `Overlay.selection`): that provider at
      its OWN model and preset, needing no standing selection -- rerolling
      onto a working endpoint is how a broken standing route is fixed. A
      format-1 connection still has a model of its own, and a provider and a
      model together take the named connection's own preset too, as a
      format-1 store always has. That preset is the planner's for such a
      slot, so a GLM connection's legacy reasoning effort rides its derived
      preset and is sent, as the legacy wire sent it. A model alone keeps the
      standing slot, judged again on the model it now sends (`derive`,
      `Overlay.derived`), for the same reason.

    The override's preset is never merged into the selection: it outranks the
    route's preset, which no selection's own can, so `resolve` applies it on
    top of the cascade."""
    if override is None or not (override.provider or override.model):
        return standing
    if override.provider:
        if lookup(override.provider) is None:
            return None
        if own is not None:
            base = own(override.provider, override.model)
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
    chosen = Selection(base.provider, override.model or base.model, base.preset)
    # Format 1, a model alone: the standing slot judged again on the model the
    # reroll sends, as the legacy wire read a GLM effort off the connection
    # whenever the SENT model was GLM (`Overlay.derived`).
    if derive is not None and not override.provider and override.model:
        return derive(chosen)
    return chosen


def _silenced(silence: _contracts.Silence | None, glob: dict, campaign: dict) -> tuple[dict, dict]:
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
            silence: _contracts.Silence | None = None) -> ResolvedInference:
    """Where `task` runs, for campaign `cid` ("" for none), and what it falls
    back to.

    The connection files are migrated BEFORE `config.md` is read: a store from
    before named connections holds only a flat key and model, and migration is
    what seeds the active connection those resolve to. The campaign's
    frontmatter is read here too (`campaign_meta`), never raising: one that
    cannot be read has no routing opinion.

    Both are resolved as format 2 sees them (`_overlay`): a layout the
    migration or retirement has not reached is planned in memory, and
    nothing is written.

    Nothing here refuses. A primary that cannot send is still the primary --
    the seam reports it (`problem`) rather than walking past it; no selection
    at all is an empty `attempts`.

    The fallback attempt is the one the facade sends (spec 5.2, 5.4, 5.5),
    riding the primary on the resolution's chain (`rides`): it is
    dropped when it cannot be read or cannot send (so a misconfigured fallback
    never replaces the primary's real error) and when it names the primary's
    own connection (`llm._same_route`: a second try on the connection that just
    failed is not a fallback; kept behind a decide primary that cannot
    generate when it names another model, where it is a stage of its own,
    `_apart`) -- either says why in
    `fallback_problem`
    (`problem`'s reason, or `SAME_PROVIDER`), which nothing refuses on; and
    always, whatever the role says, on a task whose code policy sends none
    (`routing.policy(task).fallback == "none"`, `NO_FALLBACK_POLICY`; "" --
    a role card -- reads the default policy); and
    when the route has a
    preset -- campaign or global scope, a `PRESET_CLEAR` included -- the
    fallback carries that same sampling rather than its own preset
    (`llm.fallback_sampling`). Its
    `model_params` stay its own model's. A fallback KNOWN unable to do what
    the route needs is kept in `attempts` and reported (`fallback_missing`) but
    does not ride, so it is never sent (spec 5.3). On a decide resolution the
    fallback rides only where both attempts are structured (`_rides`);
    otherwise it stays in `attempts`, riding nothing, and `inference.stages`
    sends it as a stage of its own.

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
    meta = _targeting.campaign_meta(cid)
    seen = _targeting._overlay(cfg, meta, cid)
    lookup = _targeting.connection_lookup()
    presets = _targeting._preset_lookup(seen.presets)
    route = None if role else routing.route(task)
    glob, campaign = _silenced(silence, dict(seen.cfg), dict(seen.meta))

    def exists(conn_id: str) -> bool:
        return lookup(conn_id) is not None

    choice = (cascade.choose_role(role, campaign=campaign, glob=glob, exists=exists) if role
              else cascade.choose(route, campaign=campaign, glob=glob, exists=exists))

    # A preset in the override outranks the route's (spec 5.6) -- the most
    # specific choice there is.
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
    selection = _overridden(choice.selection, override, lookup,
                            own=seen.selection if seen.legacy else None,
                            derive=seen.derived if seen.legacy else None)
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
            unforced = _targeting._sampling(cascaded(choice.selection), presets)
            standing_preset = unforced["preset_id"]
            sampling = _targeting._sampling(lambda known: overriding(known, primary), presets)
        else:
            sampling = unforced = _targeting._sampling(cascaded(primary), presets)
        first = _targeting._attempt(primary.provider, primary.model, sampling, raw,
                         retries=config.llm_retries(cfg), stated=seen.facts)
        attempts.append(first)
        fallback = choice.fallback
        fb_raw = lookup(fallback.provider) if fallback is not None else None
        if routing.policy(task).fallback == "none":
            # The task's code policy sends no fallback, whatever its role
            # says (spec 01d §4.2). The cascade already walked past a slot
            # naming no provider, so `fb_raw` is None here only for a
            # connection that vanished since -- which says nothing, as below.
            fallback_problem = _contracts.NO_FALLBACK_POLICY if fb_raw is not None else None
        else:
            # A fallback on the primary's own provider is a retry (#144),
            # which the retry budget already covers -- except behind a decide
            # primary that cannot generate (`_apart`) when it names ANOTHER
            # model: that fallback is a stage of its own, never a second try
            # of the call that failed. The same model on the same connection
            # is that second try whatever the stage is called, so it is
            # dropped as C drops it. The reason is lifted exactly where the
            # drop is (`SAME_PROVIDER`).
            fallback_problem = (_targeting.problem(fb_raw)
                                if fb_raw is not None and fallback is not None
                                and _apart(first, operation)
                                and not _same_model(first.target, fb_raw, fallback.model)
                                else _fallback_problem(first.target.provider_id, fb_raw))
        if fallback is not None and fb_raw is not None and fallback_problem is None:
            # A copy, so the two attempts never share a mutable block.
            fb_sampling = ({**unforced, "params": dict(unforced["params"])}
                           if unforced["scope"] in _contracts.ROUTE_SCOPES
                           else _targeting._sampling(_targeting._own_preset(fallback), presets))
            attempts.append(_targeting._attempt(fallback.provider, fallback.model, fb_sampling,
                                     fb_raw, stated=seen.facts))

    tail = _chain(attempts, operation, _capability_policy._needs(route, operation),
                  choice=choice, selection=selection)
    return ResolvedInference(
        task=task, operation=operation,
        route=route.key if route is not None else "",
        role=choice.role, via=choice.via, scope=choice.scope,
        attempts=tail.attempts, standing=choice.selection,
        standing_preset=standing_preset, missing=tail.missing,
        fallback_missing=tail.fallback_missing, fallback_problem=fallback_problem,
        rides=tail.rides, legacy_route=seen.legacy_route(route) if seen.legacy else None)


def _chain(attempts: list[Attempt], operation: str, needs: tuple[frozenset[str], ...], *,
           choice: cascade.Choice, selection: Selection | None) -> _contracts._Tail:
    """The tail of `resolve`: stamp each attempt's account, flag the
    structured-capable ones, say what each is known to lack, give a decide
    resolution's attempts their `decision_mode`, and say whether the fallback
    rides the facade behind the primary (`_rides`; spec 5.3: a fallback known
    incapable never does, so it is never sent). Each stamp replaces the
    attempt around a new target (`_stamp`)."""
    attempts = _account(attempts, operation, choice, selection)
    attempts = _capability_policy._flag_structured(attempts, operation)
    fallback_missing = _capability_policy._missing(attempts[1], needs) if len(attempts) > 1 else ()
    missing = _capability_policy._missing(attempts[0], needs) if attempts else ()
    if operation == "decide":
        # Before `_rides`, which reads the fallback's mode.
        attempts = [_decide_attempt(a) for a in attempts]
    rides = len(attempts) > 1 and not fallback_missing and _rides(attempts, operation)
    return _contracts._Tail(tuple(attempts), missing, fallback_missing, rides)


def _decide_attempt(attempt: Attempt) -> Attempt:
    """`attempt` on a decide resolution: its `decision_mode`, and -- when that
    is "native" -- its controls all `n/a` (spec 8), since its provider's
    decisions endpoint takes no sampling. Under ruling 1 a native attempt is
    always one that cannot generate, so there is no sampling to keep."""
    mode = _capability_policy.decision_mode(attempt)
    if mode != "native":
        return dataclasses.replace(attempt, decision_mode=mode)
    return dataclasses.replace(
        attempt, decision_mode=mode,
        controls=llm_sampling.not_applicable(attempt.target, llm_sampling.WHY_NATIVE))


def _rides(attempts: list[Attempt], operation: str) -> bool:
    """Whether the fallback rides the facade behind the primary
    (`ResolvedInference.rides`): always on a generate resolution; on a decide one only
    when both are structured -- the primary `generates` and the fallback's
    mode is "structured". A native attempt, on either side, is a stage of its
    own (`inference.stages`), which a structured call cannot carry. Under
    ruling 1 a generating primary is structured, so every fallback slice F
    sent behind its primary still rides."""
    if operation != "decide":
        return True
    return attempts[1].decision_mode == "structured" and _capability_policy.generates(attempts[0])


def _apart(primary: Attempt, operation: str) -> bool:
    """Whether a fallback on `primary`'s own provider may be kept (`resolve`):
    on a decide resolution whose primary cannot generate, and only when it
    names another model (`_same_model`, checked by the caller). That
    fallback is never a retry of the primary's call -- it is a stage of its
    own, on another model (`inference.stages`) -- so the same-provider drop
    (#144) does not apply. The same model on the same connection IS that
    retry, and is dropped with `SAME_PROVIDER`. Kept like any other fallback:
    one that cannot serve either backend is reported in `fallback_missing`."""
    return operation == "decide" and not _capability_policy.generates(primary)


def _fallback_problem(primary_id: str, fallback: dict | None) -> str | None:
    """Why a fallback that exists is left out of the chain: it is on the
    primary's own provider (`primary_id`; `SAME_PROVIDER`), or it cannot send
    (`problem`). Said either way -- without a reason the settings view showed
    a dropped fallback as a working one. None for no fallback, or one that is
    sent."""
    if fallback is None:
        return None
    if _same_provider(primary_id, fallback):
        return _contracts.SAME_PROVIDER
    return _targeting.problem(fallback)


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
             selection: Selection | None) -> list[Attempt]:
    """Stamp each attempt's account (spec 9.3): the `operation`, and the
    `role` whose slot supplied the resolution, on both attempts (ruling 9) --
    on its target (`_stamp`).

    No role for a pin (`choice.role` is empty), nor when a per-call override
    chose the provider or the model -- the user supplied that selection, not a
    role."""
    stamp = {"operation": operation}
    kind = attempts[0].provider_kind if attempts else ""
    if choice.role and _role_supplied(choice.selection, selection, kind):
        stamp["role"] = choice.role
    return [_capability_policy._stamp(attempt, account=stamp) for attempt in attempts]


def _same_model(primary: wire.Target, fallback: dict, model: str) -> bool:
    """Whether a fallback on record `fallback`, at `model`, is the primary's
    own connection AND model (`facts.model_of`, so an unset Claude model is
    the default it runs -- the primary target's `model` already is): a
    second send of the very call that failed, which no stage boundary turns
    into a fallback (#144)."""
    return (_same_provider(primary.provider_id, fallback)
            and facts.model_of({**fallback, "model": model}) == primary.model)


def _same_provider(primary_id: str, fallback: dict) -> bool:
    """Whether the fallback is the primary's own connection (`primary_id`, its
    record's id), by store id -- `llm._same_route`'s rule (two records read
    from disk are never the same object, so the id is what decides)."""
    return bool(primary_id) and primary_id == fallback.get("id", "")
