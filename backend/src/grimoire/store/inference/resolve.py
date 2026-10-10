"""Resolve a task to the provider, model, preset and fallback it runs on.

The impure half of `store/inference/`: this reads `config.md`, the campaign's
`campaign.md`, the connection files, the sampler presets and the model-catalog
sidecars, and hands the pure pieces -- `cascade` (spec §5.1, §5.2, §5.5) --
dicts and predicates. What comes back is a `ResolvedInference`, whose attempts
each carry the `wire.Target` an adapter sends (`_target`, built directly from
the provider's record, the model, the preset and the model's facts), and whose
`chain` is what a call site hands the facade.

Behaviour-neutral by construction for every store the app can hold today: the
equivalence tests compare each answer with the frozen baseline's, over its
states.

Never imports `llm`: the one thing borrowed from it, a connection's effective
model, differs from the stored one only for `claude` (`facts.model_of`), and
`model_params` consults the catalog for OpenRouter alone. The facade rule the
fallback attempt mirrors (`llm.fallback_sampling`) is restated here, with
`ROUTE_SCOPES`, and the tests hold it to the facade's own answer.

The fallback the facade sends is the one resolved here (slice C): it rides the
primary (`ResolvedInference.rides`, so `chain.fallback`), unless it is known
unable to do what the route needs (spec 5.3) -- then it is reported
(`fallback_missing`) and does not ride. On a decide resolution, each attempt
whose `structured_output` is `yes` is flagged on its target
(`wire.Target.structured`, slice F), which is how the facade asks that
attempt, and only that one, for its provider's structured mode. Each attempt
of a decide resolution also says which backend would answer it
(`decision_mode`, slice H): its provider's native decisions endpoint for a
model known unable to generate that may decide natively (`native_only`),
structured generation for one that can generate, whatever its
`decide_native` says (a native-first task asks its decisions endpoint first:
`native_first`, a stage of `inference.stages`, not a mode).

Each attempt's target carries an account (`wire.Account`): what the ledger
files about the attempt that the wire does not say -- its `billing`, its
`operation` and the `role` whose slot supplied it (`_account`, laid on by
`_stamp`, the one place a resolution writes a target's account or structured
flag).

Each attempt also carries what slice B knows of it -- its provider's kind, URL,
rev, billing and preset, its model's facts, its effective controls
(`llm_sampling.effective` over its target), and every capability with its
source (`capabilities.resolve_caps`, fed the catalog row the target was built
from, so a sidecar is read once per attempt) -- and the resolution says which
of the route's needs the primary and the fallback are known not to meet
(`missing`, `fallback_missing`). `resolve` refuses on none of it; whether the
seam serves a resolution is `refusal`, a pure function of it, which the seam
raises from and the settings view reports -- one decision, not two copies.

Every store resolves as format 2 (slice I). A layout the migration has not
reached -- a format-1 store, an unmarked campaign, a scope not yet retired --
is read through the planner, in memory: `_overlay` is the one call into
`legacy_plan`, and what it returns (the settings as format 2 sees them, the
derived reasoning presets, the legacy model facts) is resolved as though it
were stored. Nothing on that path writes. Two answers stay format 1's on a
format-1 store (spec 5.6; user ruling 2026-10-09), each read off the overlay
rather than a legacy field: a reroll naming a provider alone runs that
provider's own model and preset (`Overlay.selection`, `_overridden`), and the
`missing_key` sentence keeps its format-1 wording (`ResolvedInference.legacy`,
`unusable`). The model's facts drive its
behaviour: a target's `prefill`, `post_process` and post-image reach are its
model's facts (`_stated`), never the connection's legacy fields.

There is no connection dict. Slice I built the target beside a lowered dict
(Task 7) and deleted the dict once the facade took targets (Task 10);
`test_lowering_retired_guard.py` keeps it deleted.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from typing import NamedTuple

from ... import llm_sampling, wire
from .. import (
    campaigns,
    config,
    image_drafts,
    llm_connections,
    locks,
    routing,
    sampler_presets,
)
from .. import inference_keys as keys
from .. import inference_retired as retired
from . import capabilities, cascade, facts, legacy_plan, limits, providers
from .cascade import Selection
from .resolved import Attempt, ResolvedInference

#: What a connection carries when no preset could be resolved for it at all.
NO_SAMPLING = {"preset_id": "", "preset_name": "", "scope": "none", "params": {}}

#: Where a sampler preset came from, for the scopes that are about the ROUTE
#: rather than the connection: a preset from one of these follows the route onto
#: the fallback. `llm.ROUTE_SCOPES`, restated because this module never imports
#: `llm`; a test holds the two equal.
ROUTE_SCOPES = frozenset({"campaign", "global"})

#: Why a fallback on the primary's own provider is left out of `attempts`
#: (`fallback_problem`): a second try on the connection that just failed is not
#: a fallback (`llm._same_route`). A name of its own, so the one resolution
#: that keeps such a fallback -- a decide primary that cannot generate, whose
#: fallback on another model is a stage apart (`_apart`) -- lifts this reason
#: where it lifts the drop, and leaves the credential ones (`problem`) alone.
#: The same model there is still a second try, and still dropped with it.
SAME_PROVIDER = "it is on the primary's own provider"

#: Why a task's fallback is left out of `attempts` when its code policy sends
#: none (`routing.TaskPolicy.fallback == "none"`, spec 01d §4.2). Outranks the
#: other reasons: on such a task the policy is why the fallback is unsent,
#: whatever else is true of it. Said only for a fallback the cascade chose,
#: so the readout never reports dropping one that did not exist.
NO_FALLBACK_POLICY = "this task's policy sends no fallback"

#: The ways reading one connection can fail, every one of which reads as "no
#: such connection" -- a dangling reference is walked past, never raised.
_UNREADABLE = (llm_connections.ConnectionNotFound, locks.StoreBusy,
               OSError, UnicodeDecodeError)


def problem(conn: dict) -> str | None:
    """Why this connection record cannot send, or None if it can.

    The credential check the seam turns into a 409 and the fallback turns into
    "there is no fallback" -- one function, because a fallback that is
    silently unusable is exactly the failure a fallback exists to prevent, and
    two copies of this rule would drift. `target_problem` asks it of a target.
    """
    return _credential_problem(conn["kind"], conn.get("api_key"), conn.get("base_url"))


def target_problem(target: wire.Target) -> str | None:
    """`problem` asked of an attempt's target: its kind, key and address are
    its provider record's (`_target`), so the answer is the record's."""
    return _credential_problem(target.kind, target.api_key, target.base_url)


def _credential_problem(kind: object, api_key: object, base_url: object) -> str | None:
    if kind == "openrouter" and not api_key:
        return "OpenRouter key not set"
    if kind == "anthropic" and not api_key:
        return "Anthropic API key not set"
    if kind == "openai_compatible" and not base_url:
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
def connection_lookup() -> llm_connections.Lookup:
    """`read_connection_raw`, memoised for one resolution (or one settings
    view, `settings.view`) and never raising.

    One resolution asks about the same few ids several times over (the
    cascade checks existence, the attempts read the records), so each file is
    read at most once here -- which also means every question the resolver
    asks in one resolution is answered from the same read. (The planner reads
    through a lookup of its own, `_overlay`.)"""
    seen: dict[str, dict | None] = {}

    def lookup(conn_id: str) -> dict | None:
        if conn_id not in seen:
            try:
                seen[conn_id] = llm_connections.read_connection_raw(conn_id)
            except _UNREADABLE:
                seen[conn_id] = None
        return seen[conn_id]

    return lookup


def _preset_lookup(virtual: Mapping[str, dict] | None = None) -> Callable[[str], dict | None]:
    """`sampler_presets.read_preset`, memoised for one resolution -- with the
    planner's `virtual` presets (`Overlay.presets`, the derived reasoning
    presets retirement has not written yet) answering first."""
    seen: dict[str, dict | None] = {}
    # The mapping itself, not a copy: a format-1 reroll's derived preset is
    # added to it after this reader is made (`Overlay.selection`).
    planned: Mapping[str, dict] = virtual if virtual is not None else {}

    def lookup(pid: str) -> dict | None:
        if pid in planned:
            return planned[pid]
        if pid not in seen:
            seen[pid] = sampler_presets.read_preset(pid)
        return seen[pid]

    return lookup


# ---- the legacy layout, through the planner ----
def _overlay(cfg: Mapping[str, str], meta: Mapping[str, str],
             cid: str = "") -> legacy_plan.Overlay:
    """The one call into the planner: `cfg` and `meta` as format 2 sees them,
    in memory (`legacy_plan.overlay`). Free on a retired store.

    Not memoised, on purpose, and that has a cost until retirement lands
    (slice I, Task 6). On a store not yet retired every call plans from
    scratch: the derivation and the route notes (a cascade per route, and a
    soft read of each connection a slot names, memoised for the call only),
    and below format 2 the whole mapping plus a read of every listed
    connection for the facts. Every resolution pays it, and so does
    `embed_space.endpoint` on every turn and `settings.view` on each of its
    rows. A memo would have to be keyed on every file the plan reads -- each
    connection record, its facts and catalog, every preset -- and an in-place
    rewrite moves no directory stamp, so one that was obviously safe would
    stat as much as the plan reads. Retirement marks each scope, and a
    retired scope plans nothing: the cost goes with it."""
    return legacy_plan.overlay(cfg, meta, cid=cid)


def current_view(cfg: Mapping[str, str], meta: Mapping[str, str] | None = None, *,
                 cid: str = "") -> legacy_plan.Overlay:
    """The global settings (and campaign `cid`'s, `meta`) as the resolver
    reads them: what `settings.view` and `in_use` show, so a store the
    migration or retirement has not reached shows the format-2 layout it
    will be written as."""
    return _overlay(cfg, meta or {}, cid)


def retirement_notes(cid: str = "", *,
                     cfg: Mapping[str, str] | None = None) -> tuple[retired.Note, ...]:
    """What the planner could not carry over (ruling 5), for the global scope
    and, with `cid`, that campaign. Never raises on a campaign that cannot be
    read (`campaign_meta`). `cfg` is `config.md` as the caller has already
    read it (read here when not given): a caller asking for every campaign
    reads it once."""
    return _overlay(config.read_config() if cfg is None else cfg,
                    campaign_meta(cid), cid).notes


def _embedding_view(cfg: Mapping[str, str]) -> dict[str, str]:
    """The Embedding role's slot as a format-2 `cfg` stores it, both values
    stripped (the `embeddings_*` trim rule, kept): what `embedding` hands the
    cascade."""
    provider = keys.role_key("embedding", "provider")
    model = keys.role_key("embedding", "model")
    return {provider: str(cfg.get(provider, "") or "").strip(),
            model: str(cfg.get(model, "") or "").strip()}


def embedding_role(cfg: Mapping[str, str]) -> tuple[str, str]:
    """`(provider, model)` for the Embedding role as format 2 sees `cfg`
    (`_overlay`): what the role is set to, not the resolution (`embedding`).
    Below format 2 that is the planner's mapping, which turns off a legacy
    choice that never embedded."""
    view = _embedding_view(_overlay(cfg, {}).cfg)
    return (view[keys.role_key("embedding", "provider")],
            view[keys.role_key("embedding", "model")])


def stored_embedding_role(cfg: Mapping[str, str]) -> tuple[str, str]:
    """`(provider, model)` for the Embedding role exactly as `cfg` stores it,
    stripped and NOT judged (`Overlay.embedding`): below format 2 the legacy
    pair, even one the mapping turns off because its record, as it stands,
    does not embed. What a guard asking whether an EDIT of that record moves
    the role's space must compare (`embed_space.moved_by`): the mapping's
    verdict is about the record being replaced."""
    return _overlay(cfg, {}).embedding


def _read_facts(provider_id: str, model: str, rev: str,
                stated: Mapping[str, Mapping[str, dict]] | None) -> dict:
    """`model`'s facts on `provider_id`, never raising. Below format 2 the
    planner lists the provider in `stated` (`Overlay.facts`: the legacy fields
    its connection states, by the connection's own model), and the facts are
    read as the migration's step 3 will leave them (`facts.adopted`): an
    interrupted run's copies taken back, the connection's fields laid over.
    Otherwise they are the file's (`_model_facts`)."""
    planned = (stated or {}).get(provider_id)
    if not planned:
        return _model_facts(provider_id, model, rev)
    adopting, fields = next(iter(planned.items()))
    try:
        return facts.adopted(provider_id, model, rev, adopting=adopting, stated=dict(fields))
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


# ---- the target ----
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


#: The facts a target is built with, and what each sends unstated (spec 4.2):
#: post images on auto, no prefill, no post-processing -- the defaults a
#: legacy connection carried (`_stated`).
_UNSTATED = {"vision": "", "prefill": False, "post_process": "none"}


def _stated(model_facts: dict) -> tuple[str, bool, str]:
    """`(vision, prefill, post_process)` -- the model's stated behaviour, from
    `model_facts` (`facts.of`), never the connection's legacy fields. An
    unstated fact is the default (`_UNSTATED`), never the connection's flag: a
    model nothing was said of runs as one nothing was said of.

    `vision` is the post-image preference and nothing more: `off` stops post
    images (`_target`'s `reads_images`) and is NOT a capability `no`
    (`capabilities._stated`), so it never refuses an image description."""
    vision = model_facts.get("vision")
    post_process = model_facts.get("post_process")
    return (vision if isinstance(vision, str) else "",
            model_facts.get("prefill") is True,
            # Only a value the facts module would write: a hand-edited sidecar
            # sends the default rather than a string nothing reads.
            (post_process if isinstance(post_process, str)
             and post_process in facts.POST_PROCESS_VALUES and post_process else "none"))


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


def facts_for(raw: dict, model: str) -> dict:
    """The facts a target outside any route (`target_for`) is built with:
    `model`'s on `raw`, under the model the record runs (`facts.model_of`),
    read exactly as a resolved attempt reads them (`_read_facts`) -- so what
    the model test's probes and the screens' previews send and show is what
    play sends. Below format 2 that is through the planner's overlay
    (`Overlay.facts`): the connection's legacy `vision`, `prefill` and
    `post_process` laid over its OWN model's facts, as the migration's step 3
    will leave them, and nothing of them on another model. Never raises: a
    `config.md` that cannot be read plans nothing, and unreadable facts say
    nothing."""
    try:
        stated = _overlay(config.read_config(), {}).facts
    except (locks.StoreBusy, OSError, UnicodeDecodeError):
        stated = {}
    return _read_facts(_text(raw, "id"), _sent_model(raw, model), _rev(raw), stated)


def _rev(conn: dict) -> str:
    rev = conn.get("rev", "")
    return rev if isinstance(rev, str) else ""


def _text(conn: dict, key: str) -> str:
    value = conn.get(key, "")
    return value if isinstance(value, str) else ""


def _sent_model(raw: dict, model: str) -> str:
    """The model a target on `raw` sends when asked for `model`: `model`
    itself, except an unset Claude model, which runs the default
    (`facts.model_of`, `llm.effective_model`'s rule)."""
    return facts.model_of({"kind": raw.get("kind"), "model": model})


def _target(raw: dict, model: str, sampling: dict, row: dict | None,
            caps: dict[str, capabilities.Cap], model_facts: dict,
            sizes: wire.Limits) -> wire.Target:
    """The `wire.Target` an adapter sends for record `raw` serving `model`
    with `sampling` (`_sampling`'s shape): built from the record, the
    catalog row read for this model (`row`, or None), the attempt's resolved
    capabilities (`caps`) and its model's facts (`model_facts`, `facts.of`'s
    shape). Every target the store hands out is built here -- a resolved
    attempt's (`_attempt`) and one outside any route (`target_for`) -- so the
    two cannot disagree.

    `model` is the one sent (`_sent_model`), and so is `requested_model`:
    what the usage holder files as asked for. `model_params` and
    `model_features` are the catalog row's, never anything already on the
    record (it may belong to another model, and inheriting it would report a
    parameter the model was never checked for as verified). `prefill`,
    `post_process` and the post-image preference are the model's facts
    (`_stated`); `reads_images` is `post_images.capability`'s rule
    (`capabilities.post_image_reach`, a kind left off is OpenRouter's) over
    that preference and the attempt's own `vision` capability, so the store
    is not read again. `sizes` is the model's window and output cap
    (`limits.of` over the same row and facts, 01i). The account is the
    billing alone, and the target is never flagged structured: those are a
    resolution's stamps (`_stamp`)."""
    sent = _sent_model(raw, model)
    params = _params_of(raw, row)
    features = row.get("features") if row is not None else None
    vision, prefill, post_process = _stated(model_facts)
    return wire.Target(
        provider_id=_text(raw, "id"), kind=_text(raw, "kind"), model=sent,
        provider_name=_text(raw, "name"), base_url=_text(raw, "base_url"),
        api_key=_text(raw, "api_key"), rev=_rev(raw), requested_model=sent,
        sampling=wire.Sampling(preset_id=sampling["preset_id"],
                               preset_name=sampling["preset_name"],
                               scope=sampling["scope"], params=dict(sampling["params"])),
        sampler_support=_text(raw, "sampler_support"),
        model_params=tuple(params) if params is not None else None,
        model_features=dict(features) if isinstance(features, dict) else None,
        limits=sizes, prefill=prefill, post_process=post_process,
        reads_images=capabilities.post_image_reach(
            str(raw.get("kind", "openrouter")), vision,
            caps.get("vision", _UNKNOWN_CAP)),
        account=wire.Account(billing=providers.billing(raw)))


#: A capability nothing has said anything about.
_UNKNOWN_CAP = capabilities.Cap(capabilities.UNKNOWN, "unknown")


def _attempt(provider_id: str, model: str, sampling: dict, raw: dict, *,
             retries: int = 0, catalog: bool = True,
             model_facts: dict | None = None,
             stated: Mapping[str, Mapping[str, dict]] | None = None) -> Attempt:
    """One attempt: its target, and what is known of it.

    Capabilities are resolved from the catalog row the target is built from
    and the model's facts, once -- never by `capabilities.caps_for`, which
    would read the same sidecar again. `catalog` False reads no row
    (`embed_attempt`: a record whose rev the cached catalog is not for). The
    facts are read under the model the attempt runs (`facts.model_of`: an
    unset Claude model is `opus`, where the migration wrote them) -- below
    format 2, as the migration will leave them (`stated`, `Overlay.facts`;
    `_read_facts`).

    Those same facts are what the target sends (`_stated`), so an attempt
    carries ITS model's prefill, post-processing and post-image preference.
    A reroll onto another model therefore runs with that model's facts, not
    the connection's -- per-model facts are the design (spec 4.2). The
    baseline's override cells observe neither prefill nor vision, so nothing
    frozen records the difference.

    `model_facts` stands in for the read (`facts.of`'s shape): facts not yet
    written, which a facts write's guard judges (`embed_attempt`)."""
    row = _catalog_row(raw, model) if catalog else None
    preset = providers.infer(raw)
    rev = _rev(raw)
    if model_facts is None:
        model_facts = _read_facts(provider_id, _sent_model(raw, model), rev, stated)
    caps, target = _typed(raw, model, sampling, row, preset, model_facts)
    base_url = raw.get("base_url", "")
    return Attempt(
        provider_id, model, sampling["preset_id"],
        provider_kind=str(raw.get("kind", "") or ""),
        base_url=(base_url if isinstance(base_url, str) and base_url else preset.base_url),
        rev=rev, billing=providers.billing(raw), provider_preset=preset.id,
        facts=model_facts, capabilities=caps,
        controls=llm_sampling.effective(target), retries=retries, target=target)


def _typed(raw: dict, model: str, sampling: dict, row: dict | None,
           preset: providers.Preset, model_facts: dict
           ) -> tuple[dict[str, capabilities.Cap], wire.Target]:
    """`raw` at `model` made an attempt's: its capabilities resolved from the
    catalog row and its model's facts -- never by `capabilities.caps_for`,
    which would read the same sidecar again -- its size from the same two
    (`limits.of`, 01i: no read of its own), and its target built from all of
    it. The one builder `_attempt` and `target_for` share."""
    caps = capabilities.resolve_caps(preset, model, catalog_row=row, facts=model_facts)
    sizes = limits.of(row, model_facts)
    return caps, _target(raw, model, sampling, row, caps, model_facts, sizes)


def target_for(raw: dict, model: str, sampling: dict, *, model_facts: dict) -> wire.Target:
    """The `wire.Target` for `raw` (a connection record) sending `model` with
    `sampling` (`_sampling`'s shape), its model's stated behaviour taken from
    `model_facts` (`facts.of`'s shape; `facts_for` reads them as a target
    outside any route sends them): a target outside any route, built by the
    builder a resolved attempt's is (`_typed`), so the two cannot disagree.

    For what sends or describes one attempt without resolving a task: the
    controls preview, the connection editor's display (`own_target`), a call
    that asks the provider rather than a model (`provider_target`) and the
    model test's probes. Its account is the billing alone, and it is never
    flagged structured: those are a resolution's stamps (`_stamp`)."""
    row = _catalog_row(raw, model)
    return _typed(raw, model, sampling, row, providers.infer(raw), model_facts)[1]


def provider_target(raw: dict) -> wire.Target:
    """`raw` as a target at its own model, with no preset and nothing said of
    the model: what a call that asks the PROVIDER rather than a model is
    handed -- the catalog listing, the catalog probe of a provider not yet
    saved, the health check -- and where a screen reads the model the record
    runs (`.model`: an unset Claude model is the default)."""
    return target_for(raw, _own_model(raw), dict(NO_SAMPLING), model_facts={})


def _own_model(raw: dict) -> str:
    """The model `raw` names, "" for none."""
    model = raw.get("model", "")
    return model if isinstance(model, str) else ""


#: The capabilities an operation needs of itself, as ALTERNATIVES: an attempt
#: meets the operation when it is not known unable to do every one of them.
#: A decision is answered natively (`decide_native`) or by structured
#: generation (`generate`), so a model needs either. Read off
#: `capabilities.NEEDS`, the picker's own table, so the seam and the picker
#: hold one answer to "what does a decision need".
OPERATION_CAPABILITY: dict[str, tuple[str, ...]] = {
    op: capabilities.NEEDS[op] for op in ("generate", "embed", "decide")}


def _needs(route: routing.Route | None, operation: str) -> tuple[frozenset[str], ...]:
    """What an attempt must be able to do for this route and operation, as
    groups of alternatives: the operation's own (`OPERATION_CAPABILITY`) as
    one group, and each of the route's `requires` as a group of its own."""
    own = frozenset(OPERATION_CAPABILITY.get(operation, ("generate",)))
    return (own, *(frozenset({cap}) for cap in (route.requires if route is not None else ())))


#: The sources whose `no` is a guess rather than knowledge: the name rule
#: reads "embed" in an id, and a chat model can carry that word. Such a `no`
#: hides a model in a picker (`capabilities.group_for`) but is never missing,
#: so the seam never refuses on it.
_GUESSES = frozenset({"name"})


def _known_no(found: capabilities.Cap | None) -> bool:
    """Whether `found` is a `no` that is knowledge rather than a guess."""
    return (found is not None and found.value == capabilities.NO
            and found.source not in _GUESSES)


def _missing(attempt: Attempt, needs: tuple[frozenset[str], ...]) -> tuple[str, ...]:
    """Every member of every group in `needs` whose members `attempt` is ALL
    known (`no`) not to have, in `capabilities.NAMES` order -- a group with
    one member it may have is met. `unknown` is never missing, and neither is
    a guess (`_GUESSES`)."""
    caps = attempt.capabilities
    unmet = {cap for group in needs if all(_known_no(caps.get(c)) for c in group)
             for cap in group}
    return tuple(cap for cap in capabilities.NAMES if cap in unmet)


def _own_preset(selection: Selection) -> Callable[[Callable[[str], bool]], tuple[str, str]]:
    """The preset choice for a selection with no route: its own, or none."""
    return lambda known: cascade.preset_for(None, selection, campaign={}, glob={},
                                            known=known)


def own_target(raw: dict) -> wire.Target:
    """`raw` as a target at its own model with its OWN sampler preset (scope
    `connection`, or `none`) and its model's facts (`facts_for`) -- what a
    connection sends outside any route: the connection editor's display. (A
    resolved fallback brings its own preset through the cascade instead,
    `resolve`.)"""
    model = _own_model(raw)
    own = Selection(_text(raw, "id"), model, llm_connections.own_preset(raw))
    return target_for(raw, model, _sampling(_own_preset(own), _preset_lookup()),
                      model_facts=facts_for(raw, model))


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


class Silence(NamedTuple):
    """One scope's own choice left out of a resolution: what that scope's row
    would run on if it were cleared (`settings.view`'s `inherits`).

    The keys are dropped from that scope's view AFTER the planner's overlay,
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
    meta = campaign_meta(cid)
    seen = _overlay(cfg, meta, cid)
    lookup = connection_lookup()
    presets = _preset_lookup(seen.presets)
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
            unforced = _sampling(cascaded(choice.selection), presets)
            standing_preset = unforced["preset_id"]
            sampling = _sampling(lambda known: overriding(known, primary), presets)
        else:
            sampling = unforced = _sampling(cascaded(primary), presets)
        first = _attempt(primary.provider, primary.model, sampling, raw,
                         retries=config.llm_retries(cfg), stated=seen.facts)
        attempts.append(first)
        fallback = choice.fallback
        fb_raw = lookup(fallback.provider) if fallback is not None else None
        if routing.policy(task).fallback == "none":
            # The task's code policy sends no fallback, whatever its role
            # says (spec 01d §4.2). The cascade already walked past a slot
            # naming no provider, so `fb_raw` is None here only for a
            # connection that vanished since -- which says nothing, as below.
            fallback_problem = NO_FALLBACK_POLICY if fb_raw is not None else None
        else:
            # A fallback on the primary's own provider is a retry (#144),
            # which the retry budget already covers -- except behind a decide
            # primary that cannot generate (`_apart`) when it names ANOTHER
            # model: that fallback is a stage of its own, never a second try
            # of the call that failed. The same model on the same connection
            # is that second try whatever the stage is called, so it is
            # dropped as C drops it. The reason is lifted exactly where the
            # drop is (`SAME_PROVIDER`).
            fallback_problem = (problem(fb_raw)
                                if fb_raw is not None and fallback is not None
                                and _apart(first, operation)
                                and not _same_model(first.target, fb_raw, fallback.model)
                                else _fallback_problem(first.target.provider_id, fb_raw))
        if fallback is not None and fb_raw is not None and fallback_problem is None:
            # A copy, so the two attempts never share a mutable block.
            fb_sampling = ({**unforced, "params": dict(unforced["params"])}
                           if unforced["scope"] in ROUTE_SCOPES
                           else _sampling(_own_preset(fallback), presets))
            attempts.append(_attempt(fallback.provider, fallback.model, fb_sampling,
                                     fb_raw, stated=seen.facts))

    tail = _chain(attempts, operation, _needs(route, operation),
                  choice=choice, selection=selection)
    return ResolvedInference(
        task=task, operation=operation,
        route=route.key if route is not None else "",
        role=choice.role, via=choice.via, scope=choice.scope,
        attempts=tail.attempts, standing=choice.selection,
        standing_preset=standing_preset, missing=tail.missing,
        fallback_missing=tail.fallback_missing, fallback_problem=fallback_problem,
        rides=tail.rides, legacy_route=seen.legacy_route(route) if seen.legacy else None)


class _Tail(NamedTuple):
    """What `_chain` settles about a resolution's attempts."""

    attempts: tuple[Attempt, ...]
    missing: tuple[str, ...]
    fallback_missing: tuple[str, ...]
    #: Whether the fallback rides the primary (`ResolvedInference.rides`).
    rides: bool


def _chain(attempts: list[Attempt], operation: str, needs: tuple[frozenset[str], ...], *,
           choice: cascade.Choice, selection: Selection | None) -> _Tail:
    """The tail of `resolve`: stamp each attempt's account, flag the
    structured-capable ones, say what each is known to lack, give a decide
    resolution's attempts their `decision_mode`, and say whether the fallback
    rides the facade behind the primary (`_rides`; spec 5.3: a fallback known
    incapable never does, so it is never sent). Each stamp replaces the
    attempt around a new target (`_stamp`)."""
    attempts = _account(attempts, operation, choice, selection)
    attempts = _flag_structured(attempts, operation)
    fallback_missing = _missing(attempts[1], needs) if len(attempts) > 1 else ()
    missing = _missing(attempts[0], needs) if attempts else ()
    if operation == "decide":
        # Before `_rides`, which reads the fallback's mode.
        attempts = [_decide_attempt(a) for a in attempts]
    rides = len(attempts) > 1 and not fallback_missing and _rides(attempts, operation)
    return _Tail(tuple(attempts), missing, fallback_missing, rides)


def _decide_attempt(attempt: Attempt) -> Attempt:
    """`attempt` on a decide resolution: its `decision_mode`, and -- when that
    is "native" -- its controls all `n/a` (spec 8), since its provider's
    decisions endpoint takes no sampling. Under ruling 1 a native attempt is
    always one that cannot generate, so there is no sampling to keep."""
    mode = decision_mode(attempt)
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
    return attempts[1].decision_mode == "structured" and generates(attempts[0])


def generates(attempt: Attempt) -> bool:
    """Whether `attempt` can generate: `generate` is not among what it is
    KNOWN (`_missing`) not to do, so `unknown` and a name-rule guess both
    still generate. What `decision_mode`, the same-provider rule (`_apart`)
    and the decide chain (`inference.stages`) ask."""
    return not _missing(attempt, (frozenset({"generate"}),))


def decides_natively(attempt: Attempt) -> bool:
    """Whether `attempt` may decide natively: `decide_native` is not among
    what it is KNOWN (`_missing`) not to do, so `unknown` and a name-rule
    guess both still may (spec 5.3). `generates`' twin, asked where a native
    decision is forced on an attempt (`evals/runner.chain`)."""
    return not _missing(attempt, (frozenset({"decide_native"}),))


def native_only(caps: dict[str, capabilities.Cap]) -> bool:
    """Whether a model with these capabilities is answered natively: it is
    known (`no`, from a source other than the name rule) unable to
    `generate`, and not known unable to `decide_native` -- `unknown` is
    allowed (spec 5.3). The one rule the resolver and the controls preview
    both ask, so the two never disagree."""
    return _known_no(caps.get("generate")) and not _known_no(caps.get("decide_native"))


def native_capable(attempt: Attempt) -> bool:
    """Whether `attempt` may be asked natively FIRST (spec 01c §3.2, §4.2):
    its `decide_native` is a known `yes` -- never `unknown`, which
    `native_only` allows because a native-only model has no other way to
    answer, and a native-first one does. That a kind with no decisions
    endpoint never reads `yes` is the presets' business: each lists
    `decide_native` in `never`, an adapter-source `no` no override lifts
    (`capabilities.resolve_caps`), and a test holds every preset to it -- so
    the store needs no gateway import (`adapters`: the store never imports
    the gateway). Production only: `evals/runner.chain` keeps
    `decides_natively`, so evidence can be gathered on an `unknown`."""
    found = attempt.capabilities.get("decide_native")
    return found is not None and found.value == capabilities.YES


def native_first(resolved: ResolvedInference) -> bool:
    """Whether `resolved`'s decide chain asks its primary's decisions endpoint
    before its structured stage (01c-C1): a decide resolution whose primary
    is `structured`, whose adapter kind its task's code policy lists
    (`routing.policy(resolved.task).native_first`), and which is
    `native_capable`. The one rule `inference.stages` (and a later settings
    readout) asks. Pure: reads only the resolution and the policy."""
    if resolved.operation != "decide" or not resolved.attempts:
        return False
    primary = resolved.attempts[0]
    return (primary.decision_mode == "structured"
            and primary.target.kind in routing.policy(resolved.task).native_first
            and native_capable(primary))


def decision_mode(attempt: Attempt) -> str:
    """The backend that would answer `attempt` on a decide resolution (ruling
    1, C1): "native" (its provider's decisions endpoint) when it is
    `native_only`; else "structured" (`generate(schema=)` and the parser)
    when it `generates`, whatever its `decide_native` says -- a model that
    can generate stays structured; a task whose policy lists its kind in
    `native_first` asks its decisions endpoint first, which is a stage of
    `inference.stages` (`native_first`), not a mode; else "", for an attempt
    that can do neither."""
    if native_only(attempt.capabilities):
        return "native"
    return "structured" if generates(attempt) else ""


def _apart(primary: Attempt, operation: str) -> bool:
    """Whether a fallback on `primary`'s own provider may be kept (`resolve`):
    on a decide resolution whose primary cannot generate, and only when it
    names another model (`_same_model`, checked by the caller). That
    fallback is never a retry of the primary's call -- it is a stage of its
    own, on another model (`inference.stages`) -- so the same-provider drop
    (#144) does not apply. The same model on the same connection IS that
    retry, and is dropped with `SAME_PROVIDER`. Kept like any other fallback:
    one that cannot serve either backend is reported in `fallback_missing`."""
    return operation == "decide" and not generates(primary)


def structured_capable(attempt: Attempt) -> bool:
    """Whether `attempt` is asked for its provider's structured mode when a
    call sends a schema: its `structured_output` is `yes`. A `no` or an
    `unknown` is not -- a strict endpoint answers the envelope it does not
    know with a 400, and the user's lever is the facts override (spec 01f
    3.2). The one rule a decide resolution's flags (`_flag_structured`) and a
    generate call's per-call chain (`inference._structured_chain`) both ask."""
    found = attempt.capabilities.get("structured_output")
    return found is not None and found.value == capabilities.YES


def _flag_structured(attempts: list[Attempt], operation: str) -> list[Attempt]:
    """Flag each attempt that is `structured_capable` (spec 7.2) on its
    target (`_stamp`). A decide resolution only: a generate resolution's
    targets are what they were before slice F (plan Minor 4) -- a generation
    that sends a schema flags its own per-call targets instead (01f 3.2)."""
    if operation != "decide":
        return attempts
    return [_stamp(attempt, structured=True) if structured_capable(attempt) else attempt
            for attempt in attempts]


def _stamp(attempt: Attempt, *, account: dict | None = None,
           structured: bool = False) -> Attempt:
    """`attempt` with `account` laid over its target's account and, when
    `structured`, its target flagged for its provider's structured mode --
    the one place a resolution writes either. The target is frozen, so the
    attempt is replaced around a new one."""
    target = attempt.target
    if account:
        target = target.with_account(**account)
    if structured:
        target = dataclasses.replace(target, structured=True)
    return dataclasses.replace(attempt, target=target)


def embed_endpoint(conn: dict) -> str:
    """The base URL `conn` serves embeddings from, or "" when it serves none.

    An ``openai_compatible`` connection brings its own URL. OpenRouter serves
    ``/embeddings`` too, at the preset's URL (an OpenRouter connection's own is
    locked), and one with no key is not set up. A legacy config naming an
    OpenRouter connection for embeddings has always meant "off": the planner
    maps that choice to no Embedding role at all (`legacy_plan.legacy_embeds`),
    so it never reaches here.
    """
    if conn["kind"] == "openai_compatible":
        return conn["base_url"] or ""
    if conn["kind"] == "openrouter" and conn["api_key"]:
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
_EMBED_NEEDS = (frozenset(OPERATION_CAPABILITY["embed"]),)


class EmbedAttempt(NamedTuple):
    """The Embedding role's one attempt on a provider record, and what follows
    from it (`embed_attempt`)."""

    attempt: Attempt
    #: `embed`, when the attempt is known (`no`) not to make embeddings.
    missing: tuple[str, ...]
    #: The space it embeds in (`space_of`), or None when it embeds nothing.
    space_id: str | None


def embed_attempt(provider_id: str, model: str, raw: dict, *,
                  catalog: bool = True, model_facts: dict | None = None,
                  stated: Mapping[str, Mapping[str, dict]] | None = None) -> EmbedAttempt:
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
    yet written instead of `facts.json`'s (`embed_space.facts_moved`), and
    `stated` is the planner's legacy facts (`Overlay.facts`)."""
    attempt = _attempt(provider_id, model, dict(NO_SAMPLING), raw, catalog=catalog,
                       model_facts=model_facts, stated=stated)
    endpoint = embed_endpoint(raw)
    attempt = dataclasses.replace(attempt, base_url=endpoint)
    missing = _missing(attempt, _EMBED_NEEDS)
    space_id = space_of(raw, model) if model and endpoint and not missing else None
    return EmbedAttempt(attempt, missing, space_id)


def _fallback_problem(primary_id: str, fallback: dict | None) -> str | None:
    """Why a fallback that exists is left out of the chain: it is on the
    primary's own provider (`primary_id`; `SAME_PROVIDER`), or it cannot send
    (`problem`). Said either way -- without a reason the settings view showed
    a dropped fallback as a working one. None for no fallback, or one that is
    sent."""
    if fallback is None:
        return None
    if _same_provider(primary_id, fallback):
        return SAME_PROVIDER
    return problem(fallback)


def embedding(cfg: dict | None = None, *,
              lookup: llm_connections.Lookup | None = None) -> ResolvedInference:
    """The Embedding role's resolution: its one attempt, what that attempt is
    known not to do, and the vector space it embeds in (spec 5.4, slice D).

    The one reader of the role. `config.md` is read only when `cfg` is None;
    the role is global only (spec 4.4), so no campaign is read -- one space,
    one vector cache. The selection is `cascade.role_selection("embedding")`
    over `cfg` as format 2 sees it (`_overlay`; a legacy `embeddings_*`
    choice is the planner's mapping), both values stripped (`_embedding_view`,
    the `embeddings_*` trim rule, kept). Embedding inherits nothing and has no
    fallback on any axis (rule 4: a vector is saved only under the space that
    produced it), so there is exactly one attempt or none (`embed_attempt`).

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
    seen = _overlay(cfg, {})

    def exists(conn_id: str) -> bool:
        return lookup(conn_id) is not None

    selection, _, scope = cascade.role_selection(
        "embedding", campaign={}, glob=_embedding_view(seen.cfg), exists=exists)
    raw = lookup(selection.provider) if selection is not None else None
    if selection is None or raw is None:
        return ResolvedInference(task="", operation="embed", route="",
                                 role="", via="", scope="none", attempts=())
    got = embed_attempt(selection.provider, selection.model, raw, stated=seen.facts)
    # The account a chat resolution's attempts get from `_account`: the
    # operation, and the role whose slot supplied the selection -- always the
    # Embedding role's, since nothing overrides it per call (spec 9.3).
    attempt = _stamp(got.attempt, account={"operation": "embed", "role": "embedding"})
    return ResolvedInference(
        task="", operation="embed", route="",
        role="embedding", via="role", scope=scope, attempts=(attempt,),
        standing=selection, missing=got.missing, space_id=got.space_id)


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
    return [_stamp(attempt, account=stamp) for attempt in attempts]


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


# ---- the refusal ----
#: A refusal: the HTTP status and the body the seam answers with. The body is
#: a `{detail, kind}` dict, or -- for an image route on a wire protocol that
#: cannot carry an image -- the bare `image_drafts.UNSUPPORTED` sentence that
#: route has always answered with.
Refusal = tuple[int, dict | str]


def unusable(resolved: ResolvedInference) -> Refusal | None:
    """Why this resolution cannot send, if it cannot: the 409 `missing_key`.

    Shared by the seam and by the per-call override, whose `model`-only reroll
    drives the standing route and must be refused on exactly the same terms --
    including the routed-connection wording below.
    """
    if not resolved.attempts:
        return 409, {"detail": "No LLM connection selected", "kind": "missing_key"}
    primary = resolved.attempts[0].target
    why = target_problem(primary)
    if why is None:
        return None
    # Refused before the facade, so a configured fallback does not rescue it.
    # The facade falls back on `auth`, and the distinction is real: a key the
    # provider rejected is a runtime failure, worth routing around silently,
    # while no key at all is a setup mistake. Quietly serving it from the
    # fallback would leave someone playing for weeks on the wrong connection.
    name = primary.label
    if resolved.legacy:
        # A format-1 store keeps the sentences it always answered (user
        # ruling 2026-10-09): the bare reason, and a ROUTED connection named
        # by the legacy route it was stored under -- reported, not walked
        # past, because the user pointed this task at it.
        if resolved.via == "route":
            return 409, {"detail": f"{why} ({name}, routed for "
                                   f"{routing.label_for(resolved.legacy_route or '').lower()})",
                         "kind": "missing_key"}
        return 409, {"detail": why, "kind": "missing_key"}
    # Spec 12: the provider, and the route or role that chose it. Nearly
    # everything comes through a role, and two providers of one kind would
    # otherwise read the same sentence. A pin is named by its own route (a
    # split route's pin is its own, not its legacy parent's): a ROUTED
    # connection that cannot send is reported, not walked past -- the user
    # pointed this task at it.
    where = (f"routed for {routing.label_for(resolved.route).lower()}"
             if resolved.via == "route"
             else f"the {resolved.role.capitalize()} role" if resolved.role else "")
    detail = f"{why} ({name}, {where})" if where else f"{why} ({name})"
    return 409, {"detail": detail, "kind": "missing_key"}


def incapable(resolved: ResolvedInference) -> Refusal | None:
    """The 409 for a primary that is KNOWN unable to do what its route needs.

    Only a known `no` (`resolved.missing`); `unknown` is let through, and a
    fallback's gaps are reported (`fallback_missing`) but never refused on.
    Asked after `unusable` (`refusal`), so a connection with no key says that
    first: it is the fix the reader has to make before anything else matters.

    A wire protocol that cannot carry an image (an `adapter` `no` on vision)
    answers with the sentence the image-description route always answered
    with, in the same body, so nothing that reads it sees a change. A model
    whose facts say "Images: on" is not refused over a catalog's vision `no`:
    that word is a user `yes` the capability resolution already ranks above
    the catalog (a legacy connection's own "Images: on" reaches the facts
    through the planner, `Overlay.facts`). Anything else is `incapable` (`incapable_text`), naming the first missing
    capability in `capabilities.NAMES` order -- with its alternatives, on a
    decide resolution whose primary can neither generate nor decide natively
    (`_cannot`). A name-rule guess is never
    missing (`_GUESSES`), and a failed test call is `unknown`, so neither can
    refuse here.
    """
    if not resolved.missing or not resolved.attempts:
        return None
    primary = resolved.attempts[0]
    vision = primary.capabilities.get("vision")
    if "vision" in resolved.missing and vision is not None and vision.source == "adapter":
        return 409, image_drafts.UNSUPPORTED
    return 409, {"detail": incapable_text(resolved, resolved.missing[0]),
                 "kind": "incapable"}


def refusal(resolved: ResolvedInference) -> Refusal | None:
    """The seam's one decision about `resolved`: `(status, body)` when it
    cannot be served, None when it can. Pure -- it reads nothing.

    `routes.common` raises from it, and the settings view reports it, so what
    the screen says is wrong with a row is the seam's own answer rather than a
    copy of it."""
    return unusable(resolved) or incapable(resolved)


def incapable_text(resolved: ResolvedInference, cap: str) -> str:
    """The `incapable` sentence (spec 5.3): the route, the role when one
    supplied the model, the model on its provider, what it cannot do
    (`_cannot`), and what to do about it.

    "The <label> route ..." rather than "<label> runs ...", because half the
    route labels are plural ("Scene turns", "Image descriptions"). The role
    is named only when the model IS the role's: a pin names none, and a
    per-call override moved the call off whatever the role chose. The remedy
    follows: another model for the role, or a pin, where there is a route to
    pin; another model for the route where it is already pinned. The model is
    the one the connection runs (`facts.model_of`, `llm.effective_model`'s
    rule)."""
    subject, where, remedy = _primary_phrases(resolved)
    return f"{subject} {where}, which cannot {_cannot(resolved, cap)} — {remedy}."


def _cannot(resolved: ResolvedInference, cap: str) -> str:
    """What the primary cannot do, as `incapable_text` says it: `cap`'s
    phrase (`capabilities.CANNOT`), or -- when `cap` is one of its
    operation's alternatives (`OPERATION_CAPABILITY`) and the primary is
    missing them all -- every alternative's, joined by "or" in
    `capabilities.NAMES` order ("generate text or make native decisions").
    `CANNOT` stays keyed by capability, so a picker's reasons
    (`capabilities.group_for`) are unchanged."""
    group = OPERATION_CAPABILITY.get(resolved.operation, ())
    if len(group) > 1 and cap in group and all(c in resolved.missing for c in group):
        return " or ".join(capabilities.CANNOT.get(c, c) for c in capabilities.NAMES
                           if c in group)
    return capabilities.CANNOT.get(cap, cap)


def _on(attempt: Attempt) -> str:
    """"<model> on <provider>": the model the connection runs, and the
    provider's name (else its preset's label, else its id)."""
    target = attempt.target
    preset = providers.PRESETS.get(attempt.provider_preset)
    provider = target.provider_name or (preset.label if preset is not None
                                        else target.provider_id)
    return f"{target.model} on {provider}"


def _primary_phrases(resolved: ResolvedInference) -> tuple[str, str, str]:
    """`(subject, where, remedy)` for the primary attempt -- what the route
    is, what it runs on, and what to do about it (`incapable_text`)."""
    primary = resolved.attempts[0]
    on = _on(primary)
    standing = resolved.standing
    chosen = standing is not None and (standing.provider, standing.model) == (
        primary.provider_id, primary.model)
    subject = (f"The {routing.label_for(resolved.route)} route" if resolved.route
               else "This decision" if resolved.operation == "decide"
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
