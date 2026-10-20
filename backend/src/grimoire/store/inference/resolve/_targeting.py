"""Memoized settings/planner/record reads and target construction from one catalog/facts snapshot, with per-resolution presets."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from .... import llm_sampling, wire
from ... import campaigns, config, llm_connections, locks, sampler_presets
from ... import inference_keys as keys
from ... import inference_retired as retired
from .. import capabilities, cascade, facts, legacy_plan, limits, providers
from ..cascade import Selection
from ..resolved import Attempt
from . import _contracts

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
                stated: Mapping[str, Mapping[str, dict]] | None, *,
                strict: bool = False) -> dict:
    """`model`'s facts on `provider_id`, never raising. Below format 2 the
    planner lists the provider in `stated` (`Overlay.facts`: the legacy fields
    its connection states, by the connection's own model), and the facts are
    read as the migration's step 3 will leave them (`facts.adopted`): an
    interrupted run's copies taken back, the connection's fields laid over.
    Otherwise they are the file's (`_model_facts`).

    `strict` (the Embedding role's read, 01h) lets `facts.FactsUnreadableError`
    through -- a file that exists and cannot be read, or does not parse --
    rather than reading it as a model nothing was said of; anything else
    still reads as `{}`."""
    planned = (stated or {}).get(provider_id)
    try:
        if not planned:
            if strict:
                return facts.of(provider_id, model, rev, strict=True)
            return _model_facts(provider_id, model, rev)
        adopting, fields = next(iter(planned.items()))
        return facts.adopted(provider_id, model, rev, adopting=adopting,
                             stated=dict(fields), strict=strict)
    except facts.FactsUnreadableError:
        if strict:
            raise
        return {}
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
        return dict(_contracts.NO_SAMPLING)
    return {"preset_id": preset_id,
            "preset_name": preset["name"] if preset else "",
            "scope": scope,
            "params": dict(preset["params"]) if preset else {}}


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
        return dict(_contracts.NO_SAMPLING)
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
        embed_options=_stated_options(model_facts),
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
            caps.get("vision", _contracts._UNKNOWN_CAP)),
        account=wire.Account(billing=providers.billing(raw)))


def _stated_options(model_facts: dict) -> wire.EmbedOptions | None:
    """The embedding options `model_facts` state, read fail-soft: a block the
    validator refuses is no options here. What a chat target, and a target
    outside any route, carry for display; the Embedding role reads them
    strictly instead (`embed_attempt`), and so does the model test
    (`strict_embed_options`)."""
    try:
        return facts.embed_options(model_facts)
    except ValueError:
        return None


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
    return target_for(raw, _own_model(raw), dict(_contracts.NO_SAMPLING), model_facts={})


def _own_model(raw: dict) -> str:
    """The model `raw` names, "" for none."""
    model = raw.get("model", "")
    return model if isinstance(model, str) else ""


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
