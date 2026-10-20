"""Embedding endpoint, strict options, space identity and the embedding role single-attempt resolution."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from .... import wire
from ... import config, llm_connections, locks
from .. import cascade, facts, providers
from ..resolved import ResolvedInference
from . import _capability_policy, _contracts, _targeting


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


def _strict_options(provider_id: str, model: str, rev: str,
                    stated: Mapping[str, Mapping[str, dict]] | None
                    ) -> tuple[dict, wire.EmbedOptions | None, str]:
    """`(facts, options, problem)` for `model` (the model SENT) on
    `provider_id`: one strict read of its facts. A held file is `({}, None,
    OPTIONS_HELD)`, a mangled one `({}, None, OPTIONS_MANGLED)`, and a block
    the validator refuses `(facts, None, OPTIONS_INVALID)`; otherwise the
    problem is ""."""
    try:
        model_facts = _targeting._read_facts(provider_id, model, rev, stated, strict=True)
    except facts.FactsMangledError:
        return {}, None, _contracts.OPTIONS_MANGLED
    except facts.FactsUnreadableError:
        return {}, None, _contracts.OPTIONS_HELD
    try:
        return model_facts, facts.embed_options(model_facts), ""
    except ValueError:
        return model_facts, None, _contracts.OPTIONS_INVALID


def strict_embed_options(raw: dict, model: str
                         ) -> tuple[dict, wire.EmbedOptions | None, str]:
    """`_strict_options` for `raw` (a connection record) at `model`, read as
    `facts_for` reads a target outside any route -- below format 2 through
    the planner's overlay. What the model test's embed probe sends, so a
    confirmed test verifies what the role would send, and sends nothing when
    the role would name no space."""
    try:
        stated = _targeting._overlay(config.read_config(), {}).facts
    except (locks.StoreBusy, OSError, UnicodeDecodeError):
        stated = {}
    return _strict_options(_targeting._text(raw, "id"), _targeting._sent_model(raw, model), _targeting._rev(raw), stated)


def space_of(conn: dict, model: str, options: wire.EmbedOptions | None = None) -> str:
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
    connection, so changing it does not move `rev`.

    `options` are the model's stated embedding options (01h §5.1). Their
    document side moves the space -- a cached vector is what was sent for a
    document -- as a NUL, `embopt1:` and its digest after today's string; default
    options (none, or a query-side-only set) leave today's string byte for
    byte, so no vector written before options existed is orphaned."""
    base = f"{conn['id']}\0{conn['rev']}\0{model}"
    if options is None or options.is_default():
        return base
    return f"{base}\0{wire.EMBED_OPTIONS_TAG}:{options.digest()}"


def embed_attempt(provider_id: str, model: str, raw: dict, *,
                  catalog: bool = True, model_facts: dict | None = None,
                  stated: Mapping[str, Mapping[str, dict]] | None = None) -> _contracts.EmbedAttempt:
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
    `stated` is the planner's legacy facts (`Overlay.facts`).

    The facts are read STRICTLY, once (`_strict_options`; 01h §3.2): a facts
    file that exists and cannot be read, or an `embedding` block the
    validator refuses, names no space and says why (`options_problem`) --
    reading either as "no options" would move the space unasked. The same
    read feeds the capabilities, and the options it yields are both put on
    the attempt's target and the space is computed from them: what is sent
    and where it is cached are one object."""
    if model_facts is None:
        model_facts, options, why = _strict_options(
            provider_id, _targeting._sent_model(raw, model), _targeting._rev(raw), stated)
    else:
        try:
            options, why = facts.embed_options(model_facts), ""
        except ValueError:
            options, why = None, _contracts.OPTIONS_INVALID
    attempt = _targeting._attempt(provider_id, model, dict(_contracts.NO_SAMPLING), raw, catalog=catalog,
                       model_facts=model_facts, stated=stated)
    endpoint = embed_endpoint(raw)
    attempt = dataclasses.replace(
        attempt, base_url=endpoint,
        target=dataclasses.replace(attempt.target, embed_options=options))
    missing = _capability_policy._missing(attempt, _contracts._EMBED_NEEDS)
    space_id = (space_of(raw, model, options)
                if model and endpoint and not missing and not why else None)
    return _contracts.EmbedAttempt(attempt, missing, space_id, why)


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
    lookup = _targeting.connection_lookup() if lookup is None else lookup
    seen = _targeting._overlay(cfg, {})

    def exists(conn_id: str) -> bool:
        return lookup(conn_id) is not None

    selection, _, scope = cascade.role_selection(
        "embedding", campaign={}, glob=_targeting._embedding_view(seen.cfg), exists=exists)
    raw = lookup(selection.provider) if selection is not None else None
    if selection is None or raw is None:
        return ResolvedInference(task="", operation="embed", route="",
                                 role="", via="", scope="none", attempts=())
    got = embed_attempt(selection.provider, selection.model, raw, stated=seen.facts)
    # The account a chat resolution's attempts get from `_account`: the
    # operation, and the role whose slot supplied the selection -- always the
    # Embedding role's, since nothing overrides it per call (spec 9.3).
    attempt = _capability_policy._stamp(got.attempt, account={"operation": "embed", "role": "embedding"})
    return ResolvedInference(
        task="", operation="embed", route="",
        role="embedding", via="role", scope=scope, attempts=(attempt,),
        standing=selection, missing=got.missing, space_id=got.space_id,
        embed_options_problem=got.options_problem)
