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

# Compatibility facade. State and implementation live in responsibility leaves;
# internal fault injection targets those owners, while existing imports remain.
import dataclasses
from collections.abc import Callable, Mapping
from typing import NamedTuple

from .... import llm_sampling, wire
from ... import campaigns, config, image_drafts, llm_connections, locks, routing, sampler_presets
from ... import inference_keys as keys
from ... import inference_retired as retired
from .. import capabilities, cascade, facts, legacy_plan, limits, providers
from ..cascade import Selection
from ..resolved import Attempt, ResolvedInference
from ._capability_policy import _flag_structured as _flag_structured
from ._capability_policy import _known_no as _known_no
from ._capability_policy import _missing as _missing
from ._capability_policy import _needs as _needs
from ._capability_policy import _stamp as _stamp
from ._capability_policy import decides_natively as decides_natively
from ._capability_policy import decision_mode as decision_mode
from ._capability_policy import generates as generates
from ._capability_policy import known_lacks as known_lacks
from ._capability_policy import native_capable as native_capable
from ._capability_policy import native_first as native_first
from ._capability_policy import native_only as native_only
from ._capability_policy import structured_capable as structured_capable
from ._contracts import _EMBED_NEEDS as _EMBED_NEEDS
from ._contracts import _GUESSES as _GUESSES
from ._contracts import _UNKNOWN_CAP as _UNKNOWN_CAP
from ._contracts import _UNSTATED as _UNSTATED
from ._contracts import NO_FALLBACK_POLICY as NO_FALLBACK_POLICY
from ._contracts import NO_SAMPLING as NO_SAMPLING
from ._contracts import OPERATION_CAPABILITY as OPERATION_CAPABILITY
from ._contracts import OPTIONS_HELD as OPTIONS_HELD
from ._contracts import OPTIONS_INVALID as OPTIONS_INVALID
from ._contracts import OPTIONS_MANGLED as OPTIONS_MANGLED
from ._contracts import ROUTE_SCOPES as ROUTE_SCOPES
from ._contracts import SAME_PROVIDER as SAME_PROVIDER
from ._contracts import EmbedAttempt as EmbedAttempt
from ._contracts import Refusal as Refusal
from ._contracts import Silence as Silence
from ._contracts import _Tail as _Tail
from ._embedding import _strict_options as _strict_options
from ._embedding import embed_attempt as embed_attempt
from ._embedding import embed_endpoint as embed_endpoint
from ._embedding import embedding as embedding
from ._embedding import space_of as space_of
from ._embedding import strict_embed_options as strict_embed_options
from ._refusals import _cannot as _cannot
from ._refusals import _on as _on
from ._refusals import _primary_phrases as _primary_phrases
from ._refusals import escalation_refusal as escalation_refusal
from ._refusals import escalation_text as escalation_text
from ._refusals import incapable as incapable
from ._refusals import incapable_text as incapable_text
from ._refusals import refusal as refusal
from ._refusals import unusable as unusable
from ._resolution import _account as _account
from ._resolution import _apart as _apart
from ._resolution import _chain as _chain
from ._resolution import _decide_attempt as _decide_attempt
from ._resolution import _fallback_problem as _fallback_problem
from ._resolution import _overridden as _overridden
from ._resolution import _refuse_embed as _refuse_embed
from ._resolution import _rides as _rides
from ._resolution import _role_supplied as _role_supplied
from ._resolution import _same_model as _same_model
from ._resolution import _same_provider as _same_provider
from ._resolution import _silenced as _silenced
from ._resolution import resolve as resolve
from ._targeting import _UNREADABLE as _UNREADABLE
from ._targeting import _attempt as _attempt
from ._targeting import _catalog_row as _catalog_row
from ._targeting import _credential_problem as _credential_problem
from ._targeting import _embedding_view as _embedding_view
from ._targeting import _model_facts as _model_facts
from ._targeting import _overlay as _overlay
from ._targeting import _own_model as _own_model
from ._targeting import _own_preset as _own_preset
from ._targeting import _params_of as _params_of
from ._targeting import _preset_lookup as _preset_lookup
from ._targeting import _read_facts as _read_facts
from ._targeting import _rev as _rev
from ._targeting import _sampling as _sampling
from ._targeting import _sent_model as _sent_model
from ._targeting import _stated as _stated
from ._targeting import _stated_options as _stated_options
from ._targeting import _target as _target
from ._targeting import _text as _text
from ._targeting import _typed as _typed
from ._targeting import campaign_meta as campaign_meta
from ._targeting import connection_lookup as connection_lookup
from ._targeting import current_view as current_view
from ._targeting import embedding_role as embedding_role
from ._targeting import facts_for as facts_for
from ._targeting import model_params as model_params
from ._targeting import own_target as own_target
from ._targeting import preset_sampling as preset_sampling
from ._targeting import problem as problem
from ._targeting import provider_target as provider_target
from ._targeting import retirement_notes as retirement_notes
from ._targeting import stored_embedding_role as stored_embedding_role
from ._targeting import target_for as target_for
from ._targeting import target_problem as target_problem

# Original star-import surface, captured before extraction.
__all__ = [
    'NO_FALLBACK_POLICY',
    'NO_SAMPLING',
    'OPERATION_CAPABILITY',
    'OPTIONS_HELD',
    'OPTIONS_INVALID',
    'OPTIONS_MANGLED',
    'ROUTE_SCOPES',
    'SAME_PROVIDER',
    'Attempt',
    'Callable',
    'EmbedAttempt',
    'Mapping',
    'NamedTuple',
    'Refusal',
    'ResolvedInference',
    'Selection',
    'Silence',
    'annotations',
    'campaign_meta',
    'campaigns',
    'capabilities',
    'cascade',
    'config',
    'connection_lookup',
    'current_view',
    'dataclasses',
    'decides_natively',
    'decision_mode',
    'embed_attempt',
    'embed_endpoint',
    'embedding',
    'embedding_role',
    'escalation_refusal',
    'escalation_text',
    'facts',
    'facts_for',
    'generates',
    'image_drafts',
    'incapable',
    'incapable_text',
    'keys',
    'known_lacks',
    'legacy_plan',
    'limits',
    'llm_connections',
    'llm_sampling',
    'locks',
    'model_params',
    'native_capable',
    'native_first',
    'native_only',
    'own_target',
    'preset_sampling',
    'problem',
    'provider_target',
    'providers',
    'refusal',
    'resolve',
    'retired',
    'retirement_notes',
    'routing',
    'sampler_presets',
    'space_of',
    'stored_embedding_role',
    'strict_embed_options',
    'structured_capable',
    'target_for',
    'target_problem',
    'unusable',
    'wire',
]

# Preserve the original deferred module metadata without redeclaring exports.
__annotations__ = {'OPERATION_CAPABILITY': 'dict[str, tuple[str, ...]]'}
